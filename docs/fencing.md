# Fencing

A design pass over field walls, yard fences and hedgerows: what the data
actually contains, why the grid is the wrong home for it, and the staged plan
that follows. `docs/ftg-geojson-import.md` §5 stage 5 is the backlog entry this closes
out. §§1-8 are the field-wall design and are built; §10 is the design review
of the *yard* boundary and §11 is what was done about it.

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

### 5.1 A box is not where the coordinate is (2026-08-29)

Both checks above were reading a placement's stored coordinate as the collider
centre. It is the asset's **origin**, and where that sits inside the collider
is a property of how the piece was authored: a prop is centred on its origin, a
tile stands with its collider's min corner there. `build.placed_bounds` is the
one place that knows the difference and every other placement check in `verify`
goes through it; these two did not.

It only ever showed on a tile, and there is exactly one boundary family in the
medieval palette that is `kind="tile"` -- the palisade, `off=(0.5, 0.5)`. Which
would have made it rare, except that a *closed* run is built as a barricade
whatever `--fence-style` asks for (§8.1), so every style laid some. The box came
out half a cell low on both axes, straddling the four cells that meet at the
tile's own min corner rather than the one cell it fills, and picked up a street
two cells away.

Sedgewater reported `2 boundary piece(s) stand in a street or lane` at
x=145.00, z=134.00 under eight styles and 7 pieces under `palisade`, with
nothing standing in a street. **The identical count from `drystone` and `hedge`
was the diagnosis and it was read as the opposite of what it meant**: two
styles that share no piece cannot fail the same way about their own pieces, so
the pieces were never theirs. Corrected, 0 pieces intrude on any of the nine
styles, and no piece moved -- `fence_pieces` is identical across all of them.

The joint path was tightened at the same time, for the reason the panel path
was: `_lay_fences` tested a panel against its body and a joint against the one
cell its centre landed in. `field_wall_post` is 0.51 square and cannot reach
past that cell, but `paling`'s corner is 1.64 and spans four, so a joint could
sit a foot inside a lane and test clean. Both go through `blocks_a_way` now.

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


## 8. A perimeter is not a field wall (2026-08-26)

Everything above is about a **boundary between fields**: an open, surveyed
line, laid along its true bearing out of prop-kind pieces. It turns out that
covers only half of what a fence run can be, and the other half wants the
opposite of every decision in §4.

### 8.1 The test is closed versus open

A *closed* run in `Layout.fences` -- one that comes back to its first point --
is somebody's **perimeter**, not a boundary between two fields. `_is_closed`
is the whole test, and `raster.compounds` uses the same one to decide what a
property is (`docs/marsh.md` has the neighbouring case). It needs no new
vocabulary in the export and no new flag: a hamlet's field walls are open runs
and a keep's barricade is a closed one, and the two get built differently
because they are different things.

Three consequences fall out, and each was a defect first:

**The material.** `--fence-style palisade` built the outlying farms' field
boundaries as ten-foot timber stockades -- correct for the keep, absurd across
a wheat field, and unmissable from the air as a fortification cutting through
somebody's crop. `DEFAULT_ENCLOSURE_STYLE` is now applied to closed runs and
`DEFAULT_FENCE_STYLE` to open ones, so the default gets both right with no
flag at all. Measured on Sedgewater: 4 open runs -> 91 `Stone fence 02`,
1 closed run -> 92 `Palisade wall tall 1x2` + 21 corners.

**The lattice.** Every style in §4 resolves to a `kind="prop"` asset, and a
prop is *allowed* off the half-tile grid -- it stores its collider centre, so
`verify`'s off-grid canary exempts it. The `Palisade` kit is `kind="tile"`,
and laying tiles along an arbitrary bearing put **166 of them off the grid**
on the first build. That is a real FAIL from the check that exists because one
fractional overhang drags a whole board off the lattice and breaks mini
snapping.

So a tile boundary is laid on **cells** (`FenceStyle.on_cells`), and for this
kit that is right rather than a compromise. §2.2 argues against stair-stepping
because a thin panel run leaves daylight at every step -- the comb, the rank of
fins. A palisade piece is a **full cell deep**, so an ORTHOGONAL step has no
daylight to leave; the same reasoning `city_wall_core` is built on.

