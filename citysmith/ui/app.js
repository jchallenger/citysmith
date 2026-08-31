let slabsLoaded = false;
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

/* The import options are measured in feet, and feet have fractions. Rounding
 * them the way `numberOrNull` rounds a seed would quietly change what was
 * typed, and `Number.isFinite` is doing real work: the server rejects Infinity
 * and NaN, so sending one is a 400 for something the box can produce. */
function decimalOrNull(input) {
  const raw = input.value.trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/* -- the import half ------------------------------------------------------- */

/* The seven options `citysmith import` takes. They are keyed by the importers'
 * own keyword names, which is what `/api/options` sends defaults under and what
 * the server passes straight through -- one spelling from the box to the
 * reader. `read` says how to get a value out of the control. */
const IMPORT_FIELDS = [
  { id: "core_only", read: (e) => e.checked },
  { id: "margin_feet", read: decimalOrNull },
  { id: "house_frontage_ft", read: decimalOrNull },
  { id: "cluster_gap_ft", read: decimalOrNull },
  { id: "clip", read: (e) => e.checked },
  { id: "fences", read: (e) => e.checked },
  { id: "name", read: (e) => e.value.trim() || null },
];

/* Which format accepts which option, from the server. A label saying "FTG only"
 * that this file decided for itself is a second copy of `importers`' option
 * sets, and the copy is the one that goes stale. */
function markImportFields() {
  const accepts = (options && options.import_options) || {};
  const mfcg = new Set(accepts.mfcg || []);
  const ftg = new Set(accepts.ftg || []);
  for (const field of IMPORT_FIELDS) {
    const label = $(field.id + "-label")
      || document.querySelector('label[for="' + field.id + '"]');
    if (!label) continue;
    const only = ftg.has(field.id) && !mfcg.has(field.id) ? " (FTG only)"
      : mfcg.has(field.id) && !ftg.has(field.id) ? " (MFCG only)" : "";
    if (only) label.append(el("span", "hint", only));
  }
}

/* A layout.json is already imported, so every control in there is inert on one.
 * Hidden rather than disabled: a disabled row still reads as "this could apply
 * here", and it cannot. `formBody` omits the fields to match, so the request
 * says nothing about an import that is not going to happen. */
function showImportFields(kind) {
  $("import-fields").hidden = kind !== "geojson";
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
  for (const field of IMPORT_FIELDS) {
    const input = $(field.id);
    const value = d[field.id];
    if (input.type === "checkbox") input.checked = value !== false;
    else input.value = value === null || value === undefined ? "" : value;
  }
  markImportFields();
  await rescan();
  if (paintPlatform()) await pasteRescan();
}

async function rescan() {
  const note = $("scan-note");
  note.textContent = "scanning...";
  try {
    const data = await getJSON("/api/sources");
    sources = data.sources;
    /* One scan, both screens. The camera picker offering last week's list
       while the build picker shows this week's is the kind of drift that
       makes a page untrustworthy. */
    fillCameraSources();
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
    showImportFields(null);
    return;
  }
  const kb = Math.max(1, Math.round(chosen.size / 1024));
  line.textContent = chosen.kind + " -- " + chosen.detail + " (" + kb + " KB)";
  showImportFields(chosen.kind);
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
  const body = {
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
  /* Only for a GeoJSON. On a layout.json the server would take these, use none
   * of them and say so in the log -- a line about a choice nobody made. */
  const chosen = sources.find((s) => s.id === body.source);
  if (chosen && chosen.kind === "geojson") {
    for (const field of IMPORT_FIELDS) body[field.id] = field.read($(field.id));
  }
  return body;
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
// -- the Slabs screen -------------------------------------------------------
//
// Deliberately the smallest screen in the page: one list, one picture, one
// legend. The server does the decoding and the drawing, so nothing here knows
// what a placement is -- the same split every other screen makes.

async function loadSlabs() {
  const pick = document.getElementById("slab-pick");
  const note = document.getElementById("slab-note");
  try {
    const data = await getJSON("/api/slabs");
    pick.innerHTML = "";
    for (const s of data.slabs) {
      const opt = document.createElement("option");
      opt.value = s.path;
      opt.textContent = s.path + "  (" + s.bytes.toLocaleString() + " B)";
      pick.append(opt);
    }
    note.textContent = data.slabs.length + " slab(s)";
    if (data.slabs.length) showSlab(pick.value);
  } catch (err) {
    note.textContent = "could not list slabs: " + err.message;
  }
}

async function showSlab(path) {
  const img = document.getElementById("slab-view");
  const legend = document.getElementById("slab-legend");
  const note = document.getElementById("slab-note");
  // Encoded per SEGMENT: a slab lives in a subdirectory and the slashes have
  // to survive, but nothing else does.
  const enc = path.split("/").map(encodeURIComponent).join("/");
  img.src = "/api/slabs/svg/" + enc;
  document.getElementById("slab-view-panel").hidden = false;
  try {
    const data = await getJSON("/api/slabs/legend/" + enc);
    note.textContent = data.placements.toLocaleString() + " placements";
    legend.innerHTML = "";
    for (const row of data.legend) {
      const el = document.createElement("div");
      el.className = "legend-row";
      el.innerHTML =
        '<span class="swatch" style="background:' + row.colour + '"></span>' +
        '<b>' + row.count + '</b> ' + row.name +
        ' <span class="hint">' + row.size.join(" x ") +
        "  [" + row.folder + "]</span>";
      legend.append(el);
    }
    document.getElementById("slab-legend-panel").hidden = false;
  } catch (err) {
    legend.textContent = "could not read it: " + err.message;
  }
}

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
      ? " -- " + plural(plan.missing.length, "slab") + " NAMED BUT MISSING: "
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
$("camera-form").addEventListener("submit", frameShot);
$("cam-drive").addEventListener("click", driveCamera);
wirePreview();
wirePlanClick();
$("cam-colour").addEventListener("change", () => {
  if (previewLast) drawPreview(previewLast);
});
$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  for (const other of $("tabs").children) other.classList.toggle("is-on", other === tab);
  for (const screen of document.querySelectorAll(".screen")) {
    screen.classList.toggle("is-on", screen.id === "screen-" + tab.dataset.screen);

  // The slab list is scanned on first sight of the screen, not on page load:
  // a thousand-slab output directory should not be walked by someone who only
  // came to build a town.
  if (tab.dataset.screen === "slabs" && !slabsLoaded) {
    slabsLoaded = true;
    document.getElementById("slab-pick")
      .addEventListener("change", (e) => showSlab(e.target.value));
    document.getElementById("slab-rescan")
      .addEventListener("click", loadSlabs);
    loadSlabs();
  }
  }
  /* Rescan on arrival: the usual route here is straight off a build, and a
   * stale list would offer the previous town's paste order for the new one. */
  if (tab.dataset.screen === "paste" && !pastePolling && paintPlatform()) {
    pasteRescan();
  }
});

boot();


/* -- the camera screen ----------------------------------------------------- *
 *
 * The other two screens do something to the world; this one only predicts.
 * That is the point. Every camera command in `ts.ps1` is *relative*, so a
 * session that only issues them ends up over the void wondering where the map
 * went -- and the one thing nobody could do before was ask what a frame would
 * contain *before* taking it. Fences were built, shipped and reviewed over two
 * sessions from crops that contained none of them.
 *
 * The plan view is drawn here because it is four points and a rectangle. The
 * FOOTPRINT is not: it comes from `citysmith.camera` on the server and is
 * never re-derived in JavaScript. Two implementations of a frustum is two
 * frustums, and only one of them was measured against the game.
 */

/* Taken off the empty <svg> in the page, never written down here: the page is
 * checked for any absolute URL scheme, and an XML namespace looks exactly like
 * an origin to a check blunt enough to be worth having. */
function svgNS() {
  return $("cam-plan").namespaceURI;
}

function svg(tag, cls, attrs) {
  const node = document.createElementNS(svgNS(), tag);
  if (cls) node.setAttribute("class", cls);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  return node;
}

/* Two blank lines then a comment, appended under a plan that leans on a
 * constant nobody measured. Written as an escape rather than a multi-line
 * literal so the shape of the string survives being edited by tools. */
const ASSUMED_NOTE =
  "\n\n# this plan leans on constants that are NOT measurements: ";

/* Layouts only. A GeoJSON export has not been rasterised, so it has no tile
 * coordinates to draw in -- the server refuses it and this does not offer it. */
function fillCameraSources() {
  const select = $("cam-source");
  const chosen = select.value;
  clear(select);
  const none = el("option", null, "none -- empty tile space");
  none.value = "";
  select.appendChild(none);
  for (const source of sources) {
    if (source.kind !== "layout") continue;
    const option = el("option", null, source.label + "  " + source.detail);
    option.value = source.id;
    if (source.id === chosen) option.selected = true;
    select.appendChild(option);
  }
}

function camRect() {
  return [Number($("cam-x0").value), Number($("cam-z0").value),
          Number($("cam-x1").value), Number($("cam-z1").value)];
}

function camFail(message) {
  $("cam-verdict-panel").hidden = false;
  $("cam-verdict").textContent = message;
  $("cam-verdict").className = "verdict is-fail";
  for (const id of ["cam-view-panel", "cam-script-panel"]) $(id).hidden = true;
}

async function frameShot(event) {
  event.preventDefault();
  const rect = camRect();
  if (!(rect[2] > rect[0] && rect[3] > rect[1])) {
    camFail("The second corner has to be past the first.");
    return;
  }
  const body = {
    source: $("cam-source").value || undefined,
    rect: rect,
    yaw: Number($("cam-yaw").value),
    pitch: Number($("cam-pitch").value),
    margin: Number($("cam-margin").value),
    width: Number($("cam-width").value),
    height: Number($("cam-height").value),
  };
  /* All five or none. A partial pose would be sent as zeroes and planned from
   * a camera at the origin looking north, which is a confident answer to a
   * question nobody asked. */
  const at = ["cam-at-fx", "cam-at-fz", "cam-at-dist", "cam-at-yaw",
              "cam-at-pitch"].map((id) => $(id).value.trim());
  if (at.some((v) => v !== "")) {
    if (at.some((v) => v === "")) {
      camFail("Give all five of the current-pose numbers, or none of them.");
      return;
    }
    body.at = at.map(Number);
  }
  let data;
  try {
    data = await postJSON("/api/camera/plan", body);
  } catch (err) {
    camFail(String(err.message || err));
    return;
  }
  paintCamera(data, rect);
}

function paintCamera(data, rect) {
  const f = data.framing;
  const p = f.pose;

  $("cam-verdict-panel").hidden = false;
  const verdict = $("cam-verdict");
  verdict.textContent = f.fits
    ? "This fits in one shot."
    : "This does NOT fit in one shot.";
  verdict.className = "verdict " + (f.fits ? "is-ok" : "is-warn");
  $("cam-detail").textContent =
    f.note + " -- focus (" + p.fx + ", " + p.fz + "), bearing " + p.yaw +
    " deg, pitch " + p.pitch + " deg, slant range " + p.dist +
    " tiles, eye " + p.eye_y + " tiles above the board" +
    (data.sees_horizon ? ". The horizon is in shot." : ".");

  $("cam-view-panel").hidden = false;
  planSvg(data, rect);
  let scale =
    "Scale at the centre of the frame: " + data.px_per_tile[0] +
    " px per tile across, " + data.px_per_tile[1] + " along. A 1.5-tile " +
    "obstruction under the cursor would slide a paste " +
    data.anchor_slide_1_5 + " tiles toward the camera.";
  /* The count is the whole reason for drawing the board. Fences were built,
   * shipped and reviewed twice from crops that held none of them, and nothing
   * on the screen said so. */
  if (data.board) {
    const held = data.board.in_frame.length;
    const total = data.board.buildings.length;
    scale += "  " + data.board.name + ": " + held + " of " + total +
      " buildings wholly in frame" +
      (held === 0 ? " -- NOTHING of the town is in this shot." : ".");
    if (data.board.dropped) {
      scale += "  (" + data.board.dropped + " of the smallest are not drawn.)";
    }
  }
  $("cam-scale").textContent = scale;

  /* Seed the preview from the framing, then let the mouse take over. */
  $("cam-preview-panel").hidden = false;
  previewPose = [p.fx, p.fz, p.dist, p.yaw, p.pitch];
  requestView({ kind: "none", dx: 0, dy: 0, ticks: 0 });

  $("cam-script-panel").hidden = false;
  $("cam-script").textContent = data.plan
    ? data.plan.script
    : "Fill in \"where the camera is now\" and the moves appear here. " +
      "Every camera command is relative, so there is nothing to say without " +
      "a pose to start from.";
  if (data.plan && data.plan.assumed.length) {
    $("cam-script").textContent += ASSUMED_NOTE + data.plan.assumed.join(", ");
      data.plan.assumed.join(", ");
  }

  paintRig(data.rig);
}

/* The board, the frustum and the target into one box, and draw.
 *
 * **z runs DOWN here, as it does in `city-raster.svg`.** This started the
 * other way, with a key that read "north is up" -- and that was two mistakes
 * in one line. The raster the Build screen shows draws tile z downward, so two
 * plan views of the same town would have disagreed in handedness, which is the
 * kind of error that looks entirely plausible while being a mirror. And
 * "north" was a claim about the game's compass that was never measured;
 * "+z down, same as the raster" is a fact about our own renderer.
 */
function planSvg(data, rect) {
  const pts = data.footprint;
  const board = data.board;
  const xs = pts.map((q) => q[0]).concat([rect[0], rect[2]]);
  const zs = pts.map((q) => q[1]).concat([rect[1], rect[3]]);
  if (board) {
    /* Fit the whole town, not just the frame. The point of drawing the board
     * is to see WHERE on it the frame falls. */
    xs.push(board.extent[0], board.extent[2]);
    zs.push(board.extent[1], board.extent[3]);
  }
  const pad = 4;
  const x0 = Math.min.apply(null, xs) - pad, x1 = Math.max.apply(null, xs) + pad;
  const z0 = Math.min.apply(null, zs) - pad, z1 = Math.max.apply(null, zs) + pad;
  const w = 640;
  const h = Math.max(200, Math.min(520, Math.round(w * (z1 - z0) / (x1 - x0))));
  const sx = (v) => ((v - x0) / (x1 - x0)) * w;
  const sz = (v) => ((v - z0) / (z1 - z0)) * h;
  const box = (b, cls) => svg("rect", cls, {
    x: sx(b[0]).toFixed(1), y: sz(b[1]).toFixed(1),
    width: Math.max(1, sx(b[2]) - sx(b[0])).toFixed(1),
    height: Math.max(1, sz(b[3]) - sz(b[1])).toFixed(1),
  });

  const rad = Math.PI / 180;
  const p = data.framing.pose;
  const back = p.dist * Math.cos(p.pitch * rad);
  const eye = [p.fx - Math.sin(p.yaw * rad) * back,
               p.fz - Math.cos(p.yaw * rad) * back];

  const node = $("cam-plan");
  clear(node);
  node.setAttribute("viewBox", "0 0 " + w + " " + h);
  /* Kept so a click on the map can be turned back into tiles. The forward
   * transform is built here and nowhere else, so the inverse has to be taken
   * from it rather than recomputed -- two transforms would drift and the map
   * would send the camera somewhere adjacent to where you pointed. */
  planTransform = { x0: x0, x1: x1, z0: z0, z1: z1, w: w, h: h };

  /* Board first, so the frustum washes over it and you can see through to
   * what is underneath. Water, then buildings, then the wall. */
  if (board) {
    node.appendChild(box(board.extent, "cam-extent"));
    for (const ring of board.water) {
      node.appendChild(svg("polygon", "cam-water", {
        points: ring.map((q) => sx(q[0]).toFixed(1) + "," + sz(q[1]).toFixed(1))
                    .join(" "),
      }));
    }
    const lit = new Set(board.in_frame);
    board.buildings.forEach((b, i) => {
      node.appendChild(box(b, lit.has(i) ? "cam-bldg is-in" : "cam-bldg"));
    });
    for (const ring of board.walls) {
      node.appendChild(svg("polyline", "cam-wall", {
        points: ring.map((q) => sx(q[0]).toFixed(1) + "," + sz(q[1]).toFixed(1))
                    .join(" "),
        fill: "none",
      }));
    }
  }

  node.appendChild(svg("polygon", "cam-frustum", {
    points: pts.map((q) => sx(q[0]).toFixed(1) + "," + sz(q[1]).toFixed(1)).join(" "),
  }));
  node.appendChild(box(rect, "cam-target"));
  node.appendChild(svg("line", "cam-axis", {
    x1: sx(eye[0]).toFixed(1), y1: sz(eye[1]).toFixed(1),
    x2: sx(p.fx).toFixed(1), y2: sz(p.fz).toFixed(1),
  }));
  node.appendChild(svg("circle", "cam-eye", {
    cx: sx(eye[0]).toFixed(1), cy: sz(eye[1]).toFixed(1), r: 5,
  }));
  node.appendChild(svg("circle", "cam-focus", {
    cx: sx(p.fx).toFixed(1), cy: sz(p.fz).toFixed(1), r: 4,
  }));
  const key = svg("text", "cam-key", { x: 8, y: 18 });
  key.textContent = "+x right, +z down -- the same way up as " +
                    "city-raster.svg. Shaded: what the camera sees. " +
                    "Dashed: what you asked for.";
  node.appendChild(key);
}

function paintRig(rig) {
  $("cam-rig-panel").hidden = false;
  const assumed = rig.constants.filter((c) => !c.measured);
  let note = assumed.length
    ? assumed.length + " of " + rig.constants.length + " constants are " +
      "assumptions rather than measurements. Anything leaning on them is a " +
      "guess with arithmetic on top."
    : "Every constant here was measured off the running game.";
  if (rig.unknown_keys && rig.unknown_keys.length) {
    note += "  config/camera.json also has keys the model does not know, " +
            "and they do nothing: " + rig.unknown_keys.join(", ") + ".";
  }
  $("cam-rig-note").textContent = note;

  const table = $("cam-rig");
  clear(table);
  const head = el("tr");
  for (const label of ["evidence", "constant", "value", "where it came from"]) {
    head.appendChild(el("th", null, label));
  }
  table.appendChild(head);
  for (const c of rig.constants) {
    const row = el("tr", c.measured ? "is-measured" : "is-assumed");
    row.appendChild(el("td", null, c.measured ? "measured" : "ASSUMED"));
    const name = el("td");
    name.appendChild(el("code", null, c.name));
    row.appendChild(name);
    row.appendChild(el("td", null, String(Number(c.value.toFixed(5)))));
    row.appendChild(el("td", null, c.source));
    table.appendChild(row);
  }
}


/* -- the preview, and why the mouse does what it does ---------------------- *
 *
 * Dragging here runs the RIG -- the measured control model -- on the server.
 * Left-drag is `orbit`, wheel is Ctrl+scroll, shift-drag pans. Those are the
 * game's own controls with the game's own measured sensitivities, which has
 * one consequence worth stating: **the preview cannot show you a shot the
 * camera cannot take.** Drag the pitch past 78 degrees and it stops, because
 * that is where the real one stops.
 *
 * The browser projects nothing. It posts a pose and a drag, and draws the
 * quads that come back. One implementation of the camera, and it is the
 * measured one.
 */

/* -- what the preview is allowed to claim about a building ----------------- *
 *
 * Colour by STOREYS by default, and it is not a style choice. Measured on the
 * layouts: East Tradebourne is 709 houses out of 991, so colouring by kind
 * leaves 72% of the town one colour and says almost nothing; storeys run
 * 169/575/247 across one, two and three and vary street by street. Kind is
 * still offered, because on a small town it is the interesting axis -- and
 * because being able to see that a quarter is all one kind IS the finding.
 *
 * Every kind the layouts actually contain has an entry. The first version had
 * six and the towns use nine, so `shop`, `guildhall`, `barracks` and `stable`
 * fell through to the house grey -- an absent distinction that looked like a
 * present one.
 */
const KIND_INK = {
  house: [126, 124, 116],
  shop: [163, 141, 92],
  smithy: [130, 88, 64],
  tavern: [176, 118, 66],
  warehouse: [116, 106, 88],
  temple: [150, 142, 172],
  guildhall: [140, 112, 160],
  barracks: [110, 122, 136],
  stable: [126, 114, 92],
  manor: [166, 132, 152],
  apothecary: [112, 152, 126],
  market: [170, 138, 84],
};
/* Storeys: one hue, three clearly separated lightnesses. A sequence should
 * read as a sequence -- three unrelated hues would say "three categories". */
const FLOOR_INK = {
  1: [92, 104, 120],
  2: [132, 146, 164],
  3: [186, 198, 212],
  4: [226, 234, 244],
};

function previewInkFor(face, scene) {
  if (face.kind === "water") return [58, 96, 112];
  if (face.kind === "ground") return null;
  const mode = $("cam-colour").value;
  const b = (scene.buildings || [])[face.b];
  if (mode === "floors" && b) {
    return FLOOR_INK[Math.min(4, b.floors)] || FLOOR_INK[1];
  }
  return KIND_INK[face.kind] || KIND_INK.house;
}

function paintLegend(scene) {
  const box = $("cam-legend");
  clear(box);
  const mode = $("cam-colour").value;
  const seen = new Map();
  for (const b of scene.buildings || []) {
    const key = mode === "floors" ? Math.min(4, b.floors) : b.kind;
    seen.set(key, (seen.get(key) || 0) + 1);
  }
  if (!seen.size) return;
  const keys = [...seen.keys()].sort((a, c) =>
    mode === "floors" ? a - c : String(a).localeCompare(String(c)));
  for (const key of keys) {
    const ink = mode === "floors"
      ? (FLOOR_INK[key] || FLOOR_INK[1]) : (KIND_INK[key] || KIND_INK.house);
    const item = el("span");
    const swatch = el("i");
    swatch.style.background = "rgb(" + ink.join(",") + ")";
    item.appendChild(swatch);
    item.appendChild(document.createTextNode(
      (mode === "floors" ? key + " storey" : key) + " × " + seen.get(key)));
    box.appendChild(item);
  }
}

/* Point in polygon, so the page can say what you are pointing at. Faces come
 * furthest-first, so the LAST one containing the cursor is the nearest -- the
 * same order the painter drew them in, which is why this needs no depth of
 * its own. */
function faceUnder(scene, x, y) {
  for (let i = scene.faces.length - 1; i >= 0; i--) {
    const f = scene.faces[i];
    if (f.kind === "ground" || f.b === undefined) continue;
    const p = f.pts;
    let inside = false;
    for (let a = 0, c = p.length - 1; a < p.length; c = a++) {
      const [xa, ya] = p[a], [xc, yc] = p[c];
      if ((ya > y) !== (yc > y)
          && x < (xc - xa) * (y - ya) / (yc - ya) + xa) inside = !inside;
    }
    if (inside) return f;
  }
  return null;
}

function describeBuilding(b) {
  if (!b) return "Point at a building to name it.";
  const bits = [];
  if (b.name) bits.push(b.name);
  bits.push(b.kind);
  bits.push(b.floors + (b.floors === 1 ? " storey" : " storeys"));
  bits.push(b.size[0] + " x " + b.size[1] + " tiles");
  bits.push("at (" + b.at[0] + ", " + b.at[1] + ")");
  if (b.stone) bits.push("stone");
  return bits.join("  --  ");
}

let planTransform = null;
let previewPose = null;       /* [fx, fz, dist, yaw, pitch] */
let previewBusy = false;      /* one request in flight at a time */
let previewQueued = null;     /* the drag accumulated while it was busy */
let previewLast = null;       /* the last scene drawn */

function previewCanvas() {
  return $("cam-preview");
}

/* Coalesced. A drag fires mousemove far faster than a round trip, and sending
 * every one would queue a hundred frames the user has already dragged past.
 * One in flight, one waiting, and the waiting one accumulates. */
async function requestView(drag) {
  if (!previewPose) return;
  if (previewBusy) {
    if (!previewQueued || previewQueued.kind !== drag.kind) {
      previewQueued = Object.assign({}, drag);
    } else {
      previewQueued.dx += drag.dx || 0;
      previewQueued.dy += drag.dy || 0;
      previewQueued.ticks += drag.ticks || 0;
    }
    return;
  }
  previewBusy = true;
  const canvas = previewCanvas();
  try {
    const scene = await postJSON("/api/camera/view", {
      source: $("cam-source").value || undefined,
      pose: previewPose,
      drag: drag,
      width: canvas.width,
      height: canvas.height,
    });
    const p = scene.pose;
    previewPose = [p.fx, p.fz, p.dist, p.yaw, p.pitch];
    drawPreview(scene);
  } catch (err) {
    $("cam-preview-note").textContent = "preview failed: " +
      (err.message || err);
  } finally {
    previewBusy = false;
    const next = previewQueued;
    previewQueued = null;
    if (next) requestView(next);
  }
}

function drawPreview(scene) {
  previewLast = scene;
  const canvas = previewCanvas();
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#10131a";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (const face of scene.faces) {
    const ink = previewInkFor(face, scene);
    const s = face.shade;
    if (face.kind === "ground" || ink === null) {
      /* The grid is lines, not a filled shape: a closed ground ring filled
       * would paint over everything behind it. */
      ctx.strokeStyle = "rgba(150,150,140,0.22)";
      ctx.lineWidth = 1;
      strokePath(ctx, face.pts);
      continue;
    }
    ctx.fillStyle = "rgb(" + Math.round(ink[0] * s) + "," +
      Math.round(ink[1] * s) + "," + Math.round(ink[2] * s) + ")";
    fillPath(ctx, face.pts);
  }

  const p = scene.pose;
  let note = "bearing " + p.yaw.toFixed(1) + " deg, pitch " +
    p.pitch.toFixed(1) + " deg, slant range " + p.dist.toFixed(1) +
    " tiles, eye " + p.eye_y.toFixed(1) + " above the board. Looking at (" +
    p.fx.toFixed(1) + ", " + p.fz.toFixed(1) + ").";
  const stops = [];
  if (scene.at_stop.pitch_max) stops.push("the top of the pitch range");
  if (scene.at_stop.pitch_min) stops.push("the bottom of the pitch range");
  if (scene.at_stop.dist_max) stops.push("the far end of Ctrl+scroll");
  if (scene.at_stop.dist_min) stops.push("the near end of Ctrl+scroll");
  if (stops.length) {
    /* Say it, rather than letting the drag feel broken. A control against its
     * stop is indistinguishable from a dead one, and this project has misread
     * that in the game itself more than once. */
    note += "  AT A STOP: " + stops.join(" and ") +
      " -- the real camera goes no further either.";
  }
  if (scene.dropped) note += "  (" + scene.dropped + " faces not drawn.)";
  $("cam-preview-note").textContent = note;
  paintLegend(scene);
}

