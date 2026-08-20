"""Build the Forest Church design-review page (single self-contained HTML).

Re-run after each design rev; the artifact is redeployed from the output file.
Screenshots come from out/flyby/*.jpg, embedded as data URIs.
"""

from __future__ import annotations

import base64
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLY = ROOT / "out" / "flyby"
OUT = ROOT / "out" / "forest-church-review.html"

imgs = {p.stem: base64.b64encode(p.read_bytes()).decode() for p in sorted(FLY.glob("*.jpg"))}


def img(stem: str) -> str:
    return f"data:image/jpeg;base64,{imgs[stem]}"


DISTRICTS = [
    ("The Town Edge", "a-town-edge", "Where the last houses meet the river meadow",
     "The establishing shot: cobble flush with the verge, rooflines varying by storey count, and "
     "a wood that thins into pasture rather than stopping at a line. Cobble is 0.25 thick against "
     "grass at 0.5, and both used to be laid from a common bottom &mdash; a 15 inch kerb down "
     "both sides of every road, on 1,234 tiles. Surfaces align at the top now.",
     [("shipped", "surfaces align at the top, not the bottom"),
      ("shipped", "roof height follows each building's storey count")]),

    ("The Riverbank", "b-riverbank", "Shallows at the bank, a channel down the middle",
     "This is what read as accidental terracing, and the reading was fair &mdash; it was a trench. "
     "The height field turned out to be flat everywhere except the border, the channel and the "
     "wall, so nothing was stepped by mistake. A three-channel probe found the real problem: "
     "TaleSpire&#39;s water tile is <em>translucent and tints with what is under it</em>, and every "
     "cell of the river had a flush bed &mdash; the palest, flattest case of the three. The bed now "
     "follows distance from the bank, so the edges stay shallow and fordable and the middle goes "
     "dark. The surface is still one flat layer: depth costs nothing, because the water does the "
     "work. Where a street runs along the bank there is a harbour rail now, instead of cobbles "
     "ending at a half-tile cliff.",
     [("shipped", "bed graded by distance from bank: 448 shallow, 660 deep"),
      ("shipped", "quay rail where paving meets water"),
      ("shipped", "open water uses the 2x2 tile: 115 blocks, 460 cells")]),

    ("The Roofscape", "c-roofscape", "Yards cut out of the plans",
     "20 of 51 buildings have a yard cut from one corner now, so the town is not 51 boxes. The "
     "regulariser reduces every imported blob to its largest inscribed rectangle &mdash; which is "
     "what makes wall runs straight and roofs seat &mdash; and the fix was not to put the blobs "
     "back. An L is still built <em>from</em> rectangles, and roof depth has come from a search "
     "inward from the real boundary since the L-terrace bug, so an L roofs correctly where a blob "
     "never could. A notch that would seal a courtyard, or take the facade a door needed, is put "
     "back.",
     [("shipped", "20 of 51 footprints are L-plans with a yard"),
      ("shipped", "notches reverted where they would cost a doorway"),
      ("next", "no porches, wings or lean-tos yet")]),

    ("The Rampart", "d-rampart", "Solid masonry, battlements out, wall-walk behind",
     "A wall a party can be chased along. Two revs ago this same circuit was four parallel fins "
     "of curtain wall with 2.5 ft of daylight between them &mdash; an archer could have shot "
     "clean through the fortification, and every check that read the tile grid called it solid. "
     "Merlons crown only the cells facing out of town; the weathered flags behind them are a "
     "fighting surface two abreast.",
     [("shipped", "full-cell core: no daylight through the wall"),
      ("shipped", "battlements face out, walk paved behind")]),

    ("The Gate Towers", "f-gatehouse", "The circuit rising either side of the road in",
     "Where the main street crosses the wall there used to be eighteen cells of nothing &mdash; a "
     "35 ft breach open to the sky. It became a tunnel with the rampart carried over on a lintel, "
     "and the wall flanking it now rises two courses so the approach reads as a defended entrance "
     "from across the valley. The passage keeps its cart headroom.",
     [("shipped", "wall carried over the road on a lintel"),
      ("shipped", "flanking towers, diagonal jambs included"),
      ("next", "no gate leaves or arch dressing yet")]),

    ("The Back Lanes", "e-back-lanes", "Trodden earth between the houses",
     "872 tiles of lane. A lane starts as ground pinched between buildings and is then walked "
     "outward to the nearest road &mdash; that second step is what makes it work, because houses "
     "stand back from the carriageway and of 121 pinched cells <em>none</em> touched a street. "
     "Requiring a lane to begin at a road found nothing at all. There is a market square now too, "
     "opened where the most street already meets, which is where the town&#39;s own road layout "
     "says the centre is; the well and the market clutter finally have somewhere to stand.",
     [("shipped", "872 lane tiles, walked out to the road"),
      ("shipped", "7x7 market square on the busiest junction"),
      ("shipped", "18 signed trade buildings")]),

    ("The Pinewood", "g-pinewood", "Trees with trunks, spaced so they all arrive",
     "Every conifer used to be a bare canopy cone sitting on the grass. &ldquo;Stackable Pine "
     "Top&rdquo; is the top of a three-piece kit and it was being planted on its own, while cut "
     "stumps were scattered separately nearby &mdash; read together, leaves that did not line up "
     "with any trunk. Worse, 47% of all scenery sat inside another prop&#39;s collider, and "
     "TaleSpire drops those on paste without saying so.",
     [("shipped", "pines stacked from their kit: 557 joints, none misaligned"),
      ("shipped", "0 overlapping props, down from 1,000 of 2,137")]),

    ("The Map Edge", "z-map-edge", "Terraces stepping down out of the map",
     "The falloff, finally caught at an angle that shows it: the ground steps down in irregular "
     "blocks with a ragged outer boundary, so the map ends as country running out rather than as "
     "a slab someone cut a town from. What it cost is the interesting part &mdash; lowering the "
     "border would have stopped that ground counting as &ldquo;at grade&rdquo;, which is how "
     "open-country chunks are recognised, so the taper would have silently disabled the skipping "
     "that drops a fifth of the map. Ground is tested against its own cell&#39;s baseline now. "
     "Ten chunks skipped before, ten after.",
     [("shipped", "terraced, ragged falloff on every border"),
      ("shipped", "skipping survives it: 10 chunks before and after")]),
]

