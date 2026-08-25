# Building massing: storeys, footprints and yards

A design pass over how tall buildings are, what shape they take, and what
happens in the space around them. Measured on all four towns.

Nothing here is built yet. The companion pass on surfaces is
`docs/district-surfaces.md`; §5 is the finding they share.

## 1. Every settlement is the same height, and that is a bug with a number

Floor count is invented in one place, identically in both importers
(`ftg._read_buildings`, `mfcg`):

```python
floors = 1
if kind != "shed":
    if area >= 40:   floors = rng.randint(1, 3)
    elif area >= 20: floors = rng.randint(1, 2)
```

Footprint area is the only input. Not the settlement, not the kind beyond
`shed`, not where the building stands. What that produces:

| town | buildings | 1 storey | 2 storeys | 3 storeys | mean |
|---|---|---|---|---|---|
| Pelvesthollow | 35 | 25.7% | 45.7% | 28.6% | 2.03 |
| Forest Church | 51 | 31.4% | 41.2% | 27.5% | 1.96 |
| Graybank | 150 | 33.3% | 33.3% | 33.3% | 2.00 |
| East Tradebourne | 991 | 35.6% | 34.9% | 29.5% | 1.94 |

**A 35-building hamlet and a 991-building city have the same skyline.** Every
town converges on a flat third/third/third, mean 2.0.

The reason the area gate does nothing:

| town | footprint median | ≥40 tiles | 20–40 tiles |
|---|---|---|---|
| Pelvesthollow | 56 | **97%** | 3% |
| Forest Church | 65 | 86% | 12% |
| Graybank | 57 | 94% | 6% |
| East Tradebourne | 66 | 92% | 8% |

An FTG house is 34–35 ft across, so a median footprint is **56 tiles** — well
over the 40-tile gate. Between 92% and 97% of every town takes the
`randint(1, 3)` branch, and the threshold that was meant to keep small
buildings low never fires.

**Pelvesthollow is 74% two-or-three storey.** That is the hamlet reading as a
town, and it is the whole of the complaint.

## 2. What should drive height instead

A settlement's height is a function of land value and of what the building is
for, and both are available:

- **Settlement size.** A hamlet is single-storey cottages with a two-storey inn
  and nothing else. A city centre stacks because the plot is expensive. The
  building count is known at import; nothing needs to be configured.
- **Kind.** Already authored on FTG, already tiered by `tier_of`. A tavern, a
  guildhall, a manor and a temple earn height; a house, a shed, a stable and a
  warehouse mostly do not. `utility` is already pinned to `UTILITY_STOREYS` —
  that rule is right and wants extending, not replacing.
- **Position.** `inside_walls` is a real axis on East Tradebourne — **443 of
  991** — where it was 1 of 51 on Forest Church and 0 of 150 on Graybank. So it
  is available exactly where height variation matters. `_main_street_frontage`
  already computes through-road frontage for the glazing.

A shape for it, subject to the review in §6:

| settlement | typical | what stands taller |
|---|---|---|
| under ~60 buildings (hamlet) | 1 | the inn and the hall at 2 |
| ~60–250 (village, small town) | 1, some 2 | trade on the through road at 2, civic at 2–3 |
| over ~250 (town, city) | 2 inside the walls, 1 outside | civic and trade in the core at 3 |

Footprint stays in the model but stops being the *only* input, and its
threshold has to move: at 40 tiles it selects 92% of buildings, so it is not
selecting anything. The median is the honest place to cut.

## 3. Footprints are near-square, and the shape question is not where it looks

| town | aspect ratio (long/short), median | p90 |
|---|---|---|
| Pelvesthollow | 1.19 | 1.33 |
| Forest Church | 1.42 | 2.02 |
| Graybank | 1.20 | 1.33 |
| East Tradebourne | 1.30 | 1.84 |

FTG footprints are blocky — a median building is half again as long as it is
wide at most. **These are authored polygons, so citysmith does not get to
choose their shape**, and a design pass that proposes L-plans and wings for
imported towns is proposing to overrule the export.

Where shape *is* ours to choose is `city.py`, the generated-city path, which
lays its own plots — and that path is not what any of these four towns came
through. Worth separating explicitly so the work does not get misfiled:

- **Imported towns**: the footprint is data. What we control is height (§2),
  fabric (already tiered), and the ground around it (§4).
- **Generated cities**: the footprint is ours, and L-plans, courtyards and
  ranges around a yard are a real design question. Out of scope here.

## 4. Yards exist, and only in the small towns

Nearest-neighbour gap, measured edge to edge:

| town | median gap | p90 | touching (<5 ft) | roomy (>40 ft) |
|---|---|---|---|---|
| Pelvesthollow | **2.6 tiles** | 8.5 | 37% | **14%** |
| Graybank | 1.0 | 7.9 | 55% | 9% |
| Forest Church | 0.0 | 5.7 | 86% | 8% |
| East Tradebourne | 0.0 | 0.7 | **94%** | **0%** |

**East Tradebourne has no gaps at all** — 94% of buildings touch a neighbour
and the 90th percentile gap is two-thirds of a tile. It is a terraced city, and
a yard scheme there would have nowhere to go.

**Pelvesthollow has 13 ft between buildings and one in seven standing in more
than 40 ft of clear ground.** That is where yards are, and it is exactly the
settlement where the storey rule is most wrong. The same measurement condemns
one feature and enables the other.

What happens in that space today: `_dress_districts` keeps trees back from
buildings (`TREE_CLEARANCE = 3`) and drops `yard_clutter` — a log pile, a cart,
barrels, a ladder — at p=0.28 inside that ring. So a yard gets *props* and no
ground treatment, no boundary and no structure. The surface stays grass right
up to the wall.

The design, in the order it is worth doing:

1. **Surface the yard.** A worked yard is not lawn. Trodden earth or gravel
   inside the clearance ring, tapering to grass — which is tier 1 of
   `docs/district-surfaces.md` applied at building scale, and needs the same
   material set.
2. **Bound it.** A yard with an edge is a *place*; a yard without one is a gap
   between houses. `docs/fencing.md` builds runs of paling and drystone along
   an arbitrary line, and a yard boundary is exactly that — the `yard_fence`
   role is already pinned and unused. This is the one piece of joined-up work
   across all three passes.
3. **Give it a reason.** Cluster the clutter by the building's trade rather
   than scattering it: `TRADE_CLUTTER` already exists for street frontage and
   is the model. A smithy's yard has fuel and slack; a stable's has straw and a
   trough.

Only step 1 needs new geometry. Steps 2 and 3 are existing machinery pointed at
the space between buildings instead of the street in front of them.

## 5. The finding both passes share

> **Settlement size decides which axis exists.** East Tradebourne has real
> trade quarters — smithies in clumps of 55 and 47 — and no room between its
> buildings. Pelvesthollow has no quarters at all and 13 ft of median gap.
>
> A city varies by **district**. A hamlet varies by **building**. Neither axis
> is available on the other.

That is why the storey rule keys on settlement size and the surface rule keys
on quarter, and why each has to measure before it fires rather than assume the
town it is on. It also explains why the existing code looks arbitrary: one flat
`randint(1, 3)` and one flat cobble palette are what you get when a single rule
has to serve a hamlet and a city at once.

## 6. Verification, and what needs a board

The numbers here are all off the layout, and two of the three questions cannot
be settled that way:

- **Storey mix** is a distribution and a table settles it: rebuild all four
  towns and check the hamlet comes out mostly single-storey while the city does
  not. A test can assert the ordering — mean storeys strictly increasing with
  settlement size — without pinning aesthetics.
- **Whether a hamlet now reads as a hamlet** needs a board. Pelvesthollow is 35
  buildings and 20,687 assets in 9 chunks, so the whole town fits a review:
  build it before and after and put both through `review.ps1 360`, judged from
  eye level and from a low oblique. Plan view will not answer it — a roof looks
  the same height from directly above, which is the reading trap this project
  has recorded three times.
- **Whether a yard reads as a yard** needs eye level specifically, and it needs
  the fencing work landed first, since a bounded yard is most of the effect.

## 7. Plan

1. **`storeys_for(settlement, kind, inside_walls, frontage, area)`** replacing
   the `randint` in both importers, with the size bands in §2. One function,
   because both importers currently duplicate the same four lines.
2. **Move the area threshold off 40 tiles** to something that selects a
   minority rather than 92%.
