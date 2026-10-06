from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from conftest import at, feeding, feeds_of, sleep, sleeps_of

from baby_dash import metrics
from baby_dash.timebase import label_range, nights_for_rows, rows_frame


def _rows(rules, *labels: date):
    return rows_frame(list(labels), rules)


class TestFeedEpisodes:
    def test_side_switch_and_top_up_merge_but_other_is_ignored(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 10:00", "2026-10-06 10:15", "left breast"),
            feeding("2026-10-06 10:20", "2026-10-06 10:30", "right breast"),
            feeding("2026-10-06 10:45", method="bottle", amount=60),
            feeding("2026-10-06 13:00", method="bottle", amount=90),
            feeding("2026-10-06 13:20", method="bottle", amount=None),
            feeding("2026-10-06 16:00", method="self fed"),
            feeding("2026-10-06 16:30", "2026-10-06 16:40", "left breast"),
        ])
        ep = metrics.feed_episodes(feeds, rules)

        assert ep["start"].to_list() == [at("2026-10-06 10:00"), at("2026-10-06 13:00"), at("2026-10-06 16:30")]
        assert ep["end"].to_list() == [at("2026-10-06 10:45"), at("2026-10-06 13:20"), at("2026-10-06 16:40")]
        assert ep["kind"].to_list() == ["mixed", "bottle", "breast"]
        assert ep["bottle_volume"].to_list() == [60.0, 90.0, None]
        assert ep["amount_missing"].to_list() == [False, True, False]
        assert ep["n_feeds"].to_list() == [3, 2, 1]

    def test_merge_window_is_inclusive(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 10:00", "2026-10-06 10:10"),
            feeding("2026-10-06 10:30", "2026-10-06 10:40"),
            feeding("2026-10-06 11:00:01", "2026-10-06 11:10"),
        ])
        assert metrics.feed_episodes(feeds, rules)["n_feeds"].to_list() == [2, 1]

    def test_overlapping_logs_from_two_parents_become_one_episode(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 02:00", "2026-10-06 02:20"),
            feeding("2026-10-06 02:05", "2026-10-06 02:25"),
        ])
        ep = metrics.feed_episodes(feeds, rules)
        assert ep.height == 1
        assert ep["end"][0] == at("2026-10-06 02:25")

    def test_long_feed_keeps_episode_open_past_a_short_inner_feed(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 10:00", "2026-10-06 11:00"),
            feeding("2026-10-06 10:10", "2026-10-06 10:15"),
            feeding("2026-10-06 11:15", "2026-10-06 11:20"),
        ])
        assert metrics.feed_episodes(feeds, rules).height == 1

    def test_empty(self, rules):
        ep = metrics.feed_episodes(feeds_of([]), rules)
        assert ep.is_empty()
        assert "kind" in ep.columns


class TestFeedGaps:
    def test_gap_between_episode_starts_with_period_from_gap_start(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 10:00", "2026-10-06 10:15"),
            feeding("2026-10-06 10:20", "2026-10-06 10:30", "right breast"),
            feeding("2026-10-06 13:00", "2026-10-06 13:10"),
            feeding("2026-10-06 19:30", "2026-10-06 19:40"),
            feeding("2026-10-06 23:30", "2026-10-06 23:45"),
            feeding("2026-10-07 07:00", "2026-10-07 07:15"),
            feeding("2026-10-07 10:00", "2026-10-07 10:15"),
        ])
        gaps = metrics.feed_gaps(metrics.feed_episodes(feeds, rules), rules)
        assert gaps["length_h"].to_list() == pytest.approx([3.0, 6.5, 4.0, 7.5, 3.0])
        assert gaps["period"].to_list() == ["day", "day", "day", "night", "day"]

    def test_no_gaps_for_single_episode(self, rules):
        gaps = metrics.feed_gaps(metrics.feed_episodes(feeds_of([feeding("2026-10-06 10:00")]), rules), rules)
        assert gaps.is_empty()


