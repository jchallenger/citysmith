# Fantasy Town Generator import — format reference and plan

Status, 2026-08-30: **stages 1-4 and 6 are done — all three towns are on
boards — and stage 5 is built.** `citysmith/importers.py` sniffs the format,
`citysmith/ftg.py` reads it, and `citysmith import` dispatches between the two
readers. The stage-5 extras (fences, town wall, authored bridges,
forest-driven tree density, trails) are all constructed and under test; what
remains of stage 5 is the in-game orbit of the three newest (§6). §2 is the
format reference and is the durable part of this file; §6 tracks the work.

Companion docs: `CLAUDE.md` (engineering notes), `docs/asset-conventions.md`
(footprints and roles), `docs/pasting-into-talespire.md` (the paste procedure
every size estimate here is constrained by).

```bash
python -m citysmith --out-dir out/pelves import "E:/Downloads/Pelvesthollow.geojson"
```

```bash
python -m citysmith --out-dir out/pelves build out/pelves/layout.json --stem pelves --seed 33 --by-region --chunk-tiles 64 --keep-open-country
```

```bash
.\tools\review.ps1 tiled -Name pelves -Stem pelves -OutDir out\pelves
```

## 1. What arrived

Two new exports, plus one already sitting in `E:/Downloads` from an earlier
session that turns out to be the same format:

| File | Features | Buildings | Edges | Backgrounds | Water | Canvas |
|---|---|---|---|---|---|---|
| `Pelvesthollow.geojson` | 827 | 41 | 676 | 108 | 2 | 1500 x 1500 |
| `Graybank.geojson` | 1867 | 159 | 1463 | 241 | 4 | 2000 x 2000 |
| `East Tradebourne.geojson` | 5543 | 1007 | 3770 | 761 | 5 | 4000 x 4000 |

