# Market squares

A design pass over the carved plaza and the market laid on it: what the fixed
7x7 square was and the measurements that condemned it, how the square is now
grown out of its surroundings, how the dressing follows what hand-builders
actually do, and what is deliberately left undecided until the probe has been
on a board.

Every number below is measured off the rasterised artifact
(`samples/forest_church.json` imported and rasterised this revision, plus the
same import run against the pre-rework code), off the three FTG layouts, or
off the 2,382 hand-placed props in `docs/interior-slabs.md` -- not estimated,
and not read out of the code. `tests/test_market.py` holds the invariants;
this file holds the evidence.

## 1. What the 7x7 was, measured

The fallback plaza was a `PLAZA_SIDE = 7` block stamped onto the 7x7 window
containing the most street cells, subject to the whole block being *clear* of
buildings, walls and water. Two consequences follow from that rule, and both
are visible in the artifact:

| Forest Church | fixed 7x7 | grown |
|---|---|---|
| area | 49 cells | 130 cells |
| extent | 7x7 at (58-64, 75-81) | 18x13 across (63-80, 66-78) |
| perimeter cells | 24 | 39 |
| perimeter cells against a building | **1** | **18** |
| doorways opening onto the square | 1 (the smithy) | 4, from 3 buildings |
| cells on the main carriageway | 43 of 49 (**88%**) | 63 of 130 (48%) |
| connected, building access, cart pinches | all clean | all clean |

(A doorway "opens onto the square" when the cell its door faces is plaza.)

The two bad numbers are the same number. Demanding a block clear of buildings
maximises the square's distance from every facade, so the stamp lands on the
widest open paving it can find -- which is the middle of the through road.
88% of the old square *was* the carriageway; one perimeter cell in 24 touched
a building. A market square is the room between frontages -- the stalls back
onto the shops, the doors open into the crowd -- and this was a rug in the
middle of a road.

