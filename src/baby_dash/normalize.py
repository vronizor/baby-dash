from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from baby_dash.config import Rules
from baby_dash.timebase import dt_type, hours, lit_dt

FEED_KIND = {
    "bottle": "bottle",
    "left breast": "breast",
    "right breast": "breast",
    "both breasts": "breast",
    "parent fed": "other",
    "self fed": "other",
}


@dataclass(frozen=True)
class Timer:
    id: int
    name: str | None
    start: datetime
    is_sleep: bool


@dataclass(frozen=True)
class Normalized:
    feeds: pl.DataFrame
    sleeps: pl.DataFrame
    timers: list[Timer]
    duplicate_feeds: int
    duplicate_sleeps: int


def feeds_schema(rules: Rules) -> dict[str, pl.DataType]:
    return {
        "id": pl.Int64,
        "start": dt_type(rules),
        "end": dt_type(rules),
        "duration_h": pl.Float64,
        "method": pl.String,
        "type": pl.String,
        "kind": pl.String,
        "amount": pl.Float64,
        "amount_missing": pl.Boolean,
    }


def sleeps_schema(rules: Rules) -> dict[str, pl.DataType]:
    return {"id": pl.Int64, "start": dt_type(rules), "end": dt_type(rules), "duration_h": pl.Float64}


def _raw_frame(raw: list[dict[str, Any]], columns: dict[str, pl.DataType]) -> pl.DataFrame:
    rows = [{k: r.get(k) for k in columns} for r in raw]
    return pl.DataFrame(rows, schema=columns, strict=False)


def _parse_times(frame: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    return frame.with_columns(
        pl.col(c).str.to_datetime(time_zone="UTC", time_unit="us").dt.convert_time_zone(rules.tz.key)
        for c in ("start", "end")
    ).with_columns(duration_h=hours(pl.col("end") - pl.col("start")))


def _scope(frame: pl.DataFrame, rules: Rules, child_id: int | None, since: datetime | None) -> pl.DataFrame:
    if child_id is not None:
        frame = frame.filter(pl.col("child") == child_id)
    frame = frame.filter(pl.col("start").is_not_null() & pl.col("end").is_not_null())
    frame = _parse_times(frame, rules)
    if since is not None:
        frame = frame.filter(pl.col("end") >= lit_dt(since, rules))
    return frame


def normalize_feedings(
    raw: list[dict[str, Any]], rules: Rules, child_id: int | None = None, since: datetime | None = None
) -> tuple[pl.DataFrame, int]:
    frame = _raw_frame(
        raw,
        {"id": pl.Int64, "child": pl.Int64, "start": pl.String, "end": pl.String,
         "method": pl.String, "type": pl.String, "amount": pl.Float64},
    )
    frame = _scope(frame, rules, child_id, since)
    before = frame.height
    frame = frame.unique(subset=["start", "end", "method", "type", "amount"], keep="first", maintain_order=True)
    frame = frame.with_columns(
        kind=pl.col("method").replace_strict(FEED_KIND, default="other", return_dtype=pl.String)
    ).with_columns(amount_missing=(pl.col("kind") == "bottle") & pl.col("amount").is_null())
    frame = frame.select(feeds_schema(rules).keys()).cast(feeds_schema(rules)).sort("start")
    return frame, before - frame.height


def normalize_sleeps(
    raw: list[dict[str, Any]], rules: Rules, child_id: int | None = None, since: datetime | None = None
) -> tuple[pl.DataFrame, int]:
    frame = _raw_frame(raw, {"id": pl.Int64, "child": pl.Int64, "start": pl.String, "end": pl.String})
    frame = _scope(frame, rules, child_id, since)
    before = frame.height
    frame = frame.unique(subset=["start", "end"], keep="first", maintain_order=True)
    frame = frame.select(sleeps_schema(rules).keys()).cast(sleeps_schema(rules)).sort("start")
    return frame, before - frame.height


def normalize_timers(
    raw: list[dict[str, Any]], rules: Rules, sleep_pattern: re.Pattern[str], child_id: int | None = None
) -> list[Timer]:
    timers = []
    for t in raw:
        if t.get("active") is False or t.get("end"):
            continue
        if child_id is not None and t.get("child") not in (child_id, None):
            continue
        if not t.get("start"):
            continue
        name = t.get("name") or None
        timers.append(
            Timer(
                id=int(t["id"]),
                name=name,
                start=datetime.fromisoformat(t["start"]).astimezone(rules.tz),
                is_sleep=bool(name and sleep_pattern.search(name)),
            )
        )
    return sorted(timers, key=lambda t: t.start)


def normalize(
    feedings: list[dict[str, Any]],
    sleeps: list[dict[str, Any]],
    timers: list[dict[str, Any]],
    rules: Rules,
    sleep_pattern: re.Pattern[str],
    child_id: int | None = None,
    since: datetime | None = None,
) -> Normalized:
    feeds, dup_feeds = normalize_feedings(feedings, rules, child_id, since)
    sleep_frame, dup_sleeps = normalize_sleeps(sleeps, rules, child_id, since)
    return Normalized(
        feeds=feeds,
        sleeps=sleep_frame,
        timers=normalize_timers(timers, rules, sleep_pattern, child_id),
        duplicate_feeds=dup_feeds,
        duplicate_sleeps=dup_sleeps,
    )
