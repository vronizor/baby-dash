# baby-dash

A read-only, mobile-first dashboard on top of a [Baby Buddy](https://github.com/babybuddy/babybuddy) instance. It answers two questions:

1. **Rhythm:** are feeding and sleep settling into something sensible over days and weeks?
2. **Now:** is a feed or a nap about due?

It shows how long it has been since the last feed and since waking, each against the usual P25–P75 range from the last few days. Below that are an actogram (one row per "baby day", 18:00 → 18:00), small trend charts, and a scatter of feed gaps by time of day. It never predicts a time and never writes to Baby Buddy. See [SPEC.md](SPEC.md).

```
phone (tailnet) ──► Caddy ──► baby-dash (FastAPI, :8080) ──► babybuddy:8000 /api/*
```

## Deploy

1. [Create the read-only user](#create-the-read-only-user) and copy its API key.
2. `cp .env.example .env` and fill in `BB_PUBLIC_URL` and `BB_TOKEN`. Set `BB_NETWORK` to Baby Buddy's Docker network (`docker network ls`).
3. Check the assumptions against your instance, then start:

   ```sh
   docker compose build
   docker compose run --rm baby-dash baby-dash verify   # must print "[ok] POST /api/feedings/ → 403"
   docker compose up -d
   ```

4. Add the Caddy site block below and reload Caddy.

The image is multi-arch-friendly (`python:3.12-slim-bookworm`, pure-Python plus polars wheels) and runs as a non-root user. Build it on the Pi itself (`docker compose build`) or with `docker buildx build --platform linux/arm64`.

> **Raspberry Pi 4 note:** if the container dies with `Illegal instruction`, the polars wheel targets newer ARM cores than the Pi 4's Cortex-A72. Switch the dependency to `polars[rtcompat]` (`uv add 'polars[rtcompat]'`) and rebuild. The Pi 5 is fine.

### Caddy

Baby-dash has no auth of its own: membership of the tailnet is the boundary. If Caddy shares a Docker network with `baby-dash`:

```caddyfile
baby-dash.your-tailnet.ts.net {
	encode zstd gzip
	reverse_proxy baby-dash:8080
}
```

If Caddy runs on the host, uncomment the loopback `ports:` entry in `compose.yaml` and use `reverse_proxy 127.0.0.1:8080`. Match the hostname/TLS lines to your other tailnet services.

### Create the read-only user

Verified against Baby Buddy's source (v2.11.0). Re-check the labels in your installed version.

1. Log in as an admin, open the admin menu ▸ **Users** (`/users/`), then add a user (`/users/add/`).
2. Tick **Read only**. This adds the user to Baby Buddy's `read_only` group, which has view permissions only. Leave **Staff**, **Superuser** and **Caregiver** unticked; Baby Buddy refuses read only + caregiver together.
3. Log out, log in as that user, open the user menu ▸ **Settings** (`/user/settings/`) and copy **API ▸ Key**.
4. Put it in `.env` as `BB_TOKEN`, then run `baby-dash check-token` (or `verify`), which must report **403**.

The write check POSTs an *empty* body to `/api/feedings/`. Django REST Framework checks permissions before validating the body, so a read-only token gets `403` and a writable one gets `400`. No record can be created either way. The dashboard's own client has no write method at all: it only issues `GET`, plus `OPTIONS` for metadata.

## Configuration

| Var | Default | Meaning |
|---|---|---|
| `BB_URL` | `http://babybuddy:8000` | Internal API base |
| `BB_PUBLIC_URL` | **required** | Tailnet URL of Baby Buddy, for the "add" deep links |
| `BB_TOKEN` | **required** | Read-only user's API key |
| `CHILD_SLUG` | unset | If unset and exactly one child exists, it is used; otherwise startup fails with the list of slugs |
| `TZ` | `Europe/Paris` | All display and day-bucketing happens in this zone |
| `ROW_START` | `18:00` | Start of a baby day, for actogram rows **and** daily totals |
| `NIGHT_START` / `NIGHT_END` | `20:00` / `07:00` | Fixed night window |
| `FEED_MERGE_MIN` | `20` | Feeds starting ≤ this many minutes after the episode's end join it |
| `SLEEP_MERGE_MIN` | `5` | Sleeps separated by < this merge |
| `BASELINE_DAYS` | `3` | Window for the "usual" ranges |
| `MIN_BASELINE_N` | `6` | Below this many observations, "not enough data" |
| `CACHE_TTL_S` | `60` | API cache TTL |
| `AMOUNT_UNIT` | `ml` | Label only |
| `SLEEP_TIMER_REGEX` | `sleep\|nap\|sieste\|dodo` | Case-insensitive; a running timer whose name matches means "asleep" (addition, see below) |

## Endpoints

- `GET /`: the page (one HTML file, vendored Observable Plot 0.6.17 and d3 7.9.0, no CDN).
- `GET /api/now`: last feed episode, current state, usual ranges with `n` and exclusions, running timers, deep links, data-quality counts.
- `GET /api/actogram?days=7|14`: per row: sleep segments, feed marks, night spans, suspect flag and spans, `is_partial`, the in-progress sleep from a timer.
- `GET /api/trends?days=14|30`: per baby day totals and flags, plus feed gaps for the last 14 days.
- `GET /healthz`: 200 if the last successful Baby Buddy fetch is within 3× TTL. It also triggers a refresh when the cache is stale, so the Docker healthcheck keeps the cache warm.

Every time is returned as an ISO string in `TZ` plus precomputed hour offsets; the browser never does timezone math. When Baby Buddy is unreachable, the last snapshot is served with `stale_since`, and refreshes back off to one attempt per TTL.

## What `verify` checked, and what still needs your instance

The spec's "Verify before relying on" items, checked against Baby Buddy's source at v2.11.0. `baby-dash verify` re-checks each one against your running instance; please run it and compare.

| Item | Finding in source | How the code copes if your version differs |
|---|---|---|
| Filter names on `/api/feedings/`, `/api/sleep/` | `child` (child **id**), `start`, `start_min`, `start_max`, `end`, `end_min`, `end_max`, `tags`, plus `type` and `method` on feedings | Names are confirmed at startup. Unconfirmed filters are not sent and everything is fetched. Child and window are always re-filtered client-side, so a wrong guess only costs bandwidth. |
| Confirming via `OPTIONS` | Baby Buddy maps `OPTIONS` to the **add** permission, so the read-only token gets **403**. `OPTIONS` does list filters, but only for writers. `/api/schema/` lists them with view rights, but returns **500** on releases up to at least v2.11.0 (fixed only on Baby Buddy's development branch so far). | Confirmation tries the schema, then `OPTIONS`, then a read-only probe: `end_min` set far in the future must return 0 results, and a nonexistent `child` must be rejected (400) or return 0. A filter that Baby Buddy ignores returns everything, so it stays off. `verify` prints which method confirmed each one. |
| Feeding `method` values | `bottle`, `left breast`, `right breast`, `both breasts`, `parent fed`, `self fed` | Unknown values map to `other` (hidden, kept). |
| Read-only mechanism | "Read only" checkbox on the user form → `read_only` group | `check-token` / `verify` must return 403. |
| Add-form paths | `/feedings/add/`, `/sleep/add/`, `/timers/` (list), `/timers/add/`; add forms accept `?child=<slug>` | The links use these. |
| Timer type | Timer API fields: `id, child, name, start, duration, user`. No type field and no `end`. Stopping a timer **deletes** it, so every listed timer is running. `name` is optional free text. | Sleep is inferred from the name (`SLEEP_TIMER_REGEX`). Name your sleep timer "Sleep" (or "Sieste"). Unnamed timers show as chips but don't change the state. |

## Decisions where the spec was ambiguous

- **No "open sleep" in Baby Buddy.** Sleep entries require an end time, so "asleep" comes only from a running timer (by name) or a logged sleep whose end is in the future.
- **Baby-day label** = the date of the row's midpoint. This equals the spec's "date of its morning" for `ROW_START=18:00`, and still makes sense if `ROW_START` is moved before noon.
- **Night window ↔ baby day:** a night belongs to the baby day in which it starts (20:00 Oct 5 → 07:00 Oct 6 belongs to "Oct 6").
- **Suspect interval:** a hole between consecutive logged activities (any feed including `other`, or any merged sleep), longer than 8 h if its *midpoint* is in the night window and 6 h otherwise. The midpoint rule mirrors the longest-night-stretch rule. The open interval from the last entry to *now* is not flagged; the Now tile already shows it.
- **Exclusions:** feed gaps and wake windows that overlap a suspect interval are dropped from the baselines; the count is returned and shown in the footer.
- **Baselines** use gaps/wake windows that *start* within the last `BASELINE_DAYS × 24 h` and have ended. A wake window is "daytime" by where it starts, like feed gaps.
- **Episodes ignore `parent fed` / `self fed`** (solids) for intervals and counts. They still count as activity for the data-quality check.
- **Feed kind per episode:** `breast`, `bottle` or `mixed`. Mixed episodes are drawn as a ring with a dot inside.
- **Exact duplicates:** same start, end, method, type and amount for feeds, or same start and end for sleeps. Duplicates of these are dropped and counted. Near-duplicates are absorbed by episode/sleep merging.
- **Trends toggle 14/30**; the gap scatter is always the last 14 days, as specified. Days before the first logged entry show no data rather than zero.
- **DST:** row geometry uses real elapsed time, so the 25 Oct 2026 row is 25 h wide and the March one 23 h. Axis labels are wall-clock for a normal 24 h row, so on the 25 h row, marks after 03:00 sit one hour right of their clock label. Tooltips always show the real clock time.
- **"Asleep since" tile** shows elapsed time with no meter; the spec's wake-window meter doesn't apply while asleep.
- **Fetch window** = 30 days + max(`BASELINE_DAYS`, 4) + 1, so the 5-night rolling median is warm on the first visible day.

## Development

```sh
uv sync
uv run pytest                                      # unit + edge-case + smoke tests
uv tree | grep -i pandas                           # must print nothing
cd tests && uv run python demo_server.py           # dashboard on fixture data at http://127.0.0.1:8080
cd tests && uv run python demo_server.py dst_fall_2026 "2026-10-26 12:00"
cd tests && uv run python make_fixtures.py         # regenerate tests/fixtures/*.json
```

All data transformation is in polars (`metrics.py`, `normalize.py`, `timebase.py`). The fixtures are API-shaped JSON: the DST fall-back weekend, a hand-built edge-case day (duplicates, overlaps, ROW_START/midnight crossings, instant feeds, running sleep timer), empty/single/breast-only/bottle-only, two children, and a seeded 10-day realistic set with one logging lapse.
