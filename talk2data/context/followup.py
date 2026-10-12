"""Authoritative analytical context management and governed follow-up suggestions."""
from __future__ import annotations
import re
from typing import Any, Optional

from talk2data.catalog.store import SemanticCatalogStore
from talk2data.domain.models import ApiActor
from talk2data.storage.repository import PersistentApplicationStore


class FollowUpService:
    """Generates governed follow-up suggestions and business-calendar enrichment using only accessible catalog metadata.
    
    ENRICHMENT GOVERNANCE BOUNDARIES:
    - Never mutates analytical facts or executes queries automatically.
    - Never exposes hidden catalog objects or dimensions outside user's authorized vocabulary.
    - Strictly avoids causal language: distinguishes temporal/calendar overlap from causal evidence.
    """

    def __init__(self, store: PersistentApplicationStore, catalogs: SemanticCatalogStore):
        self.store = store
        self.catalogs = catalogs

    def get_calendar_enrichment(
        self,
        actor: ApiActor,
        source_id: str,
        period: Optional[str] = None,
    ) -> dict[str, Any]:
        """Produce business-calendar alignment context from approved catalog metadata."""
        pin = self.catalogs.pin(actor.tenant_id, source_id)
        cat = self.catalogs.get_catalog(source_id, pin.release_id)

        fy_start = getattr(cat, "fiscal_year_start_month", 1)
        timezone = getattr(cat, "timezone", "UTC")
        default_period_dim = getattr(cat, "default_period_dimension", "order_date")

        return {
            "source_id": source_id,
            "period": period,
            "fiscal_year_start_month": fy_start,
            "calendar_type": "STANDARD_CALENDAR" if fy_start == 1 else f"FISCAL_CALENDAR_MONTH_{fy_start}",
            "timezone": timezone,
            "default_period_dimension": default_period_dim,
            "causality_disclaimer": "Calendar boundaries represent temporal accounting periods and contextual overlap, not causal drivers of performance.",
        }

    def get_follow_up_suggestions(self, actor: ApiActor, conversation_id: str) -> list[str]:
        """Generate safe, relevant follow-up prompts from persisted analytical context and accessible catalog metadata."""
        ctx = self.store.get_analytical_context(actor, conversation_id)
        if not ctx:
            return []

        source_id = ctx["source_id"]
        pin = self.catalogs.pin(actor.tenant_id, source_id)
        vocab = self.catalogs.get_accessible_vocabulary(actor, pin)
        cat = self.catalogs.get_catalog(source_id, pin.release_id)

        measure_name = ctx["measure"]
        current_dims = ctx.get("dimensions", [])
        period = ctx.get("current_period")

        suggestions: list[str] = []

        # 1. Dimension breakdown suggestion (find an authorized accessible dimension not currently queried)
        accessible_dims = [
            d.name for d in cat.dimensions.values()
            if (d.id in vocab.dimensions or d.name in vocab.dimensions)
            and not getattr(d, "requires_unsupported_disclosure_protection", False)
        ]
        remaining_dims = [d for d in accessible_dims if d.lower() not in [cd.lower() for cd in current_dims]]
        if remaining_dims:
            sugg_dim = remaining_dims[0]
            period_suffix = f" in {period}" if period else ""
            suggestions.append(f"Show {measure_name} by {sugg_dim}{period_suffix}")

        # 2. Comparison period suggestion (if single period query)
        if period and not ctx.get("comparison_period"):
            if re_match := re.match(r"^\d{4}$", period):
                prior_year = str(int(period) - 1)
                suggestions.append(f"Compare {measure_name} between {period} and {prior_year}")
            elif "q" in period.lower():
                suggestions.append(f"Compare {period} with previous quarter")

        # 3. Overall total suggestion (if slice query)
        if current_dims:
            period_suffix = f" in {period}" if period else ""
            suggestions.append(f"What was total {measure_name}{period_suffix} across all categories?")

        return suggestions[:3]