**That sentence used to stop one clause earlier, and it was wrong.** A full
cell closes a full cell of daylight; it does not close a *diagonal* step. Two
pieces stepping corner to corner touch at a **point**, and what is between
them is a slit straight through the wall. Measured on Sedgewater's barricade:
116 cells in **34 four-connected pieces**, 14 of them with no orthogonal
neighbour at all -- a stockade you can see the field through, found by
looking at the board and not by reading the collider. `_close_diagonals` adds
one connector per diagonal step (116 -> 149 cells, 34 pieces -> 1, 14 slits ->
0) and `test_a_stair_stepped_barricade_is_not_see_through` holds the line.

This is the fourth time this repo has recorded a stair-stepped run of pieces
reading as a comb, and the first time it arrived through a piece whose
measurements said it could not happen. Use the 1x1 piece
(`Palisade wall tall 1x2`, 1.00 x 2.00 x 1.00) and not the 2x2, because a
2-wide piece cannot sit on a one-cell lattice run.

**The corner, which is not where the cells say it is.** Asking whether a cell
has both an east-west and a north-south neighbour on the run calls every step
of a rasterised diagonal a turn. On Sedgewater's ring -- a smooth 16-gon whose
turns run 1.2 to 53.9 degrees, **not one of them a real corner** -- that built
21 of 116 cells as round-log corner bundles between flat stake panels: two
materials on one barricade, and only on the angled stretches. A corner is
decided on the **source polyline** against `post_min_turn`, where an angle
actually exists. A smooth ring gets none, a square pen gets four.

Two smaller ones fell out of testing that: the panel's facing at a step must
take the *dominant* local axis, or every second cell of a diagonal faces
across the run; and a vertex mapped to a cell by truncation lands on the wrong
one when it sits exactly on a cell corner, which is how a square pen showed
three corners instead of four.

**The gate.** The ring skips its own cells wherever a carriageway crosses --
correctly, or the road would be walled off -- which left a three-cell,
fifteen-foot opening with nothing in it. `Doors - Palisade` (2.00 x 2.75 x
0.50) is the kit's own gate, and it sits in folder `Doors` rather than
`Palisade`, so the kit rule never finds it: the same way `city_gate_arch` sat
pinned and unused for eleven revisions. It is taller than the 2.0 wall it
hangs in, which is what a gate should be.

Hung cell by cell it produced **seventeen gates along one boundary**, because
a road running *beside* a fence paves every cell of that stretch and every
pair of them looked like a crossing. An opening is a contiguous group, a
crossing is a SHORT one, and a long paved stretch is a road the boundary
happens to follow -- which wants no gate at all (`GATE_MAX_CELLS`).

**The facing, which is the one that had to be read off a board.** The panel is
directional: pointed stakes on one face, diagonal bracing and a walk platform
on the other. The first build put the bracing on the OUTSIDE of the entire
circuit -- a stockade with its scaffolding facing the field. Reasoning about
which way `rot=0` points produced exactly the wrong answer; the board settled
it in one look, and `_lay_palisade` now carries the measurement rather than
the reasoning. **A directional piece needs an in-game look before it is
trusted, whatever the collider says** -- which is this file's neighbour rule
about roof rotations, arriving again from a different direction.

### 8.2 The number that started it

`Wooden Fence` -- what every timber boundary here was built from, and what a
keep's barricade was built from -- is **0.68 tall**. That is 3.4 ft: a garden
paling you step over. It had never been measured, and on the board a keep's
enclosure read as somebody's vegetable patch.

The `Palisade` folder is nine pieces at 1.0 and 2.0 tall with a corner and a
cap for each, and it was found by asking the catalog for "thin and over 1.4
tall" rather than by searching names. Its names are **width x HEIGHT, not
footprint** -- `tall 1x2` is 1.00 x 2.00 x 1.00 -- which is one more entry for
"asset names are inconsistent, the collider is the only thing that says the
shape".

### 9.1 ANSWERED on a board, 2026-08-31: row 1 is whole

**Eight panels in every row. TaleSpire's drop test is on the oriented
collider, not the bounding box.** Read from directly overhead on a fresh
board, per the warning above.

- The non-overlapping control (45 degrees, spacing 2.41): **eight** discrete
  panels, evenly spaced, uniform gaps, none missing.
