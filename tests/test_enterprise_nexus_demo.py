"""Integration and schema coverage for enterprise_nexus relational database."""
import json
from decimal import Decimal
import pytest

from backend.enterprise_nexus_demo import SOURCE_ID, add_enterprise_nexus_demo, create_standalone_database
from backend.catalog.extractor import introspect_sqlite_database
from backend.demo import initialize
from backend.domain.models import ApiActor, TurnStatus
from backend.lifecycle.session import ConversationLifecycleManager
from backend.runtime import load_runtime


@pytest.fixture
def enterprise_environment(tmp_path, monkeypatch):
    config_path = initialize(tmp_path)
    add_enterprise_nexus_demo(tmp_path)
    monkeypatch.setenv("TALK2DATA_RUNTIME_CONFIG", str(config_path))
    monkeypatch.setenv("TALK2DATA_ENV", "development")
    security, catalogs, gateway = load_runtime()
    manager = ConversationLifecycleManager(security, catalogs, gateway)
    actor = ApiActor("tenant_a", "nexus_tester")
    return manager, actor


def run_nexus_query(environment, conversation_id, question, intent):
    manager, actor = environment
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id=conversation_id,
        source_id=SOURCE_ID,
        question=question,
        proposed_llm_json=json.dumps(intent),
    )
    assert turn.status == TurnStatus.ANSWERED, (turn.error_message, turn.decline_reason)
    assert answer is not None
    return answer


def row_values(answer, *dimensions):
    return {
        tuple(row[name] for name in dimensions): Decimal(row["Value"].replace(",", ""))
        for row in answer.table_rows
    }


def test_multi_hop_join_continent_and_region(enterprise_environment):
    """Test 4+ hop join: orders -> customers -> cities/countries -> regions -> continents."""
    answer = run_nexus_query(
        enterprise_environment,
        "multi_hop_continent",
        "Total revenue by continent in 2024",
        {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["continent_name"], "current_period": "2024"},
    )
    rows = row_values(answer, "continent_name")
    assert len(rows) > 0
    assert any("America" in k[0] or "Europe" in k[0] or "Asia" in k[0] for k in rows.keys())


def test_multi_hop_join_supplier_and_category(enterprise_environment):
    """Test multi-hop through product hierarchy: order_items -> products -> categories."""
    answer = run_nexus_query(
        enterprise_environment,
        "multi_hop_category",
        "Line item revenue by category in 2024",
        {"measure": "line_item_revenue", "family": "TOTAL", "dimensions": ["category_name"], "current_period": "2024"},
    )
    rows = row_values(answer, "category_name")
    assert len(rows) > 0


def test_many_to_many_product_tags_junction(enterprise_environment):
    """Test M:N junction join through nexus_product_tags without fan-out double counting."""
    answer = run_nexus_query(
        enterprise_environment,
        "m_n_tag",
        "Order count by tag in 2024",
        {"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"},
    )
    rows = row_values(answer, "tag_name")
    assert len(rows) > 0


def test_standalone_database_introspectable(tmp_path):
    """Verify that standalone enterprise_nexus.sqlite can be introspected cleanly."""
    db_file = tmp_path / "enterprise_nexus.sqlite"
    create_standalone_database(db_file)
    assert db_file.exists()
    assert db_file.stat().st_size > 50000

    catalog, manifest = introspect_sqlite_database(
        db_path=db_file,
        source_id="standalone_nexus",
        default_tenant_id="tenant_a",
    )
    assert len(catalog.tables) >= 21
    assert len(catalog.joins) >= 20
    assert len(catalog.measures) >= 5
    assert len(catalog.dimensions) >= 15
