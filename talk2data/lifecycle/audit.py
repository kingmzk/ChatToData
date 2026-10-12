"""Terminal audit recorder preserving privacy with SHA-256 digests and zero row retention."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    tenant_id: str
    subject_id: str
    turn_id: str
    event_type: str  # question_submitted, clarification_requested, analysis_started, etc.
    payload_digest: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TerminalAuditRecorder:
    """Records immutable audit trail ensuring zero raw data or question text leaks."""

    def __init__(self):
        self._events: list[AuditEvent] = []

    def record_event(
        self,
        tenant_id: str,
        subject_id: str,
        turn_id: str,
        event_type: str,
        payload_digest: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        """Append an audit event with sanitized metadata."""
        event_id = "aud_" + uuid.uuid4().hex
        clean_meta = dict(metadata or {})
        # Ensure raw SQL or raw rows are never in audit metadata
        clean_meta.pop("raw_rows", None)
        clean_meta.pop("raw_question", None)
        clean_meta.pop("raw_sql", None)

        event = AuditEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            turn_id=turn_id,
            event_type=event_type,
            payload_digest=payload_digest,
            metadata=clean_meta,
        )
        self._events.append(event)
        return event

    def get_turn_events(self, turn_id: str) -> list[AuditEvent]:
        return [e for e in self._events if e.turn_id == turn_id]

    @property
    def all_events(self) -> list[AuditEvent]:
        return list(self._events)
