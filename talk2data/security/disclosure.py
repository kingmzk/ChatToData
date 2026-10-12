"""Governed disclosure service enforcing minimum-group, derived, and repeated-query disclosure controls."""
from __future__ import annotations
from talk2data.observability import traced_stage
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Optional
from talk2data.domain.facts import ProtectedFact


class DisclosurePolicyViolationError(Exception):
    """Raised when an operation, metric, or query pattern violates the dataset disclosure policy."""
    pass


@dataclass(frozen=True)
class DatasetDisclosurePolicy:
    """Explicit, dataset-versioned disclosure policy defining permitted analytical scope and thresholds."""
    dataset_id: str
    version: str
    allowed_roles: tuple[str, ...]
    allowed_metrics: tuple[str, ...]
    allowed_dimensions: tuple[str, ...]
    allowed_operations: tuple[str, ...] = (
        "TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION",
        "FORECAST", "ANOMALY", "SCENARIO", "GOAL_SEEK"
    )
    protected_entity: str = "customer_id"
    min_group_threshold: int = 5
    requires_repeated_query_protection: bool = False
    retention_days: int = 90
    export_allowed: bool = True

    def validate_request(
        self,
        user_role: str,
        metric: str,
        dimensions: list[str],
        operation: str,
    ) -> None:
        """Validate if requested analytical operation is permitted by this dataset's policy."""
        if self.allowed_roles and user_role not in self.allowed_roles:
            raise DisclosurePolicyViolationError(
                f"Role '{user_role}' is not authorized to query dataset '{self.dataset_id}' under policy v{self.version}."
            )
        if metric not in self.allowed_metrics:
            raise DisclosurePolicyViolationError(
                f"Metric '{metric}' is not permitted under disclosure policy v{self.version} for dataset '{self.dataset_id}'."
            )
        for d in dimensions:
            if d not in self.allowed_dimensions:
                raise DisclosurePolicyViolationError(
                    f"Dimension '{d}' is not permitted under disclosure policy v{self.version} for dataset '{self.dataset_id}'."
                )
        if operation.upper() not in [op.upper() for op in self.allowed_operations]:
            raise DisclosurePolicyViolationError(
                f"Operation '{operation}' is prohibited by disclosure policy v{self.version} for dataset '{self.dataset_id}'."
            )


def _suppress_fact(fact: ProtectedFact, reason: str) -> ProtectedFact:
    """Sanitize all primary and secondary fields on a suppressed fact to prevent deductive leakage."""
    return replace(
        fact,
        value=None,
        is_suppressed=True,
        suppression_reason=reason,
        prediction_interval=None,
        diagnostics={"suppression_status": "SUPPRESSED"},
        assumptions=["Detailed parameters and baseline values suppressed under disclosure policy."],
        limitations=["Detailed diagnostics and intervals suppressed to prevent secondary deductive leakage."],
    )


