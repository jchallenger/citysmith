# citysmith

Python toolset that generates a city, ranks locations in it by encounter
potential, and builds the chosen location as a TaleSpire slab.

This file is the internal engineering notes. User-facing docs:
`README.md` (front door and end-to-end quickstart),
`docs/pasting-into-talespire.md` (the paste interaction, in full),
`docs/asset-conventions.md` (footprints, pinning, normalization, roof rotations),
`.claude/skills/citysmith/SKILL.md` (agent driving instructions).

## Layers

| Module | Role |
|---|---|
| `slab.py` | TaleSpire slab format V2 codec. Verified against real slabs. |
| `catalog.py` | Loads assets from the user's TaleSpire install; query API. |
| `palette.py` | Maps semantic roles (floor/wall/door) to catalog queries per style. |
| `mfcg.py` | Imports Watabou MFCG GeoJSON -> `Layout`. The primary path. |
| `layout.py` | Polygonal layout model; `TILE_FEET = 5.0` lives here. |
| `raster.py` | Layout -> tile grid: footprints, walls, doors, reachability. |
| `verify.py` | Playability report. Checks the TileMap, *not* the placements. |
| `city.py` | BSP city generation: streets, blocks, plots, buildings, districts. |
| `sites.py` | Scores buildings by encounter potential, with reasons. |
| `floorplan.py` | Interior rooms, doors, stairs for one building. |
| `build.py` | Geometry -> `Placement`s -> slabs. All offsets derived from bounds. |
| `render.py` | Hand-written SVG for city and floorplan reference maps. |
| `ai.py` | Claude translation layer. Optional; never emits geometry. |
| `cli.py` | Command line. Core logic stays out of here so a UI can be added. |

## Hard constraints

- **Python stdlib only** for the core. `anthropic` is an optional extra; there
  are no other dependencies and no build step.
- **Claude is a translation layer, not a generation layer.** It maps natural
  language to generator parameters and writes prose. It must never produce
  coordinates, asset UUIDs, or slab bytes.
- **Derive placement offsets from `ColliderBoundsBound`**, never hardcode them.
  Assets in the same role differ in thickness and height; hardcoding is what
  produces floating walls and sunken floors.
- **Prefer structured catalog filters** (`group=`, `tags=`) over free-text
  `terms`. Asset names are inconsistent — matching them loosely lets
  "Tavern no floor" satisfy a request for a floor.
- **The generator must work offline.** Every AI feature is additive.

## Slab format (verified, do not re-derive)

`base64(gzip(binary))`, where binary is:

```
u32 magic 0xD1CEFACE | u16 version 2 | u16 layoutCount | u16 creatureCount(0)
Layout[layoutCount]   uuid(16, .NET byte order) + u16 count + u16 reserved
u64[sum(counts)]      | 5 unused | 5 rot | 18 z | 18 y | 18 x |  (little endian)
u16 trailer 0x0000
```

- Positions are the asset's **min corner** in tile units; wire value is
  `round(pos * 100)`. Rotation is a step index 0..23, degrees = `rot * 15`.
- 1 world unit = 1 tile. Compressed payload must stay under 30720 bytes.
- Decode->encode reproduces the original **binary** byte for byte. It does not
  reproduce the base64 exactly, because .NET's deflate and zlib's differ — that
  is expected and harmless.

Official spec: `docs/slab-format-v2.md`.

## Rotation pivot (SETTLED in-game)

A placement coordinate is the min corner of the asset's bounding box **after**
rotation — the footprint swaps axes on odd quarter turns. There is no centre
pivot; the old `ROTATE_ABOUT_CENTER` flag was wrong and is gone. Ground truth
copied out of TaleSpire itself (`Wall Only With Window`, 0.5 x 2.0):
`rot=0 -> (0.50, 0.00)` and `rot=270 -> (0.00, 3.50)`. `build.rotated_footprint()`
implements it; `test_placement_matches_talespire_measurements` guards it.

## Driving TaleSpire (verified 2026-08-18)

Pasting is the only ingestion path, and it must be driven through the UI. What
actually matters, all confirmed in-game:

- **Run windowed, never exclusive fullscreen.** Fullscreen captures as a black
  frame and drops focus constantly. `Alt+Enter` toggles it and persists to the
  `Windowed Mode` setting. Size the window to fit the desktop or the bottom
  hint bar — the contextual binding display, and the best control reference in
  the game — is clipped off-screen.
- **`textinputhost.exe` steals foreground** and makes every synthetic click and
  keystroke get refused. It must be allowlisted alongside `TaleSpire.exe`.
- **A pasted slab commits on a left press with a real hold** (down, ~0.2 s, up).
  A zero-duration synthetic click is swallowed by Unity's input polling — this
  is what made paste look broken for an entire session. Same applies to keys:
  hold them briefly rather than tapping.
- **Paste is cursor-anchored, not coordinate-anchored.** The slab arrives in
  hand at the cursor, snapped to the global grid, and *stays in hand* afterwards
  as a repeat stamp — so one paste can be committed many times.
