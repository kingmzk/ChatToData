"""Tests for Talk2Data Phase 2 Validated Answer Composition, Typed Claims, and Deterministic Fallback."""
from decimal import Decimal
import pytest

from talk2data.domain.facts import ProtectedFact, SealedEvidencePackage
from talk2data.presentation.composition import (
    ClaimValidationError,
    FORBIDDEN_CAUSAL_PATTERNS,
    TypedClaim,
    ValidatedAnswerComposer,
)
from talk2data.presentation.fallback import DeterministicPresentationFallback


@pytest.fixture
def sample_evidence():
    """Create sealed evidence package with verified facts across analysis families."""
    facts = (
        ProtectedFact(
            fact_id="fact_total_2024",
            metric="sales_revenue",
            calculation_type="TOTAL",
            value=Decimal("80000.00"),
            unit="USD",
            period="2024",
            dimension_values={},
            assumptions=["Aggregated across all verified orders in 2024"],
        ),
        ProtectedFact(
            fact_id="fact_slice_electronics",
            metric="sales_revenue",
            calculation_type="GROUPED",
            value=Decimal("50000.00"),
            unit="USD",
            period="2024",
            dimension_values={"category": "Electronics"},
            assumptions=["Rank 1 by revenue"],
        ),
        ProtectedFact(
            fact_id="fact_slice_furniture",
            metric="sales_revenue",
            calculation_type="GROUPED",
            value=Decimal("30000.00"),
            unit="USD",
            period="2024",
            dimension_values={"category": "Furniture"},
            assumptions=["Rank 2 by revenue"],
        ),
        ProtectedFact(
            fact_id="fact_comparison_2024_2023",
            metric="sales_revenue",
            calculation_type="COMPARISON",
            value=Decimal("80000.00"),
            unit="USD",
            period="2024",
            dimension_values={},
            assumptions=["Comparison with 2023 ($60,000.00)", "delta=+20000.00, growth_rate_percent=33.33%"],
        ),
    )
    return SealedEvidencePackage.seal(
        package_id="pkg_test_01",
        turn_id="turn_comp_01",
        tenant_id="tenant_a",
        semantic_release_id="rel_2024",
        facts=list(facts),
    )


def test_valid_typed_claims_accepted(sample_evidence):
    """Verify that accurate typed claims across families pass validation."""
    composer = ValidatedAnswerComposer()

    # 1. Total claim
    claim_total = TypedClaim(
        metric="sales_revenue",
        value=Decimal("80000.00"),
        unit="USD",
        period="2024",
    )
    assert composer.validate_typed_claim(claim_total, sample_evidence.facts) is True

    # 2. Slice / category claim
    claim_slice = TypedClaim(
        metric="sales_revenue",
        value=Decimal("50000.00"),
        unit="USD",
        period="2024",
        category="Electronics",
    )
    assert composer.validate_typed_claim(claim_slice, sample_evidence.facts) is True

    # 3. Direction claim (positive delta = INCREASE)
    claim_dir = TypedClaim(
        metric="sales_revenue",
        value=Decimal("80000.00"),
        unit="USD",
        period="2024",
        direction="INCREASE",
    )
    assert composer.validate_typed_claim(claim_dir, sample_evidence.facts) is True

    # 4. Ranking claim (Electronics is #1)
    claim_rank = TypedClaim(
        metric="sales_revenue",
        value=Decimal("50000.00"),
        unit="USD",
        category="Electronics",
        ranking=1,
    )
    assert composer.validate_typed_claim(claim_rank, sample_evidence.facts) is True

    # 5. Contribution share claim ($50k of $80k slices = 62.5%)
    claim_share = TypedClaim(
        metric="sales_revenue",
        value=Decimal("50000.00"),
        unit="USD",
        category="Electronics",
        contribution_share=Decimal("62.5"),
    )
    assert composer.validate_typed_claim(claim_share, sample_evidence.facts) is True


