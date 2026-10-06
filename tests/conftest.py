from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from builders import TZ, at, feeding, page, sleep, timer  # noqa: F401

from baby_dash.config import Rules
from baby_dash.normalize import normalize_feedings, normalize_sleeps

FIXTURES = Path(__file__).parent / "fixtures"
SLEEP_PATTERN = re.compile(r"sleep|nap|sieste|dodo", re.IGNORECASE)


def load_fixture(name: str) -> dict[str, list[dict]]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return {k: v["results"] for k, v in data.items()}


@pytest.fixture
def rules() -> Rules:
    return Rules()


def feeds_of(rows: list[dict], rules: Rules | None = None):
    return normalize_feedings(rows, rules or Rules())[0]


def sleeps_of(rows: list[dict], rules: Rules | None = None):
    return normalize_sleeps(rows, rules or Rules())[0]


def h(td: timedelta) -> float:
    return td.total_seconds() / 3600


def settings_for(**env: str):
    from baby_dash.config import Settings

    return Settings.from_env({"BB_PUBLIC_URL": "https://baby.example.ts.net", "BB_TOKEN": "t", **env})


def snapshot_of(name: str, now, settings=None):
    from baby_dash.bb_client import pick_child
    from baby_dash.normalize import normalize
    from baby_dash.store import Snapshot, derive

    settings = settings or settings_for()
    raw = load_fixture(name)
    child = pick_child(raw["children"][:1], None)
    since = now - timedelta(days=settings.fetch_days)
    data = normalize(raw["feedings"], raw["sleep"], raw["timers"], settings.rules,
                     settings.sleep_timer_pattern, child_id=child.id, since=since)
    return Snapshot(child=child, data=data, derived=derive(data, settings.rules), fetched_at=now)