class TestSleepMerge:
    def test_merge_is_strictly_less_than_threshold(self, rules):
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-06 13:00", "2026-10-06 14:00"),
            sleep("2026-10-06 14:04", "2026-10-06 15:00"),
            sleep("2026-10-06 15:05", "2026-10-06 16:00"),
        ]), rules)
        assert merged["start"].to_list() == [at("2026-10-06 13:00"), at("2026-10-06 15:05")]
        assert merged["duration_h"].to_list() == pytest.approx([2.0, 55 / 60])

    def test_overlap_merges(self, rules):
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-06 20:00", "2026-10-06 23:00"),
            sleep("2026-10-06 22:30", "2026-10-06 23:30"),
        ]), rules)
        assert merged.height == 1
        assert merged["duration_h"][0] == pytest.approx(3.5)


class TestWakeWindows:
    def test_wake_windows_from_sleep_end_to_next_start(self, rules):
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-06 13:00", "2026-10-06 15:00"),
            sleep("2026-10-06 16:30", "2026-10-06 17:00"),
            sleep("2026-10-06 21:00", "2026-10-07 02:00"),
            sleep("2026-10-07 03:00", "2026-10-07 06:00"),
        ]), rules)
        ww = metrics.wake_windows(merged, rules)
        assert ww["length_h"].to_list() == pytest.approx([1.5, 4.0, 1.0])
        assert ww["period"].to_list() == ["day", "day", "night"]


class TestSplitByRows:
    def test_sleep_across_row_start_splits_and_sums(self, rules):
        rows = _rows(rules, date(2026, 10, 6), date(2026, 10, 7))
        merged = metrics.merge_sleeps(sleeps_of([sleep("2026-10-06 17:00", "2026-10-06 19:30")]), rules)
        parts = metrics.split_by_rows(merged, rows)
        assert parts["label"].to_list() == [date(2026, 10, 6), date(2026, 10, 7)]
        assert parts["start_h"].to_list() == pytest.approx([23.0, 0.0])
        assert parts["end_h"].to_list() == pytest.approx([24.0, 1.5])
        assert parts["continues_before"].to_list() == [False, True]
        assert parts["continues_after"].to_list() == [True, False]
        assert sum(parts["end_h"] - parts["start_h"]) == pytest.approx(2.5)

    def test_midnight_needs_no_split(self, rules):
        rows = _rows(rules, date(2026, 10, 6), date(2026, 10, 7))
        merged = metrics.merge_sleeps(sleeps_of([sleep("2026-10-06 23:00", "2026-10-07 01:30")]), rules)
        parts = metrics.split_by_rows(merged, rows)
        assert parts["label"].to_list() == [date(2026, 10, 7)]
        assert parts["start_h"].to_list() == pytest.approx([5.0])
        assert parts["end_h"].to_list() == pytest.approx([7.5])


class TestSleepByDay:
    def test_night_and_day_overlap_per_row(self, rules):
        rows = _rows(rules, date(2026, 10, 6), date(2026, 10, 7))
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-06 19:00", "2026-10-06 21:00"),
            sleep("2026-10-07 06:00", "2026-10-07 08:00"),
            sleep("2026-10-06 17:00", "2026-10-06 18:30"),
        ]), rules)
        totals = metrics.sleep_by_day(merged, rows, nights_for_rows(rows, rules))
        by = {r["label"]: r for r in totals.iter_rows(named=True)}
        assert by[date(2026, 10, 6)]["night_sleep_h"] == pytest.approx(0.0)
        assert by[date(2026, 10, 6)]["day_sleep_h"] == pytest.approx(1.0)
        assert by[date(2026, 10, 7)]["night_sleep_h"] == pytest.approx(2.0)
        assert by[date(2026, 10, 7)]["day_sleep_h"] == pytest.approx(2.5)

    def test_rows_without_sleep_are_zero_not_missing(self, rules):
        rows = _rows(rules, date(2026, 10, 6))
        totals = metrics.sleep_by_day(metrics.merge_sleeps(sleeps_of([]), rules), rows, nights_for_rows(rows, rules))
        assert totals.to_dicts() == [{"label": date(2026, 10, 6), "night_sleep_h": 0.0, "day_sleep_h": 0.0}]


