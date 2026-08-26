/* citysmith build UI.
 *
 * Vanilla, because the core is stdlib-only and has no build step, so this has
 * none either: no bundler, no framework, no CDN. The page is served under a
 * Content-Security-Policy of `default-src 'self'`, so every fetch below is a
 * relative path and there is nothing here that could talk to another origin
 * even by accident. No key of any kind reaches this file.
 *
 * Four rules that are not style:
 *
 *  1. EVERY finding is rendered in full, at its own level. The report is the
 *     product for anyone who cannot open TaleSpire, and a green tick throws
 *     away sentences that cost sessions to write. Filtering COLLAPSES rows and
 *     the chip keeps their count; it never rewrites, shortens, summarises or
 *     drops one. `ok` starts collapsed because twenty findings of which
 *     fifteen are passes buries the five that are not -- but "ok 15" is on
 *     screen and one click has them back. `fail` and `warn` are never
 *     collapsed to begin with.
 *  2. BUILDING AND VERIFYING ARE TWO VERDICTS. A build that wrote its slabs
 *     succeeded even when verify found faults in the map, and saying BUILD
 *     FAILED over four pasteable slabs sends people looking for files that are
 *     already on disk. `renderVerdict` reports the files, then the findings,
 *     and never merges them.
 *  3. PASTE ORDER IS NOT FILENAME ORDER. The chunk list is rendered by walking
 *     `result.paste_order` and looking each file up -- so the numbering on the
 *     screen is the server's order by construction. Nothing here sorts.
 *  4. Server text goes in with `textContent`, never `innerHTML`. A town name
 *     comes out of a file somebody downloaded.
 */

"use strict";

const $ = (id) => document.getElementById(id);

const LEVEL_WORD = { fail: "FAIL", warn: "WARN", pass: "ok" };
const POLL_MS = 400;

/* Levels that start collapsed. Collapsed, not omitted: the chip carries the
 * count and one click brings them back. Never `fail` or `warn`. */
const COLLAPSED = new Set(["pass"]);

let options = null;
let sources = [];
let polling = null;
let lastResult = null;

let plans = [];
let pastePolling = null;

/* -- small helpers --------------------------------------------------------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function fileUrl(name) {
  return "/api/files/" + name.split("/").map(encodeURIComponent).join("/");
}

/* The grabs are NOT under the output directory -- grab.ps1 writes to
 * out/flyby beside the repository whatever --out-dir says -- so they have
 * their own route with its own root and its own allowlist. */
function shotUrl(name) {
  return "/api/paste/shots/" + name.split("/").map(encodeURIComponent).join("/");
}

function plural(n, word) {
  return n + " " + word + (n === 1 ? "" : "s");
}

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function fillSelect(select, values, chosen) {
  clear(select);
  for (const value of values) {
    const option = el("option", null, value);
    option.value = value;
    if (value === chosen) option.selected = true;
    select.appendChild(option);
  }
}

function numberOrNull(input) {
  const raw = input.value.trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.round(value) : null;
}

/* -- boot ------------------------------------------------------------------ */

async function boot() {
  try {
    options = await getJSON("/api/options");
  } catch (err) {
    $("formerror").textContent = "Could not reach the server: " + err.message;
    return;
  }
  $("outdir").textContent = options.out_dir;
  const d = options.defaults;
  fillSelect($("style"), options.styles, d.style);
  fillSelect($("fence_style"), options.fence_styles, d.fence_style);
  fillSelect($("hour"), options.hours, d.hour);
  $("chunk_tiles").value = d.chunk_tiles;
  $("storeys").value = d.storeys;
  $("stem").value = d.stem;
  $("raster_scale").value = d.raster_scale;
  await rescan();
  if (paintPlatform()) await pasteRescan();
}

async function rescan() {
  const note = $("scan-note");
  note.textContent = "scanning...";
  try {
    const data = await getJSON("/api/sources");
    sources = data.sources;
  } catch (err) {
    note.textContent = "scan failed: " + err.message;
    return;
  }
  const select = $("source");
  const previous = select.value;
  clear(select);
  for (const source of sources) {
    const option = el("option", null, source.label);
    option.value = source.id;
    select.appendChild(option);
  }
  if (previous && sources.some((s) => s.id === previous)) select.value = previous;
  note.textContent = sources.length + " file(s)";
  showSourceDetail();
}

