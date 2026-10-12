"""Validated answer composition engine verifying typed claims against protected facts."""
from __future__ import annotations
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from talk2data.domain.facts import ProtectedFact, SealedEvidencePackage
from talk2data.presentation.fallback import (
    DeterministicPresentationFallback,
    RenderedAnswerView,
    RenderedKpiCard,
)


FORBIDDEN_CAUSAL_PATTERNS = [
    re.compile(r"\bbecause\b", re.IGNORECASE),
    re.compile(r"\bcaused by\b", re.IGNORECASE),
    re.compile(r"\bdue to\b", re.IGNORECASE),
    re.compile(r"\bas a result of\b", re.IGNORECASE),
    re.compile(r"\bled to\b", re.IGNORECASE),
    re.compile(r"\bdriven by\b", re.IGNORECASE),
    re.compile(r"\bproof\s+of\s+fraud\b", re.IGNORECASE),
    re.compile(r"\bfraudulent\b", re.IGNORECASE),
    re.compile(r"\bbusiness\s+failure\b", re.IGNORECASE),
    re.compile(r"\boperational\s+failure\b", re.IGNORECASE),
]


class ClaimValidationError(Exception):
    """Raised when composed prose violates protected facts or makes unsupported claims."""
    pass


@dataclass(frozen=True)
class TypedClaim:
    """Typed analytical claim covering supported analysis families."""
    metric: str
    value: Decimal
    unit: str
    period: Optional[str] = None
    category: Optional[str] = None
    direction: Optional[str] = None  # "INCREASE", "DECREASE", "FLAT"
    comparison_basis: Optional[str] = None  # e.g., "PRIOR_PERIOD"
    ranking: Optional[int] = None
    contribution_share: Optional[Decimal] = None


