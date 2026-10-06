from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

import polars as pl

from baby_dash import metrics
from baby_dash.bb_client import BabyBuddyError, Child, ReadOnlyBabyBuddy, pick_child
from baby_dash.config import ConfigError, Rules, Settings
from baby_dash.normalize import Normalized, normalize

log = logging.getLogger("baby_dash")
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Derived:
    episodes: pl.DataFrame
    sleeps: pl.DataFrame
    suspects: pl.DataFrame
    feed_gaps: pl.DataFrame
    wake_windows: pl.DataFrame


def derive(data: Normalized, rules: Rules) -> Derived:
    episodes = metrics.feed_episodes(data.feeds, rules)
    sleeps = metrics.merge_sleeps(data.sleeps, rules)
    suspects = metrics.suspect_intervals(data.feeds, sleeps, rules)
    return Derived(
        episodes=episodes,
        sleeps=sleeps,
        suspects=suspects,
        feed_gaps=metrics.mark_suspect(metrics.feed_gaps(episodes, rules), suspects),
        wake_windows=metrics.mark_suspect(metrics.wake_windows(sleeps, rules), suspects),
    )


@dataclass(frozen=True)
class Snapshot:
    child: Child
    data: Normalized
    derived: Derived
    fetched_at: datetime


@dataclass(frozen=True)
class StoreState:
    snapshot: Snapshot | None
    stale_since: datetime | None
    error: str | None


class Store:
    def __init__(self, client: ReadOnlyBabyBuddy, settings: Settings, clock: Clock = utc_now) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self._child: Child | None = None
        self._filters: dict[str, set[str]] | None = None
        self._snapshot: Snapshot | None = None
        self._last_failure: datetime | None = None
        self._error: str | None = None

    @property
    def ttl(self) -> timedelta:
        return timedelta(seconds=self._settings.cache_ttl_s)

    async def startup(self) -> None:
        """Fail fast on an ambiguous child; tolerate an unreachable Baby Buddy."""
        try:
            await self._resolve()
        except BabyBuddyError as e:
            log.warning("Baby Buddy not reachable at startup, will retry on first request: %s", e)

    async def _resolve(self) -> Child:
        if self._child is None:
            self._child = pick_child(await self._client.children(), self._settings.child_slug)
            log.info("Using child %s (id %s)", self._child.slug, self._child.id)
        if self._filters is None:
            self._filters = await self._client.discover_filters()
            log.info("Confirmed API filters: %s (via %s)", {k: sorted(v) for k, v in self._filters.items()},
                     self._client.filter_source)
        return self._child

    async def _refresh(self, now: datetime) -> Snapshot:
        child = await self._resolve()
        since = now - timedelta(days=self._settings.fetch_days)
        raw = await self._client.fetch(child, since, self._filters or {})
        rules = self._settings.rules
        data = normalize(raw.feedings, raw.sleeps, raw.timers, rules,
                         self._settings.sleep_timer_pattern, child_id=child.id, since=since)
        return Snapshot(child=child, data=data, derived=derive(data, rules), fetched_at=now)

    async def state(self) -> StoreState:
        async with self._lock:
            now = self._clock()
            fresh = self._snapshot is not None and now - self._snapshot.fetched_at < self.ttl
            backing_off = self._last_failure is not None and now - self._last_failure < self.ttl
            if not fresh and not backing_off:
                try:
                    self._snapshot = await self._refresh(now)
                    self._last_failure, self._error = None, None
                except (BabyBuddyError, ConfigError) as e:
                    log.warning("Refresh failed: %s", e)
                    self._last_failure, self._error = now, str(e)
            stale_since = self._snapshot.fetched_at if self._snapshot and self._last_failure else None
            return StoreState(snapshot=self._snapshot, stale_since=stale_since, error=self._error)

    def healthy(self, now: datetime) -> bool:
        return self._snapshot is not None and now - self._snapshot.fetched_at <= 3 * self.ttl