- An overlapping row (45 degrees, spacing 2.00, AABBs overlap by 0.29 on both
  axes): **one unbroken run**, panels butted end to end with a thin seam at
  each join and no bare grass anywhere along it.

A dropped panel at 2.00 spacing would leave a ~2-unit hole in a run of 1.98
pieces. There is no such hole. So the design stands and a diagonal fence may
run butted.

**No exemption is needed, and that is the part worth writing down.** This
section anticipated teaching `verify._prop_collisions` to exempt collinear
fence panels once the answer came in. The check overtook the question: since
it moved to the ORIENTED box (section 4.1) it already measures what the game
measures, so butted collinear panels never reach the flag. Measured on
Sedgewater the same day -- 5,815 props, and the whole town flags **three**
pairs:

| pair | depth | what it is |
|---|---|---|
| `hedge_piece_01` x2 | 0.97 | a turn penetrating well past one thickness |
| `Stone Wall 01` x2 | 0.447 | a turn, over the 0.43 thickness allowance by 0.017 |
| `Stone fence 01` / `Wheat Bunch` | 0.015 | a panel clipping a scatter prop |

None is a butted run. Adding the collinear exemption this section imagined
would also re-open the hole section 10.1 closed -- a lap separates on its thin
axis first, so it presents the same penetration as a corner, and waving those
through is exactly how the yard boundary came to be laid twice over on every
board. The residual three are a tolerance question and a scatter-reservation
question, not a collider question.

**Method note, because it nearly went wrong.** The first read was taken from a
low oblique, where the overlapping rows also looked continuous -- and that is
worthless here for precisely the reason stated above: end-on, the next panel
covers the gap. From the capped overhead zoom a panel subtends about 9 px and
its own ribbed texture is indistinguishable from a panel boundary; a
peak-count returned 27-41 "panels" for a single row and was discarded. The
reading that stands was taken on a **fresh board** (a stray click had armed a
build tool on the first one, so that board could no longer be trusted to show
what was pasted), from a vertical pitch, two wheel ticks back from the
`newboard` default height.

## 10. The yard boundary, reviewed on a board (2026-08-27)

Everything above is about `_lay_fences` -- the *field* boundary, run along its
surveyed bearing. `_lay_yards` is the other boundary pass, and it had never
been reviewed. `tools/yard_probe.py` builds it three ways -- structure, style,
size -- one 34x34 crop of East Tradebourne per panel, one panel per board so
every frame has the same camera.

Two things about the method, before the findings, because both cost a pass:

- **The camera cannot frame a row of panels, and this is measured.**
  Ctrl+scroll height is capped: two 1920x1080 frames 45 and 200 ticks apart
  differ by **0.59** on the mean-abs-diff metric, against the 2.0 noise floor
  `CLAUDE.md` records. At the cap an oblique covers about 40 tiles and a
  four-panel row is 151, so the row can only be read by flying -- and WASD
  ramps, so 1.6 s of `a` moved most of a panel and 2.2 s went off the map
  entirely. One panel per board, same commands in the same order, puts every
  panel in the same pixels. `tools/panel_review.ps1`.
- **The first crop contained the town wall and three-storey blocks**, which
  dominated every frame and put the yards in shadow. It is chosen now for no
  wall cells and one- and two-storey buildings. A probe that contains what it
  is not testing is a probe that gets misread.

### 10.1 The boundary is laid at double density, and it shows

`_lay_yards` calls `place_wall` once per boundary **cell edge**. `place_wall`
centres the piece on that edge, and `yard_fence` is `Wooden Fence` --
**2.0 tiles long**. So every panel laps its neighbour by half its own length.

| town | panels | with one lying on them lengthwise | stepped at the module |
|---|---|---|---|
| Pelvesthollow | 599 | **507 (85%)** | 321 |
| Graybank | 2,330 | -- | 1,261 |
| Forest Church | 472 | -- | 257 |

On the board the lap is not invisible: **posts every 5 ft instead of every
10, at irregular spacing, with the rails visibly doubled**, and a half-panel
stub overhanging past every corner into open grass. Stepped at the module the
same run reads as an ordinary post-and-rail fence.

The overhang also lands where it should not -- panels whose box covers a way,
or a building:

| town | shipped: on a way / in a building | stepped: on a way / in a building |
|---|---|---|
| Pelvesthollow | 27 / 0 | 6 / 0 |
| Graybank | 65 / 6 | 7 / 0 |
| Forest Church | 17 / 9 | 2 / 7 |

**The rule already exists one module away.** `build.FENCE_MODULE` is 2.0 and
`FENCE_MIN_SEGMENT` is 1.0, with the comment "a full-length piece laid on a
stub overhangs both its ends and reads as a fence pointing the wrong way".
`_lay_fences` steps by it. `_lay_yards` predates it and steps by the cell.

**`verify` cannot see any of this, and the reason is exact.** The minimum
penetration of two collinear panels is the panel's own *thickness* (0.180),
which is precisely the corner-join allowance `_prop_collisions` grants -- so
all 577 flagged pairs on Pelvesthollow are counted as corners and the check
passes. The allowance needs to test that the two boxes are *perpendicular*,
not merely that they meet shallowly.

### 10.2 Between two fifths and three fifths of every yard is laid as lawn

The other half of what makes a yard a place is its surface, and it is missing.

| town | yard cells | laid as `Grass - Lush` |
|---|---|---|
| Pelvesthollow | 1,195 | 556 (**47%**) |
| Graybank | 4,742 | 1,940 (**41%**) |
| Forest Church | 756 | 452 (**60%**) |
| East Tradebourne | 11,225 | 5,064 (**45%**) |

`_lay_terrain` pass 1 keys the 2x2 block on the surface **class** --
`_BLOCK_SURFACES[R.GROUND]` is `ground_2x2`, which is grass -- after checking
that the four cells agree on their **role**. A quad of four cells that all
agree on `lane_earth` passes that check and is then sheeted in grass. The
comment on the branch reads "Four cells have to agree on the role, not just
the class"; they do agree, and the block laid is the class's anyway.

On the board a yard is a chequer of dark mud patches and lawn, which reads as
shadow rather than as worked ground. With the fence taken away entirely
(panel 4 of the structure sweep) **the yard is invisible** -- so the edge is
carrying the whole feature and the surface is contributing nothing.

### 10.3 A quarter to a third of the runs are stubs

Maximal straight runs of the boundary, and how many are one or two cells long:

| town | runs | 1-2 cells |
|---|---|---|
| Pelvesthollow | 92 | 26 (28%) |
| Graybank | 377 | 83 (22%) |
| Forest Church | 83 | 30 (36%) |
| East Tradebourne | 985 | 272 (28%) |

That is the case `FENCE_MIN_SEGMENT` exists to refuse, and on the board it is
the isolated two-panel run standing in open grass, attached to nothing.

### 10.4 The way in is the whole frontage, and a yard with no frontage has none

The opening rule is "leave open every edge onto a street, lane, plaza or
pier". It fails at both ends of its range:

- **27-29% of the yard perimeter is open** (Pelvesthollow 220 of 819,
  Graybank 956 of 3,398, East Tradebourne 2,395 of 8,315). A plot fronting a
  lane along its whole side has that whole side left out, and reads as a
  three-sided pen.
- **A yard that touches no way gets no opening at all**: 17 of East
  Tradebourne's 230 and 5 of Forest Church's 15 are sealed rings -- the
  courtyard nobody can enter that this pass's own docstring says it avoids.

**Closing the ring and cutting one gate is NOT the fix, and the board is why.**
Built that way (panel 3), the frontage against a diagonal lane becomes a
stair-step of 2-tile panels laid at right angles to each other, which reads as
a comb of crossed pieces lying over the paving -- §2.2's argument against
stair-stepping a thin panel run, arriving in the yard pass. Straightening the
frontage run has to come first.

### 10.5 Style: the paling is the weakest of four, and one style is a coin flip

The same boundary in four materials, in one frame:

| style | piece | height | how it reads at play distance |
|---|---|---|---|
| paling (shipped) | `Wooden Fence` | 0.68 (3.4 ft) | **weakest** -- low and see-through, closer to decoration than boundary |
| drystone | `Stone Wall 01` | 1.00 (5 ft) | strongest; clean butt joints and corners |
| estate | `Stone Wall 02` | 1.39 (7 ft) | reads, and is grand -- a temple or a manor, not a cottage |
| hedge | `hedge_piece_01` | 1.00 (5 ft) | reads well as a garden boundary; visible steps where two runs meet |

