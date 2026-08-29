# Great buildings: markets, warehouses, churches, and the halls

The four things this file is about are the four things a party actually goes
*to*. Everything else in a town is the walk between them.

They are also the four cases citysmith is worst at, and the reason is
structural rather than a matter of taste: **every building on the board is
massed as a stack of house storeys.** A house is what the code knows how to
build, so a barn is a short house, a church is a tall house, and a market is
not a building at all — it is paving with props on it.

This is the design. Section 1 is what the boards actually measure today,
section 2 is what the buildings should be, section 3 is what the library can
supply, and section 4 is the plan.

Companion files: `docs/building-massing.md` (storeys, footprints and yards —
§13 there is the three-iteration probe on large buildings),
`docs/asset-conventions.md`, `docs/district-surfaces.md`.


## 1. What the boards measure now

All figures below are measured off the four real layouts in `out/`, rasterised
through `raster.rasterize` and read through `build.storeys_of` and
`build._roof_rings` — so they are the artifact, not the plan. Heights are
`storeys * 2.0` tiles to the eaves and `+ max ring depth` to the ridge, at
5 ft per tile.

### 1.1 The biggest buildings in the town are the lowest buildings in the town

East Tradebourne, 989 buildings, mean per kind:

| kind | n | mean cells | eaves | ridge |
|---|---|---|---|---|
| temple | 4 | 66 | 28 ft | 38 ft |
| **warehouse** | **18** | **58** | **10 ft** | **19 ft** |
| guildhall | 5 | 52 | 26 ft | 36 ft |
| house | 707 | 35 | 20 ft | 28 ft |
| tavern | 14 | 33 | 29 ft | 36 ft |
| smithy | 171 | 29 | 23 ft | 31 ft |
| shop | 67 | 27 | 24 ft | 31 ft |

A warehouse is the **second largest footprint on the map** and the **shortest
thing standing on it**. The largest is 129 cells — 3,225 sq ft, three and a
half times a house — and it stands 10 ft to the eaves while every house around
it stands 20 ft. Its ridge, at 19 ft, is nine feet *below* its neighbours'
eaves.

The cause is one line of arithmetic. Eaves height is `storeys * storey_h`,
`storey_h` is the wall piece's 2.0, and `layout.FLAT_KINDS` forces warehouse,
stable and shed to one storey — correctly, because a stable with an upper
floor is a stable nobody can get a horse into. **Height is derived from floor
count, and a great barn has one floor and no ceiling.** The rule that stops a
stable becoming a tenement also stops a tithe barn becoming a tithe barn.

For scale: a medieval great barn's eaves are 20–25 ft and its ridge 30–40 ft;
Great Coxwell is 152 x 44 ft and about 48 ft to the ridge, and great barns run
3.5:1 to 5:1 in plan. A two-storey townhouse of the period is 25–35 ft to the
ridge. So the correct silhouette is a barn standing **over** the houses, and
we build it at half their height.

### 1.2 Every roof is hipped, and not one of these four is a hipped building

`build._roof_rings` is a breadth-first flood inward from the block's real
boundary, one course up and one cell in per ring. That is a **hip on all four
sides**, always, for every footprint on every board. On an elongated rectangle
the innermost ring is a line rather than a point, so a ridge does emerge — but
both ends are still hipped, and the ends are where the interest is.

Barns, church naves and great halls are gabled. Not incidentally: the gable
end is a *functional* wall, and it is where the cart doors, the loading
hatches, the vents, the west window and the bellcote go. A hipped end has
nowhere to put any of them.

This is the largest single visual difference between what we build and what
these buildings look like, and it is one function.

### 1.3 A church is a three-storey box, and the two grandest have no tower

`pick_towers` gates on `TOWER_MIN_TILES = 60` **and**
`TOWER_MIN_ASPECT = 2.5`. On East Tradebourne, of 12 civic buildings, two get
a tower:

| building | cells | plan | aspect | tower |
|---|---|---|---|---|
| `temple-0027` | 88 | 8 x 12 | 1.50 | — |
| `temple-0004` | 81 | 10 x 9 | 1.11 | — |
| `temple-0003` | 65 | 15 x 5 | 3.00 | **Y** |
| `guildhall-0002` | 64 | 7 x 10 | 1.43 | — |
| `guildhall-0886` | 62 | 5 x 14 | 2.80 | **Y** |

