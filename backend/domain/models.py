"""Core domain entities and lifecycle representations."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import hashlib
import uuid


class TurnStatus(str, Enum):
    """Lifecycle states for a conversation turn."""
    SUBMITTED = "SUBMITTED"
    INTERPRETING = "INTERPRETING"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    ANALYZING = "ANALYZING"
    COMPOSING = "COMPOSING"
    ANSWERED = "ANSWERED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ApiActor:
    """Authenticated subject context with strict tenant scoping."""
    tenant_id: str
    subject_id: str
    roles: tuple[str, ...] = ("analyst",)
    is_admin: bool = False
    permissions: frozenset[str] = frozenset()

    def has_role(self, role: str) -> bool:
        return self.is_admin or (role in self.roles)

    def has_permission(self, permission: str) -> bool:
        return self.is_admin or (permission in self.permissions)


@dataclass(frozen=True)
class ClarificationOption:
    """Discrete, structured choice for disambiguation."""
    choice_id: str
    label: str
    target_type: str  # e.g., 'period', 'comparison_period', 'metric', 'dimension'
    resolved_value: Any


@dataclass
class ClarificationRequest:
    """Clarification gate payload bound to turn and tenant."""
    clarification_id: str
    turn_id: str
    tenant_id: str
    subject_id: str
    source_id: str
    semantic_release_id: str
    ambiguity_code: str
    question_prompt: str
    options: list[ClarificationOption]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "PENDING"  # PENDING, RESOLVED, EXPIRED
    selected_choice_id: Optional[str] = None

    def resolve(self, choice_id: str) -> ClarificationOption:
        for opt in self.options:
            if opt.choice_id == choice_id:
                self.selected_choice_id = choice_id
                self.status = "RESOLVED"
                return opt
        raise ValueError(f"Choice ID '{choice_id}' is not a valid option for this clarification.")


@dataclass
class Turn:
    """Conversation turn entity with atomic lifecycle tracking."""
    turn_id: str
    conversation_id: str
    tenant_id: str
    subject_id: str
    question_text: str  # ephemeral, never retained in long-term audit
    question_digest: str
    status: TurnStatus = TurnStatus.SUBMITTED
    source_id: Optional[str] = None
    pinned_release_id: Optional[str] = None
    clarification_request: Optional[ClarificationRequest] = None
    decline_reason: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reference_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    selected_choice_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        conversation_id: str,
        actor: ApiActor,
        question: str,
        source_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        reference_datetime: Optional[datetime] = None,
    ) -> Turn:
        t_id = turn_id or f"turn_{uuid.uuid4().hex[:12]}"
        digest = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        ref_ts = reference_datetime or now
        return cls(
            turn_id=t_id,
            conversation_id=conversation_id,
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            source_id=source_id,
            question_text=question,
            question_digest=digest,
            status=TurnStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
            reference_timestamp=ref_ts,
        )


    def transition_to(self, new_status: TurnStatus, reason: Optional[str] = None) -> None:
        """Atomic state transition."""
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        if new_status == TurnStatus.DECLINED and reason:
            self.decline_reason = reason
        elif new_status == TurnStatus.FAILED and reason:
            self.error_message = reason
