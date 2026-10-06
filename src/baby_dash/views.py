from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from baby_dash import metrics
from baby_dash.config import Rules, Settings
from baby_dash.store import Snapshot
from baby_dash.timebase import (
    clock_label,
    day_label,
    day_label_expr,
    dt_type,
    hours,
    is_night,
    label_range,
    lit_dt,
    nights_for_rows,
    rows_frame,
)

GAP_SCATTER_DAYS = 14
ROLLING_NIGHTS = 5


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _iso(ts: datetime | None, rules: Rules) -> str | None:
    return None if ts is None else ts.astimezone(rules.tz).isoformat(timespec="seconds")


def _elapsed_h(start: datetime, now: datetime) -> float:
    return (now - start).total_seconds() / 3600


def _range(r: metrics.UsualRange, **extra: Any) -> dict[str, Any]:
    return {
        "median_h": _r(r.median_h), "p25_h": _r(r.p25_h), "p75_h": _r(r.p75_h),
        "n": r.n, "enough": r.enough, "excluded": r.excluded, **extra,
    }


def _short_label(d: date) -> str:
    return f"{d.strftime('%a')} {d.day}"


def meta(snapshot: Snapshot | None, settings: Settings, now: datetime, stale_since: datetime | None) -> dict[str, Any]:
    rules = settings.rules
    return {
        "now": _iso(now, rules),
        "stale_since": _iso(stale_since, rules),
        "stale_since_label": clock_label(stale_since, rules) if stale_since else None,
        "child": {"slug": snapshot.child.slug, "name": snapshot.child.name} if snapshot else None,
        "amount_unit": settings.amount_unit,
    }


def _current_state(snapshot: Snapshot, rules: Rules, now: datetime) -> dict[str, Any]:
    sleep_timers = [t for t in snapshot.data.timers if t.is_sleep and t.start <= now]
    if sleep_timers:
        t = sleep_timers[0]
        return {"kind": "asleep", "since": _iso(t.start, rules), "since_label": clock_label(t.start, rules),
                "elapsed_h": _r(_elapsed_h(t.start, now)), "source": "timer", "timer_name": t.name}
    sleeps = snapshot.derived.sleeps.filter(pl.col("start") <= lit_dt(now, rules))
    if sleeps.is_empty():
        return {"kind": "unknown", "since": None, "since_label": None, "elapsed_h": None, "source": None}
    last = sleeps.row(-1, named=True)
    if last["end"] > now:
        return {"kind": "asleep", "since": _iso(last["start"], rules), "since_label": clock_label(last["start"], rules),
                "elapsed_h": _r(_elapsed_h(last["start"], now)), "source": "sleep"}
    return {"kind": "awake", "since": _iso(last["end"], rules), "since_label": clock_label(last["end"], rules),
            "elapsed_h": _r(_elapsed_h(last["end"], now)), "source": "sleep"}


def links(settings: Settings, snapshot: Snapshot | None) -> dict[str, str]:
    base = settings.bb_public_url
    child = f"?child={snapshot.child.slug}" if snapshot else ""
    return {"feeding": f"{base}/feedings/add/{child}", "sleep": f"{base}/sleep/add/{child}", "timers": f"{base}/timers/"}


def now_payload(snapshot: Snapshot, settings: Settings, now: datetime) -> dict[str, Any]:
    rules = settings.rules
    d = snapshot.derived
    period = "night" if is_night(now, rules) else "day"
    past = d.episodes.filter(pl.col("start") <= lit_dt(now, rules))
    last_feed = None
    if not past.is_empty():
        ep = past.row(-1, named=True)
        last_feed = {
            "start": _iso(ep["start"], rules), "start_label": clock_label(ep["start"], rules),
            "since_h": _r(_elapsed_h(ep["start"], now)), "kind": ep["kind"],
            "volume": ep["bottle_volume"], "amount_missing": ep["amount_missing"],
        }
    feed_range = metrics.baseline(d.feed_gaps, now, period, rules)
    wake_range = metrics.baseline(d.wake_windows, now, "day", rules)
    return {
        "period": period,
        "last_feed": last_feed,
        "state": _current_state(snapshot, rules, now),
        "feed_gap_usual": _range(feed_range, period=period),
        "wake_window_usual": _range(wake_range, period="day"),
        "timers": [
            {"name": t.name, "start": _iso(t.start, rules), "start_label": clock_label(t.start, rules),
             "elapsed_h": _r(_elapsed_h(t.start, now)), "is_sleep": t.is_sleep}
            for t in snapshot.data.timers
        ],
        "links": links(settings, snapshot),
        "baseline_days": rules.baseline_days,
        "min_baseline_n": rules.min_baseline_n,
        "data_quality": {
            "duplicate_feeds": snapshot.data.duplicate_feeds,
            "duplicate_sleeps": snapshot.data.duplicate_sleeps,
            "excluded_feed_gaps": feed_range.excluded,
            "excluded_wake_windows": wake_range.excluded,
        },
    }


