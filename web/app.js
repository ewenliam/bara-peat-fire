// Bara front end: one map (MapLibre + deck.gl) and a side panel. All numbers come from /api.
// Field briefing palette (web/style.css): ember P1 from the Bara mark, ochre P2, stone P3, forest routes
const TIER_RGB = { P1: [178, 38, 30], P2: [154, 91, 5], P3: [125, 117, 107] };
const ROUTE_RGB = [30, 91, 79];
const INK = [31, 27, 23];
const PAPER = [250, 247, 240];
const state = { date: null, day: null, briefing: null, replay: null, selected: null, meta: null };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 0) => Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw Object.assign(new Error((await r.json().catch(() => ({}))).detail || r.statusText), { status: r.status });
  return r.json();
}

// ---------- map
const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  center: [110.5, -1.5], zoom: 5.2, attributionControl: { compact: true },
  preserveDrawingBuffer: true,  // lets the map self-check take a snapshot of what is on screen
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
// Warm the light basemap to paper: land, water and parks only; labels and roads stay as they are
map.on("style.load", () => {
  for (const l of map.getStyle().layers) {
    if (l.type === "background") map.setPaintProperty(l.id, "background-color", "#f3eee4");
    else if (l.type === "fill" && /water/.test(l.id)) map.setPaintProperty(l.id, "fill-color", "#d9e0dc");
    else if (l.type === "fill" && /landcover|park|wood|forest/.test(l.id)) map.setPaintProperty(l.id, "fill-color", "#e7e3d3");
  }
});
const overlay = new deck.MapboxOverlay({ interleaved: true, layers: [], getTooltip: tooltip });
map.addControl(overlay);

function tooltip({ object, layer }) {
  if (!object || !layer) return null;
  if (layer.id === "clusters") {
    const c = object;
    const truth = $("show-truth").checked ? `<br>Burned next day: <b>${c.next_day_cells}</b> cells` : "";
    return { html: `<b>${c.id}</b> ${c.tier} near ${esc(c.place)}, ${esc(c.province)}<br>Risk ${c.risk} | expected new cells ${c.expected_new_cells}<br>Peat ${c.peat_share} | ${c.days_since_10mm} days since 10 mm rain${truth}` };
  }
  if (layer.id === "bases") return { html: `<b>${esc(object.name)}</b> crew base` };
  if (layer.id === "new-fires") return { html: `New-fire watch: ${(object.probability * 100).toFixed(1)}% chance tomorrow` };
  return null;
}

function render() {
  const d = state.day;
  if (!d) return;
  const showP3 = $("show-p3").checked, truth = $("show-truth").checked;
  const clusters = d.clusters.filter((c) => showP3 || c.tier !== "P3");
  const labels = [];
  d.routes.forEach((r) => r.stops.forEach((s, i) => labels.push({ ...s, text: `${i + 1} ${s.cluster_id}` })));
  const layers = [
    new deck.ScatterplotLayer({
      id: "clusters", data: clusters, pickable: true, radiusUnits: "meters",
      getPosition: (c) => [c.lon, c.lat],
      getRadius: (c) => Math.min(Math.max(Math.sqrt(c.risk) * 900, 1500), 14000),
      getFillColor: (c) => [...TIER_RGB[c.tier], c.tier === "P3" ? 120 : 200],
      getLineColor: (c) => (state.selected === c.id ? [...INK, 255] : [...PAPER, 230]),
      stroked: true, lineWidthMinPixels: 1,
      updateTriggers: { getLineColor: state.selected },
      onClick: ({ object }) => object && selectCluster(object.id),
    }),
    new deck.ScatterplotLayer({
      id: "truth", data: truth ? clusters.filter((c) => c.next_day_cells > 0) : [], radiusUnits: "meters",
      getPosition: (c) => [c.lon, c.lat], getRadius: (c) => Math.sqrt(c.next_day_cells) * 1100,
      filled: false, stroked: true, getLineColor: [...INK, 200], lineWidthMinPixels: 2,
    }),
    new deck.ScatterplotLayer({
      id: "new-fires", data: d.new_fires, pickable: true, radiusUnits: "meters",
      getPosition: (c) => [c.lon, c.lat], getRadius: 5500, filled: true, getFillColor: [122, 46, 110, 30],
      stroked: true, getLineColor: [122, 46, 110, 230], lineWidthMinPixels: 2,
    }),
    new deck.PathLayer({
      id: "routes", data: d.routes, widthUnits: "pixels", getWidth: 3, getColor: [...ROUTE_RGB, 230],
      getPath: (r) => [[r.lon, r.lat], ...r.stops.map((s) => [s.lon, s.lat])], capRounded: true, jointRounded: true,
    }),
    new deck.ScatterplotLayer({
      id: "bases", data: d.bases, pickable: true, radiusUnits: "pixels", getRadius: 5,
      getPosition: (b) => [b.lon, b.lat], getFillColor: PAPER, stroked: true, getLineColor: INK, lineWidthMinPixels: 2,
    }),
    new deck.TextLayer({
      id: "stop-labels", data: labels, getPosition: (s) => [s.lon, s.lat], getText: (s) => s.text,
      getSize: 13, getColor: INK, getPixelOffset: [0, -16], fontFamily: "IBM Plex Mono, monospace",
      background: true, getBackgroundColor: [...PAPER, 235], backgroundPadding: [4, 2],
    }),
  ];
  const today = new Set(d.routes.map((r) => `${r.base}|${r.stops.map((s) => s.cluster_id).join(",")}`));
  const changed = (state.scenario || []).filter((r) => !today.has(`${r.base}|${r.order.join(",")}`));
  if (changed.length) layers.push(new deck.PathLayer({
    id: "scenario", data: changed, widthUnits: "pixels", getWidth: 5, getColor: [214, 124, 38, 230],
    getPath: (r) => r.path, capRounded: true, jointRounded: true,
  }));
  if (typeof reportLayers === "function") layers.push(...reportLayers());
  if (typeof fieldLayers === "function") layers.push(...fieldLayers());
  const sel = d.clusters.find((c) => c.id === state.selected);
  if (sel) layers.push(new deck.ScatterplotLayer({
    id: "origin", data: [sel], radiusUnits: "pixels", getRadius: 6, getPosition: (c) => [c.origin_lon, c.origin_lat],
    getFillColor: PAPER, stroked: true, getLineColor: TIER_RGB.P1, lineWidthMinPixels: 3,
  }));
  overlay.setProps({ layers });
}

