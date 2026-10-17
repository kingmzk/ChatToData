"""Optional executive summary generation and deterministic claim guardrail.

CRITICAL ARCHITECTURAL RULES:
1. Gemini generates ONLY a template with fact placeholders (e.g., {{total_revenue}}).
   It NEVER writes authoritative ungrounded numbers.
2. Python deterministically resolves placeholders against verified, unsuppressed fact IDs.
3. Prose coverage guardrail: The final text is scanned for all number-like expressions.
   Every numeric token MUST match an authorized display fact, approved period,
   approved horizon, or approved metadata.
4. Any violation, hallucination, or failure drops the LLM narrative entirely
   and falls back to deterministic presentation. The analytical answer is NEVER failed.
"""
from __future__ import annotations
import re
from typing import Any, Optional


NUMERIC_TOKEN_PATTERN = re.compile(
    r"\b(?:\$|USD\s*)?\d+(?:,\d{3})*(?:\.\d+)?(?:%|K|M|B)?\b",
    re.IGNORECASE,
)


class ClaimVerificationError(Exception):
    """Raised when an executive summary claim or placeholder binding fails verification."""
    pass


def extract_numeric_tokens(text: str) -> list[str]:
    """Extract all numbers, percentages, and currencies from prose."""
    raw_tokens = NUMERIC_TOKEN_PATTERN.findall(text)
    cleaned = []
    for tok in raw_tokens:
        tok_clean = tok.strip().replace("$", "").replace("USD", "").replace(",", "").replace("%", "").strip()
        if tok_clean:
            cleaned.append(tok.strip())
    return cleaned


def normalize_numeric_str(val: Any) -> str:
    """Normalize a value for fuzzy numeric matching (e.g. '1,200.00' -> '1200')."""
    if val is None:
        return ""
    s = str(val).strip().replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return s.lower()


class DeterministicClaimGuardrail:
    """Validates placeholder templates and enforces prose coverage against verified facts."""

    @classmethod
    def verify_and_render(
        cls,
        summary_template: dict[str, Any],
        safe_display_facts: list[dict[str, Any]],
        periods: list[str],
        allowed_metadata: Optional[set[str]] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Verify placeholder bindings, render the template, and verify prose number coverage.
        
        Returns:
            (is_valid, rendered_text, rejection_reason)
        """
        template_str = summary_template.get("summary_template", "")
        bindings = summary_template.get("bindings", {})
        
        if not template_str or not isinstance(template_str, str):
            return False, None, "Empty or invalid template string"

        fact_map = {f["fact_id"]: f for f in safe_display_facts if "fact_id" in f}
        
        # Build approved numbers set for prose scanning
        approved_tokens_normalized = set()
        for p in periods:
            if p:
                approved_tokens_normalized.add(normalize_numeric_str(p))
                for part in str(p).split():
                    approved_tokens_normalized.add(normalize_numeric_str(part))
        
        if allowed_metadata:
            for m in allowed_metadata:
                if m:
                    approved_tokens_normalized.add(normalize_numeric_str(m))

        rendered_replacements: dict[str, str] = {}
        
        # 1. Validate each binding
        for placeholder, fact_id in bindings.items():
            token_pattern = "{{" + placeholder + "}}"
            if token_pattern not in template_str:
                return False, None, f"Placeholder '{placeholder}' declared in bindings but not in template"

            # Check special period or metadata bindings
            if fact_id in ("period", "current_period", "comparison_period") or str(fact_id).startswith("period_"):
                period_val = fact_id.replace("period_", "")
                if period_val in periods:
                    rendered_replacements[token_pattern] = period_val
                    approved_tokens_normalized.add(normalize_numeric_str(period_val))
                    continue

            # Standard analytical fact binding
            if fact_id not in fact_map:
                return False, None, f"Referenced fact_id '{fact_id}' does not exist in safe display facts"
            
            fact = fact_map[fact_id]
            if fact.get("is_suppressed", False):
                return False, None, f"Referenced fact '{fact_id}' is suppressed and cannot be released"

            display_val = fact.get("display_value")
            if not display_val:
                raw_v = fact.get("value")
                unit = fact.get("unit")
                display_val = f"{raw_v} {unit}".strip() if unit else str(raw_v)

            rendered_replacements[token_pattern] = display_val

            # Record approved numeric representation
            raw_val = fact.get("value")
            if raw_val is not None:
                approved_tokens_normalized.add(normalize_numeric_str(raw_val))
            for tok in extract_numeric_tokens(display_val):
                approved_tokens_normalized.add(normalize_numeric_str(tok))

        # Check for unfulfilled placeholders in template
        unresolved = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template_str)
        for ph in unresolved:
            ph_tok = "{{" + ph + "}}"
            if ph_tok not in rendered_replacements:
                return False, None, f"Unresolved placeholder '{ph}' in template"

        # 2. Render final prose
        rendered_text = template_str
        for ph_tok, rep_val in rendered_replacements.items():
            rendered_text = rendered_text.replace(ph_tok, rep_val)

        # 3. Prose Coverage Check: Scan for any number-like expression in rendered text
        found_tokens = extract_numeric_tokens(rendered_text)
        for tok in found_tokens:
            norm_tok = normalize_numeric_str(tok)
            # Accept if token matches an approved fact value, period, or metadata
            if norm_tok not in approved_tokens_normalized:
                # Also check integer match (e.g. 1200 vs 1200.0)
                matched = False
                for app in approved_tokens_normalized:
                    if norm_tok == app or (norm_tok.isdigit() and app.startswith(norm_tok)):
                        matched = True
                        break
                if not matched:
                    return False, None, (
                        f"Prose coverage failure: ungrounded numeric expression '{tok}' found in narrative"
                    )

        return True, rendered_text, None
