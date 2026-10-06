from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from baby_dash.config import ConfigError

DATA_ENDPOINTS = ("/api/feedings/", "/api/sleep/")
SCHEMA_ACCEPT = "application/vnd.oai.openapi+json, application/json;q=0.9"
PROBE_FAR_FUTURE = "2999-01-01T00:00:00+00:00"
PROBE_MISSING_CHILD = 0
REJECTED = -1


class BabyBuddyError(Exception):
    pass


@dataclass(frozen=True)
class Child:
    id: int
    slug: str
    name: str


@dataclass(frozen=True)
class RawData:
    feedings: list[dict[str, Any]]
    sleeps: list[dict[str, Any]]
    timers: list[dict[str, Any]]


def pick_child(children: list[dict[str, Any]], slug: str | None) -> Child:
    found = [
        Child(id=int(c["id"]), slug=c["slug"], name=" ".join(filter(None, [c.get("first_name"), c.get("last_name")])))
        for c in children
    ]
    slugs = ", ".join(c.slug for c in found) or "none"
    if slug:
        match = [c for c in found if c.slug == slug]
        if not match:
            raise ConfigError(f"CHILD_SLUG={slug!r} not found in Baby Buddy (children: {slugs})")
        return match[0]
    if len(found) != 1:
        raise ConfigError(f"Set CHILD_SLUG: Baby Buddy has {len(found)} children (slugs: {slugs}); expected exactly one")
    return found[0]


class ReadOnlyBabyBuddy:
    """Only ever issues GET (data) and OPTIONS (metadata). There is no write path."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        page_size: int = 500,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            transport=transport,
            timeout=timeout,
        )
        self._page_size = page_size
        self.filter_source: dict[str, str] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise BabyBuddyError(f"Baby Buddy unreachable: {e.__class__.__name__}: {e}") from e
        if response.status_code != 200:
            raise BabyBuddyError(f"GET {path} returned HTTP {response.status_code}")
        try:
            return response.json()
        except json.JSONDecodeError as e:
            raise BabyBuddyError(f"GET {path} did not return JSON") from e

    async def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._get(path, {**(params or {}), "limit": self._page_size, "offset": offset})
            batch = page.get("results", [])
            results.extend(batch)
            if not page.get("next") or not batch:
                return results
            offset += len(batch)

    async def children(self) -> list[dict[str, Any]]:
        return await self.get_all("/api/children/")

    async def discover_filters(self) -> dict[str, set[str]]:
        """Confirm filter names before relying on them.

        OPTIONS lists filters but Baby Buddy maps OPTIONS to the *add* permission, so a read-only
        token gets 403 there; the OpenAPI schema lists them too but 500s on some versions. Last resort:
        probe each filter with values whose effect is unambiguous.
        """
        schema = await self._filters_from_schema()
        found: dict[str, set[str]] = {}
        for path in DATA_ENDPOINTS:
            if schema.get(path):
                found[path], self.filter_source[path] = schema[path], "schema"
            elif names := await self._filters_from_options(path):
                found[path], self.filter_source[path] = names, "options"
            else:
                found[path], self.filter_source[path] = await self._filters_from_probe(path), "probe"
        return found

    async def _filters_from_schema(self) -> dict[str, set[str]]:
        for path in ("/api/schema/", "/api/schema"):
            try:
                response = await self._http.get(path, headers={"Accept": SCHEMA_ACCEPT})
                schema = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, json.JSONDecodeError):
                schema = None
            if isinstance(schema, dict) and "paths" in schema:
                return {
                    p: {param["name"] for param in schema["paths"][p].get("get", {}).get("parameters", [])}
                    for p in DATA_ENDPOINTS
                    if p in schema["paths"]
                }
        return {}

    async def _filters_from_options(self, path: str) -> set[str]:
        try:
            response = await self._http.options(path)
            if response.status_code == 200:
                return set(response.json().get("filters", []))
        except (httpx.HTTPError, json.JSONDecodeError):
            pass
        return set()

    async def _filters_from_probe(self, path: str) -> set[str]:
        async def count(params: dict[str, Any]) -> int | None:
            try:
                response = await self._http.get(path, params={**params, "limit": 1})
                if response.status_code == 400:
                    return REJECTED
                return int(response.json()["count"]) if response.status_code == 200 else None
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                return None

        total = await count({})
        if not total or total < 0:
            return set()
        confirmed = set()
        if await count({"end_min": PROBE_FAR_FUTURE}) == 0:
            confirmed.add("end_min")
        if await count({"child": PROBE_MISSING_CHILD}) in (0, REJECTED):
            confirmed.add("child")
        return confirmed

    async def fetch(self, child: Child, since: datetime, filters: dict[str, set[str]]) -> RawData:
        def params_for(path: str) -> dict[str, Any]:
            supported = filters.get(path, set())
            params: dict[str, Any] = {}
            if "child" in supported:
                params["child"] = child.id
            if "end_min" in supported:
                params["end_min"] = since.isoformat()
            return params

        feedings, sleeps, timers = await asyncio.gather(
            self.get_all("/api/feedings/", params_for("/api/feedings/")),
            self.get_all("/api/sleep/", params_for("/api/sleep/")),
            self.get_all("/api/timers/"),
        )
        return RawData(feedings=feedings, sleeps=sleeps, timers=timers)
