# Roads, paths and terrain by district

A design pass over what a town is surfaced with, and whether "district" is a
thing citysmith can key that on. Measured on all four towns, and the materials
read off a board rather than out of the catalog.

Nothing here is built yet. The companion pass on building massing is
`docs/building-massing.md`, and the two share a spine — see §7.

## 1. The vocabulary is thinner than the map

The raster distinguishes six surfaces (`GROUND`, `FIELD`, `STREET`, `PLAZA`,
`LANE`, `PIER`) and classes every road `main`, `cart` or `lane`. All of that
arrives on the board as **three materials**:

| raster says | role | asset |
|---|---|---|
| `STREET`, `PLAZA` | `street` | `CobbleStone Floor Small` |
| `LANE` | `lane` | `gravel_1x1_01` |
| `FIELD` leftovers | `field_1x1` | `gravel_1x1_01` |
| `GROUND` | `ground` | `Grass 1x1` |

Two things follow, and neither needs any district data to fix.

- **`lane`, `gravel` and `field_1x1` are the same asset.** A back lane and the
  ragged edge of a ploughed field are built from one tile. So are `gravel` and
  `lane`, which is why there is no visible difference between a track and a
  yard anywhere on any of these maps.
- **`plaza` resolves to nothing at all.** The role exists in the palette and
  returns `None`; `build_from_tilemap` maps `R.PLAZA` to `"street"`. A market
  square is paved identically to the road that runs past it — on East
  Tradebourne that is 631 cells of square that could be saying "this is where
  the town gathers" and is instead saying "road".

Against that, the palette *already* has `floor_civic` (`castle floor 1x1`)
sitting unused outdoors, and the catalog has plenty more (§3).

## 2. Authored districts are unusable, and it is worse than the old note says

`CLAUDE.md` records that ward membership is a no-op because "47 of 51 buildings
fall in a single ward" on Forest Church. Re-measured across all four towns, the
finding holds and extends:

| town | source | district polygons | buildings with a district |
|---|---|---|---|
| Pelvesthollow | ftg | 0 | 0 of 35 |
| Graybank | ftg | 0 | 0 of 150 |
| East Tradebourne | ftg | 0 | 0 of 991 |
| Forest Church | mfcg | 4 | 49 of 51, **47 in one ward** |

**FTG carries no district data whatsoever** — and FTG is now the primary path.
Forest Church's one populated ward is called `Market Ward 1` and contains the
temple, the barracks, both stables, both manors, three warehouses and 25
houses. It is not a market ward; it is the town.

So a district axis has to be *derived*, and the next question is whether there
is anything in the geometry to derive it from.

## 3. Quarters are real, and only on the big town

Buildings of a kind either cluster or they do not, and it is measurable:
take each building's eight nearest neighbours and ask how often they share its
kind, against the rate you would get from the same mix shuffled.

| town | all kinds | non-house only |
|---|---|---|
| Forest Church | 1.06x | 0.97x |
| Graybank | 1.01x | 0.86x |
| East Tradebourne | **1.36x** | **1.27x** |

Broken out per kind on East Tradebourne, and with the clumps single-linked at
100 ft so the *shape* of the clustering shows:

| kind | n | lift | clumps | biggest clumps |
|---|---|---|---|---|
| house | 709 | 1.22x | 16 | 338, 143, 97, 92 |
| smithy | 171 | **3.37x** | 33 | **55, 47**, 25, 5 |
| shop | 67 | **4.34x** | 18 | **21, 17**, 7, 3 |
| tavern | 14 | 4.08x | 10 | 2, 2, 2, 2 |
| warehouse | 18 | 1.62x | 17 | 2, 1, 1, 1 |

**Smithies form clumps of 55 and 47. Shops form clumps of 21 and 17.** Those
are a craft quarter and a market street, and they are in the authored export,
not invented. Houses form one mass of 338 plus three more of 100-odd, which is
what residential quarters look like.

On Graybank and Forest Church the same measurement returns noise — the biggest
shop clump in either town is **two**. Forest Church's kinds are invented by
quota (`mfcg.py` allocates scarce kinds), so they are random by construction;
Graybank's are authored and simply are not clustered, because 150 buildings is
not enough town to have a quarter in.

Labelling every paved cell by the dominant trade within 14 tiles, weighted by
distance, gives the share of the board a quarter scheme would actually repaint:

| town | residential | craft | market | civic | docks | outskirts |
|---|---|---|---|---|---|---|
| Forest Church | 50% | 10% | 13% | 2% | 5% | 19% |
| Graybank | 83% | 1% | 8% | 1% | — | 7% |
| East Tradebourne | 70% | **16%** | **9%** | 1% | 1% | 4% |

East Tradebourne's craft and market quarters are 16% and 9% of the paving —
big enough to see from the air and to walk through. Graybank's craft quarter is
**1%**, which is a handful of scattered cells and would read as dirt.

**So: the district axis is earned by East Tradebourne and by nothing else
here.** That is not a reason to drop it. It is a reason to make it *measure*
rather than assume — see §5.

## 4. What the materials actually look like

Read off a board, not out of the catalog: `tools/surface_probe.py` lays a 6x6
pad of every candidate into a grass field, each labelled by a tally bar, and
`review.ps1 360` walks it. Fourteen candidates, from four faces, plan, eye and
section.

