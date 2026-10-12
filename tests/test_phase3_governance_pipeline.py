"""End-to-end governed pipeline verification for Phase 3 Decision Science."""
from decimal import Decimal
import json
import sqlite3
import pytest

from talk2data.catalog.pinning import SemanticSnapshotPin
from talk2data.catalog.schema import (
    AggregationType,
    DataType,
    DimensionDefinition,
    MeasureDefinition,
    ScenarioDriverDefinition,
    ScenarioModelDefinition,
    SemanticCatalog,
    TableDefinition,
    TimeSeriesPolicy,
)
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.domain.models import ApiActor, TurnStatus
from talk2data.execution.gateway import GenericSourceGateway
from talk2data.intent.schema import AnalysisFamily, StructuredIntent
from talk2data.lifecycle.session import ConversationLifecycleManager
from talk2data.security.auth import SecurityRuntime
from talk2data.security.disclosure import GovernedDisclosureService
from talk2data.storage.repository import PersistentApplicationStore


@pytest.fixture
def test_setup(tmp_path):
    """Construct full governed testing environment with persistent store and decision science catalog."""
    db_file = str(tmp_path / "test_pipeline.db")
    store = PersistentApplicationStore(db_file)
    security = SecurityRuntime()
    security.register_tenant_source("tenant_a", "finance_src")
    cat_store = SemanticCatalogStore()

    # Create physical table and insert annual sales data
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE annual_sales (
            id INTEGER PRIMARY KEY,
            tenant_id TEXT,
            year TEXT,
            sales_amount NUMERIC
        )
        """
    )
    # Insert 5 chronological observations for tenant_a
    history_data = [
        ("tenant_a", "2020", 100.0),
        ("tenant_a", "2021", 120.0),
        ("tenant_a", "2022", 140.0),
        ("tenant_a", "2023", 160.0),
        ("tenant_a", "2024", 180.0),
    ]
    conn.executemany(
        "INSERT INTO annual_sales (tenant_id, year, sales_amount) VALUES (?, ?, ?)",
        history_data,
    )
    conn.commit()
    conn.close()

    db_conn = sqlite3.connect(db_file)
    gateway = GenericSourceGateway(connection_or_factory=db_conn)

    # Build SemanticCatalog with TimeSeriesPolicy and ScenarioModel
    tables = {
        "annual_sales": TableDefinition(
            table_name="annual_sales",
            grain="year",
            tenant_column="tenant_id",
            columns={"year": DataType.STRING, "sales_amount": DataType.DECIMAL},
        )
    }
    measures = {
        "sales_amount": MeasureDefinition(
            id="sales_amount",
            name="sales_amount",
            description="Total sales amount",
            table="annual_sales",
            column="sales_amount",
            aggregation=AggregationType.SUM,
            base_grain="year",
            unit="USD",
            allowed_families=(
                "TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION",
                "FORECAST", "ANOMALY", "SCENARIO", "GOAL_SEEK"
            ),
            allowed_dimensions=("year",),
        )
    }
    dimensions = {
        "year": DimensionDefinition(
            id="year",
            name="year",
            table="annual_sales",
            column="year",
            data_type=DataType.STRING,
        )
    }
    ts_policy = {
        "sales_amount": TimeSeriesPolicy(frequency="YEAR", 
            metric="sales_amount",
            period_dimension="year",
            min_history_periods=4,
            max_forecast_horizon=4,
            linear_trend_eligible=True,
        )
    }
    scen_models = {
        "sales_simulator": ScenarioModelDefinition(
            model_id="sales_simulator",
            name="Sales Simulator",
            description="Model sales based on price and units",
            output_metric="sales_amount",
            formula_expression="price * units",
            drivers={
                "price": ScenarioDriverDefinition(
                    name="price",
                    unit="USD",
                    min_value=Decimal("1"),
                    max_value=Decimal("100"),
                    default_value=Decimal("20"),
                    is_adjustable=True,
                ),
                "units": ScenarioDriverDefinition(
                    name="units",
                    unit="units",
                    min_value=Decimal("1"),
                    max_value=Decimal("10000"),
                    default_value=Decimal("10"),
                    is_adjustable=True,
                ),
            },
            permitted_adjustments=("price", "units"),
        )
    }

    catalog = SemanticCatalog(
        release_id="rel_science_01",
        epoch=1,
        source_id="finance_src",
        tables=tables,
        measures=measures,
        dimensions=dimensions,
        default_period_dimension="year",
        time_series_policies=ts_policy,
        scenario_models=scen_models,
    )
    cat_store.register_catalog(catalog)

    manager = ConversationLifecycleManager(
        security_runtime=security,
        catalog_store=cat_store,
        gateway=gateway,
    )

    return {
        "store": store,
        "manager": manager,
        "security": security,
        "cat_store": cat_store,
    }


def test_forecast_lifecycle_and_clarification_flow(test_setup):
    """Verify forecast triggers clarification when horizon is missing and executes deterministically upon resolution."""
    mgr = test_setup["manager"]
    actor = ApiActor(tenant_id="tenant_a", subject_id="alice", roles={"analyst"})

    # 1. Ask forecast without specifying horizon
    intent_json = json.dumps({
        "measure": "sales_amount",
        "family": "FORECAST",
        "dimensions": ["year"],
    })
    turn, answer = mgr.submit_turn(
        actor=actor,
        conversation_id="conv_fc_1",
        source_id="finance_src",
        question="What is the sales forecast?",
        proposed_llm_json=intent_json,
    )
    # Must pause for clarification
    assert turn.status == TurnStatus.AWAITING_CLARIFICATION
    assert turn.clarification_request is not None
    assert turn.clarification_request.ambiguity_code == "forecast-horizon-required"
    assert answer is None

    # 2. Submit clarification choice for 2 periods
    turn2, answer2 = mgr.submit_clarification_choice(
        actor=actor,
        turn_id=turn.turn_id,
        choice_id="opt_fc_h2",
    )
    # Must resume validation and execute
    assert turn2.status == TurnStatus.ANSWERED
    assert answer2 is not None
    assert "Projected sales_amount" in answer2.summary_text
    assert len(answer2.kpi_cards) >= 1
    assert "PI:" in answer2.kpi_cards[0].delta


def test_scenario_and_goalseek_clarification_flows(test_setup):
    """Verify scenario and goal seek require approved models and adjustable drivers before execution."""
    mgr = test_setup["manager"]
    actor = ApiActor(tenant_id="tenant_a", subject_id="alice", roles={"analyst"})

    # 1. Scenario without model ID triggers clarification
    intent_scen = json.dumps({
        "measure": "sales_amount",
        "family": "SCENARIO",
    })
    turn_scen, _ = mgr.submit_turn(
        actor=actor,
        conversation_id="conv_scen_1",
        source_id="finance_src",
        question="Simulate scenario for sales",
        proposed_llm_json=intent_scen,
    )
    assert turn_scen.status == TurnStatus.AWAITING_CLARIFICATION
    assert turn_scen.clarification_request.ambiguity_code == "scenario-model-required"

    # Responding with model
    turn_res, ans_res = mgr.submit_clarification_choice(
        actor=actor,
        turn_id=turn_scen.turn_id,
        choice_id="opt_scen_sales_simulator",
    )
    assert turn_res.status == TurnStatus.ANSWERED
    assert ans_res is not None
    assert "Hypothetical scenario simulation" in ans_res.summary_text

    # 2. Goal seek without adjustable driver triggers clarification
    intent_gs = json.dumps({
        "measure": "sales_amount",
        "family": "GOAL_SEEK",
        "scenario_model": "sales_simulator",
        "target_value": "500",
    })
    turn_gs, _ = mgr.submit_turn(
        actor=actor,
        conversation_id="conv_gs_1",
        source_id="finance_src",
        question="How do I reach 500 sales?",
        proposed_llm_json=intent_gs,
    )
    assert turn_gs.status == TurnStatus.AWAITING_CLARIFICATION
    assert turn_gs.clarification_request.ambiguity_code == "adjustable-driver-required"

    # Select price as driver
    turn_gs_res, ans_gs_res = mgr.submit_clarification_choice(
        actor=actor,
        turn_id=turn_gs.turn_id,
        choice_id="opt_drv_price",
    )
    assert turn_gs_res.status == TurnStatus.ANSWERED, f"Turn failed with: {turn_gs_res.error_message}"
    assert ans_gs_res is not None
    # price * 10 = 500 -> price = 50.00
    assert "50.00" in ans_gs_res.summary_text


def test_derived_result_disclosure_protection():
    """Verify that when an underlying historical slice fails disclosure threshold, derived forecasts are suppressed."""
    from talk2data.domain.facts import ProtectedFact
    from talk2data.security.disclosure import GovernedDisclosureService

    disclosure = GovernedDisclosureService(default_min_entities=5)

    historical_facts = [
        ProtectedFact(
            fact_id="fact_h1",
            metric="units_sold",
            calculation_type="HISTORICAL",
            value=Decimal("100"),
            period="2023",
            result_type="OBSERVED",
        ),
        ProtectedFact(
            fact_id="fact_h2",
            metric="units_sold",
            calculation_type="HISTORICAL",
            value=Decimal("120"),
            period="2024",
            result_type="OBSERVED",
        ),
    ]
    derived_forecast_fact = ProtectedFact(
        fact_id="fact_fc1",
        metric="units_sold",
        calculation_type="FORECAST",
        value=Decimal("140"),
        period="2025",
        result_type="PREDICTED",
    )
    all_facts = historical_facts + [derived_forecast_fact]

    # Distinct entity count for fact_h1 is 2 (< 5 minimum threshold)
    counts = {"fact_h1": 2, "fact_h2": 10}
    protected = disclosure.inspect_and_protect(
        facts=all_facts,
        tenant_id="tenant_a",
        distinct_entity_counts=counts,
        min_entities=5,
    )

    # Both the historical fact AND the derived forecast fact must be suppressed
    h1_prot = next(f for f in protected if f.fact_id == "fact_h1")
    assert h1_prot.is_suppressed is True
    assert h1_prot.value is None

    fc_prot = next(f for f in protected if f.fact_id == "fact_fc1")
    assert fc_prot.is_suppressed is True
    assert fc_prot.value is None
    assert "Derived result suppression" in fc_prot.suppression_reason
