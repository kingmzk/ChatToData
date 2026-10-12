"""Comprehensive Phase 4 test suite verifying advanced controls, numerical precision, and security."""
import hmac
import hashlib
import os
import sqlite3
import pytest
from decimal import Decimal

from talk2data.catalog.schema import ScenarioModelDefinition, ScenarioDriverDefinition, SemanticCatalog, TableDefinition, MeasureDefinition, DimensionDefinition, AggregationType, DataType
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.domain.facts import ProtectedFact, SealedEvidencePackage
from talk2data.domain.models import ApiActor, Turn
from talk2data.execution.gateway import GenericSourceGateway, DecimalSumAggregate, DecimalAvgAggregate
from talk2data.compiler.ast import PhysicalQueryPackage
from talk2data.context.followup import FollowUpService
from talk2data.presentation.fallback import DeterministicPresentationFallback, RenderedKpiCard, RenderedAnswerView
from talk2data.presentation.charts import ChartGenerator
from talk2data.science.goal_seek import GovernedGoalSeekEngine, GoalSeekResult, GoalSeekError, GoalSeekStatus
from talk2data.science.scenario import GovernedScenarioEngine
from talk2data.security.disclosure import (
    GovernedDisclosureService,
    DatasetDisclosurePolicy,
    RepeatedQueryAccountingService,
    DisclosurePolicyViolationError,
)
from talk2data.storage.repository import (
    PersistentApplicationStore,
    LeaseFencingError,
    AuditIntegrityError,
)


# ==============================================================================
# 1. Numerical Precision & Independent Fractional-Value Fixtures
# ==============================================================================

def test_sql_to_python_exact_decimal_aggregation():
    """Verify complete storage -> SQL aggregation -> Python path eliminates IEEE-754 binary drift."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount NUMERIC)")
    
    # Insert fractional values known to produce IEEE-754 drift in binary floats:
    # 0.1 + 0.2 = 0.30000000000000004 in floating-point math
    conn.executemany(
        "INSERT INTO transactions (amount) VALUES (?)",
        [("0.1",), ("0.2",), ("0.05",), ("0.15",), ("0.50",)]
    )
    conn.commit()

    gateway = GenericSourceGateway(conn)
    pkg = PhysicalQueryPackage(sql="SELECT SUM(amount) AS total_amount, AVG(amount) AS avg_amount FROM transactions", parameters={})
    rows = gateway.execute_package(pkg)
    
    assert len(rows) == 1
    total_val = Decimal(rows[0]["total_amount"])
    avg_val = Decimal(rows[0]["avg_amount"])

    # 0.1 + 0.2 + 0.05 + 0.15 + 0.50 = 1.00 exactly
    assert total_val == Decimal("1.00")
    # 1.00 / 5 = 0.20 exactly
    assert avg_val == Decimal("0.20")


def test_monetary_exact_accumulation_100_rows():
    """Verify accumulation of 100 fractional rows (0.07 each) equals exactly 7.00 without rounding distortion."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE fee_records (fee TEXT)")
    conn.executemany("INSERT INTO fee_records (fee) VALUES (?)", [("0.07",) for _ in range(100)])
    conn.commit()

    gateway = GenericSourceGateway(conn)
    pkg = PhysicalQueryPackage(sql="SELECT SUM(fee) AS sum_fee FROM fee_records", parameters={})
    rows = gateway.execute_package(pkg)

    sum_fee = Decimal(rows[0]["sum_fee"])
    assert sum_fee == Decimal("7.00")


# ==============================================================================
# 2. Goal Seek Advanced Controls: Continuity, Roots, Discontinuities & Bounds
# ==============================================================================

@pytest.fixture
def non_monotonic_scenario_model():
    """Quadratic scenario model: output = (effort - 30) ** 2 + 100."""
    drivers = {
        "effort": ScenarioDriverDefinition(
            name="effort",
            unit="hours",
            min_value=Decimal("0"),
            max_value=Decimal("60"),
            default_value=Decimal("30"),
            is_adjustable=True,
        )
    }
    return ScenarioModelDefinition(
        model_id="quad_model",
        name="Quadratic Model",
        description="Non-monotonic model with interior minimum",
        output_metric="cost",
        drivers=drivers,
        formula_expression="(effort - 30) ** 2 + 100",
        permitted_adjustments=("effort",),
    )