| material | reads as | fit |
|---|---|---|
| `CobbleStone Floor Small` | dark charcoal setts, tight and irregular | the through street — the current pick is right |
| `Castle Ruins floor stone 1x1` | grey-brown coursed flag, weathered | an older, humbler paving |
| `Castle Ruins Stone Floor 2` | as above, browner | pairs with it |
| `castle floor 1x1` | cream regular flagstone, visibly *dressed* | civic ground, a temple forecourt |
| `Desert stone floor 01` | very pale, near-white grid | too clean for a working town |
| `Moorgoth Floor 01`, `md_floor_1x1_01` | dark grey slab | reads indoor |
| `gravel_1x1_01` | **bright orange-tan coarse pebbles** | a yard or a track, and loud |
| `Desert Ground Dry 01` | pale sandy, wind-rippled | sand, not trodden earth |
| `Swamp floor 1x1` | dark wet brown | mud — the poor lane, the shambles |
| `Ship floor 1x1` | warm red-brown planking | a quay, a jetty |
| `Rural Floor 01` | four-leaf motif in a frame | **indoor floor; wrong outdoors** |
| `Grass 1x1` | the base | — |

Two things the board settled that the numbers could not:

- **The top-align rule holds across the whole set.** Cobble is 0.25 thick and
  everything else is 0.50, and from a low oblique every pad sits flush in the
  grass with no lip and no kerb. `Builder.surface()` is doing its job, and
  mixing thicknesses across districts costs nothing geometrically.
- **`gravel_1x1_01` is much more orange than it reads in a name.** It is doing
  three jobs (§1) and it is conspicuous in all of them. A lane surfaced in it
  looks like a gravel yard, which is part of why lanes do not currently read as
  lanes.

**A probe bug worth recording, because it cost a run.** The first version laid
grass over the whole board and then set each pad on top at the same top height,
so pad and sod occupied the identical cell volume. Half the pads came back as a
dither of both textures — scattered chips of stone in grass — which looks
exactly like a pad that failed to lay. Tiles are not props, so nothing was
dropped; they simply z-fight. **Lay one tile per cell.** The fixed probe reads
clean.

## 5. The design: two tiers, and the second one measures before it fires

**Tier 1 — give the distinctions the map already makes their own material.**
No district data, works on a 35-building hamlet, and it is most of the win:

| surface | today | proposed |
|---|---|---|
| `STREET` class `main` | cobble | cobble (unchanged) |
| `STREET` class `cart` | cobble | weathered flag (`Castle Ruins floor stone 1x1`) |
| `LANE` | gravel | trodden earth |
| `PLAZA` | cobble | dressed flag (`castle floor 1x1`) |
| `PIER` | (laid by name) | planking (unchanged) |
| `FIELD` leftover | gravel | its own tile, not the lane's |

That alone separates the market square from the road, the back lane from the
high street, and the field edge from the track — six readings where there are
now three.

**Tier 2 — a quarter repaints the lanes and yards inside it, never the through
roads.** A main road runs *between* quarters and belongs to the town; the
surfaces that say which quarter you are in are the small ones. So a quarter
overrides `LANE` and open ground, and leaves `main` alone:

| quarter | lane surface | reading |
|---|---|---|
| craft | gravel (its loudness is right here) | forge yards, spoil, hard standing |
| market | dressed flag | swept, walked, public |
| civic | dressed flag | temple and hall forecourts |
| docks | planking / gravel | working waterfront |
| residential | trodden earth | the default |
| poor | mud | where a town stops maintaining itself |

**And it fires only where it is real.** The clustering lift in §3 is a cheap
measurement — eight nearest neighbours, one pass over the buildings — so the
build computes it and only keys surfaces on quarters when the town's non-house
lift clears a threshold (1.2x is comfortably above Graybank's 0.86x and below
East Tradebourne's 1.27x). Below it, tier 1 alone. A village gets one honest
palette instead of six quarters that are each three cells wide.

That is the same shape as the fencing pass's rule about caps: **do not add a
knob, add a measurement**, and let the map tell you whether the feature applies
to it.

## 6. Verification

Per the project rule that a metric reads the artifact:

- **`surfaces_are_flush`** — no two adjacent ground placements differ in top
  height. Guards the top-align rule across a wider material set than it has
  ever carried, and it is the check that would have caught the 15-inch kerb.
- **`one_tile_per_cell`** — no cell holds two ground tiles. This is the probe
  bug from §4 promoted to a check; with six materials keyed on overlapping
  conditions it becomes easy to lay two.
- **`a_quarter_is_worth_painting`** — report each quarter's share of paved
  cells, and warn under about 3%. A quarter nobody can find is worse than no
  quarter, because it reads as dirt rather than as design.

## 7. What this shares with the buildings pass

The two passes met the same wall from opposite sides, and it is worth stating
once:

> **Settlement size decides which axis exists.** East Tradebourne has real
> quarters (§3) and no room between its buildings — 94% of them touch a
> neighbour. Pelvesthollow has no quarters at all and 13 ft of median gap, with
> 14% of buildings standing in more than 40 ft of clear ground.
>
> A city varies by **district**. A hamlet varies by **building**. Neither axis
> is available on the other, and a design that assumes one everywhere produces
> either invisible quarters or identical cottages.

`docs/building-massing.md` is the other half.

## 8. Plan

1. **Tier 1 roles and the palette.** `plaza`, `lane_earth`, `field_edge`,
   `street_cart`, pinned per §4 with the board reading in the comment.
2. **Surface selection.** `build_from_tilemap`'s `surface_roles` becomes a
   function of surface *and* street class, not a flat dict.
3. **The clustering measurement.** `sites` or a new `quarters.py`: per-kind
   lift, single-link clumps, a cell-level quarter label, and the threshold
   that decides whether any of it is used.
4. **Tier 2 override**, lanes and open ground only.
5. **The three checks in §6.**
6. **Review.** `surface_probe.py` is done and read. Then a 48x48 section of
   East Tradebourne's craft quarter against the same section built flat, both
   through `review.ps1 360`, judged at eye level — because the question "does
   this read as a different part of town" is not answerable from plan.
