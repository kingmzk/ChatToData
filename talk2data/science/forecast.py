"""Governed, deterministic time-series forecasting engine with chronological backtesting and uncertainty intervals."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Optional

from talk2data.catalog.schema import TimeSeriesPolicy
from talk2data.domain.facts import ProtectedFact


class ForecastingEligibilityError(Exception):
    """Raised when a time-series fails governance eligibility or validation gates."""
    pass


@dataclass(frozen=True)
class ForecastResult:
    """Output package of deterministic forecast run."""
    facts: list[ProtectedFact]
    slope: Decimal
    intercept: Decimal
    backtest_mae: Decimal
    naive_mae: Decimal
    training_start: str
    training_end: str


class GovernedForecastEngine:
    """Executes deterministic linear trend forecasting and chronological holdout backtesting strictly without LLM code execution."""

    def forecast(
        self,
        metric: str,
        unit: str,
        historical_series: list[tuple[str, Decimal]],  # [(period, value), ...]
        horizon: int,
        policy: Optional[TimeSeriesPolicy] = None,
        turn_id: str = "turn_fc",
    ) -> ForecastResult:
        """Run governed forecast validating history, frequency, linear trend eligibility, and uncertainty bounds."""
        if not historical_series:
            raise ForecastingEligibilityError("Time series history is empty. Cannot forecast.")

        from talk2data.science.periods import normalize_series
        try:
            historical_series = normalize_series(historical_series, policy)
        except ValueError as exc:
            raise ForecastingEligibilityError(str(exc)) from exc
        # 1. Eligibility Check
        if policy and not policy.linear_trend_eligible:
            raise ForecastingEligibilityError(
                f"Metric '{metric}' is not eligible for linear trend forecasting under catalog governance policy."
            )

        min_history = policy.min_history_periods if policy else 4
        max_horizon = policy.max_forecast_horizon if policy else 12

        if len(historical_series) < min_history:
            raise ForecastingEligibilityError(
                f"Insufficient historical observations: Found {len(historical_series)}, required minimum {min_history}."
            )

        if horizon < 1 or horizon > max_horizon:
            raise ForecastingEligibilityError(
                f"Invalid forecast horizon {horizon}: Must be between 1 and {max_horizon}."
            )

        # 2. Duplicate and Chronological Order Check
        periods = [p for p, _ in historical_series]
        if len(periods) != len(set(periods)):
            raise ForecastingEligibilityError("Duplicate periods detected in historical time series.")

        values = [Decimal(str(v)) for _, v in historical_series]
        n = len(values)

        # 3. Missing Periods Check (no automatic zero-fill unless permitted)
        # Check chronological continuity (e.g. 2021, 2022, 2024 -> missing 2023)
        self._check_period_continuity(periods, policy)

        # 4. Chronological Holdout Backtesting (Zero Future-Data Leakage)
        holdout_k = 1 if n <= 5 else 2
        train_vals = values[:-holdout_k]
        test_vals = values[-holdout_k:]

        # Train linear model on training split
        b1_train, b0_train = self._fit_ols(train_vals)
        train_n = len(train_vals)

        # Predict holdout
        holdout_errors = []
        for step in range(1, holdout_k + 1):
            pred_step = b0_train + b1_train * Decimal(train_n + step)
            actual_step = test_vals[step - 1]
            holdout_errors.append(abs(pred_step - actual_step))

        backtest_mae = sum(holdout_errors) / Decimal(holdout_k)

        # Naive Baseline (Last Observed Value from train split)
        naive_pred = train_vals[-1]
        naive_errors = [abs(naive_pred - actual_step) for actual_step in test_vals]
        naive_mae = sum(naive_errors) / Decimal(holdout_k)

        # 5. Full OLS Fit on All Historical Data
        b1_full, b0_full = self._fit_ols(values)

        # Calculate residual standard error
        residuals = [
            values[i] - (b0_full + b1_full * Decimal(i + 1))
            for i in range(n)
        ]
        sum_sq_resid = sum(r * r for r in residuals)
        dof = n - 2 if n > 2 else 1
        s_e = Decimal(str(math.sqrt(float(sum_sq_resid / Decimal(dof)))))

        t_mean = Decimal(n + 1) / Decimal(2)
        sum_t_diff_sq = sum(Decimal(i + 1 - float(t_mean)) ** 2 for i in range(n))
        if sum_t_diff_sq == 0:
            sum_t_diff_sq = Decimal(1)

        # 6. Generate Forecast Facts for Future Periods
        t_crit = Decimal("1.96")  # ~95% prediction interval multiplier
        facts: list[ProtectedFact] = []
        last_period = periods[-1]

        for h in range(1, horizon + 1):
            future_step = Decimal(n + h)
            point_forecast = b0_full + b1_full * future_step

            # Standard error of prediction (wider than confidence interval of the mean)
            dist_factor = (future_step - t_mean) ** 2 / sum_t_diff_sq
            se_pred = s_e * Decimal(str(math.sqrt(1.0 + (1.0 / n) + float(dist_factor))))

            lower_bound = point_forecast - (t_crit * se_pred)
            upper_bound = point_forecast + (t_crit * se_pred)

            # Apply catalog domain bounds if explicitly defined
            if policy:
                if policy.lower_bound is not None and lower_bound < policy.lower_bound:
                    lower_bound = policy.lower_bound
                if policy.upper_bound is not None and upper_bound > policy.upper_bound:
                    upper_bound = policy.upper_bound

            future_period_label = self._project_period(last_period, h, policy)

            fact = ProtectedFact(
                fact_id=f"fact_fc_{turn_id}_{h}",
                metric=metric,
                calculation_type="FORECAST",
                value=round(point_forecast, 2),
                unit=unit,
                period=future_period_label,
                semantic_release="",
                is_suppressed=False,
                completeness_status="COMPLETE",
                assumptions=[
                    f"Linear trend OLS model fitted over {n} chronological periods ({periods[0]} to {periods[-1]}).",
                    f"Holdout backtesting MAE: {backtest_mae:.2f} (Naive baseline MAE: {naive_mae:.2f}).",
                ],
                limitations=[
                    "Prediction intervals are model-dependent estimates reflecting historical variance, not guaranteed coverage.",
                    "Linear extrapolation assumes historical trend dynamics continue into future periods.",
                ],
                method="LINEAR_TREND_OLS",
                method_version="1.0.0",
                result_type="PREDICTED",
                prediction_interval={
                    "point": str(round(point_forecast, 2)),
                    "lower": str(round(lower_bound, 2)),
                    "upper": str(round(upper_bound, 2)),
                    "type": "prediction_interval",
                    "caveat": "model-dependent estimate, not guaranteed coverage",
                },
                diagnostics={
                    "slope": str(round(b1_full, 4)),
                    "intercept": str(round(b0_full, 4)),
                    "backtest_mae": str(round(backtest_mae, 2)),
                    "naive_mae": str(round(naive_mae, 2)),
                    "step": str(h),
                },
            )
            facts.append(fact)

        return ForecastResult(
            facts=facts,
            slope=b1_full,
            intercept=b0_full,
            backtest_mae=backtest_mae,
            naive_mae=naive_mae,
            training_start=periods[0],
            training_end=periods[-1],
        )

    def _fit_ols(self, y_vals: list[Decimal]) -> tuple[Decimal, Decimal]:
        """Fit ordinary least squares slope and intercept on 1-indexed time steps."""
        n = len(y_vals)
        t_vals = [Decimal(i + 1) for i in range(n)]
        t_bar = sum(t_vals) / Decimal(n)
        y_bar = sum(y_vals) / Decimal(n)

        num = sum((t_vals[i] - t_bar) * (y_vals[i] - y_bar) for i in range(n))
        den = sum((t_vals[i] - t_bar) ** 2 for i in range(n))

        if den == 0:
            return Decimal(0), y_bar

        slope = num / den
        intercept = y_bar - slope * t_bar
        return slope, intercept

    def _check_period_continuity(self, periods: list[str], policy: Optional[TimeSeriesPolicy]) -> None:
        """Validate chronological continuity; reject missing observations unless catalog explicitly permits."""
        from talk2data.science.periods import normalize_series
        normalize_series([(p, Decimal("0")) for p in periods], policy)

    def _project_period(self, last_period: str, step: int, policy=None) -> str:
        from talk2data.science.periods import period_index, period_label
        frequency, index = period_index(last_period)
        return period_label(frequency, index + step)
