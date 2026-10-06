"use strict";

const REFRESH_MS = 60_000;
const TICK_MS = 30_000;
const FONT = { fontSize: "12px", fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" };

const state = { now: null, actogram: null, trends: null, skewMs: 0, actoDays: 7, trendDays: 14 };
const $ = (sel, root = document) => root.querySelector(sel);

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function fmtDur(h) {
  if (h == null || !isFinite(h)) return "—";
  const mins = Math.max(0, Math.round(h * 60));
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, "0")}`;
}

function elapsedSince(iso) {
  if (!iso) return null;
  return (Date.now() + state.skewMs - Date.parse(iso)) / 3_600_000;
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  return { ok: r.ok, body };
}

/* ---------- Now ---------- */

function setMeter(tile, range, elapsedH) {
  const meter = $(".meter", tile);
  if (!range || !range.enough || elapsedH == null) { meter.hidden = true; return; }
  const max = Math.max(range.p75_h * 1.5, range.median_h * 2, elapsedH * 1.05);
  const pct = v => `${Math.min(100, (v / max) * 100)}%`;
  $(".band", meter).style.left = pct(range.p25_h);
  $(".band", meter).style.width = `${((range.p75_h - range.p25_h) / max) * 100}%`;
  $(".median", meter).style.left = pct(range.median_h);
  $(".marker", meter).style.left = pct(elapsedH);
  meter.hidden = false;
}

function rangeCaption(range, need, label) {
  if (!range) return "";
  if (!range.enough) return `${label}: not enough data · n=${range.n} (need ${need})`;
  return `usual ${fmtDur(range.p25_h)}–${fmtDur(range.p75_h)} · n=${range.n}`;
}

function feedDetail(f, unit) {
  if (!f) return "no feed logged";
  const what = {
    breast: "breast",
    bottle: f.amount_missing && f.volume == null ? "bottle, no amount" : `bottle ${f.volume ?? "?"} ${unit}`,
    mixed: `breast + bottle${f.volume != null ? ` ${f.volume} ${unit}` : ""}`,
  }[f.kind];
  return `last ${f.start_label} · ${what}`;
}

function renderNow() {
  const p = state.now;
  if (!p) return;
  const feedTile = $("#tile-feed");
  const wakeTile = $("#tile-wake");
  const need = p.min_baseline_n;

  const feedH = p.last_feed ? elapsedSince(p.last_feed.start) : null;
  $("[data-elapsed]", feedTile).textContent = fmtDur(feedH);
  setMeter(feedTile, p.feed_gap_usual, feedH);
  const periodWord = p.feed_gap_usual?.period === "night" ? "night gaps" : "day gaps";
  $("[data-caption]", feedTile).textContent = p.feed_gap_usual
    ? `${rangeCaption(p.feed_gap_usual, need, periodWord)}${p.feed_gap_usual.enough ? ` (${periodWord})` : ""}`
    : "";
  $("[data-detail]", feedTile).textContent = p.last_feed !== undefined ? feedDetail(p.last_feed, p.amount_unit) : "";

  const s = p.state;
  const title = $("[data-title]", wakeTile);
  if (s && s.kind === "asleep") {
    title.textContent = `Asleep since ${s.since_label}`;
    $("[data-elapsed]", wakeTile).textContent = fmtDur(elapsedSince(s.since));
    setMeter(wakeTile, null, null);
    $("[data-caption]", wakeTile).textContent = s.source === "timer" ? `timer “${s.timer_name}” running` : "sleep in progress";
    $("[data-detail]", wakeTile).textContent = "";
  } else if (s && s.kind === "awake") {
    title.textContent = "Awake for";
    const h = elapsedSince(s.since);
    $("[data-elapsed]", wakeTile).textContent = fmtDur(h);
    setMeter(wakeTile, p.wake_window_usual, h);
    $("[data-caption]", wakeTile).textContent = rangeCaption(p.wake_window_usual, need, "wake windows");
    $("[data-detail]", wakeTile).textContent = `woke ${s.since_label}`;
  } else {
    title.textContent = "Awake for";
    $("[data-elapsed]", wakeTile).textContent = "—";
    setMeter(wakeTile, null, null);
    $("[data-caption]", wakeTile).textContent = s ? "no sleep logged yet" : "";
    $("[data-detail]", wakeTile).textContent = "";
  }

  const timers = $("#timers");
  timers.replaceChildren(...(p.timers || []).map(t => {
    const li = document.createElement("li");
    li.textContent = `⏱ ${t.name || "unnamed timer"} · ${fmtDur(elapsedSince(t.start))}`;
    return li;
  }));
  timers.hidden = !(p.timers && p.timers.length);
}

function renderChrome(p, error) {
  if (p?.child?.name) $("#child-name").textContent = p.child.name;
  if (p?.links) {
    $("#link-feeding").href = p.links.feeding;
    $("#link-sleep").href = p.links.sleep;
    $("#link-timers").href = p.links.timers;
  }
  const banner = $("#banner");
  if (error && !p?.stale_since) {
    banner.className = "banner error";
    banner.textContent = `Can't load data: ${error}`;
    banner.hidden = false;
  } else if (p?.stale_since) {
    banner.className = "banner stale";
    banner.textContent = `Baby Buddy unreachable · stale since ${p.stale_since_label}`;
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
  if (p?.now) $("#updated").textContent = `updated ${p.now.slice(11, 16)}`;
  const q = p?.data_quality;
  if (q) {
    const bits = [];
    if (q.duplicate_feeds) bits.push(`${q.duplicate_feeds} duplicate feed(s) dropped`);
    if (q.duplicate_sleeps) bits.push(`${q.duplicate_sleeps} duplicate sleep(s) dropped`);
    if (q.excluded_feed_gaps) bits.push(`${q.excluded_feed_gaps} feed gap(s) excluded from “usual” (logging gap)`);
    if (q.excluded_wake_windows) bits.push(`${q.excluded_wake_windows} wake window(s) excluded`);
    $("#quality").textContent = bits.join(" · ");
  }
}

