# Roofscape: roofing, chimneys, and rooftop terraces

Three design passes over the roof plane, in the order they depend on each
other: what varies across a roofscape (§2), where a chimney goes (§3), and
which buildings can carry a rooftop terrace and what one is made of (§4).

`docs/great-buildings.md` §§3.1-3.4c is the sibling document and it is not
repeated here: it settles the *gable*, the two course scales, the rotation per
kit, and the crow-step. This one starts where that one stops -- at the ordinary
roof, which is 100% of the board a player actually stands in.

Every number below is measured off `out/{pelves,graybank,tradebourne}-v2/layout.json`
rasterised at `storeys=3`, and off `catalog.json`.

**§§1-7 are design, not built** -- §6 is the plan and §7 is what has to be
probed before any of it is placed. **§§8-9 have been on a board**: §8 decodes
two roofs the user built by hand and handed over, and §9 is what
`tools/roofmix_probe.py` showed when ours was pasted beside them. Where the two
halves disagree, §§8-9 win -- they are the measurement.

## 1. What the screenshot shows, read against the code

A row of six tiled houses at a high oblique. Six roofs, six chimneys, and the
eye reads them as one repeated object. Every part of that is in the source and
each has a different cause:

| what repeats | why |
|---|---|
| one stack per roof, always | `_lay_roofs` places exactly one chimney per roof *block* |
| every stack at the same spot | `chimney_at = crown[len(crown) // 2]` -- the ridge midpoint |
| every stack facing the same way | `place_tile(chimney, x, z, y)` takes no `rot`, so all 1,084 are at rot 0 |
| one material across the row | the tile deal is 333 of 989 buildings; a run of neighbours draws the same suffix |
| one pitch, one course scale | `rise = side.size_y`, and every tier resolves the 1x1x1 slope |
| no dormer, no roof light | `Thatched roof window` is the pack's only dormer and nothing resolves it |

None of these is a bug. Each is a decision that was correct when the roof was
one function, and none has been revisited since.

## 2. Pass one: roofing

### 2.1 Three materials, and whole kits missing from the deal

`ROOF_MIX` deals a suffix per building. Over East Tradebourne's 989:

| suffix | pieces | buildings |
|---|---|---|
| `''` | Rural, thatch | 632 |
| `tile` | Tavern, terracotta | 333 |
| `slate` | Abandoned Village, grey | 24 |

That is the whole roofscape: three materials, one of them at 2.4%. CLAUDE.md
already records a fourth -- **Castle Fortified is brown shingle**, measured for
its hip rotation (`ROOF_ROT_OFFSET`, `castle fortified` -> `(+6, +0)`) -- and it
appears in no palette role and no mix. A civic building is roofed in the same
Abandoned Village slate as a derelict cottage.

`Marble Palace` ships a complete roof (14 pieces, flat and curved) and is also
absent. `walls.KIT_ROLE` maps 22 wall families to a declared job; there is no
equivalent for roofs, which is why the roof deal has stayed at three.

**The shortfall is a roster, not a hunt for pieces.** Same argument
`walls.KIT_ROLE` makes: a kit reachable only through a hand-written palette
role is a kit nothing will ever deal.

### 2.2 The pitch never changes, and the scale never changes

`rise` comes off the slope piece, and every tier resolves a 1x1x1 slope, so
**every roof on every board rises exactly one tile per cell** -- 45 degrees,
everywhere, thatch and tile and slate alike. Real vernacular does not work that
way: thatch is steep (50-55 degrees) because it sheds by depth; tile is
shallower (35-45) because it sheds by lap.

The double-course family is already measured and already built
(`roof_courses`, `roof_course_anchors`, `roof_course_cells`), and it rises 2
tiles per 2 cells -- the same 45 degrees at twice the grain. So the pitch
question is genuinely open: **the library may ship only one pitch**, in which
case this is a finding to record rather than a defect to fix. It has not been
checked. `roof-pitch-survey` in §6.

### 2.3 The ridge is as long as the building

`_roof_rings` floods inward from the block boundary, so on any wing wider than
two cells the innermost ring is a line running the full length of the wing.
Over East Tradebourne's 1,462 wings the crown is:

| cells in the crown | wings |
|---|---|
| 1 | 149 |
| 2 | 309 |
| 3 | 105 |
| 4 | 116 |
| 6 | 217 |
| 8 | 309 |
| 10 or more | about 200 |

309 wings carry an eight-cell ridge -- **40 feet of unbroken ridge line with
one chimney in the middle of it**. That is the largest uniform surface on the
roofscape and nothing varies along it.

**The obvious fix does not work, and the measurement says so.** Deal the ridge
cap between its kit's siblings, the way `walls.deal` already deals wall panels
-- per cell, crc32, stable across rebuilds, with `walls.stem_of` as the test
for whether two pieces are genuinely interchangeable. `Tavern Roof flat 01` and
`Tavern Roof flat weaving 01` are both 1x0.5x1 in the Tavern kit and look like
exactly that case. They are not: `stem_of` gives `tavern roof flat` and
`tavern roof flat weaving`, which are different stems, and it is **right** to
say so -- weaving is a visibly different texture, and dealing across it is the
Shogun Palace failure in miniature (plaster, paper screens, rock and dug earth
all tie at one rank, and dealing across them builds a wall out of four things).