function fillPath(ctx, pts) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fill();
}

function strokePath(ctx, pts) {
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.stroke();
}

/* Mouse. The canvas is drawn at its own pixel size and displayed scaled to the
 * panel, so a drag in CSS pixels has to be converted before it means anything
 * to a model that thinks in client pixels. */
function previewDragScale() {
  const canvas = previewCanvas();
  const rect = canvas.getBoundingClientRect();
  return rect.width ? canvas.width / rect.width : 1;
}

/* Click the overhead map to look somewhere else.
 *
 * This is the same gesture TaleSpire itself has -- a double RIGHT click on the
 * board centres the camera on the point clicked (measured: the frame moves
 * 39.06 against a 0.42 noise floor, while a double LEFT click does nothing).
 * So the map is not inventing an interaction; it is offering the one the game
 * already has, on a view where you can see where you are sending it. */
function wirePlanClick() {
  const node = $("cam-plan");
  node.addEventListener("click", (event) => {
    if (!planTransform || !previewPose) return;
    const t = planTransform;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    /* The SVG is letterboxed by `max-height` and `xMidYMid meet`, so the
     * displayed box is not the element box. Work out the drawn rectangle
     * before inverting, or a click lands offset by the letterbox. */
    const scale = Math.min(rect.width / t.w, rect.height / t.h);
    const drawnW = t.w * scale, drawnH = t.h * scale;
    const ox = (rect.width - drawnW) / 2, oy = (rect.height - drawnH) / 2;
    const u = (event.clientX - rect.left - ox) / scale;
    const v = (event.clientY - rect.top - oy) / scale;
    if (u < 0 || v < 0 || u > t.w || v > t.h) return;
    const x = t.x0 + (u / t.w) * (t.x1 - t.x0);
    const z = t.z0 + (v / t.h) * (t.z1 - t.z0);
    previewPose = [x, z, previewPose[2], previewPose[3], previewPose[4]];
    $("cam-hover").textContent =
      "looking at (" + x.toFixed(1) + ", " + z.toFixed(1) + ")";
    requestView({ kind: "none", dx: 0, dy: 0, ticks: 0 });
  });
}