@pytest.fixture
def linear_scenario_model():
    """Strictly monotonic model: output = 5 * price."""
    drivers = {
        "price": ScenarioDriverDefinition(
            name="price",
            unit="USD",
            min_value=Decimal("10"),
            max_value=Decimal("50"),
            default_value=Decimal("20"),
            is_adjustable=True,
        )
    }
    return ScenarioModelDefinition(
        model_id="linear_model",
        name="Linear Model",
        description="Monotonic pricing model",
        output_metric="revenue",
        drivers=drivers,
        formula_expression="5 * price",
        permitted_adjustments=("price",),
    )


def test_goal_seek_interior_root_and_residual_tolerance(linear_scenario_model):
    """Verify solver finds interior root within requested residual tolerance."""
    solver = GovernedGoalSeekEngine(tolerance=Decimal("0.01"))
    # Target = 175 -> Required price = 35.00
    res = solver.solve(
        model=linear_scenario_model,
        target_metric="revenue",
        target_value=Decimal("175"),
        adjustable_driver="price",
        baseline_drivers={"price": Decimal("20")},
    )
    assert res.status == "FEASIBLE"
    assert res.solved_driver_value == Decimal("35.00")
    assert res.residual_error <= Decimal("0.01")


def test_goal_seek_boundary_solutions(linear_scenario_model):
    """Verify boundary solutions when target equals lower or upper bound output."""
    solver = GovernedGoalSeekEngine(tolerance=Decimal("0.01"))
    # At price=10 (lower bound): output = 50.00
    res_low = solver.solve(
        model=linear_scenario_model,
        target_metric="revenue",
        target_value=Decimal("50"),
        adjustable_driver="price",
        baseline_drivers={"price": Decimal("20")},
    )
    assert res_low.status == "BOUNDARY_SOLUTION"
    assert res_low.solved_driver_value == Decimal("10.00")

    # At price=50 (upper bound): output = 250.00
    res_high = solver.solve(
        model=linear_scenario_model,
        target_metric="revenue",
        target_value=Decimal("250"),
        adjustable_driver="price",
        baseline_drivers={"price": Decimal("20")},
    )
    assert res_high.status == "BOUNDARY_SOLUTION"
    assert res_high.solved_driver_value == Decimal("50.00")


def test_goal_seek_multiple_roots_detection(non_monotonic_scenario_model):
    """Verify multiple roots are detected and flagged on non-monotonic models."""
    solver = GovernedGoalSeekEngine(tolerance=Decimal("0.05"), grid_subdivisions=40)
    # Target = 125 -> (effort - 30)^2 = 25 -> effort = 25 or 35.
    res = solver.solve(
        model=non_monotonic_scenario_model,
        target_metric="cost",
        target_value=Decimal("125"),
        adjustable_driver="effort",
        baseline_drivers={"effort": Decimal("30")},
    )
    assert res.status == "FEASIBLE"
    assert res.has_multiple_roots is True
    assert res.root_count >= 2
    assert any("MULTIPLE ROOTS DETECTED" in a for a in res.facts[0].assumptions)


def test_goal_seek_discontinuity_detection():
    """Verify division by zero / discontinuity within driver range is safely trapped."""
    drivers = {
        "efficiency": ScenarioDriverDefinition(
            name="efficiency",
            unit="ratio",
            min_value=Decimal("-10"),
            max_value=Decimal("10"),
            default_value=Decimal("5"),
            is_adjustable=True,
        )
    }
    disc_model = ScenarioModelDefinition(
        model_id="disc_model",
        name="Discontinuous Model",
        description="Singularity at zero",
        output_metric="utilization",
        drivers=drivers,
        formula_expression="100 / efficiency",
        permitted_adjustments=("efficiency",),
    )
    solver = GovernedGoalSeekEngine(grid_subdivisions=20)
    res = solver.solve(
        model=disc_model,
        target_metric="utilization",
        target_value=Decimal("50"),
        adjustable_driver="efficiency",
        baseline_drivers={"efficiency": Decimal("5")},
    )
    assert res.status == "DISCONTINUITY_DETECTED"
    assert res.solved_driver_value is None


