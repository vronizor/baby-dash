from __future__ import annotations

import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
_ids = itertools.count(1)


def at(value: str) -> datetime:
    if "+" in value[10:] or value.endswith("Z"):
        return datetime.fromisoformat(value).astimezone(TZ)
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def feeding(start: str, end: str | None = None, method: str = "left breast", amount: float | None = None,
            child: int = 1, type: str | None = None) -> dict:
    s, e = at(start), at(end or start)
    if type is None:
        type = "formula" if method == "bottle" else "breast milk"
    return {
        "id": next(_ids), "child": child, "start": s.isoformat(), "end": e.isoformat(),
        "duration": _hms((e - s).total_seconds()), "type": type, "method": method,
        "amount": amount, "notes": None, "tags": [],
    }


def sleep(start: str, end: str, child: int = 1) -> dict:
    s, e = at(start), at(end)
    return {
        "id": next(_ids), "child": child, "start": s.isoformat(), "end": e.isoformat(),
        "duration": _hms((e - s).total_seconds()), "nap": None, "notes": None, "tags": [],
    }


def timer(start: str, name: str | None = None, child: int | None = 1) -> dict:
    return {"id": next(_ids), "child": child, "name": name, "start": at(start).isoformat(),
            "duration": "00:10:00", "user": 1}


def page(results: list[dict]) -> dict:
    return {"count": len(results), "next": None, "previous": None, "results": results}
