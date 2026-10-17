"""Intent parser with bounded retry and robust normalization for LLM outputs."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
from backend.intent.schema import StructuredIntent, AnalysisFamily, FilterSpec


@dataclass(frozen=True)
class ParseResult:
    success: bool
    intent: Optional[StructuredIntent] = None
    error: Optional[str] = None
    is_malformed: bool = False
    is_provider_failure: bool = False
    retry_count: int = 0


class IntentParser:
    """Parses LLM structured intent output with bounded retry on malformed JSON."""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def parse_with_bounded_retry(
        self,
        raw_text: str,
        retry_fn: Optional[Callable[[str, int], str]] = None,
    ) -> ParseResult:
        """Attempt to parse JSON with normalization; if malformed, retry up to max_retries."""
        current_text = raw_text
        for attempt in range(self.max_retries + 1):
            try:
                data = self._clean_and_load_json(current_text)
                if isinstance(data, dict) and ("provider_failure" in data or "error" in data):
                    provider_msg = data.get("provider_failure") or data.get("error")
                    return ParseResult(
                        success=False,
                        error=f"Provider execution failure: {provider_msg}",
                        is_provider_failure=True,
                        retry_count=attempt,
                    )

                intent = self._dict_to_intent(data)
                return ParseResult(
                    success=True,
                    intent=intent,
                    retry_count=attempt,
                )

            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                if attempt < self.max_retries and retry_fn is not None:
                    # Request bounded retry from generator/model
                    try:
                        current_text = retry_fn(str(exc), attempt + 1)
                        continue
                    except Exception as retry_err:
                        return ParseResult(
                            success=False,
                            error=f"Retry function failed: {retry_err}",
                            is_malformed=True,
                            retry_count=attempt + 1,
                        )
                return ParseResult(
                    success=False,
                    error=f"Malformed JSON after {attempt} retries: {exc}",
                    is_malformed=True,
                    retry_count=attempt,
                )

        return ParseResult(
            success=False,
            error="Exceeded maximum retries for malformed JSON.",
            is_malformed=True,
            retry_count=self.max_retries,
        )

    def _clean_and_load_json(self, text: str) -> dict[str, Any]:
        """Strip markdown fences, trailing commas, and extract JSON object."""
        s = text.strip()
        # Remove markdown code blocks if present
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            s = "\n".join(lines).strip()

        # Extract innermost or outermost { ... }
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            s = match.group(0)

        # Remove trailing commas before closing braces/brackets
        s = re.sub(r",\s*([\]}])", r"\1", s)

        return json.loads(s)

    def _dict_to_intent(self, data: dict[str, Any]) -> StructuredIntent:
        """Convert a raw dictionary into StructuredIntent value object."""
        if not isinstance(data, dict):
            raise ValueError("Intent must be a JSON object")
        allowed = set(StructuredIntent.__dataclass_fields__)
        if set(data) - allowed:
            raise ValueError("Unknown intent fields")
        for field in ("dimensions", "filters", "assumptions"):
            if field in data and not isinstance(data[field], (list, tuple)):
                raise ValueError(f"{field} must be a list")
        if data.get("horizon") is not None and (isinstance(data["horizon"], bool) or not isinstance(data["horizon"], int)):
            raise ValueError("horizon must be an integer")
        from decimal import Decimal
        bounds = data.get("bounds")
        if bounds is not None:
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ValueError("bounds requires two values")
            bounds = tuple(Decimal(str(value)) for value in bounds)
            if not all(value.is_finite() for value in bounds):
                raise ValueError("bounds must be finite")
        family_str = data.get("family")
        family = None
        if family_str:
            try:
                family = AnalysisFamily(str(family_str).upper())
            except ValueError:
                # If invalid family string, leave as None for validator to catch
                raise ValueError("Unsupported analysis family")

        filters: list[FilterSpec] = []
        for f in data.get("filters", []):
            if isinstance(f, dict):
                dim = f.get("dimension") or f.get("column") or ""
                op = f.get("operator", "=")
                raw_vals = f.get("values") if "values" in f else f.get("value", [])
                if not isinstance(raw_vals, (list, tuple)):
                    raw_vals = [raw_vals]
                filters.append(FilterSpec(dimension=dim, operator=op, values=tuple(raw_vals)))

        return StructuredIntent(
            measure=data.get("measure"),
            family=family,
            dimensions=tuple(data.get("dimensions", [])),
            filters=tuple(filters),
            current_period=data.get("current_period"),
            comparison_period=data.get("comparison_period"),
            ambiguity_code=data.get("ambiguity_code"),
            assumptions=tuple(data.get("assumptions", [])),
            horizon=data.get("horizon"),
            scenario_model=data.get("scenario_model"),
            adjustments=data.get("adjustments", {}),
            target_metric=data.get("target_metric"),
            target_value=data.get("target_value"),
            adjustable_driver=data.get("adjustable_driver"),
            bounds=bounds,
        )
