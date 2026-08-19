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
- **Right-click drops what is in hand** (and resets the tool to the pointer).
  Switching tools on the build toolbar also works. `Escape` does *not* clear it
  -- it backs out toward the main menu, which is how a stray Escape ended a
  session on the campaign screen -- and neither does toggling `B`.
- Drive every click as explicit mouse-down / short wait / mouse-up. Never
  press-and-hold on the window title bar — that drags the window, and resizing
  the window from outside stalls Unity's renderer until the mouse moves over
  the client area again.
- Because paste is cursor-anchored, multi-chunk boards only line up if every
  chunk shares a bounding-box origin. `Builder.to_slabs()` adds a registration
  marker at (0,0,0) to each chunk for exactly this reason; commit all chunks
  over the same grid cell without moving the camera.
- Bindings worth knowing: `B` build mode, `F1` help (a video overlay — it does
  not screen-capture), `F2` recentre, `Space` menus, `Ctrl+Z` undo,
  `X`+drag select, left-click pick up, middle-drag rotate camera, scroll zoom,
  `Shift`+scroll raise/lower.

## Testing

`python -m pytest -q`. Tests assert invariants (no overlapping buildings, no
unreachable rooms, walls resting on floors), not exact output — so generator
aesthetics can change freely while real bugs stay caught. `tests/fixtures/*.slab`
are genuine TaleSpire slabs and are the ground truth for the codec.

## Not built yet

- Local UI. Deliberately deferred; `cli.py` is a thin shell over the core
  modules so a UI can be added without touching generation code.
- Creature/mini placement (`creatureCount` is always 0 in a v2 slab).
- Automated rollout of the 17 city chunks. The mechanics are all verified, but
  the pastes are still driven by hand one at a time.