Grouped by `(folder, stem_of)` over every one-cell flat roof cap in Medieval
Fantasy, **exactly one kit ships an interchangeable pair**:

| kit | one-cell caps |
|---|---|
| **Marble Palace** | `Palace Marble roof 1x1 01`, `Palace Marble roof 1x1 02` |
| Tavern | `Tavern Roof flat 01` |
| Rural | `Thatched roof flat 01` |
| Abandoned Village | `haunted roof 1x1 flat` |
| Desert Village | `Desert roof 01` |

So a sibling deal is a **no-op on every board we build today** -- the three
kits in the deal each ship exactly one cap -- and becomes live only if
`roof-kit-roster` brings Marble Palace in. Same finding as
`wall-plain-siblings`, and worth a test that states it rather than leaving it
to be rediscovered.

What actually breaks a 40-foot ridge, then, is **the stack** -- which §3 does
anyway, and which is the argument for doing §3 before anything else here.

### 2.4 The pack's only dormer is at the wrong scale, for the same reason the gable end was

Two roof pieces in Medieval Fantasy carry a window:

| piece | kit | size |
|---|---|---|
| `Thatched roof window` | Rural | 2 x 2 x 2 |
| `Palace Marble roof curve window` | Marble Palace | 1 x 2 x 2 |

Both are **double-course** pieces, and every tier resolves a 1x1x1 slope, so
neither can be dropped into a roof as built -- a 2-tall, 2-deep piece where a
1-tall one belongs is the open trough `docs/great-buildings.md` §3.1 measured.
There is no single-course dormer anywhere in the pack.

That is the *same shortfall as the gable end, from the same direction*. But
§8.2 settles what to do about it: the double-course pieces **mix** with a
single-course field -- a 2x2x2 piece spans exactly two 1x1x1 courses, and the
user's building B is built that way. So a dormer is not a separate project and
not blocked on re-roofing a whole tier either: it arrives with the same
mixed-scale machinery `roof-end-mix-scales` needs, and is worth nothing before
then. Nothing new opens for it here.

(`Village Roof Side Wall With Window 01` is not a dormer and is not unused: it
is the `wall_window` role, the common house's facade window and the only 1-cell
window in the pack. It was misread as a roof piece once already, because its
`group` is `roof`.)

## 3. Pass two: chimneys

### 3.1 A quarter of the roofscape has no chimney at all

One stack per roof *block*, on `max(wings, key=len)`. A secondary wing gets
nothing:

| town | wings | wings with no stack |
|---|---|---|
| Pelvesthollow | 61 | 25 (41%) |
| Graybank | 215 | 61 (28%) |
| East Tradebourne | 1,462 | 378 (26%) |

An L-plan house has a stack on its main range and a cold, blank ell. This is
the same shape of defect the `gable-ends` A/B caught -- `_lay_gabled_wing` at
first placed **no** chimney at all and took the town from 1,578 stacks to 40 --
except that this one has been shipping the whole time and no check names it.

### 3.2 The stack is architecturally in the wrong place

`crown[len(crown) // 2]` puts it at the ridge midpoint of every building in the
town. A vernacular stack is somewhere else, and which one is not a stylistic
toss-up -- it follows the plan:

| form | where | what it serves |
|---|---|---|
| **end stack** | on the gable end wall, through the ridge line | the commonest form there is; a hearth on the end wall |
| **lateral stack** | part-way along a side wall, projecting | a hall hearth against a flank |
| **ridge stack** | mid-ridge | a back-to-back hearth on a spine wall -- what we build, always |
| **party stack** | on a wall shared between two houses | a terrace |

The party stack is worth checking before designing for it, and it is a
**no-op**: measured across all three towns, **zero roof blocks span more than
one building id**. Both importers give every building its own detached
footprint, so there is no party wall anywhere on any board. A party-stack rule
would be a feature that is correct and absent -- exactly what
`verify.feature_report` exists to catch. Do not build it.

The other three are all buildable today. An end stack goes on the crown cell
nearest a chosen end; a lateral stack goes on a perimeter cell with an exposed
side, at the ring the wall head reaches. The choice wants to key on something
the map already knows -- `quarter_at`, the way `gable_end_for` does -- so a
quarter reads as one place.

### 3.3 The tile roof's chimney is a slope piece, not a stack

Resolved under the palette the build actually uses,
`Palette.named(catalog, "medieval", 33)`:

| role | asset | kit | size |
|---|---|---|---|
| `roof_side_tile` | `Village Roof Side 01` | Tavern | 1 x 1 x 1 |
| `roof_chimney_tile` | **`Village Roof Side/Chimney`** | Tavern | **1 x 1 x 1** |

The chimney piece has the **identical collider to the slope**, and its name is
a slash pair. The strong reading is that it is *the slope with a stack cast
onto it* -- a combination piece, the same trap as the 2.5-tall Wall/Floor
family this project already documents. It is placed on the crown cell, at rot
0, twice, lapped 0.25.

If that reading is right then on every tiled roof in every town the piece's
slope half faces rot 0 whatever way the roof actually falls, and a stack
authored to sit *in* a slope is being used as a free-standing crown block.
**This is a hypothesis with a collider behind it and no render behind it.** The
render is one lookup:

```bash
python tools/asset_shots.py --name "Village Roof Side/Chimney" --name "Chimney 01" --name "Chimney 02" --verify
```

The Tavern kit ships **`Chimney 01` and `Chimney 02`** -- two 1x1x1 stacks, in
the same kit, both unused. If the combination reading holds, those are the
pieces the tile roof wanted all along, and there are two of them, which is a
free variance axis on the most repeated object in the picture.

### 3.4 Thatch and slate stack a 0.5 piece twice, when a 1.5 piece exists

`roof_chimney` and `roof_chimney_slate` both resolve to `Thatched Chimney`
(1 x 0.5 x 1), laid twice at `CHIMNEY_LAP = 0.25` for 0.75 tiles of stack --
under four feet above the ridge. `Rural` ships **`Thatched Roof Chimney`
(1 x 1.5 x 1)** in the same kit, three times the height in one piece, and
nothing resolves it. The lap was invented because "a single 0.5-tall piece
sitting on the ridge reads as a stub"; the kit's own answer to that is the
taller piece.

`Abandoned Village` genuinely ships no chimney -- the palette comment says so
and it is correct -- which is why slate borrows Rural's. The borrowing is fine.
Borrowing the *short* one is the part to fix.

### 3.5 Every stack in the town is at rot 0

`place_tile(chimney, ...)` never passes a rotation. On a piece with any
asymmetry -- a lean, a cowl, a flue mouth, a slope half -- that is 1,084
identical objects in one frame, which is what the screenshot shows. The ridge
cap beside it already takes a mirrored rotation (`_ridge_rotations`); the stack
takes none.

A stack is a chimney, so it should not be dealt uniformly over 24 steps either.
Quarter turns only, dealt per building on crc32 the way everything else here is
dealt.

### 3.6 How many stacks a town should have

Median footprint is 30-38 cells, which `floorplan` puts at 3-4 rooms per level:
one or two hearths, so one or two stacks. A rule of *one per wing over 6 cells,
two where the crown runs over 7*, sized against the same three towns:

| town | today | proposed | factor |
|---|---|---|---|
| Pelvesthollow | 36 | 84 | x2.33 |
| Graybank | 154 | 290 | x1.88 |
| East Tradebourne | 1,084 | 1,949 | x1.80 |

At two tiles a stack that is +1,730 placements on East Tradebourne against
411,106 -- **0.4%**, which the byte budget will not notice. The factor is
highest on the village, which is right: Pelvesthollow is 34 single-storey
cottages and every one of them has a hearth.

## 4. Pass three: rooftop terraces

### 4.1 The suitable layout already exists in the data, and it has a name

`building_ranges` splits a footprint over `RANGE_MIN_CELLS = 45` into two
ranges, the far one a storey lower, so `_lay_roofs` gives it two roofs. That
was built to break up the silhouette of a big building. It also produces,
without meaning to, **exactly the geometry a roof terrace needs**: a lower roof
with a taller wall standing along one side of it -- a wall you can put the
terrace door in.

| town | buildings | class A candidates | lower-range cells (median) | shared boundary (median) |
|---|---|---|---|---|
| Pelvesthollow | 35 | 1 (2.9%) | 21 | 7 |
| Graybank | 150 | 4 (2.7%) | 14 | 5 |
| East Tradebourne | 989 | **95 (9.6%)** | 21 | 7 |

*Class A* is: two ranges, lower range at least 6 cells, at least 2 cells of
shared boundary with the taller range. A median candidate is a **21-cell deck
-- 525 sq ft -- with a 7-cell wall behind it**. That is a real terrace, not a
ledge.

On East Tradebourne the 95 split as civic 7, trade 10, common 78; by quarter,
civic 7, market 4, craft 5, residential 79.

The scaling is honest by construction: a village of cottages has one, a city
has ninety-five. Nothing has to be tuned to get that.

### 4.2 The second class is real, and far too large to take as a filter

*Class B* is a single-height building of 2 or more storeys with a wing of at
least 9 cells and a short side of at least 3 -- a whole flat top, reached by a
stair from inside.

| town | class B | share |
|---|---|---|
| Pelvesthollow | 0 | 0% |
| Graybank | 36 | 24% |
| East Tradebourne | **492** | **49.7%** |

Half of East Tradebourne. Flat-roofing half a northern European market town
would not read as a feature, it would read as a different game. So **class B is
a rate, not a filter** -- dealt per building against a per-quarter weight, the
way `ROOF_MIX` and `GABLE_ENDS` are dealt. Class A is scarce enough to take
whole.

Pelvesthollow scoring zero is the same honest fallback as `quarter_at` being
`None` there: a village of 1.03-storey cottages has no roof to stand on.

### 4.3 A terrace and a chimney want the same cell, on 100% of candidates

Measured: of the 95 class-A candidates, the lower range carries its own chimney
on **95 of them** -- the lower range is its own roof block, so `_lay_roofs`
gives it a stack, in the middle, on the cell the terrace deck would occupy.

That is not an obstacle. It is the two passes agreeing about the same building,
and it resolves the architecturally correct way: **when a range becomes a
terrace its stack moves to the taller range's flank wall**, rising against the
wall the terrace door is in. That is exactly where a real lateral stack goes,
and §3.2 has to build the lateral stack anyway. The two passes want the same
function, which is the argument for doing them in one go.

