"""Tests for Minimum-Group Suppression, Complementary Suppression, and Deductive Disclosure Prevention."""
from decimal import Decimal
from talk2data.domain.facts import ProtectedFact, SealedEvidencePackage
from talk2data.domain.models import TurnStatus
from talk2data.presentation.fallback import DeterministicPresentationFallback
from talk2data.security.disclosure import GovernedDisclosureService


def test_minimum_group_suppression_distinct_entities():
    """Verify that a fact representing fewer distinct protected entities than min_entities is suppressed."""
    disclosure = GovernedDisclosureService(default_min_entities=5)

    fact = ProtectedFact(
        fact_id="fact_small_slice",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("12000.00"),
        dimension_values={"country": "Iceland"},
    )

    # Distinct entity count is only 2 (< min_entities 5)
    protected = disclosure.inspect_and_protect(
        [fact],
        tenant_id="tenant_a",
        distinct_entity_counts={"fact_small_slice": 2},
    )

    assert len(protected) == 1
    assert protected[0].is_suppressed is True
    assert protected[0].value is None
    assert "Minimum-group suppression" in protected[0].suppression_reason
    assert "distinct entity count 2 < 5" in protected[0].suppression_reason


def test_complementary_suppression_prevents_deduction_from_totals():
    """Verify that complementary suppression masks an additional cell when exactly 1 slice is suppressed.
    
    If Total = 100k, Slice US = 80k, and Slice Iceland = 20k (suppressed):
    Leaving Slice US visible allows deduction of Iceland = 100k - 80k = 20k.
    The disclosure service must suppress US as a complementary cell.
    """
    disclosure = GovernedDisclosureService(default_min_entities=5)

    fact_total = ProtectedFact(
        fact_id="fact_tot",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("100000.00"),
    )
    fact_us = ProtectedFact(
        fact_id="fact_us",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("80000.00"),
        dimension_values={"country": "US"},
    )
    fact_is = ProtectedFact(
        fact_id="fact_is",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("20000.00"),
        dimension_values={"country": "Iceland"},
    )

    facts = [fact_total, fact_us, fact_is]
    # Iceland has 2 customers (< 5 threshold); US has 25 customers; Total has 27 customers
    entity_counts = {"fact_is": 2, "fact_us": 25, "fact_tot": 27}

    protected = disclosure.inspect_and_protect(
        facts=facts,
        tenant_id="tenant_a",
        distinct_entity_counts=entity_counts,
    )

    # Primary suppression on Iceland
    is_protected = next(f for f in protected if f.dimension_values.get("country") == "Iceland")
    assert is_protected.is_suppressed is True
    assert is_protected.value is None

    # Complementary suppression on US
    us_protected = next(f for f in protected if f.dimension_values.get("country") == "US")
    assert us_protected.is_suppressed is True
    assert us_protected.value is None
    assert "Complementary suppression" in us_protected.suppression_reason


def test_suppressed_values_do_not_leak_in_presentation():
    """Verify that suppressed facts render as [SUPPRESSED] in summary, KPIs, and tables."""
    disclosure = GovernedDisclosureService(default_min_entities=5)
    presentation = DeterministicPresentationFallback()

    fact = ProtectedFact(
        fact_id="fact_salary",
        metric="Employee Salary",
        calculation_type="TOTAL",
        value=Decimal("125000.00"),
        unit="USD",
    )

    protected = disclosure.inspect_and_protect([fact], tenant_id="tenant_a")
    assert protected[0].is_suppressed is True

    package = SealedEvidencePackage.seal(
        package_id="pkg_suppressed",
        turn_id="turn_test",
        tenant_id="tenant_a",
        semantic_release_id="rel_1",
        facts=protected,
    )

    view = presentation.render(package)
    assert view.is_suppressed is True
    assert "[SUPPRESSED]" in view.kpi_cards[0].value
    assert "125000" not in view.summary_text
    assert "suppressed" in view.summary_text.lower()


def test_unsupported_disclosure_protections_documented():
    """Verify documentation of Phase 1 limitations (no cross-query differential privacy, tested arithmetic deductions)."""
    doc = GovernedDisclosureService.__doc__.lower()
    assert "differential privacy" in doc
    assert "cross-query" in doc
    assert "tested arithmetic" in doc and "deductions" in doc
    assert "unsupported disclosure protections must remain disabled" in doc


def test_dataset_requiring_unsupported_disclosure_protection_is_disabled(test_setup):
    """Datasets or metrics requiring unsupported disclosure protection (e.g. DP) are disabled and declined."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_unsupported_dp",
        source_id=source_id,
        question="Show unsupported microdata.",
        proposed_llm_json='{"measure": "unsupported_microdata", "family": "TOTAL"}',
    )

    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "unsupported disclosure protection" in turn.decline_reason.lower()