function showSourceDetail() {
  const chosen = sources.find((s) => s.id === $("source").value);
  const line = $("source-detail");
  if (!chosen) {
    line.textContent = "No layout or GeoJSON found. Import one, or drop an "
      + "export into the working directory and rescan.";
    return;
  }
  const kb = Math.max(1, Math.round(chosen.size / 1024));
  line.textContent = chosen.kind + " -- " + chosen.detail + " (" + kb + " KB)";
}

/* -- the request ----------------------------------------------------------- */

/* Named parameters, typed here as well as on the server: there is no field on
 * this form whose value becomes a command, a flag or a path. */
function formBody() {
  const crop = ["crop_x", "crop_z", "crop_w", "crop_d"].map((id) => numberOrNull($(id)));
  const anyCrop = crop.some((v) => v !== null);
  if (anyCrop && crop.some((v) => v === null)) {
    throw new Error("Crop needs all four numbers, or none.");
  }
  return {
    source: $("source").value,
    stem: $("stem").value.trim(),
    style: $("style").value,
    seed: numberOrNull($("seed")) ?? 0,
    storeys: numberOrNull($("storeys")) ?? 3,
    chunk_tiles: numberOrNull($("chunk_tiles")) ?? options.defaults.chunk_tiles,
    max_assets: numberOrNull($("max_assets")),
    npc_budget: numberOrNull($("npc_budget")),
    fence_style: $("fence_style").value,
    hour: $("hour").value,
    raster_scale: numberOrNull($("raster_scale")) ?? 3,
    roofs: $("roofs").checked,
    bridges: $("bridges").checked,
    quarters: $("quarters").checked,
    npcs: $("npcs").checked,
    keep_open_country: $("keep_open_country").checked,
    per_building: $("per_building").checked,
    by_region: $("by_region").checked,
    multi_slab: $("multi_slab").checked,
    crop: anyCrop ? { x: crop[0], z: crop[1], w: crop[2], d: crop[3] } : null,
  };
}

async function startBuild(event) {
  event.preventDefault();
  if (polling) return;
  $("formerror").textContent = "";
  let body;
  try {
    body = formBody();
  } catch (err) {
    $("formerror").textContent = err.message;
    return;
  }

  $("run").disabled = true;
  $("run").textContent = "Building...";
  $("idle-note").hidden = true;
  clear($("log"));
  $("verdict-panel").hidden = true;
  $("report-panel").hidden = true;
  $("chunk-panel").hidden = true;
  $("raster-panel").hidden = true;

  let job;
  try {
    job = await postJSON("/api/build", body);
  } catch (err) {
    finish();
    $("formerror").textContent = err.message;
    return;
  }
  watch(job.job);
}

/* Poll, rather than hold a request open. A big town is minutes; a request that
 * blocked for those minutes would look identical to a hung server, and a
 * dropped page would leave a half-open stream behind. */
function watch(jobId) {
  let after = -1;
  polling = setInterval(async () => {
    let snapshot;
    try {
      snapshot = await getJSON("/api/build/" + jobId + "?after=" + after);
    } catch (err) {
      log("lost contact with the server: " + err.message);
      finish();
      return;
    }
    after = snapshot.next - 1;
    for (const event of snapshot.events) log(event.text);
    if (snapshot.state === "running") return;
    finish();
    if (snapshot.state === "error") {
      showFailure(snapshot.error || "the build stopped without saying why");
    } else if (snapshot.result) {
      render(snapshot.result);
    }
  }, POLL_MS);
}

function finish() {
  if (polling) clearInterval(polling);
  polling = null;
  $("run").disabled = false;
  $("run").textContent = "Build";
}

function log(text) {
  const pane = $("log");
  pane.appendChild(document.createTextNode(text + "\n"));
  pane.scrollTop = pane.scrollHeight;
}

/* The one case where the BUILD did fail: the job stopped and produced nothing.
 * The verify row is emptied rather than left showing the previous build's
 * verdict beside this one's failure. */