- **Right-click drops what is in hand -- but it has to be a *tap*.** This is
  the exact opposite of the left click that commits a paste, and getting it
  wrong is invisible: a right-click held ~250 ms is read as the start of a drag
  and the slab stays in hand, so every later click stamps another copy of the
  map. Verified on a blank board with the cursor moved away afterwards (a held
  slab follows the cursor; a committed one does not): 250 ms holds, 40 ms
  drops. `ts.ps1 clear`.
  **Nothing else clears the hand**, all tested the same way: not `K`, which
  only toggles its own tool; not clicking a tool on the build toolbar; not `B`.
  `Escape` is untested and stays that way -- it backs out toward the main menu,
  which is how a stray Escape once ended a session on the campaign screen.
- Drive every click as explicit mouse-down / short wait / mouse-up. Never
  press-and-hold on the window title bar — that drags the window, and resizing
  the window from outside stalls Unity's renderer until the mouse moves over
  the client area again.
- Because paste is cursor-anchored, multi-chunk boards only line up if every
  chunk shares a bounding-box origin. `Builder.to_slabs()` adds a registration
  marker at (0,0,0) to each chunk for exactly this reason; commit all chunks
  over the same grid cell without moving the camera. **A slab has two origins**
  -- lowest stored coordinate and lowest point of its geometry -- and they part
  company as soon as a prop is near the corner, because a prop stores its
  collider centre. Both have to land on (0,0,0); `verify.chunk_anchors` fails
  the build if they do not.
- **Synthetic input needs a hold *and* a scan code.** The hold is the part
  CLAUDE.md already recorded; the scan code is the other half. TaleSpire reads
  raw input, where the scan code identifies the key, so `keybd_event` with
  `scan = 0` arrives as no key at all -- Ctrl+V silently did nothing while the
  clipboard, the foreground window and the mouse all checked out. Fill it from
  `MapVirtualKey(vk, 0)`. `tools/ts.ps1` is the one implementation of all this.
- **Two ways to move the camera, and both are about duration.**
  A **left drag** pans, and it has to be *slow*: 60 steps of 40 ms tracks,
  24 steps of 16 ms outruns the camera and registers as nothing, which reads
  exactly like "pan does not work". Use it for short, precise moves.
  **WASD** flies, and it ramps -- velocity eases up to a maximum, so the key
  has to be *held*. A 0.4 s press crawls a few tiles, 3 s crosses the map.
  Tapping it looks like a dead binding, which is what made me write "WASD does
  nothing here" in this file; it does, it just needs the momentum.
  `ts.ps1 key -Keys w -Hold 3.0`. Arrow keys are untested. The wheel only
  zooms, and zoom-out is capped well short of a 187-tile map, so `--crop` a
  window onto its own board is still the quick way to review one quarter.
- **Empty the hand after every paste** -- see the right-click tap above. A
  right-click with an empty hand opens the asset library over the board, so
  clear once, deliberately, rather than pre-emptively before each camera move.
  The community keybind lists do not cover any of this and are stale besides
  (they give WASD for the camera, which does nothing here); the bottom hint bar
  would, but it is clipped unless the window is sized to fit the desktop.
- Bindings worth knowing: `B` build mode, `F1` help (a video overlay — it does
  not screen-capture), `F2` recentre, `Space` menus, `Ctrl+Z` undo,
  `X`+drag select, left-click pick up, middle-drag rotate camera, scroll zoom,
  `Shift`+scroll raise/lower.

## Asset geometry rules (learned the hard way, in this order)

Three separate defects on the same board all came from assuming an asset's
shape instead of reading it. The rules that fall out:

- **`place_tile` needs an asset that fills the cell.** The medieval castle kit
  is *curtain wall*: `castle wall 1x1` is 1.0 x 2.0 x **0.5**, authored to
  stand on a cell boundary. Laying one per cell across a rampart four cells
  thick left a 0.5-tile slot between every pair of cells -- 2.5 ft of daylight
  through the wall, for the whole circuit. A mass gets a full-cell block
  (`city_wall_core`) with the thin pieces hung on the faces that show.
- **Surface tiles align at the top, not the bottom.** Cobble is 0.25 thick and
  grass is 0.5. Laid from a common bottom, every street sat a quarter tile
  below the grass -- a 15 inch kerb along both sides of every road, on 1,234
  tiles. `Builder.surface()` places by top height; use it for anything a
  creature stands on.
- **A crenellation is not a cap.** Merlons belong on the cells that face out
  of town, found by flooding the map from its border. Putting one on every
  cell with an exposed side crowned 61% of the mass, and because the circuit
  is a stair-stepped diagonal those teeth pointed every which way; the wall
  read as a comb. The cells behind the parapet are paved as a wall-walk.