def _hour_ticks(rules: Rules, step: int = 3) -> list[dict[str, Any]]:
    base = rules.row_start.hour * 60 + rules.row_start.minute
    return [{"h": h, "label": f"{(base // 60 + h) % 24:02d}:{base % 60:02d}"} for h in range(0, 25, step)]


def _grouped(frame: pl.DataFrame, cols: list[str]) -> dict[date, list[dict[str, Any]]]:
    out: dict[date, list[dict[str, Any]]] = {}
    for row in frame.iter_rows(named=True):
        out.setdefault(row["label"], []).append({c: row[c] for c in cols})
    return out


def _spans(frame: pl.DataFrame, rows: pl.DataFrame) -> dict[date, list[dict[str, Any]]]:
    parts = metrics.split_by_rows(frame.select("start", "end"), rows).with_columns(
        pl.col("start_h", "end_h").round(3)
    )
    return _grouped(parts, ["start_h", "end_h"])


def actogram_payload(snapshot: Snapshot, settings: Settings, now: datetime, days: int) -> dict[str, Any]:
    rules = settings.rules
    d = snapshot.derived
    today = day_label(now, rules)
    rows = rows_frame(label_range(today, days), rules)
    nights = nights_for_rows(rows, rules)

    sleep_parts = metrics.split_by_rows(d.sleeps, rows).with_columns(
        pl.col("start_h", "end_h", "duration_h").round(3),
        start_label=pl.col("start").dt.strftime("%H:%M"),
        end_label=pl.col("end").dt.strftime("%H:%M"),
    )
    sleeps = _grouped(sleep_parts, ["start_h", "end_h", "start_label", "end_label", "duration_h",
                                    "continues_before", "continues_after"])

    feed_marks = (
        d.episodes.with_columns(label=day_label_expr(pl.col("start"), rules))
        .join(rows.select("label", "row_start"), on="label", how="inner")
        .with_columns(h=hours(pl.col("start") - pl.col("row_start")).round(3),
                      time_label=pl.col("start").dt.strftime("%H:%M"))
        .sort("start")
    )
    feeds = _grouped(feed_marks, ["h", "time_label", "kind", "bottle_volume", "amount_missing", "n_feeds"])

    night_spans = _spans(nights.rename({"night_start": "start", "night_end": "end"}), rows)
    suspect_spans = _spans(d.suspects, rows)
    suspect_flags = dict(metrics.suspect_days(d.suspects, rows).iter_rows())

    in_progress: dict[date, list[dict[str, Any]]] = {}
    sleep_timers = [t for t in snapshot.data.timers if t.is_sleep and t.start <= now]
    if sleep_timers:
        running = pl.DataFrame({"start": [sleep_timers[0].start], "end": [now]},
                               schema={"start": dt_type(rules), "end": dt_type(rules)})
        in_progress = _spans(running, rows)

    today_start = rows.filter(pl.col("label") == today)["row_start"][0]
    max_volume = d.episodes["bottle_volume"].max() if not d.episodes.is_empty() else None
    return {
        "days": days,
        "x_max_h": float(rows["length_h"].max()),
        "ticks": _hour_ticks(rules),
        "now_h": _r(_elapsed_h(today_start, now)),
        "max_volume": max_volume,
        "rows": [
            {
                "label": r["label"].isoformat(),
                "label_short": _short_label(r["label"]),
                "length_h": r["length_h"],
                "is_partial": r["label"] == today,
                "suspect_gap": bool(suspect_flags.get(r["label"], False)),
                "night": night_spans.get(r["label"], []),
                "suspect": suspect_spans.get(r["label"], []),
                "sleeps": sleeps.get(r["label"], []),
                "sleep_in_progress": in_progress.get(r["label"], []),
                "feeds": feeds.get(r["label"], []),
            }
            for r in rows.iter_rows(named=True)
        ],
    }


