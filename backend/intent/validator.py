"""Deterministic intent validation gate distinguishing clarification, decline, and execution."""
from __future__ import annotations
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Optional
from backend.catalog.pinning import AccessibleVocabulary
from backend.catalog.schema import SemanticCatalog
from backend.domain.models import ClarificationOption, ClarificationRequest
from backend.intent.schema import StructuredIntent, AnalysisFamily


@dataclass(frozen=True)
class IntentValidationResult:
    """Outcome of deterministic intent validation."""
    status: str  # 'VALID', 'CLARIFY', 'DECLINE'
    validated_intent: Optional[StructuredIntent] = None
    decline_reason: Optional[str] = None
    clarification_request: Optional[ClarificationRequest] = None


class IntentValidator:
    """Deterministic validation gate.
    
    Principles:
    - Missing information triggers CLARIFICATION.
    - Unsupported, unauthorized, or non-analytical queries trigger DECLINE.
    - Never invent missing periods, dimensions, or business definitions.
    - Clarification and decline NEVER continue directly into semantic resolution.
    """

    GENERIC_TABLE_WORDS = frozenset({"header", "detail", "details", "history", "archive", "item", "items", "table"})
    METRIC_CONFIRMED_PREFIX = "Metric confirmed by user:"

    @staticmethod
    def _stem(word: str) -> str:
        for suffix in ("ing", "es", "s", "e"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[: -len(suffix)]
        return word

    @classmethod
    def _table_words(cls, table_name: str) -> set[str]:
        words: set[str] = set()
        for part in table_name.split("_"):
            words.update(w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", part))
        return {cls._stem(w) for w in words - cls.GENERIC_TABLE_WORDS}

    def _permitted_dimension_alias(
        self, dim_name: str, measure_def: Any, vocabulary: AccessibleVocabulary, question_words: set[str],
    ) -> str:
        dim_def = vocabulary.get_dimension(dim_name)
        if not dim_def or dim_def.id in measure_def.allowed_dimensions:
            return dim_name
        alternatives = [
            d for d in vocabulary.dimensions.values()
            if d.id != dim_def.id and d.column.lower() == dim_def.column.lower()
            and d.id in measure_def.allowed_dimensions
        ]
        if len(alternatives) == 1:
            return alternatives[0].id
        scored = [(len(self._table_words(d.table) & question_words), d) for d in alternatives]
        best = max((s for s, _ in scored), default=0)
        winners = [d for s, d in scored if s == best]
        return winners[0].id if best > 0 and len(winners) == 1 else dim_name

    GREETING_PATTERNS = [
        re.compile(r"^\s*(hello|hi|hey|greetings|good\s+(?:morning|afternoon|evening))\b.*", re.IGNORECASE),
        re.compile(r"^\s*(who\s+are\s+you|what\s+can\s+you\s+do|how\s+are\s+you)\b.*", re.IGNORECASE),
    ]

    def validate_intent(
        self,
        intent: StructuredIntent,
        vocabulary: AccessibleVocabulary,
        user_question: str,
        turn_id: str,
        tenant_id: str,
        subject_id: str,
        catalog: Optional[SemanticCatalog] = None,
    ) -> IntentValidationResult:
        """Deterministically validate intent against accessible catalog vocabulary."""
        q_clean = user_question.strip()

        # 1. Fast-Path Greeting / Non-Analytics Detection
        for pat in self.GREETING_PATTERNS:
            if pat.match(q_clean):
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason="Non-analytical input. Please ask an analytical question against your data.",
                )

        # 2. Prevent direct user filtering on tenant isolation columns
        for f in intent.filters:
            if f.dimension.lower() in ("tenant_id", "tenant"):
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason="Security policy violation: Direct filtering on tenant isolation columns is strictly forbidden.",
                )

        # 3. Check for missing measure or disambiguation
        if not intent.measure:
            measures_list = list(vocabulary.measures.values())
            if not measures_list:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason="No analytical measures are accessible to your user role.",
                )

            options = [
                ClarificationOption(
                    choice_id=f"opt_metric_{i+1}",
                    label=m.name,
                    target_type="metric",
                    resolved_value=m.id,
                )
                for i, m in enumerate(measures_list[:4])
            ]
            clar_req = ClarificationRequest(
                clarification_id=f"clar_{turn_id}_metric",
                turn_id=turn_id,
                tenant_id=tenant_id,
                subject_id=subject_id,
                source_id=vocabulary.source_id,
                semantic_release_id=vocabulary.release_id,
                ambiguity_code="metric-disambiguation",
                question_prompt="I couldn't match your question to an available metric. Which of these metrics would you like to analyze?",
                options=options,
            )
            return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

        # Verify measure exists in accessible vocabulary vs known catalog metric
        measure_def = vocabulary.get_measure(intent.measure)
        if not measure_def:
            # Check if this metric exists in the catalog but was restricted
            if catalog and intent.measure in catalog.measures:
                m_full = catalog.measures[intent.measure]
                role_req = f" Requires role '{m_full.required_role}'." if m_full.required_role else ""
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Access denied: Insufficient role permissions for metric '{m_full.name}'.{role_req}",
                )
            return IntentValidationResult(
                status="DECLINE",
                decline_reason=f"Unsupported metric: '{intent.measure}' does not exist in approved catalog.",
            )

        # Check if measure or base table requires unsupported disclosure protection
        if getattr(measure_def, "requires_unsupported_disclosure_protection", False):
            return IntentValidationResult(
                status="DECLINE",
                decline_reason=f"Dataset/measure '{measure_def.name}' requires unsupported disclosure protections and remains disabled.",
            )
        if catalog and measure_def.table in catalog.tables:
            tbl_def = catalog.tables[measure_def.table]
            if getattr(tbl_def, "requires_unsupported_disclosure_protection", False):
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Dataset/table '{tbl_def.table_name}' requires unsupported disclosure protections and remains disabled.",
                )

        # 3b. Same column/aggregation exposed by several tables (e.g. Sales vs Purchasing TotalDue):
        # never guess silently. Use the table the question names, otherwise ask.
        already_confirmed = any(a.startswith(self.METRIC_CONFIRMED_PREFIX) for a in intent.assumptions)
        if not already_confirmed:
            peers = [
                m for m in vocabulary.measures.values()
                if m.id != measure_def.id and m.table != measure_def.table
                and m.column.lower() == measure_def.column.lower()
                and m.aggregation == measure_def.aggregation
            ]
            if peers:
                by_table: dict[str, Any] = {measure_def.table: measure_def}
                for m in peers:
                    current = by_table.get(m.table)
                    # One candidate per table; the physical-column measure wins over aliases such as 'revenue'.
                    if current is None or (current is not measure_def and m.id.startswith(f"{m.table}_")):
                        by_table[m.table] = m
                candidates = list(by_table.values())
                q_words = {self._stem(w) for w in re.findall(r"[a-z]+", q_clean.lower())}

                def _score(m: Any) -> tuple[int, float]:
                    words = self._table_words(m.table)
                    hits = len(words & q_words)
                    return (hits, hits / len(words) if words else 0.0)

                scores = {m.id: _score(m) for m in candidates}
                top = max(scores.values())
                winners = [m for m in candidates if scores[m.id] == top]
                if top[0] > 0 and len(winners) == 1:
                    if winners[0].id != measure_def.id:
                        intent = replace(intent, measure=winners[0].id)
                        measure_def = winners[0]
                else:
                    ranked = sorted(candidates, key=lambda m: (-scores[m.id][0], -scores[m.id][1], m.table))[:4]
                    options = [
                        ClarificationOption(
                            choice_id=f"opt_metric_{i + 1}",
                            label=f"{m.name} (table {m.table})",
                            target_type="metric",
                            resolved_value=m.id,
                        )
                        for i, m in enumerate(ranked)
                    ]
                    clar_req = ClarificationRequest(
                        clarification_id=f"clar_{turn_id}_metric_table",
                        turn_id=turn_id, tenant_id=tenant_id, subject_id=subject_id,
                        source_id=vocabulary.source_id,
                        semantic_release_id=vocabulary.release_id,
                        ambiguity_code="metric-disambiguation",
                        question_prompt=f"'{measure_def.column}' exists in more than one table. Which one do you mean?",
                        options=options,
                    )
                    return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

        # 4. Check Family
        family = intent.family or AnalysisFamily.TOTAL
        if family.value not in measure_def.allowed_families:
            return IntentValidationResult(
                status="DECLINE",
                decline_reason=f"Analysis family '{family.value}' is not supported for metric '{measure_def.name}'.",
            )

        # 5. Check Missing Information for Specific Families
        if family == AnalysisFamily.COMPARISON:
            # Check if quarters were requested without a year (e.g. Q3 vs Q2)
            if intent.current_period in ("Q1", "Q2", "Q3", "Q4") and not re.search(r"\d{4}", intent.current_period or ""):
                options = [
                    ClarificationOption(
                        choice_id="opt_yr_2024",
                        label="2024 (Compare 2024-Q3 with 2024-Q2)",
                        target_type="period_comparison",
                        resolved_value={"current": "2024-Q3", "comparison": "2024-Q2"},
                    ),
                    ClarificationOption(
                        choice_id="opt_yr_2023",
                        label="2023 (Compare 2023-Q3 with 2023-Q2)",
                        target_type="period_comparison",
                        resolved_value={"current": "2023-Q3", "comparison": "2023-Q2"},
                    ),
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_yr",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="period-year-required",
                    question_prompt="Which year's Q3 and Q2 would you like to compare?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

            if not intent.current_period or not intent.comparison_period:
                # Missing comparison period or current period -> Trigger CLARIFICATION
                options = [
                    ClarificationOption(
                        choice_id="opt_cmp_1",
                        label="Compare 2024 with 2023",
                        target_type="period_comparison",
                        resolved_value={"current": "2024", "comparison": "2023"},
                    ),
                    ClarificationOption(
                        choice_id="opt_cmp_2",
                        label="Compare Q3 with Q2",
                        target_type="period_comparison",
                        resolved_value={"current": "Q3", "comparison": "Q2"},
                    ),
                    ClarificationOption(
                        choice_id="opt_cmp_3",
                        label="Compare Current Month with Previous Month",
                        target_type="period_comparison",
                        resolved_value={"current": "current_month", "comparison": "prev_month"},
                    ),
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_cmp_period",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="comparison-period-required",
                    question_prompt="Please select the time periods to compare:",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

        if family == AnalysisFamily.FORECAST:
            if catalog and intent.measure in catalog.time_series_policies:
                ts_policy = catalog.time_series_policies[intent.measure]
                if not ts_policy.linear_trend_eligible:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Metric '{measure_def.name}' is not eligible for linear trend forecasting under catalog governance policy.",
                    )
            if intent.horizon is None:
                options = [
                    ClarificationOption(
                        choice_id="opt_fc_h1",
                        label="1 period ahead",
                        target_type="forecast_horizon",
                        resolved_value=1,
                    ),
                    ClarificationOption(
                        choice_id="opt_fc_h2",
                        label="2 periods ahead",
                        target_type="forecast_horizon",
                        resolved_value=2,
                    ),
                    ClarificationOption(
                        choice_id="opt_fc_h4",
                        label="4 periods ahead",
                        target_type="forecast_horizon",
                        resolved_value=4,
                    ),
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_fc_horizon",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="forecast-horizon-required",
                    question_prompt="How many periods ahead would you like to forecast?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

            if intent.horizon < 1:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Forecast horizon must be a positive integer, got {intent.horizon}.",
                )
            if catalog and intent.measure in catalog.time_series_policies:
                ts_policy = catalog.time_series_policies[intent.measure]
                if intent.horizon > ts_policy.max_forecast_horizon:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Requested horizon {intent.horizon} exceeds maximum allowed horizon of {ts_policy.max_forecast_horizon}.",
                    )

        if family == AnalysisFamily.SCENARIO:
            if not catalog or not catalog.scenario_models:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"No approved declarative scenario models defined in catalog for metric '{measure_def.name}'. Hypothetical formulas cannot be invented.",
                )
            candidate_models = [m for m in catalog.scenario_models.values() if m.output_metric == intent.measure]
            if not candidate_models:
                candidate_models = list(catalog.scenario_models.values())

            if not intent.scenario_model:
                options = [
                    ClarificationOption(
                        choice_id=f"opt_scen_{m.model_id}",
                        label=f"{m.name} ({m.description})",
                        target_type="scenario_model",
                        resolved_value=m.model_id,
                    )
                    for m in candidate_models[:4]
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_scen_model",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="scenario-model-required",
                    question_prompt="Which approved scenario model would you like to simulate?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

            if intent.scenario_model not in catalog.scenario_models:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Scenario model '{intent.scenario_model}' does not exist in approved catalog.",
                )
            scen_model = catalog.scenario_models[intent.scenario_model]
            for drv_name, drv_val in intent.adjustments.items():
                if drv_name not in scen_model.drivers:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Driver '{drv_name}' is not recognized in scenario model '{scen_model.name}'.",
                    )
                drv_def = scen_model.drivers[drv_name]
                try:
                    val_dec = Decimal(str(drv_val))
                except Exception:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Invalid numeric value '{drv_val}' for driver '{drv_name}'.",
                    )
                if drv_def.min_value is not None and val_dec < drv_def.min_value:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Adjustment for '{drv_name}' ({val_dec}) is below minimum allowed value of {drv_def.min_value} {drv_def.unit}.",
                    )
                if drv_def.max_value is not None and val_dec > drv_def.max_value:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Adjustment for '{drv_name}' ({val_dec}) exceeds maximum allowed value of {drv_def.max_value} {drv_def.unit}.",
                    )

        if family == AnalysisFamily.GOAL_SEEK:
            if not catalog or not catalog.scenario_models:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason="No approved scenario models available in catalog to solve goal seek against.",
                )
            target_metric = intent.target_metric or intent.measure
            candidate_models = [m for m in catalog.scenario_models.values() if m.output_metric == target_metric]
            if not candidate_models:
                candidate_models = list(catalog.scenario_models.values())

            if not intent.scenario_model:
                options = [
                    ClarificationOption(
                        choice_id=f"opt_gs_model_{m.model_id}",
                        label=f"{m.name} (Output: {m.output_metric})",
                        target_type="scenario_model",
                        resolved_value=m.model_id,
                    )
                    for m in candidate_models[:4]
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_gs_model",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="scenario-model-required",
                    question_prompt="Which scenario model should be used to solve for your goal?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

            if intent.scenario_model not in catalog.scenario_models:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Scenario model '{intent.scenario_model}' not found in approved catalog.",
                )
            gs_model = catalog.scenario_models[intent.scenario_model]

            if intent.target_value is None:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason="Goal seek requires an explicit target value to solve for.",
                )

            if not intent.adjustable_driver:
                adj_drivers = [d_name for d_name, d_def in gs_model.drivers.items() if d_def.is_adjustable]
                if not adj_drivers:
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Scenario model '{gs_model.name}' has no adjustable drivers.",
                    )
                options = [
                    ClarificationOption(
                        choice_id=f"opt_drv_{d_name}",
                        label=f"Adjust {d_name} ({gs_model.drivers[d_name].unit})",
                        target_type="adjustable_driver",
                        resolved_value=d_name,
                    )
                    for d_name in adj_drivers
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_gs_drv",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="adjustable-driver-required",
                    question_prompt="Which driver would you like to adjust to reach the target?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

            if intent.adjustable_driver not in gs_model.drivers:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Driver '{intent.adjustable_driver}' is not part of model '{gs_model.name}'.",
                )
            if not gs_model.drivers[intent.adjustable_driver].is_adjustable:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Driver '{intent.adjustable_driver}' is marked non-adjustable in model '{gs_model.name}'.",
                )

        # 6. Check if dimension is missing for questions requiring a diagnostic breakdown
        is_breakdown_question = bool(
            re.search(r"\b(contributed|why\s+did|breakdown\s+by|split\s+by)\b", q_clean, re.IGNORECASE)
        )
        if is_breakdown_question and not intent.dimensions:
            dim_list = [d for d in vocabulary.dimensions.values() if d.id in measure_def.allowed_dimensions]
            if dim_list:
                options = [
                    ClarificationOption(
                        choice_id=f"opt_dim_{i+1}",
                        label=f"Break down by {d.name}",
                        target_type="dimension",
                        resolved_value=d.id,
                    )
                    for i, d in enumerate(dim_list[:4])
                ]
                clar_req = ClarificationRequest(
                    clarification_id=f"clar_{turn_id}_dim",
                    turn_id=turn_id,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    source_id=vocabulary.source_id,
                    semantic_release_id=vocabulary.release_id,
                    ambiguity_code="diagnostic-dimension-required",
                    question_prompt="Which dimension should be analyzed for this breakdown?",
                    options=options,
                )
                return IntentValidationResult(status="CLARIFY", clarification_request=clar_req)

        # 7. Validate Dimensions (with guard against hallucinated metric-name groupings)
        # A measure column used as a dimension (TotalDue grouped by TotalDue) is never a meaningful breakdown.
        measure_columns = {(m.table, m.column.lower()) for m in vocabulary.measures.values()}
        own_column = (measure_def.table, measure_def.column.lower())

        def _is_own_column(name: str) -> bool:
            d = vocabulary.get_dimension(name)
            if d:
                return (d.table, d.column.lower()) == own_column
            clean_name = name.lower()
            return (
                clean_name == own_column[1]
                or clean_name.endswith(f"_{own_column[1]}")
                or clean_name == f"{own_column[0].lower()}_{own_column[1]}"
            )

        if any(_is_own_column(n) for n in intent.dimensions):
            intent = replace(intent, dimensions=tuple(n for n in intent.dimensions if not _is_own_column(n)))

        effective_dims = intent.dimensions
        if intent.dimensions and family in (AnalysisFamily.TOTAL, AnalysisFamily.AVERAGE):
            has_grouping_intent = bool(re.search(
                r"\b(by|per|each|across|every|breakdown|split|distribution|group)\b",
                q_clean,
                re.IGNORECASE,
            ))
            if not has_grouping_intent:
                cleaned_dims = []
                for dim_name in intent.dimensions:
                    dim_def = vocabulary.get_dimension(dim_name)
                    if dim_def and (
                        dim_def.column.lower() in ("amount", "price", "revenue", "sales", "cost", "total", "qty", "quantity")
                        or (dim_def.table, dim_def.column.lower()) in measure_columns
                    ):
                        continue
                    cleaned_dims.append(dim_name)
                effective_dims = tuple(cleaned_dims)
                intent = replace(intent, dimensions=effective_dims)

        question_words = {self._stem(w) for w in re.findall(r"[a-z]+", q_clean.lower())}
        remapped = tuple(
            self._permitted_dimension_alias(d, measure_def, vocabulary, question_words) for d in effective_dims
        )
        if remapped != tuple(effective_dims):
            effective_dims = remapped
            intent = replace(intent, dimensions=effective_dims)

        for dim_name in effective_dims:
            dim_def = vocabulary.get_dimension(dim_name)
            if not dim_def:
                if catalog and dim_name in catalog.dimensions:
                    d_full = catalog.dimensions[dim_name]
                    role_req = f" Requires role '{d_full.required_role}'." if getattr(d_full, "required_role", None) else ""
                    return IntentValidationResult(
                        status="DECLINE",
                        decline_reason=f"Access denied: Insufficient role permissions for dimension '{d_full.name}'.{role_req}",
                    )
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Unsupported dimension: '{dim_name}' does not exist in catalog.",
                )
            if dim_def.id not in measure_def.allowed_dimensions:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Dimension '{dim_def.name}' is not permitted for metric '{measure_def.name}'.",
                )


        # 8. Validate Filters
        for f in intent.filters:
            dim_def = vocabulary.get_dimension(f.dimension)
            if not dim_def:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Filter dimension '{f.dimension}' is not recognized in catalog.",
                )
            if f.operator.upper() not in dim_def.allowed_operators:
                return IntentValidationResult(
                    status="DECLINE",
                    decline_reason=f"Operator '{f.operator}' is not allowed for dimension '{dim_def.name}'.",
                )

            expected_arity = 2 if f.operator.upper() == "BETWEEN" else 1
            if (f.operator.upper() == "IN" and not f.values) or (f.operator.upper() != "IN" and len(f.values) != expected_arity):
                return IntentValidationResult(status="DECLINE", decline_reason="Invalid filter value count")

        # 9. Success: Validated Intent
        validated = StructuredIntent(
            measure=measure_def.id,
            family=family,
            dimensions=tuple(
                vocabulary.get_dimension(d).id  # type: ignore
                for d in intent.dimensions
            ),
            filters=tuple(__import__("dataclasses").replace(f, dimension=vocabulary.get_dimension(f.dimension).id, operator=f.operator.upper()) for f in intent.filters),
            current_period=intent.current_period,
            comparison_period=intent.comparison_period,
            ambiguity_code=None,
            assumptions=intent.assumptions,
            horizon=intent.horizon,
            scenario_model=intent.scenario_model,
            adjustments=dict(intent.adjustments),
            target_metric=intent.target_metric,
            target_value=intent.target_value,
            adjustable_driver=intent.adjustable_driver,
            bounds=intent.bounds,
            limit=intent.limit if (intent.limit is None or intent.limit > 0) else None,
            order_by_metric=intent.order_by_metric,
            order_direction=intent.order_direction,
        )
        return IntentValidationResult(status="VALID", validated_intent=validated)