3. **A test that the mean storey count rises with settlement size** across the
   four fixtures.
4. **Yard surfacing** (§4 step 1), once the surface materials from
   `docs/district-surfaces.md` §5 tier 1 exist.
5. **Yard boundaries** (§4 step 2), once `docs/fencing.md` lands.
6. **360 on Pelvesthollow before and after**, at eye level.

Deliberately not in scope: footprint shaping for imported towns (§3), which
would overrule the export, and L-plans for generated cities, which is a
`city.py` question and a separate pass.

---

# Second pass: floors, layouts and large footprints

Measured after the first pass landed, against East Tradebourne as built.

## 8. Large footprints are not the defect, and the measurement says so

The expectation going in was that a big building would be a big box under a
pyramid. Measured on the artifact — the raster, not the polygons:

| | |
|---|---|
| buildings ≥60 cells | **69 of 989 (7%)** |
| median box fill of those | **92%** — they are rectangles, not L-plans |
| sides ≥60 ft | 9 buildings; ≥80 ft, 3 |
| roof courses, whole town | 2 courses 35%, 3 courses 53%, 4 courses 11%, 5–6 courses 1% |
| tallest roof | **15 ft**, on two buildings |

`_roof_rings` floods inward from the *real* boundary, so an elongated block
gets a ridge along its length rather than a pyramid — the 22×6 warehouse
(110×30 ft) roofs in three courses. Only a near-square footprint pyramids, and
there are two of those on the map.

And the footprints barely vary in the first place:

| town | p50 | p90 | p95 | max |
|---|---|---|---|---|
| Pelvesthollow | 56 | 63 | 64 | **72** |
| Graybank | 57 | 64 | 69 | 301 |
| Forest Church | 65 | 123 | 178 | 424 |
| East Tradebourne | 66 | 93 | 102 | 431 |

**A village's biggest building is 1.3x its median.** FTG exports near-uniform
footprints, so "design for a large building" is a thing a *city* has and the
other three towns do not — the same shape as the district finding, arriving
from a third direction.

**What this did surface is a bug in the first pass.** `BIG_FOOTPRINT_TILES` was
set to 80 against oriented-extent area, which is what both importers have at
the point they decide. But a footprint loses about half of itself between the
plan and the board — median extent area 66, median raster cells 30, **ratio
0.48** — because streets hold against footprints, `_notch_buildings` and
`_absorb_fragments` cut them, and the first building to claim a cell keeps it.
So 80 read as "28% of the town" on the plan and **1% on the artifact**. This is
the plan-versus-artifact trap `CLAUDE.md` records seven times, arriving in the
importer where there is no artifact to read yet. Recalibrated to 100, which is
about the 93rd percentile on East Tradebourne and above every building in
Pelvesthollow.

## 9. The real defect: a quarter is monochrome by construction

The town is built from almost nothing:

| | |
|---|---|
| buildings whose kits are exactly `Rural` + `Tavern` | **894 of 989 (90%)** |
| tiers | common 707, trade 252, utility 18, civic 12 |

and tier decides the fabric. Measured per quarter, as *building cells*:

| quarter | tier mix |
|---|---|
| residential | common **98%** |
| craft | trade **98%** |
| market | trade **99%** |
| docks | utility **100%** |
| civic | civic **100%** |

**Tier is keyed on kind. A quarter is a clump of one kind. So they are the same
variable, and every quarter is uniform by construction.** The clustering that
makes a quarter legible — which is what the surface pass spent its effort
proving is real — is the identical clustering that guarantees every building
inside it is built the same way.

On the board that is the craft/market block: 19 of 21 buildings trade tier, 15
of 21 at three storeys, every roof terracotta, every wall the same dark
timber-frame. It reads as a housing estate.

## 10. What was done, and what the pack will not allow

**Roof material is dealt per building instead of per tier** (`ROOF_MIX`),
weighted so the tier still dominates: common 80% thatch / 20% tile, trade 70%
tile / 25% thatch / 5% slate, civic 70% slate / 30% tile. Dealt from a CRC of
the building id, so it is stable across rebuilds and independent of the map
seed — `boards.digest_of` compares decoded placements, and a roof that
re-deals would make every scene read STALE for nothing.

