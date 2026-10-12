"""Comprehensive tests for real timestamp boundaries, timezones, fiscal calendars, and year transitions.

Covers:
1. Capture of one immutable reference timestamp per turn
2. Catalog timezone awareness changing local month resolution
3. Strict local month boundaries (00:00:00 to 23:59:59.999999)
4. Calendar year transitions (January anchor resolving to December of previous year)
5. Fiscal calendar boundaries with non-January start month (e.g. April 1) and fiscal quarter transitions
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from talk2data.domain.models import Turn, TurnStatus
from talk2data.execution.calendar import CalendarBoundaryResolver


def test_turn_captures_one_reference_timestamp_per_turn(test_setup):
    """Verify each turn captures exactly one reference timestamp at creation."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    ref_time = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    turn = Turn.create(
        conversation_id="conv_ref_ts_1",
        actor=actor_a,
        question="What was total sales?",
        source_id=source_id,
        reference_datetime=ref_time,
    )

    assert turn.reference_timestamp == ref_time
    # Verify immutability across state changes
    turn.transition_to(TurnStatus.ANALYZING)
    assert turn.reference_timestamp == ref_time


def test_catalog_timezone_alters_month_boundary_resolution():
    """Timestamp 2024-01-01 02:00:00 UTC is January in UTC, but December 31 in New York (EST).
    
    Therefore:
    - In UTC: Last month is December 2023 (2023-12).
    - In America/New_York: Current month is December 2023, so last month is November 2023 (2023-11).
    """
    ts_boundary = datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)

    # In UTC
    res_utc = CalendarBoundaryResolver.resolve_last_month(ts_boundary, tz_name="UTC")
    assert res_utc.period_code == "2023-12"
    assert res_utc.start_datetime.year == 2023
    assert res_utc.start_datetime.month == 12

    # In America/New_York (UTC-5)
    res_ny = CalendarBoundaryResolver.resolve_last_month(ts_boundary, tz_name="America/New_York")
    assert res_ny.period_code == "2023-11"
    assert res_ny.start_datetime.year == 2023
    assert res_ny.start_datetime.month == 11
    assert res_ny.start_datetime.tzinfo == ZoneInfo("America/New_York")


def test_calendar_year_transition_and_microsecond_boundaries():
    """Anchor on 2024-01-15 in UTC resolves across the year boundary to 2023-12.
    
    Verifies exact microsecond boundary: 2023-12-01 00:00:00 to 2023-12-31 23:59:59.999999.
    """
    anchor_jan = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
    res = CalendarBoundaryResolver.resolve_last_month(anchor_jan, tz_name="UTC")

    assert res.period_code == "2023-12"
    assert res.start_datetime == datetime(2023, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert res.end_datetime == datetime(2023, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_fiscal_calendar_quarter_resolution_and_year_transition():
    """Verify fiscal calendar with April start month (UK/India standard).
    
    Fiscal Year starts April 1:
    - FY Q1: April - June
    - FY Q2: July - September
    - FY Q3: October - December
    - FY Q4: January - March
    
    Anchor: 2024-05-15 (May 2024 -> FY2025 Q1).
    Relative offset -1 ('last fiscal quarter') -> FY2024 Q4 (January 1 to March 31, 2024).
    """
    anchor_may = datetime(2024, 5, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Current fiscal quarter (offset 0)
    res_curr_fq = CalendarBoundaryResolver.resolve_fiscal_quarter(
        anchor_may, tz_name="UTC", fiscal_year_start_month=4, relative_offset=0
    )
    assert res_curr_fq.period_code == "FY2025-Q1"
    assert res_curr_fq.start_datetime == datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert res_curr_fq.end_datetime == datetime(2024, 6, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # Last fiscal quarter (offset -1) -> Q4 of previous fiscal year
    res_last_fq = CalendarBoundaryResolver.resolve_fiscal_quarter(
        anchor_may, tz_name="UTC", fiscal_year_start_month=4, relative_offset=-1
    )
    assert res_last_fq.period_code == "FY2024-Q4"
    assert res_last_fq.start_datetime == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert res_last_fq.end_datetime == datetime(2024, 3, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
