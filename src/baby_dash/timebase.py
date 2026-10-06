from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

from baby_dash.config import Rules

US_PER_HOUR = 3_600_000_000


def dt_type(rules: Rules) -> pl.Datetime:
    return pl.Datetime("us", rules.tz.key)


def lit_dt(value: datetime, rules: Rules) -> pl.Expr:
    return pl.lit(value.astimezone(rules.tz), dtype=dt_type(rules))


def hours(duration: pl.Expr) -> pl.Expr:
    return duration.dt.total_microseconds() / US_PER_HOUR


def _label_shift(rules: Rules) -> timedelta:
    return timedelta(days=1) if rules.row_start >= time(12, 0) else timedelta(0)


def row_bounds(label: date, rules: Rules) -> tuple[datetime, datetime]:
    base = label - _label_shift(rules)
    start = datetime.combine(base, rules.row_start, rules.tz)
    end = datetime.combine(base + timedelta(days=1), rules.row_start, rules.tz)
    return start, end


def day_label(ts: datetime, rules: Rules) -> date:
    local = ts.astimezone(rules.tz)
    base = local.date() if local.time() >= rules.row_start else local.date() - timedelta(days=1)
    return base + _label_shift(rules)


def day_label_expr(ts: pl.Expr, rules: Rules) -> pl.Expr:
    base = (
        pl.when(ts.dt.time() >= pl.lit(rules.row_start))
        .then(ts.dt.date())
        .otherwise(ts.dt.date() - pl.duration(days=1))
    )
    return base + pl.duration(days=_label_shift(rules).days)


def is_night(ts: datetime, rules: Rules) -> bool:
    t = ts.astimezone(rules.tz).time()
    if rules.night_start > rules.night_end:
        return t >= rules.night_start or t < rules.night_end
    return rules.night_start <= t < rules.night_end


def is_night_expr(ts: pl.Expr, rules: Rules) -> pl.Expr:
    t = ts.dt.time()
    ns, ne = pl.lit(rules.night_start), pl.lit(rules.night_end)
    if rules.night_start > rules.night_end:
        return (t >= ns) | (t < ne)
    return (t >= ns) & (t < ne)


def period_expr(ts: pl.Expr, rules: Rules) -> pl.Expr:
    return pl.when(is_night_expr(ts, rules)).then(pl.lit("night")).otherwise(pl.lit("day"))


def rows_frame(labels: list[date], rules: Rules) -> pl.DataFrame:
    bounds = [row_bounds(d, rules) for d in labels]
    return pl.DataFrame(
        {
            "label": labels,
            "row_start": [b[0] for b in bounds],
            "row_end": [b[1] for b in bounds],
        },
        schema={"label": pl.Date, "row_start": dt_type(rules), "row_end": dt_type(rules)},
    ).with_columns(length_h=hours(pl.col("row_end") - pl.col("row_start")))


def label_range(last: date, days: int) -> list[date]:
    return [last - timedelta(days=i) for i in range(days - 1, -1, -1)]


def night_windows(first: date, last: date, rules: Rules) -> pl.DataFrame:
    starts, ends = [], []
    d = first
    while d <= last:
        starts.append(datetime.combine(d, rules.night_start, rules.tz))
        end_day = d + timedelta(days=1) if rules.night_start > rules.night_end else d
        ends.append(datetime.combine(end_day, rules.night_end, rules.tz))
        d += timedelta(days=1)
    frame = pl.DataFrame(
        {"night_start": starts, "night_end": ends},
        schema={"night_start": dt_type(rules), "night_end": dt_type(rules)},
    )
    return frame.with_columns(label=day_label_expr(pl.col("night_start"), rules))


def nights_for_rows(rows: pl.DataFrame, rules: Rules) -> pl.DataFrame:
    if rows.is_empty():
        return night_windows(date(2000, 1, 2), date(2000, 1, 1), rules)
    first = rows["row_start"].min().date() - timedelta(days=1)
    last = rows["row_end"].max().date() + timedelta(days=1)
    return night_windows(first, last, rules)


def clock_label(ts: datetime, rules: Rules) -> str:
    return ts.astimezone(rules.tz).strftime("%H:%M")


def clock_label_expr(ts: pl.Expr) -> pl.Expr:
    return ts.dt.strftime("%H:%M")