MARK = {"shipped": "&#10003; ", "flaw": "&#9651; ", "next": "&rarr; "}

cards = ""
for name, stem, sub, note, chips in DISTRICTS:
    chiphtml = "".join(
        f'<span class="chip chip-{kind}">{MARK[kind]}{text}</span>' for kind, text in chips)
    cards += f'''
<section class="card">
  <img src="{img(stem)}" alt="{name}: {sub}">
  <div class="card-body">
    <div class="card-head"><h2>{name}</h2><span class="sub">{sub}</span></div>
    <p class="table-note"><span class="tag">At the table</span>{note}</p>
    <div class="chips">{chiphtml}</div>
  </div>
</section>'''

FINDINGS = '<p class="pending">Independent design review in progress &mdash; findings land here on the next update.</p>'
findings_file = ROOT / "out" / "flyby" / "findings.html"
if findings_file.exists():
    FINDINGS = findings_file.read_text(encoding="utf-8")

html = f'''<title>Forest Church</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --ground:#F2EDDF; --surface:#FFFFFF; --ink:#2A2A22; --muted:#6E6A58;
  --line:#D8D2BE; --accent:#B23E27; --pine:#4F6B41; --wheat:#96762A;
  --chip-ok:#E4EAD9; --chip-ok-ink:#3F5A33; --chip-flaw:#F4DFD7; --chip-flaw-ink:#8C3A24;
  --chip-next:#EAE4CF; --chip-next-ink:#6E5A1E;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#1B211C; --surface:#242C25; --ink:#E8E0CD; --muted:#A79F8A;
    --line:#39443A; --accent:#D4593C; --pine:#7A9868; --wheat:#C9A44C;
    --chip-ok:#2C3A2B; --chip-ok-ink:#A9C494; --chip-flaw:#42302A; --chip-flaw-ink:#E0937B;
    --chip-next:#3B3626; --chip-next-ink:#D3BC7A;
  }}
}}
:root[data-theme="dark"] {{
  --ground:#1B211C; --surface:#242C25; --ink:#E8E0CD; --muted:#A79F8A;
  --line:#39443A; --accent:#D4593C; --pine:#7A9868; --wheat:#C9A44C;
  --chip-ok:#2C3A2B; --chip-ok-ink:#A9C494; --chip-flaw:#42302A; --chip-flaw-ink:#E0937B;
  --chip-next:#3B3626; --chip-next-ink:#D3BC7A;
}}
* {{ box-sizing:border-box }}
body {{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:Georgia,"Times New Roman",serif; line-height:1.55;
}}
.wrap {{ max-width:960px; margin:0 auto; padding:0 20px 64px }}
header {{ padding:44px 0 10px; border-bottom:2px solid var(--accent) }}
.kicker {{ font-family:Consolas,monospace; font-size:12px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--accent) }}
h1 {{ font-family:"Palatino Linotype",Palatino,Georgia,serif; font-size:clamp(34px,6vw,52px);
  margin:6px 0 4px; letter-spacing:.01em; text-wrap:balance }}
.deck {{ color:var(--muted); max-width:62ch; margin:4px 0 18px; font-size:17px }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px 26px; padding:14px 0 18px;
  font-family:Consolas,monospace; font-size:13px; color:var(--muted) }}
.stats b {{ color:var(--ink); font-variant-numeric:tabular-nums }}
.stats .ok {{ color:var(--pine) }}
h2 {{ font-family:"Palatino Linotype",Palatino,Georgia,serif; font-size:24px; margin:0 }}
.grid {{ display:grid; gap:28px; margin-top:30px }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; overflow:hidden }}
.card img {{ display:block; width:100%; height:auto; border-bottom:1px solid var(--line) }}
.card-body {{ padding:16px 20px 18px }}
.card-head {{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap }}
.sub {{ color:var(--muted); font-style:italic; font-size:15px }}
.table-note {{ margin:10px 0 12px; font-size:15.5px }}
.tag {{ font-family:Consolas,monospace; font-size:11px; letter-spacing:.12em;
  text-transform:uppercase; color:var(--wheat); margin-right:10px }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px }}
.chip {{ font-family:Consolas,monospace; font-size:12px; padding:3px 10px; border-radius:999px }}
.chip-shipped {{ background:var(--chip-ok); color:var(--chip-ok-ink) }}
.chip-flaw {{ background:var(--chip-flaw); color:var(--chip-flaw-ink) }}
.chip-next {{ background:var(--chip-next); color:var(--chip-next-ink) }}
.section-h {{ margin:44px 0 6px; display:flex; align-items:baseline; gap:14px }}
.section-h h2 {{ font-size:28px }}
.section-h .rule {{ flex:1; border-top:1px solid var(--line) }}
.pending {{ color:var(--muted); font-style:italic }}
ul.findings {{ margin:12px 0 0; padding-left:0; list-style:none }}
ul.findings li {{ padding:10px 0; border-bottom:1px solid var(--line); font-size:15px }}
ul.findings b {{ color:var(--accent) }}
ol.log {{ margin:12px 0 0; padding-left:0; list-style:none }}
ol.log li {{ display:grid; grid-template-columns:130px 1fr; gap:14px;
  padding:10px 0; border-bottom:1px solid var(--line); font-size:15px }}
ol.log .when {{ font-family:Consolas,monospace; font-size:12.5px; color:var(--muted);
  padding-top:3px; font-variant-numeric:tabular-nums }}
ol.log b {{ color:var(--accent); font-weight:600 }}
footer {{ margin-top:40px; color:var(--muted); font-size:13.5px;
  font-family:Consolas,monospace }}
@media (max-width:560px) {{ ol.log li {{ grid-template-columns:1fr }} }}
</style>
<div class="wrap">
<header>
  <div class="kicker">citysmith &middot; design review &middot; rev 18</div>
  <h1>Forest Church</h1>
  <p class="deck">A TaleSpire village generated from a Watabou export &mdash; reviewed district
  by district at avatar eye level, the way a party will actually see it. Every image below is a
  fresh capture of the current build; nothing here is a render or a mock-up.</p>
  <div class="stats">
    <span>tiles <b>187 &times; 180</b> (935 &times; 900 ft)</span>
    <span>buildings <b>51</b></span>
    <span>assets <b>23,772</b> in <b>3</b> chunks</span>
    <span>off-grid tiles <b class="ok">0</b></span>
    <span>main streets <b class="ok">20 ft &mdash; two carts pass</b></span>
    <span>reachable <b class="ok">100%</b></span>
    <span>ground-plane holes <b class="ok">0</b></span>
    <span>overlapping props <b class="ok">0</b></span>
  </div>
</header>

<div class="grid">{cards}</div>

<div class="section-h" id="findings"><h2>Open findings</h2><div class="rule"></div></div>
{FINDINGS}

<div class="section-h"><h2>Design log</h2><div class="rule"></div></div>
<ol class="log">
  <li><span class="when">rev 18 &middot; Aug 20</span><div><b>L-plans, a market square, paved back
    lanes.</b> 20 of 51 footprints now have a yard cut from a corner, the town has a 7&times;7
    square on its busiest junction, and 872 tiles of trodden lane run between the houses. Then
    building access fell from 100% to 96% and I spent two rounds blaming the notching, tightening
    its guards and making it worse each time. The cause was the lanes: <code>LANE</code> was
    missing from the open-space set and the door-priority map, so paving a lane beside a building
    invalidated the only frontage its door could use. <em>A feature that adds a surface has to say
    where that surface belongs in every set that classifies surfaces.</em> Separately,
    <code>reachable_from</code> returns a bool grid rather than a set, so the notch guard's
    membership test was always false and every notch was silently rejected &mdash; the code read
    as though it were cutting L-shapes while emitting 51 rectangles. Caught by asserting on the
    outcome, not the code path.</div></li>
  <li><span class="when">rev 17 &middot; Aug 20</span><div><b>A church that looks like one.</b>
    A survey of the design rather than the geometry: 51 of 51 footprints are perfect rectangles,
    twelve building kinds share two treatments between them, and the map named Forest Church had
    a temple that was a box with cottage thatch on it. Its plan is 6&times;19 &mdash; a nave &mdash;
    so a long, narrow, large civic building now takes a battlemented tower at the narrow end,
    three storeys above its own eaves and carrying its own roof. It is the tallest thing on the
    map, which is what a landmark is for. Eighteen trade buildings hang signs, and houses stopped
    having two front doors. Two changes were <em>rejected</em> by probe: the tiled city roof kit
    does not share the Thatched rotation convention (a side-by-side test came out scattered and
    floating), and every &ldquo;Market Sign&rdquo; in the catalog is Cyberpunk pack.</div></li>
  <li><span class="when">rev 16 &middot; Aug 19</span><div><b>A river with depth, shallows and a
    quay.</b> Two bands that looked like accidental layers turned out to be the river &mdash; the
    height field is flat everywhere else &mdash; but it was reading as a trench, and a probe found
    why: TaleSpire&#39;s water tile is translucent and tints with the bed under it, and every cell
    of the river had a flush bed, the palest case. The bed now grades by distance from the bank,
    which costs nothing because the surface stays one layer. Open water moved to the 2x2 tile.
    Streets on the bank got a harbour rail instead of a cliff edge &mdash; and placing it exposed
    a general bug: <code>place_wall</code> assumed every edge mesh runs along x, so all 26 rails
    landed a quarter tile off-grid. The build&#39;s own off-grid check caught it before the
    board did.</div></li>
  <li><span class="when">rev 15 &middot; Aug 19</span><div><b>The map edge tapers, and chunk
    skipping was taught about it.</b> These could not be done separately: open-country detection
    asked whether ground sat at grade, so lowering the border would have reclassified every
    tapered cell as a feature and silently disabled the skipping that drops a fifth of the map.
    Ground is now tested against its own cell&#39;s baseline, which the taper defines &mdash; a
    cell with no baseline is not background, so a sunken channel still disqualifies its chunk
    while a lowered border does not. The falloff lives on the 2x2 block lattice rather than per
    cell, because the terrain pass lays open ground as 2x2 tiles and a per-cell height field
    breaks every quad; doing it per cell cost a thousand border tiles for a step nobody can see.
    Ten chunks skipped before the change, ten after.</div></li>
  <li><span class="when">rev 14 &middot; Aug 19</span><div><b>Trees with trunks, and scenery that
    survives the paste.</b> 47% of props sat inside another prop&#39;s collider, and TaleSpire
    drops those silently &mdash; so the scatter was making a thin wood plus several hundred assets
    that never arrived. Scatter is collision-checked now, all-or-nothing per group. And the
    conifer had never been a tree: &ldquo;Stackable Pine Top&rdquo; is the canopy cone of a
    Stump&nbsp;&rarr;&nbsp;Middle&nbsp;&rarr;&nbsp;Top kit, planted alone on the grass. Pines are
    stacked from the kit by measured piece height &mdash; 557 joints, none misaligned, no crown
    without a trunk. An earlier attempt at two-piece pines lost a third of its canopies and was
    abandoned; that was the right diagnosis and the wrong fix, because the pieces were overlapping
    <em>vertically</em>.</div></li>
  <li><span class="when">rev 13 &middot; Aug 19</span><div><b>A gatehouse, and docs that match
    the build.</b> The gate was structurally correct but undressed: the curtain ran over it at
    ordinary height, so nothing said a gate was there. The wall flanking each opening now rises
    two courses into towers &mdash; the ring is Chebyshev rather than orthogonal, because the
    circuit stair-steps and an orthogonal-only ring left holes in the towers. Every user-facing
    doc was then audited against the code instead of against memory: the skill file was still
    advertising a scale default two revs stale, and the README still showed chunk filenames from
    a different scale.</div></li>
  <li><span class="when">rev 12 &middot; Aug 19</span><div><b>A rampart that is actually solid.</b>
    The medieval castle kit is <em>curtain wall</em> &mdash; its pieces are 0.5 deep, authored to
    stand on a cell boundary. Laid one per cell across a wall four cells thick, they were four
    parallel fins with a 0.5-tile slot between each pair: daylight through the whole circuit. The
    mass is now a full-cell core with the thin pieces facing it. Gates stopped being unbuilt holes
    &mdash; 18 cells of main street crossing the wall, open to the sky &mdash; and became tunnels
    with the rampart carried over on a lintel. Battlements crown only the cells facing out of
    town; the rest is paved as a wall-walk, its stone chosen by pasting six candidates side by
    side rather than by guessing. Streets stopped sitting a quarter tile below the grass.</div></li>
  <li><span class="when">rev 10 &middot; Aug 19</span><div><b>No more holes in the ground.</b>
    Open-country chunks were judged one at a time, so two chunks the town had built all the way
    around got dropped. An unpasted chunk is not grass &mdash; it is bare board &mdash; so that
    pasted as a 24&times;48 tile rectangular void in the middle of the map. Trimming is now a
    flood fill inward from the border, and a build fails if anything enclosed is dropped. Roof
    ring depth now comes from a search inward from a block&#39;s real boundary instead of from its
    bounding box, which had floated the edge cells of L-shaped terraces a course too high.</div></li>
  <li><span class="when">rev 9 &middot; Aug 19</span><div><b>Landscape given variety, water given
    a shore.</b> A forest of one species reads as a plantation, so trees now mix 62% conifer /
    29% broadleaf / 9% dead. A course of shingle along the waterline hides the cut edge where
    grass met the sunken channel &mdash; 748 tiles of it. The citadel wall, which read as
    disconnected pillars at the old scale, is now 307 of 308 cells orthogonally connected: the
    scale change dissolved it. 19,440 assets, 3 pastes, 51 buildings, 100% access.</div></li>
  <li><span class="when">rev 8 &middot; Aug 19</span><div><b>Scale set by play; streets set by
    carts.</b> At the old scale only 31% of buildings had a 3&times;3 interior &mdash; most of
    the town could not meaningfully be entered. At <code>--house-ft 35</code>, 94% can, and
    playability saturates there. Main streets widen to 4 tiles because two 10 ft carts must
    pass; gate arches and bridges match. Slabs now cut on a spatial grid: 8-tile detection skips
    21% of the map as open country, then survivors pack back to 3 pastes. 187&times;179 tiles,
    51 buildings, 19,172 assets, 100% access.</div></li>
  <li><span class="when">rev 7 &middot; Aug 19</span><div><b>Roof convention learned from a real
    build.</b> Downloaded 9 community slabs into a local study library and decoded them. A
    hand-built forest cottage gave up the whole convention: hip roofs assemble as concentric
    rings, one cell in and one piece up per course, closed with a flat cap &mdash; and its
    rotations (edges N=6 E=0 S=18 W=12, corners NW=12 NE=6 SW=18 SE=0) are a quarter turn off
    the wall convention, which is exactly why ours looked mis-set. Switched to the Thatched kit,
    the one that actually has a ridge cap.</div></li>
  <li><span class="when">rev 6 &middot; Aug 19</span><div><b>Civic fabric, wall variety, upper
    floors.</b> Buildings were hollow boxes visible through the new windows &mdash; 314 upper-floor
    slabs added. Cottages took timber-framed windows; temple, guildhall, manor and barracks took
    dressed stone with arched openings and a fancier door. Wall variants dealt per
    building.</div></li>
  <li><span class="when">rev 5.1 &middot; Aug 19</span><div><b>Fly-bys re-shot on the rebuilt
    board.</b> A one-tree probe proved TaleSpire drops overlapping props on paste (the community
    "missing parts" bug) &mdash; stump+canopy pines lost a third of their canopies. Trees are now
    single-piece and jittered; stumps stand alone as cut trees. New finding: the citadel wall
    rasterises into disconnected pillars along diagonals.</div></li>
  <li><span class="when">rev 5 &middot; Aug 19</span><div><b>Rectangular building modules.</b>
    Audits of a rowhouse block and the standalone stable traced every assembly defect &mdash;
    floating roof tiles, orphan gables, wall columns, walls through roofs &mdash; to blobby
    rasterised footprints. Borrowing the convention every community house slab uses, footprints
    now regularise to their largest inscribed rectangle, slivers are absorbed, and contiguous
    same-height blocks share one roof. The stable rebuilt as a clean 4&times;2 module with a
    complete wall circuit, and building access hit <b>100%</b> for the first time
    (the "sealed courtyards" were blob artifacts). Board re-paste pending.</div></li>
  <li><span class="when">rev 4 &middot; Aug 19</span><div><b>Review findings, first pass.</b>
    479 windows break the blank facades (stable per-building pattern, sparser at street level);
    fixed the field-fringe bug that laid 2&times;2 tilled blocks on 1&times;1 leftover cells.
    Slabs rebuilt; board re-paste pending.</div></li>
  <li><span class="when">rev 3 &middot; Aug 19</span><div><b>District dressing.</b> Pines, ferns,
    wheat, straw, well and barrels scattered by district; town wall gained a parapet cap. Wide
    buildings hipped. Fixed a normalization bug that had dragged every tile 0.775 off the
    grid.</div></li>
  <li><span class="when">rev 2 &middot; Aug 18</span><div><b>Roof kit relearned from a probe
    board.</b> Tiered slopes at the kit's true 2-unit pitch; upright gable walls seated flush;
    per-building storey heights (33 &times; 1fl, 16 &times; 2fl, 2 &times; 3fl).</div></li>
  <li><span class="when">rev 1 &middot; Aug 18</span><div><b>Palette rebuilt around pinned
    assets.</b> Grass, cobbles, tilled earth, real water and the Village Roof kit replaced desert
    floors, shogun interiors and tent roofs. Streets widened to a 10 ft minimum.</div></li>
</ol>

<footer>forest_church.json &rarr; citysmith &rarr; forest-01/02.slab.txt &middot; board: Unknown Realm 6 &middot; 1 tile = 5 ft</footer>
</div>
'''

OUT.write_text(html, encoding="utf-8")
print(f"wrote {OUT} ({len(html) // 1024} KB)")