### 4.4 What a terrace is made of, per fabric

Four parts: a **deck**, a **parapet**, a **way up**, and **dressing**.

**Deck.** The building's own floor role, at the lower range's roof height. One
rule inverts. `_lay_upper_floors` lays decks on *interior cells only*, because
a deck on a perimeter cell shows its edge through the facade as a band of
floorboards. On a terrace **the deck must reach the perimeter or there is
nothing to stand on at the edge** -- and the visible edge is correct there,
because a terrace has one, and the parapet covers it anyway. Worth stating
outright, because it reads like unlearning a hard-won lesson and it is not: the
lesson was about a floor seen where a wall should be.

**Parapet.** Applying `crowstep_tread`'s own test -- a 1.0-tall wall panel in
the building's own kit -- across every Medieval Fantasy folder returns four
hits, and two of them are pieces named `roof extra` / `roof filler` that pass
on the collider alone and need a render before anyone believes them:

| kit | piece | size |
|---|---|---|
| Castle Fortified | `castle wall 1x1 half` | 1 x 1 x 0.5 |
| Marble Palace | `Palace Marble wall mid` | 1 x 1 x 0.5 |
| Abandoned Village | `haunted roof extra` | 1 x 1 x 0.5 |
| Shogun Palace | `shogun_roof_filler02` | 1 x 1 x 0.5 |

So a **masonry parapet is a civic and palace gesture in this library**, exactly
as the crow-step turned out to be, and found by the same lookup.
`crowstep_tread` is the function; it wants renaming rather than copying.

A common or trade house gets a **railing** instead, and those are props:

| piece | kit | size | note |
|---|---|---|---|
| `Moorgoth Railing Straight 1x1` | Fences | 1 x 1 x 0.5 | stone balustrade; has `Corner 1x1` and `End 1x1` |
| `Palace Marble fence single` | Fences | 1 x 1 x 0.34 | marble; has `corner` and `double` |
| `Stone fence 02` | Fences | 2.02 x 0.99 x 0.49 | with `Stone Fence Corner 01` |
| `Wooden Fence` | Fences | 2 x 0.68 x 0.18 | 0.68 is knee height -- a kerb, not a rail |

Only the first two are a full cell, waist height, with a matching corner.
`docs/fencing.md` owns the "a boundary is not on the grid" argument, and a
terrace rail is the one boundary that genuinely *is*: it runs along a cell edge
at a known height. A simpler case than the field wall, not a harder one.

**The way up.** Almost every kit ships a 1x1x1 stair -- `Tavern Stair`,
`Rural Stairs` (and `Rural Stairs Railing`, 1.75 with a handrail),
`Palace Marble stair single/mid/top`, `Castle Ruins Stair`,
`abandoned_village_stairs_01`. Two treads climb one storey. There is also a
`Ladders` folder: `Ladder wood 01` is 0.5 x **2.0** x 0.76, exactly one storey,
which is the cheap answer for a class-B terrace with no taller range to enter
from.

`_lay_wall_stairs` already solves "run a flight against a wall, land it flush,
fill under it solid" for the rampart. A terrace stair is that function at a
twentieth of the scale, and its three hard-won invariants transfer intact.

**Dressing.** The kits carry it: `Round Table With Chairs 01`
(1.7 x 0.75 x 1.68), `Bench -Shabby`, `Wooden Bench Small`, `Brazier`,
`Street Lantern`, `Lantern on hook 01`, `Barrel` and `Crate` for a working
roof, `Bush - Medium` and `bush_reg_01` for a planted one, `Rug Hanging 01/02`
for a hung one. This is where `docs/interior-slabs.md` binds hardest:
**0.1% of hand-placed props sit on a cell centre and 84% are on a quarter
turn**, against `_dress`'s 100% centred and uniform over 24 steps. A terrace is
small and looked at closely, so it is the worst possible place to keep that
habit and the best place to break it first.

### 4.5 The library ships one purpose-built rooftop-terrace kit, and it is the desert one

`Desert Village` is Medieval Fantasy, and it is complete:

| part | pieces |
|---|---|
| deck | `Desert roof 01` (1x0.5x1), `Desert roof 02` (2x0.5x2) |
| parapet | `Desert roof border 01/02` (1.25 tall), `03/04` (0.75 -- a kerb) |
| parapet corner | `Desert roof border corner 01/02/03` |
| stair | `Desert Stair`, `Desert stair block` (1x1x1) |
| rail | `Desert fence` (0.5x1x1), `Desert fence corner`, `Desert fence low` |
| floor | `Desert floor 01-04`, `Desert stone floor 01/02`, `Desert floor decor` and corners |

Deck, parapet, corner, kerb, rail, stair and a decorative floor border, in one
folder, in a pack we already build from. Not a coincidence -- the flat roof
with a parapet is the *canonical* desert vernacular, and the kit was authored
for it.

`style-desert` is already open in `tasks.json`. **It just acquired its best
argument**: a desert style is not a re-skin of the medieval town, it is the one
style whose kit makes rooftop terraces the default rather than the exception,
and the terrace machinery is shared either way.