function showFailure(message) {
  const panel = $("verdict-panel");
  panel.hidden = false;
  panel.className = "panel lv-fail";
  $("verdict-word").textContent = "BUILD STOPPED";
  $("verdict-counts").textContent = "";
  $("verdict-summary").textContent = message;
  $("verify-row").className = "verdict verify is-hidden";
  $("verify-word").textContent = "";
  $("verify-counts").textContent = "";
  $("verify-line").textContent = "";
}

/* -- rendering ------------------------------------------------------------- */

function render(result) {
  lastResult = result;
  renderVerdict(result);
  renderFindings(result);
  renderChunks(result);
  renderRaster(result);
}

/* Two verdicts, deliberately. Reaching here at all means the job finished, so
 * the BUILD succeeded: the files are on disk and can be pasted. What verify
 * found is a separate sentence about the map. Rolling them together rendered
 * BUILD FAILED over four written, pasteable slabs, which sends a reader
 * looking for output that is already there.
 *
 * The FAIL findings are not softened by this, and that is the point: they stay
 * red, expanded, first in the list and counted on the chip. They are findings
 * about the town, not a failure to produce one. */
function renderVerdict(result) {
  const panel = $("verdict-panel");
  panel.hidden = false;
  panel.className = "panel";

  const slabs = result.chunks.length;
  const cap = 30720;
  const pct = Math.round((result.largest_slab_bytes / cap) * 100);
  $("verdict-word").textContent = slabs ? "BUILT" : "BUILT NOTHING";
  $("verdict-counts").textContent = slabs
    ? plural(slabs, "slab") + " written -- largest "
      + result.largest_slab_bytes.toLocaleString() + " of "
      + cap.toLocaleString() + " bytes (" + pct + "%)"
    : "no slab was written";
  $("verdict-summary").textContent = result.summary;

  const worst = result.worst;
  const row = $("verify-row");
  row.className = "verdict verify lv-" + worst;
  $("verify-word").textContent =
    worst === "fail" ? "VERIFY: FAULTS FOUND"
    : worst === "warn" ? "VERIFY: WARNINGS" : "VERIFY: CLEAN";
  const c = result.counts;
  $("verify-counts").textContent =
    c.fail + " fail / " + c.warn + " warn / " + c.pass + " pass";
  $("verify-line").textContent = worst === "pass"
    ? "Nothing to answer for."
    : "These are findings about the map, not about producing it. Every slab "
      + "above was written and can be pasted; read them below before you do.";
}

function renderFindings(result) {
  $("report-panel").hidden = false;
  const list = $("findings");
  clear(list);

  /* Order comes from the server, which is `Report.text()`'s order: worst
   * first. Do not sort. */
  for (const finding of result.findings) {
    const row = el("li", "finding lv-" + finding.level);
    row.dataset.level = finding.level;
    row.appendChild(el("span", "lvl", LEVEL_WORD[finding.level] || finding.level));
    row.appendChild(el("span", "check", finding.check));
    /* The whole sentence. No clamp, no ellipsis, no "show more". */
    row.appendChild(el("p", "detail", finding.detail));
    /* Rendered, then collapsed -- the row is in the DOM either way, so a
     * find-in-page and a chip click both reach it. */
    if (COLLAPSED.has(finding.level)) row.classList.add("is-hidden");
    list.appendChild(row);
  }

  const filters = $("filters");
  clear(filters);
  for (const level of ["fail", "warn", "pass"]) {
    const count = result.counts[level];
    const button = el("button", null, LEVEL_WORD[level] + " " + count);
    button.type = "button";
    /* The chip is the receipt. `ok 15` with the chip up is the whole of what
     * collapsing costs: nothing is hidden without saying how much. */
    button.setAttribute("aria-pressed", String(!COLLAPSED.has(level)));
    button.disabled = count === 0;
    button.title = count + " " + LEVEL_WORD[level] + " finding(s)"
      + (COLLAPSED.has(level) ? " -- collapsed; click to show" : "");
    button.addEventListener("click", () => {
      const on = button.getAttribute("aria-pressed") !== "true";
      button.setAttribute("aria-pressed", String(on));
      for (const row of list.children) {
        if (row.dataset.level === level) row.classList.toggle("is-hidden", !on);
      }
    });
    filters.appendChild(button);
  }
}