def test_goal_seek_distinguishes_unbracketed_from_proven_unreachable(linear_scenario_model, non_monotonic_scenario_model):
    """Verify strictly monotonic out-of-range target is PROVEN_UNREACHABLE_TARGET while non-monotonic unbracketed target is UNBRACKETED_TARGET."""
    solver = GovernedGoalSeekEngine(tolerance=Decimal("0.01"))
    
    # 1. Monotonic linear model: target 500 is strictly beyond max achievable 250 -> PROVEN_UNREACHABLE_TARGET
    res_lin = solver.solve(
        model=linear_scenario_model,
        target_metric="revenue",
        target_value=Decimal("500"),
        adjustable_driver="price",
        baseline_drivers={"price": Decimal("20")},
    )
    assert res_lin.status == "PROVEN_UNREACHABLE_TARGET"

    # 2. Non-monotonic quadratic model: target 50 (below minimum achievable 100)
    # Not strictly monotonic across grid -> Reported as UNBRACKETED_TARGET
    res_quad = solver.solve(
        model=non_monotonic_scenario_model,
        target_metric="cost",
        target_value=Decimal("50"),
        adjustable_driver="effort",
        baseline_drivers={"effort": Decimal("30")},
    )
    assert res_quad.status == "UNBRACKETED_TARGET"


# ==============================================================================
# 3. Derived Disclosure Secondary Field Sanitization
# ==============================================================================

def test_derived_disclosure_sanitizes_secondary_fields():
    """Verify all secondary fields (prediction intervals, diagnostics, assumptions) are sanitized on suppression."""
    service = GovernedDisclosureService(default_min_entities=5)
    
    historical_fact = ProtectedFact(
        fact_id="fact_hist_1",
        metric="salary",
        calculation_type="HISTORICAL",
        value=Decimal("100000"),
        dimension_values={"dept": "SecretOps"},
    )
    forecast_fact = ProtectedFact(
        fact_id="fact_fc_1",
        metric="salary",
        calculation_type="FORECAST",
        value=Decimal("110000"),
        prediction_interval={"lower": "105000", "upper": "115000"},
        diagnostics={"trend_slope": "5000", "r_squared": "0.98"},
        assumptions=["Forecast assumes baseline headcount of 2 employees."],
    )
    
    # Entity count for SecretOps is 2 (< 5), triggering suppression
    protected_facts = service.inspect_and_protect(
        facts=[historical_fact, forecast_fact],
        tenant_id="tenant_x",
        distinct_entity_counts={"fact_hist_1": 2, "fact_fc_1": 2},
    )
    
    fc_protected = protected_facts[1]
    assert fc_protected.is_suppressed is True
    assert fc_protected.value is None
    # Secondary fields must be completely sanitized
    assert fc_protected.prediction_interval is None
    assert fc_protected.diagnostics == {"suppression_status": "SUPPRESSED"}
    assert "suppressed" in fc_protected.assumptions[0].lower()
    assert "suppressed" in fc_protected.limitations[0].lower()


def test_presentation_fallback_does_not_leak_secondary_fields():
    """Verify RenderedAnswerView and charts redact secondary fields when facts are suppressed."""
    renderer = DeterministicPresentationFallback()
    
    suppressed_goal_seek = ProtectedFact(
        fact_id="fact_gs_suppressed",
        metric="margin",
        calculation_type="GOAL_SEEK",
        value=None,
        is_suppressed=True,
        suppression_reason="Suppressed under privacy policy",
        diagnostics={"suppression_status": "SUPPRESSED"},
    )
    
    pkg = SealedEvidencePackage.seal(
        package_id="pkg_test",
        turn_id="turn_test",
        tenant_id="tenant_a",
        semantic_release_id="rel_1",
        facts=[suppressed_goal_seek],
    )
    view = renderer.render(pkg)
    
    assert view.is_suppressed is True
    assert "[SUPPRESSED]" in view.kpi_cards[0].value
    assert "[SUPPRESSED]" in view.table_rows[0]["Target Value"]
    assert "suppressed under data disclosure policy" in view.summary_text.lower()


