"""Protected fact contract and tamper-evident evidence sealing."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
import hashlib
import json


def _json_serial(obj: Any) -> Any:
    """JSON serializer for Decimal, datetime, and set types."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(list(obj))
    raise TypeError(f"Type {type(obj)} not serializable")


@dataclass(frozen=True)
class ProtectedFact:
    """Authoritative analytical fact produced solely by deterministic backend."""
    fact_id: str
    metric: str
    calculation_type: str  # TOTAL, AVERAGE, TREND, COMPARISON, CONTRIBUTION
    value: Optional[Decimal]
    unit: Optional[str] = None
    period: Optional[str] = None
    comparison_period: Optional[str] = None
    dimension_values: dict[str, Any] = field(default_factory=dict)
    semantic_release: str = ""
    execution_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lineage: list[str] = field(default_factory=list)
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None
    completeness_status: str = "COMPLETE"
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    method: str = "EXACT_DETERMINISTIC"
    method_version: str = "1.0.0"
    result_type: str = "OBSERVED"  # OBSERVED, PREDICTED, HYPOTHETICAL, SOLVER_SOLUTION
    prediction_interval: Optional[dict[str, Any]] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_canonical_dict(self) -> dict[str, Any]:
        """Canonical dictionary representation for deterministic hashing."""
        return {
            "fact_id": self.fact_id,
            "metric": self.metric,
            "calculation_type": self.calculation_type,
            "value": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "period": self.period,
            "comparison_period": self.comparison_period,
            "dimension_values": {k: str(v) for k, v in sorted(self.dimension_values.items())},
            "semantic_release": self.semantic_release,
            "is_suppressed": self.is_suppressed,
            "suppression_reason": self.suppression_reason,
            "completeness_status": self.completeness_status,
            "assumptions": sorted(self.assumptions),
            "limitations": sorted(self.limitations),
            "method": self.method,
            "method_version": self.method_version,
            "result_type": self.result_type,
            "prediction_interval": self.prediction_interval,
            "diagnostics": {k: str(v) for k, v in sorted(self.diagnostics.items())},
        }


@dataclass(frozen=True)
class SealedEvidencePackage:
    """Evidence package with canonical protected facts and an integrity fingerprint.
    
    NOTE: SHA-256 provides canonical data integrity verification and change detection.
    It is not an independently tamper-resistant hardware seal or cryptographic non-repudiation mechanism.
    """
    package_id: str
    turn_id: str
    tenant_id: str
    semantic_release_id: str
    facts: tuple[ProtectedFact, ...]
    package_digest: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def seal(
        cls,
        package_id: str,
        turn_id: str,
        tenant_id: str,
        semantic_release_id: str,
        facts: list[ProtectedFact],
    ) -> SealedEvidencePackage:
        """Create package and fingerprint with SHA-256 hash of canonical facts."""
        facts_tuple = tuple(facts)
        canonical_facts = [f.to_canonical_dict() for f in facts_tuple]
        raw_json = json.dumps(canonical_facts, sort_keys=True, default=_json_serial)
        digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        return cls(
            package_id=package_id,
            turn_id=turn_id,
            tenant_id=tenant_id,
            semantic_release_id=semantic_release_id,
            facts=facts_tuple,
            package_digest=digest,
        )

    def verify_integrity(self) -> bool:
        """Verify that the package facts match the sealed digest."""
        canonical_facts = [f.to_canonical_dict() for f in self.facts]
        raw_json = json.dumps(canonical_facts, sort_keys=True, default=_json_serial)
        expected = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        return self.package_digest == expected
