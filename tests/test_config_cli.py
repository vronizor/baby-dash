from __future__ import annotations

import json
from datetime import time

import httpx
import pytest
from conftest import settings_for

from baby_dash.cli import check_write_forbidden
from baby_dash.config import ConfigError, Settings


def test_defaults():
    s = settings_for()
    assert s.bb_url == "http://babybuddy:8000"
    assert s.rules.tz.key == "Europe/Paris"
    assert (s.rules.row_start, s.rules.night_start, s.rules.night_end) == (time(18), time(20), time(7))
    assert s.rules.feed_merge.total_seconds() == 1200 and s.rules.sleep_merge.total_seconds() == 300
    assert (s.rules.baseline_days, s.rules.min_baseline_n, s.cache_ttl_s, s.amount_unit) == (3, 6, 60, "ml")
    assert s.child_slug is None
    assert s.fetch_days == 30 + 4 + 1


@pytest.mark.parametrize("env, message", [
    ({}, "BB_PUBLIC_URL, BB_TOKEN"),
    ({"BB_PUBLIC_URL": "x"}, "BB_TOKEN"),
    ({"BB_PUBLIC_URL": "x", "BB_TOKEN": "t", "ROW_START": "6pm"}, "ROW_START"),
    ({"BB_PUBLIC_URL": "x", "BB_TOKEN": "t", "TZ": "Mars/Base"}, "TZ"),
    ({"BB_PUBLIC_URL": "x", "BB_TOKEN": "t", "CACHE_TTL_S": "0"}, "CACHE_TTL_S"),
    ({"BB_PUBLIC_URL": "x", "BB_TOKEN": "t", "NIGHT_START": "07:00"}, "differ"),
])
def test_invalid_config_fails_with_clear_message(env, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_overrides():
    s = settings_for(TZ="America/New_York", ROW_START="19:30", CHILD_SLUG="alix", BB_URL="http://bb:9000/")
    assert s.rules.tz.key == "America/New_York" and s.rules.row_start == time(19, 30)
    assert s.child_slug == "alix" and s.bb_url == "http://bb:9000"


@pytest.mark.parametrize("status, ok", [(403, True), (400, False), (201, False), (401, False)])
def test_write_check_requires_403(status, ok):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, json.loads(request.content)))
        return httpx.Response(status, json={})

    result, _ = check_write_forbidden(settings_for(), transport=httpx.MockTransport(handler))
    assert result is ok
    assert seen == [("POST", {})]