class GovernedDisclosureService:
    """Evaluates protected facts against tenant disclosure thresholds using minimum-group and complementary suppression.
    
    DISCLOSURE BOUNDARIES & LIMITATIONS:
    Complementary suppression is implemented specifically as protection against tested arithmetic
    deductions (preventing deduction of a single primary-suppressed slice from table totals or
    unsuppressed sibling slices). It does NOT protect against all single-query disclosure attacks,
    matrix/linear-programming reconstruction attacks, or cross-query differencing across
    independent conversation turns. It does NOT provide formal differential privacy.
    Datasets requiring unsupported disclosure protections MUST remain disabled in the catalog.
    """

    def __init__(self, default_min_entities: int = 5):
        self.default_min_entities = default_min_entities

    @traced_stage('privacy_disclosure')
    def inspect_and_protect(
        self,
        facts: list[ProtectedFact],
        tenant_id: str,
        distinct_entity_counts: Optional[dict[str, int]] = None,
        min_entities: Optional[int] = None,
    ) -> list[ProtectedFact]:
        """Apply minimum-group suppression and complementary suppression to prevent deduction."""
        threshold = min_entities if min_entities is not None else self.default_min_entities
        counts = distinct_entity_counts or {}
        protected: list[ProtectedFact] = []

        # 1. Primary minimum-group suppression
        suppressed_slice_indices: list[int] = []
        slice_indices: list[int] = []

        for i, fact in enumerate(facts):
            if fact.is_suppressed:
                protected.append(_suppress_fact(fact, fact.suppression_reason or "Previously suppressed"))
                if fact.dimension_values:
                    suppressed_slice_indices.append(i)
                continue

            # Verify distinct protected-entity count
            count = counts.get(fact.fact_id)
            if count is not None and count < threshold:
                suppressed_fact = _suppress_fact(
                    fact,
                    f"Minimum-group suppression: distinct entity count {count} < {threshold}",
                )
                protected.append(suppressed_fact)
                if fact.dimension_values:
                    suppressed_slice_indices.append(i)
                continue

            # Check sensitive metric names (e.g. employee salary)
            if "salary" in fact.metric.lower() or "compensation" in fact.metric.lower():
                if count is None or count < threshold:
                    suppressed_fact = _suppress_fact(
                        fact,
                        f"Sensitive metric suppression: individual compensation below threshold {threshold}",
                    )
                    protected.append(suppressed_fact)
                    if fact.dimension_values:
                        suppressed_slice_indices.append(i)
                    continue

            if fact.dimension_values:
                slice_indices.append(i)
            protected.append(fact)

        # 2. Derived Result Suppression: Prevent deductive leakage from suppressed historical or baseline inputs
        has_suppressed_inputs = any(
            f.is_suppressed and f.calculation_type in ("HISTORICAL", "TOTAL", "AVERAGE", "SLICE")
            for f in protected
        )
        if has_suppressed_inputs:
            for j, pf in enumerate(protected):
                if pf.calculation_type in ("FORECAST", "ANOMALY", "SCENARIO", "GOAL_SEEK") or pf.result_type in ("PREDICTED", "HYPOTHETICAL", "SOLVER_SOLUTION"):
                    if not pf.is_suppressed:
                        protected[j] = _suppress_fact(
                            pf,
                            "Derived result suppression: underlying analysis population contains suppressed inputs or fails disclosure threshold.",
                        )

        # 3. Complementary suppression to prevent deduction from totals
        total_index = next((idx for idx, f in enumerate(protected) if f.calculation_type in ("TOTAL", "AVERAGE") and not f.dimension_values), None)

        if total_index is not None and len(suppressed_slice_indices) == 1 and len(slice_indices) >= 1:
            unsuppressed_slices = [
                (idx, protected[idx]) for idx in slice_indices if not protected[idx].is_suppressed and protected[idx].value is not None
            ]
            if unsuppressed_slices:
                target_idx, target_fact = min(unsuppressed_slices, key=lambda x: x[1].value)  # type: ignore
                protected[target_idx] = _suppress_fact(
                    target_fact,
                    "Complementary suppression: masked to prevent mathematical deduction of primary suppressed cell",
                )
            else:
                total_fact = protected[total_index]
                protected[total_index] = _suppress_fact(
                    total_fact,
                    "Complementary suppression: masked to prevent deductive disclosure of single constituent group",
                )

        return protected


@dataclass(frozen=True)
class DisclosureReleaseRecord:
    """Immutable audit record of released aggregate analytical outputs."""
    record_id: str
    tenant_id: str
    dataset_id: str
    policy_version: str
    canonical_scope_digest: str
    metric: str
    dimensions: tuple[str, ...]
    filters: tuple[tuple[str, str, str], ...]
    entity_count: int
    released_value: Optional[str]
    released_at: str
    turn_id: str
    conversation_id: str
    subject_id: str