// ---------- KPIs
function renderKpis() {
  const k = state.day.kpis;
  const items = [
    ["Hotspots today", fmt(k.detections_today), `NASA VIIRS; median day in ${state.day.date.slice(0, 4)}: ${fmt(k.detections_median_day)}`],
    ["Fire clusters", fmt(k.clusters_active_today), "hotspots grouped by distance and time"],
    ["P1 brigade now", fmt(k.p1), "peat-weighted risk 5+", "p1"],
    ["P2 village watch", fmt(k.p2), "risk 1 to 5", "p2"],
    ["Expected new cells", fmt(k.expected_new_cells), `tomorrow, ${state.day.forecast_for}`],
    ["Crew coverage", `${Math.round(k.crew_cover * 100)}%`, "of P1+P2 risk visited"],
  ];
  $("kpis").innerHTML = items.map(([l, v, s, c]) => `<div class="kpi ${c || ""}"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`).join("");
}

// ---------- tabs
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
}

function renderBriefing() {
  const el = $("tab-briefing");
  const b = state.briefing;
  if (!b || !b.briefing || !b.briefing.headline) {
    el.innerHTML = `<h2>No briefing for ${state.date} yet</h2>
      <p class="muted">Muse Spark 1.3 calls the six Bara tools (detect, forecast, new fires, triage, dispatch, SAM 3.1 water search), then writes the briefing. Every number, tier, province and crew order is checked against the tool output before it is shown.</p>
      <p class="muted">About $0.04 for Muse Spark, plus up to $0.12 for SAM 3.1 on new satellite images. Cached calls are free.</p>
      <button class="btn" id="make-briefing">Write briefing</button>`;
    $("make-briefing").onclick = makeBriefing;
    return;
  }
  const br = b.briefing;
  const calls = b.llm_calls || [];
  const tokens = calls.reduce((a, c) => a + (c.prompt_tokens || 0) + (c.completion_tokens || 0), 0);
  el.innerHTML = `
    <div class="muted">Forecast for ${esc(br.forecast_for)}</div>
    <h2>${esc(br.headline)}</h2>
    <p>${esc(br.briefing)}</p>
    <div class="check ${b.passed ? "" : "bad"}"><span class="badge ${b.passed ? "ok" : "bad"}">${b.passed ? "GROUNDED" : "FAILED"}</span>
      <span>${b.passed ? "Every number, tier, province and crew order matches the tool output." : esc(b.grounding_errors.join("; "))}
      ${calls.length} Muse Spark calls, ${fmt(tokens)} tokens, $${Number(b.run_cost_usd).toFixed(3)}.</span></div>
    <h3>Priority clusters</h3>
    ${br.clusters.map((c) => `<div class="card clickable" data-id="${esc(c.cluster_id)}">
      <div class="card-head"><span class="badge ${esc(c.tier)}">${esc(c.tier)}</span><span class="name">${esc(c.cluster_id)}</span><span class="muted">${esc(c.province)}</span>
      <span class="right">${c.expected_new_cells} cells | ${(c.max_probability * 100).toFixed(1)}%</span></div>
      <div class="muted">${esc(c.note)}</div></div>`).join("")}
    <h3>Crew orders</h3>
    ${br.dispatch.map((r) => `<div class="card"><b>${esc(r.base)}</b> <span class="muted">crew:</span> <span class="mono">${r.order.map(esc).join(" &rarr; ")}</span></div>`).join("")}
    <h3>Caveats</h3>
    ${br.caveats.map((c) => `<p class="muted">${esc(c)}</p>`).join("")}`;
  el.querySelectorAll(".card.clickable").forEach((c) => (c.onclick = () => selectCluster(c.dataset.id)));
}

