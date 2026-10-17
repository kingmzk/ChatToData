"""Schema-validated chart generation strictly from protected facts with disclosure controls."""
from __future__ import annotations
from decimal import Decimal
from typing import Any
from backend.domain.facts import ProtectedFact


class ChartGenerator:
    """Generates schema-validated chart specifications (KPI, Bar, Line) enforcing privacy controls."""

    def generate_charts(self, facts: list[ProtectedFact]) -> list[dict[str, Any]]:
        """Produce schema-validated chart specifications from protected facts."""
        if not facts:
            return []

        charts: list[dict[str, Any]] = []
        unsuppressed_facts = [f for f in facts if not f.is_suppressed]

        # 1. Categorical Bar Chart (for slice breakdowns)
        slice_facts = [f for f in facts if f.dimension_values and f.calculation_type in ("TOTAL", "AVERAGE", "CONTRIBUTION", "COMPARISON", "TREND", "GROUPED")]
        if slice_facts:
            dim_key = list(slice_facts[0].dimension_values.keys())[0]
            labels = []
            values = []
            tooltips = []

            for f in slice_facts:
                dim_val = str(f.dimension_values.get(dim_key, "Unknown"))
                if f.calculation_type == "COMPARISON":
                    dim_val += f" ({f.period})"
                if f.is_suppressed:
                    labels.append(f"{dim_val} [SUPPRESSED]")
                    values.append(None)
                    tooltips.append(f"{dim_val}: [SUPPRESSED] ({f.suppression_reason or 'Minimum group threshold not met'})")
                elif f.value is None:
                    labels.append(dim_val)
                    values.append(None)
                    tooltips.append(f"{dim_val}: unavailable")
                else:
                    labels.append(dim_val)
                    values.append(float(f.value))
                    tooltips.append(f"{dim_val}: {f.value:,.2f} {f.unit}")

            charts.append({
                "type": "line" if all(f.calculation_type == "TREND" for f in slice_facts) else "bar",
                "title": f"{slice_facts[0].metric} by {dim_key.title()}",
                "dimension": dim_key,
                "unit": slice_facts[0].unit,
                "labels": labels,
                "values": values,
                "tooltips": tooltips,
                "has_suppression": any(f.is_suppressed for f in slice_facts),
            })

        # 2. Forecast Chart (Historical line + Projected forecast + Prediction Interval bands)
        forecast_facts = [f for f in facts if f.calculation_type == "FORECAST"]
        if forecast_facts:
            hist_facts = [f for f in facts if f.calculation_type == "HISTORICAL"]
            all_series = hist_facts + forecast_facts
            labels = [f.period or "" for f in all_series]
            observed_values = [float(f.value) if (f.calculation_type == "HISTORICAL" and not f.is_suppressed and f.value is not None) else None for f in all_series]
            forecast_values = [float(f.value) if (f.calculation_type == "FORECAST" and not f.is_suppressed and f.value is not None) else None for f in all_series]
            lower_bounds = [float(f.prediction_interval["lower"]) if (f.prediction_interval and not f.is_suppressed) else None for f in all_series]
            upper_bounds = [float(f.prediction_interval["upper"]) if (f.prediction_interval and not f.is_suppressed) else None for f in all_series]

            charts.append({
                "type": "forecast",
                "title": f"{forecast_facts[0].metric} Forecast & Prediction Intervals",
                "unit": forecast_facts[0].unit,
                "labels": labels,
                "observed_values": observed_values,
                "forecast_values": forecast_values,
                "lower_bounds": lower_bounds,
                "upper_bounds": upper_bounds,
                "has_suppression": any(f.is_suppressed for f in all_series),
            })

        # 3. Anomaly Detection Chart (Time-series with baseline median and anomaly markers)
        anomaly_facts = [f for f in facts if f.calculation_type == "ANOMALY"]
        if anomaly_facts:
            labels = [f.period or "" for f in anomaly_facts]
            values = [float(f.value) if not f.is_suppressed and f.value is not None else 0.0 for f in anomaly_facts]
            is_anomaly = [f.diagnostics.get("anomaly_detected") == "true" for f in anomaly_facts]
            base_med = float(anomaly_facts[0].diagnostics.get("expected_median", 0.0)) if not anomaly_facts[0].is_suppressed else 0.0

            charts.append({
                "type": "anomaly",
                "title": f"{anomaly_facts[0].metric} Anomaly Detection (MAD)",
                "unit": anomaly_facts[0].unit,
                "labels": labels,
                "values": values,
                "is_anomaly": is_anomaly,
                "baseline_median": base_med,
                "has_suppression": any(f.is_suppressed for f in anomaly_facts),
            })

        # 4. Scenario Comparison Chart (Baseline vs Simulated Output)
        scenario_facts = [f for f in facts if f.calculation_type == "SCENARIO"]
        if scenario_facts:
            sf = scenario_facts[0]
            try:
                base_val = float(sf.diagnostics.get("baseline_output", 0.0))
            except (ValueError, TypeError):
                base_val = 0.0
            sim_val = float(sf.value) if not sf.is_suppressed and sf.value is not None else 0.0

            charts.append({
                "type": "scenario_bar",
                "title": f"Scenario Simulation: {sf.metric}",
                "unit": sf.unit,
                "labels": ["Baseline Output", "Simulated Output"],
                "values": [base_val, sim_val],
                "has_suppression": sf.is_suppressed,
            })

        # 5. Time-Series Line Chart (if period is present and multiple periods exist, for general trends)
        period_facts = [f for f in facts if f.period and not f.dimension_values and f.calculation_type in ("TOTAL", "AVERAGE", "COMPARISON")]
        if len(period_facts) > 1:
            labels = [f.period for f in period_facts]
            values = [float(f.value) if not f.is_suppressed and f.value is not None else None for f in period_facts]
            tooltips = [
                f"{f.period}: {f.value:,.2f} {f.unit}" if not f.is_suppressed and f.value is not None else f"{f.period}: [SUPPRESSED OR UNAVAILABLE]"
                for f in period_facts
            ]
            charts.append({
                "type": "line",
                "title": f"{period_facts[0].metric} Over Time",
                "unit": period_facts[0].unit,
                "labels": labels,
                "values": values,
                "tooltips": tooltips,
                "has_suppression": any(f.is_suppressed for f in period_facts),
            })

        return charts