class ValidatedAnswerComposer:
    """Validates candidate model narratives and typed claims against authoritative protected facts, falling back to deterministic templates."""

    def __init__(self, fallback_engine: Optional[DeterministicPresentationFallback] = None):
        self.fallback_engine = fallback_engine or DeterministicPresentationFallback()

    def validate_typed_claim(self, claim: TypedClaim, facts: tuple[ProtectedFact, ...]) -> bool:
        """Validate a single typed claim against the set of protected facts.
        
        Verifies:
        - Metric name and unit consistency.
        - Value correctness for specified period and/or category.
        - Direction consistency with verified comparison delta.
        - Ranking order validity.
        - Contribution share calculation against proportion facts.
        """
        if not facts:
            raise ClaimValidationError("Cannot validate claim: No protected facts available.")

        # 1. Metric name check
        if claim.metric.lower() != facts[0].metric.lower():
            raise ClaimValidationError(
                f"Metric mismatch: Claim cites '{claim.metric}' but verified metric is '{facts[0].metric}'."
            )

        # 2. Unit check
        if claim.unit.lower() != facts[0].unit.lower():
            raise ClaimValidationError(
                f"Unit mismatch: Claim cites '{claim.unit}' but verified unit is '{facts[0].unit}'."
            )

        # 3. Match candidate fact by period / category / comparison
        matching_fact: Optional[ProtectedFact] = None
        for f in facts:
            if claim.direction and f.calculation_type != "COMPARISON" and not any("delta=" in a for a in f.assumptions):
                continue
            matches_period = True
            if claim.period and f.period:
                matches_period = (claim.period.lower() == f.period.lower())
            elif claim.period and not f.period:
                matches_period = False

            matches_cat = True
            if claim.category and f.dimension_values:
                matches_cat = any(claim.category.lower() == str(v).lower() for v in f.dimension_values.values())
            elif claim.category and not f.dimension_values:
                matches_cat = False

            if matches_period and matches_cat:
                matching_fact = f
                break

        if not matching_fact:
            # Fall back to first fact if neither period nor category was specified
            if not claim.period and not claim.category and not claim.direction:
                matching_fact = facts[0]
            else:
                raise ClaimValidationError(
                    f"No verified fact found matching period='{claim.period}', category='{claim.category}', direction='{claim.direction}'."
                )

        # 4. Value check (exact Decimal equality within 0.01 precision)
        if abs(matching_fact.value - claim.value) > Decimal("0.01"):
            raise ClaimValidationError(
                f"Value mismatch: Claim states {claim.value} but verified fact is {matching_fact.value}."
            )

        # 5. Direction check for comparisons
        if claim.direction:
            delta_val = None
            for asm in matching_fact.assumptions:
                if "delta=" in asm:
                    delta_str = asm.split("delta=")[-1].split(",")[0].strip()
                    try:
                        delta_val = Decimal(delta_str)
                    except Exception:
                        pass
            if delta_val is None:
                raise ClaimValidationError(f"Cannot claim direction '{claim.direction}' on non-comparison fact.")
            expected_dir = "INCREASE" if delta_val > 0 else ("DECREASE" if delta_val < 0 else "FLAT")
            if claim.direction.upper() != expected_dir:
                raise ClaimValidationError(
                    f"Direction mismatch: Claim states '{claim.direction}' but verified delta indicates '{expected_dir}'."
                )

        # 6. Ranking check
        if claim.ranking is not None:
            sorted_facts = sorted([f for f in facts if f.dimension_values], key=lambda x: x.value, reverse=True)
            try:
                rank_index = sorted_facts.index(matching_fact) + 1
                if claim.ranking != rank_index:
                    raise ClaimValidationError(
                        f"Ranking mismatch: Claim ranks item at #{claim.ranking} but verified rank is #{rank_index}."
                    )
            except ValueError:
                pass

        # 7. Contribution share check
        if claim.contribution_share is not None:
            # Check against total across facts
            total_val = sum(f.value for f in facts if f.dimension_values)
            if total_val > Decimal(0):
                computed_share = (matching_fact.value / total_val) * Decimal(100)
                if abs(computed_share - claim.contribution_share) > Decimal("0.5"):
                    raise ClaimValidationError(
                        f"Contribution share mismatch: Claim states {claim.contribution_share}% but computed share is {computed_share:.2f}%."
                    )

        return True

    def compose_and_validate(
        self,
        evidence: SealedEvidencePackage,
        candidate_summary: Optional[str] = None,
        claims: Optional[list[TypedClaim]] = None,
    ) -> RenderedAnswerView:
        """Render answer view, validating candidate narrative and claims if supplied, or using deterministic fallback."""
        fallback_view = self.fallback_engine.render(evidence)

        if not candidate_summary or not candidate_summary.strip():
            return fallback_view

        try:
            # Validate typed claims if provided
            if claims:
                for cl in claims:
                    self.validate_typed_claim(cl, evidence.facts)

            # Validate candidate narrative summary
            self._validate_summary(candidate_summary, evidence.facts)

            # Candidate is valid: replace summary_text while preserving server-rendered KPI cards & tables
            return RenderedAnswerView(
                summary_text=candidate_summary.strip(),
                kpi_cards=fallback_view.kpi_cards,
                table_columns=fallback_view.table_columns,
                table_rows=fallback_view.table_rows,
                assumptions=fallback_view.assumptions,
                limitations=fallback_view.limitations,
                is_suppressed=fallback_view.is_suppressed,
                evidence_digest=fallback_view.evidence_digest,
            )
        except ClaimValidationError:
            # Fall back safely to server-rendered deterministic templates
            return fallback_view

    def _validate_summary(self, summary: str, facts: tuple[ProtectedFact, ...]) -> None:
        """Ensure candidate summary contains no causal claims and respects fact bounds."""
        # 1. Reject unsupported causal claims
        for pat in FORBIDDEN_CAUSAL_PATTERNS:
            if pat.search(summary):
                raise ClaimValidationError(f"Unsupported causal claim detected matching pattern '{pat.pattern}'.")

        # 2. Check for metric presence
        if facts:
            primary_metric = facts[0].metric.lower()
            tokens = set(re.findall(r"\w+", primary_metric))
            if not any(token in summary.lower() for token in tokens if len(token) > 2):
                raise ClaimValidationError(f"Summary does not reference required metric '{facts[0].metric}'.")

        # 3. Check that numbers cited in the summary match verified fact values
        numeric_matches = re.findall(r"[-+]?[0-9,]+\.?[0-9]*", summary)
        known_numeric_values: set[str] = set()

        for f in facts:
            if f.value is not None:
                val_str = f"{f.value:.2f}"
                val_int = f"{int(f.value)}" if f.value == int(f.value) else val_str
                val_commas = f"{f.value:,.2f}"
                known_numeric_values.add(val_str)
                known_numeric_values.add(val_int)
                known_numeric_values.add(val_commas)
            if f.prediction_interval:
                if "lower" in f.prediction_interval:
                    known_numeric_values.add(str(f.prediction_interval["lower"]))
                if "upper" in f.prediction_interval:
                    known_numeric_values.add(str(f.prediction_interval["upper"]))
            if f.period:
                known_numeric_values.add(f.period)

        for match in numeric_matches:
            clean = match.replace(",", "")
            # Ignore 4-digit years like 2021, 2022, 2023, 2024
            if re.match(r"^20\d\d$", clean):
                continue
            # If numeric value is present in summary, it must match one of the facts
            matched = any(
                clean == v.replace(",", "") or (Decimal(clean) == Decimal(v.replace(",", "")) if clean and not clean.endswith(".") else False)
                for v in known_numeric_values
                if v and not v.startswith("20")
            )
            # Allow common small numbers like 1, 2, 3 if describing slices
            if not matched and clean not in ("0", "1", "2", "3", "4", "5"):
                raise ClaimValidationError(f"Summary cites unverifiable numeric value '{match}'.")
