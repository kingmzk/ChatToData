"""Validated regular annual, quarterly and monthly observations."""
import re
from decimal import Decimal


def period_index(label):
    if re.fullmatch(r"\d{4}", label):
        return "YEAR", int(label)
    match = re.fullmatch(r"(\d{4})-Q([1-4])", label, re.I)
    if match:
        return "QUARTER", int(match[1]) * 4 + int(match[2]) - 1
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", label)
    if match:
        return "MONTH", int(match[1]) * 12 + int(match[2]) - 1
    raise ValueError("Unsupported time-period label: " + label)


def period_label(frequency, index):
    if frequency == "YEAR":
        return str(index)
    divisor = 4 if frequency == "QUARTER" else 12
    year, part = divmod(index, divisor)
    return f"{year}-Q{part+1}" if frequency == "QUARTER" else f"{year}-{part+1:02d}"


def normalize_series(series, policy=None):
    indexed = [(period_index(p), Decimal(str(v))) for p, v in series]
    if not indexed:
        raise ValueError("Time series is empty")
    frequency = indexed[0][0][0]
    if any(key[0] != frequency or not value.is_finite() for key, value in indexed):
        raise ValueError("Mixed frequency or non-finite observations")
    # Explicit frequency must agree with labels.
    if policy and policy.frequency != frequency:
        raise ValueError("Observation frequency differs from catalog policy")
    indices = [key[1] for key, _ in indexed]
    if indices != sorted(set(indices)):
        raise ValueError("Periods must be unique and chronologically ordered")
    values = {key[1]: value for key, value in indexed}
    result = []
    for index in range(indices[0], indices[-1] + 1):
        if index not in values and not (policy and policy.allow_missing_as_zero):
            raise ValueError("Missing historical period detected: " + period_label(frequency, index))
        result.append((period_label(frequency, index), values.get(index, Decimal("0"))))
    return result
