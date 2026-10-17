"""Relative date and calendar boundary resolver with timezone awareness and fiscal calendars."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
import calendar


@dataclass(frozen=True)
class ResolvedPeriodRange:
    """Resolved calendar period with strict date and timezone boundaries."""
    period_code: str
    start_datetime: datetime
    end_datetime: datetime
    timezone_name: str
    description: str


class CalendarBoundaryResolver:
    """Resolves relative date expressions ('last month', 'this year', fiscal periods) to calendar boundaries."""

    @staticmethod
    def _get_local_datetime(anchor_datetime: Optional[datetime], tz_name: str) -> tuple[datetime, ZoneInfo]:
        """Convert anchor datetime into local timezone-aware datetime."""
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")

        ref = anchor_datetime or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        local_ref = ref.astimezone(tz)
        return local_ref, tz

    @staticmethod
    def resolve_last_month(
        anchor_datetime: Optional[datetime] = None,
        tz_name: str = "UTC",
    ) -> ResolvedPeriodRange:
        """Resolve 'last month' relative to anchor datetime and timezone."""
        local_ref, tz = CalendarBoundaryResolver._get_local_datetime(anchor_datetime, tz_name)

        # Calculate year and month of last month in local timezone
        if local_ref.month == 1:
            target_year = local_ref.year - 1
            target_month = 12
        else:
            target_year = local_ref.year
            target_month = local_ref.month - 1

        _, last_day = calendar.monthrange(target_year, target_month)

        start_dt = datetime(target_year, target_month, 1, 0, 0, 0, tzinfo=tz)
        end_dt = datetime(target_year, target_month, last_day, 23, 59, 59, 999999, tzinfo=tz)

        month_name = calendar.month_name[target_month]
        period_code = f"{target_year}-{target_month:02d}"

        return ResolvedPeriodRange(
            period_code=period_code,
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone_name=tz.key,
            description=f"{month_name} {target_year} ({start_dt.date()} to {end_dt.date()} in {tz.key})",
        )

    @staticmethod
    def resolve_fiscal_quarter(
        anchor_datetime: Optional[datetime] = None,
        tz_name: str = "UTC",
        fiscal_year_start_month: int = 1,
        relative_offset: int = 0,  # 0 for current fiscal quarter, -1 for last fiscal quarter
    ) -> ResolvedPeriodRange:
        """Resolve fiscal quarter with support for non-January fiscal starts (e.g. April 1)."""
        local_ref, tz = CalendarBoundaryResolver._get_local_datetime(anchor_datetime, tz_name)

        # Compute months since fiscal start
        month_offset = (local_ref.month - fiscal_year_start_month) % 12
        current_q = (month_offset // 3) + 1  # 1, 2, 3, or 4

        # Compute fiscal year number
        if local_ref.month < fiscal_year_start_month:
            fy_year = local_ref.year
        else:
            fy_year = local_ref.year if fiscal_year_start_month == 1 else local_ref.year + 1

        # Apply relative quarter offset
        target_q_idx = (current_q - 1) + relative_offset
        fy_year_adjusted = fy_year + (target_q_idx // 4)
        target_q = (target_q_idx % 4) + 1

        # Calculate calendar start and end months for target fiscal quarter
        q_start_month_offset = (target_q - 1) * 3
        cal_start_month = ((fiscal_year_start_month - 1 + q_start_month_offset) % 12) + 1
        cal_end_month = ((cal_start_month - 1 + 2) % 12) + 1

        # Determine calendar year for start and end months
        if fiscal_year_start_month == 1:
            cal_start_year = fy_year_adjusted
            cal_end_year = fy_year_adjusted
        else:
            base_cal_year = fy_year_adjusted - 1
            cal_start_year = base_cal_year if cal_start_month >= fiscal_year_start_month else fy_year_adjusted
            cal_end_year = base_cal_year if cal_end_month >= fiscal_year_start_month else fy_year_adjusted

        _, last_day = calendar.monthrange(cal_end_year, cal_end_month)

        start_dt = datetime(cal_start_year, cal_start_month, 1, 0, 0, 0, tzinfo=tz)
        end_dt = datetime(cal_end_year, cal_end_month, last_day, 23, 59, 59, 999999, tzinfo=tz)

        period_code = f"FY{fy_year_adjusted}-Q{target_q}"

        return ResolvedPeriodRange(
            period_code=period_code,
            start_datetime=start_dt,
            end_datetime=end_dt,
            timezone_name=tz.key,
            description=f"Fiscal Quarter {target_q} FY{fy_year_adjusted} ({start_dt.date()} to {end_dt.date()} in {tz.key})",
        )
