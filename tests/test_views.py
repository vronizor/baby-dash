from __future__ import annotations

import json

import pytest
from conftest import at, settings_for, snapshot_of

from baby_dash import views

SETTINGS = settings_for()


def _row(payload, label):
    return next(r for r in payload["rows"] if r["label"] == label)


class TestEdgeCaseFixture:
    NOW = at("2026-10-06 12:30")

    @pytest.fixture
    def snap(self):
        return snapshot_of("edge_cases", self.NOW)

    def test_now_tile_running_sleep_timer_is_not_awake(self, snap):
        p = views.now_payload(snap, SETTINGS, self.NOW)
        assert p["state"]["kind"] == "asleep"
        assert p["state"]["source"] == "timer"
        assert p["state"]["since_label"] == "12:10"
        assert [t["name"] for t in p["timers"]] == [None, "Sleep"]

    def test_now_tile_last_feed_and_usual_ranges(self, snap):
        p = views.now_payload(snap, SETTINGS, self.NOW)
        assert p["last_feed"]["start_label"] == "11:00"
        assert p["last_feed"]["since_h"] == pytest.approx(1.5)
        assert p["period"] == "day"
        fg = p["feed_gap_usual"]
        assert (fg["n"], fg["enough"], fg["median_h"]) == (5, False, None)
        loose = views.now_payload(snap, settings_for(MIN_BASELINE_N="5"), self.NOW)["feed_gap_usual"]
        assert (loose["n"], loose["enough"], loose["median_h"], loose["p25_h"], loose["p75_h"]) == (5, True, 3.0, 3.0, 3.5)
        ww = p["wake_window_usual"]
        assert (ww["n"], ww["enough"], ww["median_h"]) == (4, False, None)
        assert p["data_quality"]["duplicate_feeds"] == 1
        assert p["links"]["feeding"] == "https://baby.example.ts.net/feedings/add/?child=alix-test"

    def test_actogram_rows_marks_and_splits(self, snap):
        p = views.actogram_payload(snap, SETTINGS, self.NOW, 7)
        assert [r["label"] for r in p["rows"]][-2:] == ["2026-10-05", "2026-10-06"]
        today, yday = _row(p, "2026-10-06"), _row(p, "2026-10-05")
        assert today["is_partial"] and not yday["is_partial"]
        assert p["now_h"] == pytest.approx(18.5)
        assert [(s["start_h"], s["end_h"]) for s in yday["sleeps"]][-1] == (23.0, 24.0)
        assert (today["sleeps"][0]["start_h"], today["sleeps"][0]["end_h"]) == (0.0, 1.0)
        assert today["sleeps"][0]["continues_before"] is True
        assert today["sleep_in_progress"] == [{"start_h": pytest.approx(18.167, abs=1e-3), "end_h": 18.5}]
        assert yday["feeds"][-1]["h"] == pytest.approx(23.75)
        assert today["feeds"][0]["h"] == pytest.approx(5.833, abs=1e-3)
        assert today["feeds"][0]["since_prev_h"] == pytest.approx(6.083, abs=1e-3)
        assert today["feeds"][1]["since_prev_h"] == pytest.approx(4.167, abs=1e-3)
        assert yday["feeds"][0]["since_prev_h"] is None
        assert [f["kind"] for f in today["feeds"]] == ["breast", "bottle", "bottle", "breast"]
        assert yday["feeds"][3]["amount_missing"] is True
        assert today["night"] == [{"start_h": 2.0, "end_h": 13.0}]
        assert not any(r["suspect_gap"] for r in p["rows"])

    def test_solids_are_separate_marks_with_their_note(self, snap):
        p = views.actogram_payload(snap, SETTINGS, self.NOW, 7)
        today = _row(p, "2026-10-06")
        assert today["solids"] == [{"h": 9.5, "time_label": "03:30", "note": "Broccoli (steamed)"}]
        assert all(f["time_label"] != "03:30" for f in today["feeds"])

    def test_zero_duration_feeds_are_point_marks(self, snap):
        p = views.actogram_payload(snap, SETTINGS, self.NOW, 7)
        for row in p["rows"]:
            for f in row["feeds"]:
                assert set(f) >= {"h", "kind"} and "end_h" not in f

    def test_trends_totals(self, snap):
        p = views.trends_payload(snap, SETTINGS, self.NOW, 14)
        today, yday = _row(p, "2026-10-06"), _row(p, "2026-10-05")
        assert (today["night_sleep_h"], today["day_sleep_h"], today["longest_night_h"]) == (9.0, 2.5, 6.5)
        assert (today["bottle_volume"], today["breast_episodes"], today["feed_episodes"]) == (210.0, 2, 4)
        assert (yday["bottle_volume"], yday["bottle_missing"], yday["breast_episodes"]) == (120.0, 1, 3)
        assert today["is_partial"]
        earlier = _row(p, "2026-10-04")
        assert earlier["has_data"] is False and earlier["night_sleep_h"] is None