class TestLongestNightStretch:
    def test_longest_sleep_with_midpoint_in_night_measured_unclipped(self, rules):
        rows = _rows(rules, date(2026, 10, 7))
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-06 16:00", "2026-10-06 20:30"),
            sleep("2026-10-06 21:00", "2026-10-06 23:00"),
            sleep("2026-10-06 23:30", "2026-10-07 03:30"),
            sleep("2026-10-07 04:00", "2026-10-07 09:00"),
        ]), rules)
        out = metrics.longest_night_by_day(merged, rows, nights_for_rows(rows, rules))
        assert out.to_dicts() == [{"label": date(2026, 10, 7), "longest_night_h": pytest.approx(5.0)}]

    def test_no_night_sleep_is_null(self, rules):
        rows = _rows(rules, date(2026, 10, 7))
        merged = metrics.merge_sleeps(sleeps_of([sleep("2026-10-07 13:00", "2026-10-07 14:00")]), rules)
        out = metrics.longest_night_by_day(merged, rows, nights_for_rows(rows, rules))
        assert out["longest_night_h"].to_list() == [None]


class TestSuspect:
    def test_daytime_hole_over_6h_is_suspect(self, rules):
        feeds = feeds_of([feeding("2026-10-06 08:00"), feeding("2026-10-06 15:00")])
        sus = metrics.suspect_intervals(feeds, metrics.merge_sleeps(sleeps_of([]), rules), rules)
        assert sus["start"].to_list() == [at("2026-10-06 08:00")]
        assert sus["length_h"].to_list() == pytest.approx([7.0])

    def test_night_threshold_is_8h_by_midpoint(self, rules):
        feeds = feeds_of([feeding(t) for t in (
            "2026-10-06 21:00", "2026-10-07 04:00", "2026-10-07 08:00", "2026-10-07 12:00",
            "2026-10-07 16:00", "2026-10-07 20:00", "2026-10-08 05:00")])
        sus = metrics.suspect_intervals(feeds, metrics.merge_sleeps(sleeps_of([]), rules), rules)
        assert sus["start"].to_list() == [at("2026-10-07 20:00")]

    def test_sleep_counts_as_activity(self, rules):
        feeds = feeds_of([feeding("2026-10-06 08:00"), feeding("2026-10-06 15:00")])
        merged = metrics.merge_sleeps(sleeps_of([sleep("2026-10-06 10:00", "2026-10-06 11:00")]), rules)
        assert metrics.suspect_intervals(feeds, merged, rules).is_empty()

    def test_suspect_days_and_gap_exclusion(self, rules):
        feeds = feeds_of([feeding("2026-10-06 08:00"), feeding("2026-10-06 15:00"), feeding("2026-10-06 17:00")])
        empty = metrics.merge_sleeps(sleeps_of([]), rules)
        sus = metrics.suspect_intervals(feeds, empty, rules)
        rows = _rows(rules, date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7))
        assert metrics.suspect_days(sus, rows)["suspect_gap"].to_list() == [False, True, False]
        gaps = metrics.mark_suspect(metrics.feed_gaps(metrics.feed_episodes(feeds, rules), rules), sus)
        assert gaps["suspect"].to_list() == [True, False]