They are **not** MFCG. They are [Fantasy Town Generator](https://docs.fantasytowngenerator.com/useSettlements/export/)
(FTG) GeoJSON exports — confirmed from FTG's own export docs, which describe a
GeoJSON containing all the backgrounds, edges, buildings and their outlines as
coordinates, and state the coordinate system is FTG-internal with **1 unit =
1 metre**. FTG does not publish the property schema; section 2 is reverse
engineered from the three files and is the spec until something better exists.

Pelvesthollow is a hamlet (41 buildings, all wood, residences and farms).
Graybank is a village (159 buildings, ten types, one stone building).
East Tradebourne is a town (1007 buildings, fourteen types, a partial town wall,
five bridges, a market square) and is the stress case for everything below.

## 2. The format

### 2.1 Telling the two formats apart

**The file extension is not a discriminator and must not be used as one.** All
four combinations exist on this machine already:

| File | Extension | Format |
|---|---|---|
| `samples/forest_church.json` | `.json` | MFCG |
| `E:/Downloads/candlewell_church.json` | `.json` | MFCG |
| `E:/Downloads/East Tradebourne.geojson` | `.geojson` | FTG |
| `E:/Downloads/Pelvesthollow.geojson` | `.geojson` | FTG |

Both are `FeatureCollection`s. Sniff the first feature instead:

- **MFCG** — features carry a top-level `"id"` string from a closed vocabulary
  (`values`, `earth`, `buildings`, `walls`, `roads`, ...) and have no
  `properties`.
- **FTG** — features have no top-level `id`; they carry
  `properties.type` in `{BUILDING, EDGE, BACKGROUND, WATER}` and a
  `properties.id` integer.

Neither can be mistaken for the other, and the check is one dict lookup.

### 2.2 Feature schema

```
Feature.geometry : Polygon (single ring, closed) | LineString (exactly 2 points)
Feature.properties:
  id            int, 1..N, unique *per type*, NOT globally unique
  type          BUILDING | EDGE | BACKGROUND | WATER

  BUILDING      name           str, authored ("The Halfling and the Fox")
                buildingType   enum, see below
                material       WOOD | STONE_BRICK | PAVEMENT
  EDGE          edgeType       enum, see below
  BACKGROUND    backgroundType enum, see below
                raised         bool
  WATER         (no further properties)
```

There is **no `values`/metadata feature** — no version, no settlement name, no
road width, no wall thickness. MFCG gave all of those. The settlement name is
only in the filename, and every width is ours to choose (§3.3).

### 2.3 Vocabularies (union over the three files)

`buildingType` — RESIDENCE, SHOP, ARTISAN, FARM, INDUSTRIAL, TAVERN, INN,
SERVICE, WAREHOUSE, RELIGIOUS, EDUCATIONAL, LAW_ENFORCEMENT, FACTION, MARKET.

`edgeType` — INVISIBLE, WATERFRONT, BORDER, STONE_FENCE, STONE_WALL,
MAIN_ROAD, ROAD, SMALL_ROAD, DIRT_ROAD, TRAIL.

`backgroundType` — GRASS, LAWN_TEXTURE_TYPE, FOREST, WHEAT, GRAIN, TILLED,
SHEEP_TEXTURE_TYPE, PIGS_TEXTURE_TYPE, CATTLE_TEXTURE_TYPE, ROAD_TEXTURE_TYPE.

The vocabulary **grew between files**: `STONE_WALL`, `ROAD_TEXTURE_TYPE`,
`PAVEMENT`, `raised: true`, and five building types appear only in East
Tradebourne. Assume more exist. The importer must map an unknown enum value to a
safe default **and report it by name**, never raise and never silently drop —
a dropped feature is invisible on the board, and that is the failure mode
`CLAUDE.md` records four separate times.

### 2.4 Measured invariants

Everything here was measured across all three files, not assumed:

- Every Polygon has exactly **one ring**, and it is **closed** (first point ==
  last). No holes, no MultiPolygon. MFCG's unclosed-ring and
  inconsistent-nesting workarounds are not needed.
- Every EDGE is a **2-point segment**, not a polyline. Roads must be chained
  into paths through shared endpoints before they can be given a width.
- The EDGE layer is the **boundary graph of the BACKGROUND polygons**: 93–96%
  of edge endpoints are also background-ring vertices. `INVISIBLE` is a parcel
  boundary that is simply not drawn — it is the largest or second largest class
  in every file (351/676, 613/1463, 1324/3770) and is not geometry we want.
- Vertices are shared exactly, so chaining can key on the raw coordinate pair;
  no snapping tolerance is needed. Road-only vertex degree is overwhelmingly 2.
- **BACKGROUND polygons overlap, but never more than two deep.** A 60x60 sample
  grid over each canvas: depth is 0, 1 or 2 at every point, and every depth-2
  combination is `GRASS` plus one other. GRASS is the base sheet (92–94% of
  canvas); FOREST, the field textures and LAWN sit on it, one at a time.
  So there is **no z-order to resolve**: paint GRASS, then let anything else
  win. (File order groups by type and is *not* a reliable draw order — in
  Pelvesthollow FOREST is listed last, in Graybank LAWN is listed first, and
  both need to be on top. Do not use it.)
- Coordinates are continuous floats; there is no lattice to snap to.
- Buildings are mostly rotated quads (883/1007 four-cornered in East
  Tradebourne), occasionally 5–8, once 14.
- `raised: true` occurs on **exactly five features across all three files**, and
  every one is a ~20x20 m `ROAD_TEXTURE_TYPE` quad. Those are the **bridges**.
  Nothing else is ever raised.
- `STONE_WALL` in East Tradebourne is 47 segments / 49 vertices with degree
  histogram `{1: 4, 2: 45}` — **two open polylines**, 1220 m total, not a closed
  circuit. `Layout.walls` currently holds closed rings.

### 2.5 Scale — and a pleasing cross-check

FTG's docs give 1 unit = 1 m, so `feet_per_unit = 3.28084` and
`units_per_tile = 5 / 3.28084 = 1.524`.

Independently, running `resolve_scale`'s median-house-frontage anchor at the
CLI's `--house-ft 35` default over these files gives **1.49, 1.47 and 1.53**
units per tile. The declared metric scale and citysmith's playability anchor
agree to within 4%, which is worth stating plainly: an FTG house has a median
short side of 10.3–10.7 m = **34–35 ft**, and citysmith's 35 ft anchor was
chosen for play, with no knowledge of FTG. They landed on the same number from
opposite directions.

**So FTG imports should default to the native metric scale**
(`feet_per_unit = 3.280839895`), not to a derived anchor. `--house-ft` and
`--feet-per-unit` stay available as overrides. This is the first import path in
the project with a documented real-world scale; take it.

## 3. Mapping onto `Layout`

### 3.1 What lands where

| FTG | citysmith |
|---|---|
| `BUILDING` polygon | `LayoutBuilding.ring` |
| `buildingType` | `LayoutBuilding.kind` via table (§3.2) |
| `name` | **new** `LayoutBuilding.name` field |
| `material: STONE_BRICK` | select the existing `wall_civic` / `door_civic` / `wall_corner_civic` roles |
| `WATER` polygon | `LayoutArea("water")` |
| `BACKGROUND: FOREST` | `LayoutArea("forest")` → the `TileMap.forest` mask the tree scatter reads |
| `BACKGROUND: WHEAT/GRAIN/TILLED` | `LayoutArea("field")` |
| `BACKGROUND: SHEEP/PIGS/CATTLE` | `LayoutArea("pasture")` — grass underfoot; nothing reads it past the SVG yet |
| `BACKGROUND: LAWN` | `LayoutArea("lawn")` — same |
| `BACKGROUND: GRASS` | nothing — it is the base sheet `raster.py` already lays |
| `BACKGROUND: ROAD_TEXTURE_TYPE`, `raised: false` | `LayoutArea("plaza")` |
| `BACKGROUND: ROAD_TEXTURE_TYPE`, `raised: true` | bridge deck — `bridge_deck` role, already pinned to `Harbor Middle 06` |
| `EDGE: MAIN_ROAD/ROAD/SMALL_ROAD/DIRT_ROAD` | `LayoutRoad(kind="road")`, chained, widths per §3.3 |
| `EDGE: TRAIL` | `LayoutRoad(kind="trail")` — **new kind**, see §3.3 |
| `EDGE: STONE_WALL` | `Layout.walls`, but as **open** polylines |
| `EDGE: STONE_FENCE` | new `field_wall` role (drystone), see §4 |
| `EDGE: WATERFRONT` | shoreline; redundant with the water polygons, ignore at first |
| `EDGE: BORDER` | canvas edge, ignore |
| `EDGE: INVISIBLE` | ignore |
| *(nothing)* | `Layout.districts` — FTG has no wards |
| *(nothing)* | `Layout.gates` — no gate is marked |
| *(nothing)* | trees — FOREST is a texture polygon, so trees must be scattered |

### 3.2 Building kinds

The big win of this format: **types and names are authored, not guessed.**
`mfcg.py` currently invents wards from radial bands (`_BANDS`) and then rolls a
weighted kind per ward (`_DISTRICT_BUILDINGS`). For FTG none of that runs — the
export says what each building is, and gives it a name. Proposed table:

```
RESIDENCE       -> house       SHOP        -> shop      TAVERN  -> tavern
INN             -> tavern      ARTISAN     -> smithy    SERVICE -> shop
INDUSTRIAL      -> warehouse   WAREHOUSE   -> warehouse FARM    -> stable
RELIGIOUS       -> temple      EDUCATIONAL -> guildhall FACTION -> guildhall
LAW_ENFORCEMENT -> barracks
MARKET          -> NOT A BUILDING (see below)
unknown         -> house, and print the unmapped value
```

**Trap: `MARKET` + `material: PAVEMENT` is a plaza, not a building.** East
Tradebourne's "Warden Market" is a 1350 m² polygon typed `BUILDING`. Built as a
building shell it becomes a roofed box over the market square. It has to become
`LayoutArea("plaza")`. The discriminator is `material == "PAVEMENT"`; treat
`buildingType == "MARKET"` as corroboration, not as the test, since the
vocabulary grows.

Downstream, `sites.py` scores by kind and should keep doing so — but the
authored `name` should ride along into the site report and the brief, because
"The Halfling and the Fox" is a better encounter hook than "tavern-0042".

### 3.3 Widths, which we now have to invent

FTG ships no `roadWidth`. Proposed, in metres, with the tile count at 1.524
units per tile:

| edgeType | width | tiles |
|---|---|---|
| `MAIN_ROAD` | 6.0 m | 3.9 |
| `ROAD` | 4.5 m | 3.0 |
| `SMALL_ROAD` | 3.0 m | 2.0 |
| `DIRT_ROAD` | 3.0 m | 2.0 |
| `TRAIL` | 1.5 m | 1.0 |

`check_playability` warns when the widest road is under `MIN_ROAD_TILES` (2.0).
A 1-tile trail is a footpath and is *correct* at one tile — so it must be a
separate `kind` the check skips, not a narrow road that trips it.

## 4. The size problem, which is the real one

The whole canvas is fields. Cropping is not a nicety here; it is the difference
between a board and an impossibility. Forest Church is the yardstick: 186.5 x
179.1 tiles = 33,411 tiles, emitted as 9 tiled chunks — about 3,700 tiles per
chunk against the 30,720-byte cap.

| Export | Crop | Buildings | Metres | Tiles | Total | ≈ chunks |
|---|---|---|---|---|---|---|
| Pelvesthollow | all | 41/41 | 352 x 320 | 231 x 210 | 48,500 | 13 |
| Pelvesthollow | **core** | 35/41 | 217 x 230 | 142 x 151 | 21,500 | **6** |
| Graybank | all | 159/159 | 1299 x 1544 | 853 x 1013 | 864,000 | 233 |
| Graybank | **core** | 150/159 | 610 x 415 | 400 x 272 | 109,000 | **29** |
| East Tradebourne | all | 1007/1007 | 1160 x 1991 | 761 x 1307 | 995,000 | 268 |
| East Tradebourne | **core** | 671/1007 | 1072 x 857 | 704 x 563 | 396,000 | **107** |

"core" is single-link clustering of building centroids at 60 m. The outliers are
a handful of isolated farms that drag the bounding box across the whole canvas —
Graybank's 9 stragglers cost 755,000 tiles.

`mfcg.import_layout` crops to `walls ∪ buildings + margin`. On FTG that is the
"all" row: correct by its own logic, catastrophic in practice. **The clip window
has to come from the settled core, not the building bounding box.**

The fences make the same argument twice. Total `STONE_FENCE` run:

| Export | Whole canvas | Inside the core crop |
|---|---|---|
| Pelvesthollow | 1,347 tiles | — |
| Graybank | 18,728 tiles | 1,042 (6%) |
| East Tradebourne | 92,638 tiles | 6,931 (7%) |

East Tradebourne's field walls alone are **three times Forest Church's entire
board**. The core crop removes 93–94% of that, which is the whole answer, but
`field_wall` still deserves a cap and a `--no-fences` escape.

Even cropped, Graybank at 29 chunks and East Tradebourne at 107 are far past
Forest Church's 9, and every chunk is a hand-driven paste
(`docs/pasting-into-talespire.md`). Pelvesthollow at 6 chunks is comfortable and
should be the first target.

## 5. Decisions

2, 3 and 4 are settled and built; 1 is still open and only gates stage 6.

1. **How much town is one board? — SETTLED: the whole core, one board.** The
   answer turned out to be option (c), and it is cheaper than the estimate
   feared. East Tradebourne's core is 114 chunks, not the ~107-and-rising the
   planning arithmetic suggested, because `--max-assets` merges open country
   that a per-cell estimate counted separately. At ~20 s a chunk that is a
   38-minute unattended paste — long, but it is one command and it does not
   need watching. `review.ps1 tiled -ShotEvery N` thins the screenshots so a
   hundred-chunk town does not take two hundred grabs. No crop radius and no
   sub-crops were needed; if a town ever does need them, `--cluster-gap-ft`
   already tightens the core.
2. **Where FTG lives — SETTLED.** `citysmith/ftg.py` beside `citysmith/mfcg.py`,
   with `citysmith/importers.py` holding the sniffer and the dispatcher.
   `Layout` is the common currency; `mfcg.py` was not touched except to record
   its scale anchor and to align its house-frontage default with the CLI's (the
   module said 20 ft and the CLI passed 35, so the module default was a
   constant nothing used).
