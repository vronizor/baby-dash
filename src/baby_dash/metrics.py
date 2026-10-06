from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import polars as pl

from baby_dash.config import Rules
from baby_dash.timebase import day_label_expr, hours, is_night_expr, lit_dt, period_expr


@dataclass(frozen=True)
class UsualRange:
    median_h: float | None
    p25_h: float | None
    p75_h: float | None
    n: int
    enough: bool
    excluded: int = 0


def _intervals_schema(frame: pl.DataFrame) -> dict[str, pl.DataType]:
    return {"start": frame.schema["start"], "end": frame.schema["end"]}


def _merge_ids(frame: pl.DataFrame, is_new: pl.Expr) -> pl.DataFrame:
    return frame.sort("start").with_columns(_prev_end=pl.col("end").cum_max().shift(1)).with_columns(
        _group=(pl.col("_prev_end").is_null() | is_new).cum_sum()
    )


def feed_episodes(feeds: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    milk = feeds.filter(pl.col("kind").is_in(["breast", "bottle"]))
    grouped = _merge_ids(milk, pl.col("start") > pl.col("_prev_end") + rules.feed_merge)
    is_bottle = pl.col("kind") == "bottle"
    bottle_amounts = pl.col("amount").filter(is_bottle)
    episodes = grouped.group_by("_group", maintain_order=True).agg(
        start=pl.col("start").min(),
        end=pl.col("end").max(),
        has_breast=(pl.col("kind") == "breast").any(),
        has_bottle=is_bottle.any(),
        bottle_volume=pl.when(bottle_amounts.count() > 0).then(bottle_amounts.sum()),
        amount_missing=pl.col("amount_missing").any(),
        n_feeds=pl.len().cast(pl.Int64),
    )
    return episodes.select(
        "start",
        "end",
        kind=pl.when(pl.col("has_breast") & pl.col("has_bottle"))
        .then(pl.lit("mixed"))
        .when(pl.col("has_bottle"))
        .then(pl.lit("bottle"))
        .otherwise(pl.lit("breast")),
        has_breast="has_breast",
        bottle_volume=pl.col("bottle_volume").cast(pl.Float64),
        amount_missing="amount_missing",
        n_feeds="n_feeds",
    )


def merge_sleeps(sleeps: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    grouped = _merge_ids(sleeps, pl.col("start") >= pl.col("_prev_end") + rules.sleep_merge)
    merged = grouped.group_by("_group", maintain_order=True).agg(
        start=pl.col("start").min(), end=pl.col("end").max()
    )
    return merged.select("start", "end", duration_h=hours(pl.col("end") - pl.col("start")))


def _consecutive(frame: pl.DataFrame, start: str, end: str, rules: Rules) -> pl.DataFrame:
    return (
        frame.sort("start")
        .select(start=pl.col(start), end=pl.col(end).shift(-1))
        .drop_nulls()
        .with_columns(length_h=hours(pl.col("end") - pl.col("start")), period=period_expr(pl.col("start"), rules))
    )


def feed_gaps(episodes: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    return _consecutive(episodes, "start", "start", rules)


def wake_windows(merged_sleeps: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    return _consecutive(merged_sleeps, "end", "start", rules)


def split_by_rows(intervals: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    joined = intervals.join_where(
        rows.select("label", "row_start", "row_end"),
        pl.col("start") < pl.col("row_end"),
        pl.col("end") > pl.col("row_start"),
    )
    return joined.with_columns(
        seg_start=pl.max_horizontal("start", "row_start"),
        seg_end=pl.min_horizontal("end", "row_end"),
        continues_before=pl.col("start") < pl.col("row_start"),
        continues_after=pl.col("end") > pl.col("row_end"),
    ).with_columns(
        start_h=hours(pl.col("seg_start") - pl.col("row_start")),
        end_h=hours(pl.col("seg_end") - pl.col("row_start")),
    ).sort("seg_start")


def _overlap_h(a_start: str, a_end: str, b_start: str, b_end: str) -> pl.Expr:
    return hours(pl.min_horizontal(a_end, b_end) - pl.max_horizontal(a_start, b_start))


def sleep_by_day(merged_sleeps: pl.DataFrame, rows: pl.DataFrame, nights: pl.DataFrame) -> pl.DataFrame:
    segs = split_by_rows(merged_sleeps, rows)
    total = segs.group_by("label").agg(total_h=hours(pl.col("seg_end") - pl.col("seg_start")).sum())
    night = (
        segs.join_where(
            nights.select("night_start", "night_end"),
            pl.col("seg_start") < pl.col("night_end"),
            pl.col("seg_end") > pl.col("night_start"),
        )
        .group_by("label")
        .agg(night_sleep_h=_overlap_h("seg_start", "seg_end", "night_start", "night_end").sum())
    )
    return (
        rows.select("label")
        .join(total, on="label", how="left")
        .join(night, on="label", how="left")
        .with_columns(pl.col("total_h", "night_sleep_h").fill_null(0.0))
        .select("label", "night_sleep_h", day_sleep_h=pl.col("total_h") - pl.col("night_sleep_h"))
    )


def longest_night_by_day(merged_sleeps: pl.DataFrame, rows: pl.DataFrame, nights: pl.DataFrame) -> pl.DataFrame:
    with_mid = merged_sleeps.with_columns(mid=pl.col("start") + (pl.col("end") - pl.col("start")) / 2)
    longest = (
        with_mid.join_where(
            nights.select("night_start", "night_end", "label"),
            pl.col("mid") >= pl.col("night_start"),
            pl.col("mid") < pl.col("night_end"),
        )
        .group_by("label")
        .agg(longest_night_h=pl.col("duration_h").max())
    )
    return rows.select("label").join(longest, on="label", how="left")


def suspect_intervals(feeds: pl.DataFrame, merged_sleeps: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    activity = pl.concat([feeds.select("start", "end"), merged_sleeps.select("start", "end")])
    holes = (
        activity.sort("start")
        .with_columns(prev_end=pl.col("end").cum_max().shift(1))
        .filter(pl.col("start") > pl.col("prev_end"))
        .select(start=pl.col("prev_end"), end=pl.col("start"))
        .with_columns(length_h=hours(pl.col("end") - pl.col("start")))
    )
    mid = pl.col("start") + (pl.col("end") - pl.col("start")) / 2
    threshold_h = (
        pl.when(is_night_expr(mid, rules))
        .then(pl.lit(rules.suspect_night_gap / timedelta(hours=1)))
        .otherwise(pl.lit(rules.suspect_day_gap / timedelta(hours=1)))
    )
    return holes.filter(pl.col("length_h") > threshold_h)


def suspect_days(suspects: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    flagged = split_by_rows(suspects.select("start", "end"), rows)["label"].unique()
    return rows.select("label").with_columns(suspect_gap=pl.col("label").is_in(flagged.implode()))


def mark_suspect(intervals: pl.DataFrame, suspects: pl.DataFrame) -> pl.DataFrame:
    indexed = intervals.with_row_index("_i")
    hits = indexed.join_where(
        suspects.select(s_start="start", s_end="end"),
        pl.col("start") < pl.col("s_end"),
        pl.col("end") > pl.col("s_start"),
    )["_i"].unique()
    return indexed.with_columns(suspect=pl.col("_i").is_in(hits.implode())).drop("_i")


def usual_range(values: pl.Series, min_n: int) -> UsualRange:
    values = values.drop_nulls().cast(pl.Float64)
    n = values.len()
    if n < min_n or n == 0:
        return UsualRange(None, None, None, n, False)
    return UsualRange(
        median_h=values.median(),
        p25_h=values.quantile(0.25, "linear"),
        p75_h=values.quantile(0.75, "linear"),
        n=n,
        enough=True,
    )


def baseline(intervals: pl.DataFrame, now: datetime, period: str | None, rules: Rules) -> UsualRange:
    window = intervals.filter(
        (pl.col("start") >= lit_dt(now - timedelta(days=rules.baseline_days), rules))
        & (pl.col("end") <= lit_dt(now, rules) if "end" in intervals.columns else pl.lit(True))
    )
    if period is not None:
        window = window.filter(pl.col("period") == period)
    excluded = int(window["suspect"].sum())
    result = usual_range(window.filter(~pl.col("suspect"))["length_h"], rules.min_baseline_n)
    return replace(result, excluded=excluded)


def feeding_by_day(feeds: pl.DataFrame, episodes: pl.DataFrame, rows: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    bottles = (
        feeds.filter(pl.col("kind") == "bottle")
        .with_columns(label=day_label_expr(pl.col("start"), rules))
        .group_by("label")
        .agg(bottle_volume=pl.col("amount").sum(), bottle_missing=pl.col("amount_missing").sum().cast(pl.Int64))
    )
    eps = (
        episodes.with_columns(label=day_label_expr(pl.col("start"), rules))
        .group_by("label")
        .agg(breast_episodes=pl.col("has_breast").sum().cast(pl.Int64), feed_episodes=pl.len().cast(pl.Int64))
    )
    return (
        rows.select("label")
        .join(bottles, on="label", how="left")
        .join(eps, on="label", how="left")
        .with_columns(
            pl.col("bottle_volume").fill_null(0.0),
            pl.col("bottle_missing", "breast_episodes", "feed_episodes").fill_null(0),
        )
    )
