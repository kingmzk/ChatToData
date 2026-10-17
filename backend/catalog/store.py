"""Semantic catalog registry, version management, and role-based filtering."""
from __future__ import annotations
from typing import Optional
from copy import deepcopy
from backend.catalog.schema import SemanticCatalog, MeasureDefinition, DimensionDefinition
from backend.catalog.pinning import SemanticSnapshotPin, AccessibleVocabulary
from backend.domain.models import ApiActor


class CatalogNotFoundError(Exception):
    """Raised when a catalog release or source cannot be found."""
    pass


class SemanticCatalogStore:
    """Manages versioned semantic catalogs and access-governed vocabulary extraction."""

    def __init__(self):
        # (source_id, release_id) -> SemanticCatalog
        self._catalogs: dict[tuple[str, str], SemanticCatalog] = {}
        # source_id -> latest release_id
        self._active_releases: dict[str, str] = {}

    def register_catalog(self, catalog: SemanticCatalog, set_active: bool = True) -> None:
        """Register a versioned semantic catalog."""
        key = (catalog.source_id, catalog.release_id)
        if key in self._catalogs and self._catalogs[key] != catalog:
            raise ValueError("Semantic releases are immutable; register a new release identifier")
        self._catalogs[key] = deepcopy(catalog)
        if set_active or catalog.source_id not in self._active_releases:
            self._active_releases[catalog.source_id] = catalog.release_id

    def get_catalog(self, source_id: str, release_id: Optional[str] = None) -> SemanticCatalog:
        """Retrieve a specific or active catalog release."""
        rel = release_id or self._active_releases.get(source_id)
        if not rel:
            raise CatalogNotFoundError(f"No active release found for source '{source_id}'.")
        key = (source_id, rel)
        if key not in self._catalogs:
            raise CatalogNotFoundError(f"Semantic release '{rel}' not found for source '{source_id}'.")
        return deepcopy(self._catalogs[key])

    def pin(self, tenant_id: str, source_id: str) -> SemanticSnapshotPin:
        """Pin the current active release for a source to guarantee turn reproducibility."""
        cat = self.get_catalog(source_id)
        return SemanticSnapshotPin(
            release_id=cat.release_id,
            epoch=cat.epoch,
            source_id=cat.source_id,
        )

    def get_accessible_vocabulary(
        self,
        actor: ApiActor,
        pin: SemanticSnapshotPin,
    ) -> AccessibleVocabulary:
        """Filter the pinned catalog's vocabulary down to what the actor is permitted to see."""
        cat = self.get_catalog(pin.source_id, pin.release_id)

        allowed_measures: dict[str, MeasureDefinition] = {}
        for m_id, measure in cat.measures.items():
            # If measure requires a specific role, verify actor has it
            if measure.required_role and not actor.has_role(measure.required_role):
                continue
            # If measure is RESTRICTED or CONFIDENTIAL, check permission
            if measure.sensitivity in ("RESTRICTED", "CONFIDENTIAL"):
                if not actor.is_admin and not actor.has_permission(f"read:{measure.id}"):
                    continue
            allowed_measures[m_id] = measure

        allowed_dimensions: dict[str, DimensionDefinition] = {}
        for d_id, dimension in cat.dimensions.items():
            if dimension.required_role and not actor.has_role(dimension.required_role):
                continue
            if dimension.sensitivity in ("RESTRICTED", "CONFIDENTIAL"):
                if not actor.is_admin and not actor.has_permission(f"read:{dimension.id}"):
                    continue
            allowed_dimensions[d_id] = dimension


        return AccessibleVocabulary(
            release_id=cat.release_id,
            source_id=cat.source_id,
            measures=allowed_measures,
            dimensions=allowed_dimensions,
            scenario_models={key: model for key, model in cat.scenario_models.items() if model.output_metric in allowed_measures},
            time_series_policies={key: policy for key, policy in cat.time_series_policies.items() if key in allowed_measures and policy.period_dimension in allowed_dimensions},
        )