3. **`import` sniffs — SETTLED.** `citysmith import` reads the file to decide,
   with `--format mfcg|ftg` to override. A wrong override is loud: forcing
   `--format ftg` on an MFCG file reports that it has no BUILDING features.
4. **New area kinds — SETTLED.** `forest`, `pasture` and `lawn` are recorded
   in the layout and drawn on the reference SVG. The rasteriser leaves all
   three as ground, which is *correct* — they are grass underfoot. `forest`
   now also lands in `TileMap.forest`, the mask the tree scatter reads (stage
   5, §6); `pasture` and `lawn` are still carried for nothing past the SVG.

## 6. Staged work

Each stage ends with something measurable, in the project's own idiom — measure
the artifact, not the plan.

**Stage 1 — sniff and dispatch. DONE.** `citysmith/importers.py` with
`detect_format`, `classify` and a dispatching `import_layout`.
*Accepted:* all four files on this machine classify correctly, the same bytes
classify the same way under either extension, and a `FeatureCollection` of
`LAMPPOST` features errors with "LAMPPOST" in the message.

**Stage 2 — geometry in. DONE.** `citysmith/ftg.py`: buildings, water,
backgrounds, chained roads, metric scale, core-cluster crop.
*Accepted:* Pelvesthollow imports as 175x184 tiles, 35 buildings, 19 chained
roads, `check_playability` clean. Its `layout.svg` was put beside a direct plot
of the raw GeoJSON and is the same village — same tongue of clearing, same
street pattern, same buildings, river in the same place.