/* ---------- Charts ---------- */

function width(el) {
  return Math.max(280, Math.floor(el.clientWidth));
}

function mount(el, node) {
  el.replaceChildren(node);
}

function empty(el, text) {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = text;
  el.replaceChildren(d);
}

function hourTicks(ticks, w) {
  const step = w < 440 ? 6 : 3;
  return ticks.filter(t => t.h % step === 0).map(t => t.h);
}

function tickFormatter(ticks) {
  const m = new Map(ticks.map(t => [t.h, t.label]));
  return h => m.get(h) ?? "";
}

function renderActogram() {
  const el = $("#actogram");
  const p = state.actogram;
  if (!p) return;
  const rows = p.rows;
  const W = width(el);
  const rowH = p.days <= 7 ? 38 : 24;
  const shortOf = new Map(rows.map(r => [r.label, r.label_short + (r.is_partial ? " ·" : "")]));
  const flat = key => rows.flatMap(r => r[key].map(d => ({ ...d, label: r.label })));
  const sleeps = flat("sleeps");
  const feeds = flat("feeds");
  const nights = flat("night");
  const progress = flat("sleep_in_progress");
  const suspectRows = rows.filter(r => r.suspect_gap).map(r => ({ label: r.label, x1: 0, x2: r.length_h }));
  const today = rows.find(r => r.is_partial);
  const unit = state.now?.amount_unit ?? "ml";
  const maxR = Math.min(rowH * 0.42, 11);
  const ring = Math.max(4, Math.min(5.5, rowH * 0.18));

  const tips = [
    ...sleeps.map(s => ({
      x: (s.start_h + s.end_h) / 2, label: s.label,
      title: `Sleep ${s.start_label}–${s.end_label}\n${fmtDur(s.duration_h)}`,
    })),
    ...feeds.map(f => ({
      x: f.h, label: f.label,
      title: `${f.kind === "mixed" ? "Breast + bottle" : f.kind === "bottle" ? "Bottle" : "Breast"} ${f.time_label}` +
        (f.bottle_volume != null ? `\n${f.bottle_volume} ${unit}` : "") +
        (f.amount_missing ? "\namount not logged" : "") +
        (f.n_feeds > 1 ? `\n${f.n_feeds} entries merged` : ""),
    })),
  ];

  const plot = Plot.plot({
    width: W,
    height: rows.length * rowH + 30,
    marginLeft: 50,
    marginRight: 8,
    marginTop: 4,
    marginBottom: 24,
    style: { ...FONT, color: css("--ink-2"), background: "transparent" },
    x: { domain: [0, p.x_max_h], ticks: hourTicks(p.ticks, W), tickFormat: tickFormatter(p.ticks), label: null },
    y: { domain: rows.map(r => r.label), tickFormat: l => shortOf.get(l), label: null, tickSize: 0, padding: 0 },
    r: { domain: [0, Math.max(1, p.max_volume ?? 1)], range: [0, maxR] },
    opacity: { type: "identity" },
    marks: [
      Plot.barX(nights, { x1: "start_h", x2: "end_h", y: "label", fill: css("--night"), insetTop: 1, insetBottom: 1 }),
      Plot.barX(suspectRows, { x1: "x1", x2: "x2", y: "label", fill: "url(#hatch)", insetTop: 1, insetBottom: 1 }),
      Plot.ruleX(hourTicks(p.ticks, W), { stroke: css("--grid"), strokeWidth: 1 }),
      Plot.barX(sleeps, {
        x1: "start_h", x2: "end_h", y: "label", fill: css("--sleep"),
        insetTop: rowH * 0.3, insetBottom: rowH * 0.3, rx: 2,
      }),
      Plot.barX(progress, {
        x1: "start_h", x2: "end_h", y: "label", fill: css("--sleep"), fillOpacity: 0.35,
        stroke: css("--sleep"), strokeDasharray: "3,2", insetTop: rowH * 0.3, insetBottom: rowH * 0.3, rx: 2,
      }),
      Plot.dot(feeds.filter(f => f.bottle_volume != null), {
        x: "h", y: "label", r: "bottle_volume", fill: css("--feed"), stroke: css("--surface"), strokeWidth: 1,
      }),
      Plot.dot(feeds.filter(f => f.kind !== "bottle"), {
        x: "h", y: "label", r: ring, stroke: css("--feed"), strokeWidth: 1.6, fill: "none",
      }),
      Plot.dot(feeds.filter(f => f.amount_missing), {
        x: "h", y: "label", r: ring, stroke: css("--feed"), strokeWidth: 1.6, strokeDasharray: "2,2", fill: "none",
      }),
      today ? Plot.tickX([{ h: p.now_h, label: today.label }], { x: "h", y: "label", stroke: css("--ink"), strokeWidth: 2 }) : null,
      Plot.tip(tips, Plot.pointer({ x: "x", y: "label", title: "title", maxRadius: 28, fontSize: 12 })),
    ],
  });
  mount(el, plot);
}

