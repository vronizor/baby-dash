# Baby dashboard — v1 spec

A read-only, mobile-first dashboard on top of an existing Baby Buddy instance. It answers two questions:

1. **Rhythm:** are feeding and sleep patterns settling into something sensible over days and weeks?
2. **Now:** is a feed or a nap about due?

It replaces Baby Buddy's "Sleep Pattern" and "Feeding Pattern" Plotly views, which fail on phones. Their specific failures: one skinny column per day with half-hour rows stretched vertically, days impossible to compare, nights cut at midnight, and instant-logged feeds drawn as invisible hairlines because feeds are drawn by duration.

## Non-goals (v1)

- No data entry. The only "input" is links that open Baby Buddy's own add forms.
- No notifications, no webhooks, no diaper/pumping/growth views.
- No multi-child UI: one child, configured or auto-detected.
- No predictions ("next feed at 10:42"). Show where *now* sits relative to recent ranges, never a point forecast.

## Working agreements for Claude Code

- All data transformation is done in **polars**. pandas is allowed only as an input format that a third-party library strictly requires, converted at the last moment before the call. Before adding any dependency that pulls in pandas, flag it and propose a polars-native alternative. For v1, the expected count is zero: `uv tree | grep -i pandas` should return nothing.
- Keep code comments to a minimum; names and tests should carry the meaning.
- Be a critical collaborator. If something in this spec is wrong, ambiguous, or more complex than it needs to be, say so before building around it. Ask instead of guessing on anything listed under "Verify before relying on".
- Write the metric functions test-first against fixture data (see Testing). They are the part of this app that can be silently wrong.

## Architecture

```
phone (tailnet) ──► Caddy ──► baby-dash (FastAPI, :8080) ──► babybuddy:8000 /api/*
                                   │
                                   └─ static page + vendored Observable Plot
```

- **New container `baby-dash`**: Python 3.12, `uv`, FastAPI, uvicorn, httpx, polars. Image must run on arm64 (Raspberry Pi).
- **Data access: Baby Buddy REST API only.** Never mount Baby Buddy's `/config` volume or read its SQLite file. Reasons: a stable contract instead of Django internals; no access to the secret key or password hashes; no SQLite lock contention with the app.
- **Network:** `baby-dash` joins Baby Buddy's Docker network and calls it at `http://babybuddy:8000` directly. The token never leaves the Docker host.
- **Auth to Baby Buddy:** the API token of a dedicated **read-only** Baby Buddy user (setup steps below). The dashboard must be unable to write even if buggy.
- **Exposure:** a Caddy site block on the tailnet, same pattern as the other homelab services. No auth on the dashboard itself; tailnet membership is the boundary.
- **Caching:** the server keeps the normalized frames in memory with a TTL (default 60 s). One fetch serves all endpoints. No database, no files.
- **Frontend:** one HTML page, no build step. Observable Plot is vendored into `static/` (pinned version, no CDN at runtime). The server does all computation and returns chart-ready JSON. JS only renders and ticks the clock.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `BB_URL` | `http://babybuddy:8000` | Internal API base |
| `BB_PUBLIC_URL` | required | Tailnet URL of Baby Buddy, used for "add" deep links |
| `BB_TOKEN` | required | Read-only user's API key |
| `CHILD_SLUG` | unset | If unset and exactly one child exists, use it; otherwise fail at startup with a clear message |
| `TZ` | `Europe/Paris` | All display and day-bucketing happens in this zone |
| `ROW_START` | `18:00` | Start of a "baby day". Used for actogram rows **and** all daily totals, so everything shares one boundary |
| `NIGHT_START` / `NIGHT_END` | `20:00` / `07:00` | Fixed night window for day/night splits |
| `FEED_MERGE_MIN` | `20` | Feeds starting within this many minutes of the previous feed's end merge into one episode |
| `SLEEP_MERGE_MIN` | `5` | Sleeps separated by less than this merge into one sleep |
| `BASELINE_DAYS` | `3` | Window for the "usual" ranges on the Now tiles |
| `MIN_BASELINE_N` | `6` | Below this many observations, show "not enough data" instead of a range |
| `CACHE_TTL_S` | `60` | API cache TTL |
| `AMOUNT_UNIT` | `ml` | Label only; Baby Buddy amounts are unitless |

