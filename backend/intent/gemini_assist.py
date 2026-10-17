"""Gemini LLM Intent Assist implementing catalog-restricted prompt construction."""
from __future__ import annotations
from backend.observability import traced_stage
import os
import json
from typing import Optional
from dotenv import load_dotenv

from backend.catalog.pinning import AccessibleVocabulary
from backend.intent.schema import StructuredIntent
from backend.intent.parser import IntentParser

load_dotenv()


class GeminiIntentAssist:
    """Translate natural language using only accessible catalog metadata.

    Model selection is explicit and provider availability is verified by live tests,
    not assumed from a configured model name.
    """

    SYSTEM_PROMPT = """You are an analytical intent interpreter in Talk2Data.
Your job is to translate the user's natural language analytics question into a STRICT structured analytical intent JSON object.

RULES:
1. ONLY select measures and dimensions from the approved lists provided.
2. DO NOT invent metrics, dimensions, tables, SQL, or code.
3. If the user's question asks to compare periods but omits periods or ambiguity exists, specify "ambiguity_code".
4. If the question asks for growth contribution (e.g. 'contributed most to growth'), set family to "CONTRIBUTION".
5. Supported families: TOTAL, AVERAGE, TREND, COMPARISON, CONTRIBUTION, FORECAST, ANOMALY, SCENARIO, GOAL_SEEK.
5a. Set dimensions to [] unless the user explicitly asks for a breakdown ("by", "per", "each") or a trend. A year mentioned as a period is NOT a request to group by year. "Across all years" requests one total, not a year breakdown.
5b. Put requested periods in current_period/comparison_period. Do not duplicate them in filters. Filters are for additional explicitly requested restrictions, such as region or customer segment.
5c. When the question explicitly requests an average or mean, use family AVERAGE and the approved AVG measure, not family TOTAL. Other requests for a pre-defined average metric may also use AVERAGE.
6. Return ONLY a valid JSON object matching this schema:
{
  "measure": "<measure_id or null>",
  "family": "<TOTAL | AVERAGE | TREND | COMPARISON | CONTRIBUTION | FORECAST | ANOMALY | SCENARIO | GOAL_SEEK>",
  "dimensions": ["<dimension_id>", ...],
  "filters": [
    {"dimension": "<dim_id>", "operator": "=", "values": ["<val>"]}
  ],
  "current_period": "<e.g. 2024 or null>",
  "comparison_period": "<e.g. 2023 or null>",
  "ambiguity_code": "<code string or null>",
  "horizon": null, "scenario_model": null, "adjustments": {},
  "target_metric": null, "target_value": null, "adjustable_driver": null, "bounds": null,
  "assumptions": []
}"""

    RESPONSE_SCHEMA = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "measure": {"type": ["string", "null"]},
            "family": {"type": ["string", "null"], "enum": ["TOTAL","AVERAGE","TREND","COMPARISON","CONTRIBUTION","FORECAST","ANOMALY","SCENARIO","GOAL_SEEK",None]},
            "dimensions": {"type":"array","items":{"type":"string"}},
            "filters": {"type":"array","items":{"type":"object","additionalProperties":False,"properties":{
                "dimension":{"type":"string"},"operator":{"type":"string","enum":["=","!=","IN",">","<",">=","<=","BETWEEN"]},
                "values":{"type":"array","items":{"type":["string","number","boolean"]}}},"required":["dimension","operator","values"]}},
            "current_period":{"type":["string","null"]}, "comparison_period":{"type":["string","null"]},
            "ambiguity_code":{"type":["string","null"]}, "assumptions":{"type":"array","items":{"type":"string"}},
            "horizon":{"type":["integer","null"]}, "scenario_model":{"type":["string","null"]},
            "adjustments":{"type":"object","additionalProperties":{"type":"number"}},
            "target_metric":{"type":["string","null"]},"target_value":{"type":["number","null"]},
            "adjustable_driver":{"type":["string","null"]},"bounds":{"type":["array","null"],"items":{"type":"number"},"minItems":2,"maxItems":2}
        },
        "required":["measure","family","dimensions","filters","current_period","comparison_period","ambiguity_code","assumptions"]
    }

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        from backend.config import get_canonical_model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or get_canonical_model()

        self.parser = IntentParser()

    def build_catalog_prompt(self, vocabulary: AccessibleVocabulary, question: str) -> str:
        """Construct prompt containing strictly accessible metadata definitions."""
        measures_info = [
            f"- {m.id}: {m.name} ({m.description}) [Aggregation: {m.aggregation.value}; Allowed families: {', '.join(m.allowed_families)}; Allowed dimensions: {', '.join(m.allowed_dimensions)}]"
            for m in vocabulary.measures.values()
        ]
        dimensions_info = [
            f"- {d.id}: {d.name} (type: {d.data_type.value})"
            for d in vocabulary.dimensions.values()
        ]

        from dataclasses import asdict
        science_metadata = json.dumps({"scenario_models": {k: asdict(v) for k,v in vocabulary.scenario_models.items()},
            "time_series_policies": {k: asdict(v) for k,v in vocabulary.time_series_policies.items()}}, default=str)
        return f"""APPROVED SCIENCE METADATA:
{science_metadata}

APPROVED MEASURES:
{chr(10).join(measures_info)}

APPROVED DIMENSIONS:
{chr(10).join(dimensions_info)}

USER QUESTION:
"{question}"
"""

    @traced_stage('gemini_interpretation')
    def interpret(self, vocabulary: AccessibleVocabulary, question: str, max_retries: int = 2) -> str:
        """Invoke Gemini API to generate structured JSON analytical intent with bounded backoff for 503s."""
        if not self.api_key:
            return '{"provider_failure": "Gemini API key is not configured in environment."}'

        import time
        from google import genai

        last_error = ""
        last_status = None
        for attempt in range(max_retries + 1):
            try:
                client = genai.Client(api_key=self.api_key, http_options={"timeout": 20000})
                prompt = self.build_catalog_prompt(vocabulary, question)

                response = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "system_instruction": self.SYSTEM_PROMPT,
                        "response_mime_type": "application/json",
                        "response_json_schema": self.RESPONSE_SCHEMA,
                        "temperature": 0.0,
                    },
                )
                return response.text or "{}"
            except Exception as exc:
                last_error = str(exc)
                last_status = getattr(exc, "code", None)
                import logging
                logging.getLogger(__name__).warning("Gemini request failed: %s", last_error.replace(self.api_key, "[REDACTED]")[:1000])
                if ("503" in last_error or "UNAVAILABLE" in last_error) and attempt < max_retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break

        return json.dumps({"provider_failure": f"Gemini API request failed (status {last_status or 'unknown'}); check provider availability or quota."})

