from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from typing import Any

import httpx

from baby_dash.bb_client import DATA_ENDPOINTS, BabyBuddyError, ReadOnlyBabyBuddy
from baby_dash.config import ConfigError, Settings


def _settings() -> Settings:
    try:
        return Settings.from_env(os.environ)
    except ConfigError as e:
        sys.exit(f"config error: {e}")


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    _settings()
    uvicorn.run("baby_dash.app:create_app_from_env", factory=True, host=args.host, port=args.port,
                proxy_headers=True, log_level="info")


def check_write_forbidden(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[bool, str]:
    """POST an empty body. Permissions are checked before validation, so a read-only token gets 403
    and a writable one gets 400 (invalid body). No record can be created either way."""
    with httpx.Client(transport=transport, timeout=10) as http:
        response = http.post(f"{settings.bb_url}/api/feedings/", json={},
                             headers={"Authorization": f"Token {settings.bb_token}"})
    if response.status_code == 403:
        return True, "POST /api/feedings/ → 403: token cannot write"
    return False, f"POST /api/feedings/ → {response.status_code}: TOKEN CAN WRITE (or is wrong); use a read-only user"


def check_token(_: argparse.Namespace) -> None:
    ok, message = check_write_forbidden(_settings())
    print(("[ok] " if ok else "[FAIL] ") + message)
    sys.exit(0 if ok else 1)


async def _verify(settings: Settings) -> bool:
    client = ReadOnlyBabyBuddy(settings.bb_url, settings.bb_token)
    raw = httpx.AsyncClient(base_url=settings.bb_url, headers={"Authorization": f"Token {settings.bb_token}"},
                            timeout=15)
    all_ok = True

    def report(ok: bool | None, text: str) -> None:
        nonlocal all_ok
        all_ok = all_ok and ok is not False
        print({True: "[ok]  ", False: "[FAIL]", None: "[info]"}[ok], text)

    try:
        print(f"Baby Buddy API at {settings.bb_url}\n")
        try:
            children = await client.children()
            report(True, f"token accepted; children: {[(c['slug'], c['id']) for c in children]}")
        except BabyBuddyError as e:
            report(False, str(e))
            return False

        report(*check_write_forbidden(settings))

        for path in DATA_ENDPOINTS:
            r = await raw.options(path)
            filters = r.json().get("filters") if r.status_code == 200 else None
            report(None, f"OPTIONS {path} → {r.status_code}" + (f", filters: {filters}" if filters else
                   " (expected 403 for a read-only user: Baby Buddy maps OPTIONS to the add permission)"))

        schema = await _schema(raw)
        report(None, f"GET /api/schema/ → {schema.get('_status', 'unreachable')}")
        found = await client.discover_filters()
        for path in DATA_ENDPOINTS:
            names = sorted(found.get(path, set()))
            usable = {"child", "end_min"} <= set(names)
            report(True if usable else None,
                   f"{path} filters: {names or 'none found'} (via {client.filter_source.get(path)}) → " +
                   ("server-side child + end_min filtering will be used" if usable else "will fetch everything"))

        enum = _method_enum(schema)
        report(None, f"feeding method enum in schema: {enum or 'not exposed'}")
        feedings = await client.get_all("/api/feedings/", {"limit": 500})
        report(None, f"feeding methods seen in data: {dict(Counter(f.get('method') for f in feedings))}")

        timers = await client.get_all("/api/timers/")
        fields = sorted({k for t in timers for k in t})
        report(None, f"running timers: {len(timers)}; fields: {fields or 'n/a (no timer running)'}; "
                     f"names: {[t.get('name') for t in timers]}")
        report(None, "timers carry no type; sleep is detected by name matching "
                     f"SLEEP_TIMER_REGEX={settings.sleep_timer_pattern.pattern!r}")
        slug = children[0]["slug"] if len(children) == 1 else (settings.child_slug or "<slug>")
        base = settings.bb_public_url
        report(None, f"deep links (open on your phone to confirm): {base}/feedings/add/?child={slug} · "
                     f"{base}/sleep/add/?child={slug} · {base}/timers/")
    finally:
        await client.aclose()
        await raw.aclose()
    return all_ok


async def _schema(raw: httpx.AsyncClient) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for path in ("/api/schema/", "/api/schema"):
        try:
            r = await raw.get(path, headers={"Accept": "application/vnd.oai.openapi+json"})
            if r.status_code == 200:
                return {**r.json(), "_status": 200}
            last = {"_status": f"{r.status_code} {r.headers.get('content-type', '')}"}
        except (httpx.HTTPError, ValueError):
            pass
    return last


def _method_enum(schema: dict[str, Any]) -> list[str] | None:
    props = schema.get("components", {}).get("schemas", {}).get("Feeding", {}).get("properties", {})
    if "method" in props:
        return props["method"].get("enum")
    get = schema.get("paths", {}).get("/api/feedings/", {}).get("get", {})
    for p in get.get("parameters", []):
        if p.get("name") == "method":
            return p.get("schema", {}).get("enum")
    return None


def verify(_: argparse.Namespace) -> None:
    sys.exit(0 if asyncio.run(_verify(_settings())) else 1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="baby-dash")
    sub = parser.add_subparsers(required=True)
    p_serve = sub.add_parser("serve", help="run the dashboard")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.set_defaults(func=serve)
    sub.add_parser("verify", help="check the live Baby Buddy instance against the spec's assumptions").set_defaults(func=verify)
    sub.add_parser("check-token", help="confirm the token cannot write (expects 403)").set_defaults(func=check_token)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
