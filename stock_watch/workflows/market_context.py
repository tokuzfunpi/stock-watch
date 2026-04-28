from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

DEFAULT_SCHEDULE_TARGET_TIMES = ("08:45", "14:00")
US_MARKET_TZ = ZoneInfo("America/New_York")


def history_market(ticker: str) -> str:
    normalized = str(ticker or "").strip().upper()
    if normalized.endswith(".TW") or normalized.endswith(".TWO") or normalized in {"^TWII", "TWII"}:
        return "tw"
    return "us"


def business_day_on_or_before(day: pd.Timestamp) -> pd.Timestamp:
    current = pd.Timestamp(day).normalize()
    while current.weekday() >= 5:
        current -= pd.Timedelta(days=1)
    return current


def previous_business_day(day: pd.Timestamp) -> pd.Timestamp:
    current = pd.Timestamp(day).normalize() - pd.Timedelta(days=1)
    while current.weekday() >= 5:
        current -= pd.Timedelta(days=1)
    return current


def required_history_end_date(
    ticker: str,
    *,
    now_local: datetime | None,
    local_tz: ZoneInfo,
    us_market_tz: ZoneInfo = US_MARKET_TZ,
) -> pd.Timestamp:
    current = now_local or datetime.now(local_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_tz)

    market = history_market(ticker)
    market_tz = local_tz if market == "tw" else us_market_tz
    close_minutes = 13 * 60 + 35 if market == "tw" else 16 * 60 + 5

    market_now = current.astimezone(market_tz)
    market_day = pd.Timestamp(market_now.date()).normalize()
    if market_now.weekday() >= 5:
        return business_day_on_or_before(market_day)

    if market_now.hour * 60 + market_now.minute >= close_minutes:
        return market_day

    return previous_business_day(market_day)


def runtime_trigger_label(event_name: str | None = None) -> str:
    raw_event_name = os.getenv("GITHUB_EVENT_NAME", "") if event_name is None else event_name
    normalized = str(raw_event_name or "").strip().lower()
    if normalized == "schedule":
        return "Scheduled"
    if normalized == "workflow_dispatch":
        return "Manual"
    if normalized:
        return normalized
    return "Local"


def nearest_schedule_delay_minutes(
    now_local: datetime,
    *,
    schedule_target_times: tuple[str, ...] = DEFAULT_SCHEDULE_TARGET_TIMES,
) -> int | None:
    candidates: list[int] = []
    for time_str in schedule_target_times:
        hour_str, minute_str = time_str.split(":")
        target = now_local.replace(
            hour=int(hour_str),
            minute=int(minute_str),
            second=0,
            microsecond=0,
        )
        delta_minutes = int((now_local - target).total_seconds() // 60)
        if delta_minutes >= 0:
            candidates.append(delta_minutes)
    return min(candidates) if candidates else None


def runtime_context_lines(
    *,
    now_local: datetime | None,
    local_tz: ZoneInfo,
    trigger: str | None = None,
    schedule_target_times: tuple[str, ...] = DEFAULT_SCHEDULE_TARGET_TIMES,
) -> list[str]:
    current = now_local or datetime.now(local_tz)
    trigger_label = runtime_trigger_label() if trigger is None else trigger
    lines = [
        f"台灣時間：{current.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if trigger_label == "Scheduled":
        delay_minutes = nearest_schedule_delay_minutes(current, schedule_target_times=schedule_target_times)
        if delay_minutes is None:
            lines.append("排程延遲：尚未到預定時段")
        elif delay_minutes <= 15:
            lines.append(f"排程延遲：{delay_minutes} 分鐘內，屬正常波動")
        else:
            lines.append(f"排程延遲：已延後約 {delay_minutes} 分鐘")

    return lines


def market_session_phase(*, now_local: datetime | None, local_tz: ZoneInfo) -> str:
    current = now_local or datetime.now(local_tz)
    minutes = current.hour * 60 + current.minute
    if minutes < 9 * 60:
        return "preopen"
    if minutes < (13 * 60 + 35):
        return "intraday"
    return "postclose"