**Stage 3 — types and names. DONE.** The §3.2 table, `LayoutBuilding.name` and
`.stone`, the PAVEMENT/plaza rule, unknown-value reporting through
`Layout.unmapped`.
*Accepted:* all 35 Pelvesthollow buildings keep their authored names, nothing
is unmapped, and a synthetic `MARKET`/`PAVEMENT` building becomes a plaza with
no building of that name. `sites.py` runs on a `City`, not a `Layout`, so
ranking Graybank's inn by name is stage 6 work, not a stage 3 gap.

**Stage 4 — build it. DONE.** Pelvesthollow builds to 20,687 assets in 9
`--by-region` chunks (3x3 of 64 tiles), largest 13,848 bytes against the 30,720
cap, every verify check green apart from *no gates* (it has no wall) and 36
tile-seam pairs — against Forest Church's 409 on the same settings.
*Accepted:* pasted onto a fresh board with `review.ps1 tiled` and walked round.
The ground is one continuous sheet with no step at any join, buildings sit flush
on it with doors at ground level, thatch and chimneys complete, cobbled lanes
meeting grass flush, pines and broadleaf scattered through open country. Read
from four faces, from overhead, and from eye level.

*What was not measured:* no specific chunk seam was located on screen and
inspected at close range. What the shots show is several frames each spanning
more than one 64-tile chunk with no step anywhere in them. That plus
`verify.chunk_datum` passing is strong, but it is not the copy-out measurement
that settled the tiling rules in the first place.