class TestUsualRange:
    def test_median_and_linear_quartiles(self, rules):
        r = metrics.usual_range(pl.Series([6.0, 1.0, 3.0, 2.0, 5.0, 4.0]), min_n=6)
        assert (r.median_h, r.p25_h, r.p75_h, r.n, r.enough) == (3.5, 2.25, 4.75, 6, True)

    def test_below_min_n_returns_no_range(self, rules):
        r = metrics.usual_range(pl.Series([1.0, 2.0, 3.0]), min_n=6)
        assert (r.median_h, r.p25_h, r.p75_h, r.n, r.enough) == (None, None, None, 3, False)

    def test_baseline_filters_window_period_and_suspect(self, rules):
        gaps = pl.DataFrame({
            "start": [at("2026-10-02 10:00"), at("2026-10-04 10:00"), at("2026-10-04 13:00"),
                      at("2026-10-05 02:00"), at("2026-10-05 10:00"), at("2026-10-06 09:00")],
            "length_h": [9.0, 3.0, 2.0, 5.0, 7.0, 4.0],
            "period": ["day", "day", "day", "night", "day", "day"],
            "suspect": [False, False, False, False, True, False],
        })
        r = metrics.baseline(gaps, now=at("2026-10-06 12:00"), period="day", rules=rules)
        assert r.n == 3
        assert r.excluded == 1
        assert r.enough is False

        loose = metrics.baseline(gaps, now=at("2026-10-06 12:00"), period="day",
                                 rules=type(rules)(min_baseline_n=3))
        assert (loose.median_h, loose.p25_h, loose.p75_h) == (3.0, 2.5, 3.5)


class TestDst:
    def test_fall_back_row_is_25h_and_sleep_across_switch_counts_once(self, rules):
        rows = _rows(rules, date(2026, 10, 25), date(2026, 10, 26))
        assert rows["length_h"].to_list() == [25.0, 24.0]
        merged = metrics.merge_sleeps(sleeps_of([
            sleep("2026-10-25T01:30:00+02:00", "2026-10-25T03:30:00+01:00"),
            sleep("2026-10-25T17:00:00+01:00", "2026-10-25T19:00:00+01:00"),
        ]), rules)
        assert merged["duration_h"].to_list() == pytest.approx([3.0, 2.0])

        parts = metrics.split_by_rows(merged, rows)
        assert parts["start_h"].to_list() == pytest.approx([7.5, 24.0, 0.0])
        assert parts["end_h"].to_list() == pytest.approx([10.5, 25.0, 1.0])

        totals = metrics.sleep_by_day(merged, rows, nights_for_rows(rows, rules))
        assert totals["night_sleep_h"].to_list() == pytest.approx([3.0, 0.0])
        assert totals["day_sleep_h"].to_list() == pytest.approx([1.0, 1.0])
        longest = metrics.longest_night_by_day(merged, rows, nights_for_rows(rows, rules))["longest_night_h"]
        assert longest.to_list()[0] == pytest.approx(3.0)
        assert longest.to_list()[1] is None

    def test_spring_forward_row_is_23h(self, rules):
        rows = rows_frame(label_range(date(2026, 3, 30), 3), rules)
        assert rows["length_h"].to_list() == [24.0, 23.0, 24.0]

    def test_feed_gap_across_fall_back_uses_real_elapsed_time(self, rules):
        feeds = feeds_of([feeding("2026-10-25T01:00:00+02:00"), feeding("2026-10-25T04:00:00+01:00")])
        gaps = metrics.feed_gaps(metrics.feed_episodes(feeds, rules), rules)
        assert gaps["length_h"].to_list() == pytest.approx([4.0])


class TestFeedingByDay:
    def test_bottle_volume_by_feed_start_and_breast_episodes_by_episode(self, rules):
        feeds = feeds_of([
            feeding("2026-10-06 17:50", "2026-10-06 18:05", "left breast"),
            feeding("2026-10-06 18:10", method="bottle", amount=40),
            feeding("2026-10-06 22:00", method="bottle", amount=120),
            feeding("2026-10-07 02:00", method="bottle"),
            feeding("2026-10-07 05:00", "2026-10-07 05:20", "both breasts"),
        ])
        rows = _rows(rules, date(2026, 10, 6), date(2026, 10, 7))
        out = metrics.feeding_by_day(feeds, metrics.feed_episodes(feeds, rules), rows, rules)
        assert out.to_dicts() == [
            {"label": date(2026, 10, 6), "bottle_volume": 0.0, "bottle_missing": 0,
             "breast_episodes": 1, "feed_episodes": 1},
            {"label": date(2026, 10, 7), "bottle_volume": 160.0, "bottle_missing": 1,
             "breast_episodes": 1, "feed_episodes": 3},
        ]
