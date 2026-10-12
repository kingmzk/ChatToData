"""Database schema and versioned migrations for Talk2Data persistent application state."""
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3
from typing import Optional


MIGRATION_001_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    status TEXT NOT NULL,
    reference_timestamp TEXT NOT NULL,
    pinned_release_id TEXT,
    error_message TEXT,
    decline_reason TEXT,
    clarification_request_json TEXT,
    selected_choice_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    worker_id TEXT,
    lease_expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    error_message TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, subject_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS answers (
    answer_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    summary_text TEXT NOT NULL,
    kpi_cards_json TEXT NOT NULL,
    table_columns_json TEXT NOT NULL,
    table_rows_json TEXT NOT NULL,
    charts_json TEXT,
    assumptions_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    is_suppressed INTEGER NOT NULL DEFAULT 0,
    evidence_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
    UNIQUE(turn_id, version)
);

CREATE TABLE IF NOT EXISTS audit_outbox (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS analytical_contexts (
    context_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    last_turn_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    measure TEXT NOT NULL,
    family TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    current_period TEXT,
    comparison_period TEXT,
    semantic_release_id TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
"""

MIGRATION_002_SQL = """
CREATE TABLE IF NOT EXISTS decision_science_facts (
    fact_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    family TEXT NOT NULL,
    method TEXT NOT NULL,
    method_version TEXT NOT NULL,
    result_type TEXT NOT NULL,
    prediction_interval_json TEXT,
    diagnostics_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(turn_id) REFERENCES turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_lease ON jobs(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_answers_turn ON answers(turn_id);
"""

MIGRATION_003_SQL = """
ALTER TABLE jobs ADD COLUMN fencing_token TEXT;
ALTER TABLE audit_outbox ADD COLUMN prev_event_digest TEXT;
ALTER TABLE audit_outbox ADD COLUMN event_digest TEXT;
ALTER TABLE audit_outbox ADD COLUMN signature TEXT;

CREATE TABLE IF NOT EXISTS disclosure_history (
    record_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    canonical_scope_digest TEXT NOT NULL,
    metric TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    entity_count INTEGER NOT NULL,
    released_value TEXT,
    released_at TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    subject_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_disclosure_lookup ON disclosure_history(tenant_id, dataset_id, metric);
CREATE INDEX IF NOT EXISTS idx_jobs_fencing ON jobs(job_id, fencing_token);
"""

MIGRATION_004_SQL = """
ALTER TABLE turns ADD COLUMN execution_state_json TEXT;
ALTER TABLE turns ADD COLUMN actor_json TEXT;
"""

MIGRATIONS = [
    (1, "001_phase1_phase2_core", MIGRATION_001_SQL),
    (2, "002_phase3_decision_science", MIGRATION_002_SQL),
    (3, "003_phase4_advanced_controls", MIGRATION_003_SQL),
    (4, "004_durable_execution_context", MIGRATION_004_SQL),
]


def init_database(db_path_or_conn: str | sqlite3.Connection) -> sqlite3.Connection:
    """Initialize database tables and run versioned migrations in order."""
    if isinstance(db_path_or_conn, str):
        conn = sqlite3.connect(db_path_or_conn, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    else:
        conn = db_path_or_conn
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA busy_timeout = 5000")
    # Migrations are serialized across API and worker startup processes.
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        applied_versions = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in MIGRATIONS:
            if version in applied_versions:
                continue
            statement = ""
            for line in sql.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ""
            if statement.strip():
                raise RuntimeError("Incomplete migration statement")
            conn.execute("INSERT INTO schema_migrations (version,name,applied_at) VALUES (?,?,?)",
                         (version, name, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return conn