async function makeBriefing() {
  const btn = $("make-briefing");
  btn.disabled = true;
  btn.textContent = "Muse Spark is calling the tools...";
  try {
    state.briefing = await api(`/api/briefing?date=${state.date}`, { method: "POST" });
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Write briefing";
    $("tab-briefing").insertAdjacentHTML("beforeend", `<p class="muted">Failed: ${esc(e.message)}</p>`);
    return;
  }
  renderBriefing();
  renderCrews();
  refreshSpend();
}

async function runSelfCheck() {
  const btn = $("selfcheck-btn");
  btn.disabled = true; btn.textContent = "Muse Spark is looking at the map...";
  const pts = state.day.routes.flatMap((r) => [[r.lon, r.lat], ...r.stops.map((s) => [s.lon, s.lat])]);
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 60, duration: 0 });
  await new Promise((r) => map.once("idle", r));
  const image = map.getCanvas().toDataURL("image/jpeg", 0.85);
  try {
    state.selfcheck = await api("/api/selfcheck", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: state.date, image, routes_shown: state.day.routes.length }) });
    state.selfcheck.snapshot = image;
  } catch (e) {
    state.selfcheck = { error: e.message };
  }
  renderCrews();
  refreshSpend();
}

function selfCheckHtml() {
  const s = state.selfcheck;
  if (!s) return `<button class="btn ghost" id="selfcheck-btn">Check this map with Muse Spark (vision)</button>
    <p class="muted">Muse Spark looks at a snapshot of the map and the plan data, and flags what numbers alone miss, such as a route across open sea or high-risk fires no crew reaches. Advisory: the review can be wrong, so a person decides. About $0.03.</p>`;
  if (s.error || !s.review) return `<p class="muted">Self-check failed: ${esc(s.error || "no review")}</p>`;
  return `<div class="card"><div class="card-head"><span class="name">Map self-check</span><span class="right">$${Number(s.cost_usd).toFixed(3)}</span></div>
    <p>${esc(s.review.summary)}</p>
    ${s.review.findings.map((f) => `<div class="finding"><span class="badge ${f.status === "ok" ? "ok" : "bad"}">${f.status === "ok" ? "OK" : "CHECK"}</span>
      <span><b>${esc(f.check.replace("_", " "))}</b>: ${esc(f.detail)}</span></div>`).join("")}
    ${s.dropped && s.dropped.length ? `<p class="muted">${s.dropped.length} finding(s) dropped: they named clusters or bases not in the plan.</p>` : ""}
    <details><summary class="muted">Snapshot Muse Spark saw</summary><img class="thumb" src="${s.snapshot}" alt="Map snapshot sent to Muse Spark"></details></div>`;
}

