"""Optional, value-free LangSmith instrumentation; never part of authorization."""
import logging
import os
import json
from functools import wraps

logger = logging.getLogger(__name__)


def traced_stage(name):
    """Trace stage names, duration and status only, excluding all application payloads.

    Telemetry failures cannot re-run a query or change its result. Exception messages
    are deliberately excluded because they can contain SQL, credentials or values.
    """
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if os.getenv('LANGSMITH_TRACING', '').lower() not in ('true', '1', 'yes'):
                return fn(*args, **kwargs)
            context = run = None
            try:
                from langsmith import trace
                context = trace(name, inputs={}, project_name=os.getenv('LANGSMITH_PROJECT', 'Talk2Data'),
                                metadata={'payload_policy': 'stage_status_only'})
                run = context.__enter__()
            except Exception:
                logger.warning('LangSmith trace initialization unavailable; continuing without telemetry')
                context = None
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                if run:
                    try:
                        run.end(error='Application stage failed; details omitted')
                    except Exception:
                        pass
                raise
            else:
                if run:
                    try:
                        status = 'completed'
                        if isinstance(result, tuple) and result and hasattr(result[0], 'status'):
                            status = result[0].status.value
                        elif name == 'gemini_interpretation' and isinstance(result, str):
                            try:
                                parsed = json.loads(result)
                                if isinstance(parsed, dict) and parsed.get('provider_failure'):
                                    status = 'provider_failure'
                            except ValueError:
                                status = 'malformed_output'
                        run.end(outputs={'status': status})
                    except Exception:
                        logger.warning('LangSmith trace completion unavailable')
                return result
            finally:
                if context:
                    try:
                        # Never hand the SDK raw exceptions or traceback locals.
                        context.__exit__(None, None, None)
                    except Exception:
                        logger.warning('LangSmith trace submission unavailable')
        return wrapped
    return decorate
