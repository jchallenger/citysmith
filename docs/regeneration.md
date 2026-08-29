# Regenerating the towns

Seven towns: the four already published (Pelvesthollow, Graybank, East
Tradebourne, Forest Church) rebuilt against the facade work, and three new FTG
exports (Crystalstar, New Athorielgrave, Zorewyrgrave) built for the first
time.

Every number below is measured -- imported and built on 2026-08-28/29 from the
exports in `E:\Downloads`, reports kept. Nothing here is estimated. See §8 for
which numbers are already moving and why.

## 1. Why the published four are worth redoing

The four boards in `campaign/boards.json` were pasted before commit `86f37f6`
("A wall is a run in a course, and a tier deals a fabric"). That work changed
every facade on every board: measured A/B on Pelvesthollow, wall panels went
**549 -> 345, down 37.2%**, and a tier now deals a whole fabric per building
rather than one kit per tier. So a regeneration is not bookkeeping -- the
boards do not currently show the code.

Everything else re-imports almost unchanged, which is the reassuring half:
Graybank comes back at 92,636 assets against the 94,139 recorded in CLAUDE.md,
in the same 22 chunks. The drift is the facade and storey work, nothing else.

## 2. The bug this found, and it is the reason to fix before building

**Park areas are painted after the roads, with no `over=` guard, so they erase
the street network.**

`raster.rasterize` lays terrain coarse-to-fine, then roads, then plazas and
parks:

    field / marsh / water   -> painted BEFORE roads, so a road wins. Safe.
    roads                   -> paint STREET
    plaza                   -> paints PLAZA, which is itself a way. Safe.
    park                    -> paints GROUND over ANYTHING, roads included.

On a town whose park polygons are incidental this costs a few tiles. On a town
where the *base sheet* maps to `park`, it costs the entire road network.

That is exactly what the two new coastal towns do. `SAND` is not in
`ftg.BACKGROUND_AREAS`, so it falls to `DEFAULT_BACKGROUND_AREA = "park"` --
and SAND is the base sheet on both, covering the whole canvas the way GRASS
does elsewhere. Measured on a 70x70 sample grid over each export:

| export | base sheet (depth-1 samples) | depth>=2 | park areas imported |
|---|---|---|---|
| Crystalstar | **SAND** 3,536 of 3,538 | 76, all SAND + one other | 290 |
| New Athorielgrave | **SAND** 3,300 of 3,302 | 20, all CLIFF + SAND | 173 |
| Zorewyrgrave | GRASS 2,016 of 2,108 | 2,729, all GRASS + one other | 0 |

The layering invariant CLAUDE.md records still holds -- no point is more than
two backgrounds deep, and every depth-2 point is base sheet plus one thing. It
is the *name* of the base sheet that grew, which is the documented failure mode
for this format arriving somewhere nothing guarded.

What it did to the two towns, measured:

| | streets built | plaza | lane |
|---|---|---|---|
| New Athorielgrave, as-is | **0** | 0 | 3,185 |
| with the park paint guarded | **8,361** | 49 | 2,760 |

The town had 55 roads in its layout and not one tile of street on the board.
Crystalstar is the same shape: 82 roads, **23** street tiles.

**Both candidate fixes converge on the identical result.** Mapping
`SAND -> None` (drop it, as GRASS is dropped) and guarding the park paint
produce byte-for-byte the same surface counts. The guard is the one to take,
because it is one line, it does not require knowing next year's base-sheet
vocabulary in advance, and it makes the whole class impossible: no area kind
can ever erase a way again.

**It is live on a published board too.** Forest Church has 2 park polygons and
they cost it **28 street tiles** (1,203 -> 1,231 with the guard). Small, and it
has been on the board since the first paste.

### 2.1 Why nothing caught it

This is the eighth entry for CLAUDE.md's "metrics must read the artifact" list,
and it is the sharpest one yet: **every check passed on a town with no roads.**

    [ok  ] connectivity: one connected town covering 100% of walkable space
    [ok  ] building access: 248 of 248 buildings (100.0%) can be entered

Access passed because `_lay_lanes` floods a lane out from every doorway over
open GROUND, and with the streets gone there was nothing *but* open ground --
so the town was perfectly connected, by footpaths. `street width` and `vehicle
width` printed nothing at all, because a check with no street tiles to measure
has nothing to say and says it silently.

`verify.feature_report` is the check built for precisely this -- "offered and
not built -> FAIL" -- and it covers field walls, marsh, yards, quarters and
surfaces. **It does not cover roads.** A `roads` entry would have read "the
layout offers 55 roads; 0 street tiles were built" and failed the build.