class TestDstFixture:
    NOW = at("2026-10-26 12:00")

    @pytest.fixture
    def snap(self):
        return snapshot_of("dst_fall_2026", self.NOW)

    def test_ambiguous_wall_clock_feeds_are_distinct(self, snap):
        assert snap.data.feeds.height == 9
        assert snap.data.duplicate_feeds == 0

    def test_25h_row_geometry_uses_real_elapsed_time(self, snap):
        p = views.actogram_payload(snap, SETTINGS, self.NOW, 7)
        row = _row(p, "2026-10-25")
        assert row["length_h"] == 25.0
        assert p["x_max_h"] == 25.0
        assert [(s["start_h"], s["end_h"]) for s in row["sleeps"]] == [
            (2.0, 5.25), (6.0, 8.25), (9.75, 13.25), (18.0, 19.5), (24.5, 25.0)]
        assert [f["h"] for f in row["feeds"]] == [1.0, 5.5, 8.5, 9.5, 13.5, 17.0, 20.5, 24.0]
        assert row["night"] == [{"start_h": 2.0, "end_h": 14.0}]
        nxt = _row(p, "2026-10-26")
        assert [(s["start_h"], s["end_h"]) for s in nxt["sleeps"]] == [(0.0, 0.5), (3.0, 7.0)]

    def test_totals_across_switch_neither_double_count_nor_vanish(self, snap):
        p = views.trends_payload(snap, SETTINGS, self.NOW, 14)
        row = _row(p, "2026-10-25")
        assert (row["night_sleep_h"], row["day_sleep_h"], row["longest_night_h"]) == (9.0, 2.0, 3.5)
        total_logged = sum(
            (s["end_h"] - s["start_h"])
            for r in views.actogram_payload(snap, SETTINGS, self.NOW, 7)["rows"] for s in r["sleeps"]
        )
        assert total_logged == pytest.approx(3.25 + 2.25 + 3.5 + 1.5 + 1.0 + 4.0)

    def test_gaps_across_switch(self, snap):
        gaps = snap.derived.feed_gaps["length_h"].to_list()
        assert gaps == pytest.approx([4.5, 3.0, 1.0, 4.0, 3.5, 3.5, 3.5, 3.5])


@pytest.mark.parametrize("name", ["empty", "single_entry", "breast_only", "bottle_only", "realistic_10d",
                                  "edge_cases", "dst_fall_2026"])
@pytest.mark.parametrize("now", ["2026-10-06 14:00", "2026-10-06 23:00", "2026-10-26 08:00"])
def test_every_payload_builds_and_serializes(name, now):
    t = at(now)
    snap = snapshot_of(name, t)
    for payload in (
        views.now_payload(snap, SETTINGS, t),
        views.actogram_payload(snap, SETTINGS, t, 7),
        views.actogram_payload(snap, SETTINGS, t, 14),
        views.trends_payload(snap, SETTINGS, t, 14),
        views.trends_payload(snap, SETTINGS, t, 30),
    ):
        json.dumps(payload, allow_nan=False)


def test_not_enough_data_states():
    t = at("2026-10-06 14:00")
    p = views.now_payload(snapshot_of("empty", t), SETTINGS, t)
    assert p["last_feed"] is None
    assert p["state"]["kind"] == "unknown"
    assert p["feed_gap_usual"]["enough"] is False
    single = views.now_payload(snapshot_of("single_entry", t), SETTINGS, t)
    assert single["last_feed"]["kind"] == "bottle"
    assert single["feed_gap_usual"]["n"] == 0


class TestRealistic:
    NOW = at("2026-10-06 14:00")

    @pytest.fixture
    def snap(self):
        return snapshot_of("realistic_10d", self.NOW)

    def test_logging_lapse_flags_day_and_is_excluded_from_baselines(self, snap):
        p = views.trends_payload(snap, SETTINGS, self.NOW, 14)
        assert [r["label"] for r in p["rows"] if r["suspect_gap"]] == ["2026-10-02"]
        assert snap.derived.feed_gaps["suspect"].sum() == 1

    def test_side_switches_do_not_register_as_gaps(self, snap):
        assert snap.derived.feed_gaps["length_h"].min() > 20 / 60
        assert snap.data.duplicate_feeds == 1

    def test_baselines_have_enough_data(self, snap):
        p = views.now_payload(snap, SETTINGS, self.NOW)
        assert p["feed_gap_usual"]["enough"] and p["wake_window_usual"]["enough"]
        assert p["feed_gap_usual"]["p25_h"] <= p["feed_gap_usual"]["median_h"] <= p["feed_gap_usual"]["p75_h"]


class TestHiatusAndConfusingCategories:
    NOW = at("2026-10-06 12:30")

    @pytest.fixture
    def snap(self):
        return snapshot_of("hiatus", self.NOW)

    def test_days_without_any_entry_are_no_data_not_zero(self, snap):
        p = views.trends_payload(snap, SETTINGS, self.NOW, 14)
        by = {r["label"]: r for r in p["rows"]}
        for label in ("2026-10-01", "2026-10-02", "2026-10-03"):
            assert by[label]["has_data"] is False
            assert by[label]["night_sleep_h"] is None and by[label]["breast_episodes"] is None
            assert by[label]["suspect_gap"] is True
        assert by["2026-09-30"]["has_data"] and by["2026-10-04"]["has_data"]
        assert by["2026-10-04"]["breast_episodes"] == 3

    def test_parent_fed_milk_counts_as_bottle(self, snap):
        p = views.trends_payload(snap, SETTINGS, self.NOW, 14)
        by = {r["label"]: r for r in p["rows"]}
        assert by["2026-10-05"]["bottle_volume"] == 70.0
        assert by["2026-10-06"]["bottle_missing"] == 1
        acto = views.actogram_payload(snap, SETTINGS, self.NOW, 7)
        today = [r for r in acto["rows"] if r["label"] == "2026-10-06"][0]
        bottle = [f for f in today["feeds"] if f["time_label"] == "10:30"][0]
        assert (bottle["kind"], bottle["amount_missing"]) == ("bottle", True)