function renderChunks(result) {
  $("chunk-panel").hidden = false;
  const ft = result.tile_size * 5;
  $("chunk-meta").textContent =
    result.rows + " row(s) x " + result.cols + " col(s) of "
    + result.tile_size + "x" + result.tile_size + " tiles (" + ft + "x" + ft + " ft)"
    + "  |  budget " + result.chunk_budget.toLocaleString() + " assets"
    + (result.budget_from_board_size ? " (from board size)" : "");

  const map = $("gridmap");
  clear(map);
  map.style.gridTemplateColumns = "repeat(" + Math.max(1, result.cols) + ", 10px)";
  for (const row of result.grid) {
    for (const covered of row) map.appendChild(el("i", covered ? "on" : null));
  }

  const byFile = new Map(result.chunks.map((c) => [c.file, c]));
  const list = $("chunks");
  clear(list);
  let layer = null;
  /* `paste_order` is authoritative. The anchor chunk is written LAST so the
   * anchor cell is still bare board for every paste before it; an alphabetical
   * sort moves it into the middle and the ones after it inherit its height. */
  for (const file of result.paste_order) {
    const chunk = byFile.get(file);
    if (!chunk) continue;
    if (chunk.layer !== layer) {
      layer = chunk.layer;
      if (layer) list.appendChild(el("li", "layer", layer));
    }
    const row = el("li");
    const name = el("div", "file");
    const link = el("a", null, file);
    link.href = fileUrl(file);
    link.setAttribute("download", "");
    name.appendChild(link);
    row.appendChild(name);
    const bits = [
      "x " + chunk.x0 + "-" + (chunk.x1 - 1),
      "z " + chunk.z0 + "-" + (chunk.z1 - 1),
      chunk.assets.toLocaleString() + " assets",
      chunk.size_bytes.toLocaleString() + " bytes",
    ];
    if (chunk.cells > 1) bits.push(chunk.cells + " cells");
    if (chunk.buildings) bits.push(chunk.buildings + " buildings");
    row.appendChild(el("div", "span", bits.join("  ")));
    list.appendChild(row);
  }

  const skipped = $("skipped");
  clear(skipped);
  for (const chunk of result.skipped) {
    skipped.appendChild(el("li", null,
      chunk.label + "  x " + chunk.x0 + "-" + (chunk.x1 - 1)
      + "  z " + chunk.z0 + "-" + (chunk.z1 - 1)
      + "  " + chunk.assets.toLocaleString() + " assets (open country, not written)"));
  }

  $("paste-help").textContent = result.help;
}

function renderRaster(result) {
  if (!result.raster_svg) return;
  $("raster-panel").hidden = false;
  const img = $("raster");
  img.src = fileUrl(result.raster_svg) + "?built=" + Date.now();
  img.style.width = "";
  $("zoom").value = "100";
  $("zoom-note").textContent = "100%";

  const files = $("files");
  clear(files);
  const extras = [
    ["paste order", result.paste_order_file],
    ["NPC manifest", result.npc_manifest],
    ["multi-slab document", result.multislab],
  ];
  for (const [label, name] of extras) {
    if (!name) continue;
    if (files.childNodes.length) files.appendChild(document.createTextNode("  |  "));
    const link = el("a", null, label);
    link.href = fileUrl(name);
    files.appendChild(link);
  }
}

/* -- the paste screen ------------------------------------------------------ *
 *
 * Windows only, and the server says which. Off Windows this screen is one
 * sentence and no control: a disabled button reads as a broken feature, and a
 * platform limit is not one.
 *
 * The preconditions are the reason this screen exists rather than a shell
 * one-liner. A tiled run is up to 102 chunks and the better part of half an
 * hour of driven input, and every chunk of it lands a course high if a build
 * plane is up -- with nothing wrong in any file. So it is checked before the
 * run, checked AGAIN on the server before anything is spawned, and an
 * UNKNOWN reading refuses exactly like an ON one. */

function paintPlatform() {
  const paste = (options && options.paste) || { available: false, note: "" };
  const note = $("paste-platform");
  const controls = $("paste-controls");
  if (paste.available) {
    note.hidden = true;
    controls.hidden = false;
    return true;
  }
  note.hidden = false;
  note.textContent = paste.note;
  controls.hidden = true;
  return false;
}

