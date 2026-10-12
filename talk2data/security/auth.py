"""Authentication, tenant isolation, and security runtime."""
from __future__ import annotations
import re
from typing import Any, Optional
from talk2data.domain.models import ApiActor, Turn


class AuthenticationError(Exception):
    """Raised when authentication fails or credentials are missing."""
    pass


class AuthorizationError(Exception):
    """Raised when an actor attempts an unauthorized action, accesses an unauthorized source, or breaches tenant boundary."""
    pass


class SecurityPolicyViolationError(Exception):
    """Raised when a security boundary violation or malicious tampering is detected."""
    pass


class SecurityRuntime:
    """Security control plane enforcing deny-by-default access control and tenant isolation.
    
    Tenant isolation is enforced by strict server-side predicates, source allowlists,
    and immutable turn ownership. Phrase pattern detection is strictly a fast-path
    rejection filter and is NOT relied upon as the primary security boundary.
    """

    # Fast-path detection of common prompt injection strings
    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(?:all\s+)?restrictions", re.IGNORECASE),
        re.compile(r"query\s+(?:another|other)\s+tenant", re.IGNORECASE),
        re.compile(r"switch\s+(?:to\s+)?tenant", re.IGNORECASE),
        re.compile(r"bypass\s+(?:security|policy|controls|tenant)", re.IGNORECASE),
        re.compile(r"access\s+tenant", re.IGNORECASE),
        re.compile(r"drop\s+table", re.IGNORECASE),
        re.compile(r"delete\s+from", re.IGNORECASE),
        re.compile(r"union\s+select", re.IGNORECASE),
        re.compile(r"truncate\s+table", re.IGNORECASE),
    ]

    def __init__(self, tenant_source_permissions: Optional[dict[str, set[str]]] = None):
        # tenant_id -> set of allowed source_ids
        self._tenant_source_permissions: dict[str, set[str]] = {}
        if tenant_source_permissions:
            for t_id, s_ids in tenant_source_permissions.items():
                self._tenant_source_permissions[t_id] = set(s_ids)

    def register_tenant_source(self, tenant_id: str, source_id: str) -> None:
        """Grant a tenant access to a specific data source."""
        if tenant_id not in self._tenant_source_permissions:
            self._tenant_source_permissions[tenant_id] = set()
        self._tenant_source_permissions[tenant_id].add(source_id)

    def revoke_tenant_source(self, tenant_id: str, source_id: str) -> None:
        """Revoke a tenant's access to a data source dynamically."""
        if tenant_id in self._tenant_source_permissions:
            self._tenant_source_permissions[tenant_id].discard(source_id)

    def authenticate_actor(
        self,
        tenant_id: str,
        subject_id: str,
        roles: tuple[str, ...] = ("analyst",),
        is_admin: bool = False,
        permissions: Optional[set[str]] = None,
    ) -> ApiActor:
        """Authenticate and produce an ApiActor with immutable tenant scoping."""
        if not tenant_id or not tenant_id.strip():
            raise AuthenticationError("tenant_id must be provided and non-empty.")
        if not subject_id or not subject_id.strip():
            raise AuthenticationError("subject_id must be provided and non-empty.")

        return ApiActor(
            tenant_id=tenant_id.strip(),
            subject_id=subject_id.strip(),
            roles=roles,
            is_admin=is_admin,
            permissions=frozenset(permissions or set()),
        )

    def check_tenant_access(self, actor: ApiActor, target_tenant_id: str) -> None:
        """Ensure the actor cannot cross tenant boundaries under any circumstances."""
        if actor.is_admin:
            return
        if actor.tenant_id != target_tenant_id:
            raise AuthorizationError(
                f"Tenant isolation breach attempt: Actor tenant '{actor.tenant_id}' "
                f"cannot access target tenant '{target_tenant_id}'."
            )

    def check_source_access(self, actor: ApiActor, source_id: str) -> None:
        """Verify the actor's tenant currently has access to the requested data source."""
        if actor.is_admin:
            return
        allowed_sources = self._tenant_source_permissions.get(actor.tenant_id, set())
        if source_id not in allowed_sources:
            raise AuthorizationError(
                f"Source access denied: Tenant '{actor.tenant_id}' does not have access "
                f"to data source '{source_id}'."
            )

    def check_turn_ownership(self, actor: ApiActor, turn: Turn) -> None:
        """Verify that the turn belongs to the actor's tenant and subject."""
        self.check_tenant_access(actor, turn.tenant_id)
        if not actor.is_admin and turn.subject_id != actor.subject_id:
            raise AuthorizationError(
                f"Turn access denied: Actor '{actor.subject_id}' does not own turn '{turn.turn_id}'."
            )

    def check_measure_access(self, actor: ApiActor, measure_def: Any) -> None:
        """Verify actor has permission for a specific catalog measure."""
        if actor.is_admin:
            return
        if measure_def.required_role and not actor.has_role(measure_def.required_role):
            raise AuthorizationError(
                f"Access denied: Insufficient role permissions for metric '{measure_def.name}'. "
                f"Requires role '{measure_def.required_role}'."
            )
        if measure_def.sensitivity in ("RESTRICTED", "CONFIDENTIAL"):
            perm = f"read:{measure_def.id}"
            if not actor.has_permission(perm):
                raise AuthorizationError(
                    f"Access denied: Missing explicit permission '{perm}' for restricted metric '{measure_def.name}'."
                )

    def check_dimension_access(self, actor: ApiActor, dimension_def: Any) -> None:
        """Verify actor has permission for a specific catalog dimension."""
        if actor.is_admin:
            return
        if getattr(dimension_def, "required_role", None) and not actor.has_role(dimension_def.required_role):
            raise AuthorizationError(
                f"Access denied: Insufficient role permissions for dimension '{dimension_def.name}'. "
                f"Requires role '{dimension_def.required_role}'."
            )
        if getattr(dimension_def, "sensitivity", "INTERNAL") in ("RESTRICTED", "CONFIDENTIAL"):
            perm = f"read:{dimension_def.id}"
            if not actor.has_permission(perm):
                raise AuthorizationError(
                    f"Access denied: Missing explicit permission '{perm}' for restricted dimension '{dimension_def.name}'."
                )

    def detect_cross_tenant_injection(self, question: str) -> bool:
        """Fast-path check for obvious prompt injection targeting tenant boundaries."""
        if not question:
            return False
        for pattern in self.INJECTION_PATTERNS:
            if pattern.search(question):
                return True
        return False