function xAxisFor(rows, w) {
  const n = rows.length;
  const step = Math.max(1, Math.ceil(n / Math.max(1, Math.floor((w - 48) / 56))));
  const ticks = rows.map(r => r.i).filter(i => (n - 1 - i) % step === 0);
  const fmt = new Map(rows.map(r => [r.i, r.label_short]));
  return { domain: rows.map(r => r.i), ticks, tickFormat: i => fmt.get(i), label: null, padding: 0.25 };
}

function smallChart(el, rows, { height = 104, axis = false, y, marks }) {
  const plot = Plot.plot({
    width: width(el),
    height: height + (axis ? 22 : 0),
    marginLeft: 40,
    marginRight: 8,
    marginTop: 8,
    marginBottom: axis ? 24 : 4,
    style: { ...FONT, color: css("--ink-2"), background: "transparent" },
    x: { ...xAxisFor(rows, width(el)), axis: axis ? "bottom" : null },
    y: { grid: true, label: null, nice: true, zero: true, ticks: 3, ...y },
    opacity: { type: "identity" },
    marks,
  });
  mount(el, plot);
}

function suspectBackdrop(rows, top) {
  return Plot.barY(rows.filter(r => r.suspect_gap), { x: "i", y1: 0, y2: () => top, fill: "url(#hatch)" });
}

