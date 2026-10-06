from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from conftest import at, load_fixture, settings_for
from fastapi.testclient import TestClient

from baby_dash.app import create_app
from baby_dash.bb_client import ReadOnlyBabyBuddy
from baby_dash.config import ConfigError

ENDPOINTS = {"/api/children/": "children", "/api/feedings/": "feedings", "/api/sleep/": "sleep",
             "/api/timers/": "timers"}
FILTERS = ["child", "end", "end_max", "end_min", "start", "start_max", "start_min", "tags"]


class FakeBabyBuddy:
    def __init__(self, fixture: str, *, schema: bool = True) -> None:
        self.data = load_fixture(fixture)
        self.schema = schema
        self.up = True
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append((request.method, request.url.path, params))
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        if request.headers.get("Authorization") != "Token t":
            return httpx.Response(401, json={"detail": "auth"})
        if request.method == "OPTIONS":
            return httpx.Response(403, json={"detail": "You do not have permission"})
        if request.method != "GET":
            return httpx.Response(403, json={"detail": "You do not have permission"})
        if request.url.path == "/api/schema/":
            if not self.schema:
                return httpx.Response(404)
            params_list = [{"name": n, "in": "query"} for n in FILTERS]
            return httpx.Response(200, json={"openapi": "3.0.2", "paths": {
                p: {"get": {"parameters": params_list}} for p in ("/api/feedings/", "/api/sleep/")}})
        if request.url.path not in ENDPOINTS:
            return httpx.Response(404, text="Not found")
        items = list(self.data[ENDPOINTS[request.url.path]])
        if "child" in params:
            items = [i for i in items if i.get("child") == int(params["child"])]
        if "end_min" in params:
            cutoff = datetime.fromisoformat(params["end_min"])
            items = [i for i in items if datetime.fromisoformat(i["end"]) >= cutoff]
        limit, offset = int(params.get("limit", 100)), int(params.get("offset", 0))
        chunk = items[offset:offset + limit]
        more = offset + limit < len(items)
        return httpx.Response(200, json={
            "count": len(items), "previous": None, "results": chunk,
            "next": f"http://babybuddy:8000{request.url.path}?limit={limit}&offset={offset + limit}" if more else None,
        })


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(fixture="realistic_10d", now="2026-10-06 14:00", **kw):
    bb, clock = FakeBabyBuddy(fixture, **kw), Clock(at(now))
    app = create_app(settings_for(), transport=httpx.MockTransport(bb), clock=clock)
    return app, bb, clock


def test_every_endpoint_returns_valid_json():
    app, bb, _ = make()
    with TestClient(app) as c:
        assert "<title>" in c.get("/").text
        for url in ("/api/now", "/api/actogram?days=7", "/api/actogram?days=14", "/api/trends?days=14",
                    "/api/trends?days=30", "/healthz"):
            r = c.get(url)
            assert r.status_code == 200, url
            assert isinstance(r.json(), dict)
        assert c.get("/api/actogram?days=9").status_code == 422
        assert c.get("/static/app.js").status_code == 200


def test_one_fetch_serves_all_endpoints_within_ttl():
    app, bb, clock = make()
    with TestClient(app) as c:
        c.get("/api/now")
        c.get("/api/actogram")
        c.get("/api/trends")
        assert sum(1 for m, p, _ in bb.calls if p == "/api/feedings/") == 1
        clock.now += timedelta(seconds=61)
        c.get("/api/now")
        assert sum(1 for m, p, _ in bb.calls if p == "/api/feedings/") == 2


def test_confirmed_filters_are_sent_and_unconfirmed_are_not():
    app, bb, _ = make()
    with TestClient(app) as c:
        c.get("/api/now")
    feed_call = next(params for m, p, params in bb.calls if p == "/api/feedings/")
    assert feed_call["child"] == "1" and "end_min" in feed_call

    app, bb, _ = make(schema=False)
    with TestClient(app) as c:
        assert c.get("/api/now").status_code == 200
    feed_call = next(params for m, p, params in bb.calls if p == "/api/feedings/")
    assert "child" not in feed_call and "end_min" not in feed_call


def test_never_sends_a_write_method():
    app, bb, clock = make()
    with TestClient(app) as c:
        for _ in range(3):
            c.get("/api/now")
            c.get("/api/trends?days=30")
            clock.now += timedelta(minutes=5)
    assert {m for m, _, _ in bb.calls} <= {"GET", "OPTIONS"}
    assert not any(hasattr(ReadOnlyBabyBuddy, name) for name in ("post", "put", "patch", "delete"))


def test_unreachable_serves_stale_cache_then_recovers():
    app, bb, clock = make()
    with TestClient(app) as c:
        assert c.get("/api/now").json()["stale_since"] is None
        bb.up = False
        clock.now += timedelta(seconds=90)
        r = c.get("/api/now")
        assert r.status_code == 200
        assert r.json()["stale_since_label"] == "14:00"
        assert c.get("/api/actogram").json()["stale_since_label"] == "14:00"
        clock.now += timedelta(minutes=5)
        assert c.get("/healthz").status_code == 503
        bb.up = True
        clock.now += timedelta(seconds=61)
        assert c.get("/api/now").json()["stale_since"] is None
        assert c.get("/healthz").status_code == 200


def test_unreachable_without_cache_is_an_error_state():
    app, bb, _ = make()
    bb.up = False
    with TestClient(app) as c:
        r = c.get("/api/now")
        assert r.status_code == 503
        body = r.json()
        assert "unreachable" in body["error"]
        assert body["links"]["timers"].endswith("/timers/")
        assert c.get("/healthz").status_code == 503


def test_ambiguous_child_fails_at_startup():
    app, _, _ = make("two_children")
    with pytest.raises(ConfigError, match="CHILD_SLUG"):
        with TestClient(app):
            pass


def test_empty_instance_renders():
    app, _, _ = make("empty")
    with TestClient(app) as c:
        assert c.get("/api/now").json()["state"]["kind"] == "unknown"
        assert c.get("/api/trends").status_code == 200


def test_client_paginates_until_next_is_null():
    bb = FakeBabyBuddy("realistic_10d")
    client = ReadOnlyBabyBuddy("http://babybuddy:8000", "t", transport=httpx.MockTransport(bb), page_size=25)
    feeds = asyncio.run(client.get_all("/api/feedings/"))
    assert len(feeds) == len(bb.data["feedings"])
    assert [p["offset"] for _, path, p in bb.calls] == ["0", "25", "50", "75", "100"]