async function pasteRescan() {
  const note = $("paste-scan-note");
  note.textContent = "scanning...";
  let data;
  try {
    data = await getJSON("/api/paste/plans");
  } catch (err) {
    note.textContent = "scan failed: " + err.message;
    return;
  }
  plans = data.plans;
  const select = $("paste-stem");
  const previous = select.value;
  clear(select);
  for (const plan of plans) {
    const option = el("option", null, plan.stem);
    option.value = plan.stem;
    select.appendChild(option);
  }
  if (previous && plans.some((p) => p.stem === previous)) select.value = previous;
  note.textContent = plural(plans.length, "build") + " with a paste order";
  showPlanDetail();
}

function showPlanDetail() {
  const plan = plans.find((p) => p.stem === $("paste-stem").value);
  const line = $("paste-plan-detail");
  if (!plan) {
    line.textContent = "Nothing in the output directory has a "
      + "<stem>-paste-order.txt. Build a town first.";
    return;
  }
  if (!$("paste-name").value.trim()) $("paste-name").value = plan.stem;
  const kb = Math.max(1, Math.round(plan.bytes / 1024));
  line.textContent = plural(plan.chunks, "chunk") + ", " + kb + " KB"
    + (plan.missing.length
      ? "  --  " + plural(plan.missing.length, "slab") + " NAMED BUT MISSING: "
        + plan.missing.join(", ")
      : "");
}

function renderChecks(preflight) {
  const list = $("preflight-list");
  clear(list);
  for (const check of preflight.checks) {
    const row = el("li", "check-row st-" + check.state);
    row.appendChild(el("span", "lvl",
      check.state === "ok" ? "ok" : check.state === "fail" ? "NO" : "--"));
    row.appendChild(el("span", "check", check.name));
    row.appendChild(el("p", "detail", check.detail));
    /* What the probe actually said, verbatim. A reading you cannot see is a
     * reading you cannot argue with, and this project has acted on a confident
     * "ON" that was a photograph of grass. */
    if (check.raw) row.appendChild(el("pre", "raw", check.raw));
    list.appendChild(row);
  }
  $("preflight-note").textContent = preflight.ok
    ? "ready"
    : "not ready -- " + preflight.refusal;
  $("preflight-note").className = "hint " + (preflight.ok ? "st-ok" : "st-fail");
}

async function runPreflight() {
  const button = $("preflight");
  button.disabled = true;
  $("preflight-note").textContent = "checking...";
  $("preflight-note").className = "hint";
  try {
    renderChecks(await postJSON("/api/paste/preflight", {}));
  } catch (err) {
    clear($("preflight-list"));
    $("preflight-note").textContent = err.message;
    $("preflight-note").className = "hint st-fail";
  } finally {
    button.disabled = false;
  }
}

async function startPaste(event) {
  event.preventDefault();
  if (pastePolling) return;
  $("paste-error").textContent = "";

  const body = {
    stem: $("paste-stem").value,
    name: $("paste-name").value.trim() || $("paste-stem").value,
    shot_every: numberOrNull($("paste-shot-every")) ?? 1,
  };

  $("paste-run").disabled = true;
  $("paste-run").textContent = "Pasting...";
  $("paste-idle-note").hidden = true;
  clear($("paste-log"));
  clear($("paste-chunks"));
  $("paste-verdict-panel").hidden = true;
  $("paste-chunk-panel").hidden = true;

  let job;
  try {
    job = await postJSON("/api/paste", body);
  } catch (err) {
    finishPaste();
    $("paste-error").textContent = err.message;
    return;
  }
  watchPaste(job.job);
}

/* A poll returns only what has not been seen, so anything counted across the
 * whole run is counted HERE and not off the last snapshot -- which holds two
 * events out of two hundred and would report a finished map as one chunk. */
