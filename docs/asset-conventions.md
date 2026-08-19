# Asset conventions

The rules that keep emitted geometry valid. Each one exists because breaking it
produced a specific, visible failure in a real build.

## Scale: 1 tile = 5 ft, one creature per tile

`TILE_FEET = 5.0` in `citysmith/layout.py`. Every derived dimension follows from
it, so it is never a free parameter:

- A creature occupies exactly one tile. A street two tiles (10 ft) wide is the
  minimum for two creatures to pass abreast — `verify` flags anything narrower
  (`MIN_STREET_TILES` in `citysmith/verify.py`).
- `import` derives the tile scale from a real median house frontage
  (`--house-ft`, default 20 ft) rather than assuming one. If the result puts the
  median building under ~4 tiles across, `check_playability` warns: there is no
  room to fight indoors.
- Board limits are in the same units: 2000 × 2000 grid units, 1,000,000 assets.

## Footprints: cell roles are 1×1, block roles are 2×2

`citysmith/palette.py` declares two role families and `Palette.validate()`
enforces their footprints:

```python
CELL_ROLES  = ("floor", "floor_upper", "ground", "street", "water", "roof")
              + WALL_COURSE_ROLES   # ("wall_corner", "wall_corner_civic")
BLOCK_ROLES = ("ground_2x2", "field")
```

A **cell role** is laid one per tile and must resolve to an asset with
`size_x == size_z == 1.0`. A **block role** fills a 2×2 block and must be
exactly `2.0 × 2.0`. Roles in `WALL_SEGMENT_ROLES` (`door`, `wall_window`,
`wall_interior`) must match the resolved `wall`'s footprint exactly.

This is not tidiness. A 2-wide door dropped into a 1-wide wall slot overhangs
its cell, which pushes the slab's minimum corner out by a fraction of a tile,
and normalization then drags **the entire board** off the grid. The board stays
self-consistent so it *looks* right, but minis with grid snap no longer land on
the floors. The field-fringe bug was the same failure from the other side: 2×2
tilled blocks laid onto 1×1 leftover cells.

`Palette.validate()` runs on every command that resolves a palette and refuses
to build rather than emit an off-grid board. Only roles a style actually
declares are size-checked — an undeclared role falls back to a bare name search,
which is how `water` once resolved to a rowing boat.

## Normalization shifts by whole tiles only

`Slab.normalized()` translates by the exact minimum corner. That corner belongs
to whichever placement sticks out furthest — for a dressed map, some pine canopy
with a 2.55-tile footprint. Translating by its fractional overhang moves every
tile on the board off the grid.

`build._normalized_whole_tiles()` is what the builder actually calls. **Any new
code path that normalizes a slab must go through it, not through
`Slab.normalized()` directly.** Props reintroduced this bug once through the
scenery after it had already been fixed for doors.

The canary, which should be part of any review of a generated slab:

```python
# every non-prop placement must sit on a half-tile boundary
bad = [p for p in slab.placements
       if catalog[p.asset_id].kind != "prop"
       and any(abs(v * 2 - round(v * 2)) > 0.01 for v in (p.x, p.z))]
assert not bad
```

Expected result on a healthy build: `0`.

## Pin assets by exact NAME, never by tags

Tags describe **material**, not **look**. `--tag stone --group wall` returns 117
assets including "Aberration Floor 2x2". Free-text terms are no better: asset
names are inconsistent enough that "Tavern no floor" satisfies a search for a
floor.

Selecting roles by tag once produced a medieval town with **desert floors,
shogun interiors, and a fishing net standing in for water**. Every visual role
in `MEDIEVAL` is now pinned with `name=`:

```python
"water":  [_tile(name="tempWater1x1", ...)],
"street": [_tile(name="CobbleStone Floor Small", ...)],
"ground": [_tile(name="Grass 1x1", ...)],
"door":   [_tile(name="Door -Peasant", ...)],
```

Pinned names are the first query in the role's candidate list; looser queries
stay behind them as fallbacks so a missing pack degrades instead of failing.
Structured filters (`group=`, `tags=`, `kind=`) are still preferred over
free-text `terms` for those fallbacks.

Find the exact name to pin with:

```bash
python -m citysmith catalog search thatched --kind tile --limit 25
```

## Derive offsets from collider bounds, never hardcode them

Assets in the same role differ in thickness and height. `place_tile` /
`place_wall` / `place_centered` compute their offsets from the asset's
`ColliderBoundsBound` (surfaced as `size_x/size_y/size_z`). Hardcoding an offset
is what produces floating walls and sunken floors the moment someone swaps a
pack.

## Rotation pivot

A placement coordinate is the **min corner of the bounding box after
rotation** — the footprint swaps axes on odd quarter turns. There is no centre
pivot. Ground truth copied out of TaleSpire itself, for `Wall Only With Window`
(0.5 × 2.0): `rot=0 → (0.50, 0.00)` and `rot=270 → (0.00, 3.50)`.
`build.rotated_footprint()` implements it and
`test_placement_matches_talespire_measurements` guards it. Rotation is a step
index 0..23; degrees = `rot * 15`.

## The roof kit is a quarter turn off the wall convention

Walls use `ROT_N, ROT_E, ROT_S, ROT_W = 0, 6, 12, 18`. The Thatched/Village roof
kit does **not**:

```python
ROOF_EDGE_ROT   = {"n": 6,  "e": 0, "s": 18, "w": 12}
ROOF_CORNER_ROT = {"nw": 12, "ne": 6, "sw": 18, "se": 0}
```

Both live in `citysmith/build.py`. This is measured, not guessed — it was read
off a hand-built community cottage slab (see `tools/harvest_slabs.py` and
`tools/analyse_library.py`) after `tools/roof_probe.py` had narrowed it down
in-game. It is the reason our roofs looked mis-set for two revisions.

**Do not "fix" it to match the wall convention.** It is a property of the art,
not of our code.

Assembly convention, also learned from the same slab: hip roofs build as
concentric rings, one cell in and one piece up per course, closed with a flat
cap. The Thatched kit is the one that actually has a ridge cap; that is why the
palette uses it.

## Deriving a convention you do not know

The technique that has worked every time, and the one to reach for before
guessing:

1. **Make the game its own oracle.** Emit a probe slab that lays the unknown
   asset at every quarter turn, each on its own floor pad, with a stub wall
   marking north so the orientation is unambiguous from any camera angle.
   `tools/roof_probe.py` and `tools/rev6_probe.py` are the working examples.
2. **Or decode somebody's working build.** `tools/harvest_slabs.py` pulls
   community slabs into `library/`; `tools/analyse_library.py` decodes them and
   reports, per group tag, the asset names used, the rotation histogram, and the
   `y` histogram. A rotation histogram against known geometry gives you the
   convention directly.
3. **Correlate position against rotation.** The convention falls out of how a
   piece's min corner moves as `rot` changes — that is how the pivot question
   was settled, and it does not depend on interpreting a picture.

**Measure the slab; do not judge from screenshots.** Decode it and print
coordinates. Screenshots have repeatedly been read wrong here — a half-tile
offset, a piece one course too low, and a dropped prop all look like "close
enough" at a glance and are unambiguous in the numbers.

## Never stack overlapping props

TaleSpire silently drops props whose colliders overlap on paste. Build scenery
from single-piece assets. Full detail in
[pasting-into-talespire.md](pasting-into-talespire.md).