One near-miss, recorded so nobody finds it again and gets excited:
**`Brick Building` ships a complete flat roof** -- `city roof 1x1`,
`city roof 2x2`, `city roof side 1x1/2x1`, `city roof corner 1x1 inner/out`,
all 0.5 tall -- and its pack is **Cyberpunk and Sci-fi**, so `_WRONG_SETTING`
excludes it, and should.

## 5. What must be reported, not just built

`verify.feature_report` exists because fences were built, shipped, reviewed
over two sessions and written up while being absent from every board looked at.
Every feature in this document is more susceptible to that than fences were,
because all three are *scarce by design*:

- a terrace fires on 1 building in Pelvesthollow;
- an end stack fires only where a wing gables;
- a shingle roof would fire only on the civic tier, which is 5 buildings on
  Forest Church.

So each needs its `offered / built` pair the way `_gables_built` and
`_fences_built` have theirs, and the "none here; the layout has N, all outside
this crop" branch is the one that matters. A crop of the town centre is where
every screenshot gets taken, and it is exactly where a rooftop terrace on an
outlying merchant's yard will not be.

## 6. Plan

Ordered so each step is verifiable on a board before the next depends on it.
Steps 1-3 are cheap and fix what the screenshot shows; 4-6 are the terrace.

1. **Render the chimney candidates.** `asset_shots.py --verify` on
   `Village Roof Side/Chimney`, `Chimney 01`, `Chimney 02`,
   `Thatched Roof Chimney`, `chimney wonky`. A lookup, not a probe, and it
   either confirms §3.3 or kills it. Nothing after this should be built first.
   -> `chimney-render-shortlist`
2. **Repin the chimney roles and rotate the stack.** `Thatched Roof Chimney`
   for thatch and slate; whichever of `Chimney 01/02` survives step 1 for tile,
   dealt as a sibling pair; a quarter turn per building on crc32. Pure palette,
   plus one `rot` argument. -> `chimney-repin`
3. **A stack per wing, and put it where the plan says.** One per wing over 6
   cells, two on a crown over 7; end / lateral / ridge dealt by quarter through
   a `CHIMNEY_FORMS` table beside `GABLE_ENDS`. A/B the placement counts on all
   three towns and expect about x1.8. -> `chimney-per-wing`, `chimney-forms`
4. **Probe the terrace before building one in a town.**
   `tools/terrace_probe.py`: one class-A footprint per fabric, deck plus
   parapet plus stair, four faces and overhead, with a bare hipped range beside
   it as the control. Size it to one frame -- `panel_review.ps1` says whether
   it frames *before* it pastes, and CLAUDE.md's "most probes do not fit" is
   what happens otherwise. -> `terrace-probe`
5. **Build class A.** `_lay_roofs` learns to skip a wing, `_lay_upper_floors`
   learns to deck a terrace out to its perimeter, the stack moves to the taller
   range's flank. 95 buildings on East Tradebourne, 1 on Pelvesthollow, and the
   two small towns should come out placement-for-placement near-identical --
   the same A/B shape `gable-ends` used. -> `terrace-class-a`
6. **Class B as a rate, keyed on quarter.** Only after 5 is on a board and
   looks right. -> `terrace-class-b`

Alongside, and independent of all six:

7. **A roof roster.** `ROOF_KIT_ROLE` beside `walls.KIT_ROLE`, so Castle
   Fortified shingle and Marble Palace reach the deal, and a fifth kit arrives
   by being mapped rather than by hand-writing five palette roles.
   -> `roof-kit-roster`
8. **State the ridge-cap sibling count per kit** in a test, so the no-op in
   §2.3 is recorded rather than rediscovered. Live only after step 7.
   -> `ridge-cap-siblings`
9. **Survey the pitch.** Establish whether the library has anything but 45
   degrees to offer, before treating one pitch as a defect.
   -> `roof-pitch-survey`
10. **Report all three** through `feature_report`. -> `roofscape-feature-report`

## 7. What must be probed before it is placed

Standing rules from CLAUDE.md, applied to this document specifically:

- **A probe read from one angle lies.** A parapet is a thin panel standing on
  an edge: from above it is invisible, and from one oblique it hides its own
  gaps. Orbit four sides *plus* overhead, and cut a section with `N`.
- **The four faces of `review.ps1 360` are not cardinal** -- `-DX 320` is about
  60 degrees, and `review-cardinal-faces` is open. A parapet and a stack are
  judged on **silhouette**, which needs a real elevation. Aim it the way
  `crowstep-end-elevation` did: bearing off `camerastate`, 480 px for a quarter
  turn, verified on the compass.
- **Probe with the palette the build uses.** `Palette.named(catalog, style,
  seed)`, never an unseeded `Palette` -- under seed 33 the roles resolve
  differently, and an hour was lost to that once already.
- **Keep a known-bad control in frame.** For the terrace that is a bare hipped
  range of the same footprint beside it; for the parapet it is whichever piece
  step 1 rejects.
- **A render is the shortlist and a probe is the verification.** Step 1 is a
  render on purpose. It cannot say which quarter turn closes anything.

## 8. Ground truth: two roofs the user built by hand

A slab of **two buildings on the same 6 x 4 footprint, differing only in the
roof**, handed over 2026-08-29. Decoded through `citysmith.slab` and matched
against `catalog.json`; 89 placements, all Tavern kit.

