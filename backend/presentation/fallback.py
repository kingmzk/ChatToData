"""Deterministic presentation fallback and claim validation engine."""
from __future__ import annotations
from backend.observability import traced_stage
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional
from backend.domain.facts import ProtectedFact, SealedEvidencePackage


@dataclass(frozen=True)
class RenderedKpiCard:
    title: str
    value: str
    unit: Optional[str]
    period: Optional[str]
    delta: Optional[str] = None
    growth_percent: Optional[str] = None


@dataclass(frozen=True)
class RenderedAnswerView:
    summary_text: str
    kpi_cards: list[RenderedKpiCard]
    table_columns: list[str]
    table_rows: list[dict[str, Any]]
    assumptions: list[str]
    limitations: list[str]
    is_suppressed: bool
    evidence_digest: str


class DeterministicPresentationFallback:
    """Renders answers strictly from sealed evidence packages with zero raw row exposure."""

    @traced_stage('answer_presentation')
    def render(self, evidence: SealedEvidencePackage) -> RenderedAnswerView:
        """Deterministically render narrative, KPI cards, and data table from sealed facts."""
        facts = evidence.facts
        if not facts:
            return RenderedAnswerView(
                summary_text="No analytical data found.",
                kpi_cards=[],
                table_columns=[],
                table_rows=[],
                assumptions=[],
                limitations=[],
                is_suppressed=False,
                evidence_digest=evidence.package_digest,
            )

        kpi_cards: list[RenderedKpiCard] = []
        table_rows: list[dict[str, Any]] = []
        summary_sentences: list[str] = []
        all_assumptions: set[str] = set()
        all_limitations: set[str] = set()
        any_suppressed = any(f.is_suppressed for f in facts)

        # Collect assumptions & limitations
        for f in facts:
            all_assumptions.update(f.assumptions)
            all_limitations.update(f.limitations)
            if f.is_suppressed and f.suppression_reason:
                all_limitations.add(f.suppression_reason)

        # Build KPI cards for Total / Average / Delta
        total_fact = next((f for f in facts if f.calculation_type in ("TOTAL", "AVERAGE", "COMPARISON") and not f.dimension_values), None)
        delta_fact = next((f for f in facts if f.calculation_type == "DELTA"), None)
        growth_fact = next((f for f in facts if f.calculation_type == "GROWTH_PERCENT"), None)
        top_contrib_fact = next((f for f in facts if f.calculation_type == "TOP_CONTRIBUTOR"), None)

        if total_fact:
            val_str = "[SUPPRESSED]" if total_fact.is_suppressed else (
                f"{total_fact.value:,.2f}" if total_fact.value is not None else "0"
            )
            delta_str = None
            if delta_fact and not delta_fact.is_suppressed and delta_fact.value is not None:
                sign = "+" if delta_fact.value > 0 else ""
                delta_str = f"{sign}{delta_fact.value:,.2f}"
            
            growth_str = None
            if growth_fact and not growth_fact.is_suppressed and growth_fact.value is not None:
                sign = "+" if growth_fact.value > 0 else ""
                growth_str = f"{sign}{growth_fact.value}%"

            kpi_cards.append(
                RenderedKpiCard(
                    title=f"{total_fact.calculation_type.title()} {total_fact.metric}",
                    value=val_str,
                    unit=total_fact.unit,
                    period=total_fact.period,
                    delta=delta_str,
                    growth_percent=growth_str,
                )
            )

        top_growth_contrib = next((f for f in facts if f.calculation_type == "TOP_GROWTH_CONTRIBUTOR"), None)

        if any_suppressed:
            summary_sentences.append("Values for one or more requested dimensions or metrics have been suppressed to protect data privacy.")

        if top_growth_contrib and top_growth_contrib.dimension_values:
            top_dim_val = list(top_growth_contrib.dimension_values.values())[0]
            pct_str = ""
            if top_growth_contrib.assumptions:
                pct_str = f" ({top_growth_contrib.assumptions[0]})"
            summary_sentences.append(
                f"{top_dim_val} was the highest contributor to growth{pct_str}."
            )
        elif total_fact:
            comp_p_str = f" compared to {delta_fact.comparison_period}" if (delta_fact and delta_fact.comparison_period) else ""
            if total_fact.calculation_type == "COMPARISON" or (delta_str and growth_str):
                summary_sentences.append(
                    f"{total_fact.metric} for period {total_fact.period} was {val_str} {total_fact.unit or ''}{comp_p_str} (change: {delta_str}, growth: {growth_str})."
                )
            else:
                summary_sentences.append(
                    f"{total_fact.calculation_type.title()} {total_fact.metric} for period {total_fact.period or 'All'} was {val_str} {total_fact.unit or ''}."
                )

        if not total_fact and any(f.dimension_values for f in facts) and not any_suppressed:
            summary_sentences.append(f"{facts[0].metric} results are shown by category in the table; group values are not summed into an overall metric.")

        # Build Slices Table (grouped by slice dimension values)
        dim_slice_map: dict[tuple, dict[str, Any]] = {}
        for sf in facts:
            if sf.dimension_values:
                key = tuple(sorted(sf.dimension_values.items()))
                if key not in dim_slice_map:
                    dim_slice_map[key] = {}
                    for d, val in sf.dimension_values.items():
                        dim_slice_map[key][d] = val
                        dim_slice_map[key][d.lower()] = val
                    dim_slice_map[key]["Metric"] = sf.metric
                    dim_slice_map[key]["Unit"] = sf.unit or ""

                if sf.calculation_type in ("TOTAL", "AVERAGE", "CONTRIBUTION", "TREND"):
                    row_val = "[SUPPRESSED]" if sf.is_suppressed else (
                        f"{sf.value:,.2f}" if sf.value is not None else "0"
                    )
                    dim_slice_map[key]["Value"] = row_val
                elif sf.calculation_type == "COMPARISON":
                    row_val = "[SUPPRESSED]" if sf.is_suppressed else (f"{sf.value:,.2f}" if sf.value is not None else "N/A")
                    dim_slice_map[key][f"Value ({sf.period})"] = row_val
                elif sf.calculation_type in ("DELTA", "GROWTH_PERCENT"):
                    dim_slice_map[key]["Change" if sf.calculation_type == "DELTA" else "Growth %"] = "[SUPPRESSED]" if sf.is_suppressed else (str(sf.value) if sf.value is not None else "N/A")
                elif sf.calculation_type == "GROWTH_CONTRIBUTION_PERCENT":
                    dim_slice_map[key]["Growth Contribution %"] = f"{sf.value}%" if sf.value is not None else "N/A"
                elif sf.calculation_type == "CONTRIBUTION_PERCENT":
                    dim_slice_map[key]["Contribution %"] = f"{sf.value}%" if sf.value is not None else "N/A"

        table_rows = list(dim_slice_map.values())
        if not table_rows and facts:
            # Fallback table for non-sliced metrics, comparisons, and totals
            table_columns = ["Metric", "Calculation", "Period", "Value", "Unit"]
            for f in facts:
                if f.value is not None and f.calculation_type in ("TOTAL", "AVERAGE", "COMPARISON", "DELTA"):
                    val_str = "[SUPPRESSED]" if f.is_suppressed else f"{f.value:,.2f}"
                    table_rows.append({
                        "Metric": f.metric,
                        "Calculation": f.calculation_type,
                        "Period": f.period or f.comparison_period or "All",
                        "Value": val_str,
                        "Unit": f.unit or "",
                    })
        else:
            table_columns = list(table_rows[0].keys()) if table_rows else []

        # Phase 3 Decision Science Family Handling
        forecast_facts = [f for f in facts if f.calculation_type == "FORECAST"]
        anomaly_facts = [f for f in facts if f.calculation_type == "ANOMALY"]
        scenario_facts = [f for f in facts if f.calculation_type == "SCENARIO"]
        goal_seek_facts = [f for f in facts if f.calculation_type == "GOAL_SEEK"]

        if forecast_facts:
            summary_sentences = [s for s in summary_sentences if "suppressed" in s.lower()]
            table_rows = []
            table_columns = ["Period", "Type", "Metric", "Value", "Lower PI", "Upper PI", "Unit"]
            for f in facts:
                if f.calculation_type in ("HISTORICAL", "FORECAST"):
                    val_str = "[SUPPRESSED]" if f.is_suppressed else (f"{f.value:,.2f}" if f.value is not None else "0")
                    lower_pi = f.prediction_interval.get("lower", "N/A") if (f.prediction_interval and not f.is_suppressed) else "N/A"
                    upper_pi = f.prediction_interval.get("upper", "N/A") if (f.prediction_interval and not f.is_suppressed) else "N/A"
                    table_rows.append({
                        "Period": f.period or "",
                        "Type": f.result_type,
                        "Metric": f.metric,
                        "Value": val_str,
                        "Lower PI": lower_pi,
                        "Upper PI": upper_pi,
                        "Unit": f.unit or "",
                    })

            for f in forecast_facts:
                val_str = "[SUPPRESSED]" if f.is_suppressed else f"{f.value:,.2f}"
                delta_str = f"PI: [{f.prediction_interval.get('lower')}, {f.prediction_interval.get('upper')}]" if (f.prediction_interval and not f.is_suppressed) else None
                kpi_cards.append(
                    RenderedKpiCard(
                        title=f"Forecast {f.metric} ({f.period})",
                        value=val_str,
                        unit=f.unit,
                        period=f.period,
                        delta=delta_str,
                    )
                )
                if f.is_suppressed:
                    summary_sentences.append(f"Forecast for period {f.period} has been suppressed to prevent disclosure.")
                else:
                    pi_info = f" with prediction interval [{f.prediction_interval.get('lower')}, {f.prediction_interval.get('upper')}]" if f.prediction_interval else ""
                    summary_sentences.append(
                        f"Projected {f.metric} for period {f.period} is {f.value:,.2f} {f.unit}{pi_info}. Note: Prediction intervals are model-dependent estimates, not guaranteed coverage."
                    )

        elif anomaly_facts:
            summary_sentences = [s for s in summary_sentences if "suppressed" in s.lower()]
            table_rows = []
            table_columns = ["Period", "Metric", "Value", "Expected Median", "MAD Score", "Direction", "Anomaly Status", "Unit"]
            anom_detected_count = 0
            has_anom_suppressed = any(f.is_suppressed for f in anomaly_facts)
            base_median = anomaly_facts[0].diagnostics.get("expected_median", "N/A") if not has_anom_suppressed else "[SUPPRESSED]"
            for f in anomaly_facts:
                val_str = "[SUPPRESSED]" if f.is_suppressed else f"{f.value:,.2f}"
                is_anom = f.diagnostics.get("anomaly_detected") == "true"
                if is_anom:
                    anom_detected_count += 1
                table_rows.append({
                    "Period": f.period or "",
                    "Metric": f.metric,
                    "Value": val_str,
                    "Expected Median": f.diagnostics.get("expected_median", "N/A") if not f.is_suppressed else "[SUPPRESSED]",
                    "MAD Score": f.diagnostics.get("anomaly_score", "0") if not f.is_suppressed else "[SUPPRESSED]",
                    "Direction": f.diagnostics.get("direction", "FLAT"),
                    "Anomaly Status": "ANOMALOUS" if is_anom else "NORMAL",
                    "Unit": f.unit or "",
                })

            kpi_cards.append(
                RenderedKpiCard(
                    title=f"Anomalies Detected ({anomaly_facts[0].metric})",
                    value=f"{anom_detected_count} of {len(anomaly_facts)}" if not has_anom_suppressed else "[SUPPRESSED]",
                    unit="points",
                    period=f"{anomaly_facts[0].period} - {anomaly_facts[-1].period}",
                    delta=f"Baseline Median: {base_median}",
                )
            )
            if has_anom_suppressed:
                summary_sentences.append("Anomaly metrics and baseline values have been suppressed under data disclosure policy.")
            else:
                summary_sentences.append(
                    f"Evaluated {len(anomaly_facts)} observations against baseline median of {base_median} {anomaly_facts[0].unit}. Detected {anom_detected_count} statistical anomalies using Median Absolute Deviation (MAD). Note: Anomalies indicate statistical baseline deviation only, not proof of fraud or defect."
                )

        elif scenario_facts:
            summary_sentences = [s for s in summary_sentences if "suppressed" in s.lower()]
            sf = scenario_facts[0]
            val_str = "[SUPPRESSED]" if sf.is_suppressed else f"{sf.value:,.2f}"
            base_str = sf.diagnostics.get("baseline_output", "N/A") if not sf.is_suppressed else "[SUPPRESSED]"
            delta_str = sf.diagnostics.get("delta", "N/A") if not sf.is_suppressed else "[SUPPRESSED]"
            kpi_cards.append(
                RenderedKpiCard(
                    title=f"Scenario: {sf.metric}",
                    value=val_str,
                    unit=sf.unit,
                    period=sf.period,
                    delta=f"Baseline: {base_str} (Δ {delta_str})",
                )
            )
            table_columns = ["Scenario Model", "Output Metric", "Simulated Output", "Baseline Output", "Delta", "Unit"]
            table_rows = [{
                "Scenario Model": sf.diagnostics.get("model_name", "Approved Model"),
                "Output Metric": sf.metric,
                "Simulated Output": val_str,
                "Baseline Output": base_str,
                "Delta": delta_str,
                "Unit": sf.unit or "",
            }]
            summary_sentences.append(
                f"Hypothetical scenario simulation for {sf.metric} yields {val_str} {sf.unit} (baseline: {base_str}, delta: {delta_str}). Results represent simulated projections under stated driver assumptions, distinct from observed facts."
            )

        elif goal_seek_facts:
            summary_sentences = [s for s in summary_sentences if "suppressed" in s.lower()]
            gf = goal_seek_facts[0]
            status = gf.diagnostics.get("feasibility", "UNKNOWN")
            drv = gf.diagnostics.get("driver_name", "Driver")

            if gf.is_suppressed:
                solved = "[SUPPRESSED]"
                tgt = "[SUPPRESSED]"
                achieved = "[SUPPRESSED]"
                residual = "[SUPPRESSED]"
                kpi_cards.append(
                    RenderedKpiCard(
                        title=f"Goal Seek: {drv}",
                        value="[SUPPRESSED]",
                        unit=gf.unit,
                        period=None,
                        delta="Target: [SUPPRESSED] (Status: SUPPRESSED)",
                    )
                )
                table_columns = ["Target Metric", "Target Value", "Adjustable Driver", "Solved Driver Value", "Achieved Output", "Residual Error", "Feasibility Status"]
                table_rows = [{
                    "Target Metric": gf.metric,
                    "Target Value": "[SUPPRESSED]",
                    "Adjustable Driver": drv,
                    "Solved Driver Value": "[SUPPRESSED]",
                    "Achieved Output": "[SUPPRESSED]",
                    "Residual Error": "[SUPPRESSED]",
                    "Feasibility Status": "SUPPRESSED",
                }]
                summary_sentences.append("Goal seek outputs and parameters have been suppressed under data disclosure policy.")
            else:
                solved = gf.diagnostics.get("solved_driver_value") or gf.diagnostics.get("driver_solution", "N/A")
                tgt = gf.diagnostics.get("target_value", "N/A")
                achieved = f"{gf.value:,.2f}" if gf.value is not None else "N/A"
                residual = gf.diagnostics.get("residual_error", "0")
                kpi_cards.append(
                    RenderedKpiCard(
                        title=f"Goal Seek: {drv}",
                        value=str(solved) if solved != "N/A" else status,
                        unit=gf.unit,
                        period=None,
                        delta=f"Target: {tgt} (Status: {status})",
                    )
                )
                table_columns = ["Target Metric", "Target Value", "Adjustable Driver", "Solved Driver Value", "Achieved Output", "Residual Error", "Feasibility Status"]
                table_rows = [{
                    "Target Metric": gf.metric,
                    "Target Value": tgt,
                    "Adjustable Driver": drv,
                    "Solved Driver Value": solved,
                    "Achieved Output": achieved,
                    "Residual Error": residual,
                    "Feasibility Status": status,
                }]
                if status in ("FEASIBLE", "BOUNDARY_SOLUTION"):
                    summary_sentences.append(
                        f"Goal seek solved: Required driver '{drv}' value is {solved} to achieve target {tgt} {gf.unit} (achieved: {achieved}, residual: {residual})."
                    )
                elif status == "PROVEN_UNREACHABLE_TARGET":
                    summary_sentences.append(
                        f"Goal seek status: PROVEN UNREACHABLE. Target {tgt} {gf.unit} cannot be reached within approved driver bounds. Closest achievable boundary output is {achieved} (shortfall residual: {residual})."
                    )
                elif status == "UNBRACKETED_TARGET":
                    summary_sentences.append(
                        f"Goal seek status: UNBRACKETED TARGET. Target {tgt} {gf.unit} was not bracketed within evaluated sub-intervals. Closest sampled output is {achieved} (residual: {residual})."
                    )
                elif status == "DISCONTINUITY_DETECTED":
                    summary_sentences.append(
                        f"Goal seek status: DISCONTINUITY DETECTED. Solver could not complete continuous search due to mathematical singularity or domain error."
                    )
                else:
                    summary_sentences.append(
                        f"Goal seek status: {status}. Target {tgt} {gf.unit} could not be met exactly within approved driver bounds. Closest achievable output is {achieved} (residual: {residual})."
                    )

        summary_text = " ".join(summary_sentences) if summary_sentences else "Analysis completed successfully."

        return RenderedAnswerView(
            summary_text=summary_text,
            kpi_cards=kpi_cards,
            table_columns=table_columns,
            table_rows=table_rows,
            assumptions=sorted(list(all_assumptions)),
            limitations=sorted(list(all_limitations)),
            is_suppressed=any_suppressed,
            evidence_digest=evidence.package_digest,
        )
