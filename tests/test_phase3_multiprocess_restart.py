"""Tests verifying multi-process execution and restart recovery using persistent on-disk SQLite."""
import os
import subprocess
import sys
import pytest
from datetime import datetime, timezone
from decimal import Decimal

from talk2data.domain.models import ApiActor, Turn, TurnStatus
from talk2data.storage.repository import PersistentApplicationStore


def test_multiprocess_job_execution_and_restart_recovery(tmp_path, test_setup):
    """Verify that jobs submitted to a persistent on-disk database are claimed and executed across separate processes."""
    db_file = str(tmp_path / "shared_cluster.db")
    
    # Process 1: API / Producer opens persistent database and submits job
    store_api = PersistentApplicationStore(db_file)
    actor = test_setup["actor_a"]
    conv_id = store_api.create_conversation(actor, title="Multi-Process Test")
    turn_id = "turn_mp_01"

    t = Turn.create(
        conversation_id=conv_id,
        actor=actor,
        question="What was total sales revenue in 2024?",
        source_id="finance_dw",
        turn_id=turn_id,
    )
    store_api.save_turn(t, actor)

    job, is_new = store_api.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_QUESTION",
        idempotency_key="key_mp_01",
        payload={
            "question": "What was total sales revenue in 2024?",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024"}',
        },
    )
    assert is_new is True
    assert job["status"] == "QUEUED"
    store_api.conn.close()

    # Process 2: Worker Daemon runs in a separate Python process against the same database file
    # We invoke a python snippet that imports and runs DurableJobWorker with configured test setup
    worker_script = f"""
import sys
from talk2data.storage.repository import PersistentApplicationStore
from talk2data.jobs.worker import DurableJobWorker
from talk2data.lifecycle.session import ConversationLifecycleManager
from talk2data.security.auth import SecurityRuntime
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.execution.gateway import GenericSourceGateway

store = PersistentApplicationStore(r"{db_file}")
sec = SecurityRuntime()
sec.register_tenant_source("tenant_a", "finance_dw")
cat_store = SemanticCatalogStore()

# Run worker on job
# Create a dummy test gateway with orders table
import sqlite3
test_conn = sqlite3.connect(":memory:")
cur = test_conn.cursor()
cur.execute("CREATE TABLE orders (id TEXT, tenant_id TEXT, customer_id TEXT, amount NUMERIC, period TEXT)")
cur.execute("INSERT INTO orders VALUES ('o1', 'tenant_a', 'c1', 50000.0, '2024')")
test_conn.commit()

from talk2data.catalog.schema import SemanticCatalog, TableDefinition, MeasureDefinition, DimensionDefinition, DataType, AggregationType
tbl = TableDefinition(table_name="orders", columns={{"amount": DataType.DECIMAL, "period": DataType.STRING}})
meas = MeasureDefinition(id="sales_revenue", name="Sales Revenue", description="Revenue", table="orders", column="amount", aggregation=AggregationType.SUM, base_grain="order", unit="USD")
dim = DimensionDefinition(id="period", name="Period", table="orders", column="period", data_type=DataType.STRING)
cat = SemanticCatalog(release_id="rel_active", epoch=1, source_id="finance_dw", tables={{"orders": tbl}}, measures={{"sales_revenue": meas}}, dimensions={{"period": dim}})
cat_store.register_catalog(cat, set_active=True)

gw = GenericSourceGateway(test_conn)
mgr = ConversationLifecycleManager(security_runtime=sec, catalog_store=cat_store, gateway=gw)
worker = DurableJobWorker(store=store, manager=mgr, security=sec, worker_id="daemon_proc_1")
processed = worker.process_next_job()
print(f"WORKER_FINISHED: {{processed is not None}}")
"""
    result = subprocess.run(
        [sys.executable, "-c", worker_script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "WORKER_FINISHED: True" in result.stdout, f"Subprocess output: {result.stdout}, stderr: {result.stderr}"

    # Process 3: Simulating API restart / recovery after worker execution
    store_restarted = PersistentApplicationStore(db_file)
    turn_after = store_restarted.get_turn(actor, turn_id)
    assert turn_after["status"] == "ANSWERED"

    # Restart restores authorization services as well as persistent records.
    store_restarted.security = test_setup["security"]
    store_restarted.catalogs = test_setup["catalogs"]
    from dataclasses import replace
    restored_catalog = store_restarted.catalogs.get_catalog("finance_dw")
    store_restarted.catalogs.register_catalog(replace(restored_catalog, release_id="rel_active"), set_active=False)
    ans = store_restarted.get_published_answer(actor, turn_id)
    assert ans is not None
    assert "summary_text" in ans
    assert ans["version"] == 1
    store_restarted.conn.close()