function renderCrews() {
  const d = state.day;
  const water = (state.briefing && state.briefing.water) || {};
  $("tab-crews").innerHTML = `
    ${selfCheckHtml()}
    <p class="muted">One crew per provincial capital (assumed bases). The solver picks, for each crew, the visiting order that reaches the most peat-weighted risk in a 10-hour day (30 km/h, 2 h per visit). Visiting is not extinguishing: peat smoulders underground.</p>
    ${d.routes.map((r) => `<div class="route"><div class="card-head"><span class="name">${esc(r.base)}</span><span class="muted">${esc(r.province)}</span><span class="right">risk ${r.risk_covered}</span></div>
      ${r.stops.map((s, i) => {
        const c = d.clusters.find((x) => x.id === s.cluster_id) || {};
        const w = water[s.cluster_id];
        const river = w ? (w.river_found ? `River ${fmt(w.nearest_river_m)} m ${w.direction} (SAM 3.1, conf ${w.sam_confidence})` : "No river seen within 5 km (SAM 3.1)") : "";
        return `<div class="stop"><span class="n">${i + 1}</span><div><b class="mono">${s.cluster_id}</b> <span class="badge ${c.tier}">${c.tier}</span> near ${esc(c.place)}
          <div class="muted">${river}</div>${w && w.image ? `<img class="thumb" loading="lazy" src="${w.image}" alt="SAM 3.1 river mask around ${s.cluster_id}">` : ""}</div>
          <span class="muted mono">${s.arrive_h} h</span></div>`;
      }).join("")}</div>`).join("")}`;
  if ($("selfcheck-btn")) $("selfcheck-btn").onclick = runSelfCheck;
}

async function renderEvidence() {
  if (!state.replay) state.replay = await api("/api/replay");
  const r = state.replay, t = r.totals;
  const max = t.oracle.cells;
  const row = (name, v, cls = "") => `<div class="bar-row"><span>${name}</span><div class="bar ${cls}" style="width:${(v / max) * 100}%"></div><span class="v">${fmt(v)}</span></div>`;
  $("tab-evidence").innerHTML = `
    <h2>Does the plan reach tomorrow's fires?</h2>
    <p class="muted">Replay of ${r.days} days (${r.start} to ${r.end}). Each day's plan uses only that day's data and is scored on where new fire actually appeared the next day.</p>
    <h3>Next-day fire cells reached by crews</h3>
    <div class="bars">
      ${row("Bara (forecast + solver)", t.solver.cells)}
      ${row("Forecast + greedy", t.greedy.cells)}
      ${row("Biggest fire first", t.biggest_today.cells, "base")}
      ${row("Nearest fire first", t.nearest.cells, "base")}
      ${row("Oracle (knows tomorrow)", t.oracle.cells, "oracle")}
    </div>
    <p class="muted">Bara reaches ${Math.round((t.solver.cells / t.biggest_today.cells - 1) * 100)}% more next-day fire cells than sending crews to the biggest fire first, with fewer visits (${fmt(t.solver.visits)} vs ${fmt(t.biggest_today.visits)}).</p>
    <h3>Triage</h3>
    <p>P1 holds ${(r.triage.p1_cluster_share * 100).toFixed(1)}% of clusters but ${(r.triage.p1_cells_share * 100).toFixed(1)}% of next-day fire cells (ranking by today's hotspots: ${(r.triage.p1_cells_share_persistence * 100).toFixed(1)}%).</p>
    <h3>Forecast model (2019 validation)</h3>
    <p>Average precision 0.308 vs 0.207 for the best "yesterday's fires" baseline. 2019 was also the tuning year; the held-out 2023 season is the real test.</p>`;
}

// ---------- season replay player
const PLAY_SVG = '<svg viewBox="0 0 24 24"><path d="M7 5v14l11-7z" fill="currentColor" stroke="none"/></svg>';
const PAUSE_SVG = '<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>';
const rp = { frames: null, i: 0, timer: null };

async function startReplay() {
  if (!rp.frames) {
    $("loading").hidden = false;
    $("loading").textContent = "Loading the 2019 season...";
    rp.frames = (await api("/outputs/replay_frames_20190701_20191031.json")).frames;
    $("loading").hidden = true;
  }
  document.body.classList.add("replaying");
  $("replay-hud").hidden = false;
  $("replay-slider").max = rp.frames.length - 1;
  drawHudChart();
  map.flyTo({ center: [108.5, -1.2], zoom: 5.1 });
  showReplayFrame(0);
  playReplay(true);
}

function stopReplay() {
  playReplay(false);
  document.body.classList.remove("replaying");
  $("replay-hud").hidden = true;
  render();
}

function playReplay(on) {
  clearInterval(rp.timer);
  rp.timer = on ? setInterval(() => {
    if (rp.i >= rp.frames.length - 1) return playReplay(false);
    showReplayFrame(rp.i + 1);
  }, 700) : null;
  $("replay-play").innerHTML = on ? PAUSE_SVG : PLAY_SVG;
}