function renderTrends() {
  const p = state.trends;
  if (!p) return;
  const rows = p.rows;
  const op = r => (r.is_partial ? 0.45 : 1);
  const unit = state.now?.amount_unit ?? "ml";
  $("[data-unit]").textContent = `· ${unit}`;
  const ids = ["#trend-longest", "#trend-sleep", "#trend-bottle", "#trend-breast"];
  if (!rows.some(r => r.has_data)) {
    ids.forEach(id => empty($(id), "Not enough data yet."));
    return;
  }
  const withData = rows.filter(r => r.has_data);
  const tipTitle = (r, text) => `${r.label_short}${r.is_partial ? " (so far)" : ""}\n${text}`;

  const longest = withData.filter(r => r.longest_night_h != null);
  const topLongest = Math.max(1, ...longest.map(r => r.longest_night_h));
  if (!longest.length) empty($("#trend-longest"), "No night sleep logged in this range.");
  else smallChart($("#trend-longest"), rows, {
    y: { tickFormat: v => `${v}h`, domain: [0, topLongest] },
    marks: [
      suspectBackdrop(rows, topLongest),
      Plot.lineY(withData, { x: "i", y: "longest_night_median_h", stroke: css("--sleep"), strokeWidth: 2, curve: "monotone-x" }),
      Plot.dot(longest, { x: "i", y: "longest_night_h", r: 4, fill: css("--sleep"), stroke: css("--surface"), strokeWidth: 1.5, opacity: op }),
      Plot.tip(longest, Plot.pointerX({
        x: "i", y: "longest_night_h", fontSize: 12,
        title: r => tipTitle(r, `longest ${fmtDur(r.longest_night_h)}` +
          (r.longest_night_median_h != null ? `\n5-night median ${fmtDur(r.longest_night_median_h)}` : "")),
      })),
    ],
  });

  const parts = withData.flatMap(r => [
    { i: r.i, part: "night", h: r.night_sleep_h, r },
    { i: r.i, part: "day", h: r.day_sleep_h, r },
  ]);
  const topSleep = Math.max(1, ...withData.map(r => r.night_sleep_h + r.day_sleep_h));
  smallChart($("#trend-sleep"), rows, {
    y: { tickFormat: v => `${v}h`, domain: [0, topSleep] },
    marks: [
      suspectBackdrop(rows, topSleep),
      Plot.barY(parts, {
        x: "i", y: "h", fill: d => (d.part === "night" ? css("--sleep-night") : css("--sleep-day")),
        order: ["night", "day"], z: "part", opacity: d => op(d.r), stroke: css("--surface"), strokeWidth: 1, rx: 2,
      }),
      Plot.tip(withData, Plot.pointerX({
        x: "i", y: r => r.night_sleep_h + r.day_sleep_h, fontSize: 12,
        title: r => tipTitle(r, `night ${fmtDur(r.night_sleep_h)}\nday ${fmtDur(r.day_sleep_h)}\ntotal ${fmtDur(r.night_sleep_h + r.day_sleep_h)}`),
      })),
    ],
  });

  const topBottle = Math.max(10, ...withData.map(r => r.bottle_volume));
  smallChart($("#trend-bottle"), rows, {
    y: { domain: [0, topBottle] },
    marks: [
      suspectBackdrop(rows, topBottle),
      Plot.barY(withData, { x: "i", y: "bottle_volume", fill: css("--feed"), opacity: op, rx: 2 }),
      Plot.text(withData.filter(r => r.bottle_missing > 0), {
        x: "i", y: "bottle_volume", text: () => "+?", dy: -7, fill: css("--ink-2"), fontSize: 12,
      }),
      Plot.tip(withData, Plot.pointerX({
        x: "i", y: "bottle_volume", fontSize: 12,
        title: r => tipTitle(r, `${r.bottle_volume} ${unit}` + (r.bottle_missing ? `\n+ ${r.bottle_missing} bottle(s) without amount` : "")),
      })),
    ],
  });

  const topBreast = Math.max(2, ...withData.map(r => r.breast_episodes));
  smallChart($("#trend-breast"), rows, {
    axis: true,
    y: { domain: [0, topBreast] },
    marks: [
      suspectBackdrop(rows, topBreast),
      Plot.barY(withData, { x: "i", y: "breast_episodes", fill: css("--feed"), opacity: op, rx: 2 }),
      Plot.tip(withData, Plot.pointerX({
        x: "i", y: "breast_episodes", fontSize: 12,
        title: r => tipTitle(r, `${r.breast_episodes} breastfeed episode(s)\n${r.feed_episodes} feed episode(s) total`),
      })),
    ],
  });
}

