# Fencing

A design pass over field walls, yard fences and hedgerows: what the data
actually contains, why the grid is the wrong home for it, and the staged plan
that follows. Nothing here is built yet. `docs/ftg-geojson-import.md` §5 stage 5
is the backlog entry this closes out.

Every number below is measured off the three imported layouts in
`out/*/layout.json` (Pelvesthollow, Graybank, East Tradebourne) and off
`catalog.json`, not estimated.

## 1. Where fencing stands today

Three things are true at once, and the gap between them is the whole feature.

- **The importer reads fences and nothing consumes them.** `ftg._read_edges`
  chains `STONE_FENCE` segments into `Layout.fences`; `layout.py` serialises
  them; `render.py` draws them on the SVG reference map. `raster.py` never looks
  at `Layout.fences` and `build.py` never looks at it either. On a board, a
  fenced town and an unfenced one are the same board.
- **There is already a fence-shaped feature, and it is a decoy.** `_dress_seams`
  scatters the `hedge` role -- four bush props -- on `FIELD` cells that touch
  `GROUND`, at p=0.35. It was written as "a field boundary is a *boundary*, so
  it gets a hedgerow", which is the right instinct aimed at the wrong data: it
  reads the raster's surface classes, not the authored boundary. On all three
  FTG towns it fires **zero times**, because the FTG reader emits no `FIELD`
  cells at all -- every one of 17,801 fence samples lands on `ground`. So the
  town with real field walls in its export is the town with no hedgerow, and the
  pass meant to suggest enclosure only ever runs on MFCG maps that have no
  enclosure data to be faithful to.
- **The escape hatch is already wired.** `--no-fences` reaches
  `ftg.import_layout` through `importers.py`. It currently switches off a list
  nobody reads.

MFCG exports no fencing of any kind. Everything below is FTG-driven; §7 says
what this means for the MFCG path.

## 2. What the data is

### 2.1 It is small, and the cap the backlog demanded is not needed

`docs/ftg-geojson-import.md` recorded East Tradebourne's whole-canvas fence run
at 92,638 tiles -- three times Forest Church's entire board -- and concluded
that `field_wall` "still deserves a cap and a `--no-fences` escape". That was
measured before the core crop existed. After it:

| town | fence lines | segments | run length | panels at 2 tiles | board assets | share |
|---|---|---|---|---|---|---|
| Pelvesthollow | 8 | 36 | 686 tiles | ~343 | 20,687 | **1.7%** |
| Graybank | 5 | 34 | 1,088 tiles | ~544 | 91,429 | **0.6%** |
| East Tradebourne | 21 | 281 | 7,045 tiles | ~3,522 | 387,381 | **0.9%** |

Under 2% of the board on every town. **Drop the cap from the plan.** The core
crop already did the work a cap was invented to do, and a cap on a feature this
cheap is a knob that only ever makes a map wrong in a way nobody can see the
reason for. `--no-fences` stays, because it is free and it is the right control
for "I do not want enclosure on this board".

### 2.2 It is not on the grid, and that is not close

This is the measurement the design turns on.

| town | segments | axis-aligned | mean error to a 15 degree step | worst |
|---|---|---|---|---|
| Pelvesthollow | 36 | 1 (3%) | 1.69 deg | 3.91 deg |
| Graybank | 34 | 0 (0%) | 3.90 deg | 6.76 deg |
| East Tradebourne | 281 | 6 (2%) | 3.72 deg | 7.41 deg |

**97-100% of fence segments run off-axis.** A field boundary is a surveyed line
across farmland; it has no reason to be square to anything, and it isn't.

Rasterising a fence into cells and hanging curtain pieces on cell edges -- the
way `_lay_city_wall` builds the rampart -- turns each of those lines into a
stair-step. `CLAUDE.md` already records what a stair-stepped run of thin pieces
looks like on a board three separate times: a comb, a rank of fins, a lattice of
piers. A rampart survives it because it is four cells thick with a solid core
behind the curtain. **A fence is one piece thick with nothing behind it, so
there is nothing to hide the step.**

