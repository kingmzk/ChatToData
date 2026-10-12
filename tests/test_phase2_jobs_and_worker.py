"""Tests for Talk2Data Phase 2 Durable Asynchronous Jobs, Worker Leasing, and Publication Consistency."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from talk2data.domain.models import ApiActor, Turn, TurnStatus
from talk2data.domain.facts import ProtectedFact, SealedEvidencePackage
from talk2data.jobs.worker import DurableJobWorker
from talk2data.presentation.fallback import RenderedAnswerView, RenderedKpiCard
from talk2data.security.auth import AuthorizationError
from talk2data.storage.repository import PersistentApplicationStore


def test_idempotent_job_submission(test_setup):
    """Verify duplicate submissions return the existing job without re-queueing."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Idempotency Test")
    turn_id = "turn_idem_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Total revenue 2024?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    payload = {"question": "Total revenue 2024?"}
    job1, is_new1 = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_QUESTION",
        idempotency_key="idempotency_key_123",
        payload=payload,
    )
    assert is_new1 is True
    assert job1["job_id"] is not None

    # Duplicate submission with same actor and idempotency key
    job2, is_new2 = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_QUESTION",
        idempotency_key="idempotency_key_123",
        payload=payload,
    )
    assert is_new2 is False
    assert job2["job_id"] == job1["job_id"]


def test_atomic_worker_claiming_and_lease(test_setup):
    """Verify atomic claiming prevents multiple workers from processing the same job simultaneously."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Lease Test")
    turn_id = "turn_lease_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Revenue?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    job, _ = store.submit_job_idempotent(actor, turn_id, "SUBMIT_QUESTION", "key_lease_test", {})

    # Worker 1 claims job
    worker1 = DurableJobWorker(store, test_setup["manager"], test_setup["security"], worker_id="w1", lease_seconds=60)
    worker2 = DurableJobWorker(store, test_setup["manager"], test_setup["security"], worker_id="w2", lease_seconds=60)

    claimed1 = store.claim_next_job("w1", lease_seconds=60)
    assert claimed1 is not None
    assert claimed1["job_id"] == job["job_id"]
    assert claimed1["status"] == "RUNNING"
    assert claimed1["worker_id"] == "w1"

    # Worker 2 attempts to claim while lease is active -> returns None
    claimed2 = store.claim_next_job("w2", lease_seconds=60)
    assert claimed2 is None


def test_stale_job_recovery_after_lease_expiry(test_setup):
    """Verify that when a worker crashes and lease expires, another worker can reclaim the job."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Recovery Test")
    turn_id = "turn_rec_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Revenue?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    job, _ = store.submit_job_idempotent(actor, turn_id, "SUBMIT_QUESTION", "key_rec_test", {})

    # Worker 1 claims job with short lease (1 second)
    claimed1 = store.claim_next_job("w1", lease_seconds=1)
    assert claimed1 is not None

    # Artificially expire lease in database
    expired_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with store.conn:
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE job_id = ?",
            (expired_time, job["job_id"]),
        )

    # Worker 2 can now atomically reclaim the stale job
    claimed2 = store.claim_next_job("w2", lease_seconds=60)
    assert claimed2 is not None
    assert claimed2["job_id"] == job["job_id"]
    assert claimed2["worker_id"] == "w2"
    assert claimed2["attempts"] == 2