It was also unguarded. FTG exports author their own squares (`MARKET` /
`PAVEMENT` polygons -- East Tradebourne's Warden Market), which the raster
paints before the carve runs, and the carve then added a second 7x7 market on
the busiest street of every such town. Any existing PLAZA cell now disarms
it: the carve is a fallback for exports with no square, nothing more.

## 2. The square is grown, and every dimension comes from the surroundings

`raster._carve_plaza` / `_grow_plaza`. Three decisions, each pinned to a
measurement:

- **The seed scores frontage as well as traffic.** Street cells within
  `PLAZA_TRAFFIC_RADIUS = 3` (the old stamp's own 7x7 window) *plus* building
  cells within `PLAZA_FRONTAGE_RADIUS = 5`. The frontage term exists because
  scoring traffic alone re-derives the old failure: main streets are four
  tiles wide and buildings stand a verge back from the carriageway, so a
  traffic-only seed lands mid-junction and the grown square touched 0-11
  frontage cells on the two towns measured. With the frontage term it lands
  where the busiest street meets the densest block: 18 and 26.
- **Target area is `20 * sqrt(buildings)`, clamped to [24, 256].** The
  constant is fixed by the one authored market on hand: Warden Market
  rasterises to 631 plaza tiles in a town of 991 buildings, and
  631 / sqrt(991) = 20.0. The square-root *shape* is chosen by a frontage
  measurement rather than by the single data point (one point fits any
  curve): scaling linearly from the same market gives Forest Church 33
  cells, and a 33-cell square at its junction touches zero frontage -- it
  drowns in its own junction. At 20 * sqrt(51) = 143 it reaches the facades.
  The floor is a widened junction -- all the market a hamlet has -- and the
  ceiling stops a fallback from paving a quarter of a town big enough to
  have authored its own square.
- **Growth is BFS over public open ground only** (STREET / GROUND / LANE --
  never a building, wall, gate, field, pier or water cell), nearest cells
  first, radius-capped so an open flank cannot leak the disc down a street;
  then smoothed so every cell belongs to a full 2x2 plaza block (a one-cell
  tentacle down a lane reads as a paving error); then cut to the seed's
  connected component, so the square is one room.

The target is a budget, not a promise: Forest Church's target is 143 and the
square comes out at 130, because the frontages, the radius cap and the
smoothing clip the disc. That is the design working -- the surroundings
decide the outline, the formula only says when to stop asking for more.

Street class survives the paving, which is what lets `verify` still see the
through route crossing the square and lets the dressing keep off it.

## 3. The dressing: rows and aisles, not mist

The old dressing was a p=0.16 roll per plaza cell: goods everywhere, no
structure, one well somewhere. `docs/interior-slabs.md` measured what
hand-builders do instead, on 2,382 community-placed props, and every number
argues against uniform scatter:

| measured on hand-built boards | the old roll | `_dress_market` now |
|---|---|---|
| 84% of props on quarter turns | uniform over 24 steps | goods at the measured 84%; stalls always, a row is deliberate |
| 0.1% on a cell centre | 100% dead centre | jittered off-centre, jitter clamped so no box reaches across an aisle |
| placed in runs and clusters | independent per-cell rolls | stall rows with goods clustered in their gaps |
| 1 in 5 props bigger than a cell | shape never consulted | the stall's rotated footprint sets the row pitch and stretches the period |

The layout, per connected plaza region, seeded from the region's own lowest
cell (`zlib.crc32`, the `_notch_buildings` pattern, so a rebuild lays the
same market):

- **Rows along the square's long axis, one row every third line**
  (`MARKET_ROW_PERIOD = 3`), so every aisle is two cells -- the same
  two-abreast pedestrian floor the streets are held to (`raster.LANE_TILES`).
  A stall deeper than one cell widens the period by its extra depth; the
  aisle never shrinks, whatever the asset measures.
- **Rows face each other across a shared aisle**, fronts to the walkway.
- **At most two stalls stand shoulder to shoulder**
  (`MARKET_MAX_STALL_RUN = 2`) before a cross-gap is forced. Not taste: the
  first version of the pass put three abreast on Forest Church and the 30 ft
  counter sealed 8 cells of the square against its own frontage.
- **The well goes at the centroid of the biggest square**, first, with a
  reserved ring of standing room -- and the street dressing then knows not
  to stand a second one somewhere else in town.
- **Nothing stands where a cell has a job.** On Forest Church that is 83 of
  the 130 cells: the through route crossing the square (`street_class`
  main/cart -- the lane the carts have), the frontage strip where doors open
  and `_stack_trade_goods` leans its wares, the mouths of entering streets,
  and door aprons. The market lives in the other 47.
- **Every placement is tested before it stands**: cell-by-cell box coverage
  plus a connectivity flood that refuses anything which would cut the
  square's open cells in two. `verify.market_square_open` re-measures both
  claims on the *emitted boxes* -- per this project's metrics rule, the plan
  is not the artifact -- and fails the build on a blocked cart route or a
  walled-off corner. Props shorter than `MARKET_BLOCKS_ABOVE = 0.5` block
  nothing; a basket is stepped over.

Measured on Forest Church's grown square, dressed with the test suite's stub
assets in the real shapes the roles ask for: 4 stalls, 4 goods, 1 well --
9 placements, 9 on quarter turns, `market_square_open` clean, 0.19 props per
*eligible* cell. That is still sparser than the hand-built 0.41-0.66, and
deliberately: those densities are interiors, and most of a market square is
circulation the keep-clear rules protect. Raising density before the stall
asset is probed would be more of a placement nobody has looked at yet.

Degradation is role by role, never an error: no stall asset means goods
clusters where the stalls would stand, no goods means bare gaps, no well
means no well. Pelvesthollow's corner square comes out as a well and a
basket -- 77 of its 92 cells are carriageway or frontage, and a hamlet's
market on a live junction is exactly that thin.

## 4. What is deliberately not decided, and what the probe must settle

**No stall asset is picked, and no name is invented.** The machine this was
designed on has no TaleSpire install, so nothing could confirm what the
Medieval Fantasy pack calls a stall -- or that it has one. `market_stall`
and `plaza_well` are therefore structured queries only (`group=`/`tags=`,
the palette rule), in `palette.OPTIONAL_ROLES` so `validate()` does not fail
an install that lacks them: their pass degrades by design, where a missing
*required* role leaves a silent hole. A fabricated name would resolve to
nothing forever while looking pinned -- the failure mode the palette rules
exist to prevent. The well's first query pins `Well 01` because the street
dressing has always placed it; that one is a known-good name, not a guess.

Also unknown until a paste: **which quarter turn is a stall mesh's front**
(`_stall_rotation` reads the footprint's long side off the collider, but a
collider cannot say which face is the counter), and whether the stall's
canvas top reads as a roof or as a wall from eye level.

`tools/market_probe.py` is the gauntlet, built to the standard the wall and
roof picks paid for:

    python tools/market_probe.py --seed <the build's seed> > out/marketprobe.slab.txt
    # paste, then review.ps1 360

Run it with the build's own `--style`/`--seed` -- `Palette.resolve` seeds a
choice inside the first matching query, so a different seed can place a
different stall, and the probe marks which candidate this seed's palette
will actually use. Reading:

- **Pads are numbered by a bar of N cobble cells running east**, sitting on
  the grass -- count in plan, never by tallying at an oblique.
- **The facing rank** (rot 0, 6, 12, 18, each with a cobble strip against
  its south face): the rotation whose counter opens onto its strip names the
  mesh's front. If that disagrees with `_stall_rotation`'s pick, the fix is
  one constant in that function -- every stall on every board turning its
  back on the aisle is the failure it prevents.
- **The lane** (two rows over a two-cell aisle, the generator's own row
  arithmetic, cross-gap and goods included): judge from **eye level in the
  aisle**, because the aisle is where the party stands. Then orbit -- four
  faces, overhead -- since a rank of booths hides its own gaps from the
  front exactly the way the wall pieces did.
- **The control** is `castle merlon 1x1` laid as a stall row: boarded timber
  that crowned the rampart in crates for eleven revisions. It must read
  wrong; it calibrates every screenshot that contains it.
- If the role resolves to nothing, the probe says so and the degraded
  goods-cluster market is what towns get -- also worth one paste before
  deciding it needs fixing, since an unfurnished square with a well is a
  real form, the same way an unfurnished interior is.

After the probe: pin the winner in the palette (by kit, per the corner
lesson), rebuild a real town, and re-read `market_square_open` -- the pitch
and period derive from the asset's measured footprint, so a real stall
re-shapes the rows on its own, but only the board says whether the aisles
read as a market or as a car park.