## 3. What each town measures

Built with `--by-region`, chunk size tuned per town (§4).

| town | src | tiles | buildings | assets | chunks | largest slab | build |
|---|---|---|---|---|---|---|---|
| Pelvesthollow | FTG | 176x184 | 35 | 21,922 | 9 | 14,415 (47%) | 10 s |
| Forest Church | MFCG | 187x180 | 51 | 28,603 | 9 | 23,697 (77%) | 15 s |
| New Athorielgrave | FTG | 339x375 | 248 | 81,445 | 22 | 24,723 (80%) | 40 s |
| Graybank | FTG | 434x306 | 150 | 92,636 | 22 | 24,171 (79%) | 50 s |
| Crystalstar | FTG | 663x456 | 323 | 179,919 | 65 | 23,526 (77%) | 2 min |
| East Tradebourne | FTG | 739x598 | 991 | 393,877 | 114 | 23,964 (78%) | 4 min |
| Zorewyrgrave | FTG | 966x834 | 2,822 | 592,644 | 218 | 22,260 (72%) | **7 min** |

The two coastal towns' figures are pre-fix and will rise once they have roads.

Zorewyrgrave is the largest thing this project has built: 2.85x East
Tradebourne's buildings, 1.5x its assets, **59.3% of the 1,000,000 per-board
asset cap**. It fits, with room, and it carries a real circuit -- 1 wall ring,
5 gates, 540 buildings inside the walls, 2 market squares correctly diverted to
plazas by the PAVEMENT material rule.

### 3.1 Vocabulary the exports grew

Reported through `Layout.unmapped`, never dropped, exactly as designed:

| value | where | count | lands on | verdict |
|---|---|---|---|---|
| `SAND` | Crystalstar, New Athorielgrave | 297 / 177 | park | **§2, blocking** |
| `PEBBLE_BEACH` | both coastal | 3 / 5 | park | map it |
| `CLIFF` | New Athorielgrave | 2 | park | map it |
| `SANDSTONE` | Crystalstar | **322 of 323** | not stone | §5 |
| `LIGHT_GRASS` | Zorewyrgrave | 1 | park | map to lawn |
| `CULTURAL` | Zorewyrgrave | 1 | house | map to guildhall |
| `MARKET` | Zorewyrgrave | 2 | (diverted) | **already correct** |
| `WOOD` | Zorewyrgrave | 7 | not stone | correct |

`MARKET` is worth reading twice: the buildingType is unmapped, and the two
market squares still became plazas rather than roofed boxes, because the
diversion tests the *material*. That is CLAUDE.md's "the material is the test;
buildingType is only corroboration" holding up on the first export to disagree
with it.

## 4. Chunk sizing is the paste budget

Paste count is the human cost of this whole exercise: `review.ps1 tiled` sleeps
9 seconds per chunk (hold 3, commit 4, clear 2) before overhead, so a chunk is
~10-12 s of driving.

Chunk size is the lever, and the default is far too small. Swept on
Pelvesthollow, same map:

| `--chunk-tiles` | chunks | largest slab | verdict |
|---|---|---|---|
| 24 (default) | **49** | 3,399 (11%) | eight minutes of pasting for a hamlet |
| 48 | 15 | 9,177 (30%) | |
| **64** | **9** | 14,415 (47%) | the pick |
| 88 | 6 | 25,410 (83%) | over the two-thirds rule |
| 112 | *build fails* | 32,722 (107%) | busts the cap |

The 112 failure is CLAUDE.md's documented one, firing exactly as described: a
chunk reached 8,646 assets under a 9,000 budget and compressed past the limit.
`--max-assets` is the lever there, not `--chunk-tiles`.

**Rule: raise `--chunk-tiles` until the largest slab is near two thirds of the
30,720-byte cap, and lower `--max-assets` if a chunk busts.** Re-check after
any change that adds dressing.

Paste budget at the sizes in §3: **459 chunks, roughly 80-95 minutes** of
driven pasting across all seven, plus board creation and naming.

## 5. Sand is a biome, and the kit is already there

Once §2 is fixed the coastal towns are *correct* but green -- a beach with pine
trees on it. Crystalstar is 322 of 323 buildings in `SANDSTONE` and its ground
is sand from edge to edge; it is a desert town and should read as one.

