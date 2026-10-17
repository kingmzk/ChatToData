"""Deterministic analytical kernel performing zero-LLM exact math calculations."""
from __future__ import annotations
from backend.observability import traced_stage
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any
from backend.catalog.pinning import SemanticSnapshotPin
from backend.domain.facts import ProtectedFact
from backend.intent.schema import AnalysisFamily
from backend.semantic.plan import ResolvedSemanticPlan


class DeterministicAnalysisKernel:
    """Executes authoritative numerical calculations strictly in deterministic Python."""

    @traced_stage('deterministic_analysis')
    def analyze(
        self,
        plan: ResolvedSemanticPlan,
        rows: list[dict[str, Any]],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Compute authoritative protected facts from aggregate query output."""
        # Phase 3 Decision Science Family Dispatches
        if plan.family == AnalysisFamily.FORECAST:
            return self._analyze_forecast(plan, rows, pin)
        if plan.family == AnalysisFamily.ANOMALY:
            return self._analyze_anomaly(plan, rows, pin)
        if plan.family == AnalysisFamily.SCENARIO:
            return self._analyze_scenario(plan, rows, pin)
        if plan.family == AnalysisFamily.GOAL_SEEK:
            return self._analyze_goal_seek(plan, rows, pin)

        facts: list[ProtectedFact] = []
        metric_name = plan.measure.name
        unit = plan.units
        release_id = pin.release_id
        lineage = [f"{t.schema_name}.{t.table_name}" for t in plan.required_tables]

        # Handle empty results
        if not rows:
            facts.append(
                ProtectedFact(
                    fact_id="fact_empty_001",
                    metric=metric_name,
                    calculation_type=plan.family.value,
                    value=Decimal("0"),
                    unit=unit,
                    period=plan.current_period,
                    semantic_release=release_id,
                    lineage=lineage,
                    completeness_status="COMPLETE",
                    assumptions=["Query returned 0 matching records."],
                )
            )
            return facts

        # Case 1: Simple Total / Average without Slices
        if not plan.dimensions:
            raw_val = rows[0].get("metric_value")
            val = None if raw_val is None and plan.measure.aggregation.value not in ("SUM", "COUNT", "COUNT_DISTINCT") else self._to_decimal(raw_val)
            facts.append(
                ProtectedFact(
                    fact_id=f"fact_{plan.family.value.lower()}_001",
                    metric=metric_name,
                    calculation_type=plan.family.value,
                    value=val,
                    unit=unit,
                    period=plan.current_period,
                    semantic_release=release_id,
                    lineage=lineage,
                    completeness_status="COMPLETE",
                    assumptions=list(plan.assumptions),
                )
            )
            return facts

        # Case 2: Sliced Breakdown (Total / Contribution / Comparison)
        total_val = Decimal("0")
        slice_facts: list[ProtectedFact] = []
        idx = 1

        for r in rows:
            raw_val = r.get("metric_value")
            val = self._to_decimal(raw_val)
            total_val += val

            # Extract dimension values
            dim_vals = {}
            for d in plan.dimensions:
                col_key = f"{d.table}.{d.column}"
                # Sometimes returned as alias or plain column
                val_extracted = r.get(col_key, r.get(d.column, r.get(f"{d.column}")))
                dim_vals[d.id] = str(val_extracted)

            fact_id = f"fact_slice_{idx:03d}"
            idx += 1
            slice_facts.append(
                ProtectedFact(
                    fact_id=fact_id,
                    metric=metric_name,
                    calculation_type=plan.family.value,
                    value=val,
                    unit=unit,
                    period=plan.current_period,
                    dimension_values=dim_vals,
                    semantic_release=release_id,
                    lineage=lineage,
                    completeness_status="COMPLETE",
                    assumptions=list(plan.assumptions),
                )
            )

        # Non-additive grouped values cannot be summed into an overall metric.
        if plan.measure.aggregation.value in ("AVG", "COUNT_DISTINCT", "MIN", "MAX"):
            return slice_facts

        # Add total fact
        facts.append(
            ProtectedFact(
                fact_id="fact_total_001",
                metric=metric_name,
                calculation_type="TOTAL",
                value=total_val,
                unit=unit,
                period=plan.current_period,
                semantic_release=release_id,
                lineage=lineage,
                completeness_status="COMPLETE",
                assumptions=list(plan.assumptions),
            )
        )
        facts.extend(slice_facts)

        # Contribution Calculations
        if plan.family == AnalysisFamily.CONTRIBUTION and total_val != Decimal("0"):
            top_slice = None
            max_val = Decimal("-Infinity")

            for s in slice_facts:
                if s.value is not None:
                    contrib_pct = ((s.value / total_val) * Decimal("100")).quantize(Decimal("0.01"))
                    facts.append(
                        ProtectedFact(
                            fact_id=f"fact_contrib_{s.fact_id}",
                            metric=metric_name,
                            calculation_type="CONTRIBUTION_PERCENT",
                            value=contrib_pct,
                            unit="%",
                            period=plan.current_period,
                            dimension_values=s.dimension_values,
                            semantic_release=release_id,
                            lineage=lineage,
                            completeness_status="COMPLETE",
                        )
                    )
                    if s.value > max_val:
                        max_val = s.value
                        top_slice = s

            if top_slice is not None:
                facts.append(
                    ProtectedFact(
                        fact_id="fact_top_contributor",
                        metric=metric_name,
                        calculation_type="TOP_CONTRIBUTOR",
                        value=top_slice.value,
                        unit=unit,
                        period=plan.current_period,
                        dimension_values=top_slice.dimension_values,
                        semantic_release=release_id,
                        lineage=lineage,
                        completeness_status="COMPLETE",
                    )
                )

        return facts

    def compute_comparison(
        self,
        current_fact: ProtectedFact,
        comparison_fact: ProtectedFact,
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Compute delta and growth percentage with strict zero-denominator handling."""
        facts: list[ProtectedFact] = [current_fact, comparison_fact]
        curr_val = current_fact.value or Decimal("0")
        comp_val = comparison_fact.value or Decimal("0")

        combined_assumptions = list(current_fact.assumptions) + [
            a for a in comparison_fact.assumptions if a not in current_fact.assumptions
        ]

        delta = curr_val - comp_val if current_fact.value is not None and comparison_fact.value is not None else None
        facts.append(
            ProtectedFact(
                fact_id="fact_comparison_delta",
                metric=current_fact.metric,
                calculation_type="DELTA",
                value=delta,
                unit=current_fact.unit,
                period=current_fact.period,
                comparison_period=comparison_fact.period,
                semantic_release=pin.release_id,
                lineage=current_fact.lineage,
                completeness_status="COMPLETE",
                assumptions=list(combined_assumptions),
            )
        )

        # Zero-denominator protection
        if comp_val == Decimal("0") or delta is None:
            facts.append(
                ProtectedFact(
                    fact_id="fact_growth_percent",
                    metric=current_fact.metric,
                    calculation_type="GROWTH_PERCENT",
                    value=None,
                    unit="%",
                    period=current_fact.period,
                    comparison_period=comparison_fact.period,
                    semantic_release=pin.release_id,
                    lineage=current_fact.lineage,
                    completeness_status="COMPLETE",
                    assumptions=list(combined_assumptions) + ["Baseline comparison value is 0; growth percentage is mathematically undefined."],
                )
            )
        else:
            growth_pct = ((delta / abs(comp_val)) * Decimal("100")).quantize(Decimal("0.01"))
            facts.append(
                ProtectedFact(
                    fact_id="fact_growth_percent",
                    metric=current_fact.metric,
                    calculation_type="GROWTH_PERCENT",
                    value=growth_pct,
                    unit="%",
                    period=current_fact.period,
                    comparison_period=comparison_fact.period,
                    semantic_release=pin.release_id,
                    lineage=current_fact.lineage,
                    completeness_status="COMPLETE",
                    assumptions=list(combined_assumptions),
                )
            )

        return facts

    def compute_growth_contribution(
        self,
        current_facts: list[ProtectedFact],
        comparison_facts: list[ProtectedFact],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Compute each dimension slice's contribution to total change (growth) between periods.
        
        Contribution to growth distinguishes absolute sales share from contribution to change:
        Contrib % = (Slice_current - Slice_comparison) / (Total_current - Total_comparison) * 100
        """
        curr_total = next((f for f in current_facts if f.calculation_type in ("TOTAL", "AVERAGE") and not f.dimension_values), None)
        comp_total = next((f for f in comparison_facts if f.calculation_type in ("TOTAL", "AVERAGE") and not f.dimension_values), None)

        if not curr_total or not comp_total:
            return current_facts

        curr_tot_val = curr_total.value or Decimal("0")
        comp_tot_val = comp_total.value or Decimal("0")
        total_growth_delta = curr_tot_val - comp_tot_val

        facts: list[ProtectedFact] = list(self.compute_comparison(curr_total, comp_total, pin))

        # Map comparison slices by dimension key (filtering for base slice values)
        comp_slices_by_dim = {}
        for cf in comparison_facts:
            if cf.dimension_values and cf.calculation_type in ("TOTAL", "AVERAGE", "CONTRIBUTION"):
                key = tuple(sorted(cf.dimension_values.items()))
                comp_slices_by_dim[key] = cf.value or Decimal("0")

        top_slice = None
        max_delta = Decimal("-Infinity")

        for sf in current_facts:
            if sf.dimension_values and sf.calculation_type in ("TOTAL", "AVERAGE", "CONTRIBUTION"):
                # Retain the base slice fact in facts for table rendering
                facts.append(sf)
                key = tuple(sorted(sf.dimension_values.items()))
                base_val = comp_slices_by_dim.get(key, Decimal("0"))
                curr_val = sf.value or Decimal("0")
                slice_delta = curr_val - base_val

                contrib_pct = None
                if total_growth_delta != Decimal("0"):
                    contrib_pct = ((slice_delta / total_growth_delta) * Decimal("100")).quantize(Decimal("0.01"))

                facts.append(
                    ProtectedFact(
                        fact_id=f"fact_growth_delta_{sf.fact_id}",
                        metric=sf.metric,
                        calculation_type="SLICE_GROWTH_DELTA",
                        value=slice_delta,
                        unit=sf.unit,
                        period=sf.period,
                        comparison_period=comp_total.period,
                        dimension_values=sf.dimension_values,
                        semantic_release=pin.release_id,
                        lineage=sf.lineage,
                        completeness_status="COMPLETE",
                    )
                )

                if contrib_pct is not None:
                    facts.append(
                        ProtectedFact(
                            fact_id=f"fact_growth_contrib_{sf.fact_id}",
                            metric=sf.metric,
                            calculation_type="GROWTH_CONTRIBUTION_PERCENT",
                            value=contrib_pct,
                            unit="%",
                            period=sf.period,
                            comparison_period=comp_total.period,
                            dimension_values=sf.dimension_values,
                            semantic_release=pin.release_id,
                            lineage=sf.lineage,
                            completeness_status="COMPLETE",
                        )
                    )

                if slice_delta > max_delta:
                    max_delta = slice_delta
                    top_slice = (sf, slice_delta, contrib_pct)

        if top_slice is not None:
            ts_fact, ts_delta, ts_pct = top_slice
            facts.append(
                ProtectedFact(
                    fact_id="fact_top_growth_contributor",
                    metric=ts_fact.metric,
                    calculation_type="TOP_GROWTH_CONTRIBUTOR",
                    value=ts_delta,
                    unit=ts_fact.unit,
                    period=ts_fact.period,
                    comparison_period=comp_total.period,
                    dimension_values=ts_fact.dimension_values,
                    semantic_release=pin.release_id,
                    lineage=ts_fact.lineage,
                    completeness_status="COMPLETE",
                    assumptions=[f"Contributed {ts_pct}% of total period-over-period growth."],
                )
            )

        return facts

    def _to_decimal(self, val: Any) -> Decimal:
        """Safely convert any numeric value to exact Decimal, preventing IEEE-754 drift."""
        if val is None:
            return Decimal("0")
        if isinstance(val, Decimal):
            return val
        if isinstance(val, float):
            # Normalize float representation to eliminate trailing binary artifact digits
            return Decimal(str(val))
        try:
            return Decimal(str(val))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Invalid numeric source value") from exc

    def _extract_time_series(self, plan: ResolvedSemanticPlan, rows: list[dict[str, Any]]) -> list[tuple[str, Decimal]]:
        """Extract time-ordered (period, value) observations from aggregate rows."""
        series: list[tuple[str, Decimal]] = []
        period_col = plan.period_column or "period"
        for r in rows:
            p_val = r.get(period_col) or r.get(f"{plan.period_table}.{period_col}")
            if p_val is None and plan.dimensions:
                for dim in plan.dimensions:
                    p_val = r.get(f"{dim.table}.{dim.column}", r.get(dim.column))
                    if p_val is not None:
                        break
            if p_val is None:
                for k, v in r.items():
                    if any(term in k.lower() for term in ("period", "year", "date", "month", "quarter")):
                        p_val = v
                        break
            raw_metric = r.get("metric_value", r.get("value"))
            metric_val = self._to_decimal(raw_metric)
            if p_val is not None:
                series.append((str(p_val), metric_val))
        return series

    def _analyze_forecast(
        self,
        plan: ResolvedSemanticPlan,
        rows: list[dict[str, Any]],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Execute deterministic forecast on historical series."""
        from backend.science.forecast import GovernedForecastEngine
        lineage = [f"{t.schema_name}.{t.table_name}" for t in plan.required_tables]
        series = self._extract_time_series(plan, rows)
        if not series:
            raise ValueError("No historical observations available for forecast")

        # Sort chronologically by period
        series.sort(key=lambda x: x[0])
        horizon = plan.horizon or 1
        engine = GovernedForecastEngine()
        fc_res = engine.forecast(
            metric=plan.measure.name,
            unit=plan.units,
            historical_series=series,
            horizon=horizon,
            policy=plan.time_series_policy,
            turn_id=plan.turn_id,
        )

        facts: list[ProtectedFact] = []
        for idx, (p, val) in enumerate(series):
            facts.append(
                ProtectedFact(
                    fact_id=f"fact_hist_{plan.turn_id}_{idx+1:03d}",
                    metric=plan.measure.name,
                    calculation_type="HISTORICAL",
                    value=val,
                    unit=plan.units,
                    period=p,
                    semantic_release=pin.release_id,
                    lineage=lineage,
                    completeness_status="COMPLETE",
                    method="HISTORICAL_OBSERVATION",
                    method_version="1.0.0",
                    result_type="OBSERVED",
                    assumptions=list(plan.assumptions),
                )
            )

        for f in fc_res.facts:
            facts.append(
                replace(
                    f,
                    semantic_release=pin.release_id,
                    lineage=lineage,
                )
            )
        return facts

    def _analyze_anomaly(
        self,
        plan: ResolvedSemanticPlan,
        rows: list[dict[str, Any]],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Execute deterministic MAD anomaly detection on time series."""
        from backend.science.anomaly import GovernedAnomalyDetector
        lineage = [f"{t.schema_name}.{t.table_name}" for t in plan.required_tables]
        series = self._extract_time_series(plan, rows)
        if not series:
            raise ValueError("No historical observations available for anomaly detection")

        series.sort(key=lambda x: x[0])
        detector = GovernedAnomalyDetector()
        anom_facts = detector.detect_anomalies(
            metric=plan.measure.name,
            unit=plan.units,
            series=series,
            policy=plan.time_series_policy,
            turn_id=plan.turn_id,
        )
        return [
            replace(af, semantic_release=pin.release_id, lineage=lineage)
            for af in anom_facts
        ]

    def _analyze_scenario(
        self,
        plan: ResolvedSemanticPlan,
        rows: list[dict[str, Any]],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Execute declarative scenario analysis against approved catalog model."""
        from backend.science.scenario import GovernedScenarioEngine, ScenarioExecutionError
        if not plan.scenario_model_def:
            raise ScenarioExecutionError(f"No approved scenario model defined for plan '{plan.plan_id}'.")
        model = plan.scenario_model_def
        lineage = [f"{t.schema_name}.{t.table_name}" for t in plan.required_tables]
        engine = GovernedScenarioEngine()

        baseline_drivers: dict[str, Decimal] = {}
        for d_name, d_def in model.drivers.items():
            if rows and d_name in rows[0]:
                baseline_drivers[d_name] = self._to_decimal(rows[0][d_name])
            elif d_def.default_value is not None:
                baseline_drivers[d_name] = d_def.default_value
            else:
                raise ValueError(f"Missing approved baseline for driver {d_name}")

        adj_decimals = {k: Decimal(str(v)) for k, v in plan.adjustments.items()}
        scen_facts = engine.evaluate_scenario(
            model=model,
            baseline_drivers=baseline_drivers,
            adjustments=adj_decimals,
            turn_id=plan.turn_id,
        )
        return [
            replace(sf, semantic_release=pin.release_id, lineage=lineage, unit=plan.units)
            for sf in scen_facts
        ]

    def _analyze_goal_seek(
        self,
        plan: ResolvedSemanticPlan,
        rows: list[dict[str, Any]],
        pin: SemanticSnapshotPin,
    ) -> list[ProtectedFact]:
        """Execute deterministic goal-seek root finding on approved scenario model."""
        from backend.science.goal_seek import GovernedGoalSeekEngine, GoalSeekError
        if not plan.scenario_model_def:
            raise GoalSeekError(f"No approved scenario model defined for goal seek plan '{plan.plan_id}'.")
        model = plan.scenario_model_def
        lineage = [f"{t.schema_name}.{t.table_name}" for t in plan.required_tables]
        engine = GovernedGoalSeekEngine()

        baseline_drivers: dict[str, Decimal] = {}
        for d_name, d_def in model.drivers.items():
            if rows and d_name in rows[0]:
                baseline_drivers[d_name] = self._to_decimal(rows[0][d_name])
            elif d_def.default_value is not None:
                baseline_drivers[d_name] = d_def.default_value
            else:
                raise ValueError(f"Missing approved baseline for driver {d_name}")

        target_metric = plan.target_metric or model.output_metric
        target_val = Decimal(str(plan.target_value)) if plan.target_value is not None else Decimal("0")
        adj_driver = plan.adjustable_driver or list(model.drivers.keys())[0]

        gs_res = engine.solve(
            model=model,
            target_metric=target_metric,
            target_value=target_val,
            adjustable_driver=adj_driver,
            baseline_drivers=baseline_drivers,
            custom_bounds=plan.bounds,
            turn_id=plan.turn_id,
        )
        return [
            replace(f, semantic_release=pin.release_id, lineage=lineage, unit=plan.units)
            for f in gs_res.facts
        ]
