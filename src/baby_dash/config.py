from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Rules:
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Paris"))
    row_start: time = time(18, 0)
    night_start: time = time(20, 0)
    night_end: time = time(7, 0)
    feed_merge: timedelta = timedelta(minutes=20)
    sleep_merge: timedelta = timedelta(minutes=5)
    baseline_days: int = 3
    min_baseline_n: int = 6
    suspect_day_gap: timedelta = timedelta(hours=6)
    suspect_night_gap: timedelta = timedelta(hours=8)


@dataclass(frozen=True)
class Settings:
    bb_url: str
    bb_public_url: str
    bb_token: str
    child_slug: str | None
    rules: Rules
    cache_ttl_s: int = 60
    amount_unit: str = "ml"
    sleep_timer_pattern: re.Pattern[str] = re.compile(r"sleep|nap|sieste|dodo", re.IGNORECASE)
    history_days: int = 30

    @property
    def fetch_days(self) -> int:
        return self.history_days + max(self.rules.baseline_days, 4) + 1

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        missing = [k for k in ("BB_PUBLIC_URL", "BB_TOKEN") if not env.get(k)]
        if missing:
            raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")
        tz_name = env.get("TZ", "Europe/Paris")
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ConfigError(f"TZ={tz_name!r} is not a known time zone") from e
        rules = Rules(
            tz=tz,
            row_start=_parse_time(env, "ROW_START", "18:00"),
            night_start=_parse_time(env, "NIGHT_START", "20:00"),
            night_end=_parse_time(env, "NIGHT_END", "07:00"),
            feed_merge=timedelta(minutes=_parse_int(env, "FEED_MERGE_MIN", 20)),
            sleep_merge=timedelta(minutes=_parse_int(env, "SLEEP_MERGE_MIN", 5)),
            baseline_days=_parse_int(env, "BASELINE_DAYS", 3, minimum=1),
            min_baseline_n=_parse_int(env, "MIN_BASELINE_N", 6, minimum=1),
        )
        if rules.night_start == rules.night_end:
            raise ConfigError("NIGHT_START and NIGHT_END must differ")
        try:
            pattern = re.compile(env.get("SLEEP_TIMER_REGEX", r"sleep|nap|sieste|dodo"), re.IGNORECASE)
        except re.error as e:
            raise ConfigError(f"SLEEP_TIMER_REGEX is not a valid regex: {e}") from e
        return cls(
            bb_url=env.get("BB_URL", "http://babybuddy:8000").rstrip("/"),
            bb_public_url=env["BB_PUBLIC_URL"].rstrip("/"),
            bb_token=env["BB_TOKEN"],
            child_slug=env.get("CHILD_SLUG") or None,
            rules=rules,
            cache_ttl_s=_parse_int(env, "CACHE_TTL_S", 60, minimum=1),
            amount_unit=env.get("AMOUNT_UNIT", "ml"),
            sleep_timer_pattern=pattern,
        )


def _parse_time(env: Mapping[str, str], key: str, default: str) -> time:
    raw = env.get(key, default)
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except ValueError as e:
        raise ConfigError(f"{key}={raw!r} must be HH:MM") from e


def _parse_int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"{key}={raw!r} must be an integer") from e
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    return value