The other half of the same measurement is the escape. TaleSpire rotation is a
step index 0..23, so 15 degrees is the finest turn available. Snapping a
segment's true bearing to the nearest step costs at most 7.41 degrees, and over
one 2-tile panel that is **0.26 tiles of lateral drift -- 1.3 ft**, at the worst
vertex in the largest town. A joint post is 0.51 tiles wide and swallows it
whole.

So the line can be followed directly, at native precision, using machinery that
already exists (`place_centered` takes fractional coordinates and any of the 24
steps). It does not need a new coordinate system; it needs permission to leave
the cell grid, which the numbers give.

### 2.3 The corners are mostly not corners

Interior vertices by turn angle, all three towns pooled (317 vertices):

| turn | count | share |
|---|---|---|
| under 5 deg | 151 | 48% |
| 5-20 deg | 76 | 24% |
| 20-45 deg | 19 | 6% |
| 45-90 deg | 33 | 10% |
| over 90 deg | 38 | 12% |

Nearly three quarters of vertices are gentler than 20 degrees -- they are
polyline detail, not corners. A **post at every vertex** covers the whole
distribution: it hides the angle break where there is one and reads as an
ordinary gate post where there isn't.

`Stone Wall - Corner 01` and `Stone Wall Corner 02` exist in the same kit and
are the tempting answer, but both are authored as **90 degree right angles**
(1.73 x 1.70 and 1.82 x 1.78 footprints); they fit 10-12% of vertices and are
wrong at every other one. Keep them out of the first build. If a hard 90 degree
corner ever reads badly with a post on it, they are there.

### 2.4 It crosses almost nothing, and runs a long way off the map

Sampling every fence line at half-tile intervals against the rasterised TileMap:

| town | samples | on ground | on street | inside a building | **off the map** |
|---|---|---|---|---|---|
| Pelvesthollow | 1,391 | 83.9% | 0 | 0 | **16.1%** |
| Graybank | 2,191 | 70.7% | 0.3% | 0 | **29.0%** |
| East Tradebourne | 14,219 | 73.9% | 0.4% | 0 | **25.7%** |

Two findings, and the second is the one that bites.

**Fences never cross a building and barely cross a road.** FTG authors them as
parcel boundaries around the built area, so they already respect the buildings;
zero samples of 17,801 land inside a footprint. Road crossings are real but tiny
-- 60 samples in East Tradebourne, about 30 tiles of fence over carriageway. A
gap rule is still required (a drystone wall laid across a main road is an
impassable line through the one thing the map exists to let people walk down),
but it is cheap, and it is the only intersection rule needed.

**A quarter of every fence line lies outside the crop window.**
`ftg.inside_window` is a bounding-box overlap test applied per segment, and a
fence segment reaches 258 tiles at its longest -- so a segment that clips one
corner of the window is kept in full and runs off into open canvas. Worst
overhang: 34 tiles on Pelvesthollow, 92 on Graybank, **188 on East
Tradebourne**.

This has never mattered because every consumer clips by construction:
`_fill_polygon` and `_stroke_line` write into a bounded grid and simply discard
what falls outside it. Areas overhang by up to 784 tiles today and nobody has
ever noticed. **A prop run along the true line gets no such protection.** It has
to clip the polyline against the map rectangle itself, before laying anything.
Skipping this is not a cosmetic bug: props 188 tiles off the map would drag the
build's bounding box, and the bounding box is what every registration marker,
chunk anchor and `verify.anchor_on_a_whole_tile` check is measured against.

## 3. The asset