## Ingestion

- Endpoints: `/api/children/`, `/api/feedings/`, `/api/sleep/`, `/api/timers/`.
- Paginate with `limit`/`offset` (e.g. `limit=500`) until `next` is null.
- Fetch window: the longest view (30 days) plus `BASELINE_DAYS`. Use server-side date and child filters **only after confirming their names** via an `OPTIONS` request on each endpoint. If no usable date filter exists, fetch everything; volume is small (months of data is a few hundred KB).
- Normalization (polars):
  - Parse `start`/`end` as tz-aware datetimes and convert to `TZ`.
  - Compute duration as `end - start`. Do not parse the `"HH:MM:SS"` duration strings.
  - Feeding kind from `method`: `bottle` → **bottle**; `left breast` / `right breast` / `both breasts` → **breast** (side ignored in v1); `parent fed` / `self fed` → **other**, hidden in v1 but kept in the frame.
  - Bottle with null `amount` → keep, flagged `amount_missing`.
- Running timers for the child are fetched every refresh. They signal an ongoing activity that isn't yet an entry.

## Derived metrics (precise definitions)

All "day" bucketing uses `ROW_START`. A baby day is labelled by the calendar date of its morning (the 18:00 Oct 5 → 18:00 Oct 6 row is "Oct 6").

**Feed episodes.** Sort feeds by start. A feed whose start is within `FEED_MERGE_MIN` of the previous feed's end joins that episode, e.g. switching sides, or breast followed by a top-up bottle. Episode start = first start. Episode bottle volume = sum of bottle amounts. All interval metrics use episodes, not raw feeds; otherwise a side switch registers as a 5-minute "gap".

**Inter-feed gap.** Time between consecutive episode starts. A gap is tagged night or day by where its *start* falls relative to the night window.

**Sleeps.** Merge per `SLEEP_MERGE_MIN`. Then:
- **Wake window:** time from a sleep's end to the next sleep's start.
- **Longest night stretch:** for each night window, the longest single merged sleep whose midpoint lies inside the window, measured unclipped.
- **Sleep per baby day, day vs night:** sum of each sleep's overlap with the night window vs outside it. Sleeps crossing `ROW_START` are split across rows.

**Usual ranges (Now tiles).** Median, P25 and P75, plus n, over the last `BASELINE_DAYS`:
- feed gaps from the **same period** (day or night) as the current time, since night gaps are structurally longer;
- wake windows, daytime only.

If n < `MIN_BASELINE_N`, return no range and say so.

**Data-quality flag.** A baby day is flagged `suspect_gap` if it contains an interval of more than 6 h (daytime) or 8 h (night) with no feed and no sleep logged. Flagged days are drawn hatched in the actogram and marked in trends. They are **not** silently dropped. Gaps that span a suspect interval are excluded from the usual-range baselines, and the count of exclusions is returned.

## Server endpoints

- `GET /` → the page.
- `GET /api/now` → last feed episode (time, kind, volume), time since; current state (asleep from timer or open sleep, awake since last sleep end, or unknown); usual feed-gap range for the current period; usual wake-window range; n for each; any running timer with its name and start; deep-link URLs.
- `GET /api/actogram?days=7|14` → per row: date label, sleep segments (start/end in hours since `ROW_START`), feed marks (hour, kind, volume, amount_missing), `suspect_gap`, `is_partial` for today.
- `GET /api/trends?days=14|30` → per baby day: longest night stretch, night sleep, day sleep, bottle volume, breastfeed episode count, flags. Plus the list of feed gaps (time of day, length, period) for the gap scatter.
- `GET /healthz` → 200 if the last Baby Buddy fetch succeeded within 3× TTL.

All times returned as ISO strings in `TZ` plus precomputed hour offsets, so the client never does timezone math.

## UI (portrait phone first, 360–430 px wide)

Top to bottom, single scroll, no tabs:

1. **Now** — two tiles side by side:
   - "Since last feed": elapsed time (large), with a meter showing the usual P25–P75 band and a marker for now, and a caption "usual 2h30–3h15 · n=14".
   - "Awake for" (or "Asleep since" when a sleep timer runs): the same pattern with wake windows.
   - Elapsed time ticks client-side every 30 s; data refreshes every 60 s.
   - Below the tiles, a row of buttons: "+ Feeding", "+ Sleep", "Timers", deep-linking into `BB_PUBLIC_URL`.
2. **Actogram** — 7/14-day toggle. One row per baby day, x-axis `ROW_START` → `ROW_START` + 24 h, newest row at the bottom, light shading for the night window. Marks:
   - sleep = blue bars;
   - breast episode = hollow orange ring, fixed size;
   - bottle = filled orange dot with **area** (not radius) proportional to volume;
   - bottle with missing amount = dashed ring;
   - a "now" line on today's row;
   - suspect days hatched.
3. **Trends** — small stacked charts sharing one date x-axis (no dual axes):
   - longest night stretch with a 5-day rolling median;
   - sleep per day split night/day;
   - bottle volume per day;
   - breastfeed episodes per day.
4. **Feed gaps by time of day** — scatter of gap length vs clock time of gap start, last 14 days, colored by recency (sequential, one hue).

Visual rules: dark mode via `prefers-color-scheme`; no information available only on hover (tap shows a tooltip); text at least 12 px; charts re-render on resize/orientation change; partial today visually distinct (lighter) in trends.

## Edge cases that must be handled and tested

- **DST:** Europe switches back on **Sun 25 Oct 2026**, which creates a 25-hour baby day; in March there is a 23-hour one. Row geometry must use real elapsed time. Sleeps across the switch must not double-count or vanish.
- Sleep crossing `ROW_START` → split across two rows; totals sum correctly.
- Sleep or feed crossing midnight → no special handling needed beyond the above; test it anyway.
- A running sleep timer → the "Awake for" tile must not claim awake.
- Feeds with zero duration (logged instantly) → drawn as marks, never as zero-width bars.
- Empty data, a single entry, or only breast or only bottle data → page renders with "not enough data" states, no exceptions.
- Baby Buddy unreachable → serve the last cached data with a visible "stale since HH:MM" banner. If there's no cache, show an error state.
- Overlapping or duplicate entries (two parents logging the same feed) → episode merging absorbs near-duplicates; exact duplicates are dropped and counted.

## Testing

- `pytest` with JSON fixtures shaped like real API responses, hand-built to cover each edge case above, plus a 10-day synthetic "realistic" fixture.
- Unit tests on every metric function, with expected values computed by hand in the test.
- One smoke test that runs the FastAPI app against a mocked Baby Buddy (httpx mock transport) and checks every endpoint returns valid JSON.
- No pandas anywhere, including test helpers.

## Deployment

- `Dockerfile` (multi-stage, `uv`, slim Python base, non-root user) and a `compose.yaml` service joining Baby Buddy's network as `external`. Env comes from a `.env` file that is git-ignored; ship a `.env.example`.
- A Caddy snippet in the README matching the existing tailnet services.
- README section "Create the read-only user":
  1. Baby Buddy admin → add a user.
  2. Give it read-only rights (verify the exact mechanism in the installed version).
  3. Log in as that user and copy its API key from User Settings.
  4. Put it in `.env`.

## Verify before relying on

Check these against the running instance or the OpenAPI schema at `/api/schema`, and report back rather than assume:

- Filter parameter names for child and date range on `/api/feedings/` and `/api/sleep/` (via `OPTIONS`).
- Exact `method` enum strings for feedings.
- How the read-only permission is granted for a user in this version.
- Paths of the add forms for feedings, sleep and timers (for deep links).
- Whether timers expose anything that identifies them as sleep vs feeding, beyond a free-text name.

## Acceptance criteria

- On a phone in portrait, the Now tiles and buttons are visible without scrolling, and the 7-day actogram is fully readable without zooming.
- Baby Buddy's token has no write access, verified by a test request that must fail with 403.
- All edge-case tests pass, including a fixture spanning 25 Oct 2026.
- Cold page load under 2 s on the tailnet. No external network requests from the browser.

## Later (explicitly not v1)

Quick-log buttons via a second, write-scoped token (needs a confirm step and undo); webhook-driven refresh; breast-side view; diaper and growth views.