function watchPaste(jobId) {
  let after = -1;
  let total = 0;
  let landed = 0;
  pastePolling = setInterval(async () => {
    let snapshot;
    try {
      snapshot = await getJSON("/api/paste/" + jobId + "?after=" + after);
    } catch (err) {
      pasteLog("lost contact with the server: " + err.message);
      finishPaste();
      return;
    }
    after = snapshot.next - 1;
    for (const event of snapshot.events) {
      pasteLog(event.text);
      if (event.stage === "preflight" && event.checks) {
        renderChecks({ ok: event.ok, checks: event.checks, refusal: event.text });
      }
      if (event.stage === "plan") total = event.total;
      if (event.stage === "chunk") {
        landed = event.index;
        renderPastedChunk(event, total);
      }
    }
    if (snapshot.state === "running") return;
    finishPaste();
    showPasteVerdict(snapshot, landed, total);
  }, POLL_MS);
}

function finishPaste() {
  if (pastePolling) clearInterval(pastePolling);
  pastePolling = null;
  $("paste-run").disabled = false;
  $("paste-run").textContent = "Paste";
}

function pasteLog(text) {
  const pane = $("paste-log");
  pane.appendChild(document.createTextNode(text + "\n"));
  pane.scrollTop = pane.scrollHeight;
}

/* One row per chunk, in the order the run reported them -- which is the
 * manifest's order, which is not filename order. The grabs hang off the row
 * they were taken for, so "which chunk is down and what did it look like" is
 * one glance rather than an alt-tab. */
function renderPastedChunk(event, total) {
  $("paste-chunk-panel").hidden = false;
  $("paste-chunk-meta").textContent =
    event.index + " of " + (event.total || total) + " down";
  const row = el("li");
  row.appendChild(el("div", "file", event.file));
  const shots = event.shots || [];
  if (shots.length) {
    const strip = el("div", "shots");
    for (const shot of shots) {
      const link = el("a");
      link.href = shotUrl(shot.name);
      link.target = "_blank";
      link.rel = "noopener";
      link.title = shot.name;
      const img = el("img");
      img.src = shotUrl(shot.name);
      img.alt = "TaleSpire after chunk " + event.index
        + (shot.view ? " (" + shot.view + ")" : "");
      img.loading = "lazy";
      link.appendChild(img);
      strip.appendChild(link);
    }
    row.appendChild(strip);
  } else {
    row.appendChild(el("div", "span", "no grab for this chunk"));
  }
  $("paste-chunks").appendChild(row);
}

function showPasteVerdict(snapshot, landed, total) {
  const panel = $("paste-verdict-panel");
  panel.hidden = false;
  const ok = snapshot.state === "done";
  panel.className = "panel lv-" + (ok ? "pass" : "fail");
  $("paste-verdict-word").textContent = ok ? "PASTED" : "NOT PASTED";
  $("paste-verdict-counts").textContent =
    landed + " of " + (total || landed) + " chunks down";
  $("paste-verdict-line").textContent = ok
    ? "Every chunk went down at one cursor cell with the camera straight "
      + "down. Walk the joins before trusting a seam: a watercourse or a road "
      + "edge along a row reads exactly like a step."
    : (snapshot.error || "the run stopped without saying why");
}

/* -- wiring ---------------------------------------------------------------- */

$("form").addEventListener("submit", startBuild);
$("rescan").addEventListener("click", rescan);
$("source").addEventListener("change", showSourceDetail);
$("paste-form").addEventListener("submit", startPaste);
$("paste-rescan").addEventListener("click", pasteRescan);
$("paste-stem").addEventListener("change", showPlanDetail);
$("preflight").addEventListener("click", runPreflight);
$("zoom").addEventListener("input", () => {
  const percent = Number($("zoom").value);
  const img = $("raster");
  if (img.naturalWidth) img.style.width = (img.naturalWidth * percent / 100) + "px";
  $("zoom-note").textContent = percent + "%";
});
$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  for (const other of $("tabs").children) other.classList.toggle("is-on", other === tab);
  for (const screen of document.querySelectorAll(".screen")) {
    screen.classList.toggle("is-on", screen.id === "screen-" + tab.dataset.screen);
  }
  /* Rescan on arrival: the usual route here is straight off a build, and a
   * stale list would offer the previous town's paste order for the new one. */
  if (tab.dataset.screen === "paste" && !pastePolling && paintPlatform()) {
    pasteRescan();
  }
});

boot();