The block goes from **one roof material to three** — 16 tile, 4 thatch, 1 slate
across 21 buildings — and at eye level it reads as a street built over two
centuries rather than an estate. That is the whole of the change and it is the
cheapest variety available.

`test_a_tier_is_roofed_in_its_own_material` asserted the old exclusive
invariant and was rewritten rather than deleted: a tier must still *dominate*
its material and the three tiers must not collapse onto one, which is the
defect it was written for. A second test pins the deal's stability.

**The wall is where this stops, and it is the pack's fault rather than the
design's.** `CLAUDE.md` records why trade shares the house's wall: exactly two
1-cell windows exist in the whole Medieval Fantasy pack, so any tier that wants
glazing is built from one of two kits. With 90% of buildings on `Rural` +
`Tavern` there is no third fabric to deal. Worth knowing before anyone spends a
session trying: the constraint is the asset library, and the honest options are
to accept it, to buy a pack, or to vary something that is not material.

## 11. Still open

- **Massing.** Every building is a box with a flat facade, whatever its size.
  Breaking a large footprint into two ranges at different heights would give it
  two ridges and a step in its silhouette, and it is the strongest anti-box
  move available — but it needs per-cell storey counts, and the shell, upper
  floors and roof all read one number per building today (`storeys_of`). That
  is a real refactor, and on 7% of buildings.
- **Storeys inside the walls.** The craft block is 15 of 21 at three storeys
  and has no single-storey building at all. A real street has a low workshop
  and an outbuilding. `storeys_for` cannot see frontage, because it runs in the
  importer and frontage needs the raster — so either the decision moves to
  build time or the importer gets a cheaper proxy.
- **Yards**, still, and waiting on nothing now that fencing has landed on
  `main`: surface the clearance ring, bound it with `yard_fence`, and cluster
  the clutter by trade (§4 of the first pass).


## 12. Yards, built

§4 designed these and three successive passes deferred them. Built now, and the
gate took three attempts to get right -- which is the part worth recording.

**What a yard is:** open ground within `YARD_REACH` (2 cells, 10 ft) of a
building that stands far enough from its neighbours to own it. Surfaced by
trade -- gravel for a smithy or a warehouse, trodden earth otherwise -- and
bounded with `yard_fence` timber paling on every edge that does not face a
street, a lane or its own building. The street edge is left open: that is the
way in, and a yard sealed on four sides is a courtyard nobody can enter.

**The gate is the whole feature, and two plausible versions were wrong:**

| gate | Pelvesthollow | Forest Church | Graybank | East Tradebourne |
|---|---|---|---|---|
| none — a 2-cell apron | 100% | 100% | 100% | **100%**, 31,927 cells |
| local built density < 0.30 | 77% | 43% | 96% | 52% |
| **gap to nearest neighbour >= 3 cells** | **57%** | **29%** | **59%** | **23%** |

- **No gate** gives every building in a 991-building city a gravel apron --
  four fifths as much ground as all its paving. On a board that is a gravelled
  city, not a town with yards.
- **Local built density does not discriminate**, and this was the surprise:
  measured within 6 cells it is 0.25 median on Pelvesthollow against 0.30 on
  East Tradebourne. FTG footprints are all much the same size, so density is
  much the same everywhere.
- **The gap to the nearest *other* building does**, measured on the raster by
  one multi-source flood: a hamlet's buildings mostly stand apart and a city's
  mostly do not, which is what §4 argued from the layout polygons and what the
  artifact confirms.

Keying on the measurement rather than on the settlement band matters: an
outlying farm on a city's edge still gets its yard, and a tight terrace in a
village still does not.

**One bug this caught.** The first version tagged yard tiles with
`Builder.group = bid`. That tag exists so a building's *shell* is never split
across chunks, and tagging terrain with it made the landscape chunk claim the
building and count it in `SlabChunk.buildings` -- the number a missing
structure paste is diagnosed from.
`test_a_building_straddling_a_chunk_line_stays_in_one_chunk` caught it. A yard
is terrain and carries no group.

**Read on a board** (`--crop 104,64,40,40` on Pelvesthollow, `review.ps1 360`):
timber paling round the cottages, worked brown ground inside it against green
pasture outside, and drystone field walls running away across the fields --
three different boundary treatments doing three different jobs in one frame.
