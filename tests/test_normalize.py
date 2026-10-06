from __future__ import annotations

from datetime import date, time

import pytest
from conftest import SLEEP_PATTERN, at, feeding, sleep, timer

from baby_dash.config import Rules
from baby_dash.normalize import normalize, normalize_feedings, normalize_sleeps, normalize_timers
from baby_dash.timebase import day_label, is_night, row_bounds


class TestFeedings:
    def test_kind_mapping_and_amount_flag(self, rules):
        rows = [feeding("2026-10-06 10:00", method=m, amount=a, type=t) for m, t, a in [
            ("bottle", "formula", 90),
            ("bottle", "breast milk", None),
            ("left breast", "breast milk", None),
            ("right breast", "breast milk", None),
            ("both breasts", "breast milk", None),
            ("parent fed", "breast milk", 60),
            ("parent fed", "formula", None),
            ("self fed", "fortified breast milk", 30),
            ("parent fed", "solid food", None),
            ("self fed", "solid food", None),
            ("bottle", "solid food", None),
            ("something new", "breast milk", None),
        ]]
        feeds, dups = normalize_feedings(rows, rules)
        assert dups == 0
        assert feeds["kind"].to_list() == [
            "bottle", "bottle", "breast", "breast", "breast", "bottle", "bottle", "bottle",
            "other", "other", "other", "other"]
        assert feeds["amount_missing"].to_list() == [
            False, True, False, False, False, False, True, False, False, False, False, False]

    def test_times_are_converted_to_tz_and_duration_ignores_string(self, rules):
        row = feeding("2026-10-06 10:00", "2026-10-06 10:25")
        row["start"], row["end"], row["duration"] = "2026-10-06T08:00:00Z", "2026-10-06T08:25:00.5Z", "99:99:99"
        feeds, _ = normalize_feedings([row], rules)
        assert feeds["start"][0] == at("2026-10-06 10:00")
        assert str(feeds.schema["start"]) == "Datetime(time_unit='us', time_zone='Europe/Paris')"
        assert feeds["duration_h"][0] == pytest.approx(25 / 60 + 0.5 / 3600)

    def test_exact_duplicates_dropped_and_counted_near_duplicates_kept(self, rules):
        a = feeding("2026-10-06 05:00", "2026-10-06 05:10")
        b = dict(a, id=999)
        c = feeding("2026-10-06 05:01", "2026-10-06 05:10")
        feeds, dups = normalize_feedings([a, b, c], rules)
        assert dups == 1
        assert feeds.height == 2

    def test_child_and_window_filters(self, rules):
        rows = [feeding("2026-10-06 10:00", child=1), feeding("2026-10-06 10:00", child=2),
                feeding("2026-09-01 10:00", child=1)]
        feeds, _ = normalize_feedings(rows, rules, child_id=1, since=at("2026-09-20 00:00"))
        assert feeds.height == 1

    def test_empty(self, rules):
        feeds, dups = normalize_feedings([], rules)
        assert feeds.is_empty() and dups == 0
        assert "kind" in feeds.columns


class TestSleeps:
    def test_sleep_across_window_start_kept(self, rules):
        sleeps, _ = normalize_sleeps([sleep("2026-09-19 22:00", "2026-09-20 03:00")], rules,
                                     since=at("2026-09-20 00:00"))
        assert sleeps.height == 1

    def test_duplicates(self, rules):
        a = sleep("2026-10-06 13:00", "2026-10-06 14:00")
        _, dups = normalize_sleeps([a, dict(a, id=1000)], rules)
        assert dups == 1


class TestTimers:
    def test_sleep_detection_by_name_and_child_scope(self, rules):
        timers = normalize_timers([
            timer("2026-10-06 13:00", "Sleep"),
            timer("2026-10-06 13:05", "Sieste du midi"),
            timer("2026-10-06 13:10", "Feeding"),
            timer("2026-10-06 13:15", None, child=None),
            timer("2026-10-06 13:20", "nap", child=2),
        ], rules, SLEEP_PATTERN, child_id=1)
        assert [(t.name, t.is_sleep) for t in timers] == [
            ("Sleep", True), ("Sieste du midi", True), ("Feeding", False), (None, False)]

    def test_inactive_v1_timers_are_ignored(self, rules):
        old = timer("2026-10-06 13:00", "Sleep") | {"active": False, "end": "2026-10-06T13:30:00+02:00"}
        assert normalize_timers([old], rules, SLEEP_PATTERN) == []


def test_normalize_handles_all_empty(rules):
    n = normalize([], [], [], rules, SLEEP_PATTERN)
    assert n.feeds.is_empty() and n.sleeps.is_empty() and n.timers == []


class TestTimebase:
    def test_row_label_is_date_of_its_morning(self, rules):
        assert day_label(at("2026-10-05 18:00"), rules) == date(2026, 10, 6)
        assert day_label(at("2026-10-06 17:59"), rules) == date(2026, 10, 6)
        assert day_label(at("2026-10-06 18:00"), rules) == date(2026, 10, 7)
        assert row_bounds(date(2026, 10, 6), rules) == (at("2026-10-05 18:00"), at("2026-10-06 18:00"))

    def test_morning_row_start_labels_by_same_date(self):
        r = Rules(row_start=time(6, 0))
        assert day_label(at("2026-10-06 07:00"), r) == date(2026, 10, 6)
        assert day_label(at("2026-10-07 05:00"), r) == date(2026, 10, 6)

    def test_night_window_bounds(self, rules):
        assert is_night(at("2026-10-06 20:00"), rules)
        assert is_night(at("2026-10-07 06:59"), rules)
        assert not is_night(at("2026-10-07 07:00"), rules)
        assert not is_night(at("2026-10-06 19:59"), rules)