`folder` is the kit (`CLAUDE.md`, "the kit is `folder`, look it up, do not read
the name"), and the kit here is **`Fences`**, Medieval Fantasy. Everything in it
is a **prop**, not a tile -- so a fence stores its collider centre, and
`place_centered` is the placement primitive rather than `place_tile`.

| name | size (x, y, z) | reading |
|---|---|---|
| `Stone Wall 01` | 1.98 x 1.00 x 0.43 | drystone, 5 ft tall, 2 ft thick -- **the field wall** |
| `Stone Wall 02` | 1.98 x 1.39 x 0.61 | 7 ft and heavier -- an estate or churchyard wall |
| `Stone fence 02` | 2.02 x 0.99 x 0.49 | second drystone run, same height, slightly thicker |
| `Stone fence 01` | 0.51 x 1.02 x 0.51 | square post, exactly the wall's height -- **the joint** |
| `Wooden Fence` | 2.00 x 0.68 x 0.18 | 3.5 ft paling -- a paddock or a yard, not a field |
| `hedge_piece_01` | 2.06 x 1.00 x 0.94 | `Nature` kit, same 2-tile module and same height |

`Stone Wall 01` at 1.0 tile is 5 ft -- a correct drystone field wall -- and its
2-tile length is the module every candidate here shares, which is what makes one
run length work for all of them. `Stone fence 01` is 1.02 tall against the
wall's 1.00: the post stands a hair proud, which is what a gate post does.

Three roles, so the tier axis has somewhere to go later:

- `field_wall` -> `Stone Wall 01`, `Stone fence 02` (drystone, the FTG default)
- `field_wall_post` -> `Stone fence 01`
- `yard_fence` -> `Wooden Fence` (paling, for §7's generated towns)

Every one is 2.0 long by about 1.0 tall, so a single run-the-line routine serves
all three and a swap is a palette edit rather than a geometry change.

**Not chosen, and why.** The `Palisade` kit is full-cell tiles (1x1x1, 2x1x1) --
correct for a stockade, wrong for a field, and grid-bound by construction.
`Harbor Fence 02` exists twice under one name (a `Harbor` **tile** at
0.5 x 0.5 x 1.0 and a `Fences` **prop** at 0.98 x 0.48 x 0.20); the palette
already pins the tile for `quay_rail`, and a `_prop` query here would silently
take the other one. Pin by kit, not by name.

## 4. The decision: run the line, do not raster it

**A fence is laid as a run of props along its true polyline, clipped to the map,
stepped at the panel length, rotated to the nearest 15 degree step, with a post
at every vertex.** It does not enter the TileMap and it is not stroked into
cells.

This is the first geometry in citysmith that is not grid-bound, so it is worth
saying plainly what earns the exception. Every other feature has a reason to be
square: a floor is stood on, a wall is a room's side, a street has to be a whole
number of creatures wide. A field boundary has none of those -- nothing walks on
it, nothing is measured off it, and the export gives it a real bearing that the
grid can only destroy. The grid is a service to playability; here it buys
nothing and costs the whole shape of the feature.

What follows from the decision:

- **Clip first.** Intersect each polyline against `(0,0)-(width,depth)` before
  laying anything (§2.4). A line may clip into several pieces; each is its own
  run with its own end posts.
- **Step by the panel, not by the cell.** Walk the clipped polyline by arc
  length in 2.0-tile steps. A remainder shorter than a panel gets a post rather
  than a stretched or overhanging panel.
- **Post at every vertex, and at both ends of every run.** §2.3. Ends matter as
  much as turns: `chain_segments` stops at junctions, so a fork is two runs whose
  shared endpoint needs a post that reads as one.
- **Gap at a road crossing, and the gap is the road's own width.** Test each
  panel at its centre *and both ends*; drop it if any of the three stands on
  paving or in a building. That is a field gate without needing a gate asset,
  and it is the whole of the intersection rule (§2.4).

  **The first rule here was a fixed spread and it was wrong -- caught by the
  preview, not by reasoning.** Suppressing the panel on the paving plus one
  either side is right for a boundary that crosses a road once and ruinous for
  one that runs beside it. Section C of `tools/fence_sections.py` is a 48-tile
  boundary grazing a winding road six times; the three-panel demolition per
  graze left **9 of 24 panels standing**, which is not a wall with gates in it,
  it is a row of stubs. Sampling the panel's own extent instead opens exactly
  as much wall as the paving covers -- a wide road takes several panels, a
  graze takes none -- and it needs no number to tune. Section C now stands at
  17 of 24, and the 7 missing are the crossings.
- **Follow the ground.** Fences run across open country where the edge taper
  applies. Take `y` from the same `taper` dict every other landscape pass reads,
  and skip a panel whose cell tapers to `None`, exactly as `_dress_seams` does.
- **Landscape layer.** A fence is terrain furniture, not structure, and it is not
  owned by a building. `Builder.layer(LANDSCAPE)`, no `group`.

### 4.1 The collision test has to be bypassed, and this is measured

`Scatter` exists because **TaleSpire silently drops a prop whose collider
overlaps one already in the slab** -- on Forest Church, 1,000 of 2,137 props
were inside another prop's collider. So the obvious move is to lay fence panels
through `Scatter.place`. It does not work, and the reason is arithmetic:
`Scatter._clear` tests **axis-aligned bounding boxes**, and a 1.98 x 0.43 panel
turned off-axis has an AABB far larger than the panel.

| bearing | panel AABB | step | AABB overlap | outcome |
|---|---|---|---|---|
| 0 deg | 1.98 x 0.43 | (2.00, 0.00) | x -0.02 | clear |
| 15 deg | 2.02 x 0.93 | (1.93, 0.52) | x +0.09, z +0.41 | **rejected** |
| 30 deg | 1.93 x 1.36 | (1.73, 1.00) | x +0.20, z +0.36 | **rejected** |
| 45 deg | 1.70 x 1.70 | (1.41, 1.41) | x +0.29, z +0.29 | **rejected** |

Every consecutive pair of panels on any off-axis run overlaps as boxes while the
panels themselves are end to end and disjoint. Against §2.2 -- 97-100% off-axis
-- **`Scatter` would reject roughly every second panel of every fence on the
map**, and it would do it silently, reporting only a `rejected` count. A fence
built this way arrives as a dashed line, and the dashes would be blamed on the
export.

The fence pass therefore does its own overlap bookkeeping along the run (trivial:
it knows the panels are collinear and end to end) and registers the resulting
boxes with the `Scatter` so that *trees and bushes* still keep clear of the
fence. That direction of the test is the one that matters and it is still cheap.

**What is not known: whether TaleSpire's own drop test is on the AABB or the
oriented collider.** `CLAUDE.md` records the drop behaviour but not its shape.
If the game tests AABBs too, a butt-jointed diagonal run loses every second
panel on the board no matter what this code believes, and the run has to be
spaced. This is the one thing in the design that cannot be settled from the
files, and it gates everything else -- so it is stage 0.

## 5. Verification

The project's own rule is that a metric must read the artifact, not the plan,
and that new checks go in `verify.check_placements` rather than the TileMap pass.
Three checks, all on emitted boxes:

- **`fences_do_not_block_a_street`** -- no fence placement's box overlaps a cell
  classed `main` or `cart`. This is the playability check and it is the one that
  matters; a wall across a cart route is `verify`'s existing "can this be walked"
  question asked about a new obstacle.
- **`fences_stay_on_the_map`** -- every fence placement's box lies inside the map
  rectangle. Guards §2.4 directly, and it **fails** the build rather than
  warning, because an off-map prop moves the bounding box every registration
  check is measured against.
- **`fence_runs_are_continuous`** -- consecutive panels in a run are within a
  tolerance of touching. This is the artifact-side proof that §4.1 did not
  silently drop panels, and it is the check that would have caught the `Scatter`
  problem without a paste.

`fences` also wants a line in `Layout.summary()` and in the build's chunk report,
so a board that came out unfenced is diagnosable from the terminal.

## 6. Review

Per `CLAUDE.md`: a probe read from one angle is a probe that lies, and this has
cost three wall picks in a row. A fence is a thin, one-piece-deep, off-axis run
-- the exact shape that hides its own gaps from the front.

`tools/fence_probe.py` lays each candidate as **a straight run, a 45 degree run,
and a polyline with one 15 degree vertex, one 60 degree vertex and one over 90**,
on open ground, numbered on the ground with a bar of N cells running east (a
vertical tally stack vanishes from overhead). `review.ps1 360` then reads it from
four faces, overhead, and eye level. The post question -- is a post at a 5 degree
vertex visible clutter or invisible? -- is answered from eye level and nowhere
else.

## 7. What this does for MFCG, and the honest answer

Nothing, directly. MFCG exports no boundary data at all, and `city.py`'s
generated towns have plots but no enclosure. Two options exist and neither is
part of this work:

- **Derive fences from plot boundaries.** `city.py` already has plots; their
  shared edges are exactly what a fence follows. This is real, and it is a
  separate design pass, because it is a *generation* question (which boundaries
  are fenced, and why) rather than an import one.
- **Point `hedge` at the fence machinery.** The `_dress_seams` hedgerow (§1) is
  a scatter standing in for a boundary. Once a run-the-line routine exists, a
  field/ground boundary traced as a polyline would be a hedgerow rather than a
  sprinkle of bushes. Cheap, but it changes MFCG maps, so it wants its own
  before-and-after.

Deliberately out of scope here. This pass is about building the fences the export
already contains.

## 8. Plan

Stage 0 gates the rest; 1-3 are the build; 4-5 are the polish.

**Stage 0 -- settle the collider question.** Paste a slab of 2-tile props butted
end to end at 0, 15, 30 and 45 degrees, count what arrives, and record whether
TaleSpire's drop test is AABB or oriented (§4.1). One scratch board, one paste.
Everything downstream assumes the answer. *Accepts when:* the count on the board
matches the count in the file, or the spacing needed to make it match is measured
and written down.

**Stage 1 -- the geometry.** `build.run_along_polyline(points, module) ->
[(cx, cz, rot)]`: clip to the map rect, walk by arc length, snap each bearing to
the nearest of 24 steps, emit vertex and end positions separately. Pure
arithmetic, no catalog, no builder -- so it is unit-testable against the numbers
in §2.2 and §2.4 without a paste. *Accepts when:* tests cover a line wholly
outside the map, a line clipped into two pieces, a sub-panel remainder, and a
zero-length segment.

**Stage 2 -- the palette.** `field_wall`, `field_wall_post`, `yard_fence` pinned
per §3, with the kit named in the comment and the `Harbor Fence 02` name clash
called out where the next person will read it.

**Stage 3 -- the pass.** `_lay_fences(b, tm, layout, grade, taper)` in the
landscape layer: stage 1's positions, stage 2's assets, road gaps, taper
following, own overlap bookkeeping registered with the `Scatter` (§4.1).
*Accepts when:* all three towns build, and the asset counts land within a few
percent of §2.1's panel estimates.

**Stage 4 -- the checks.** The three in §5, plus the summary lines.

**Stage 5 -- look at it.** `tools/fence_probe.py` and a `review.ps1 360` pass per
§6, then one real town on a board. *Accepts when:* a fence line is read from four
faces, overhead and eye level, and it is a continuous wall rather than a dashed
one or a stair-step.

`--no-fences` is already wired and needs no work. The cap is dropped (§2.1).

## 9. The preview, and what to look at

Stages 1-4 are built. `--fence-style` selects one of seven designs, and
`tools/fence_sections.py` writes each of three sections of East Tradebourne
once per style, so they can be pasted in a row and compared on identical
ground. Nothing has been on a board yet; what follows is what to look for.

    python tools/fence_sections.py --layout out/tradebourne/layout.json

| section | crop | what it is for |
|---|---|---|
| A | `288,120,48,48` | four runs, ten vertices, one turning 135 degrees, no buildings — the joint policy with nothing to distract from it |
| B | `648,480,48,48` | nine buildings, a waterfront, 212 paved cells — whether a boundary reads at town scale |
| C | `0,514,48,48` | a road crossing one 48-tile run six times — the gate rule |

| slab | assets | fence pieces | bytes | files |
|---|---|---|---|---|
| `A-drystone` | 1,205 | 64 | 6,296 | 1 |
| `A-drystone-plain` | 1,195 | 49 | 6,232 | 1 |
| `A-drystone-corner` | 1,201 | 57 | 6,272 | 1 |
| `A-drystone-tall` | 1,205 | 64 | 6,296 | 1 |
| `A-paling` | 1,202 | 56 | 6,312 | 1 |
| `A-hedge` | 1,188 | 49 | 6,160 | 1 |
| `A-hedgerow` | 1,181 | 42 | 6,112 | 1 |
| `B-drystone` | 2,823 | 64 | 12,776 | 2 |
| `B-paling` | 2,818 | 58 | 12,760 | 2 |
| `B-hedgerow` | 2,804 | 50 | 12,548 | 2 |
| `C-drystone` | 1,031 | 17 | 5,308 | 1 |

Every slab is far under the 30,720-byte cap; section B splits in two because
props on the crop's edge overhang it, which is the ordinary behaviour and not a
fence problem.

**Paste `out/fence/spacing-probe.slab.txt` first.** It is the one thing that
gates the rest (§4.1): five runs of eight panels on a pad, labelled by a bar of
N cells running east, at bearings and spacings chosen so that counting the
panels says whether TaleSpire's drop test is on the bounding box or on the real
collider.

| row | bearing | spacing | AABB | boxes overlap? |
|---|---|---|---|---|
| 1 | 45° | 2.00 | 1.70 x 1.70 | yes — **the question** |
| 2 | 45° | 2.20 | 1.70 x 1.70 | yes |
| 3 | 45° | 2.41 | 1.70 x 1.70 | no |
| 4 | 15° | 2.00 | 2.02 x 0.93 | yes |
| 5 | 0° | 2.00 | 1.98 x 0.43 | no — control |

Eight panels in every row is the correct answer. **Count them from overhead** —
a gap in a diagonal run is invisible end-on, because the next panel covers it,
which is the trap that cost this project three wall picks. If row 1 is whole,
the collider is oriented, the design stands, and `verify._prop_collisions` can
be taught to exempt collinear fence panels *with evidence*. If row 1 is gappy
and row 3 is whole, the run has to be spaced and every diagonal wall will be
visibly dashed unless a post plugs each joint.

Until that is answered the builds report `[FAIL] placements: N props overlap`,
and the check now names how many of those pairs are consecutive fence panels so
the failure is not misread as a scatter regression. On `A-drystone` it is 33 of
307, and **every one is fence-against-fence** — zero fence-against-scenery,
which is the reservation in `Scatter.reserve` doing its job.

What the styles are actually asking, in the order worth answering:

- **`drystone` vs `drystone-plain` vs `drystone-corner`** — the joint policy.
  48% of vertices turn less than 5 degrees, so `drystone` puts a post at a great
  many places where the line barely bends. Does that read as a gate post or as
  clutter? Judge from eye level; from overhead a post is always defensible.
- **`hedge` vs `hedgerow`** — whether a living boundary survives being laid on a
  survey line. The regular one is the same geometry as the wall; the jittered
  one wanders 0.3 tiles and drops one piece in ten. If the regular hedge reads
  as extruded green plastic, that is the answer, and it is worth knowing whether
  the same jitter would help the *wall* too.
- **`drystone` vs `drystone-tall`** — 5 ft against 7 ft. The tall one is an
  estate wall; on a field boundary it should read as too much.
- **`paling`** — timber at 3.5 ft, cornered only at hard turns. The one style
  with no joint at gentle vertices, so it doubles as a second read on the post
  question.
- **`C-drystone`** — the gate rule, and the only thing to check there is whether
  the gaps land on the road rather than beside it.


## 11. Stage 0 is answered: the drop test is NOT on the bounding box

**Settled on a real build, 2026-08-25.** A 40x40 crop of Pelvesthollow
(`--crop 104,64,40,40`) carrying 80 tiles of `STONE_FENCE` was built and
pasted. `verify` flagged **78 pairs of consecutive fence panels** as
box-overlapping -- exactly the pairs §4.1 predicted -- and on the board the
drystone walls came out as **continuous ribbons**, running across the fields at
their true bearings with no periodic breaks.

That is the answer. If TaleSpire tested bounding boxes, roughly every second
panel of every diagonal run would be missing: a 2-tile hole at a 50% duty
cycle, which is not a subtle effect and is not present. **The game tests the
oriented collider**, the design in §4 stands as written, and the run stays
butt-jointed at 2.00.

Two consequences:

- `verify._prop_collisions` is *pessimistic about fences specifically*, and now
  demonstrably so. It may be taught to exempt collinear same-asset panels --
  with this as the evidence. Until then a fenced build reports the FAIL and the
  second line names how many pairs are fence panels, which is what stops it
  being read as a scatter regression.
- `tools/fence_spacing_probe.py` was built for this and is kept. It gives the
  formal per-row count (8 panels per block, five bearings and spacings) where
  this reading gives the qualitative answer; nothing about the probe is wrong,
  it simply was not needed once a real map answered the same question.

**And the first look at fences on a board settled the geometry too.** The runs
follow their surveyed bearings across open field as single continuous walls --
no stair-step, no comb, no fins. That was the whole argument of §2.2 and §4,
and it holds.
