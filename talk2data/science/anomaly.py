"""Governed statistical anomaly detection engine using Median Absolute Deviation (MAD)."""
from __future__ import annotations
from decimal import Decimal
import statistics
from typing import Any, Optional

from talk2data.catalog.schema import TimeSeriesPolicy
from talk2data.domain.facts import ProtectedFact


class AnomalyDetectionError(Exception):
    """Raised when time series fails anomaly detection preconditions."""
    pass


class GovernedAnomalyDetector:
    """Executes robust, non-parametric MAD anomaly detection without causal claims or black-box code execution."""

    def __init__(
        self,
        min_observations: int = 5,
        threshold: float = 3.0,
        method_version: str = "1.0.0",
    ):
        self.min_observations = min_observations
        self.threshold = threshold
        self.method_version = method_version

    def detect_anomalies(
        self,
        metric: str,
        unit: str,
        series: list[tuple[str, Decimal]],  # [(period, value), ...]
        policy: Optional[TimeSeriesPolicy] = None,
        turn_id: str = "turn_anom",
    ) -> list[ProtectedFact]:
        """Detect statistical outliers using Median Absolute Deviation (MAD) across baseline window."""
        from talk2data.science.periods import normalize_series
        try:
            series = normalize_series(series, policy)
        except ValueError as exc:
            raise AnomalyDetectionError(str(exc)) from exc
        if len(series) < self.min_observations:
            raise AnomalyDetectionError(
                f"Insufficient observations for anomaly detection: Found {len(series)}, required minimum {self.min_observations}."
            )

        periods = [p for p, _ in series]
        values = [float(v) for _, v in series]
        n = len(values)

        # 1. Calculate Median
        median_val = statistics.median(values)

        # 2. Calculate Median Absolute Deviation (MAD)
        abs_deviations = [abs(x - median_val) for x in values]
        mad = statistics.median(abs_deviations)

        # Consistency scale factor for normal distribution approximation: 1.4826
        # Modified Z-score: 0.6745 * |x - median| / MAD
        facts: list[ProtectedFact] = []

        is_constant_series = all(x == values[0] for x in values)

        for idx, (period, raw_dec) in enumerate(series):
            x = float(raw_dec)
            diff = x - median_val

            if is_constant_series:
                # Constant series: zero variance, zero anomaly score
                score = 0.0
                is_anomalous = False
            elif mad > 1e-9:
                score = (0.6745 * abs(diff)) / mad
                is_anomalous = (score >= self.threshold)
            else:
                # Zero-MAD but non-constant series (e.g. >50% identical values with a few deviating outliers)
                mean_dev = statistics.mean(abs_deviations)
                if mean_dev > 1e-9:
                    score = (0.6745 * abs(diff)) / mean_dev
                    is_anomalous = (score >= self.threshold)
                else:
                    score = 0.0
                    is_anomalous = False

            direction = "SPIKE" if diff > 0 else ("DIP" if diff < 0 else "FLAT")

            fact = ProtectedFact(
                fact_id=f"fact_anom_{turn_id}_{idx+1}",
                metric=metric,
                calculation_type="ANOMALY",
                value=raw_dec,
                unit=unit,
                period=period,
                semantic_release="",
                is_suppressed=False,
                completeness_status="COMPLETE",
                assumptions=[
                    f"Assessed against retrospective baseline median of {median_val:,.2f} across {n} observations.",
                    f"MAD (Median Absolute Deviation): {mad:,.2f} with detection threshold M >= {self.threshold}.",
                ],
                limitations=[
                    "Statistical anomaly detection identifies numeric deviation from baseline only.",
                    "Anomalies do not constitute proof of fraud, operational defect, causality, or business failure.",
                ],
                method="MAD_MODIFIED_Z_SCORE",
                method_version=self.method_version,
                result_type="OBSERVED",
                diagnostics={
                    "anomaly_detected": str(is_anomalous).lower(),
                    "anomaly_score": str(round(Decimal(str(score)), 2)),
                    "direction": direction,
                    "expected_median": str(round(Decimal(str(median_val)), 2)),
                    "mad": str(round(Decimal(str(mad)), 2)),
                    "threshold": str(self.threshold),
                },
            )
            facts.append(fact)

        return facts