**Stage 5 — the extras. BUILT; in-game orbit outstanding.** The five items,
and where each landed:

- `STONE_FENCE` as `field_wall` — built separately, `docs/fencing.md`
  (`build._lay_fences`; `TileMap.fences` carries the clipped polylines).
- `STONE_WALL` as open wall polylines — built with stage 6; East Tradebourne's
  2,367-cell rampart below is that work.
- **`raised` quads as bridge decks.** The raster paints a
  `LayoutArea("bridge")` ring as PIER and the plank machinery does the rest:
  `_lay_terrain` runs the channel on beneath, `_lay_bridges` lays the deck by
  its top at grade and rails the sides facing open water. Only cells already
  WATER convert — the authored quad overhangs its banks, and the overhang
  stays bank rather than becoming a timber platform on grass; the deck meets
  it flush. Painted after the road loop, so an MFCG river (a *road* there)
  cannot erase it, and a road that already claimed its own crossing keeps it.
- **Tree density follows the FOREST outline.** The rings land in
  `TileMap.forest` (a mask, not a surface — forest floor stays GROUND;
  translated and filtered in `TileMap.crop` like the gates). Inside the line
  the canopy field is lifted (`t + 0.55·(1−t)`), outside damped (`t · 0.40`),
  both only when a mask exists at all: an unmasked (MFCG) map scatters
  **bit-identically** to before the field existed, proven by digest on the old
  and new code. Redistributed, not raised — on the Pelvesthollow corner
  fixture, tree pieces inside the rings went 72 → 138, outside 390 → 146.