def test_bounded_retries_and_failure(test_setup):
    """Verify transient failures increment attempts and transition to FAILED upon retry exhaustion."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Retry Test")
    turn_id = "turn_retry_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Revenue?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    job, _ = store.submit_job_idempotent(actor, turn_id, "SUBMIT_QUESTION", "key_retry_test", {})
    job_id = job["job_id"]

    # Attempt 1 fails -> goes back to QUEUED for retry
    c1 = store.claim_next_job("w1")
    store.fail_job(job_id, "Transient network timeout 1", "w1")
    j1 = store.get_job(actor, job_id)
    assert j1["status"] == "QUEUED"
    assert j1["attempts"] == 1

    # Attempt 2 fails -> goes back to QUEUED
    c2 = store.claim_next_job("w2")
    store.fail_job(job_id, "Transient network timeout 2", "w2")
    j2 = store.get_job(actor, job_id)
    assert j2["status"] == "QUEUED"
    assert j2["attempts"] == 2

    # Attempt 3 fails -> reaches max_attempts (3), transitions to FAILED
    c3 = store.claim_next_job("w3")
    store.fail_job(job_id, "Fatal retry exhaustion", "w3")
    j3 = store.get_job(actor, job_id)
    assert j3["status"] == "FAILED"
    assert j3["attempts"] == 3


def test_clarification_pauses_worker_and_persists_across_restart(test_setup):
    """Verify that entering clarification releases the worker lease and resumes upon choice submission."""
    store = PersistentApplicationStore(":memory:")
    manager = test_setup["manager"]
    security = test_setup["security"]
    actor = test_setup["actor_a"]

    conv_id = store.create_conversation(actor, title="Clarification Lifecycle")
    turn_id = "turn_clarif_01"

    # Ambiguous question triggering clarification (COMPARISON without comparison period)
    t = Turn.create(
        conversation_id=conv_id,
        actor=actor,
        question="Show sales growth.",
        source_id="finance_dw",
        turn_id=turn_id,
    )
    store.save_turn(t, actor)

    # Intent with COMPARISON family without periods
    job, _ = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_QUESTION",
        idempotency_key="key_clarif_1",
        payload={
            "question": "Show sales growth.",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "COMPARISON"}',
        },
    )

    worker = DurableJobWorker(store=store, manager=manager, security=security, worker_id="w_clarif")
    worker.process_next_job()

    # Turn should now be in AWAITING_CLARIFICATION and job in PAUSED_FOR_CLARIFICATION
    turn_saved = store.get_turn(actor, turn_id)
    assert turn_saved["status"] == "AWAITING_CLARIFICATION"
    assert turn_saved["clarification_request_json"] is not None

    job_saved = store.get_job(actor, job["job_id"])
    assert job_saved["status"] == "PAUSED_FOR_CLARIFICATION"
    assert job_saved["lease_expires_at"] is None

    # User submits choice
    req_json = json.loads(turn_saved["clarification_request_json"])
    choice_id = req_json["options"][0]["choice_id"]

    job_res, _ = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_CLARIFICATION",
        idempotency_key=f"key_clarif_choice_{choice_id}",
        payload={"choice_id": choice_id},
    )

    # Process continuation job
    worker.process_next_job()

    turn_final = store.get_turn(actor, turn_id)
    assert turn_final["status"] == "ANSWERED"
    ans = store.get_published_answer(actor, turn_id)
    assert ans is not None
    assert ans["summary_text"] is not None


def test_cancellation_during_execution_and_duplicate_publication_protection(test_setup):
    """Verify that cancellation prevents late answer publication and retries cannot duplicate answers."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Cancel Test")
    turn_id = "turn_cancel_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Revenue?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    # Cancel turn
    store.cancel_job_and_turn(actor, turn_id)
    t_after = store.get_turn(actor, turn_id)
    assert t_after["status"] == "CANCELLED"

    # Attempting to publish answer on cancelled turn MUST raise error
    dummy_view = RenderedAnswerView(
        summary_text="Total revenue was $100,000.00.",
        kpi_cards=[RenderedKpiCard(title="Total Revenue", value="$100,000.00", unit="USD", period="2024")],
        table_columns=["Metric", "Value"],
        table_rows=[{"Metric": "Revenue", "Value": "$100,000.00"}],
        assumptions=["Test assumption"],
        limitations=["Test limitation"],
        is_suppressed=False,
        evidence_digest="digest_test_123",
    )

    with pytest.raises(RuntimeError, match="Cannot publish answer for turn in status 'CANCELLED'"):
        store.publish_answer_transactional(turn_id=turn_id, actor=actor, view=dummy_view, version=1)

    # Now verify duplicate publication protection on an active turn
    turn_active_id = "turn_active_pub_01"
    t_act = Turn.create(conversation_id=conv_id, actor=actor, question="Active rev?", source_id="finance_dw", turn_id=turn_active_id)
    store.save_turn(t_act, actor)

    ans_id_1 = store.publish_answer_transactional(turn_id=turn_active_id, actor=actor, view=dummy_view, version=1)
    assert ans_id_1 is not None

    # Re-publishing version 1 (e.g. from retry or recovered worker) returns existing answer ID and does not duplicate
    ans_id_2 = store.publish_answer_transactional(turn_id=turn_active_id, actor=actor, view=dummy_view, version=1)
    assert ans_id_2 == ans_id_1

    # Published answer count in DB is exactly 1
    rows = store.conn.execute("SELECT * FROM answers WHERE turn_id = ?", (turn_active_id,)).fetchall()
    assert len(rows) == 1


def test_lease_fencing_prevents_expired_worker_from_publishing(test_setup):
    """Verify lease ownership/fencing prevents expired workers from publishing after another worker claims their job."""
    store = PersistentApplicationStore(":memory:")
    actor = test_setup["actor_a"]
    conv_id = store.create_conversation(actor, title="Fencing Test")
    turn_id = "turn_fence_01"

    t = Turn.create(conversation_id=conv_id, actor=actor, question="Revenue?", source_id="finance_dw", turn_id=turn_id)
    store.save_turn(t, actor)

    job, _ = store.submit_job_idempotent(actor, turn_id, "SUBMIT_QUESTION", "key_fence_01", {})
    job_id = job["job_id"]

    # Worker 1 claims job
    c1 = store.claim_next_job("w1", lease_seconds=1)
    assert c1 is not None

    # Artificially expire lease in database
    expired_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with store.conn:
        store.conn.execute("UPDATE jobs SET lease_expires_at = ? WHERE job_id = ?", (expired_time, job_id))

    # Worker 2 reclaims the stale job
    c2 = store.claim_next_job("w2", lease_seconds=60)
    assert c2 is not None
    assert c2["worker_id"] == "w2"

    dummy_view = RenderedAnswerView(
        summary_text="Total revenue was $100,000.00.",
        kpi_cards=[RenderedKpiCard(title="Total Revenue", value="100,000.00", unit="USD", period="2024")],
        table_columns=["Metric", "Value"],
        table_rows=[{"Metric": "Revenue", "Value": "100,000.00"}],
        assumptions=[],
        limitations=[],
        is_suppressed=False,
        evidence_digest="digest_fence_test",
    )

    # Worker 1 (whose lease expired and was overtaken by w2) attempts late publication -> FENCED OUT
    with pytest.raises(RuntimeError, match="Lease fencing violation"):
        store.publish_answer_transactional(
            turn_id=turn_id,
            actor=actor,
            view=dummy_view,
            version=1,
            job_id=job_id,
            worker_id="w1",
        )

    # Worker 2 (active lease holder) publishes successfully
    ans_id = store.publish_answer_transactional(
        turn_id=turn_id,
        actor=actor,
        view=dummy_view,
        version=1,
        job_id=job_id,
        worker_id="w2",
    )
    assert ans_id is not None

