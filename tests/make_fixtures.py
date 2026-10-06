"""Regenerate tests/fixtures/*.json: `uv run python tests/make_fixtures.py`."""
from __future__ import annotations

import json
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from builders import at, feeding, page, sleep, timer

OUT = Path(__file__).parent / "fixtures"
CHILD = {"id": 1, "first_name": "Alix", "last_name": "Test", "birth_date": "2026-08-20",
         "birth_time": None, "slug": "alix-test", "picture": None}


def write(name: str, feedings=(), sleeps=(), timers=(), children=(CHILD,)) -> None:
    body = {"children": page(list(children)), "feedings": page(list(feedings)),
            "sleep": page(list(sleeps)), "timers": page(list(timers))}
    (OUT / f"{name}.json").write_text(json.dumps(body, indent=1) + "\n")


def dst_fall_2026() -> None:
    write(
        "dst_fall_2026",
        feedings=[
            feeding("2026-10-24T19:00:00+02:00", "2026-10-24T19:20:00+02:00"),
            feeding("2026-10-24T23:30:00+02:00", "2026-10-24T23:50:00+02:00", "right breast"),
            feeding("2026-10-25T02:30:00+02:00", method="bottle", amount=90),
            feeding("2026-10-25T02:30:00+01:00", method="bottle", amount=60),
            feeding("2026-10-25T06:30:00+01:00", "2026-10-25T06:45:00+01:00"),
            feeding("2026-10-25T10:00:00+01:00", "2026-10-25T10:15:00+01:00"),
            feeding("2026-10-25T13:30:00+01:00", "2026-10-25T13:45:00+01:00"),
            feeding("2026-10-25T17:00:00+01:00", "2026-10-25T17:15:00+01:00"),
            feeding("2026-10-25T20:30:00+01:00", "2026-10-25T20:45:00+01:00"),
        ],
        sleeps=[
            sleep("2026-10-24T20:00:00+02:00", "2026-10-24T23:15:00+02:00"),
            sleep("2026-10-25T00:00:00+02:00", "2026-10-25T02:15:00+02:00"),
            sleep("2026-10-25T02:45:00+01:00", "2026-10-25T06:15:00+01:00"),
            sleep("2026-10-25T11:00:00+01:00", "2026-10-25T12:30:00+01:00"),
            sleep("2026-10-25T17:30:00+01:00", "2026-10-25T18:30:00+01:00"),
            sleep("2026-10-25T21:00:00+01:00", "2026-10-26T01:00:00+01:00"),
        ],
    )


def edge_cases() -> None:
    dup = feeding("2026-10-05 09:00", "2026-10-05 09:15")
    write(
        "edge_cases",
        feedings=[
            feeding("2026-10-05 06:00", method="bottle", amount=120),
            dup, dict(dup, id=dup["id"] + 10_000),
            feeding("2026-10-05 09:05", "2026-10-05 09:20", "right breast"),
            feeding("2026-10-05 12:30"),
            feeding("2026-10-05 15:30", method="bottle"),
            feeding("2026-10-05 17:45", "2026-10-05 18:10", "both breasts"),
            feeding("2026-10-05 23:50", "2026-10-06 00:10", "left breast"),
            feeding("2026-10-06 03:30", method="parent fed"),
            feeding("2026-10-06 04:00", method="bottle", amount=100),
            feeding("2026-10-06 08:00", method="bottle", amount=110),
            feeding("2026-10-06 11:00", "2026-10-06 11:20"),
        ],
        sleeps=[
            sleep("2026-10-05 10:00", "2026-10-05 11:30"),
            sleep("2026-10-05 13:00", "2026-10-05 14:00"),
            sleep("2026-10-05 14:03", "2026-10-05 15:00"),
            sleep("2026-10-05 17:00", "2026-10-05 19:00"),
            sleep("2026-10-05 20:30", "2026-10-06 03:00"),
            sleep("2026-10-06 04:30", "2026-10-06 07:30"),
            sleep("2026-10-06 09:00", "2026-10-06 10:00"),
        ],
        timers=[timer("2026-10-06 12:10", "Sleep"), timer("2026-10-06 11:55", None, child=None)],
    )