Walls are identical in both: a 6 x 4 main block with a 4 x 2 lower rear wing,
one course of `Tavern Wall 01` / `Wall Only With Window`, wall head at y=2.0.
Both also carry a small tiled **pentice** -- one `Village Roof Side 01` at
y=0.5 projecting from the east wall, a door hood. We build porches; we do not
build this.

### 8.1 Building A: flush gable, verge filled with stacked caps

Ridge along x at the middle of the 4-deep block, two courses of
`Village Roof Side 01` each side, gable ends at x=0 and x=5. The verge columns
(x=0 and x=5, z=1 and z=2) carry **two `Tavern Roof flat 01` stacked** -- 0.5
each, so 1.0 = exactly one course -- filling from the wall head to the slope
above. 38 roof placements.

### 8.2 Building B: double-course end pieces, single-course field

Same block, same two courses of 1x1x1 slopes, but the verges are closed by the
**double-course end pieces**:

| piece | size | placed at | rotations |
|---|---|---|---|
| `Village Roof Side End 01` | 1 x 2 x 2 | west verge, 1 cell wide | 12 (fall n), 0 (fall s) |
| `Village Roof Side End 02` | 2 x 2 x 2 | east verge, 2 cells wide | 6 (fall n), 18 (fall s) |

**Two scales in one roof.** The end piece is 2 tall and 2 deep, so one of them
spans exactly two courses of the 1x1x1 field beside it, and two cover the
4-deep roof. The field is the remaining 3 cells: 6 = 1 + 3 + 2, and the field
has to be odd for the ridge to land between two cells.

`docs/great-buildings.md` §3.1 concluded the double-course family could not
reach a single-course roof -- "dropping a 1x2x2 end piece into a single-course
roof puts a two-tile-tall, two-cell-deep piece where a one-tile one belongs,
which is the trough". That is true of dropping one into a *ring flood*. It is
not true of a roof built in courses, and B is the proof.

**End 01 takes the slope's own rotation for the same fall; End 02 takes that
minus 6.** End 01 matches §3.1a's measured +6 row exactly. **End 02's is new**
-- that sweep filtered to pieces presenting a 1x2 footprint and End 02 is
2x2x2, so it was invisible to it.

20 roof placements: **B builds a taller roof with a real ridge in fewer pieces
than A's 38, and fewer than our own 25.**

### 8.3 The flat deck, at two scales, and raised

Both buildings roof the rear wing flat, at the wall head:

| | pieces | height | deck top |
|---|---|---|---|
| A | 8 x `Tavern Roof flat 01` (1x1) | y=1.50 | 2.00, flush with the wall head |
| B | **2 x `Tavern Roof flat 02` (2x2)** | y=1.75 | 2.25, a quarter **proud** |

"It's ok to use the x2 roof slabs when there is space for them" -- 2 placements
for the same 4 x 2 area instead of 8, a 4:1 saving. The constraint is the one
this project already recorded the hard way: a piece bigger than a cell puts its
min corner on the cell and reaches past it, so it goes on the 2-cell lattice
and only where all four of its cells are inside the block.

Both 2x2 caps are at **rot 18**. The piece is square so the turn does not move
the footprint -- it turns the texture.

**The raise is deliberate and worth copying.** B's deck stands a quarter tile
proud of the wall head, which gives the terrace a visible lip instead of a
flush join. A flat roof with no edge reads as a hole.

### 8.4 The chimney is co-located with a slope, never instead of one

| | pieces | rotation | slope under it |
|---|---|---|---|
| A | 2, lapped 0.25 (y=2.75, y=3.00) | **0** | a rot-12 slope at the same cell |
| B | 1 (y=3.00) | **12** | a rot-12 slope at the same cell |

In both, `Village Roof Side/Chimney` sits **on top of an ordinary slope at the
same cell and height**. Ours replaces the ridge cap with two chimney pieces and
lays no roof surface there at all.

That also settles §3.3's hypothesis: the piece behaves as a *slope with a stack
cast onto it*, which is why it is laid over a slope rather than instead of one,
and why its rotation is chosen against the slope -- matching it (B, a stack on
the slope) or opposing it (A, a stack straddling the ridge). **Rotation is how
the two stack positions are expressed**, which is `chimney-forms` arriving from
the data rather than from architecture books. And the 0.25 lap is exactly our
`CHIMNEY_LAP` -- independently arrived at, which is a small vote of confidence.

## 9. What the board said

`tools/roofmix_probe.py` builds the same 6 x 4 three ways side by side -- ours,
the shipped flush gable, and B's end-mix -- on `PROBE roof mix`. Bay 3
reproduces the hand-build at **18 of 20 placements identical** (`--ridge x`);
the two that differ are the chimney's position along the ridge, one cell, and
the pentice, which the probe does not build.

Pasted and read square-on to the verges at medium range:

| bay | what it reads as |
|---|---|
| 1 OURS | a **flat-topped box**. One course, hipped all round, a 4 x 2 flat deck where the ridge should be |
| 2 FLUSH (shipped) | a **horizontal band at the verge with the roof set back behind it** -- a parapet, not a triangle |
| 3 END-MIX | **a gabled house.** Timber-framed triangle, diagonal brace, tiled slopes from a proper apex |

**Bay 3 is the only one of the three that reads as a roof anyone would draw.**

