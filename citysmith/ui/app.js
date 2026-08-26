/* citysmith build UI.
 *
 * Vanilla, because the core is stdlib-only and has no build step, so this has
 * none either: no bundler, no framework, no CDN. The page is served under a
 * Content-Security-Policy of `default-src 'self'`, so every fetch below is a
 * relative path and there is nothing here that could talk to another origin
 * even by accident. No key of any kind reaches this file.
 *
 * Three rules that are not style:
 *
 *  1. EVERY finding is rendered in full, at its own level. The report is the
 *     product for anyone who cannot open TaleSpire, and a green tick throws
 *     away sentences that cost sessions to write. Filtering hides rows; it
 *     never rewrites, shortens or summarises one.
 *  2. PASTE ORDER IS NOT FILENAME ORDER. The chunk list is rendered by walking
 *     `result.paste_order` and looking each file up -- so the numbering on the
 *     screen is the server's order by construction. Nothing here sorts.
 *  3. Server text goes in with `textContent`, never `innerHTML`. A town name
 *     comes out of a file somebody downloaded.
 */

"use strict";

const $ = (id) => document.getElementById(id);

const LEVEL_WORD = { fail: "FAIL", warn: "WARN", pass: "ok" };
const POLL_MS = 400;

let options = null;
let sources = [];
let polling = null;
let lastResult = null;

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

function showFailure(message) {
  const panel = $("verdict-panel");
  panel.hidden = false;
  panel.className = "panel lv-fail";
  $("verdict-word").textContent = "BUILD STOPPED";
  $("verdict-counts").textContent = "";
  $("verdict-summary").textContent = message;
}

/* -- rendering ------------------------------------------------------------- */

function render(result) {
  lastResult = result;
  renderVerdict(result);
  renderFindings(result);
  renderChunks(result);
  renderRaster(result);
}

function renderVerdict(result) {
  const panel = $("verdict-panel");
  const worst = result.failed ? "fail" : result.worst;
  panel.hidden = false;
  panel.className = "panel lv-" + worst;
  $("verdict-word").textContent = result.failed
    ? "BUILD FAILED"
    : worst === "warn" ? "BUILT, WITH WARNINGS" : "BUILT CLEAN";
  const c = result.counts;
  $("verdict-counts").textContent =
    c.fail + " fail / " + c.warn + " warn / " + c.pass + " pass";
  $("verdict-summary").textContent = result.summary
    + " -- largest slab " + result.largest_slab_bytes.toLocaleString()
    + " bytes of 30,720";
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
    list.appendChild(row);
  }

  const filters = $("filters");
  clear(filters);
  for (const level of ["fail", "warn", "pass"]) {
    const count = result.counts[level];
    const button = el("button", null,
      LEVEL_WORD[level] + " " + count);
    button.type = "button";
    button.setAttribute("aria-pressed", "true");
    button.disabled = count === 0;
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

/* -- wiring ---------------------------------------------------------------- */

$("form").addEventListener("submit", startBuild);
$("rescan").addEventListener("click", rescan);
$("source").addEventListener("change", showSourceDetail);
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
});

boot();