# ==============================================================================
# 4. Attempt-Specific Lease Fencing Tokens
# ==============================================================================

def test_attempt_specific_lease_fencing_prevents_stale_publication():
    """Verify attempt-specific fencing token, not worker ID alone, rejects stale executions."""
    store = PersistentApplicationStore(":memory:")
    actor = ApiActor(subject_id="user_1", tenant_id="tenant_corp", roles=["ANALYST"])
    conv_id = store.create_conversation(actor, "Fencing Test")
    
    turn_id = "turn_fence_01"
    t = Turn.create(conversation_id=conv_id, actor=actor, question="What is revenue?", source_id="src_test", turn_id=turn_id)
    store.save_turn(t, actor)
    job, is_new = store.submit_job_idempotent(actor, turn_id, "SUBMIT_QUESTION", "idemp_1")
    
    # 1. Worker A claims job on Attempt 1
    claimed_1 = store.claim_next_job("worker_a", lease_seconds=1)
    assert claimed_1 is not None
    token_1 = claimed_1["fencing_token"]
    assert token_1 is not None
    assert "att1" in token_1

    # 2. Worker A times out; Worker B claims job on Attempt 2
    store.conn.execute("UPDATE jobs SET lease_expires_at = '2020-01-01T00:00:00+00:00' WHERE job_id = ?", (job["job_id"],))
    store.conn.commit()
    claimed_2 = store.claim_next_job("worker_b", lease_seconds=60)
    assert claimed_2 is not None
    token_2 = claimed_2["fencing_token"]
    assert token_2 is not None
    assert "att2" in token_2
    assert token_2 != token_1

    # 3. Worker A wakes up and attempts late publication with stale token_1 -> REJECTED
    view = RenderedAnswerView(
        summary_text="Stale summary",
        kpi_cards=[],
        table_columns=[],
        table_rows=[],
        assumptions=[],
        limitations=[],
        is_suppressed=False,
        evidence_digest="digest_fake",
    )
    with pytest.raises(LeaseFencingError, match="Late publication aborted"):
        store.publish_answer_transactional(
            turn_id=turn_id,
            actor=actor,
            view=view,
            job_id=job["job_id"],
            worker_id="worker_a",
            fencing_token=token_1,
        )

    # 4. Worker B publishes with active token_2 -> SUCCEEDS
    ans_id = store.publish_answer_transactional(
        turn_id=turn_id,
        actor=actor,
        view=view,
        job_id=job["job_id"],
        worker_id="worker_b",
        fencing_token=token_2,
    )
    assert ans_id.startswith("ans_")


# ==============================================================================
# 5. Dataset-Versioned Disclosure Policies & Repeated-Query Differencing
# ==============================================================================

def test_dataset_disclosure_policy_validation():
    """Verify DatasetDisclosurePolicy strictly validates permitted roles, metrics, and operations."""
    policy = DatasetDisclosurePolicy(
        dataset_id="payroll_dataset",
        version="1.0",
        allowed_roles=("PAYROLL_ADMIN",),
        allowed_metrics=("gross_pay", "tax_withheld"),
        allowed_dimensions=("department", "pay_grade"),
        allowed_operations=("TOTAL", "AVERAGE"),
        min_group_threshold=5,
    )

    # Authorized request succeeds
    policy.validate_request(
        user_role="PAYROLL_ADMIN",
        metric="gross_pay",
        dimensions=["department"],
        operation="TOTAL",
    )

    # Unauthorized role rejected
    with pytest.raises(DisclosurePolicyViolationError, match="Role 'ANALYST' is not authorized"):
        policy.validate_request("ANALYST", "gross_pay", ["department"], "TOTAL")

    # Prohibited forecast operation rejected
    with pytest.raises(DisclosurePolicyViolationError, match="prohibited by disclosure policy"):
        policy.validate_request("PAYROLL_ADMIN", "gross_pay", ["department"], "FORECAST")