function renderGaps() {
  const el = $("#gaps");
  const p = state.trends;
  if (!p) return;
  const gaps = p.gaps;
  if (gaps.length < 2) { empty(el, "Not enough feed gaps logged in the last 14 days."); return; }
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const W = width(el);
  const plot = Plot.plot({
    width: W,
    height: 220,
    marginLeft: 40,
    marginRight: 8,
    marginBottom: 28,
    style: { ...FONT, color: css("--ink-2"), background: "transparent" },
    x: { domain: [0, 24], ticks: hourTicks(p.gap_ticks, W), tickFormat: tickFormatter(p.gap_ticks), label: null },
    y: { grid: true, label: null, tickFormat: v => `${v}h`, zero: true, nice: true },
    color: {
      type: "linear",
      scheme: "oranges",
      domain: dark ? [0, p.gap_days] : [p.gap_days, 0],
      range: dark ? [0.2, 0.8] : [0.3, 1],
      legend: true,
      label: "days ago",
      tickFormat: d => Math.round(Math.abs(d)),
      ticks: 3,
      width: 180,
      style: FONT,
    },
    marks: [
      Plot.barX(p.gap_night, { x1: "start_h", x2: "end_h", fill: css("--night") }),
      Plot.dot(gaps.filter(g => !g.suspect).sort((a, b) => b.days_ago - a.days_ago), {
        x: "tod_h", y: "length_h", fill: "days_ago", r: 4.5, stroke: css("--surface"), strokeWidth: 1,
      }),
      Plot.dot(gaps.filter(g => g.suspect), { x: "tod_h", y: "length_h", stroke: "days_ago", r: 4.5, strokeWidth: 1.5 }),
      Plot.tip(gaps, Plot.pointer({
        x: "tod_h", y: "length_h", fontSize: 12, maxRadius: 30,
        title: g => `from ${g.start_label}\n${fmtDur(g.length_h)} (${g.period})${g.suspect ? "\nspans a logging gap" : ""}`,
      })),
    ],
  });
  mount(el, plot);
}

function renderCharts() {
  try { renderActogram(); } catch (e) { console.error(e); empty($("#actogram"), "Couldn't draw the actogram."); }
  try { renderTrends(); } catch (e) { console.error(e); }
  try { renderGaps(); } catch (e) { console.error(e); empty($("#gaps"), "Couldn't draw the gap chart."); }
}

/* ---------- Data flow ---------- */

const lastJSON = {};
function changed(key, body) {
  const s = JSON.stringify(body);
  if (lastJSON[key] === s) return false;
  lastJSON[key] = s;
  return true;
}

async function loadNow() {
  try {
    const { ok, body } = await getJSON("/api/now");
    if (body.now) state.skewMs = Date.parse(body.now) - Date.now();
    renderChrome(body, ok ? null : body.error);
    if (ok) { state.now = body; renderNow(); }
  } catch (e) {
    renderChrome(state.now, "dashboard server unreachable");
  }
}

async function loadActogram() {
  try {
    const { ok, body } = await getJSON(`/api/actogram?days=${state.actoDays}`);
    if (ok && changed("acto", body)) {
      state.actogram = body;
      try { renderActogram(); } catch (e) { console.error(e); }
    }
  } catch (e) { console.error(e); }
}

async function loadTrends() {
  try {
    const { ok, body } = await getJSON(`/api/trends?days=${state.trendDays}`);
    if (ok && changed("trends", body)) {
      state.trends = body;
      try { renderTrends(); } catch (e) { console.error(e); }
      try { renderGaps(); } catch (e) { console.error(e); }
    }
  } catch (e) { console.error(e); }
}

async function refresh() {
  await loadNow();
  await Promise.all([loadActogram(), loadTrends()]);
}

document.querySelectorAll(".seg").forEach(seg => {
  seg.addEventListener("click", ev => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    seg.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
    const days = Number(btn.dataset.days);
    if (seg.dataset.target === "actogram") { state.actoDays = days; loadActogram(); }
    else { state.trendDays = days; loadTrends(); }
  });
});

let resizeTimer = null;
let lastWidth = 0;
new ResizeObserver(entries => {
  const w = Math.round(entries[0].contentRect.width);
  if (w === lastWidth) return;
  lastWidth = w;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderCharts, 120);
}).observe($("main"));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderCharts);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

setInterval(renderNow, TICK_MS);
setInterval(refresh, REFRESH_MS);
refresh();