The facade already deals a wall kit per tier (`tier_of`, four fabrics); the
yard boundary deals one piece for everything. Three of these four are already
pinned in the palette, so the axis costs a table rather than an asset hunt.

**`field_wall_tall` is a coin flip.** Its query lists
`("Stone Wall 02", "Stone Wall 01")` and `Palette.resolve` seeds a choice
*inside* the first matching query, so `--fence-style drystone-tall` deals the
ordinary wall on **five seeds in eight** -- the same board as `drystone`. A
style you can select that silently is not the style.

### 10.6 Size: `YARD_REACH = 2` is too small (BUILT)

Two Pelvesthollow farmsteads with open country round them, at reach 1 to 4:

- **1** -- almost nothing survives; no plot reads.
- **2** (shipped) -- an L round one corner of the building. A corner, not an
  enclosure.
- **3** -- reads as a farmstead, and `_dress_yards` finally has room to put
  the working life of the trade somewhere.
- **4** -- the strongest read; two properties, plainly.

The cost was expected to be the objection and is not, because `YARD_MIN_GAP`
already gates *which* buildings qualify:

| reach | share of open ground (all four towns) | median yard |
|---|---|---|
| 1 | 1-2% | 22-30 cells |
| 2 | 3-5% | 49-61 |
| 3 | 5-7% | 74-94 |
| 4 | 7-10% | 98-132 |

Even East Tradebourne at reach 4 is 23,307 of 232,465 open cells.

#### Built, the same day: the reach is measured per side

A uniform reach of 3 or 4 would fix the *scale* and leave the other half of the
problem standing, which is that **every yard in a town is the same yard**. A
farmstead in open country and a house wedged between two neighbours have
different amounts of ground, and the yard should be the ground each one has.

`yard_reach_by_side` measures it. Three inputs, in the order they decide:

- **Clearance per side.** From every footprint cell with that face exposed,
  walk outward over open ground and take the **median** of the runs -- not the
  least, because on a rasterised footprint one clipped corner would otherwise
  veto a whole side. The walk stops at a building, a road, a watercourse or the
  map edge, so the number is "how much of its own ground is out this way".
- **The door's side is capped** at `YARD_FRONT_REACH` (2 cells). A house
  fronting a street keeps a shallow strip and puts its wood, its midden and its
  work round the back. **That cap is the whole of the difference between a
  front yard and a back one** -- everything else falls out of the clearance, so
  a building with room only in front still gets a front yard, just a shallow
  one.
- **A side under `YARD_MIN_SIDE` gets nothing.** One cell of worked ground
  against a wall is a verge, and fencing it produces a panel with a building on
  both sides.

`yard_cells` then dilates the footprint by those four numbers rather than by
one, so the apron is a rectangle per side and the corners fill only where both
adjacent sides are live. `yard_form` names what comes out.

What it produces, over the four towns:

| | before (flat 2) | after (measured) |
|---|---|---|
| East Tradebourne yard size | 49 cells, every yard | 5 to 197, quartiles 58 / 85 / 109 |
| forms | one | full 119, wrapped 56, corner 34, side 10, back 6, through 3 |
| reach per side | always 2 | 0 on 19%, 2 on 20%, 3 on 7%, 4 on 52% |
| ground behind deeper than in front | never | **205 of 228 (90%)** |
| share of open ground | 3-5% | 6-8% |

**The variance is measured from the site, not dealt from a seed**, and that is
deliberate: two farmsteads with the same room round them *should* get the same
yard. §7 of this file makes the same argument about wards, and
`docs/district-surfaces.md` makes it again -- an axis that does not
discriminate is a knob dressed as a feature. The thing that ought to differ
between a farmstead and a terrace house is the ground each actually has, and
that is what is now read.

Read on two boards against the flat apron on identical ground: on the
Pelvesthollow farmsteads the plots grow enough to hold the clutter
`_dress_yards` was already producing, and in the East Tradebourne crop the
central house gains a long back yard running west with a cart and barrels in
it, where the flat version had a box hugging the wall.

`tests/test_yards.py` pins it, including the failure that would be silent --
the sizing measured correctly and then thrown away by a square dilation, whose
only symptom is that every yard is the same yard again.

