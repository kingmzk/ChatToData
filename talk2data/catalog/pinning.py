"""Semantic release pinning and accessible vocabulary projection."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from talk2data.catalog.schema import MeasureDefinition, DimensionDefinition


@dataclass(frozen=True)
class SemanticSnapshotPin:
    """Pins an immutable semantic release and source version for a running turn."""
    release_id: str
    epoch: int
    source_id: str
    pinned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AccessibleVocabulary:
    """Subset of catalog vocabulary strictly accessible to an authenticated actor."""
    release_id: str
    source_id: str
    measures: dict[str, MeasureDefinition]
    dimensions: dict[str, DimensionDefinition]
    scenario_models: dict = field(default_factory=dict)
    time_series_policies: dict = field(default_factory=dict)

    def has_measure(self, measure_name_or_id: str) -> bool:
        norm = measure_name_or_id.strip().lower()
        for m in self.measures.values():
            if m.id.lower() == norm or m.name.lower() == norm:
                return True
        return False

    def get_measure(self, measure_name_or_id: str) -> MeasureDefinition | None:
        norm = measure_name_or_id.strip().lower()
        for m in self.measures.values():
            if m.id.lower() == norm or m.name.lower() == norm:
                return m
        return None

    def has_dimension(self, dimension_name_or_id: str) -> bool:
        norm = dimension_name_or_id.strip().lower()
        for d in self.dimensions.values():
            if d.id.lower() == norm or d.name.lower() == norm:
                return True
        return False

    def get_dimension(self, dimension_name_or_id: str) -> DimensionDefinition | None:
        norm = dimension_name_or_id.strip().lower()
        for d in self.dimensions.values():
            if d.id.lower() == norm or d.name.lower() == norm:
                return d
        return None
