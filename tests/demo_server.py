"""Serve the dashboard against fixture data: `uv run python tests/demo_server.py [fixture] [now]`."""
from __future__ import annotations

import os
import sys

import httpx
import uvicorn
from conftest import at, settings_for
from test_app_smoke import FakeBabyBuddy

from baby_dash.app import create_app

if __name__ == "__main__":
    fixture = sys.argv[1] if len(sys.argv) > 1 else "realistic_10d"
    pinned = at(sys.argv[2] if len(sys.argv) > 2 else "2026-10-06 14:00")
    app = create_app(settings_for(), transport=httpx.MockTransport(FakeBabyBuddy(fixture)), clock=lambda: pinned)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