function wirePreview() {
  const canvas = previewCanvas();
  let dragging = null;
  let last = null;

  canvas.addEventListener("pointerdown", (event) => {
    if (!previewPose) return;
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("is-dragging");
    dragging = (event.shiftKey || event.button === 2) ? "pan" : "orbit";
    last = [event.clientX, event.clientY];
    event.preventDefault();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) {
      /* Not a drag: say what is under the cursor. This is the only place the
       * page uses the per-face building index, and it is why `render` bothers
       * to send one. */
      if (!previewLast) return;
      const rect = canvas.getBoundingClientRect();
      const k = previewDragScale();
      const f = faceUnder(previewLast,
                          (event.clientX - rect.left) * k,
                          (event.clientY - rect.top) * k);
      $("cam-hover").textContent = describeBuilding(
        f ? (previewLast.buildings || [])[f.b] : null);
      return;
    }
    const k = previewDragScale();
    const dx = Math.round((event.clientX - last[0]) * k);
    const dy = Math.round((event.clientY - last[1]) * k);
    if (!dx && !dy) return;
    last = [event.clientX, event.clientY];
    requestView({ kind: dragging, dx: dx, dy: dy, ticks: 0 });
  });
  for (const done of ["pointerup", "pointercancel", "pointerleave"]) {
    canvas.addEventListener(done, () => {
      dragging = null;
      canvas.classList.remove("is-dragging");
    });
  }
  /* Right-drag pans, so the context menu has to go -- the same gesture the
   * game uses for a precise pan. */
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    if (!previewPose) return;
    event.preventDefault();
    requestView({ kind: "scroll", dx: 0, dy: 0,
                  ticks: event.deltaY > 0 ? -1 : 1 });
  }, { passive: false });
}