- **`TRAIL` as a 1-tile path.** A trail arm in the road loop paints LANE
  (trodden earth) over ground and field only — never water, because a footpath
  does not bridge: the path stops at the bank and resumes on the far side,
  where it used to be paved straight over the stream as a cobble ford. No
  street class, so verify never demands two abreast of it; a carriageway
  crossing the path takes the junction cell.

*Still owed:* the in-game pass — each of the three new builds visible on a
board and orbited from four sides (`review.ps1 360`). Look for East
Tradebourne's five authored crossings decked in harbour planking flush with
their banks; Pelvesthollow's three forest rings reading as closed stands with
thinned pasture outside; and any TRAIL leaving town as a one-tile gravel line
that vanishes at the stream bank. The drystone fencing keeps its own probe
discipline in `docs/fencing.md`.

**Stage 6 — scale up. DONE for all three.** Each town has its own board in one
campaign, pasted with `review.ps1 tiled`. Per-town numbers below.
Graybank imports as 434x306 tiles, 150 of 159 buildings (126 house, 15 shop,
3 smithy, 3 tavern, 2 guildhall, 1 temple), 50 chained roads, 91,429 assets.
**The nine buildings the core crop dropped are exactly the six FARM and three
INDUSTRIAL ones** — the outliers are literally the outlying farms and their
barns, which is the clearest confirmation the clustering crop is cutting where
it should.

*Chunk size is a real choice at this scale, and the byte cap decides it.* At 96
tiles the map is 20 chunks but the largest slab is 29,817 bytes against a 30,720
cap — 97%, close enough that a different seed could bust it. At 80 tiles it is
24 chunks and 20,604 bytes (67%). **Use 80.** Pelvesthollow's 64 gives 13,848,
so there is room to go up on a small map and none on a big one.

*Accepted:* all 24 chunks pasted at one cursor cell with `review.ps1 tiled`,
then toured. Every verify check green apart from *no gates*, 344 tile-seam pairs
and two cart-clearance tiles (0.0%). On the board: the ground is one continuous
sheet across every frame, houses seated flush along cobbled lanes, and the
river reads as a river — translucent water over a dark bed with a continuous
shingle bank on both shores, grass and trees meeting it flush. No step anywhere
in any frame.