def test_repeated_query_differencing_attack_blocked():
    """Verify repeated-query differencing protection blocks subset differencing leaking small groups."""
    accounting = RepeatedQueryAccountingService()
    policy = DatasetDisclosurePolicy(
        dataset_id="hr_data",
        version="1.0",
        allowed_roles=("ANALYST",),
        allowed_metrics=("compensation",),
        allowed_dimensions=("department",),
        requires_repeated_query_protection=True,
        min_group_threshold=5,
    )

    # Query 1: Total compensation for Engineering department (10 employees)
    allowed_1, status_1, rec_1 = accounting.evaluate_release(
        policy=policy,
        tenant_id="tenant_hr",
        metric="compensation",
        dimensions=["department"],
        filters=[{"dimension": "department", "operator": "=", "values": ["Engineering"]}],
        entity_count=10,
        turn_id="turn_001",
        conversation_id="conv_1",
        subject_id="analyst_bob",
        released_value="1500000.00",
    )
    assert allowed_1 is True
    assert status_1 == "RELEASE_RECORDED"

    # Query 2 (Identical retry across conversation): Idempotent release allowed without penalty
    allowed_2, status_2, rec_2 = accounting.evaluate_release(
        policy=policy,
        tenant_id="tenant_hr",
        metric="compensation",
        dimensions=["department"],
        filters=[{"dimension": "department", "operator": "=", "values": ["Engineering"]}],
        entity_count=10,
        turn_id="turn_002",
        conversation_id="conv_2",
        subject_id="analyst_alice",
    )
    assert allowed_2 is True
    assert status_2 == "IDEMPOTENT_SCOPE_MATCH"

    # Query 3: Differencing attack: Engineering department excluding Senior Manager (9 employees)
    # Difference in population is 10 - 9 = 1 (< threshold of 5)
    allowed_3, reason_3, _ = accounting.evaluate_release(
        policy=policy,
        tenant_id="tenant_hr",
        metric="compensation",
        dimensions=["department"],
        filters=[
            {"dimension": "department", "operator": "=", "values": ["Engineering"]},
            {"dimension": "title", "operator": "!=", "values": ["Senior Manager"]},
        ],
        entity_count=9,
        turn_id="turn_003",
        conversation_id="conv_3",
        subject_id="analyst_bob",
    )
    assert allowed_3 is False
    assert "differencing violation" in reason_3.lower()


# ==============================================================================
# 6. Audit Trail Integrity Verification & Retention Cleanup
# ==============================================================================

def test_audit_integrity_hash_chain_and_hmac_verification(monkeypatch):
    """Verify hash chain consistency and keyed HMAC signature verification."""
    monkeypatch.setenv("TALK2DATA_AUDIT_KEY", "super_secret_test_audit_key_2026")
    store = PersistentApplicationStore(":memory:")
    actor = ApiActor(subject_id="user_auditor", tenant_id="tenant_sec", roles=["ANALYST"])
    conv_id = store.create_conversation(actor, "Audit Test")
    turn_1 = Turn.create(conversation_id=conv_id, actor=actor, question="Q1", source_id="src_1", turn_id="turn_aud_1")
    turn_2 = Turn.create(conversation_id=conv_id, actor=actor, question="Q2", source_id="src_1", turn_id="turn_aud_2")
    store.save_turn(turn_1, actor)
    store.save_turn(turn_2, actor)

    view = RenderedAnswerView(
        summary_text="Answer text",
        kpi_cards=[],
        table_columns=[],
        table_rows=[],
        assumptions=[],
        limitations=[],
        is_suppressed=False,
        evidence_digest="digest_001",
    )

    store.publish_answer_transactional("turn_aud_1", actor, view)
    store.publish_answer_transactional("turn_aud_2", actor, view)

    # Verify uncorrupted audit chain
    is_valid, count, violations = store.verify_audit_integrity("tenant_sec")
    assert is_valid is True
    assert count == 2
    assert len(violations) == 0

    # Tamper with event payload in database
    store.conn.execute("UPDATE audit_outbox SET payload_digest = 'tampered_fake_digest' WHERE turn_id = ?", ("turn_aud_1",))
    store.conn.commit()

    # Re-verify -> Tampering MUST be detected!
    is_valid_tampered, count_tampered, violations_tampered = store.verify_audit_integrity("tenant_sec")
    assert is_valid_tampered is False
    assert any("Digest mismatch" in v or "HMAC signature mismatch" in v for v in violations_tampered)