### 10.7 What to do, in order

**All six are done -- §11 is what was built.** The order below held: nothing
else could be decided about the boundary until it was chained into runs.

1. **Step the yard boundary at `FENCE_MODULE` along maximal straight runs, and
   refuse a run shorter than `FENCE_MIN_SEGMENT`.** Closes 10.1 and 10.3,
   halves the props, and takes the overhang off the roads and out of the
   walls. The run-chaining is `yard_probe.straight_runs` / `panels`.
2. **Stop the 2x2 block pass laying the class's material over an agreed
   non-default role** -- either lay the role's own block, or fall through to
   1x1. Closes 10.2, which is the larger of the two defects.
3. **Deal the yard boundary per tier**, as the facade is dealt. Closes 10.5.
4. ~~**Scale `YARD_REACH` with the measured neighbour gap.**~~ **DONE**
   2026-08-27, and per *side* rather than per building -- clearance out of each
   face, capped in front of the door. `yard_reach_by_side`, `yard_form`,
   `tests/test_yards.py`. Closes 10.6.
5. **Then, and not before, revisit the frontage and the gate** (10.4) -- it
   needs the straightened run from step 1.
6. **Add the three checks §5 already specifies**, and teach the corner-join
   allowance in `_prop_collisions` to require that the two boxes be
   perpendicular. Without that, step 1 has nothing holding it in place.

## 11. What was done about it (2026-08-27)

All six findings are closed. The order in §10.7 held: the boundary had to be
chained into runs before anything else could be decided about it, and the
frontage could not be touched until it was.

### 11.1 The boundary is a run of panels, not a piece per cell edge

`boundary_runs` chains the yard's edge into maximal straight runs; `_run_panels`
steps each one at **the panel's own length**, read off the collider rather than
taken from `FENCE_MODULE`, so a per-tier boundary from another kit still steps
correctly.

**An odd run leaves a gap, it does not lap a panel.** Rounding the panel count
up puts a half-panel lap in every odd run -- 70 pairs on Pelvesthollow, every
one a genuine collinear overlap. Rounding down and butting outward from *both*
ends puts the remainder in the middle, where a gate would be, and keeps both
corners flush. The corner is the part that reads.

Two thresholds decide whether a short run is built at all:

- `FENCE_MIN_RUN` (2 cells) -- below this the panel overhangs both its own
  ends, so it is built only where a real run meets it at a corner.
- `FENCE_MIN_ISOLATED` (4 cells) -- with nothing at either end, one panel is a
  panel lying in the grass. Chaining alone did not fix this: it removed the
  one-cell stub and left the two-cell one, 11% of Graybank's kept runs.

**A lone yard cell was the case that got through the first cut.** Its four
sides all meet at itself, so a test that only asked whether *some*
perpendicular run shared an endpoint kept all four -- a cross of 2-tile panels
centred on one 5 ft square. Because such a cell is usually an island cut off by
a road, the arms landed in the carriageway: 21 of them on Graybank. The
neighbour has to be long enough itself.

| town | panels before | after | collinear laps | in a road | in a wall |
|---|---|---|---|---|---|
| Pelvesthollow | 599 | **350** | 507 -> **0** | 27 -> **0** | 0 -> 0 |
| Graybank | 2,330 | **1,253** | -- -> **0** | 65 -> **0** | 6 -> **0** |
| Forest Church | 472 | **229** | -- -> **0** | 17 -> **0** | 9 -> **0** |

Half the props, on yards that are now substantially bigger.

### 11.2 A yard is surfaced in its own material, all of it

`_block_role` looks the 2x2 up by the **role the four cells agreed on**, not by
the surface class, and falls through to 1x1 where that role has no block of its
own. Lawned yard cells: 41-60% on all four towns -> **0%**.

This is the larger of the two defects and the one that shows most: with the
boundary removed entirely the yard used to be invisible, because the surface
was contributing nothing. It contributes now.

### 11.3 Four tiers, four boundaries

`YARD_BOUNDARY`, keyed on `tier_of` -- the same axis the facade has used for a
long time:

| tier | piece | height | what it is |
|---|---|---|---|
| civic | `Stone Wall 02` | 7 ft | a precinct wall |
| trade | `Stone Wall 01` | 5 ft | a working yard with stock in it |
| common | `hedge_piece_01` | 5 ft | a garden, and the one living boundary |
| utility | `Wooden Fence` | 3.4 ft | a paddock behind a shed |

The paling is the weakest read of the four and it is now on the buildings that
carry the least. On East Tradebourne that deals 174 hedges, 39 drystone,
8 estate walls and 7 palings.

**`Builder.yard_pieces` records what the pass laid**, because `field_wall` and
`field_hedge` are shared with `_lay_fences` and no asset id can name the pass
that placed one. `feature_report` asks the pass -- the same rule
`_fences_built` states, which the yards line walked straight into the moment
the boundary stopped being paling: it named `yard_fence`, so a town of hedged
cottages reported its yards as unbuilt.

**Two palette roles were coin flips.** `resolve` seeds its choice *inside* the
first matching query, so a single query listing two names picks one per seed.
`field_wall_tall` dealt the ordinary wall on **five seeds in eight**, and
`field_wall_post` -- whose fallback is a 1.98-long wall panel -- put a full
panel across a vertex instead of a joint. Both are two queries now, which is
what makes the second a fallback rather than a coin.

### 11.4 The frontage is fenced where it runs straight

The old rule left every edge onto a way open, which failed at both ends of its
range (§10.4). Closing the ring outright builds a comb against a diagonal lane.
So: the whole ring is built, then opened again on any way-facing run shorter
than `FRONTAGE_MIN_RUN` (3 cells, 15 ft) -- which is exactly the stair-steps,
and leaves the straight stretches fenced.

**And every yard gets a way in.** Where nothing above opened one, a gate is cut
by dropping the middle panel of the longest run, on the side facing the most
paving.

| | before | after |
|---|---|---|
| perimeter fenced | 71% | 70-94% |
| yards with no way in | 17 of 230 (ETB), 5 of 15 (FC) | **0 on all four towns** |

### 11.5 The checks that hold it

- **`_prop_collisions` can tell a corner from a lap.** The allowance excused
  any boundary overlap no deeper than a panel's own thickness, meaning to
  excuse corners -- but two panels lying along the *same line* separate on
  their thin axis first and measure exactly the same. That is how a doubled
  fence stayed invisible on every board this project has built. A join now
  requires the two pieces be non-parallel: `(rot_i - rot_j) % 12 != 0`, which
  works at all 24 steps and so covers a surveyed field wall as well as an
  axis-aligned yard. Pelvesthollow: 146 flagged props of 3,719 -> **6**.
- **`_boundaries_do_not_block_a_way`** and **`_boundaries_stay_on_the_map`**,
  the two of §5's three that can be measured on the artifact without knowing
  the runs. The street check earned itself on its first run: **14 field-wall
  panels standing in a Pelvesthollow lane**. `_lay_fences` sampled three points
  along a panel -- centre and both ends -- and three points cannot see a panel
  crossing the corner of a road cell between two of them. It measures the
  panel's body now (`covered_cells`), and tests it *after* the jitter rather
  than before, which the hedgerow style needed.

**The third check, `fence_runs_are_continuous`, is deliberately not built.**
On the artifact a deliberate gap and a dropped panel look identical -- the
frontage openings, the gates and the odd-run remainders are all gaps by design
now -- so the check would either fire on all of them or be tuned until it fired
on nothing. What it was for is covered exactly by the lap rule above, which is
the failure it was really guarding against.

### 11.6 What it costs, and what is left

East Tradebourne, `--by-region --chunk-tiles 112 --max-assets 6500`:
**408,853 assets in 114 chunks, largest slab 24,354 bytes against the 30,720
cap (79%)** -- no change to the byte headroom, and slightly fewer assets than
before despite yards half again as large, because the boundary is no longer
built twice.

Two residuals, both small and both now visible rather than hidden:

- **8 pairs of field-wall panels on Graybank still lap** (5 drystone, 3 hedge,
  0.06% of its props). These come from `run_along_polyline` at a shallow
  vertex, not from the yard pass, and they are reported rather than excused.
- **`_prop_collisions` fails on any overlap at all**, so a build with 46
  flagged props out of 39,799 reads as FAIL. That threshold is older than this
  work and is left alone here.