*What was not checked:* the one auto-added bridge was never found in game — the
river was toured but not walked end to end. Same caveat as Pelvesthollow on
seams: no specific chunk join was located and inspected, only many frames
spanning more than one 80-tile chunk with no step in them.

### East Tradebourne — the scale case

739x598 tiles, **991 of 1007 buildings** (709 house, 171 smithy, 67 shop, 18
warehouse, 14 tavern, 5 guildhall, 4 temple, 3 barracks), 162 chained roads,
**387,381 assets = 38.7% of the per-board limit**, 114 chunks. Nothing about
the format was new here; everything that had only ever been read in the
importer got *built* for the first time:

- **The town wall exists.** Two open `STONE_WALL` polylines, rasterised to a
  2,367-cell rampart with 443 buildings inside it.
- **The market square is a plaza**, not a roofed box — 631 plaza tiles where
  the export says `MARKET` / `PAVEMENT`. The rule works on the file it was
  written for.
- **Five `raised` bridge quads** import as `LayoutArea("bridge")`; at the time
  of this build they were not yet constructed, and the three bridges that board
  has are the rasteriser's own, added to join districts split by water. Stage 5
  has since taught the raster to deck them (the quad's water cells become PIER)
  — the next paste of this town gets its authored crossings.

**Chunk size stops mattering once `--max-assets` binds.** At 112 tiles it is
114 chunks / 23,085 bytes; at 80 tiles, 146 / 23,070; at 160 tiles, 137 /
23,043. All three land on the same slab size because the quadtree split at
`--max-assets 6500` is what decides it, not the grid. **112 is the pick** — it
is the fewest chunks, and 160 is *worse* than 112 because oversized cells split
into four where a smaller cell would not have split at all.

**Closed, and it was never the import's doing:** `[FAIL] placements: 732 of
2367 town-wall cells have gaps in the masonry`. Bisected to `8412ce9`, which
added `_lay_city_wall`'s `entombed()` — a hollow-core optimisation that
emptied exactly the cells the masonry check samples; its parent `93ccba6`
builds Forest Church clean, and `e83671a` reverted the optimisation, so the
rampart builds solid again and the check passes. Forest Church is an MFCG map,
so the FTG path was never involved — East Tradebourne was simply the first FTG
town with a wall, and so the first to meet the regression. The history is in
`CLAUDE.md` under *Metrics must read the artifact*.

## 7. Fixtures and tests — DONE

`tests/fixtures/ftg_pelvesthollow_corner.geojson` is a real FTG export trimmed
to a 140 m square over the middle of the village: 23 buildings, 102 edges, 38
backgrounds, 2 water, 40 KB. It carries all four feature types and enough
vocabulary to exercise the tables. `tests/test_import.py` is 38 tests covering:

- format detection over both formats and both extensions, and a named error for
  a collection that is neither
- every ring closed and single, every edge a 2-point line — these guard §2.4, so
  if FTG changes shape they fail rather than the reader quietly misreading it
- background depth never exceeds 2 and a depth-2 point always includes GRASS
  (415 of 576 sample points are depth 2, so it is not a vacuous check)
- an unknown `buildingType` / `edgeType` / `backgroundType` imports under a
  default and is reported through `Layout.unmapped`
- chaining consumes every segment exactly once, stops at a junction, closes a
  loop, and survives a degenerate segment
- no two imported buildings overlap, as a *polygon* test — 5 of the fixture's
  253 pairs share a bounding box, so a box test would have been wrong here
- the core crop drops two synthetic outlying farms and cuts board area by more
  than 5x doing it
- metric scale puts a median house at 30–40 ft, and both overrides beat it

The chaining and plaza tests were mutation-checked: breaking `chain_segments` to
drop a chain, and disabling the PAVEMENT diversion, each fails the tests that
claim to cover them.