def test_retention_cleanup_prunes_expired_records():
    """Verify configured retention prunes expired jobs and disclosure records."""
    store = PersistentApplicationStore(":memory:")
    actor = ApiActor(subject_id="user_admin", tenant_id="tenant_clean", roles=["ADMIN"])
    conv_id = store.create_conversation(actor, "Retention Test")
    turn = Turn.create(conversation_id=conv_id, actor=actor, question="Q", source_id="src_1", turn_id="turn_ret_1")
    store.save_turn(turn, actor)
    
    # Insert completed job older than 100 days
    old_time = "2024-01-01T00:00:00+00:00"
    store.conn.execute(
        """
        INSERT INTO jobs (job_id, turn_id, tenant_id, subject_id, operation, idempotency_key, status, updated_at, created_at)
        VALUES ('job_old', 'turn_ret_1', 'tenant_clean', 'user_admin', 'SUBMIT_QUESTION', 'key_old', 'COMPLETED', ?, ?)
        """,
        (old_time, old_time)
    )
    store.conn.commit()

    pruned = store.prune_expired_records(retention_days=90)
    assert pruned["jobs"] >= 1
    
    remaining = store.conn.execute("SELECT * FROM jobs WHERE job_id = 'job_old'").fetchone()
    assert remaining is None


# ==============================================================================
# 7. Safe Context Enrichment & Calendar Alignment
# ==============================================================================

def test_safe_context_enrichment_and_calendar_alignment():
    """Verify calendar context enrichment and non-causal follow-up generation."""
    store = PersistentApplicationStore(":memory:")
    catalogs = SemanticCatalogStore()
    
    table = TableDefinition(table_name="orders", columns={"order_id": DataType.INTEGER, "order_date": DataType.DATE})
    measure = MeasureDefinition(
        id="sales_revenue", name="sales_revenue", description="Total revenue",
        table="orders", column="amount", aggregation=AggregationType.SUM, base_grain="order", unit="USD"
    )
    dim = DimensionDefinition(id="region", name="region", table="orders", column="region", data_type=DataType.STRING)
    cat = SemanticCatalog(
        release_id="rel_2026",
        epoch=1,
        source_id="finance_dw",
        tables={"orders": table},
        measures={"sales_revenue": measure},
        dimensions={"region": dim},
        fiscal_year_start_month=2,
        default_period_dimension="order_date",
    )
    catalogs.register_catalog(cat, set_active=True)

    actor = ApiActor(subject_id="u1", tenant_id="t1", roles=["ANALYST"])
    followup_svc = FollowUpService(store, catalogs)

    # 1. Calendar enrichment provides fiscal calendar boundaries with non-causal disclaimer
    enrichment = followup_svc.get_calendar_enrichment(actor, "finance_dw", period="2024")
    assert enrichment["fiscal_year_start_month"] == 2
    assert "FISCAL_CALENDAR_MONTH_2" in enrichment["calendar_type"]
    assert "causal" in enrichment["causality_disclaimer"].lower()

    # 2. Contextual follow-up suggestions
    conv_id = store.create_conversation(actor, "Context Conv")
    store.save_analytical_context(
        conversation_id=conv_id,
        turn_id="turn_f1",
        source_id="finance_dw",
        measure="sales_revenue",
        family="TOTAL",
        dimensions=[],
        current_period="2024",
        comparison_period=None,
        semantic_release_id="rel_2026",
        assumptions=[],
    )
    suggestions = followup_svc.get_follow_up_suggestions(actor, conv_id)
    assert len(suggestions) > 0
    assert any("region" in s.lower() for s in suggestions)
    assert any("compare" in s.lower() for s in suggestions)
