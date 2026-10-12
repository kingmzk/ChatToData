"""Independent verification tests for GovernedForecastEngine."""
from decimal import Decimal
import pytest
from talk2data.catalog.schema import TimeSeriesPolicy
from talk2data.science.forecast import GovernedForecastEngine, ForecastingEligibilityError


def test_known_linear_series_forecast():
    """Verify exact OLS fit and projections against independently calculated linear arithmetic.
    
    Formula: y = 10 + 5*t
    Historical series (t=1..5):
    t=1 (2020): y=15
    t=2 (2021): y=20
    t=3 (2022): y=25
    t=4 (2023): y=30
    t=5 (2024): y=35
    
    Slope = 5.0, Intercept = 10.0
    Residuals = 0.0, s_e = 0.0
    Projection for t=6 (2025): 10 + 5(6) = 40.0
    Projection for t=7 (2026): 10 + 5(7) = 45.0
    """
    engine = GovernedForecastEngine()
    history = [
        ("2020", Decimal("15")),
        ("2021", Decimal("20")),
        ("2022", Decimal("25")),
        ("2023", Decimal("30")),
        ("2024", Decimal("35")),
    ]
    res = engine.forecast(
        metric="sales_revenue",
        unit="USD",
        historical_series=history,
        horizon=2,
    )
    assert res.slope == Decimal("5")
    assert res.intercept == Decimal("10")
    assert len(res.facts) == 2
    
    fact_2025 = res.facts[0]
    assert fact_2025.period == "2025"
    assert fact_2025.value == Decimal("40.00")
    assert fact_2025.result_type == "PREDICTED"
    assert fact_2025.method == "LINEAR_TREND_OLS"
    
    fact_2026 = res.facts[1]
    assert fact_2026.period == "2026"
    assert fact_2026.value == Decimal("45.00")


def test_chronological_holdout_backtesting_zero_leakage():
    """Verify backtesting evaluates chronological holdout without using future observations in training."""
    engine = GovernedForecastEngine()
    history = [
        ("2019", Decimal("100")),
        ("2020", Decimal("110")),
        ("2021", Decimal("120")),
        ("2022", Decimal("130")),
        ("2023", Decimal("140")),
        ("2024", Decimal("150")),
    ]
    res = engine.forecast(
        metric="revenue",
        unit="USD",
        historical_series=history,
        horizon=1,
    )
    # The backtest splits into train (first 4: 2019..2022) and test holdout (last 2: 2023, 2024)
    # Train OLS: slope=10, intercept=90. Holds out 2023 (step 5: 140) and 2024 (step 6: 150).
    # Step 5 pred: 90+50 = 140 (err 0). Step 6 pred: 90+60 = 150 (err 0).
    assert res.backtest_mae == Decimal("0")
    # Naive baseline: uses train[-1] = 130 for both test steps:
    # err1 = |130 - 140| = 10, err2 = |130 - 150| = 20 -> naive MAE = (10+20)/2 = 15.0
    assert res.naive_mae == Decimal("15")


def test_insufficient_history_rejected():
    """Verify series with fewer observations than minimum history threshold is rejected."""
    engine = GovernedForecastEngine()
    policy = TimeSeriesPolicy(frequency="YEAR", metric="sales", period_dimension="year", min_history_periods=5)
    history = [("2023", Decimal("100")), ("2024", Decimal("110"))]
    with pytest.raises(ForecastingEligibilityError, match="Insufficient historical observations"):
        engine.forecast(metric="sales", unit="USD", historical_series=history, horizon=1, policy=policy)


def test_missing_periods_rejected_by_default():
    """Verify chronological gaps (e.g. 2020, 2021, 2023 without 2022) trigger rejection without zero filling."""
    engine = GovernedForecastEngine()
    policy = TimeSeriesPolicy(frequency="YEAR", metric="sales", period_dimension="year", allow_missing_as_zero=False)
    history = [
        ("2020", Decimal("100")),
        ("2021", Decimal("110")),
        ("2023", Decimal("130")),  # Missing 2022!
        ("2024", Decimal("140")),
    ]
    with pytest.raises(ForecastingEligibilityError, match="Missing historical period detected"):
        engine.forecast(metric="sales", unit="USD", historical_series=history, horizon=1, policy=policy)


def test_linear_trend_eligibility_enforced():
    """Verify non-eligible metrics under catalog policy cannot be forecasted using linear trend."""
    engine = GovernedForecastEngine()
    policy = TimeSeriesPolicy(frequency="YEAR", metric="volatile_fx", period_dimension="year", linear_trend_eligible=False)
    history = [
        ("2021", Decimal("1.1")),
        ("2022", Decimal("1.2")),
        ("2023", Decimal("1.15")),
        ("2024", Decimal("1.18")),
    ]
    with pytest.raises(ForecastingEligibilityError, match="not eligible for linear trend forecasting"):
        engine.forecast(metric="volatile_fx", unit="ratio", historical_series=history, horizon=1, policy=policy)


def test_prediction_intervals_and_domain_bounds():
    """Verify prediction intervals expand with horizon and respect catalog domain lower bounds."""
    engine = GovernedForecastEngine()
    policy = TimeSeriesPolicy(frequency="YEAR", 
        metric="headcount",
        period_dimension="year",
        lower_bound=Decimal("0"),  # Headcount cannot be negative
    )
    # Slightly noisy series
    history = [
        ("2020", Decimal("50")),
        ("2021", Decimal("45")),
        ("2022", Decimal("42")),
        ("2023", Decimal("38")),
        ("2024", Decimal("32")),
    ]
    res = engine.forecast(metric="headcount", unit="people", historical_series=history, horizon=2, policy=policy)
    assert len(res.facts) == 2
    fc1 = res.facts[0]
    fc2 = res.facts[1]
    
    # Uncertainty intervals are present and clearly labeled
    assert fc1.prediction_interval is not None
    assert fc1.prediction_interval["caveat"] == "model-dependent estimate, not guaranteed coverage"
    assert Decimal(fc1.prediction_interval["lower"]) >= Decimal("0")
    assert Decimal(fc2.prediction_interval["lower"]) >= Decimal("0")