def test_invalid_typed_claims_rejected(sample_evidence):
    """Verify that inaccurate claims fail with ClaimValidationError."""
    composer = ValidatedAnswerComposer()

    # Metric mismatch
    with pytest.raises(ClaimValidationError, match="Metric mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="operating_profit", value=Decimal("80000.00"), unit="USD"),
            sample_evidence.facts,
        )

    # Value hallucination
    with pytest.raises(ClaimValidationError, match="Value mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="sales_revenue", value=Decimal("99999.00"), unit="USD", period="2024"),
            sample_evidence.facts,
        )

    # Unit mismatch
    with pytest.raises(ClaimValidationError, match="Unit mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="sales_revenue", value=Decimal("80000.00"), unit="EUR", period="2024"),
            sample_evidence.facts,
        )

    # Inverted direction (claiming DECREASE when delta is positive)
    with pytest.raises(ClaimValidationError, match="Direction mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="sales_revenue", value=Decimal("80000.00"), unit="USD", period="2024", direction="DECREASE"),
            sample_evidence.facts,
        )

    # False ranking (claiming Furniture is #1)
    with pytest.raises(ClaimValidationError, match="Ranking mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="sales_revenue", value=Decimal("30000.00"), unit="USD", category="Furniture", ranking=1),
            sample_evidence.facts,
        )

    # False contribution share (claiming Furniture is 80%)
    with pytest.raises(ClaimValidationError, match="Contribution share mismatch"):
        composer.validate_typed_claim(
            TypedClaim(metric="sales_revenue", value=Decimal("30000.00"), unit="USD", category="Furniture", contribution_share=Decimal("80.0")),
            sample_evidence.facts,
        )


def test_causal_language_rejection_and_deterministic_fallback(sample_evidence):
    """Verify that causal claims ('because', 'due to', etc.) are rejected and trigger fallback."""
    composer = ValidatedAnswerComposer()
    fallback_view = DeterministicPresentationFallback().render(sample_evidence)

    # Candidate with unsupported causal claim
    causal_narrative = (
        "Total sales revenue in 2024 was $80,000.00 because of high consumer demand and new store openings."
    )

    # Must fall back to deterministic view without crashing or allowing causal prose
    view = composer.compose_and_validate(sample_evidence, candidate_summary=causal_narrative)
    assert view.summary_text == fallback_view.summary_text
    assert "because" not in view.summary_text.lower()
    assert len(view.kpi_cards) == len(fallback_view.kpi_cards)
    assert view.kpi_cards[0].value == "80,000.00"
    assert view.kpi_cards[0].unit == "USD"


def test_hallucinated_number_triggers_deterministic_fallback(sample_evidence):
    """Verify that unverified numbers in model prose trigger fallback."""
    composer = ValidatedAnswerComposer()
    fallback_view = DeterministicPresentationFallback().render(sample_evidence)

    # Narrative citing numbers that don't exist in facts ($125,000.00)
    hallucinated_narrative = "Total sales revenue was $125,000.00 in 2024."
    view = composer.compose_and_validate(sample_evidence, candidate_summary=hallucinated_narrative)

    # Safe fallback
    assert view.summary_text == fallback_view.summary_text
    assert "$125,000.00" not in view.summary_text


def test_valid_factual_summary_accepted(sample_evidence):
    """Verify that compliant model narrative adhering strictly to verified facts is accepted."""
    composer = ValidatedAnswerComposer()

    valid_narrative = "In 2024, total sales_revenue reached $80,000.00 USD across all recorded transactions."
    view = composer.compose_and_validate(sample_evidence, candidate_summary=valid_narrative)

    assert view.summary_text == valid_narrative
    # Server-rendered KPI cards and tables are preserved
    assert len(view.kpi_cards) > 0
    assert view.kpi_cards[0].value == "80,000.00"
    assert view.kpi_cards[0].unit == "USD"