- **A translucent tile shows whatever is under it, so the thing under it is
  the material you actually see.** The river bed was laid in the `ground` role,
  which is grass, and TaleSpire's water is translucent: the board read as two
  sheets of turf with a blue film between them. That is what three sessions of
  "I see a second layer of land" meant. The bed has its own role now. Which bed
  reads as *water* is a rendering question and `tools/water_probe.py` puts the
  candidates side by side under one to four tiles of water -- bright beds (sand,
  desert stone) barely tint and the river reads as a dry wash; a grey stony bed
  goes deep teal by two tiles down. Depth was already in the geometry and
  invisible until the water column was filled and the bed stopped being bright.

- **A full-cell collider does not mean a full-cell mesh.** `md_wall_1x1_diag_01`
  measures exactly 1.0 x 2.0 x 1.0 and is a blade cutting the cell corner to
  corner. Built as the rampart mass it striped the entire circuit with vertical
  daylight -- the same failure as the curtain-wall piece above, from an asset
  whose measurements say it cannot happen. Nothing in the catalog data
  distinguishes them; the *kit* does, in the name: `diag`, `half`, `filler`,
  `1x2`. `city_wall_core` is guarded against those, and
  `build.is_curtain_piece` is the shared test for "thinner than a cell, so it
  goes on the edge".
- **A group tag names a family, not a form.** `city_wall_cap` was pinned to
  `castle merlon 1x1` because its group tag is "merlon". It is a *hoarding* --
  boarded timber, not stone -- and so is every other piece in that group. The
  circuit was crowned with wooden crates for eleven revisions.
- **A probe read from one angle is a probe that lies, and this cost three
  picks in a row.** `md_wall_1x1_diag_01` won a probe of flat 3x3x2 masses
  photographed from the front -- the one view where a rank of blades hides its
  own gaps. `Castle Ruins Wallbase 02` replaced it and is *ruined* masonry;
  read from overhead and one oblique it looks solid, because at those angles
  its front face covers its own holes, and it tiled a town into a lattice of
  piers you could see straight through. Nothing in the catalog data says either
  of these things.

  The standard now: probe the shape the generator actually builds, including a
  **run one cell thick** -- the harshest case, since nothing stands behind the
  block to plug what it leaves open -- and **orbit all four sides plus
  overhead** before choosing. Keep the known-bad pieces in the probe as
  controls so every screenshot contains a failure to calibrate against.
  A plain solid cube (`md_stairblock_01`) has no relief to hide a hole in and
  nothing directional to line up; that is why it is the mass now.
  `tools/wall_probe.py` and `tools/parapet_probe.py` build for this.

The general form: **an asset's `ColliderBoundsBound` is data, and shape
assumptions are bugs waiting for a big enough map to become visible.**

## Metrics must read the artifact, not the plan

This has now bitten four times, and each time the report said the map was
fine while the board was visibly broken:

1. `verify` read the TileMap, so doorways built as solid wall still scored
   100% enterable.
2. The street check read a tile's *class*, so a main-street tile with a house
   on three sides scored compliant.
3. Wall continuity was measured as orthogonal cell adjacency, which a rampart
   of 0.5-deep fins passes perfectly.
4. Chunk skipping was judged per chunk, so two chunks enclosed by finished
   town were dropped and pasted as a rectangular hole in the ground.
5. Chunk registration was judged on `Slab.bounds()`, which is componentwise:
   the one chunk covering the map's corner had *some* placement at x=0, *some*
   at y=0 and *some* at z=0, so its minimum read as the origin and it was the
   only chunk that never got a marker -- while its pines overhung that corner
   by a tile.

`verify.check_placements` and `verify.enclosed_voids` measure emitted boxes and
the written chunk plan respectively. **New checks go there, not into the
TileMap pass.** `verify._Occupancy` reconstructs solid geometry from the
placements and is the tool for "is there actually anything here".

Corollary that cost an hour: **when a measurement looks wrong, check for stale
artifacts before debugging the code.** A tree-species count read 80/15/5
against a 62/30/8 target purely because a previous build's chunk files were
still in `out/`.

## Testing

`python -m pytest -q`. Tests assert invariants (no overlapping buildings, no
unreachable rooms, walls resting on floors), not exact output — so generator
aesthetics can change freely while real bugs stay caught. `tests/fixtures/*.slab`
are genuine TaleSpire slabs and are the ground truth for the codec.

## Not built yet

- Local UI. Deliberately deferred; `cli.py` is a thin shell over the core
  modules so a UI can be added without touching generation code.
- Creature/mini placement (`creatureCount` is always 0 in a v2 slab).
- Interiors. Footprints are now ~650 sq ft, which is finally big enough for
  rooms to be worth generating; `floorplan.py` already does the geometry, but
  nothing wires it onto a second board per building.
- A tapered map edge. The border ring still ends on a hard straight cut, so
  the map reads as a cropped rectangle from outside (finding 8).
- Gate furniture. The gate is a tunnel through the rampart with the wall
  carried over it, but there is no arch dressing, no doors and no gatehouse
  towers flanking the opening.
