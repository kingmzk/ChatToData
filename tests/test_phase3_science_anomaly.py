"""Independent verification tests for GovernedAnomalyDetector."""
from decimal import Decimal
import pytest
from talk2data.science.anomaly import GovernedAnomalyDetector, AnomalyDetectionError


def test_known_anomaly_fixture():
    """Verify MAD detection against a verified outlier fixture.
    
    Series: [10, 11, 10, 12, 10, 100, 10, 11, 10, 12]
    Sorted: [10, 10, 10, 10, 10, 11, 11, 12, 12, 100]
    Median: (10 + 11) / 2 = 10.5
    Deviations from 10.5: [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 89.5]
    MAD: 0.5
    The point 100 has diff 89.5. Score = 0.6745 * 89.5 / 0.5 = 120.735 >> 3.0 (SPIKE).
    Points near 10-12 have diff <= 1.5. Max score = 0.6745 * 1.5 / 0.5 = 2.0235 < 3.0 (NORMAL).
    """
    detector = GovernedAnomalyDetector(min_observations=5, threshold=3.0)
    series = [
        ("2024-01", Decimal("10")),
        ("2024-02", Decimal("11")),
        ("2024-03", Decimal("10")),
        ("2024-04", Decimal("12")),
        ("2024-05", Decimal("10")),
        ("2024-06", Decimal("100")),  # Anomaly!
        ("2024-07", Decimal("10")),
        ("2024-08", Decimal("11")),
        ("2024-09", Decimal("10")),
        ("2024-10", Decimal("12")),
    ]
    facts = detector.detect_anomalies(metric="daily_sales", unit="USD", series=series)
    assert len(facts) == 10
    
    anomalies = [f for f in facts if f.diagnostics["anomaly_detected"] == "true"]
    assert len(anomalies) == 1
    
    anom_fact = anomalies[0]
    assert anom_fact.period == "2024-06"
    assert anom_fact.value == Decimal("100")
    assert anom_fact.diagnostics["direction"] == "SPIKE"
    assert Decimal(anom_fact.diagnostics["anomaly_score"]) > Decimal("100")
    
    # Non-accusatory language verification
    for f in facts:
        assert any("Anomalies do not constitute proof of fraud" in lim for lim in f.limitations)


def test_constant_series_zero_mad():
    """Verify constant series yields zero score and zero false positive anomalies without zero division."""
    detector = GovernedAnomalyDetector(min_observations=5, threshold=3.0)
    series = [
        ("2024-01", Decimal("50")),
        ("2024-02", Decimal("50")),
        ("2024-03", Decimal("50")),
        ("2024-04", Decimal("50")),
        ("2024-05", Decimal("50")),
        ("2024-06", Decimal("50")),
    ]
    facts = detector.detect_anomalies(metric="server_uptime", unit="pct", series=series)
    assert len(facts) == 6
    for f in facts:
        assert f.diagnostics["anomaly_detected"] == "false"
        assert f.diagnostics["anomaly_score"] == "0.00"
        assert f.diagnostics["direction"] == "FLAT"


def test_zero_mad_with_deviating_tail():
    """Verify series where >50% values are identical (MAD=0) gracefully detects outlier without zero division."""
    detector = GovernedAnomalyDetector(min_observations=5, threshold=3.0)
    series = [
        ("2024-01", Decimal("10")),
        ("2024-02", Decimal("10")),
        ("2024-03", Decimal("10")),
        ("2024-04", Decimal("10")),
        ("2024-05", Decimal("10")),
        ("2024-06", Decimal("10")),
        ("2024-07", Decimal("10")),
        ("2024-08", Decimal("10")),
        ("2024-09", Decimal("50")),  # Deviation from flat baseline
    ]
    facts = detector.detect_anomalies(metric="error_count", unit="errors", series=series)
    anom = [f for f in facts if f.diagnostics["anomaly_detected"] == "true"]
    assert len(anom) == 1
    assert anom[0].period == "2024-09"
    assert anom[0].value == Decimal("50")


def test_insufficient_observations_raises_error():
    """Verify series shorter than min_observations threshold raises AnomalyDetectionError."""
    detector = GovernedAnomalyDetector(min_observations=6)
    series = [
        ("2024-01", Decimal("10")),
        ("2024-02", Decimal("12")),
        ("2024-03", Decimal("11")),
    ]
    with pytest.raises(AnomalyDetectionError, match="Insufficient observations"):
        detector.detect_anomalies(metric="traffic", unit="reqs", series=series)