function drawHudChart() {
  const f = rp.frames, n = f.length;
  const max = Math.max(...f.map((x) => x.bara_reached));
  const line = (key) => f.map((x, i) => `${(i / (n - 1)) * 1000},${88 - (x[key] / max) * 80}`).join(" ");
  $("hud-chart").innerHTML = `<polyline points="${line("biggest_reached")}" fill="none" stroke="#7d756b" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <polyline points="${line("bara_reached")}" fill="none" stroke="#1e5b4f" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <line id="hud-cursor" x1="0" x2="0" y1="0" y2="90" stroke="#1f1b17" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
}

function showReplayFrame(i) {
  rp.i = i;
  const f = rp.frames[i];
  const done = rp.frames.slice(0, i + 1);
  const bara = done.reduce((a, x) => a + x.bara_reached, 0), big = done.reduce((a, x) => a + x.biggest_reached, 0);
  $("replay-slider").value = i;
  $("hud-date").textContent = f.date;
  $("hud-tally").innerHTML = `Next-day fire cells reached so far: <b>${fmt(bara)}</b> Bara vs <b>${fmt(big)}</b> biggest-first
    <span class="up">${big ? `+${Math.round((bara / big - 1) * 100)}%` : ""}</span>`;
  const cursor = document.getElementById("hud-cursor");
  if (cursor) { const x = (i / (rp.frames.length - 1)) * 1000; cursor.setAttribute("x1", x); cursor.setAttribute("x2", x); }
  const tierRgb = { 1: TIER_RGB.P1, 2: TIER_RGB.P2, 3: TIER_RGB.P3 };
  overlay.setProps({ layers: [
    new deck.ScatterplotLayer({ id: "r-clusters", data: f.clusters.filter((c) => c[2] < 3), radiusUnits: "meters",
      getPosition: (c) => [c[0], c[1]], getRadius: (c) => Math.min(Math.max(Math.sqrt(c[3]) * 900, 1500), 14000),
      getFillColor: (c) => [...tierRgb[c[2]], 190] }),
    new deck.ScatterplotLayer({ id: "r-truth", data: f.clusters.filter((c) => c[4] > 0), radiusUnits: "meters",
      getPosition: (c) => [c[0], c[1]], getRadius: (c) => Math.sqrt(c[4]) * 1100, filled: false, stroked: true,
      getLineColor: [...INK, 190], lineWidthMinPixels: 1.5 }),
    new deck.PathLayer({ id: "r-biggest", data: f.biggest, getPath: (p) => p, getColor: [...TIER_RGB.P3, 170], widthUnits: "pixels", getWidth: 2 }),
    new deck.PathLayer({ id: "r-bara", data: f.bara, getPath: (p) => p, getColor: [...ROUTE_RGB, 240], widthUnits: "pixels", getWidth: 3 }),
  ] });
}

$("replay-btn").onclick = startReplay;
$("replay-exit").onclick = stopReplay;
$("replay-play").onclick = () => playReplay(!rp.timer);
$("replay-slider").oninput = (e) => { playReplay(false); showReplayFrame(Number(e.target.value)); };

// ---------- selection
function selectCluster(id) {
  state.selected = id;
  const c = state.day.clusters.find((x) => x.id === id);
  if (c) map.flyTo({ center: [c.lon, c.lat], zoom: Math.max(map.getZoom(), 8), speed: 1.4 });
  render();
}

// ---------- loading a day
async function loadDay(date) {
  state.date = date;
  state.selected = null;
  state.scenario = null;
  state.selfcheck = null;
  state.chat = [];
  $("date").value = date;
  history.replaceState(null, "", `?date=${date}`);
  $("loading").textContent = "Running Detect, Forecast, Triage, Dispatch...";
  $("loading").hidden = false;
  try {
    state.day = await api(`/api/day?date=${date}`);
  } finally {
    $("loading").hidden = true;
  }
  state.briefing = await api(`/api/briefing?date=${date}`).catch(() => null);
  renderKpis();
  render();
  renderBriefing();
  renderCrews();
  renderChat();
  loadReports();
  field.sig = null;
  loadField();
  const first = state.day.routes[0];
  if (first && !loadDay.done) { map.flyTo({ center: [first.lon, first.lat], zoom: 6.4 }); loadDay.done = true; }
}

function shiftDay(n) {
  const d = new Date(`${state.date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  const s = d.toISOString().slice(0, 10);
  if (s >= state.meta.first_day && s <= state.meta.last_day) loadDay(s);
}

async function refreshSpend() {
  state.meta = await api("/api/meta");
  $("spend").innerHTML = `Meta API <b>$${state.meta.spent_usd.toFixed(2)}</b> of $${state.meta.cap_usd.toFixed(0)}`;
}

$("prev-day").onclick = () => shiftDay(-1);
$("next-day").onclick = () => shiftDay(1);
$("date").onchange = (e) => e.target.value && loadDay(e.target.value);
$("show-p3").onchange = render;
$("show-truth").onchange = render;
document.querySelector('[data-tab="evidence"]').addEventListener("click", renderEvidence);
// ---------- Ask Bara (what-if chat)
const SUGGESTIONS = [
  "What if Palangkaraya had 2 crews?",
  "Which P1 fires does no crew reach today?",
  "Why is C4 a P1?",
  "What if crews could only work 8 hours?",
];
state.chat = [];
function renderChat() {
  const el = $("tab-ask");
  el.innerHTML = `
    <p class="muted">Ask about today's fires or try a what-if. Bara answers by calling the same tools as the map (Muse Spark 1.3 decides which), and every number in the answer is checked against the tool output.</p>
    <div class="chips">${SUGGESTIONS.map((s) => `<button class="chip">${esc(s)}</button>`).join("")}</div>
    <div class="chat-log" id="chat-log">${state.chat.map(chatBubble).join("")}</div>
    <form class="chat-form" id="chat-form"><label class="sr-only" for="chat-input">Question</label>
      <input id="chat-input" autocomplete="off" placeholder="e.g. What if we lose the Banjarbaru base?">
      <button class="btn" type="submit">Ask</button></form>
    ${state.scenario ? `<button class="btn ghost" id="clear-scenario">Hide scenario routes on map</button>` : ""}`;
  el.querySelectorAll(".chip").forEach((b) => (b.onclick = () => sendChat(b.textContent)));
  $("chat-form").onsubmit = (e) => { e.preventDefault(); const v = $("chat-input").value.trim(); if (v) sendChat(v); };
  if ($("clear-scenario")) $("clear-scenario").onclick = () => { state.scenario = null; render(); renderChat(); };
  const log = $("chat-log");
  log.scrollTop = log.scrollHeight;
}
function chatBubble(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.content)}</div>`;
  if (m.pending) return `<div class="msg bot muted">Bara is calling the tools...</div>`;
  const tools = (m.trace || []).map((t) => `<span class="tool">${esc(t.tool)}(${esc(Object.entries(t.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</span>`).join("");
  return `<div class="msg bot">${esc(m.content).replace(/\n/g, "<br>")}
    <div class="msg-meta"><span class="badge ${m.passed ? "ok" : "bad"}">${m.passed ? "GROUNDED" : "CHECK FAILED"}</span> ${m.calls} Muse Spark calls, $${m.cost.toFixed(3)}</div>
    <div class="tools">${tools}</div></div>`;
}
async function sendChat(text) {
  state.chat.push({ role: "user", content: text }, { role: "assistant", pending: true });
  renderChat();
  const history = state.chat.filter((m) => !m.pending).map((m) => ({ role: m.role, content: m.content }));
  try {
    const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ date: state.date, messages: history }) });
    state.chat[state.chat.length - 1] = { role: "assistant", content: r.answer, passed: r.passed, trace: r.tool_trace, calls: r.llm_calls.length, cost: r.cost_usd };
    if (r.scenario_routes) { state.scenario = r.scenario_routes; render(); }
  } catch (e) {
    state.chat[state.chat.length - 1] = { role: "assistant", content: `Failed: ${e.message}`, passed: false, trace: [], calls: 0, cost: 0 };
  }
  renderChat();
  refreshSpend();
}
// ---------- field photo reports
const KIND_RGB = { confirm: [47, 110, 70], escalate: TIER_RGB.P1, new_fire: TIER_RGB.P1, burn_scar: TIER_RGB.P3,
  possible_false_alarm: TIER_RGB.P2, clear: TIER_RGB.P3, unclear: TIER_RGB.P3 };
const rep = { list: [], credits: null, pick: false, lat: null, lon: null, file: null, note: "" };

async function loadReports() {
  rep.list = await api(`/api/reports?date=${state.date}`).catch(() => []);
  if (!rep.credits) rep.credits = await api("/samples/credits.json").catch(() => []);
  renderReports();
  render();
}

function samplePlacement(file) {
  // Demo only: the sample photos were not taken at these clusters; they are placed on today's
  // crew stops to show the workflow (the panel says so).
  const stops = state.day.routes.flatMap((r) => r.stops);
  const at = (k) => stops[k % Math.max(stops.length, 1)] || { lat: -2, lon: 113 };
  if (file.startsWith("burned")) { const s = at(0); return { lat: s.lat - 0.09, lon: s.lon - 0.2 }; }
  const s = at(file.startsWith("marking") ? 0 : file.startsWith("water") ? 2 : 3);
  return { lat: s.lat, lon: s.lon };
}

function renderReports() {
  const el = $("tab-reports");
  el.innerHTML = `
    <p class="muted">A ranger sends a photo and a location. Muse Spark 1.3 says what the photo shows; Bara's rules then match it to the nearest fire cluster and say what it means for today's plan.</p>
    <h3>Try a demo photo</h3>
    <div class="samples">${(rep.credits || []).map((c, i) => `<button class="sample" data-i="${i}" title="${esc(c.title)}"><img src="/samples/${c.file}" alt="${esc(c.description)}" loading="lazy"></button>`).join("")}</div>
    <h3>Send a report</h3>
    <form id="report-form" class="report-form">
      <input type="file" id="report-file" accept="image/*">
      <div class="loc-row"><span class="muted" id="report-loc">${rep.lat == null ? "No location yet" : `${rep.lat.toFixed(4)}, ${rep.lon.toFixed(4)}`}</span>
        <button type="button" class="btn ghost" id="pick-loc">${rep.pick ? "Click on the map..." : "Set location on map"}</button></div>
      <div class="muted" id="report-note">${esc(rep.note)}</div>
      <button class="btn" type="submit" id="report-send">Check photo</button>
    </form>
    <h3>Reports for ${state.date}</h3>
    ${rep.list.length ? rep.list.slice().reverse().map(reportCard).join("") : `<p class="muted">None yet.</p>`}`;
  el.querySelectorAll(".sample").forEach((b) => (b.onclick = () => useSample(Number(b.dataset.i))));
  $("report-file").onchange = (e) => { rep.file = e.target.files[0] || null; rep.note = ""; };
  $("pick-loc").onclick = () => { rep.pick = !rep.pick; renderReports(); };
  $("report-form").onsubmit = sendReport;
}

function reportCard(r) {
  const c = r.classification, d = r.decision;
  if (!c) return `<div class="card"><b>Could not read the photo</b><div class="muted">${esc(r.error)}</div></div>`;
  return `<div class="card report"><img src="${r.photo}" alt="Field photo" loading="lazy">
    <div><div class="card-head"><span class="badge ${d.kind === "confirm" ? "ok" : d.kind === "escalate" || d.kind === "new_fire" ? "bad" : "P3"}">${esc(c.category.replace("_", " "))}</span>
      <span class="right">model confidence ${c.confidence}</span></div>
      <div class="muted">${c.visible.map(esc).join(", ")}</div>
      <p class="decision">${esc(d.action)}</p>
      <div class="muted">${esc(d.place)}, ${esc(d.province)} | ${r.note ? esc(r.note) + " | " : ""}$${Number(r.cost_usd).toFixed(3)}</div></div></div>`;
}

async function useSample(i) {
  const c = rep.credits[i];
  const blob = await (await fetch(`/samples/${c.file}`)).blob();
  rep.file = new File([blob], c.file, { type: "image/jpeg" });
  Object.assign(rep, samplePlacement(c.file));
  rep.note = `Demo photo: ${c.artist}, ${c.license}, Wikimedia Commons. Not taken at this spot; placed here to show the workflow.`;
  renderReports();
}

async function sendReport(e) {
  e.preventDefault();
  if (!rep.file || rep.lat == null) { $("report-note").textContent = "Choose a photo and set a location first."; return; }
  const btn = $("report-send");
  btn.disabled = true; btn.textContent = "Muse Spark is looking at the photo...";
  const form = new FormData();
  form.append("photo", rep.file); form.append("lat", rep.lat); form.append("lon", rep.lon);
  form.append("date", state.date); form.append("note", rep.note);
  try {
    await api("/api/report", { method: "POST", body: form });
    rep.file = null; rep.note = "";
  } catch (err) {
    rep.note = `Failed: ${err.message}`;
  }
  await loadReports();
  refreshSpend();
}

map.on("click", (e) => {
  if (!rep.pick) return;
  rep.pick = false; rep.lat = e.lngLat.lat; rep.lon = e.lngLat.lng;
  renderReports();
});

function reportLayers() {
  const d = state.day;
  const pts = rep.list.filter((r) => r.decision);
  const links = pts.map((r) => { const c = d.clusters.find((x) => x.id === r.decision.nearest_cluster); return c && r.decision.nearest_cluster_km <= 5 ? { from: [r.lon, r.lat], to: [c.lon, c.lat] } : null; }).filter(Boolean);
  return [
    new deck.LineLayer({ id: "report-links", data: links, getSourcePosition: (l) => l.from, getTargetPosition: (l) => l.to, getColor: [...INK, 140], getWidth: 1.5 }),
    new deck.ScatterplotLayer({ id: "reports", data: pts, radiusUnits: "pixels", getRadius: 8, getPosition: (r) => [r.lon, r.lat],
      getFillColor: (r) => KIND_RGB[r.decision.kind] || TIER_RGB.P3, stroked: true, getLineColor: INK, lineWidthMinPixels: 2 }),
  ];
}

(async () => {
  await refreshSpend();
  const q = new URLSearchParams(location.search).get("date");
  loadDay(q || state.meta.default_day);
})();

// ---------- live WhatsApp field reports (src/fusion.py): phone frame and re-planned routes
const field = { data: null, sig: null };
const STATE_LABEL = { active_flames: "flames", smouldering_peat: "smouldering peat", smoke_only: "smoke only",
  burn_scar: "burn scar", no_fire: "no fire", unclear: "unclear" };
const DECISION_RGB = { confirmed: TIER_RGB.P1, new_fire: TIER_RGB.P1, smoke_or_scar: TIER_RGB.P2,
  reported_clear: [47, 110, 70], no_change: TIER_RGB.P3 };

async function loadField() {
  if (!state.date) return;
  const f = await api(`/api/field?date=${state.date}`).catch(() => null);
  if (!f) return;
  const sig = JSON.stringify([state.date, f.reports.map((r) => r.id), f.route_diff]);
  if (sig === field.sig) return;
  field.data = f;
  field.sig = sig;
  renderPhone();
  render();
}

function phoneMessages(r) {
  const rep = r.report || {};
  const time = r.received_at.slice(11, 16);
  const facts = [STATE_LABEL[rep.fire_state] || rep.fire_state, rep.place_text, rep.area_ha != null ? `${rep.area_ha} ha` : null,
    ...(rep.needs || []).filter((n) => n !== "none").map((n) => `needs ${n}`)].filter(Boolean);
  const kind = r.type === "audio" ? "Voice note" : r.type === "text" ? "Message" : r.type;
  return `<div class="bubble sent"><div class="meta"><span>${esc(kind)}</span><span>${esc(time)}</span></div>
      <div class="said">"${esc(r.transcript)}"</div>
      <div class="facts">${facts.map((x) => `<span class="fact">${esc(x)}</span>`).join("")}
        <span class="fact">${r.grounded ? "every field quoted" : "needs review"}</span></div></div>
    <div class="bubble bara"><div class="meta"><span>Bara</span><span>${esc(time)}</span></div>
      ${esc(r.reply)}<div class="draft">Draft reply; sending is not switched on.</div></div>`;
}

function renderPhone() {
  const f = field.data;
  if (!f) return;
  $("phone").hidden = false;
  $("phone-body").innerHTML = f.reports.length ? f.reports.map(phoneMessages).join("")
    : `<p class="phone-empty">No field reports in the last ${f.window_hours} hours. Send a voice note to the Bara test number.</p>`;
  $("phone-body").scrollTop = $("phone-body").scrollHeight;
  const moved = f.route_diff.length ? ` ${f.route_diff.length} crew route(s) re-planned; risk covered ${fmt(f.risk_covered_before, 1)} to ${fmt(f.risk_covered_after, 1)}.` : "";
  $("phone-foot").textContent = `Live reports from the last ${f.window_hours} h, placed on the ${state.date} replay day for this demo.${moved}`;
}

function fieldLayers() {
  const f = field.data;
  if (!f) return [];
  const pins = f.reports.filter((r) => r.location.status === "located");
  const links = pins.filter((r) => r.decision.cluster_lat != null)
    .map((r) => ({ from: [r.location.lon, r.location.lat], to: [r.decision.cluster_lon, r.decision.cluster_lat] }));
  return [
    new deck.PathLayer({ id: "field-replan", data: f.route_diff, widthUnits: "pixels", getWidth: 5,
      getColor: [214, 124, 38, 230], getPath: (c) => c.path_after, capRounded: true, jointRounded: true }),
    new deck.LineLayer({ id: "field-links", data: links, getSourcePosition: (l) => l.from, getTargetPosition: (l) => l.to,
      getColor: [...INK, 160], getWidth: 1.5 }),
    new deck.ScatterplotLayer({ id: "field-pins", data: pins, radiusUnits: "pixels", getRadius: 9,
      getPosition: (r) => [r.location.lon, r.location.lat], getFillColor: (r) => DECISION_RGB[r.decision.kind] || TIER_RGB.P3,
      stroked: true, getLineColor: INK, lineWidthMinPixels: 2 }),
  ];
}

setInterval(loadField, 10000);