**The two largest churches in the city are towerless, and a smaller one is the
landmark.** The aspect gate reads compactness as "not a nave" — but a compact
plan at 88 cells is a nave *with aisles and transepts*, which is precisely the
church that has a tower. The gate is inverted with respect to its own
intention, and its docstring ("a nave with a tower at one end is the one
silhouette nobody mistakes for a barn") argues for the opposite of what it
does.

What the towerless ones get instead: three courses of `Castle Fortified` wall,
glazed one-in-2 at the front and one-in-3 on the flanks on **every** course,
upper floor decks on the interior cells, and a hip roof. That is a tenement in
dressed stone. A nave is one volume with a floor at the bottom and a roof at
the top; there is no first floor in a church.

For scale, from the period: a modest parish church is a nave of 35–50 x 16–22
ft (7–10 x 3–4 cells) with a chancel half to two thirds its length; a large
town church is 60–90 x 24–35 ft (12–18 x 5–7 cells), aisles 8–18 ft, and a
west tower 12–30 ft square standing clearly above the nave ridge. Our temples
are 8x12 and 10x9 — large-town-church footprints, built as flats.

### 1.4 A market is paving with six props on it

Plaza cells per town: Forest Church 49, Graybank 49, East Tradebourne 631.

`build._dress_districts` scatters `market_goods` at 0.16 per plaza cell, and
`market_goods` resolves to six loose props — `Crate - Large`, `Baskets`,
`Apple Basket`, `Barrels`, `Sack`, `Bench -Shabby`. There is no stall, no
awning, no market cross, no market hall, and no weekly-market geometry of any
kind. 631 cells is about three and a half acres of empty stone.

A medieval market place is a short list and we have none of it: an **open
square**, a **market cross** at its centre, **stalls or benches ranged around
it**, and a **tolbooth or market hall** on the edge — an open arcade at ground
level for trade with an enclosed civic chamber over it, roughly 6–7 m by
10–13 m for a compact one, gabled, sometimes with a belfry.


## 2. What each of the four should be

Stated as silhouettes, because the silhouette is what a player sees from
across the board and it is what all four of these currently get wrong.

**Warehouse / barn.** One tall volume. Eaves at 4–5 tiles (20–25 ft), ridge at
6–8 (30–40 ft), gabled both ends, cart doors in the gable on the long axis's
end wall, a porch over the main pair. No upper floor, no windows — the
`utility` tier's window-less Rural boarding is already right and is right for
the same reason it was chosen. Internally, aisle posts down the length: that
is what lets the span be wide, and it is a room a party fights in.

**Church.** One tall volume plus a tower. The nave is a single storey pitched
at 3–4 courses rather than three storeys of two, glazed **once**, high up —
one band of tall windows, not three bands of house windows. A west tower over
the narrow end, standing above the ridge. Where the footprint is wide enough,
aisles: a lower range each side of a taller centre, which is the clerestory,
and which `building_ranges` already knows how to express as two storey counts
flooding into two roofs.

**Market.** Not a building — three things on a square. A **cross** at the
centre of the largest open run of plaza. **Stalls** in rows along it, from the
`Merchant` kit, oriented to face an aisle rather than scattered. Optionally a
**market hall** on the plaza edge: an arcade of piers at ground level with a
gabled chamber over, which is a small building with a hole in the bottom and
the one genuinely new geometry here.

**The halls — guildhall, manor, barracks, the great tavern.** The great-hall
volume: eaves 18–25 ft, ridge 30–40, gabled, one open storey where it is one
range and a stepped silhouette where `building_ranges` splits it. A roof
louvre over the hearth end is the period detail that names the type.

**The common thread, and it is one idea.** All four are buildings whose height
is set by their *volume*, not by how many floors are stacked in them. That is
the axis citysmith does not have. It has settlement band, kind, footprint area
and inside/outside-the-walls, and it turns all four into a floor count.


## 3. What the library can actually supply

Read out of the installed packs through `tools/kit_index.py` and
`citysmith.walls.families`, not out of asset names.

### 3.1 The gable pieces exist, in a kit we already build from

| piece | kit | size | note |
|---|---|---|---|
| `Village Roof Side End 01` | Tavern | 1 x 2 x 2 | the gable end |
| `Village Roof Side End 02` | Tavern | 2 x 2 x 2 | its 2-cell partner |
| `Thatched Roof Wall` | Rural | 2 x 2 x 1 | the thatched verge |
| `Village Roof Side Wall 01/02` | Tavern | 1 x 2 x 0.5 | the panel *under* the gable |

The last row is the one worth noticing. `Village Roof Side Wall 01/02` is
already in the palette — as the common house's **facade panel**, and then as
"the only 1-cell window in the Medieval Fantasy pack". It is neither. It is a
roof set's gable infill, which is exactly what `walls._rank` says when it
ranks `group='roof'` below `group='wall'`.

**REFUTED ON THE BOARD, 2026-08-28. The sentence that stood here was "the
gable is buildable today and its pieces are already resolved", and it is
false.** `tools/gable_probe.py` built the four end treatments, and the roof
came back with open troughs at every gabled end. The cause is not a rotation
and not a ring depth:

**Tavern and Rural each ship their roof at TWO SCALES, and the end pieces are
in the wrong one.**

| | single-course | double-course |
|---|---|---|
| slope | `Village Roof Side 01` 1x1x1 | `Village Roof Side 02` 1x2x2 |
| corner | `Village Roof Corner 01` 1x1x1 | `Village Roof Corner 02` 2x2x2 |
| **end** | **none** | `Village Roof Side End 01/02` |

`_roof_rings` steps one cell in and one course up, and `_lay_roofs` takes its
rise from the slope piece — which resolves to the 1x1x1 for every tier
(measured: civic, trade, common and utility all step 1.0). The double-course
family steps **two** rings at once. Dropping a 1x2x2 end piece into a
single-course roof puts a two-tile-tall, two-cell-deep piece where a one-tile
one belongs, which is the trough.

**There is no single-course end piece anywhere in the catalog** — searched
across all 3,200 assets for a roof tile at rise <= 1.0 tagged or named `end`:
zero. So this is a property of the library, not of the Tavern kit.

This is `md_tower_wall_01` again — "a 4x4 tower piece is a quarter of an 8x8
tower" — arriving from a new direction. The collider was plausible, the tag
said `end`, and the render showed a tiled slope closed with a solid triangle.
All three were true and none of them said *what scale the piece belongs to*.

Two ways forward, and they are genuinely different designs:

- **Roof great buildings from the double-course family.** It is complete —
  slope, corner, inner corner and end, at both 1-cell and 2-cell widths — and
  a two-cell course is proportionate to a building that is 15 cells across
  anyway. A 15-cell nave is 7 fiddly courses at the single scale and 4 big
  ones at the double. Cost: the double family's rotations are **unmeasured**
  (see below).
- **Crow-step the gable** out of `Village Roof Side Wall` panels at the
  single-course scale, letting the ridge run out to a stepped end wall. This
  is not a compromise dressed as a feature: a crow-stepped gable is a real
  northern-British and Scottish form, and it is the only gable a one-cell ring
  scale can express.

**`tools/roofrot_probe.py` could not see any of this**, and that is the second
finding. It filtered to `(1.0, 1.0)` footprints — so the tool built to answer
"which quarter turn closes a hip", after this project got the hip rotations
wrong once and wrote them up, was blind to the entire double-course family.
It takes `--footprint 1x1|1x2|2x2|wide|all` now, and `--band matrix|runs|both`,
because both bands on one board is 46 cells deep and does not frame.

**Its ground sheet was also computed by hand and was too small**, which is a
defect in its own right and not only a framing nuisance: `depth` was
`len(ROTS) * ROW_PITCH + 12` while Band B ran to `4 * (RUN + 2)` past the
labels, so at the 1x1 scale the sheet stopped at z=32 with roof pieces
standing over bare void out to z=46. The extent is read off the placements now
(`placed_bounds`), so a band that grows cannot outrun its own ground again.


### 3.1a The double-course rotation, MEASURED

`gable_probe.py --scale 2` sweeps the four quarter-step offsets, building a
gabled double-course roof at each. Board: `PROBE gable double-course`.

**Two of the four tile and two are a rank of fins**, which is the sweep working
as designed. Which two is settled analytically rather than by counting label
bars off a screenshot — and that matters, because reading the bars off the plan
shot gave `+12, +18` and that is **wrong**:

| offset | fall n | fall s | footprint | result |
|---|---|---|---|---|
| +0 | rot 6 | rot 18 | 2x1 | wrong axis — fins |
| **+6** | **rot 12** | **rot 0** | **1x2** | **tiles** |
| +12 | rot 18 | rot 6 | 2x1 | wrong axis — fins |
| +18 | rot 0 | rot 12 | 1x2 | tiles |

`Village Roof Side 02` and `Village Roof Side End 01` are both authored
1 x 2 x 2, so only the two offsets that present a **1x2** footprint can sit on
the pair of cells a double-course piece spans. That is +6 and +18, exactly two,
exactly matching the board.

**+6 is the answer, and the kit says so itself.** `build.ROOF_ROT_OFFSET`
records Tavern's single-course edge offset as **+6**, giving fall n → rot 12
and fall s → rot 0 — identical to the double-course +6 row above. **The two
scales share one convention.** +18 is its 180-degree inversion: it tiles, but
every slope falls the wrong way.

**Worth recording as a caution: the inversion was NOT distinguishable by eye**
from any of the seven views taken. Both +6 and +18 read as closed, correct
roofs from overhead and from all four faces. Had the analytic footprint check
not been run, a coin-flip between them would have looked fully evidenced. This
is "a probe read from one angle is a probe that lies" with the twist that
*seven* angles did not help — the discriminator was in the collider, not the
picture.

**What the gable actually looks like, and it is the payoff:** a proper ridge
running the full length with tiled slopes falling both ways and a **closed
triangular gable end with timber framing**. Unmistakably a barn or a hall, and
unmistakably not a hip.

**The catch, and it is a real constraint: the double-course family only tiles
a 4-CELL SPAN.** `_roof_rings` steps one cell per course and a piece covers
two, so the scales only agree where the span is exactly one piece each side.
Enumerated across spans:

| span | rings across | result |
|---|---|---|
| **4** | 0,1,1,0 | **32/32 cells covered, no gap, no overlap** |
| 5 | 0,1,2,1,0 | ridge cell bare |
| 6 | 0,1,2,2,1,0 | two cells bare |
| 8 | 0,1,2,2,2,2,1,0 | two cells bare |

So a 20 ft span gabled cleanly and **nothing wider did**.

**CLOSED. `build.roof_courses` is the flood that steps at the piece's own
scale**, and every span from 3 to 13 now tiles at both scales with zero gaps
and zero overlap — asserted rather than asserted-about, in
`tests/test_roof_scale.py`. The arithmetic is four lines:

    W        span across the ridge
    full     complete courses per side = (W // 2) // cells_per_course
    covered  cells each side's courses reach = full * cells_per_course
    middle   W - 2 * covered, the ridge band

`roof_course_cells` reads the scale off the piece's rise (1.0 -> 1 cell,
2.0 -> 2), so a caller never states it; `roof_course_anchors` returns only the
**outermost** cell of each band, which is what stops two pieces landing on one
pair at the ridge.

It is a **new function beside `_roof_rings`, not a flag on it.** That one
floods from a block's whole boundary and is what every hip on every board is
built from; a gable is a different question asked of the same rectangle, and
`roof_wings` has already cut the plan into rectangles before either is called.
Nine existing callers are untouched.

**Verified on the board at a real warehouse footprint** — 11 x 11, two 2.0
courses of wall for a 20 ft eaves, gabled both ends: `PROBE gable warehouse`.
In plan the roof is one continuous tiled surface with no gap anywhere; from
the side it is a proper barn, with a ridge running the full length, slopes
falling both ways and closed gable ends.

**The cost, and it is visible in that plan shot: the remainder is capped
FLAT.** A span of 4k tiles with no cap at all; anything else leaves 1 to 3
cells of flat ridge deck — on the 11-cell warehouse, three cells, which reads
as a small flat strip down the middle of the roof. That is a real roof form
and it is not what a tithe barn has. Finishing the remainder with a
*single-course* pair instead, mixing the two scales at the ridge only, would
leave at most one flat cell; it is `roof-ridge-mixed-scale` in `tasks.json`.

**The roof was one unit too big to the north and east, and a copy-out off the
board is what said so.** Decoded against the probe's own output, the ridge cap
ran to x=15 on a building whose walls stop at x=14 — because the cap was
`Tavern Roof flat 02`, which is 2 x 0.5 x **2**, laid one per cell with
`place_tile`. A piece bigger than a cell puts its min corner on the cell and
reaches past it. The kit ships `flat 01` at 1 x 0.5 x 1 for exactly this.

Three things worth keeping about it:

- **It is CLAUDE.md's oldest asset rule** — "`place_tile` needs an asset that
  fills the cell" — arriving through a piece table I wrote by hand. I reached
  for "the 2-wide partner" because the *courses* were two cells deep, and the
  cap is laid per **cell**, not per course. Those are different axes and the
  name does not distinguish them.
- **The town was never affected.** `palette`'s `roof` role resolves to
  `flat 01` for every tier at every seed checked — the probe bypasses
  `palette.CELL_ROLES`, which is the guard that would have caught it.
- **It is guarded now**, by
  `test_every_roof_piece_laid_per_cell_is_exactly_one_cell` over four tiers and
  four seeds. That guard exists for `gable-ends`, not for today: wiring the
  gable into `_lay_roofs` means writing a double-course piece table for real,
  and this is the exact mistake waiting there.

**One more thing the board found that the arithmetic could not.** The first
warehouse was built without the gable *infill*, and it was a barn you could
see straight through: `Village Roof Side End` closes the roof's own edge and
**nothing closes the wall below it**, so the gable was an open triangle with
the far slope's underside visible through it. §3.1's own table names
`Village Roof Side Wall` as "the panel *under* the gable" and the wide piece
set left it out anyway. One panel per course, stepping up toward the ridge, is
what makes the triangle a triangle.

Three placement bugs were found and fixed getting here, all mine and all worth
keeping because each is the same shape of error:

- The flood **walked into the gable columns**, so an end cell took a depth from
  the flood instead of inheriting one, and the whole gable column sat a course
  high with no cell for the end piece.
- The eaves frontier counted a neighbouring **end** cell as an edge, dropping
  the two columns beside each gable to ring 0 — a step in the ridge a cell
  short of both ends.
- The fall test measured **all four sides**, so a gable-end cell saw open board
  along the ridge axis, read as a corner and was skipped — which is exactly the
  cell the end piece exists for. The first run left every gable open and read
  as "the end piece does not close", when nothing had asked it to.



### 3.2 The tall wall exists and is unreachable by construction

`walls.families` prunes every kit to one course height, so all 22 families
come back at 2.0. That is right for a house — it is what stopped Tavern's 2.5
Wall/Floor corner being dealt for its 2.0 wall. It also means these are
invisible to every caller:

| piece | kit | size |
|---|---|---|
| `Moorgoth LargeWall 01` | Moorgoth | 1 x **4.0** x 2 |
| `Moorgoth LargeWall 02` | Moorgoth | 1 x **4.0** x 1 |
| `Moorgoth Large Roof` | Moorgoth | **4 x 4** x 1 |

A 4.0 wall is a 20 ft eaves in a single course, which is the exact number the
barn and the hall want.

**Except that neither of them is a wall.** Read as renders before any slab was
built, `Moorgoth LargeWall 01` and `02` are tall **glazed openings** — dark
stone frames around a traceried window. The catalog said so and I did not
look: both carry the tag `window`, which the collider cannot express and which
was sitting in our own local data the whole time. So the shortlist step earned
its keep on its first real use, and the lesson is narrower than "look at
renders" — **the tags carry more than the collider does, and they are already
local**.

What follows:

- **For the barn, they are out.** A barn wants blank boarding, and a 20 ft
  window is the opposite of that.
- **For the church, they are exactly right.** A 20 ft traceried opening is a
  nave clerestory, which is the one thing §2 asks for that nothing else in the
  library supplies.
- **There is no blank tall wall in the catalog at all.** Every tile at least
  3.0 high whose group or name says "wall" is one of six: two Castle Fortified
  `Tall 2x2x4 Roof filler` pieces, `Desert Entrance` (an arch), `Palace Marble
  wall Dome`, and these two windows. So a great barn's 20 ft eaves **must** be
  two stacked 2.0 courses. §4.2's "smallest honest version" is not the
  smallest version, it is the only one.

The pruning point still stands for the church: a pruning rule written for
houses hides a piece the great buildings need, and the fix is not to loosen it
— it is that a great building asks for a **course height** and gets the family
pruned to *that*.

### 3.3 Marble Palace is the church kit, and Moorgoth is the gothic one

`Marble Palace` is the only kit in the catalog that authors all three courses
(base / mid / top), and it is `complete`: 6 wall pieces at each width, 5
window variants at each width, 3 corners, fillers. Beyond the wall family it
ships `arch base/mid/top` and `wall Arch full base/mid/top` (2-cell), `pillar
base/mid`, `Rim top` (a cornice), `roof curve` in six pieces, `roof tower`
(1 x 4 x 1) and `wall Dome` (1 x 4 x 1). That is an arcade, a clerestory and a
crossing tower, in one material, already coursed.

`Moorgoth` ships `Buttress Base` / `Buttress Arch` / `Buttress Spire` — a
flying buttress in three stacking pieces — plus `Fancy Pillar`, `Wall With
Arches`, `Wall With Lit Arches` (which carries its own light), and the 4x4
roof.

Neither kit is in the medieval palette. Today: civic is `Castle Fortified`,
trade and common are `Tavern`, utility is `Rural`.

### 3.4 The Merchant kit is a complete market and is entirely unused

Twenty-four pieces, and no reference to the kit anywhere in `palette.py` or
`build.py`:

| group | pieces |
|---|---|
| stall counters | `Stall Fruit 01`, `Stall Vegetables 01/02`, `Stall Corner Spices`, `Stall Empty 01`, `Stall Corner Empty` |
| tent wall | `Tent Wall 01` (1x2x1), `Tent Wall 02` (1x2x2) |
| tent corner | `Tent Wall Corner 01/02`, `Tent Wall Inner Corner 01` |
| tent roof | `Tent Roof A/B` at 1x1, 1x2 and **2x2** |
| overhang | `Tent Overhang 01/02`, `Tent Overhang Corner` |
| hanging | `Tent Roof Hanging 01–04` |

Wall, corner, inner corner, roof at three spans, and an overhang for the
awning: this is a complete building family for a stall, in the same shape as a
wall family. `Furniture` adds `Stall Empty 02`, `Stall Magic` and `Stall
Weapons` at 2 cells wide.

**No market cross exists**, and that is still true — but it is the wrong
question, and `tools/cross_probe.py` answered the right one. A cross is a
**stepped base, a shaft and a head**, and the library has all three as
separate pieces, so the question is which stack reads as one. Swept 4 shafts x
4 heads with a null row and a null column, on `PROBE market cross`:

- **The stepped plinth reads, and I doubted it in writing first.** Three
  courses of plaza paving is 0.75 tiles — under four feet — and the docstring
  said that might simply not register from eye level. It does: pale stone
  steps under a dark monument are legible from the ground at native pixels.
- **The shaft is doing real work.** The null column — a head sitting straight
  on the steps — reads as an object someone left in the square. That is what
  a control is for.
- **A tapering pinnacle on a round drum reads as a market cross.**
  `Moorgoth Buttress Spire` over `Castle Ruins Pillar Base/Mid/Top` is the
  strongest of the sixteen; `Moorgoth Buttress Base` reads as a gothic
  tabernacle and is the runner-up.
- **A statue on a tall shaft does not.** `Knight Statue` is 0.59 wide against
  a 1.0 drum, so at monument height it reads as a doll on a column. It is a
  better *monolith* than a head, which is what the separate `--monoliths`
  board is for.

### 3.4b What the probe pass settled, and what it left open

Run 2026-08-28. Five boards built, three pasted and walked with
`review.ps1 360`. Every board was checked with `camera_aim --slab` **before**
it was pasted, and three of the five had to be resized — the first gable board
was 59x29 and needed 70 tiles of slant range against a stop at 50. That is
`probe-size-to-one-frame` firing in advance instead of after the fact, which is
the whole point of it.

Settled:

| question | verdict |
|---|---|
| Moorgoth LargeWall as a barn wall | **rejected** — it is a window (§3.2) |
| a blank tall wall anywhere | **does not exist**; stack two 2.0 courses |
| gable end at the roof's own scale | **does not exist**; ends are double-course (§3.1) |
| market cross | **build it**: pinnacle on a drum, on three steps (§3.4) |
| paving as a step | **yes**, 0.75 tiles reads from eye level |

Left open, and each is now a task rather than a paragraph:

- **The double-course roof rotations are unmeasured.** `roofrot_probe.py` can
  see the family now but its board does not frame at that scale.
- **Neither aisle-post candidate is right.** On `PROBE barn aisle`,
  `Harbor Beam 01` reads as a thin dark stick at 0.5 thick and is nearly
  invisible at 0.2 — and it is **two different assets sharing one name**, which
  is why the probe pins by id. `Dungeon Pillar` reads as a substantial pier and
  is stone in a timber barn, eating a whole cell of a five-cell floor. The
  answer is a third piece nobody has looked at yet.
- **A 20 ft blank wall reads as a fortress, not a barn.** This is the finding
  nobody was looking for. Two courses of `Rural Wall 01` with no openings is a
  windowless cliff — §2's "no upper floor, no windows" is right about the
  windows and wrong about what that leaves. A barn wall needs relief: boarding
  lines, a cart door, a vent, a lean-to. Raising the eaves without that trades
  one wrong silhouette for another.

One probe bug worth keeping, because it is this project's own rule biting the
probe rather than the town: the first gable board built its shell from
`Tavern Wall 01` with `place_tile` and a hand-rolled rotation. That piece is a
**2-cell** panel, so every segment overhung its neighbour and two stacked on
each cell edge — a jumbled dark mass, sitting directly under the roof the probe
existed to judge. `place_wall` exists precisely to inset the thin axis and read
which axis is thin off the collider. A probe that hand-rolls what the builder
already does correctly is testing its own arithmetic.


### 3.4c The crow-stepped gable, probed

**`Tavern` is the only kit in the library with a roof `end` piece.** Rural,
Castle Fortified, Abandoned Village, Moorgoth and Marble Palace ship none, at
either scale. So the measured double-course gable is available to the house
fabric and to nothing else — and the two great buildings that most want a
gable, the barn in boarding and the temple in dressed stone, cannot have one
that way.

A crow-stepped gable needs no end piece: the gable wall carries on past the
roof in steps that follow its pitch. `tools/crowstep_probe.py` sweeps four end
treatments in the **civic** fabric (Castle Fortified walls, Abandoned Village
slate — what `roof_set` actually resolves for that tier) on `PROBE crow-step`.

**The piece is `castle wall 1x1 half`** — 1 x **1.0** x 0.5, coursed pale
stone, exactly one 45-degree step. Settled by render before any slab existed,
and so was its rival: `castle merlon 1x1 stair L/R` is 1x1x1 and the name says
stair. It **is** one — a wooden staircase for climbing. CLAUDE.md already
records that this kit's entire merlon group is boarded timber ("the circuit was
crowned with wooden crates for eleven revisions"), and the render confirmed it
in a second. Third time this session the shortlist has paid for itself.

**It works.** In profile the steps read as a deliberate masonry staircase —
pale stone against grey slate, unmistakably an architectural decision rather
than a jagged edge. A stone building gets an identity the flush gable does not
give it, and it costs one piece the kit already ships.

Two things the board corrected:

- **A crow-stepped end is not roofed, and the first run got that backwards.**
  It laid the roof over the end column and stood the parapet on its outer face,
  reasoning that a parapet is a wall beside a roofed cell. On the board that is
  a row of **detached lumps with slate between them**: the parapet stands one
  course proud of the roof at each position, and the roof one position inward
  is a course higher again, so it rises between every pair of steps and the
  staircase never reads as one wall. A crow-step is the building's *end* — the
  roof stops against it. The end column carries the parapet and no roof.
- **The coping is worse than none.** `Top 1x1 flare out` is a real leaded
  coping and at this scale it reads as a row of little hats perched on the
  steps, each visibly detached. `crow-bare` is cleaner. If a coping is wanted
  it needs a piece that sits flush, not a roof-top border course.

**The square-on end elevation, taken 2026-08-29, and it confirms the form.**
`PROBE gable by district`. At native pixels, square-on to the west wall, the
crow-step is **continuous coursed masonry** climbing in clean one-tile steps to
a flat two-cell apex, standing proud of the slate behind it. The "detached
lumps" of the first run are entirely gone. Pale stone against grey slate reads
at a glance, and the three treatments — hip, flush, crow — are distinguishable
instantly, side by side, which is what makes dealing them by district worth
doing at all.

**Why every earlier reading was oblique, and it is a defect in the review
recipe rather than in anyone's patience.** `review.ps1 360` orbits
`-DX 320` between shots, and CLAUDE.md already records that **320 px is about
60 degrees, not a quarter turn** — it says so explicitly, in the camera
section, and calls inferring a game constant from our own script "the trap this
whole file exists to warn about". So the four shots named `n`, `e`, `s`, `w`
are 60 degrees apart from an arbitrary start: they are four obliques, and they
can only land square-on to a wall by luck. Every crow-step reading before this
one came off them.

The elevation was taken by aiming instead: read the bearing off the compass
(`ts.ps1 camerastate`), turn 180 degrees at the measured 0.1876 deg/px, verify
on the compass again, then drop the pitch. **The open-loop turn landed clean**
— W at the top of the rose before, E after — which is worth recording beside
`camera-drive-open-loop`, the task about a driven move landing 14 degrees off.
A 180 on the spot is evidently not the case that fails.

`review-cardinal-faces` in `tasks.json` covers making the recipe's four faces
actually cardinal.

**Dealt by district.** `build.GABLE_ENDS` and `build.gable_end_for` deal a
treatment per **quarter**, not per building, stably from a seed — the same
crc32 deal `roof_suffix_for` uses, for the same reason (a town must rebuild to
the same bytes; `boards.digest_of` depends on it). Per quarter rather than per
building is the argument `QUARTER_SURFACE` already makes about paving: a
district that ends its ridges one way reads as a district from across the
board, and a roofline dealt per building reads as noise. `ROOF_MIX` varies the
*material* per building; this varies the *silhouette* per quarter, and the two
axes stay separate.

`civic` is weighted 70% to `crow` — not taste: crow-stepping is a masonry form
and civic is the dressed-stone tier. `outskirts` is all hip, because a
crow-stepped gable is a town building's gesture and a cottage in the fields
does not make it.


### 3.5 Aisle posts and arcades

`Harbor Beam 01/02` (0.5 x 2 x 0.5) is the timber post for a barn aisle;
`Harbor Leg 01/02` its half-height partner. `Dungeon Pillar`, `Desert Pillar`
and `Moorgoth Fancy Pillar` are 1 x 2 x 1 full-cell columns. For a market
hall's ground-floor arcade the candidates are `castle wall 2x2 unique arch` /
`castle wall ARCH` (2 x 2 x 0.5, Castle Fortified) and the Marble Palace arch
walls — an *opening piece*, which is the open `wall-feature-pieces` task in
`tasks.json` and which this design is the first real caller for.


## 4. Plan

Ordered by evidence per hour, and each step is checkable on the artifact
rather than in the plan. Nothing here is started.

### 4.1 The measurement that decides the shape of everything after it

**A "great building" is a population, not a tier.** Under `>= 45 raster cells`
and a kind in `{warehouse, temple, guildhall, barracks, manor, tavern}` the
four towns give:

| town | buildings | great | share |
|---|---|---|---|
| Pelvesthollow | 35 | **0** | 0% |
| Graybank | 150 | 2 | 1.3% |
| Forest Church | 51 | 4 | 7.8% |
| East Tradebourne | 989 | 16 | 1.6% |

Pelvesthollow getting zero is the check, not a miss: a hamlet has no great
buildings, and a rule that gave it one would be `raise_a_landmark`'s mistake
made deliberately. This is the same shape as the settlement-band argument in
`storeys_for`, and it is why this is *not* a fifth tier — the
`civic-large-town` task already records why a fifth tier is a special case in
a tier's clothes.

### 4.2 Steps

1. **Volume, not floors.** A great building's eaves come from a course height
   and a course count, not from `storeys * 2.0`. Smallest honest version:
   `utility` at 45+ cells gets two 2.0 courses of blank boarding instead of
   one, which is 20 ft eaves with no new asset and no new pass. The 4.0
   `Moorgoth LargeWall` is the follow-on, and it needs `walls.families` to
   take a requested course height.
2. **Gable the ends.** `_roof_rings` grows a `gable_axis` argument: on a great
   building the ring flood does not step in from the two ends, so the ridge
   runs out to the gable wall, and the triangle is filled with `Village Roof
   Side End` over `Village Roof Side Wall`. Every piece is already resolved.
   This is the single biggest visual win in the file.
3. **Fix the tower gate.** Drop `TOWER_MIN_ASPECT` for footprints over ~75
   cells, or replace it with "the tower goes on the narrow end of the largest
   rectangle in the plan", which reads a compact aisled church correctly and
   still refuses a house. Evidence: `temple-0027` and `temple-0004` acquire
   towers; nothing under 60 cells does.
4. **One volume, not three floors.** A great civic building gets its glazing
   dealt **once** at the head course rather than per course, and no upper
   decks. Evidence: no `floor_upper` placement inside a great civic footprint.
5. **The market square.** In order: a cross at the centroid of the largest
   open plaza run; stall rows from the `Merchant` family, aligned to an aisle
   and facing it; then the market hall as a small arcaded building on the
   plaza edge. Each is independently visible, so each is its own review.
6. **The market hall's arcade.** The first real caller for
   `wall-feature-pieces` — an arch belongs where the raster says there is a
   way through, which on an arcade is every bay of one wall.

### 4.3 What has to be probed before it is built

This project's standing rule is that a collider is data and an appearance is
not, and three of its worst picks were pieces whose measurements were honest.
So, before any of it lands:

- **The market cross.** No candidate identified. Shortlist by render
  (`tools/asset_shots.py`), then probe.
- **The gable pieces**, read from all four faces plus overhead, at both 1-cell
  and 2-cell spans, with a known-bad control in frame.
- **Aisle posts inside a barn**, which is the first interior anyone will see
  through an open cart door.
- **`Moorgoth LargeWall`**, whose 4.0 height is the whole reason to want it and
  whose appearance nobody here has ever looked at.

`tools/lot_probe.py` already composes several large buildings onto one board
with `_isolate`, and §13 of `docs/building-massing.md` records why isolation is
not optional: iteration 2 of that probe was misread because a neighbouring
building was in the crop. Every probe in this file goes through it.
