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
    ("The Greens", "01-woodland-park", "Pine woodland on the old commons",
     "A ranger scouting ahead moves tree to tree with real cover, and jittered trunks kill the "
     "orchard-grid look. The lone stable in the clearing is the proof module: rectangular, full "
     "wall circuit, saddle roof seated. The party&#39;s where-do-we-camp conversation has an "
     "obvious answer.",
     [("shipped", "single-piece pines, jittered"), ("shipped", "stable = clean proof module")]),
    ("The Farmland Fringe", "02-farmland", "Tilled strips and wheat sheaves at the town's edge",
     "Chest-high wheat is concealment a kobold skirmisher will absolutely use, and the gravel "
     "margins from the field-fringe fix read as worked headlands. The map edge still drops to "
     "void &mdash; a stepped-down border ring stays queued.",
     [("shipped", "wheat rows + gravel margins"), ("flaw", "map edge is a sheer void cliff")]),
    ("Town Edge", "03-town-edge", "Where the last cottage meets the treeline",
     "Rooflines now vary by storey and every facade carries windows, so the street silhouette "
     "reads as a settlement rather than a bunker row. The civic flank behind still wants its "
     "windows checked at eye level.",
     [("shipped", "windows on every facade"), ("shipped", "varied rooflines")]),
    ("The Back Lanes", "04-back-lanes", "Between houses in the dense quarter",
     "Grass alleys between lots read as lived-in shortcuts &mdash; a rogue's escape route during "
     "a chase. Adjacent gables at differing heights give archers roof positions reachable from a "
     "cart. Lanes are grass, not mud; a churned-earth strip would sell it harder.",
     [("shipped", "varied roof heights by storey"), ("next", "consider dirt/gravel back lanes")]),
    ("The Riverbank", "05-riverbank", "Stepped banks down to the sunken channel",
     "Water sits a full tile low, so shoving a bandit off the bank is a real 5-ft decision, and "
     "the stepping-stone planks are a legible chokepoint for a fighting retreat. Bank sides "
     "still show raw tile edges; the gravel shore course stays queued.",
     [("shipped", "sunken water, plank chokepoint"), ("flaw", "bank sides show raw tile edges")]),
    ("The Civic Quarter", "06-civic-flank", "The guildhall's long flank",
     "The citadel ring turned out to be the real tenant here, and at eye level it reads as a "
     "ruin: the wall line rasterises to diagonal stair-steps, so the circuit breaks into "
     "free-standing pillars with arched tiles. Atmospheric, but it should be a choice, not an "
     "accident.",
     [("shipped", "parapet cap course"), ("flaw", "diagonal wall cells break into pillars")]),
]

DISTRICTS += [
    ("The Rampart", "10-rampart-walk", "Solid masonry, battlements facing out, a walk behind",
     "The wall a party can actually be chased along. Merlons crown only the cells that look out "
     "of town, so the parapet is a line rather than a hedge of teeth, and the weathered flags "
     "behind them are a fighting surface two abreast. Until this rev the same circuit was four "
     "parallel fins of curtain wall with 2.5 ft of daylight between them &mdash; an archer could "
     "have shot straight through the fortification.",
     [("shipped", "full-cell core: no daylight through the wall"),
      ("shipped", "battlements face out; walk paved behind"),
      ("next", "no arch, doors or flanking towers at the gate")]),
    ("The Main Road", "11-level-streets", "Cobble laid flush with the verge",
     "Cobble is 0.25 thick and grass is 0.5, and both were laid from a common bottom, so every "
     "street sat a quarter tile low &mdash; a 15 inch kerb down both sides of every road in town, "
     "on 1,234 tiles. Nothing in the tile grid could show it. Surfaces now align at the top, "
     "which is the plane a creature stands on.",
     [("shipped", "surfaces align at the top, not the bottom"),
      ("shipped", "varied rooflines by storey count")]),
    ("The Gatehouse", "12-gatehouse", "Towers flanking the road in",
     "Two revs ago this was an 18-cell breach with nothing in it &mdash; a 35 ft hole in the "
     "circuit, open to the sky, where the main street crossed. It became a tunnel with the "
     "rampart carried over on a lintel, and now the wall either side rises two courses into "
     "towers, so the approach along the road reads as a defended entrance from a long way off. "
     "The passage still keeps its cart headroom.",
     [("shipped", "wall carried over the road on a lintel"),
      ("shipped", "flanking towers, diagonal jambs included"),
      ("next", "no gate leaves, arch dressing or murder holes")]),
    ("The Pinewood", "13-woodland", "Trees with trunks, spaced so they all arrive",
     "Every conifer here used to be a bare canopy cone lying on the grass &mdash; "
     "&ldquo;Stackable Pine Top&rdquo; is the top of a three-piece kit, and it was being planted "
     "on its own, while cut stumps were scattered separately nearby. Read together they were "
     "leaves that did not line up with any trunk. Worse, 47% of all scenery sat inside another "
     "prop&#39;s collider, and TaleSpire drops those on paste without saying so, which is what "
     "made the woods look half-built. Spacing is now enforced, so what is in the file is what "
     "reaches the board.",
     [("shipped", "pines stacked from their kit: 557 joints, none misaligned"),
      ("shipped", "0 overlapping props, down from 1,000 of 2,137"),
      ("shipped", "street clutter actually appears now")]),
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
  <div class="kicker">citysmith &middot; design review &middot; rev 14</div>
  <h1>Forest Church</h1>
  <p class="deck">A 117 &times; 113-tile TaleSpire village generated from a Watabou export &mdash;
  reviewed district by district at avatar eye level, the way a party will actually see it.
  This page is updated as the design changes.</p>
  <div class="stats">
    <span>tiles <b>187 &times; 179</b> (933 &times; 896 ft)</span>
    <span>buildings <b>51</b></span>
    <span>assets <b>24,040</b> in <b>3</b> chunks</span>
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
