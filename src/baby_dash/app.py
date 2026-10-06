from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from baby_dash import views
from baby_dash.bb_client import ReadOnlyBabyBuddy
from baby_dash.config import Settings
from baby_dash.store import Clock, Store, StoreState, utc_now

STATIC = Path(__file__).parent / "static"


def create_app(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = utc_now,
) -> FastAPI:
    client = ReadOnlyBabyBuddy(settings.bb_url, settings.bb_token, transport=transport)
    store = Store(client, settings, clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.startup()
        yield
        await client.aclose()

    app = FastAPI(title="baby-dash", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def respond(state: StoreState, build, on_error: dict[str, Any] | None = None) -> JSONResponse:
        now = clock()
        body: dict[str, Any] = views.meta(state.snapshot, settings, now, state.stale_since)
        if state.snapshot is None:
            body.update(error=state.error or "No data yet", **(on_error or {}))
            return JSONResponse(body, status_code=503)
        body.update(build(state.snapshot, now))
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/now")
    async def now_endpoint() -> JSONResponse:
        return respond(
            await store.state(),
            lambda snap, now: views.now_payload(snap, settings, now),
            on_error={"links": views.links(settings, None)},
        )

    @app.get("/api/actogram")
    async def actogram(days: int = Query(7)) -> JSONResponse:
        allowed(days, (7, 14))
        return respond(await store.state(), lambda snap, now: views.actogram_payload(snap, settings, now, days))

    @app.get("/api/trends")
    async def trends(days: int = Query(14)) -> JSONResponse:
        allowed(days, (14, 30))
        return respond(await store.state(), lambda snap, now: views.trends_payload(snap, settings, now, days))

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        state = await store.state()
        ok = store.healthy(clock())
        fetched = state.snapshot.fetched_at.isoformat() if state.snapshot else None
        return JSONResponse({"ok": ok, "last_fetch": fetched, "error": state.error}, status_code=200 if ok else 503)

    return app


def allowed(days: int, choices: tuple[int, ...]) -> None:
    if days not in choices:
        raise HTTPException(422, f"days must be one of {', '.join(map(str, choices))}")


def create_app_from_env() -> FastAPI:
    return create_app(Settings.from_env(os.environ))