def small_cases() -> None:
    write("empty")
    write("single_entry", feedings=[feeding("2026-10-06 09:00", method="bottle", amount=80)])
    write("breast_only", feedings=[feeding(f"2026-10-0{d} {h:02d}:00", f"2026-10-0{d} {h:02d}:20")
                                   for d in (4, 5, 6) for h in (2, 6, 9, 12, 15, 18, 21) if (d, h) < (6, 12)])
    write("bottle_only", feedings=[feeding(f"2026-10-0{d} {h:02d}:00", method="bottle", amount=90 + h)
                                   for d in (4, 5, 6) for h in (1, 5, 9, 13, 17, 21) if (d, h) < (6, 12)])
    write("two_children", children=[CHILD, dict(CHILD, id=2, slug="sam-test", first_name="Sam")])


def realistic_10d() -> None:
    rng = random.Random(20261006)
    start, now = at("2026-09-27 07:00"), at("2026-10-06 14:00")
    feeds, sleeps = [], []
    lapse = (at("2026-10-02 09:30"), at("2026-10-02 16:30"))

    def night(t: datetime) -> bool:
        return t.time() >= time(20) or t.time() < time(7)

    def add_feed(t: datetime) -> datetime:
        if lapse[0] <= t <= lapse[1]:
            return t + timedelta(minutes=20)
        roll = rng.random()
        if roll < 0.15:
            feeds.append(feeding(t.isoformat(), method="bottle", amount=rng.choice([90, 100, 120, 130, 150])))
            return t + timedelta(minutes=10)
        side = rng.choice(["left breast", "right breast"])
        first = rng.randint(8, 18)
        end = t + timedelta(minutes=first)
        feeds.append(feeding(t.isoformat(), end.isoformat(), side))
        if rng.random() < 0.6:
            s2 = end + timedelta(minutes=rng.randint(1, 5))
            end = s2 + timedelta(minutes=rng.randint(5, 12))
            other = "right breast" if side == "left breast" else "left breast"
            feeds.append(feeding(s2.isoformat(), end.isoformat(), other))
        if not night(t) and rng.random() < 0.2:
            top = end + timedelta(minutes=rng.randint(5, 15))
            amount = None if rng.random() < 0.15 else rng.choice([30, 40, 60])
            feeds.append(feeding(top.isoformat(), method="bottle", amount=amount))
            end = top
        return end

    def add_sleep(s: datetime, e: datetime) -> None:
        if lapse[0] <= s <= lapse[1] or lapse[0] <= e <= lapse[1]:
            return
        if rng.random() < 0.1 and (e - s) > timedelta(minutes=60):
            mid = s + (e - s) / 2
            sleeps.append(sleep(s.isoformat(), mid.isoformat()))
            sleeps.append(sleep((mid + timedelta(minutes=3)).isoformat(), e.isoformat()))
        else:
            sleeps.append(sleep(s.isoformat(), e.isoformat()))

    t = start
    while t < now:
        fed_until = add_feed(t)
        if night(t):
            s = fed_until + timedelta(minutes=rng.randint(10, 30))
            e = s + timedelta(minutes=rng.randint(150, 240))
        else:
            s = t + timedelta(minutes=rng.randint(60, 105))
            if time(19, 15) <= s.time() <= time(21, 0):
                e = s + timedelta(minutes=rng.randint(180, 260))
            else:
                e = s + timedelta(minutes=rng.randint(30, 120))
        if e > now:
            break
        add_sleep(s, e)
        t = e + timedelta(minutes=rng.randint(0, 15))

    feeds.append(dict(feeds[40], id=feeds[40]["id"] + 50_000))
    write("realistic_10d", feedings=feeds, sleeps=sleeps)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    dst_fall_2026()
    edge_cases()
    small_cases()
    realistic_10d()
    for f in sorted(OUT.glob("*.json")):
        data = json.loads(f.read_text())
        print(f"{f.name}: {len(data['feedings']['results'])} feedings, {len(data['sleep']['results'])} sleeps")