def _logged_labels(snapshot: Snapshot, rows: pl.DataFrame) -> pl.Series:
    activity = pl.concat([snapshot.data.feeds.select("start", "end"), snapshot.data.sleeps.select("start", "end")])
    instant = activity.with_columns(end=pl.max_horizontal("end", pl.col("start") + pl.duration(microseconds=1)))
    return metrics.split_by_rows(instant, rows)["label"].unique()


def trends_payload(snapshot: Snapshot, settings: Settings, now: datetime, days: int) -> dict[str, Any]:
    rules = settings.rules
    d = snapshot.derived
    today = day_label(now, rules)
    rows = rows_frame(label_range(today, days + ROLLING_NIGHTS - 1), rules)
    nights = nights_for_rows(rows, rules)
    logged = _logged_labels(snapshot, rows)

    table = (
        rows.select("label", "row_start", "row_end")
        .join(metrics.sleep_by_day(d.sleeps, rows, nights), on="label")
        .join(metrics.longest_night_by_day(d.sleeps, rows, nights), on="label")
        .join(metrics.feeding_by_day(snapshot.data.feeds, d.episodes, rows, rules), on="label")
        .join(metrics.suspect_days(d.suspects, rows), on="label")
        .sort("label")
        .with_columns(has_data=pl.col("label").is_in(logged.implode()))
    )
    value_cols = ["night_sleep_h", "day_sleep_h", "longest_night_h", "bottle_volume", "bottle_missing",
                  "breast_episodes", "feed_episodes"]
    table = table.with_columns(
        pl.when(pl.col("has_data")).then(pl.col(c)).otherwise(None).alias(c) for c in value_cols
    ).with_columns(
        longest_night_median_h=pl.col("longest_night_h").rolling_median(window_size=ROLLING_NIGHTS, min_samples=3)
    ).tail(days)

    out_rows = []
    for i, r in enumerate(table.iter_rows(named=True)):
        out_rows.append({
            "i": i,
            "label": r["label"].isoformat(),
            "label_short": _short_label(r["label"]),
            "is_partial": r["label"] == today,
            "has_data": r["has_data"],
            "suspect_gap": r["suspect_gap"],
            **{c: _r(r[c]) for c in ("night_sleep_h", "day_sleep_h", "longest_night_h", "longest_night_median_h")},
            "bottle_volume": _r(r["bottle_volume"], 1),
            "bottle_missing": r["bottle_missing"],
            "breast_episodes": r["breast_episodes"],
            "feed_episodes": r["feed_episodes"],
        })
    return {"days": days, "rows": out_rows, **gap_scatter(snapshot, rules, now)}


def gap_scatter(snapshot: Snapshot, rules: Rules, now: datetime) -> dict[str, Any]:
    row_start_h = rules.row_start.hour + rules.row_start.minute / 60
    gaps = snapshot.derived.feed_gaps.filter(
        pl.col("start") >= lit_dt(now - timedelta(days=GAP_SCATTER_DAYS), rules)
    ).with_columns(
        tod_h=((pl.col("start").dt.hour() + pl.col("start").dt.minute() / 60 - row_start_h) % 24).round(3),
        days_ago=(hours(lit_dt(now, rules) - pl.col("start")) / 24).round(2),
        start_label=pl.col("start").dt.strftime("%a %H:%M"),
    )

    def tod(t) -> float:
        return round((t.hour + t.minute / 60 - row_start_h) % 24, 3)

    ns, ne = tod(rules.night_start), tod(rules.night_end)
    night = [{"start_h": ns, "end_h": ne}] if ns < ne else [{"start_h": ns, "end_h": 24}, {"start_h": 0, "end_h": ne}]
    return {
        "gap_days": GAP_SCATTER_DAYS,
        "gap_ticks": _hour_ticks(rules),
        "gap_night": night,
        "gaps": [
            {"start": _iso(g["start"], rules), "start_label": g["start_label"], "tod_h": g["tod_h"],
             "length_h": _r(g["length_h"]), "period": g["period"], "days_ago": g["days_ago"],
             "suspect": g["suspect"]}
            for g in gaps.iter_rows(named=True)
        ],
    }
