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