Two process notes, both of which cost something:

- **At a distance the eaves shadow reads exactly like an open gable.** The
  first pass at this concluded both gable bays were see-through, from a wide
  shot. Pasting the user's own slab and looking at it close showed its verges
  closed -- so the reading was the error, not the geometry. A verge is judged at
  medium range, square-on. This is "a probe read from one angle lies" with a
  second clause: *and at one range*.
- **The first cut of the probe hand-rolled bay 2** and was about to report that
  stacked-cap infill does not work -- while `gable-single-course-infill` was
  already **done**, and `build.gable_infill` already does it correctly by
  stopping at the roof's *underside* rather than its top. The copy filled to the
  top. That is CLAUDE.md's "a probe that reimplements what it is probing can
  only tell you about the probe", caught one step before it became a finding.
  Bay 2 drives the shipped function now.

### 9.1 A bug found on the way: the verge is not the roof's material

`gable_infill(palette, tier, tread)` resolves its cap from
`roof_set(palette, tier)` -- the **tier's default** material. `_lay_roofs` deals
the material per *building* through `roof_suffix_for` and `roof_override`. The
two disagree:

| tier | dealt roof | verge infill | buildings on East Tradebourne |
|---|---|---|---|
| common | tile | **thatch** | 153 |
| trade | thatch | **tile** | 60 |
| trade | slate | **tile** | 14 |
| civic | slate | `castle wall 1x1 half` | 10 |
| civic | tile | `castle wall 1x1 half` | 2 |

**227 of 989 non-civic buildings (23%) would carry a verge in a different
material from their own roof** -- a tiled house finished in thatch. The civic
rows are correct by design: the masonry gable wall carried up is what the
docstring intends. `gable-infill-follows-the-tier-not-the-roof`.

It bites only where a wing actually gables, which today is the civic quarter --
but `gable_end_for` deals by quarter and another seed reaches the residential
one, so this is live rather than theoretical.

## 10. Built: `endmix`

`roof-end-mix-scales` is **done**. §8.2 said the two scales mix; this is the
wiring, and it is on a board.

`build.gable_end_piece` resolves the double-course end from the **roof's** own
kit by collider -- one cell along the ridge, `END_PIECE_CELLS` across, twice the
field's rise. By collider and not by name, because `Thatched Roof Wall` carries
`end` in its name too and is 2 x 2 x 1: a verge board, not a double-course end.
`_end_pairs` pairs each verge column's cells from the eaves inward, per slope
half, and **leaves the leftovers** -- an odd half, and the single ridge cell an
odd-depth wing carries, keep the flush treatment. Same rule `walls.pack`
follows for a wide panel and `lay_flat_deck` for a 2x2 cap: lay the wide piece
where all of its cells fit, fill the remainder with the narrow one.

Why that is enough, measured over gable-eligible wings on all three towns:

| across depth | wings | what pairing does |
|---|---|---|
| 4 | 437 (34%) | both halves pair whole |
| 5 | 576 (45%) | both halves pair, one ridge cell falls back |
| 3, 6, 7 | 259 (20%) | one leftover cell per half |
| 8, 9 | 4 | whole, or whole plus a ridge cell |

**It self-gates.** Only `Tavern` ships an end piece, so a wing whose roof was
dealt thatch or slate falls back to flush inside `_lay_gabled_wing` -- exactly
as `crow` falls back where the fabric ships no tread. That is why `endmix` can
be weighted freely in `GABLE_ENDS` without a rule about which quarters get
tile. It takes its share from `flush`, because it *is* a flush gable closed
with the kit's own end instead of stacked infill. `civic` is left alone:
crow-stepping is the masonry form.

### 10.1 The A/B

Isolated properly -- the deal is identical in both arms and only whether
`endmix` is *honoured* changes. (The first attempt renormalised the weights,
which moved thatch counts that `endmix` cannot touch, and was thrown away.)

| town | flush | endmix | |
|---|---|---|---|
| Pelvesthollow | 1,394 | 1,394 | **identical** |
| Graybank | 5,044 | 5,044 | **identical** |
| East Tradebourne | 47,948 | **44,372** | **-3,576, -7.5%** |

1,192 end pieces replace 2,384 slopes and 2,384 caps. **Cheaper and better at
once**, because the piece closes its own triangle and the verge then needs no
infill at all. The two small towns being untouched is the honest-fallback
guarantee holding: no quarters, so no gables, so nothing to change.

### 10.2 The test caught a real bug before the board did

The first cut inferred the end piece's footprint from the **fall**. That works
only because Tavern's edge offset is +6, which makes `n` and `s` both even
quarter turns and lands the 1 x 2 piece one cell along the ridge by two across.
At an offset of 0 the same fall gives an *odd* turn: the piece lies across the
ridge and its min corner lands half a tile outside the wing.
`test_an_end_piece_never_overhangs_its_wing` swept 4-11 by 3-9 and found it at
4 x 4. `rotated_footprint` decides now, and a verge whose end cannot face the
right way keeps flush rather than being laid wrong -- "a gable it cannot close
is worse than a hip", one step down.