**The catalog already ships the family, at the right shapes.** The `desert`
group has `Desert Ground 01` in **both 1.0x0.5x1.0 and 2.0x0.5x2.0** -- exactly
the `ground` / `ground_2x2` pair the terrain pass wants -- plus `Desert Ground
Dry 01` in both, and four 2x2 `Desert Ground Road` tiles. All 0.5 tall, the
same course as grass, so they lay through the ordinary terrain pass with no
special casing. This is a palette swap, not new geometry.

What stands in the way is one constant. `palette._WRONG_SETTING` excludes
`"desert"` and `"sand"` by name from every village-appropriate role, and its
comment records why: it is what stopped Candlewell being paved in desert tiles.
That exclusion is right, and it is currently *global* where it needs to be
conditional on the town. A biome carried on the `Layout` -- set at import from
whichever background is the base sheet -- is the shape that fits: `raster`
initialises `surface` from it instead of hardcoding `GROUND` at line 156, and
the palette resolves the ground roles against it.

**New Athorielgrave is a separate question and must not be assumed.** It is
`STONE_BRICK` with `CLIFF` and `PEBBLE_BEACH` -- a northern cliff coast, not a
desert. Sand on the strand, stone buildings, and green verges may well be right
for it. Decide it on a board, not here.

## 6. The order of work

Nothing is pasted until the code is right, because a paste is the expensive
step and 459 of them twice is the failure this ordering exists to avoid.

1. **Guard the park paint** (§2). One line in `raster.rasterize`, plus a test
   that a park polygon laid over a road leaves the road. Fixes both coastal
   towns and recovers 28 tiles on Forest Church.
2. **Add `roads` to `verify.feature_report`** (§2.1). Offered-and-not-built has
   to fail. This is the check that should have caught §2, and it is the only
   reason to trust the next six builds.
3. **Map the grown vocabulary** (§3.1): `PEBBLE_BEACH`, `CLIFF`, `LIGHT_GRASS`,
   `CULTURAL`. Report-not-raise stays.
4. **Settle `facade-fabric-weighting` before pasting anything.** It is open,
   and its own note says the 6:1 poor-fabric deal "has never been seen" -- it
   was landed on an A/B of counts. Correlate it with something the map already
   knows (storey count, distance from centre, `quarters.py`) rather than with a
   hash of the building id, and read it on a small board. Seven towns pasted
   against a weighting that reads as scattered damage is seven towns pasted
   twice.
5. **The sand biome** (§5), for Crystalstar.
6. **Rebuild all seven**, tuning `--chunk-tiles` per §4.
7. **Paste smallest first.** Pelvesthollow and Forest Church are 9 chunks each
   and are the cheapest place to discover that something is wrong.
8. **Zorewyrgrave last, in batches.** 218 chunks is the longest unattended
   drive this project has attempted; checkpoint between batches so a stall
   costs a batch and not the run. Its walled core (540 buildings) is the
   playable board and is worth reading first.

Every board is named and indexed at paste time by the driver itself
(`review.ps1 tiled -Board`), because that is the only moment anything knows
what is on it.

## 7. Known findings carried into this, not fixed by it

- **Props overlap on every town**, ~0.1% and scaling with size: 2 of 3,719 on
  Pelvesthollow, 13 of 13,543 on Graybank, 46 of 40,080 on East Tradebourne,
  97 of 79,692 on Zorewyrgrave. TaleSpire drops these silently, so they are
  missing from the board. It is a FAIL and the build still writes its slabs; at
  a tenth of a percent it is not what blocks a regeneration, but it is on every
  town and nobody has looked at what the overlapping pairs *are*.
- **Tile seams** scale the same way -- 30,190 pairs on Zorewyrgrave. Warns.
- **A `--by-region` build exits non-zero on a FAIL and still writes every
  slab.** Worth knowing before wrapping any of this in a script that checks the
  exit code.

## 8. These numbers have a shelf life

Measured against `7740c67` plus a working tree that **changed while the
measurements were being taken**. A concurrent session landed the frontage
storey work (`build.storeys_by_frontage`, `docs/building-massing.md`) partway
through, and it moved the skyline:

| Graybank | before | after |
|---|---|---|
| storeys | `1:80 2:68 3:2`, mean 1.48 | `1:109 2:40 3:1`, mean **1.28** |
| assets | 93,394 | 92,636 |
| chunks | 22 | 22 |

So: **chunk counts and slab sizes held, storeys moved materially, assets
drifted under 1%.** The paste budget in §4 is therefore sound, and the storey
figures anywhere in this document are not.

The practical consequence for §6: take the snapshot *after* the concurrent work
settles, and re-run the §3 table as step 6 rather than trusting it. A build is
cheap -- ten seconds to seven minutes -- and it is the paste that is expensive.