class RepeatedQueryAccountingService:
    """Experimental in-memory count-difference heuristic; not a production privacy boundary.
    
    SUPPORTED PROTECTIONS:
    - Canonical Query Scope Deduplication (idempotent retries and cross-conversation replays).
    - Totals vs Filtered Subsets Differencing Check (prevents leaking small sub-populations |Total - Subset| < k).
    - Overlapping Population Differencing Check.
    
    LIMITATIONS & BOUNDARIES:
    This service evaluates pairwise and linear differencing between released aggregate query scopes.
    It does NOT provide formal differential privacy guarantees (DP) and does not protect against
    higher-order linear programming reconstruction attacks across vast query numbers.
    Unsupported sensitive queries are rejected.
    """

    def __init__(self):
        # In-memory index of releases: tenant_id -> list of DisclosureReleaseRecord
        self._releases: dict[str, list[DisclosureReleaseRecord]] = {}

    @staticmethod
    def compute_canonical_scope_digest(
        tenant_id: str,
        dataset_id: str,
        metric: str,
        dimensions: list[str],
        filters: list[dict[str, Any]],
        policy_version: str,
    ) -> str:
        """Deterministic hash of the canonical analytical scope."""
        norm_dims = sorted([d.lower() for d in dimensions])
        norm_filters = []
        for f in filters:
            dim = f.get("dimension", "").lower()
            op = f.get("operator", "=").upper()
            vals = sorted([str(v) for v in f.get("values", [])])
            norm_filters.append((dim, op, ",".join(vals)))
        norm_filters.sort()

        canonical_obj = {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
            "metric": metric.lower(),
            "dimensions": norm_dims,
            "filters": norm_filters,
            "policy_version": policy_version,
        }
        raw_str = json.dumps(canonical_obj, sort_keys=True)
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def evaluate_release(
        self,
        policy: DatasetDisclosurePolicy,
        tenant_id: str,
        metric: str,
        dimensions: list[str],
        filters: list[dict[str, Any]],
        entity_count: int,
        turn_id: str,
        conversation_id: str,
        subject_id: str,
        released_value: Optional[str] = None,
    ) -> tuple[bool, Optional[str], Optional[DisclosureReleaseRecord]]:
        """Evaluate candidate release against historical disclosures for the tenant.
        
        Returns:
            (is_allowed, reason_or_status, release_record)
        """
        if not policy.requires_repeated_query_protection:
            # Policy does not require cross-query tracking
            return True, "POLICY_REPEATED_QUERY_TRACKING_NOT_REQUIRED", None

        scope_digest = self.compute_canonical_scope_digest(
            tenant_id=tenant_id,
            dataset_id=policy.dataset_id,
            metric=metric,
            dimensions=dimensions,
            filters=filters,
            policy_version=policy.version,
        )

        history = self._releases.get(tenant_id, [])

        # 1. Check for exact scope match (Idempotent retry or repeated query)
        for prev in history:
            if (
                prev.dataset_id == policy.dataset_id
                and prev.canonical_scope_digest == scope_digest
            ):
                # Identical canonical query scope: idempotent release allowed without double-counting
                return True, "IDEMPOTENT_SCOPE_MATCH", prev

        # 2. Check for differencing attack against prior releases on same metric
        threshold = policy.min_group_threshold
        norm_filters = tuple(sorted([
            (f.get("dimension", "").lower(), f.get("operator", "=").upper(), ",".join(sorted([str(v) for v in f.get("values", [])])))
            for f in filters
        ]))

        for prev in history:
            if prev.dataset_id != policy.dataset_id or prev.metric.lower() != metric.lower():
                continue

            # Compare populations: Total vs Subset differencing
            # If one query has no filters (Total) and candidate has filters (Subset), or vice-versa:
            diff = abs(entity_count - prev.entity_count)
            if 0 < diff < threshold:
                return (
                    False,
                    f"Repeated-query differencing violation: population residual difference ({diff}) between candidate query and previously released query ({prev.turn_id}) is below minimum threshold ({threshold}).",
                    None,
                )

            # Overlapping subsets: If filters differ slightly leaving a residual < threshold
            if set(norm_filters) != set(prev.filters):
                if 0 < diff < threshold:
                    return (
                        False,
                        f"Overlapping population differencing violation: residual entity difference ({diff}) fails threshold ({threshold}).",
                        None,
                    )

        # 3. Create release record and commit to ledger
        import uuid
        from datetime import datetime, timezone
        rec = DisclosureReleaseRecord(
            record_id=f"disc_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            dataset_id=policy.dataset_id,
            policy_version=policy.version,
            canonical_scope_digest=scope_digest,
            metric=metric,
            dimensions=tuple(sorted(dimensions)),
            filters=norm_filters,
            entity_count=entity_count,
            released_value=released_value,
            released_at=datetime.now(timezone.utc).isoformat(),
            turn_id=turn_id,
            conversation_id=conversation_id,
            subject_id=subject_id,
        )
        if tenant_id not in self._releases:
            self._releases[tenant_id] = []
        self._releases[tenant_id].append(rec)

        return True, "RELEASE_RECORDED", rec

    def get_records_for_tenant(self, tenant_id: str) -> list[DisclosureReleaseRecord]:
        return list(self._releases.get(tenant_id, []))