async function driveCamera() {
  if (!previewPose) return;
  const at = ["cam-at-fx", "cam-at-fz", "cam-at-dist", "cam-at-yaw",
              "cam-at-pitch"].map((id) => $(id).value.trim());
  if (at.some((v) => v === "")) {
    $("cam-drive-note").textContent =
      "Fill in \"where the camera is now\" first -- every move is relative, " +
      "so there is nothing to plan from.";
    return;
  }
  $("cam-drive-note").textContent = "driving...";
  try {
    const out = await postJSON("/api/camera/drive", {
      at: at.map(Number),
      pose: previewPose,
      width: Number($("cam-width").value),
      height: Number($("cam-height").value),
    });
    $("cam-drive-note").textContent = out.text;
    /* The game is now where the plan said it would be, near enough, so the
     * "camera is now" fields follow -- otherwise the next drive would plan
     * from a pose two moves stale. This is dead reckoning and it says so:
     * read the camera back with camera_read.py to re-anchor. */
    if (out.landed) {
      const e = out.landed;
      $("cam-at-fx").value = e.fx;
      $("cam-at-fz").value = e.fz;
      $("cam-at-dist").value = e.dist;
      $("cam-at-yaw").value = e.yaw;
      $("cam-at-pitch").value = e.pitch;
    }
  } catch (err) {
    $("cam-drive-note").textContent = "failed: " + (err.message || err);
  }
}
