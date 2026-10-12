"""Persistent application store implementing durable transactions, tenant isolation, and atomic job queue."""
from __future__ import annotations
from talk2data.observability import traced_stage
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from talk2data.domain.models import ApiActor, Turn, TurnStatus
from talk2data.presentation.fallback import RenderedAnswerView, RenderedKpiCard
from talk2data.security.auth import AuthorizationError
from talk2data.storage.schema import init_database


class LeaseFencingError(RuntimeError):
    """Raised when a stale worker or superseded fencing token attempts to publish."""
    pass


class AuditIntegrityError(RuntimeError):
    """Raised when an audit record hash chain or keyed signature fails verification."""
    pass


class PersistentApplicationStore:
    """Thread-safe SQLite repository supporting multi-tenant isolation, idempotent jobs, and answer versions."""

    def __init__(self, db_path_or_conn: str | sqlite3.Connection = ":memory:"):
        self.conn = init_database(db_path_or_conn)
        self._lock = threading.RLock()
        self._transaction_depth = 0

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---------------------------------------------------------
    # Conversations
    # ---------------------------------------------------------
    def create_conversation(self, actor: ApiActor, title: str = "New Analysis") -> str:
        """Create a new conversation scoped to the authenticated tenant and actor."""
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        now = self._now_iso()
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO conversations (conversation_id, tenant_id, owner_subject_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conv_id, actor.tenant_id, actor.subject_id, title, now, now),
            )
        return conv_id

    def get_conversation(self, actor: ApiActor, conversation_id: str) -> dict[str, Any]:
        """Retrieve conversation, enforcing tenant isolation and ownership."""
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Conversation '{conversation_id}' not found.")
        if row["tenant_id"] != actor.tenant_id or row["owner_subject_id"] != actor.subject_id:
            raise AuthorizationError("Access denied: You do not have access to this conversation.")
        return dict(row)

    def list_conversations(self, actor: ApiActor) -> list[dict[str, Any]]:
        """List conversations owned by this actor in their tenant."""
        rows = self.conn.execute(
            """
            SELECT * FROM conversations 
            WHERE tenant_id = ? AND owner_subject_id = ?
            ORDER BY updated_at DESC
            """,
            (actor.tenant_id, actor.subject_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # Turns
    # ---------------------------------------------------------
    def save_turn(self, turn: Turn, actor: ApiActor, job_id=None, worker_id=None, fencing_token=None) -> None:
        """Persist or update turn state."""
        if turn.tenant_id != actor.tenant_id or turn.subject_id != actor.subject_id:
            raise AuthorizationError("Turn ownership does not match the actor")
        now = self._now_iso()
        clarification_json = (
            json.dumps(
                {
                    "ambiguity_code": turn.clarification_request.ambiguity_code,
                    "prompt": getattr(turn.clarification_request, "question_prompt", getattr(turn.clarification_request, "prompt", "")),
                    "options": [
                        {
                            "choice_id": opt.choice_id,
                            "label": opt.label,
                            "description": getattr(opt, "description", opt.label),
                            "target_type": getattr(opt, "target_type", ""),
                            "resolved_value": getattr(opt, "resolved_value", None),
                        }
                        for opt in turn.clarification_request.options
                    ],
                }
            )
            if turn.clarification_request
            else None
        )

        with self.transaction():
            if job_id:
                self.assert_job_ownership(job_id, worker_id, fencing_token)
            existing = self.conn.execute("SELECT status FROM turns WHERE turn_id = ?", (turn.turn_id,)).fetchone()
            if existing and existing["status"] in ("CANCELLED", "ANSWERED", "DECLINED"):
                if existing["status"] != turn.status.value:
                    raise LeaseFencingError("Terminal turn cannot be overwritten")
            self.conn.execute(
                """
                INSERT INTO turns (
                    turn_id, conversation_id, tenant_id, subject_id, source_id, question_text,
                    status, reference_timestamp, pinned_release_id, error_message, decline_reason,
                    clarification_request_json, selected_choice_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    status=excluded.status,
                    pinned_release_id=excluded.pinned_release_id,
                    error_message=excluded.error_message,
                    decline_reason=excluded.decline_reason,
                    clarification_request_json=excluded.clarification_request_json,
                    selected_choice_id=excluded.selected_choice_id,
                    updated_at=excluded.updated_at
                """,
                (
                    turn.turn_id,
                    turn.conversation_id,
                    actor.tenant_id,
                    actor.subject_id,
                    turn.source_id,
                    turn.question_text,
                    turn.status.value,
                    turn.reference_timestamp.isoformat(),
                    turn.pinned_release_id,
                    turn.error_message,
                    turn.decline_reason,
                    clarification_json,
                    getattr(turn, "selected_choice_id", None),
                    now,
                    now,
                ),
            )

    def get_turn(self, actor: ApiActor, turn_id: str) -> dict[str, Any]:
        """Retrieve turn metadata, enforcing tenant and subject boundaries."""
        row = self.conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if not row:
            raise KeyError(f"Turn '{turn_id}' not found.")
        if row["tenant_id"] != actor.tenant_id or row["subject_id"] != actor.subject_id:
            raise AuthorizationError("Access denied: You do not have access to this turn.")
        res = dict(row)
        job = self.conn.execute("SELECT status FROM jobs WHERE turn_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (turn_id,)).fetchone()
        res["job_status"] = job["status"] if job else None
        if res["clarification_request_json"]:
            res["clarification_request"] = json.loads(res["clarification_request_json"])
        else:
            res["clarification_request"] = None
        return res

    def list_turns_for_conversation(self, actor: ApiActor, conversation_id: str) -> list[dict[str, Any]]:
        """List turns for a conversation, enforcing ownership."""
        self.get_conversation(actor, conversation_id)
        rows = self.conn.execute(
            """
            SELECT * FROM turns 
            WHERE conversation_id = ? AND tenant_id = ? AND subject_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id, actor.tenant_id, actor.subject_id),
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            d["clarification_request"] = json.loads(d["clarification_request_json"]) if d["clarification_request_json"] else None
            res.append(d)
        return res

    # ---------------------------------------------------------
    # Jobs (Durable Queue & Leases)
    # ---------------------------------------------------------
    def submit_job_idempotent(
        self,
        actor: ApiActor,
        turn_id: str,
        operation: str,
        idempotency_key: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Submit a job idempotently. Returns (job_dict, is_newly_created)."""
        now = self._now_iso()
        payload_str = json.dumps(payload or {})

        # Check existing job
        row = self.conn.execute(
            """
            SELECT * FROM jobs 
            WHERE tenant_id = ? AND subject_id = ? AND idempotency_key = ?
            """,
            (actor.tenant_id, actor.subject_id, idempotency_key),
        ).fetchone()

        if row:
            if row["operation"] != operation or row["payload_json"] != payload_str:
                raise ValueError("Idempotency key was reused with a different operation or payload")
            return dict(row), False

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO jobs (
                    job_id, turn_id, tenant_id, subject_id, operation, idempotency_key,
                    status, attempts, max_attempts, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 0, 3, ?, ?, ?)
                """,
                (job_id, turn_id, actor.tenant_id, actor.subject_id, operation, idempotency_key, payload_str, now, now),
            )

        new_row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(new_row), True

    def get_job(self, actor: ApiActor, job_id: str) -> dict[str, Any]:
        """Retrieve job status, enforcing tenant and actor boundaries."""
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"Job '{job_id}' not found.")
        if row["tenant_id"] != actor.tenant_id or row["subject_id"] != actor.subject_id:
            raise AuthorizationError("Access denied: You do not have access to this job.")
        return dict(row)

    def cancel_job_and_turn(self, actor: ApiActor, turn_id: str) -> bool:
        """Cancel an in-flight job and transition turn to CANCELLED."""
        turn = self.get_turn(actor, turn_id)
        if turn["status"] in ("ANSWERED", "DECLINED", "FAILED", "CANCELLED"):
            return False

        now = self._now_iso()
        with self.transaction():
            current = self.get_turn(actor, turn_id)
            if current["status"] in ("ANSWERED", "DECLINED", "FAILED", "CANCELLED"):
                return False
            self.conn.execute(
                "UPDATE jobs SET status = 'CANCELLED', updated_at = ? WHERE turn_id = ? AND tenant_id = ?",
                (now, turn_id, actor.tenant_id),
            )
            self.conn.execute(
                "UPDATE turns SET status = 'CANCELLED', error_message = 'Execution cancelled by user.', updated_at = ? WHERE turn_id = ?",
                (now, turn_id),
            )
        return True

    def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Atomically claim the next eligible QUEUED or stale job with lease renewal."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_exp = (now_dt + timedelta(seconds=lease_seconds)).isoformat()

        # Atomic query and claim; crashed attempts are bounded too.
        with self.transaction():
            expired = self.conn.execute("SELECT turn_id FROM jobs WHERE status='RUNNING' AND lease_expires_at < ? AND attempts >= max_attempts", (now,)).fetchall()
            for exhausted in expired:
                self.conn.execute("UPDATE turns SET status='FAILED', error_message='Worker recovery attempts exhausted', updated_at=? WHERE turn_id=? AND status NOT IN ('ANSWERED','CANCELLED','DECLINED')", (now, exhausted["turn_id"]))
            self.conn.execute("UPDATE jobs SET status='FAILED', updated_at=? WHERE status='RUNNING' AND lease_expires_at < ? AND attempts >= max_attempts", (now, now))
            query = """
                SELECT * FROM jobs 
                WHERE status = 'QUEUED' 
                   OR (status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                ORDER BY created_at ASC
                LIMIT 1
            """
            row = self.conn.execute(query, (now,)).fetchone()
            if not row:
                return None

            job_id = row["job_id"]
            next_attempts = row["attempts"] + 1
            fencing_token = f"fence_{job_id}_att{next_attempts}_{uuid.uuid4().hex[:8]}"

            updated = self.conn.execute(
                """
                UPDATE jobs 
                SET status = 'RUNNING',
                    worker_id = ?,
                    fencing_token = ?,
                    lease_expires_at = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE job_id = ? AND (status = 'QUEUED' OR (status = 'RUNNING' AND lease_expires_at < ?))
                """,
                (worker_id, fencing_token, lease_exp, now, job_id, now),
            ).rowcount

            if updated == 0:
                return None  # Claimed concurrently by another worker

        claimed_row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(claimed_row)

    def complete_job(self, job_id: str, worker_id: str, fencing_token: Optional[str] = None) -> None:
        """Mark job successfully completed, respecting attempt fencing token."""
        now = self._now_iso()
        with self.transaction():
            self.assert_job_ownership(job_id, worker_id, fencing_token)
            if fencing_token:
                self.conn.execute(
                    """
                    UPDATE jobs 
                    SET status = 'COMPLETED', lease_expires_at = NULL, updated_at = ?
                    WHERE job_id = ? AND worker_id = ? AND fencing_token = ?
                    """,
                    (now, job_id, worker_id, fencing_token),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE jobs 
                    SET status = 'COMPLETED', lease_expires_at = NULL, updated_at = ?
                    WHERE job_id = ? AND worker_id = ?
                    """,
                    (now, job_id, worker_id),
                )

    def fail_job(self, job_id: str, error_message: str, worker_id: str, fencing_token=None, retryable=True) -> None:
        """Mark job failed or increment retry."""
        now = self._now_iso()
        with self.transaction():
            self.assert_job_ownership(job_id, worker_id, fencing_token)
            row = self.conn.execute("SELECT attempts, max_attempts FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not retryable or (row and row["attempts"] >= row["max_attempts"]):
                status = "FAILED"
            else:
                status = "QUEUED"  # Retry eligible
            self.conn.execute(
                """
                UPDATE jobs 
                SET status = ?, error_message = ?, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND worker_id = ? AND status = 'RUNNING'
                """,
                (status, error_message, now, job_id, worker_id),
            )
            self.conn.execute("UPDATE turns SET status=?, error_message=?, updated_at=? WHERE turn_id=(SELECT turn_id FROM jobs WHERE job_id=?) AND status NOT IN ('ANSWERED','CANCELLED','DECLINED')",
                ("FAILED" if status == "FAILED" else "SUBMITTED", error_message if status == "FAILED" else None, now, job_id))

    def release_job_for_clarification(self, job_id: str, worker_id: str, fencing_token=None) -> None:
        """Release worker when turn enters AWAITING_CLARIFICATION state."""
        now = self._now_iso()
        with self.transaction():
            self.assert_job_ownership(job_id, worker_id, fencing_token)
            self.conn.execute(
                """
                UPDATE jobs 
                SET status = 'PAUSED_FOR_CLARIFICATION', lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job_id),
            )

    # ---------------------------------------------------------
    # Published Answers (Single Source of Truth, Versioned)
    # ---------------------------------------------------------
    @traced_stage('answer_publication')
    def publish_answer_transactional(
        self,
        turn_id: str,
        actor: ApiActor,
        view: RenderedAnswerView,
        version: int = 1,
        charts: Optional[list[dict[str, Any]]] = None,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        fencing_token: Optional[str] = None,
    ) -> str:
        """Transactionally persist published answer and update turn to ANSWERED with attempt-specific lease fencing."""
        answer_id = f"ans_{uuid.uuid4().hex[:12]}"
        now = self._now_iso()

        cards_json = json.dumps([
            {
                "title": c.title,
                "value": c.value,
                "unit": c.unit,
                "period": c.period,
                "delta": c.delta,
                "growth_percent": c.growth_percent,
            }
            for c in view.kpi_cards
        ])
        columns_json = json.dumps(view.table_columns)
        rows_json = json.dumps(view.table_rows)
        charts_json = json.dumps(charts or [])
        assumptions_json = json.dumps(view.assumptions)
        limitations_json = json.dumps(view.limitations)

        with self.transaction():
            if job_id:
                self.assert_job_ownership(job_id, worker_id, fencing_token)
            # 1. Lease ownership & attempt-specific fencing token check
            if job_id:
                job_row = self.conn.execute(
                    "SELECT worker_id, status, fencing_token FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if job_row:
                    if job_row["status"] == "CANCELLED":
                        raise LeaseFencingError(f"Cannot publish answer: Job '{job_id}' was cancelled.")
                    if worker_id and job_row["worker_id"] != worker_id:
                        raise LeaseFencingError(
                            f"Lease fencing violation: Worker '{worker_id}' lost ownership of job '{job_id}' "
                            f"(current owner is '{job_row['worker_id']}'). Late publication aborted."
                        )
                    if fencing_token and job_row["fencing_token"] != fencing_token:
                        raise LeaseFencingError(
                            f"Lease fencing violation: Fencing token '{fencing_token}' does not match active "
                            f"token '{job_row['fencing_token']}' on job '{job_id}'. Attempt is stale. Late publication aborted."
                        )

            # 2. Check turn is not cancelled or terminated
            curr_turn = self.conn.execute("SELECT status FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if not curr_turn:
                raise RuntimeError(f"Cannot publish answer: Turn '{turn_id}' does not exist.")
            if curr_turn["status"] in ("CANCELLED", "DECLINED"):
                raise RuntimeError(f"Cannot publish answer for turn in status '{curr_turn['status']}'.")

            # 3. Check existing answers: worker recovery or retries cannot overwrite a newer or identical execution
            existing_ans = self.conn.execute(
                "SELECT answer_id, version FROM answers WHERE turn_id = ? ORDER BY version DESC LIMIT 1",
                (turn_id,),
            ).fetchone()
            if existing_ans and existing_ans["version"] >= version:
                return str(existing_ans["answer_id"])

            # Insert answer version
            self.conn.execute(
                """
                INSERT INTO answers (
                    answer_id, turn_id, tenant_id, version, summary_text,
                    kpi_cards_json, table_columns_json, table_rows_json, charts_json,
                    assumptions_json, limitations_json, is_suppressed, evidence_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    answer_id,
                    turn_id,
                    actor.tenant_id,
                    version,
                    view.summary_text,
                    cards_json,
                    columns_json,
                    rows_json,
                    charts_json,
                    assumptions_json,
                    limitations_json,
                    1 if view.is_suppressed else 0,
                    view.evidence_digest,
                    now,
                ),
            )

            # Update turn to ANSWERED
            self.conn.execute(
                "UPDATE turns SET status = 'ANSWERED', updated_at = ? WHERE turn_id = ?",
                (now, turn_id),
            )

            # Record audit outbox event with hash chain and keyed HMAC signature
            prev_row = self.conn.execute(
                "SELECT event_digest FROM audit_outbox ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            prev_digest = prev_row["event_digest"] if prev_row and prev_row["event_digest"] else "GENESIS_ROOT"

            event_id = f"evt_{uuid.uuid4().hex[:12]}"
            payload_raw = f"{event_id}:{actor.tenant_id}:{actor.subject_id}:{turn_id}:answer_published:{view.evidence_digest}:{prev_digest}:{now}"
            event_digest = hashlib.sha256(payload_raw.encode("utf-8")).hexdigest()
            audit_key = self.audit_key()
            signature = hmac.new(audit_key.encode("utf-8"), event_digest.encode("utf-8"), hashlib.sha256).hexdigest()

            self.conn.execute(
                """
                INSERT INTO audit_outbox (
                    event_id, tenant_id, subject_id, turn_id, event_type,
                    payload_digest, prev_event_digest, event_digest, signature, created_at
                )
                VALUES (?, ?, ?, ?, 'answer_published', ?, ?, ?, ?, ?)
                """,
                (event_id, actor.tenant_id, actor.subject_id, turn_id, view.evidence_digest, prev_digest, event_digest, signature, now),
            )

        return answer_id

    def get_published_answer(self, actor: ApiActor, turn_id: str) -> dict[str, Any]:
        """Retrieve published answer, verifying tenant and subject boundaries."""
        # Check turn access first
        turn = self.get_turn(actor, turn_id)
        self.verify_answer_permissions(actor, turn)

        row = self.conn.execute(
            """
            SELECT * FROM answers 
            WHERE turn_id = ? AND tenant_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (turn_id, actor.tenant_id),
        ).fetchone()

        if not row:
            raise KeyError(f"No published answer found for turn '{turn_id}'.")

        res = dict(row)
        res["kpi_cards"] = json.loads(res["kpi_cards_json"])
        res["table_columns"] = json.loads(res["table_columns_json"])
        res["table_rows"] = json.loads(res["table_rows_json"])
        res["charts"] = json.loads(res["charts_json"]) if res["charts_json"] else []
        res["assumptions"] = json.loads(res["assumptions_json"])
        res["limitations"] = json.loads(res["limitations_json"])
        res["is_suppressed"] = bool(res["is_suppressed"])
        return res

    # ---------------------------------------------------------
    # Analytical Context (Follow-Up)
    # ---------------------------------------------------------
    def save_analytical_context(
        self,
        conversation_id: str,
        turn_id: str,
        source_id: str,
        measure: str,
        family: str,
        dimensions: list[str],
        current_period: Optional[str],
        comparison_period: Optional[str],
        semantic_release_id: str,
        assumptions: list[str],
    ) -> None:
        """Persist authoritative analytical context for conversational follow-ups."""
        now = self._now_iso()
        ctx_id = f"ctx_{uuid.uuid4().hex[:12]}"
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO analytical_contexts (
                    context_id, conversation_id, last_turn_id, source_id, measure, family,
                    dimensions_json, current_period, comparison_period, semantic_release_id,
                    assumptions_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    last_turn_id=excluded.last_turn_id,
                    source_id=excluded.source_id,
                    measure=excluded.measure,
                    family=excluded.family,
                    dimensions_json=excluded.dimensions_json,
                    current_period=excluded.current_period,
                    comparison_period=excluded.comparison_period,
                    semantic_release_id=excluded.semantic_release_id,
                    assumptions_json=excluded.assumptions_json,
                    updated_at=excluded.updated_at
                """,
                (
                    ctx_id,
                    conversation_id,
                    turn_id,
                    source_id,
                    measure,
                    family,
                    json.dumps(dimensions),
                    current_period,
                    comparison_period,
                    semantic_release_id,
                    json.dumps(assumptions),
                    now,
                ),
            )

    def get_analytical_context(self, actor: ApiActor, conversation_id: str) -> Optional[dict[str, Any]]:
        """Retrieve analytical context for follow-up suggestions."""
        self.get_conversation(actor, conversation_id)
        row = self.conn.execute(
            "SELECT * FROM analytical_contexts WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        res["dimensions"] = json.loads(res["dimensions_json"])
        res["assumptions"] = json.loads(res["assumptions_json"])
        return res

    # ---------------------------------------------------------
    # Audit Trail Integrity Verification & Retention Cleanup
    # ---------------------------------------------------------
    def verify_audit_integrity(self, tenant_id: Optional[str] = None, expected_head: Optional[str] = None) -> tuple[bool, int, list[str]]:
        """Verify hash-chain continuity and keyed HMAC authentication signatures.
        
        Distinguishes:
        - Ordinary hashes: checks that event_digest matches SHA-256 of canonical payload string.
        - Hash-chain consistency: checks that each record's prev_event_digest matches prior event_digest.
        - Keyed signatures: verifies HMAC-SHA256 signature using TALK2DATA_AUDIT_KEY.
        - Limitations: A hash chain stored beside records in SQLite is NOT tamper-proof against
          bulk database deletion or rollback without independent append-only external replication.
        """
        audit_key = self.audit_key()
        query = "SELECT * FROM audit_outbox"
        params: list[Any] = []
        # Verify the global chain even when a tenant filter is requested.
        query += " ORDER BY created_at ASC, rowid ASC"

        rows = self.conn.execute(query, params).fetchall()
        if not rows:
            return (expected_head in (None, "GENESIS_ROOT")), 0, ([] if expected_head in (None, "GENESIS_ROOT") else ["Audit history missing relative to trusted checkpoint"])

        expected_prev = "GENESIS_ROOT"
        violations: list[str] = []
        verified_count = 0

        for r in rows:
            event_id = r["event_id"]
            rec_tenant = r["tenant_id"]
            rec_sub = r["subject_id"]
            rec_turn = r["turn_id"]
            rec_type = r["event_type"]
            rec_payload = r["payload_digest"]
            rec_prev = r["prev_event_digest"]
            rec_digest = r["event_digest"]
            rec_sig = r["signature"]
            rec_created = r["created_at"]

            # 1. Hash-chain link check
            if rec_prev != expected_prev:
                violations.append(
                    f"Hash chain broken at event '{event_id}': expected prev '{expected_prev}', found '{rec_prev}'."
                )

            # 2. Recompute ordinary event digest
            payload_raw = f"{event_id}:{rec_tenant}:{rec_sub}:{rec_turn}:{rec_type}:{rec_payload}:{rec_prev}:{rec_created}"
            calc_digest = hashlib.sha256(payload_raw.encode("utf-8")).hexdigest()
            if rec_digest != calc_digest:
                violations.append(
                    f"Digest mismatch at event '{event_id}': computed '{calc_digest}', recorded '{rec_digest}'."
                )

            # 3. Recompute keyed HMAC signature
            calc_sig = hmac.new(audit_key.encode("utf-8"), calc_digest.encode("utf-8"), hashlib.sha256).hexdigest()
            if rec_sig != calc_sig:
                violations.append(
                    f"HMAC signature mismatch at event '{event_id}': invalid cryptographic authentication."
                )

            expected_prev = calc_digest
            verified_count += 1

        if expected_head is not None and expected_prev != expected_head:
            violations.append("Audit head does not match trusted external checkpoint")
        is_valid = (len(violations) == 0)
        return is_valid, verified_count, violations

    def prune_expired_records(self, retention_days: int = 90) -> dict[str, int]:
        """Prune expired completed/failed jobs, turns, and answers older than retention threshold.
        
        NOTE: Active disclosure history records and audit records are preserved according to policy.
        """
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_iso = cutoff_dt.isoformat()

        pruned_counts: dict[str, int] = {}
        with self.transaction():
            # Prune completed or failed jobs
            cur = self.conn.execute(
                "DELETE FROM jobs WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED') AND updated_at < ?",
                (cutoff_iso,),
            )
            pruned_counts["jobs"] = cur.rowcount

            # Prune old disclosure history records whose dataset retention expired
            cur_disc = self.conn.execute(
                "DELETE FROM disclosure_history WHERE released_at < ?",
                (cutoff_iso,),
            )
            pruned_counts["disclosure_records"] = cur_disc.rowcount

        return pruned_counts

    def record_disclosure_release(
        self,
        record_id: str,
        tenant_id: str,
        dataset_id: str,
        policy_version: str,
        canonical_scope_digest: str,
        metric: str,
        filters_json: str,
        entity_count: int,
        released_value: Optional[str],
        released_at: str,
        turn_id: str,
        conversation_id: str,
        subject_id: str,
    ) -> None:
        """Persist disclosure release event to persistent accounting ledger."""
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO disclosure_history (
                    record_id, tenant_id, dataset_id, policy_version, canonical_scope_digest,
                    metric, filters_json, entity_count, released_value, released_at,
                    turn_id, conversation_id, subject_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id, tenant_id, dataset_id, policy_version, canonical_scope_digest,
                    metric, filters_json, entity_count, released_value, released_at,
                    turn_id, conversation_id, subject_id
                ),
            )

    def get_disclosure_history(
        self,
        tenant_id: str,
        dataset_id: str,
        metric: str,
    ) -> list[dict[str, Any]]:
        """Retrieve historical disclosure releases for repeated-query differencing checks."""
        rows = self.conn.execute(
            """
            SELECT * FROM disclosure_history
            WHERE tenant_id = ? AND dataset_id = ? AND metric = ?
            ORDER BY released_at ASC
            """,
            (tenant_id, dataset_id, metric),
        ).fetchall()
        return [dict(r) for r in rows]


    @contextmanager
    def transaction(self):
        with self._lock:
            outer = self._transaction_depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth += 1
            try:
                yield
                if outer:
                    self.conn.commit()
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise
            finally:
                self._transaction_depth -= 1

    def submit_question_atomic(self, actor, conversation_id, source_id, question, key, proposed):
        with self.transaction():
            self.get_conversation(actor, conversation_id)
            row = self.conn.execute("SELECT * FROM jobs WHERE tenant_id=? AND subject_id=? AND idempotency_key=?",
                                    (actor.tenant_id, actor.subject_id, key)).fetchone()
            payload = {"question": question, "proposed_llm_json": proposed,
                       "actor": {"roles": list(actor.roles), "permissions": sorted(actor.permissions)}}
            if row:
                original = self.get_turn(actor, row["turn_id"])
                if row["operation"] != "SUBMIT_QUESTION" or original["conversation_id"] != conversation_id or original["source_id"] != source_id or row["payload_json"] != json.dumps(payload):
                    raise ValueError("Idempotency key conflicts with the original submission")
                return dict(row), original, False
            turn = Turn.create(conversation_id, actor, question, source_id)
            self.save_turn(turn, actor)
            job, _ = self.submit_job_idempotent(actor, turn.turn_id, "SUBMIT_QUESTION", key, payload)
            return job, self.get_turn(actor, turn.turn_id), True

    def save_context_from_manager(self, turn_id, manager):
        turn = manager.get_turn(turn_id)
        intent = manager._pending_intents.get(turn_id)
        if intent:
            self.save_analytical_context(turn.conversation_id, turn_id, turn.source_id,
                intent.measure or "", intent.family.value, list(intent.dimensions), intent.current_period,
                intent.comparison_period, turn.pinned_release_id, list(intent.assumptions))

    def restore_conversation(self, actor, conversation_id, manager):
        for row in self.list_turns_for_conversation(actor, conversation_id):
            if row["status"] == "ANSWERED":
                self.restore_execution_state(actor, row["turn_id"], manager)

    def audit_key(self):
        key = os.getenv("TALK2DATA_AUDIT_KEY")
        if not key:
            if os.getenv("TALK2DATA_ENV") == "production":
                raise RuntimeError("TALK2DATA_AUDIT_KEY is required in production")
            # Development key is intentionally ephemeral, never a public hardcoded secret.
            key = getattr(self, "_development_audit_key", None) or uuid.uuid4().hex
            self._development_audit_key = key
        return key

    def assert_job_ownership(self, job_id, worker_id, fencing_token=None):
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row or row["status"] != "RUNNING" or row["worker_id"] != worker_id:
            raise LeaseFencingError("Lease fencing violation: Job is cancelled, terminal, or owned by another worker. Late publication aborted")
        if row["lease_expires_at"] <= self._now_iso():
            raise LeaseFencingError("Worker lease expired")
        if fencing_token and row["fencing_token"] != fencing_token:
            raise LeaseFencingError("Worker attempt was superseded")

    def verify_answer_permissions(self, actor, turn):
        security = getattr(self, "security", None)
        catalogs = getattr(self, "catalogs", None)
        if security is None or catalogs is None:
            raise AuthorizationError("Answer authorization services are not configured")
        security.check_source_access(actor, turn["source_id"])
        state = json.loads(turn.get("execution_state_json") or "{}")
        intent = state.get("intent")
        if not intent or not turn.get("pinned_release_id"):
            raise AuthorizationError("Immutable answer authorization scope is missing")
        cat = catalogs.get_catalog(turn["source_id"], turn["pinned_release_id"])
        metric = cat.measures.get(intent["measure"])
        if metric is None:
            raise AuthorizationError("Answer metric is no longer available")
        security.check_measure_access(actor, metric)
        dim_ids = set(intent.get("dimensions", [])) | {f["dimension"] for f in intent.get("filters", [])}
        for dim_id in dim_ids:
            dim = cat.dimensions.get(dim_id)
            if dim is None:
                raise AuthorizationError("Answer dimension is no longer available")
            security.check_dimension_access(actor, dim)

    def save_execution_state(self, turn_id, manager, actor, job_id, worker_id, token):
        from dataclasses import asdict
        self.assert_job_ownership(job_id, worker_id, token)
        turn = manager.get_turn(turn_id)
        intent = manager._pending_intents.get(turn_id)
        state = {"intent": asdict(intent) if intent else None,
                 "clarification": asdict(turn.clarification_request) if turn.clarification_request else None}
        with self.transaction():
            self.conn.execute("UPDATE turns SET execution_state_json=?, actor_json=? WHERE turn_id=?",
                (json.dumps(state, default=str), json.dumps({"roles": actor.roles, "permissions": list(actor.permissions)}), turn_id))

    def restore_execution_state(self, actor, turn_id, manager):
        from talk2data.domain.models import ClarificationRequest, ClarificationOption
        from talk2data.intent.parser import IntentParser
        row = self.get_turn(actor, turn_id)
        state = json.loads(row.get("execution_state_json") or "{}")
        turn = Turn.create(row["conversation_id"], actor, row["question_text"], row["source_id"], turn_id,
                           datetime.fromisoformat(row["reference_timestamp"]))
        turn.status = TurnStatus(row["status"])
        turn.pinned_release_id = row["pinned_release_id"]
        clar = state.get("clarification")
        if clar:
            clar["options"] = [ClarificationOption(**opt) for opt in clar["options"]]
            clar["created_at"] = datetime.fromisoformat(clar["created_at"])
            turn.clarification_request = ClarificationRequest(**clar)
        manager._turns[turn_id] = turn
        if state.get("intent"):
            manager._pending_intents[turn_id] = IntentParser()._dict_to_intent(state["intent"])
        return turn

    def renew_lease(self, job_id, worker_id, token, seconds):
        with self.transaction():
            self.assert_job_ownership(job_id, worker_id, token)
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            self.conn.execute("UPDATE jobs SET lease_expires_at=? WHERE job_id=?", (expiry, job_id))

    def record_audit_event(self, event):
        with self.transaction():
            if self.conn.execute("SELECT 1 FROM audit_outbox WHERE event_id=?", (event.event_id,)).fetchone():
                return
            prev = self.conn.execute("SELECT event_digest FROM audit_outbox ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
            prev_digest = prev["event_digest"] if prev else "GENESIS_ROOT"
            now = self._now_iso()
            raw = f"{event.event_id}:{event.tenant_id}:{event.subject_id}:{event.turn_id}:{event.event_type}:{event.payload_digest}:{prev_digest}:{now}"
            digest = hashlib.sha256(raw.encode()).hexdigest()
            signature = hmac.new(self.audit_key().encode(), digest.encode(), hashlib.sha256).hexdigest()
            self.conn.execute("INSERT INTO audit_outbox (event_id,tenant_id,subject_id,turn_id,event_type,payload_digest,prev_event_digest,event_digest,signature,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (event.event_id,event.tenant_id,event.subject_id,event.turn_id,event.event_type,event.payload_digest,prev_digest,digest,signature,now))


# Serialize use of each SQLite connection across API and lease-heartbeat threads.
def _synchronized(method):
    from functools import wraps
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped

for _name, _method in list(vars(PersistentApplicationStore).items()):
    if callable(_method) and not _name.startswith("_") and _name != "transaction":
        setattr(PersistentApplicationStore, _name, _synchronized(_method))