Worth noting *why* the A/B did not catch it: the A/B runs the real palette,
where the offset is always +6. **A measurement on real data cannot see an
assumption that real data happens to satisfy.** The test could, because it was
free to pass a value the towns never produce -- and then had to be corrected
too, because passing 0 was not a simpler test but an impossible configuration.

### 10.3 On a real town

A 180 x 180 East Tradebourne crop at seed 33 -- 180 square because quarters
need a 1.2 clustering lift and a smaller crop has none, which is the trap
`gable-ends` recorded. This one reads 1.28x.

    [ok] gabled ends: 211 of 213 wing(s) gabled, dealing crow, endmix, flush;
         2 of them still carry a hip corner, which is a terrace roofed as one block

384 `Village Roof Side End 01`, and hip corners down to **8** across the whole
crop. `verify._gables_built` needed no change: it asks whether a wing the deal
picked out carries a hip corner, and endmix lays none.

### 10.4 What this does not fix

`roof-flat-top-on-a-small-wing` is untouched and is the bigger of the two.
`endmix` only reaches a wing the quarter deal chose to gable; **every hipped
wing still goes through `_roof_rings`**, and on a 6 x 4 that is one course and a
4 x 2 flat deck. Bay 1 of `PROBE roof mix` is still a flat-topped box, and 5 x 6
is the commonest wing shape on every board measured.

`gable-infill-follows-the-tier-not-the-roof` is also still open, and endmix
*masks* part of it -- 1,200 of the mismatched thatch caps on East Tradebourne
disappear simply because those verge cells are now closed by an end piece
instead. The leftover cells still get the wrong material.

## 11. Built: the ridge, and the two kinds of chimney

Two more off the board, both from things that looked wrong in a screenshot.

### 11.1 The hip pinches to a ridge now

`roof-flat-top-on-a-small-wing`, done. `roof_top_is_supported` decides whether
a top-ring cell caps; the ring index no longer does.

**The old rule was too broad rather than wrong.** Capping the top ring exists
for a real reason -- "a slope at the apex shows its open underside", the bare
timber that showed at the top of every slate roof. But that only happens where
nothing backs the slope up. Two slopes on the same ring falling opposite ways
lean on each other and make a proper ridge; a corner leans on its diagonal. So
the test is the **neighbour**, not the ring.

The defect is an even/odd split, which is why it hid for so long:

| short side | flood stops at | old behaviour | now |
|---|---|---|---|
| **even** (4, 6) | a band **2 cells** wide | all capped -- a plateau, one course low | slopes and corners meeting at a ridge |
| odd (3, 5) | a **1-cell** ridge line | capped | unchanged |

A/B on all three towns: **placement count identical in every one**. Same cells,
flat caps become slopes and corners.

| town | flat caps before | after |
|---|---|---|
| Pelvesthollow | 206 | 28 |
| Graybank | 594 | 81 |
| East Tradebourne | 12,242 thatch + 3,093 tile | 10,543 + 2,430 |

Corners rose to match -- `Thatched Roof Corner 01` 152 -> 970 on East
Tradebourne, `Village Roof Corner 01` 52 -> 390.

### 11.2 A chimney is two pieces, and the cell picks

`chimney-sits-on-a-slope`, done, and the user named it off a screenshot before
any render did: *"one doesnt need the (chimney on slant roof) tile, just the
normal chimney. the other has the right slab, but in the wrong orientation."*

Both halves are decided by what the cell is:

| cell | piece | rotation |
|---|---|---|
| a capped ridge | `roof_stack` -- free-standing | none needed |
| a slope | the combination, laid **over** the slope | that slope's own |

Before, every stack on every board was the combination piece at rot 0, laid
**twice** and lapped, with no roof surface under it. So a ridge stack wore a
bare slope on end as a pale skirt, which is exactly what the screenshot showed.

**The catalog corroborates the combination reading independently**, and it is
worth recording because §3.3 reached it from the collider alone:
`Village Roof Side/Chimney` carries tags `roof` *and* `chimney` *and* `wood`;
`Chimney 01` carries `chimney` and `stone`, no `wood`. The timber in the tags
is the slope half.

East Tradebourne goes from 1,084 combination pieces all at rot 0 to 152
combination across rotations 0/6/12, plus 201 `Chimney 01` and 380
`Thatched Roof Chimney` free-standing. On the 180 x 180 crop the stack
placements halve, 460 to 230, because the doubling is gone.

`Thatched Roof Chimney` (1 x 1.5 x 1) also replaces two lapped
`Thatched Chimney` (1 x 0.5 x 1). Those made 0.75 tiles of stack -- under four
feet -- and the taller piece was sitting unused in the same kit. That is
`chimney-repin` item 1; item 2, dealing `Chimney 01` against `Chimney 02` as
siblings, is open as `chimney-sibling-deal`.

### 11.3 Both changes cut buried geometry, and that is the measurable part

A flat cap under a slope was a buried pair; a slope beside a slope is not. And
a chimney laid twice at one cell was a pair by construction.

| | seams |
|---|---|
| Pinfold (the golden report) | 280 -> **176** |
| East Tradebourne 180 x 180 crop | 3,822 -> **1,526** |

A 60% drop on the crop, measured through `verify.tile_interpenetration` on the
two builds' own structure slabs. Two golden lines moved with it -- the seam
count, and one slab 798 -> 792 bytes -- and nothing else in either report
changed, including the asset totals.
