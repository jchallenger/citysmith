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
    ("The Town Edge", "a-town-edge", "Cleared ground between houses and wood",
     "Trees used to scatter at a flat rate regardless of the town, so pines stood tight against "
     "walls and filled the yards the notches had just cut &mdash; a village losing to the forest "
     "rather than one that cleared ground to live in. Density is gated on distance from the "
     "nearest building now: 1 tree of 561 stands within three cells of a wall. Those cells get "
     "worked-yard clutter instead, so a cut corner reads as somebody&#39;s back yard. Weed tufts "
     "and spilled pebbles break the seam where grass meets paving.",
     [("shipped", "woodland thins toward the built-up area"),
      ("shipped", "yards get firewood and a barrow, not pines"),
      ("shipped", "95 verge props softening surface seams")]),

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

    ("The Roofscape", "c-roofscape", "A ridge per wing, and a valley where they meet",
     "A hip roof is a rectangle&#39;s answer to being roofed. Forced over a notched plan it gave a "
     "valid height field and incoherent ridges &mdash; from directly overhead a 6&times;6 came out "
     "a clean pyramid while an L and a U came out with ridge lines meeting at angles that resolved "
     "into nothing. Not a corner-piece problem, which is what an earlier rev assumed and got wrong: "
     "with axis-aligned notches the reflex corner falls on a <em>vertex between</em> cells, so no "
     "cell can carry an inner piece. A footprint is cut into maximal rectangles now and each is "
     "roofed as its own hip, so an L reads as a main range with a side wing.",
     [("shipped", "one hip per rectangular wing, largest first"),
      ("shipped", "chimney stays on the main wing"),
      ("shipped", "verified overhead, the angle that condemned the old version")]),

    ("The Rampart", "d-rampart", "Coursed masonry, and 928 assets lighter",
     "A buried-geometry check found 928 wall facings sitting <em>entirely inside</em> the block "
     "filling the same cell &mdash; a whole cubic tile of overlap each, so the facing was never "
     "visible and the block&#39;s own face is what the wall always showed. Removing them recovered "
     "948 assets and made the block&#39;s character matter, which exposed the next problem: it was "
     "a <em>corner</em> mesh, relief authored for two faces, tiled across a whole mass. It read as "
     "stacked crates. Six candidate blocks were then probed as 3x3x2 masses &mdash; which is what a "
     "rampart is &mdash; and the winner is flat, finely coursed and seams into itself.",
     [("shipped", "928 invisible facings removed"),
      ("shipped", "mass block chosen by probing masses, not tiles"),
      ("shipped", "interpenetration 3,850 pairs down to 77")]),

    ("The Gate Towers", "f-gatehouse", "The circuit rising either side of the road in",
     "Where the main street crosses the wall there used to be eighteen cells of nothing &mdash; a "
     "35 ft breach open to the sky. It became a tunnel with the rampart carried over on a lintel, "
     "and the wall flanking it now rises two courses so the approach reads as a defended entrance "
     "from across the valley. The passage keeps its cart headroom.",
     [("shipped", "wall carried over the road on a lintel"),
      ("shipped", "flanking towers, diagonal jambs included"),
      ("next", "no gate leaves or arch dressing yet")]),

    ("The Back Lanes", "e-back-lanes", "Timber framing, and floors that stop at the wall",
     "A floor used to drive a quarter of a cubic tile through the wall at every storey, because a "
     "storey was pitched at the <em>wall&#39;s</em> height &mdash; so the wall column was continuous "
     "and there was nowhere a slab could go. The pack answers this in its own vocabulary: all 75 of "
     "its Wall/Floor combination pieces are 2.5 tall, exactly wall plus floor. Pitching a storey at "
     "that leaves a floor-thick gap between courses. Zero intersections now, and the storey "
     "divisions read as tidy bands. The facade is also one kit at last &mdash; it was megadungeon "
     "brick, a Village window and a Rural floor meeting at every corner.",
     [("shipped", "storey pitch = wall + deck: 0 floor/wall intersections"),
      ("shipped", "one timber kit per house facade"),
      ("shipped", "clustered goods and signed doors")]),

    ("The Pinewood", "g-pinewood", "Trunks centred under their canopies",
     "Every prop on this map was sitting half its own size off, because the catalog read "
     "<code>m_Extent</code> from the collider and threw <code>m_Center</code> away. A tile is "
     "authored with its collider&#39;s min corner on the origin; a prop is authored with the "
     "collider <em>centred</em> on it. The stored coordinate is the origin either way, so "
     "&ldquo;subtract half the footprint&rdquo; is exactly right for one and exactly wrong for the "
     "other. On a fern that is 0.2 tiles and invisible; on a 2.55-wide pine canopy it is 1.275, "
     "while the trunk beneath moved only 0.55 &mdash; leaving three quarters of a tile between "
     "them, and a trunk anchored to the corner of its own crown.",
     [("shipped", "collider offset honoured: trunk centred at every rotation"),
      ("shipped", "one placed_bounds(), so checks and placement agree"),
      ("shipped", "felled stumps keep clear of standing trees")]),

    ("The Map Edge", "z-map-edge", "One ragged step, not a flight of terraces",
     "The falloff used to step down per ring, up to four terraces deep &mdash; and a 4-8 tile wide "
     "<em>flat</em> terrace half a tile below grade does not read as land falling away. It reads as "
     "a second layer of land laid over the first, which is what it got called twice before the "
     "penny dropped. It is one step now, however far the falloff reaches. The ragged outline is "
     "what stops the map looking cropped; the height was never doing that work. Worth noting the "
     "measurement that said &ldquo;terrain is flat everywhere except the taper&rdquo; was true and "
     "useless &mdash; the taper <em>was</em> the complaint.",
     [("shipped", "one 0.5 step at the border, ragged in extent"),
      ("shipped", "conifers are stump and crown: no bare trunk gap")]),
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
  <div class="kicker">citysmith &middot; design review &middot; rev 29</div>
  <h1>Forest Church</h1>
  <p class="deck">A TaleSpire village generated from a Watabou export &mdash; reviewed district
  by district at avatar eye level, the way a party will actually see it. Every image below is a
  fresh capture of the current build; nothing here is a render or a mock-up.</p>
  <div class="stats">
    <span>tiles <b>187 &times; 180</b> (935 &times; 900 ft)</span>
    <span>buildings <b>51</b></span>
    <span>assets <b>25,177</b> in <b>4</b> chunks</span>
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
  <li><span class="when">rev 29 &middot; Aug 20</span><div><b>A notched plan is roofed as wings.</b>
    The last visible fault. A hip roof answers a rectangle; forced over an L or a U it produced
    ridges meeting at angles that resolved into nothing, which was only obvious from directly
    overhead and merely looked &ldquo;chunky&rdquo; from anywhere else. Footprints are cut into
    maximal rectangles &mdash; the same largest-rectangle-under-a-histogram the rasteriser already
    uses to regularise a plan &mdash; and each is roofed as its own hip, largest first, so the main
    mass keeps the dominant ridge and the chimney. The L becomes a 4&times;6 range with a
    2&times;4 wing; the U becomes three.</div></li>
  <li><span class="when">rev 28 &middot; Aug 20</span><div><b>Props store their centre; tiles store
    their corner.</b> A trunk anchored to the corner of its own canopy turned out not to be the
    tree at all &mdash; it was every prop on the map. TaleSpire&#39;s collider bounds carry a
    centre <em>and</em> an extent, and the importer only ever read the extent. Tiles are authored
    with the collider&#39;s min corner on the origin, props with it centred, and the stored
    coordinate is the origin either way. Four earlier probes chased rotation, trunk choice and
    stump clearance, because all of those are visible and this was one layer down in the importer.
    The clue was there the whole time: the numbers said both pieces centred on the same point while
    the board plainly showed otherwise, and I read past that disagreement twice.</div></li>
  <li><span class="when">rev 24 &middot; Aug 20</span><div><b>One step at the border, and pines that
    meet their trunks.</b> The &ldquo;second layer of land&rdquo; was the edge taper: stepping down
    per ring gave terraces up to four deep, and a wide flat terrace half a tile down reads as a
    second layer rather than a slope. One step now. And the tree that did not match its trunk is
    the three-piece pine &mdash; probed as five stacks side by side, the two-piece meets the ground
    cleanly while the three-piece shows a bare dark trunk under the foliage, because the kit's
    Middle section is trunk with no canopy to close the gap. Two other hypotheses were measured and
    rejected first: standalone stumps under someone else's canopy came to 7 of 124, and roof tiles
    intersecting wall tiles came to exactly 0.</div></li>
  <li><span class="when">rev 23 &middot; Aug 20</span><div><b>The rampart is masonry, not stacked
    crates.</b> Six full-cell blocks probed as 3&times;3&times;2 masses rather than judged one tile
    at a time. What the wall had been built from is a <em>corner</em> mesh, with relief authored for
    two faces &mdash; tiled across a mass it read as crates. Worth recording why this is not a
    contradiction of the previous rev, which moved houses <em>off</em> md walls: there the piece was
    a 0.5-deep curtain panel whose dungeon relief left dark gaps at every join; here it is a
    full-cell block and the same relief reads as coursing. The family was never the problem. Using
    a piece outside what it was authored for was &mdash; in both directions.</div></li>
  <li><span class="when">rev 22 &middot; Aug 20</span><div><b>Walls and floors stop intersecting.</b>
    The floor fills its cell, the wall stands on the cell boundary, and they shared exactly 0.25
    cubic tiles &mdash; the band slicing the masonry at every storey. The cause was pitching a
    storey at the wall&#39;s own height, which leaves the wall column continuous and no room for a
    slab. All 75 Wall/Floor pieces in the pack are 2.5 tall, which is the kit stating the correct
    pitch; adopting it drops the slab into the gap between courses. Wall material was probed three
    ways &mdash; the megadungeon brick we used is dungeon masonry with deep relief that leaves dark
    gaps at every join, so houses moved to the Village panel our windows already came from. And a
    new buried-geometry check immediately found 928 rampart facings completely inside the wall
    core, recovering 948 assets. Interpenetration: 3,850 pairs to 77.</div></li>
  <li><span class="when">rev 21 &middot; Aug 20</span><div><b>Woodland that grows in stands.</b>
    Measured before touching: nearest-neighbour species agreement was 46% against a random rate of
    46% &mdash; not approximately random, exactly random. Spacing sat between 3.4 and 4.9 tiles with
    almost no spread, and density measured 2.1-3.1% at every distance from town. Two hashed noise
    fields fix all three: a coarse canopy field drives density, a finer stand field picks species.
    Agreement rose to 65% and density now runs 0.8% in glades against 4.2% in stands. Fixing the
    undergrowth exposed a bug of my own: the scatter is an elif ladder, so its thresholds are
    cumulative bands, and written as independent values ferns became unreachable in exactly the
    places they should be thickest.</div></li>
  <li><span class="when">rev 20 &middot; Aug 20</span><div><b>Porches, hedgerows, clustered
    goods.</b> Sixteen public buildings carry a porch over the door &mdash; set high enough to
    clear the signs on the same facade, which occupy up to 2.65 and would otherwise have been
    dropped for overlapping it. Field boundaries get a hedgerow rather than the weeds a
    grass-to-cobble seam gets, because a field boundary is a boundary. And scenery gathers now:
    fuel against a smithy, crates at a warehouse, empties behind an inn, stacked on the open cells
    each building's own perimeter touches so a pile leans on its wall instead of floating in the
    road. Separately, packing stopped trusting asset count as a proxy for bytes &mdash; the map's
    growing height variety compresses worse, and the largest chunk had crept to 29,634 of the
    30,720-byte cap with no signal until an export would have failed outright. Merges are encoded
    and measured now.</div></li>
  <li><span class="when">rev 19 &middot; Aug 20</span><div><b>Dressing that knows where it is.</b>
    Three gaps, all of them the scatter not knowing what surface it stood on. The market square
    was empty because its branch was gated on building adjacency, which nothing in the middle of a
    7&times;7 square satisfies; it has a well and eight pieces of goods now, and lanes get the same
    at a seventh of the rate. Woodland grew to the doorsteps &mdash; density is gated on a
    distance-to-building field now, and the cells nearest a wall get worked-yard clutter instead
    of pines. And the ruler-straight seams where grass meets paving are broken by low growth and
    spilled pebbles, the same trick as the shingle shore but for edges with no material of their
    own.</div></li>
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
