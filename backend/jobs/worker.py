"""Durable fenced job execution with transactional publication and restart recovery."""
from __future__ import annotations
from backend.observability import traced_stage
import json
import logging
import threading
import uuid
from backend.domain.models import TurnStatus
from backend.security.auth import AuthorizationError
from backend.storage.repository import LeaseFencingError

logger = logging.getLogger(__name__)


class DurableJobWorker:
    def __init__(self, store, manager, security, worker_id=None, lease_seconds=60):
        self.store, self.manager, self.security = store, manager, security
        self.worker_id = worker_id or "worker_" + uuid.uuid4().hex[:8]
        self.lease_seconds = lease_seconds
        self._lock = getattr(manager, "_execution_lock", threading.RLock())
        manager._execution_lock = self._lock
        store.security, store.catalogs = security, manager.catalogs

    def process_next_job(self):
        # A lifecycle manager and physical source connection execute one job at a time.
        with self._lock:
            return self._process_next_job()

    def _process_next_job(self):
        job = self.store.claim_next_job(self.worker_id, self.lease_seconds)
        if not job:
            return None
        return self._process_claimed_job(job)

    @traced_stage('durable_job')
    def _process_claimed_job(self, job):
        job_id, turn_id, token = job["job_id"], job["turn_id"], job["fencing_token"]
        payload = json.loads(job["payload_json"] or "{}")
        identity = payload.get("actor", {})
        actor = self.security.authenticate_actor(job["tenant_id"], job["subject_id"],
            roles=tuple(identity.get("roles", ["analyst"])), permissions=set(identity.get("permissions", [])))
        stopped = threading.Event()
        def heartbeat():
            while not stopped.wait(max(0.1, self.lease_seconds / 3)):
                try:
                    self.store.renew_lease(job_id, self.worker_id, token, self.lease_seconds)
                except LeaseFencingError:
                    return
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            row = self.store.get_turn(actor, turn_id)
            if row["status"] in ("ANSWERED", "DECLINED"):
                self.store.complete_job(job_id, self.worker_id, token)
                return job
            self.security.check_source_access(actor, row["source_id"])
            self.store.restore_conversation(actor, row["conversation_id"], self.manager)
            self.store.restore_execution_state(actor, turn_id, self.manager)
            if job["operation"] == "SUBMIT_QUESTION":
                turn, answer = self.manager.submit_turn(actor, row["conversation_id"], row["source_id"],
                    row["question_text"], proposed_llm_json=payload.get("proposed_llm_json"), turn_id=turn_id)
            elif job["operation"] == "SUBMIT_CLARIFICATION":
                pending_turn = self.manager.get_turn(turn_id)
                if pending_turn.clarification_request and pending_turn.status in (TurnStatus.SUBMITTED, TurnStatus.FAILED):
                    pending_turn.status = TurnStatus.AWAITING_CLARIFICATION
                turn, answer = self.manager.submit_clarification_choice(actor, turn_id, payload["choice_id"])
            else:
                raise ValueError("Unknown job operation")
            final_status = turn.status
            with self.store.transaction():
                self.store.assert_job_ownership(job_id, self.worker_id, token)
                # Successful status is visible only after answer insertion commits.
                if final_status == TurnStatus.ANSWERED:
                    turn.status = TurnStatus.COMPOSING
                self.store.save_turn(turn, actor, job_id, self.worker_id, token)
                turn.status = final_status
                self.store.save_execution_state(turn_id, self.manager, actor, job_id, self.worker_id, token)
                for event in self.manager.audit.get_turn_events(turn_id):
                    self.store.record_audit_event(event)
                if final_status == TurnStatus.AWAITING_CLARIFICATION:
                    self.store.release_job_for_clarification(job_id, self.worker_id, token)
                elif final_status == TurnStatus.ANSWERED:
                    self.manager.get_answer(actor, turn_id)
                    self.store.verify_answer_permissions(actor, self.store.get_turn(actor, turn_id))
                    from backend.presentation.charts import ChartGenerator
                    evidence = self.manager.get_evidence(turn_id)
                    charts = ChartGenerator().generate_charts(list(evidence.facts))
                    self.store.publish_answer_transactional(turn_id, actor, answer,
                        version=1 if job["operation"] == "SUBMIT_QUESTION" else 2,
                        charts=charts, job_id=job_id, worker_id=self.worker_id, fencing_token=token)
                    self.store.save_context_from_manager(turn_id, self.manager)
                    self.store.complete_job(job_id, self.worker_id, token)
                elif final_status == TurnStatus.FAILED:
                    self.store.fail_job(job_id, turn.error_message or "Execution failed", self.worker_id, token,
                        retryable="Provider execution failure" not in (turn.error_message or ""))
                else:
                    self.store.complete_job(job_id, self.worker_id, token)
        except LeaseFencingError:
            self.manager._answers.pop(turn_id, None)
            self.manager._evidence_packages.pop(turn_id, None)
        except AuthorizationError:
            with self.store.transaction():
                try:
                    self.store.assert_job_ownership(job_id, self.worker_id, token)
                    turn = self.store.restore_execution_state(actor, turn_id, self.manager)
                    turn.transition_to(TurnStatus.DECLINED, "Current permissions do not allow this analysis")
                    self.store.save_turn(turn, actor, job_id, self.worker_id, token)
                    self.store.complete_job(job_id, self.worker_id, token)
                except LeaseFencingError:
                    pass
        except Exception:
            logger.exception("Job execution failed: %s", job_id)
            try:
                self.store.fail_job(job_id, "Execution failed; check server diagnostics", self.worker_id, token)
            except LeaseFencingError:
                pass
        finally:
            stopped.set()
            heartbeat_thread.join(timeout=1)
        return job
