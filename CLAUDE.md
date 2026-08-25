# citysmith

Python toolset that generates a city, ranks locations in it by encounter
potential, and builds the chosen location as a TaleSpire slab.

This file is the internal engineering notes. User-facing docs:
`README.md` (front door and end-to-end quickstart),
`docs/pasting-into-talespire.md` (the paste interaction, in full),
`docs/asset-conventions.md` (footprints, pinning, normalization, roof rotations),
`docs/ftg-geojson-import.md` (the second import format, reverse-engineered),
`docs/scenes.md` (one building as a board the party walks into),
`docs/interior-slabs.md` (what hand-builders do inside a building, measured),
`docs/district-surfaces.md` (what a town is paved with, and whether
  "district" is a thing we can key on),
`docs/building-massing.md` (storeys, footprints and yards by settlement size),
`docs/fencing.md` (field walls, and why they are not on the grid),
`tasks.json` (what is designed, what is built, and `citysmith tasks --check`),
`docs/board-strategy.md` (interior vs exterior boards, and moving a party
  between them -- what the community recommends, and what we do instead),
`docs/branching.md` (worktrees, and the policy that closes them),
`.claude/skills/citysmith/SKILL.md` (agent driving instructions),
`.claude/skills/talespire-boards/SKILL.md` (campaigns, boards, naming).

## Layers

| Module | Role |
|---|---|
| `slab.py` | TaleSpire slab format V2 codec. Verified against real slabs. |
| `catalog.py` | Loads assets from the user's TaleSpire install; query API. |
| `palette.py` | Maps semantic roles (floor/wall/door) to catalog queries per style. |
| `importers.py` | Sniffs which generator a GeoJSON came from and dispatches. |
| `mfcg.py` | Imports Watabou MFCG GeoJSON -> `Layout`. The primary path. |
| `ftg.py` | Imports Fantasy Town Generator GeoJSON -> `Layout`. |
| `layout.py` | Polygonal layout model; `TILE_FEET = 5.0` lives here. |
| `raster.py` | Layout -> tile grid: footprints, walls, doors, reachability. |
| `verify.py` | Playability report. Checks the TileMap, *not* the placements. |
| `city.py` | BSP city generation: streets, blocks, plots, buildings, districts. |
| `sites.py` | Scores buildings by encounter potential, with reasons. |
| `floorplan.py` | Interior rooms, doors, stairs for one building. |
| `interior.py` | One imported building -> a plan, a door side, its occupants. |
| `scene.py` | A scene: interior + apron + party marks + manifest + brief. |
| `boards.py` | Which TaleSpire board holds which scene. The only record. |
| `quarters.py` | Derived town quarters, and the test for whether any exist. |
| `tasks.py` | Designed vs built. Every claim carries checkable evidence. |
| `config.py` | `config/scene.json`: defaults in code, the file overlays. |
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

## Two import formats, and the extension does not tell them apart

There are now two generators citysmith reads, and **all four combinations of
format and extension exist on this machine**: MFCG ships as `.json` *and* as
`.geojson`, and so does Fantasy Town Generator. Dispatching on the extension
would be wrong on real files, today. `importers.classify` reads the first
features instead, and the discriminator is exact rather than a guess: MFCG puts
a string `id` from a closed vocabulary on the feature itself and has no
`properties`; FTG has no feature `id` and carries `properties.type` in
`{BUILDING, EDGE, BACKGROUND, WATER}`.

What FTG gives that MFCG does not, and what follows from it:

- **A real scale.** FTG's docs state 1 unit = 1 metre, so `feet_per_unit` is
  3.28084 and nothing is inferred. The cross-check is worth keeping: citysmith's
  own median-house-frontage anchor at 35 ft lands within 4% of the metric scale
  on all three FTG exports seen, because an FTG house *is* 34-35 ft across. Two
  independent routes, one number. `--house-ft` still overrides.
- **Authored types and names.** MFCG exports geometry only, so `mfcg.py` invents
  wards and allocates scarce kinds by quota. None of that runs for FTG -- the
  export says each building's type and its name. `LayoutBuilding.name` carries
  it; `.stone` carries `material: STONE_BRICK`.
- **No metadata feature at all.** No version, no settlement name (it is only in
  the filename), no road width, no wall thickness. Carriageway widths are
  chosen in `ftg.ROAD_WIDTHS_M` and stay fixed in metres whatever anchor is in
  force. **Wall thickness is not a free parameter**: at `Layout`'s 2.0-tile
  default the band is two cells, so on a diagonal circuit only 13% of wall
  cells have all four orthogonal neighbours and the rampart has no core to hang
  its curtain pieces on. `ftg.DEFAULT_WALL_THICKNESS_M` is 4.5 m, which puts
  East Tradebourne at 41% -- the same place MFCG's own metadata puts Forest
  Church (2.77 tiles, 37%).
- **Edges are single segments, not polylines.** Every `EDGE` is exactly two
  points -- it is one boundary segment of a background polygon, and 93-96% of
  edge endpoints are also background vertices. `ftg.chain_segments` joins them
  through shared endpoints (which match exactly; no tolerance needed) and stops
  at any junction, so a fork stays a fork.
- **Ground cover is a base sheet plus at most one thing on it.** Sampled on a
  grid over all three exports, no point is more than two backgrounds deep and
  every depth-2 point is `GRASS` plus one other. So there is no z-order to
  resolve: drop GRASS, let anything else win. **The file's own feature order is
  not a draw order** -- FOREST is listed last in one export and LAWN first in
  another, and both belong on top. It was tempting and it is wrong.
- **The canvas is mostly farmland, and the crop window is the whole ballgame.**
  Clipping to `buildings + margin`, which is what the MFCG path does, gives
  Graybank 853x1013 tiles because nine outlying farms stretch the box; the
  settled core is 400x272. `ftg.core_cluster` single-links building centroids at
  60 m and crops to the largest group. It also removes 93-94% of the
  `STONE_FENCE` run, which on East Tradebourne is 92,638 tiles -- three times
  Forest Church's entire board.
- **A market square is exported as a BUILDING**, with `material: PAVEMENT`.
  Built as one it is a roofed box over the square, so it is diverted into a
  plaza area. The *material* is the test; `buildingType: MARKET` is only
  corroboration, because that vocabulary grows and this one has not.
- **The vocabulary grows between exports.** Five building types, `STONE_WALL`,
  `ROAD_TEXTURE_TYPE`, `PAVEMENT` and `raised: true` appear only in the largest
  of the three files seen. An unmapped value therefore imports under a default
  and is *reported* through `Layout.unmapped` -- never raised, never dropped. A
  dropped feature is invisible on the board, which is the failure this file
  records four other times.
- **`raised: true` means bridge.** Across all three exports it is true for
  exactly five features, and every one is a ~20x20 m `ROAD_TEXTURE_TYPE` quad
  over water.

`docs/ftg-geojson-import.md` is the full schema, the measurements behind each of
these, and the remaining stages. Built and pasted on all three exports:

| town | tiles | buildings | assets | chunks | chunk size |
|---|---|---|---|---|---|
| Pelvesthollow | 176x184 | 35 | 20,514 | 8 | 64 |
| Graybank | 434x306 | 150 | 94,139 | 22 | 80 |
| East Tradebourne | 739x598 | 989 | 411,106 | 102 | 112 |

Rebuilt 2026-08-25 with the surfaces, massing, yard and fence work, and
re-pasted on all three. **The three towns now differ from each other in the
report, which is the whole argument for that work:**

| | Pelvesthollow | Graybank | East Tradebourne |
|---|---|---|---|
| storeys | `1:34 2:1`, mean 1.03 | `1:78 2:71 3:1`, mean 1.49 | `1:167 2:575 3:247`, mean 2.08 |
| yards | 20 of 35 | 89 of 150 | 230 of 989 |
| field walls | 9 runs | 5 runs | 22 runs |
| quarters | none, 0.00x lift | none, 0.86x | 1.28x -> residential 70%, craft 15%, market 9% |
| surface materials | 9 | 9 | 9 |

**The storey counts are fixed at IMPORT, not at build**, so rebuilding an
existing `layout.json` does not re-run `storeys_for` -- a `main` build off the
same layout produces the identical skyline. Re-import to change it.
Measured against `main` on the same Pelvesthollow layout, what the *build*
changes is the surfacing and the dressing: 14 surface materials became 19, and
the single 2,289-tile cobble carpet became 1,509 castle-stone main street,
1,430 swamp-floor lane, 731 cobble, 317 tilled earth, 171 dry ground and 49
castle floor. Roofs went from one material to two.

**The 2026-08-25 dressing ate the byte headroom, and `--max-assets` is the
lever, not `--chunk-tiles`.** Yards, field walls and the wider surface palette
took East Tradebourne's largest slab from 23,085 bytes to **30,546 against the
30,720 cap** -- 99.4%, valid but with nothing to spare. Going *down* to 96
tiles makes it worse, not better: the build fails outright with a chunk at
31,739 bytes, because a smaller cell means the quadtree splits fewer of them.
`--max-assets 6500` at 112 tiles restores it -- 114 chunks, largest 24,204
bytes, 79%. This is the "re-check after any change that adds dressing" line
below, firing for real.

**Chunk size is bounded by the byte cap -- until `--max-assets` binds, and then
it stops mattering.** Graybank at 96 tiles is 20 chunks with the largest slab
at 29,817 bytes against the 30,720 cap -- 97%, close enough that another seed
could bust it; at 80 tiles it is 24 chunks and 20,604 bytes. But on East
Tradebourne, 80 / 112 / 160 tiles give 146 / 114 / 137 chunks and all three land
within 40 bytes of each other, because the quadtree split at `--max-assets 6500`
is what sets the slab size. Above that threshold, pick the cell size that
*splits least*: 160 is worse than 112 because an oversized cell splits into four
where a smaller one would not have split at all. Size for the largest slab near
two thirds of the cap, and re-check after any change that adds dressing.

**The paste order is not the filename order.** `--by-region` writes the chunk
covering the anchor cell *last*, so the anchor is still bare board for every
paste before it; an alphabetical glob sorts that chunk into the middle and the
four after it inherit its height. `_write_chunks` writes
`<stem>-paste-order.txt` beside the slabs for anything driving the paste, and
`review.ps1 tiled` reads it rather than globbing.

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
  **The anchor is the cursor's ground *hit point*, and it slides (SETTLED
  2026-08-21).** The point is wherever the cursor's ray first meets something:
  the bare board for the first paste, the top of the grass -- or a stump, or a
  pine -- for every paste after. With the camera pitched, a higher hit slides
  the point toward the camera by (height x cot(pitch)), so a chunk pasted over
  an earlier one lands a cell or two short of it. Measured three ways on one
  36x30 crop with identical camera routes: the structure chunk alone sits on
  its floors; pasted over the landscape it lands about two cells toward the
  camera, with a strip of every floor showing on the far side; pasted with the
  camera straight down it sits on its floors again. This, not a missing file,
  is the "dark pad beside a house" that was reported as buildings missing the
  mark. **Every paste in `review.ps1` is now made looking straight down**
  (`Paste-Stack`, `$PITCH_DOWN`), where cot is zero and nothing under the
  cursor can move the anchor. By hand: pitch the camera vertical before
  `Ctrl+V`, every chunk, and do not tilt it back until the last one is down.
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
- **Every binding is in the table in `docs/pasting-into-talespire.md`.** Do not
  duplicate it here; it is read off the game's own hint bar and it is the one
  copy. What belongs here is *why* the ones that bite, bite:
  * **Duration is a parameter, not a detail -- but it is ONE FRAME, not one
    second (MEASURED 2026-08-24, correcting what stood here).** A key has to be
    held across at least one game frame or it is never seen: 0 ms registers
    0/12 times, 40 ms 12/12, and 40 ms is one frame. WASD still ramps, so
    *distance* still needs a long hold -- 0.4 s crawls, 3 s crosses a map --
    but that is the camera's acceleration curve, not input polling.
    `ts.ps1 key -Keys w -Hold 3.0`. See "Input timing is set by a 25 fps cap".
    **The claim that a drag has to be slow is REFUTED.** This file used to say
    "60 steps x 40 ms tracks; 24 x 16 ms outruns the camera and registers as
    nothing". `tools/drag_speed.ps1` rotates by a fixed amount at a range of
    cadences and compares each resulting frame against a 60x40 reference:
    60x40, 40x25, 30x20, 20x16, 12x16 and **8x10 -- 80 ms of dragging against
    the reference's 2400 -- all land on the identical view**, at the 0.47 noise
    floor. Removing the pause between the press and the first motion changes
    nothing either. Whatever the original failure was, it was not the cadence.
    `ts.ps1`'s orbit/pan/rdrag now use 16x12 with trimmed pauses (`$CAM`),
    about 3x faster per camera move end to end (3430 ms -> 1073 ms measured);
    `select` and `elev` keep the old timings, because only the orbit was
    measured.
  * **The scroll modifiers retarget on whether the hand is empty**: with
    something held, `Ctrl` is vertical and `Shift` is *horizontal*.
    `raise`/`lower` were built on Shift, so a whole session of "nudge it down
    a course" tests was sliding the slab sideways and reading the result as
    evidence about height. `ts.ps1 nudge -Mode vertical|plane|rotate`.
  * **`G` and `N` persist across new boards**, and each imitates a defect: a
    build plane makes a chunk land a course high with nothing wrong in the
    file, a cut box reads as a hole in the terrain. `ts.ps1 planestate` reads
    the icon rather than trusting memory; `hold` refuses to pick up a slab
    while the plane is up. **But the icon only exists in build mode**, and
    outside it `planestate` was sampling the board: Graybank's grass reads
    rgb(177,176,69), r-b = 108, a confident "ON" from a probe pointed at turf.
    Acting on that turned the plane *on* with a toggle meant to turn it off,
    while the reading claimed the toggle had failed -- the same
    reading-the-board failure this file already records once, in the same
    function. It now tests that the toolbar strip is dark and grey and that the
    icon patch holds some near-white glyph pixels, and reports `UNKNOWN`
    otherwise; `review.ps1` requires an explicit `off` rather than
    "not ON", because `-match 'ON'` does not match `UNKNOWN`.
  * **Zoom-out is capped** well short of a big map, so the right-hand height
    slider is the only way to fit a quarter in frame -- and a high oblique
    then shows distance fog as a hard full-width line that has twice been
    mistaken for a chunk seam. Judge a step below the slider's midpoint, or
    `--crop` the district onto its own board.
- **A paste does not always land at the coordinates in the slab, and the exact
  rule is NOT settled.** What is measured: a copy-out of a region-chunked board
  carried a 3.5 relief where the source's maximum possible is 3.0, so half a
  tile was introduced at paste time. The working theory was "the slab comes to
  rest on whatever is under the cursor" -- which fits chunk 2 being lifted by
  one grass-thickness after chunk 1 laid ground under the anchor.
  **That theory does not survive the layered build.** The structure layer's own
  lowest point is the registration marker at y=0, so resting-on-top would lift
  every building by the full height of the terrain; instead it lands seated
  flush, with no nudge. So something else is going on -- possibly the snap
  resolves collisions rather than stacking, possibly it uses the grid. Do not
  rely on the resting model; rely on the procedure below, which is verified.
  Ctrl+scroll (`ts.ps1 nudge`) is the correction if a layer ever does land
  wrong, and the preview's translucent-mesh state shows intersection.
  **The reported "grass standing above paving" was looked for at close range
  and not found (2026-08-21).** A 40x40 crop of the town centre pasted as
  landscape then structure, walked round with `review.ps1 360` and zoomed at
  native pixels from eye level, shows grass, gravel lane and cobble meeting
  flush at every junction; every floor tile in the build tops out at 0.5 and
  every one has a roof over it. The dark "pads" that read as raised floor in
  the screenshots are cobble lying in a building's shadow beside sunlit grass
  -- a hard lighting boundary, not a step. That is hypothesis 2 of the brief
  and it is now the working conclusion.
  **Chunks with identical boxes do seat identically -- measured.** A 32x32
  crop cut into four 16x16 landscape chunks (`--chunk-tiles 16
  --max-assets 420 --keep-open-country`) plus its structure chunk, pasted in
  order at one cursor cell and walked round with `review.ps1 360`, is one
  continuous sheet of grass across every junction, from four faces, overhead
  and in section. The line that looked like a chunk seam on an earlier
  three-chunk crop was the river: its south bank runs along the same row, and
  from the south at a grazing angle a channel reads as a dark strip with the
  far bank's soil face above it -- a foreshortened river and a half-tile step
  are the same picture until you come round the side. The rule for a
  suspected seam is therefore: look from the side *and* check whether a
  watercourse or a road edge runs along that row before believing it.
  **The full-width band in every high overview is distance fog, not a seam.**
  With the camera slider near the top and the wheel zoomed out, a razor-sharp
  horizontal line crosses the whole frame with greyer ground beyond it. It
  sits at the same screen height whatever is under it (town, fields, the
  river), moves up the frame when the camera pitches down, never tilts when
  the camera yaws, and dragging the point under it to the centre and lowering
  the camera finds ordinary ground there with no line on it. It was mistaken
  for the z=48 chunk boundary twice in one session. Overviews are for
  composition; any judgement about a step needs the camera below the slider's
  midpoint.
- **Ctrl + right-click drag controls elevation** -- the working plane the build
  tools and the X+drag selection are cut at. `ts.ps1 elev`. Worth knowing
  before reverse-engineering anything else: several tools behave as if they are
  broken when the plane is simply somewhere unexpected.
- **Copy-out is only half solved.** `X`+drag draws a selection marquee and the
  submenu that appears offers a button whose own tooltip reads "copies a slab
  into the clipboard" -- but driven synthetically it returns a valid *empty*
  slab (31 bytes) over open ground, and real content only when the region
  contains a building. The marquee draws, the button is the right button, and
  the selection is nevertheless empty. Not yet understood; a hand-made
  selection copies terrain fine, so this is a driving problem rather than a
  game limitation. Copy-out matters because it is the *only* way to read what
  actually landed on the board rather than what was written to the file, and
  that distinction has been the crux of a whole session.
  **What the 2026-08-21 session added:** the selection returns *structure* and
  never *terrain*. Over a 40x40 crop it came back with three wall pieces (a
  corner and two panels, origins at y=0.5); with the working plane raised a
  little (`elev -DY 30`) it came back with one course of thatch roof pieces
  and nothing else; raised further, empty; lowered in steps of 30 px and of
  300 px it alternated between walls and empty, never once including a grass
  or cobble tile, whose origins are at y=0. So the selection behaves like a
  thin horizontal *slice* at the plane's height rather than a volume from the
  ground up, and the plane could not be driven down to the turf. The hint bar
  goes blank while a selection is active, so it does not name the binding.
  `M` (bottom right, a ruler icon) is still unidentified.
  **What 2026-08-22 added, and it is enough to verify a board with:**
  - **The clipboard still holds the last slab you pasted**, and `Ctrl+C` over
    a selection that did not take leaves it there. A "copy-out" that decodes
    to exactly one chunk file, at offset (0,0,0), is the echo of your own
    paste and proves nothing. Compare its length against the last file pasted
    before believing a word of it -- this was believed for several minutes.
  - Only **one marquee per board** could be driven. After a selection takes,
    `X`+drag stops registering; clicking the marquee icon on the build toolbar
    re-arms it for one more. Beyond that, make a new board.
  - The slice is at the working plane, so **what comes back depends on where
    the plane is**: at the default it sliced an upper storey at y=11. One
    `elev -DY -300` brought it down to y=3.5, which cuts the rampart and the
    tree canopies -- and canopies are the useful thing, because a prop has
    fractional coordinates and a rotation, so its signature is unique and it
    can be matched to a source chunk without ambiguity. A wall run cannot:
    every block is identical, so a chunk shifted one tile still matches.
  - **While the select tool is armed the whole scene renders washed out.** It
    reads exactly like distance fog and it is not; the tool stays armed
    between selections.
- **The right-hand vertical track is the ELEVATION CUT PLANE, not a camera
  height slider (SETTLED 2026-08-24), and `ts.ps1 camera` is withdrawn.**
  Two independent errors were stacked here.
  * **It was reading the wrong pixels.** The track is anchored to the RIGHT
    window border -- it sits 102 px in from it -- and the scan column was
    written as `client.X + 1540`, an offset from the LEFT. That was 60 px from
    the right edge of the 1600-wide window it was written on and is 380 px from
    the edge of the maximised 1920 one, where it lands on Cutscene Mode's blue
    "Grab Shot" button, rgb(0,114,165). `camerastate` reported "handle at y=38
    of 700" off that button. **Third recurrence of the same failure**, after
    `planestate` reading the board and the toolbar being centred.
  * **It was the wrong widget.** Hover its markers and the game names them:
    the sliding handle reads **"0 TILES"**, a fixed reticle beside it "0.5
    TILES", and the locked green marker at the top of the track "60 TILES".
    Dragged, it raises a cut plane and everything below it renders with a heavy
    green tint. A large camera height change (Ctrl+scroll) leaves it exactly
    where it was, which is the measurement that settles what it is not.

  **Camera height is Ctrl+scroll with an empty hand, or Ctrl+right-drag**
  (`ts.ps1 nudge -Mode vertical`, `ts.ps1 elev`) -- the play-mode hint bar says
  so directly: `CTRL + [mouse] MOVE CAMERA VERTICALLY`. `elev` was documented
  here as setting "the working plane"; it moves the camera.

  **Driving the ruler needs both halves right**, and either one alone does
  nothing: grab the blue **chevrons** (rgb(28,175,255)), not the diamond on the
  track line -- that diamond is a fixed 0-tile marker and a press on it goes
  through to the board -- and move with **relative mouse motion**, for the same
  reason a creature needs it. `ts.ps1 elevplane -DY -200` raises it,
  `ts.ps1 elevstate` reads it back; verified reversible, frac 1.0 -> 0.751 ->
  1.0.

  This matters beyond tidiness: `X`+drag copy-out "behaves like a thin
  horizontal slice at the plane's height" and "the plane could not be driven
  down to the turf" was guesswork, because nothing could read the plane. Now it
  can be read in tiles and driven to a repeatable position.
- **Read the camera back rather than tracking it in your head.** Every camera
  command is a *relative* move; a session that only issues them ends up over the
  void wondering where the map went. `ts.ps1 camerastate` saves a crop of the
  compass rose, which gives bearing by where N points and pitch by how squashed
  the circle is. The compass is anchored **bottom-left**, and its crop was
  `(X+490, Y+660)` -- correct on 1600x900, open board on 1920x1080 -- so it is
  derived from the rect now too. Check it before concluding something is missing
  from the board.
- **Derive screen coordinates from the window, never hardcode them.** The
  window gets moved and resized between sessions, and a stale rectangle does
  not fail loudly -- it silently aims a click or a pixel probe at the wrong
  thing. `ts.ps1 client` returns the client rect and centre; `grab.ps1` defaults
  to it. This is how `planestate` came back reading the board instead of the
  toolbar icon it was aimed at.
- **Look at the preview before committing.** `ts.ps1 hold` does the Ctrl+V and
  stops there, leaving the slab in hand; `commit` lands it. The preview is also a *validity* display: a
  held slab that intersects placed geometry renders as a pale translucent mesh
  instead of solid tiles, which is a direct read on whether the height snapped
  right. Pan so the paste point is at the centre of the client area (~955, 546)
  first -- near the screen edge the cursor's ground projection is at a grazing
  angle and the snap is harder to judge.
- **`G` raises a build plane, and a paste snaps to it instead of the ground.**
  A grid at a fixed elevation; Shift+scroll moves it; **both survive making a
  new board.** While it is up a chunk lands a course above its neighbours and
  nothing in the slab data is wrong -- which is exactly the shape of "grass
  above grass", arriving at paste time. It is trivial to leave on by accident
  (pressing `g` while probing keybinds does it) and the only tell is a small
  orange highlight on one toolbar icon and a faint orange grid out over the
  void. `ts.ps1 planestate` reads the icon rather than trusting memory -- the
  whole icon square is averaged, because a single pixel lands on the white
  glyph and reads the same either way; off is rgb(71,71,71), on is
  rgb(173,117,73). `ts.ps1 hold` refuses to pick up a slab while it is up.
- **Empty the hand after every paste** -- see the right-click tap above. A
  right-click with an empty hand opens the asset library over the board, so
  clear once, deliberately, rather than pre-emptively before each camera move.
  The community keybind lists do not cover any of this and are stale besides
  (they list WASD for the camera without saying it has to be held); the hint bar
  would, but it is clipped unless the window is sized to fit the desktop.
- **A campaign holds many boards, and both naming and switching are drivable.**
  A town per board only works if you can tell them apart afterwards, and
  `newboard` leaves them all called `Unknown Realm N`.
  * **`...` beside the board name opens a rename dialog directly** -- not a
    menu. The current name arrives already selected, so `Ctrl+V` over it
    replaces the lot and `OK` commits. The clipboard is the only way to get
    text in: TaleSpire reads raw input, so synthetic typing does not arrive.
    `ts.ps1 rename -Text "Graybank"`.
  * **The chevron beside it is a saved-state indicator, not a board list.** It
    toggles "No unsaved changes" and nothing else; it was tried first.
  * **The board switcher is `Space` then the top icon of the left-hand
    column** -- "Campaign Boards", a list with a play arrow per board.
    `ts.ps1 boards` opens it and screenshots it, which is the point: the list
    is sorted **alphabetically, with the current board highlighted in place**
    rather than lifted to the top -- measured on a seven-board campaign where
    the current board sat fourth. Either way the rows move every time a board
    is renamed, so read them off the shot rather than reusing a position. Each
    row's triangle expands a per-board menu that includes **Delete board**, so
    aim at the play arrow and not at the row.
    `Space` is a toggle and `boards` sends it blind: if the panel does not
    appear in the shot it was toggled shut, and running the command again
    opens it.
  * Switching to a 387k-asset board takes tens of seconds. Wait before
    clicking anything on it.
- The full binding table lives in `docs/pasting-into-talespire.md`. One copy,
  read off the hint bar; this file keeps only the reasons above.

## Input timing is set by a 25 fps cap (MEASURED 2026-08-24)

Every hold and sleep in `ts.ps1` was chosen to be safely large and never
measured. The number they were all groping for is the frame period.

**TaleSpire renders at ~24 Hz on this machine, so a frame is ~41 ms.**
Measured by `tools/probe_input.ps1 renderrate`, which holds a camera key so the
scene is continuously in motion and counts how many captured frames differ from
the one before: 118 captures in 2005 ms, 49 of them different, giving 24 Hz.
That is not a property of the hardware -- `TaleSpireSettings.json` carries
`RefreshRateSettingV0: 25`, and the measurement lands on it exactly. **It is a
setting, and raising it would make every one of these numbers smaller**; it is
the user's call, not ours, so nothing here changes it.

Everything else follows:

- **A press must span one frame.** Swept over twelve trials per value on the
  camera rotate: 0 ms registered 0/12, 10 ms 2/12, 20 ms 8/12, 30 ms 11/12 and
  **40 ms 12/12**. 40 ms is one frame. This is the real content of "the hold is
  not optional".
- **The screen answers in 42-55 ms** (median 53 over ten trials,
  `probe_input.ps1 latency`) -- one frame plus capture quantisation. Any fixed
  sleep longer than ~150 ms after a keystroke is waiting on nothing.
- **The oracle can see at 60 Hz and no faster.** `CopyFromScreen` of a 200x200
  patch and of a 400x400 patch both come back at 16.7 ms, because the desktop
  compositor bounds it; a full 1920x1080 grab is 34 ms. So measurements here
  resolve to one compositor frame, which is finer than anything TaleSpire does.
  A static board drifts by 0.49 on the diff metric, so a threshold of 2 is
  clear of the noise.

What this does **not** cover is the left press that commits a paste. It was not
measured -- that needs a scratch board to paste onto -- so `Press`'s 250 ms hold
is untouched and the 200 ms rule stands. A missed Ctrl+V is the most expensive
failure this tool has; `Send-Chord` is set to 120 ms (three frames) rather than
the 40 the measurement allows, deliberately.

## Creatures: picked up and carried, and the motion has to be REAL

`creatureCount` is always 0 in a v2 slab, so a scene pastes *marks* and the
minis go on by hand -- but the minis can be driven, and this is how.

**Read the interaction off the hint bar with the cursor over a mini**, which is
where it was found and is the one authority:

    [mouse] PICKUP CREATURE
    ALT   + [mouse] ROTATE CREATURE
    CTRL  + [mouse] ELEVATE CREATURE
    SHIFT + [mouse] TELEPORT CREATURE

So a creature is **picked up and carried**, not click-placed like a slab: the
button stays down, the mini follows the cursor on a leash line with a live
"N TILES" readout, and the release drops it. A click that goes down and up in
one place picks it up and drops it again where it was, which looks exactly like
nothing happening -- that cost three attempts before a mid-drag screenshot
showed the mini lifted with a "0 TILES" label under it.

**`SetCursorPos` is not enough to carry it, and this is the whole trick.**
`ts.ps1`'s `Drag()` walks the cursor with `SetCursorPos`. That works for the
*camera* -- verified both ways with `tools/drag_compare.ps1`, which orbits with
each method and gets the same 47-unit screen change -- but a carried creature
tracks pointer *motion*, and `SetCursorPos` teleports the pointer without
generating any. The A/B is unambiguous: same start, same delta, same gesture.

| motion | game's own readout | result |
|---|---|---|
| `SetCursorPos` walk | **0 TILES** | mini stays put |
| `mouse_event(MOUSEEVENTF_MOVE)` | **4.0 TILES** | mini lands at the cursor |

This is the same shape of failure as `keybd_event` with `scan = 0`, which this
file already records: the input looks right from outside and arrives as
nothing. `tools/creature_drag.ps1` is the implementation, and it keeps the
broken method behind `-Method setcursorpos` so the finding stays reproducible
rather than becoming folklore. Relative motion is subject to pointer
acceleration, so it overshoots by ~10%; the landing point is read back with
`GetCursorPos` rather than assumed.

**Ground truth is on disk, not on the screen.** TaleSpire persists creature
state under `primary/Persistence/<campaign>/<board>/Creatures/`,
zlib-deflated around a blob with the **same 0xD1CEFACE magic as a slab,
version 4**, carrying the content id and an f32 x/y/z.
`tools/creature_state.py` reads it. Two things about that store:

- **It is content-addressed and append-only.** A move writes a *new* file; the
  filename is a hash of the state, not a creature id. Three files on an
  untouched board are three saved states of one mini, not three minis -- which
  is exactly how it was misread first. **The current position is the newest
  file by mtime.**
- **It is written on a ~30 s tick**, not on the move, so a read straight after
  a drag still shows the old value. Wait for the tick before concluding a drag
  failed.

Verified end to end: a drag reading "4.1 TILES" in game moved the mini from
(1.5, 0.5, -4.5) to (5.5, 0.5, -3.5), and sqrt(3^2 + 1^2) = 3.16 for the
snapped cells against 4.12 for the carried point -- the readout is the live
carry distance, and the drop snaps.

## The board has three modes, and they name themselves

The three icons at the top centre are not decoration. Hovered, they are
**Exploration Mode** (footprints), **Turnbased Mode** (hourglass) and
**Cutscene Mode** (film strip); the active one is orange. This is board state
and it persists, so a board can be sitting in Cutscene Mode from some earlier
session -- `GRB/T14` was, which is why a "Grab Shot" panel was occupying the
right of the screen and read as clutter.

What changes with the mode, all observed:

| mode | on screen | carrying a mini |
|---|---|---|
| Exploration | dice tray + hotbar | works, "N TILES" readout |
| Turnbased | PREV / gear / NEXT, cyan ring on the active mini, dice tray | works, plus **dashed movement-range rings** at origin and destination |
| Cutscene | Grab Shot panel | works, "N TILES" readout |

**Turnbased Mode is only part-built in this version**: its bottom bar reads
"TURNBASED MODE - SETUP AN INIATIVE TRACKER AND SOME OTHER DESCRIPTIVE TEXT.
THIS SHOULD PROBABLY BE FETCHED FROM ONE PLACE", misspelling and all. Do not
plan a scene workflow around an initiative tracker that is not there yet.

**Build mode is a fourth axis, not a fourth mode**, and the top bar's
"[B] Build Mode" is a *button*, not a state light -- it looks identical either
way. The state is on the **hint bar**: camera bindings out of build mode,
`PICK UP OBJECT / MOVE OBJECT VERTICALLY / ...` in it. **Creatures render as
grey ghosts in build mode and cannot be picked up there**, so anything driving
a mini has to leave build mode first.

## Taking screenshots off a board (MEASURED 2026-08-24)

The README gallery is the one artifact this project has that a reader judges it
by, and every image in it is a hand-framed capture. Three things decide whether
a capture is usable, and only one of them is composition.

**Crop, do not try to hide the HUD.** `Space` does not clear the screen: it
toggles the left tool column and the dice tray / hotbar, and leaves the top bar,
the Role card, the Cutscene "Grab Shot" panel, the compass and the hint bar
exactly where they were. What works is a crop window chosen to miss all of them.
On a 1920x1080 client:

    tools\grab.ps1 -Name shot -X 200 -Y 150 -W 1250 -H 703

is clean 16:9 at native pixels. It clears the Role card (ends x=175), the Grab
Shot panel (starts x=1470), the compass and bottom bar (start y=955), the dice
tray (y=870) and the hint bar. Derive it from `ts.ps1 client` on any other
window size rather than reusing those numbers.

**Depth of field decides the framing, and it does not follow the camera.**
`TaleSpireSettings.json` has `DepthOfFieldSettingV0: true`. `Ctrl`+scroll moves
the camera without moving the focal target, so changing height throws the whole
frame out of focus -- not a soft far-field blur, the entire image. Dropping the
camera ten notches turned a sharp market square into mush, and going back up
ten did not reliably restore it. What does work:

- Find a height where the frame is sharp and then **fly horizontally** (`ts.ps1
  fly -Keys w -Hold 2.0`); WASD preserves focus, height changes do not.
- Judge focus on the capture, not on the composition -- the blur is even enough
  across the frame to be missed at a glance.
- Turning the setting off would remove the whole problem, but it is the user's
  graphics setting; ask before changing it.

**A vertical orbit that does nothing means the pitch is CLAMPED, not that the
drag failed.** Middle-drag with `-DY -110` and `-DY -250` both left the view
pixel-identical, which reads exactly like the dead synthetic drags this project
has chased before. `-DY 250` in the same session pitched the camera to near
plan immediately. The camera was simply against its upper pitch limit. The
diagnostic is one drag in the opposite direction, and it costs seconds --
`drag_compare.ps1` would have said the drag was fine.

Wheel zoom is a no-op at town distances, which is the cap this file already
records; `Ctrl`+scroll is the only height control that responds.

**The haze is board state, not the camera.** The left tool column's third icon
is **Atmosphere Settings**: Day Cycle with a sun dial, **Fog Multiplier**,
Exposure and Post Effects, plus "Apply to Game Board". East Tradebourne's warm
pink cast and the wall of fog a few hundred tiles out come from there. Nothing
in this session changed it -- a board's atmosphere is an authored choice -- but
it is where to go if a gallery shot needs the distance to read.

Five captures from one pass over East Tradebourne are in `docs/images/`:
`east-tradebourne-bridge`, `-plan`, `-waterfront`, `-market2` and `-quay2`.

## Chunking: layer first, region second

A map is split into **layers** before it is split into regions.
`build.LAYERS` is `landscape` (terrain, water, quays, trees, verges, seam
dressing) and `structure` (building shells, upper floors, porches, roofs,
towers, the town wall, and the signs and goods that hang off a building).
`Builder.layer()` tags a whole pass; the tag is recorded per placement at
`add` time, because a pass knows what it is building and an individual
`place_tile` does not.

**Why, structurally.** A paste comes to rest on whatever is under the cursor.
With region-only splitting, every chunk after the first is pasted over ground
the previous one laid, so each can land at its own height -- a whole quarter of
the map sitting a course above its neighbour with nothing wrong in the file.
The join shows as a step in open grass, which is the one thing a reviewer
notices. Put all the ground in one layer and it cannot disagree with itself,
whatever the paste does. Region splitting still happens *within* a layer when
it exceeds the byte cap; the pieces of one layer are the ones that share a
registration marker and a single paste height.

**Paste the landscape first**, onto bare board, then the structures over it.
`PASTE_HELP` says so and the chunk table is grouped in paste order.

**Verified end to end on Forest Church:** three landscape chunks onto a fresh
board, then two structure chunks straight over them -- no vertical nudging, no
per-chunk correction. The ground came out as one continuous sheet with no seam
anywhere, and the buildings seated flush, wall bases on the grass and doorways
at ground level. That is the first assembly this project has produced that did
not need a correction, and it is the argument for the split.

It also makes the build loop usable: re-paste the structure layer over the
landscape already down, instead of re-laying 20,000 grass tiles to look at a
roof change.

`SlabChunk.label` is `landscape-r02c03` (the filename stem) and
`SlabChunk.region` is `r02c03` (the grid cell) -- two chunks in different layers
cover the same region, and the map table wants to say so.

**A building's shell is never split across chunks.** Floors are landscape;
walls, upper floors, roof, porch, sign and goods are structure, and the
structure layer is two files on Forest Church. A paste that misses one leaves
every house in it as a bare floor -- a dark framed pad with a crate on it,
beside neighbours that are fine. (That was the first reading of "buildings
missing the mark"; the actual cause was the anchor slide described under
*Driving TaleSpire*, which leaves a strip of floor beside every house rather
than a whole one bare. Both produce four-leaf pads; count the pastes, then
check the pitch.) Every per-building pass tags its placements
with the building id (`Builder.group`), and `chunk_plan` assigns a tagged
placement by its building's low corner, in the grid bucket and in the
quadtree alike, so one file holds the whole shell. The chunk table prints
`N buildings` per structure file so a missing paste is diagnosable from a
screenshot: the pad's house is in whichever file did not land.

## Tiling: the anchor is a ray-hit, so never paste over anything

**SETTLED 2026-08-22, from a copy-out the user took off the board.** Matched
against the source files: the landscape layer landed **320 of 320 placements
exactly right**; the structure layer landed **+1.5 in y and a tile out in z**.
1.5 is exactly the height of the terrain surface under the cursor at the
anchor. The paste anchors on the cursor's ray-hit against *existing
geometry* -- so a layer pasted over another inherits its height, and on a
tilted camera its sideways slide too. That is what "every building floating
above and shifted from its target" was.

The layer split (landscape, then structure over it) *guarantees* that second
paste lands on the first. It solved terrain seams and created this instead.

**`build --by-region` is the answer**: cut by region with every layer
together, so the chunks tile the map without overlapping and each is pasted
onto ground nothing has been laid on yet. Nothing to inherit, so every chunk
rests on the board at the same height. `verify.chunk_datum` fails the build if
any tiled chunk does not reach y=0.

**The anchor is the bounding box's CENTRE (SETTLED 2026-08-22, measured).**
Held over open board, a 24x24 cobble pad came to rest centred on the cursor to
within half a tile -- not with a corner there. Everything else follows from
that:

- **Tiled chunks carry the map's two registration markers, same as the layers
  do**, so all nine present the identical box, all anchor on the same point,
  and **all go down at one cursor cell**. No measuring, no lining up by eye,
  no panning. Better still, an error in that single anchor is *common to all
  nine*: the map can land a tile off where it was aimed and still be perfectly
  assembled with respect to itself, which is the only thing that shows. The
  earlier scheme -- a pin at each chunk's own corner, placed by hand -- meant
  eight alignments at 17 px/tile against a 900 px window, each able to be a
  tile out. That was "a few tiles to the South East".
- **The anchor point must stay bare board for every paste**, or the last ones
  inherit a height. It sits at the middle of the map, so `_anchor_last` writes
  the chunk whose region covers it last; the CLI prints the files in that
  order and `TILE_HELP` says not to reorder them.
- **The box's extent has to be EVEN, or the snap has a tie to break.** Rounded
  out to the half-tile lattice the box was 189 wide, so its centre sat at
  x=94.5 -- exactly between two cells. Measured from two independent copy-outs
  of one paste run: `r01c00`'s props resolved at one offset and `r01c01`'s a
  whole tile east, so the tie had been broken both ways and the map had a
  one-tile step down the length of the c00/c01 join. `_even_ceil` rounds the
  far marker out to an even tile (190 x 184, centre 95, 92) and
  `verify.anchor_on_a_whole_tile` fails the build otherwise. Re-measured after
  the fix, a copy straddling the same seam matched **39 of 39 placements at
  one rigid offset, 19 of them owned by `r01c00` and 20 by `r01c01`**.

**What is measured about placing them (2026-08-22), and what is not:**

- **At one screen Y, the cursor step is exact and linear.** Two 24-tile pads
  pasted at the same screen Y and a measured step apart abut perfectly, with
  no gap and no overlap. Calibrate the step by pasting one pad and measuring
  its width in pixels: `out/calib24.slab.txt` is a 24x24 cobble pad with its
  NW corner blocked so the corner is unambiguous. Scale seen so far: ~30
  px/tile four wheel-zooms out from a fresh board, ~19 with the camera raised,
  ~16.7 at the top of the height slider.
- **Across screen Y it is not.** The camera keeps some perspective even
  pitched straight down, so a step that is exact at one screen height is a
  tile or two out at another -- two pads 720 px apart vertically came out
  ~57 px apart horizontally. **That is what "a few tiles to the South East"
  was.** Paste a whole *row* at one screen Y, then move.
- **Panning is not 1:1.** A right-drag of 462 px moved the world 245 px, about
  53%, so a pan cannot be used as a measured step. Pan roughly, then re-align
  the first chunk of the new row against its neighbour with the preview and
  step the rest of the row exactly.
- **Chunks are fixed-size squares in this mode** (`pack` is off), so the step
  is one constant per axis and neighbours complete each other's edges.

Sideways they are lined up **by eye against the grid, with the slab held**:
`ts.ps1 hold`, look, `ts.ps1 move` to nudge, then `commit`. That is what the
preview is for. Exact pinning is impossible anyway -- a pine on the map edge
overhangs the map, so the outermost chunks' geometry genuinely starts before
their own region.

Verified in game on a 48x48 sample in four regions: two chunks tiled side by
side sit on one level plane, no step at the join, buildings on their own
terrain, from four faces.

**One slab per building, when a paste has to be verifiable.** `build
--per-building` cuts the structure layer by building rather than by region:
one slab each, named for the building (`pb-structure-house-0005`), plus one
called `rampart` for the town wall, its towers and anything no building owns.
Forest Church becomes 55 slabs (3 landscape + 51 buildings + rampart) instead
of 5. Every slab still carries the map's two markers, so they all go down at
the same cursor cell -- and a building that lands wrong is re-pasted alone
while the rest of the town is untouched. The default remains a few large
chunks; this is the mode for checking, and `review.ps1 buildings` drives it.

Because the shell and the floor then live in different slabs, "the walls sit
on the floor" becomes a claim about two pastes agreeing.
`verify.shells_rest_on_their_floors` measures the lowest wall course of every
building against the top of the floor in the cell under it and **fails** the
build on any mismatch, buried or hovering.

**Probe with the palette the build used.** `cli build` constructs
`Palette.named(catalog, style, seed)`; under seed 33 both `floor` and
`floor_upper` resolve to `Rural Floor 01` (a framed four-leaf tile), while an
unseeded `Palette(catalog, MEDIEVAL)` resolves them to `Tavern Floor 01` and
`Rural Floor 02` (plank floors). Every probe and placement query run with the
unseeded palette was identifying the wrong tiles, and the board was read as
"floor pads where the file has none" for an hour. A size-coded probe -- pads
of 1x1, 2x2, ... per candidate, so the order cannot be misread, laid by the
palette's own `require` -- is the form that finally settled it.

**Open country at the edge rides along instead of being dropped.** Trimming
used to discard every edge chunk that held nothing but grass and trees: ten of
them on Forest Church, 1,618 assets, which on the board is not grass but bare
board -- a hard-edged notch in the south-west a quarter of the map wide. An
unpasted chunk is *nothing*. `_absorb_open_country` now fuses each trimmed
chunk into a kept chunk of the same layer that has room under the byte cap
(grid-adjacent first, then smallest), taking them inside-out so whatever still
has to be dropped is the outermost ring and never an enclosed hole. Forest
Church writes all 64 grid cells in the same five slabs; the largest went from
29,772 to 30,210 bytes against the 30,720 cap, so there is little slack left.

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
  `tools/wall_probe.py` and `tools/parapet_probe.py` build for this, and
  `tools/review.ps1 360` runs the pass so it is a command rather than a
  resolution: fresh board, paste, four faces at a low oblique, then overhead,
  eye level, and a cutaway. Caveat -- a `--crop` makes its own straight edge, so
  it cannot be used to review the *map edge*; crop inward, or fly the real
  border.
- **`N` toggles the cut box, and it is the check the wall probes were missing.**
  Views from outside can only tell you the faces close. The cut box removes a
  region so the *section* is on show, and a blade reads as a blade the moment
  you see it end-on. Confirmed on the rebuilt rampart: cut through, it is solid
  coursed stone layer on layer, which is a stronger statement than any number of
  exterior angles. It is also how buried geometry -- the tile seams `verify`
  warns about -- becomes visible, and eventually how interiors get reviewed.
  `ts.ps1 cutbox`. **It is a persistent toggle and it survives making a new
  board**, so a box left on from an earlier probe reads as a rectangular hole in
  the terrain with the trees still standing in it -- which is exactly how it was
  misread once. `review.ps1 360` turns it off again; anything driving it by hand
  has to as well.

- **A 4x4 tower piece is a quarter of an 8x8 tower.** `md_tower_wall_01`
  measures 4.0 x 2.0 x 4.0 and is not a ring course: stacked, it is a quarter
  shell, and `md_tower_floor_01` / `md_tower_crenelations_01` are fan-shaped
  quadrants. Four of each, rotated, make a drum eight tiles across -- a 40 ft
  keep on a 20 ft rampart, in a lighter stone than the rampart block. That is
  why the mural towers are square bastions of `city_wall_core`, two courses
  above the curtain, paved and battlemented with the wall's own pieces.
  `tools/tower_probe.py` is the probe, and it lays the bridge-deck candidates
  across one channel on the same board: the harbour deck tiles (1.0 tall,
  laid by their top so the underside rests on the water) read as a timber
  pier from every angle; a thin floor on dock legs floated, and a stone
  causeway read as a fortification. `bridge_deck` is pinned to
  `Harbor Middle 06`.

- **The kit is `folder`. Look it up; do not read the name.** Three separate
  wrong picks on this project were "what does this library actually contain"
  questions answered by building the wrong thing and looking at it.
  `tools/kit_index.py` is the lookup: it groups every tile by `folder` and
  reports, per kit, which of the roles the generator places it can supply at
  the right shape and height. `docs/asset-index.md` is the generated dump -- **regenerated locally, not
  committed**, because it is a 99 KB extraction of whichever TaleSpire packs
  the machine owns and is not ours to redistribute -- and
  `--kit` / `--role` / `--complete` are the queries.
  The three fields are not interchangeable and picking the wrong one costs
  hours: **`pack` is the DLC** ("Medieval Fantasy" covers castle, rural,
  tavern and thatch alike), **`group_tag` is a form** ("corner", "wall" -- the
  same tag covers castle stone, rural boarding and a spaceship bulkhead), and
  **`folder` is the family**, which is what the game's own asset library lists
  down its left-hand side. The name is not the kit either: `Village Roof Side
  Wall 02` sits in folder **Tavern**. Matching on the first word of the name
  looked for a corner called "village *", found none, and mitred one -- while
  `Tavern no floor (1x1 a)`, a 1x1x2.0 corner in the same kit, sat unused.
  The Tavern kit is in fact *complete*: wall, window, corner, inner corner,
  floor, roof, stairs and chimney, plus 2.5-tall Wall/Floor pieces that carry
  wall and floor in one casting. So does `Castle Fortified`, which is why the
  civic fabric already matched. Watch the classifier's one exception: those
  Village panels are tagged `group='roof'` because they ship in a roof set,
  and taking that at face value files the only 1-cell window in the medieval
  set under "roof".

- **A corner has to come from the wall's own kit, and sometimes there isn't
  one.** Under seed 33 the facade deals `Village Roof Side Wall 01/02` and
  every `wall_corner` variant resolved to `Rural Corner`: cream timber-framed
  panels with dark horizontal boarding at all four corners of every common
  house. `tools/corner_probe.py` builds the shape the generator actually makes
  -- a closed two-storey box with four outside corners -- one per candidate
  pairing, and read from two faces the mismatch is obvious from any angle.
  **There is no Village corner to find.** That family is entirely
  `group='roof'`: three flat panels, one of which is the only 1-cell window in
  the medieval set, and nothing else. Rural and Brick each ship a wall *and* a
  matching 1x1 corner, and neither has a 1-cell window -- which is why the
  facade is Village to begin with. So `_usable_corner` now requires the
  corner's kit to match the wall's (`_kit_of`, first word of the name, because
  `group` names a form and not a family), and where they disagree the corner
  is dropped and the cell falls back to a panel per exposed side. Mitred, the
  Village panel's own edge timber meets its neighbour as a corner post and
  reads clean. Civic keeps its corner piece: `castle wall 1x1` and
  `castle wall corner 1x1 base` are one kit already.
  **The cost is buried geometry**, which is what the full-cell corner was
  introduced to avoid: two panels in one square took `tile_interpenetration`
  from 265 pairs to 1,138. It warns rather than fails, and the harm it names
  -- "a seam that shifts with the camera" -- comes from *coplanar* faces;
  two panels meeting at right angles interpenetrate without ever showing one.
  Worth teaching the check about perpendicular corner panels before the count
  hides something real.

- **The storey is the wall, and everything about the roof line follows.**
  The pitch used to be wall+floor, leaving a floor-thick slot between wall
  courses for the deck to drop into. Two defects came out of that gap, and
  they are the same defect:
  * **the floor showed from outside.** A deck fills its whole cell, so in that
    slot its edge sat flush with the wall face -- a band of floorboards
    running right round every building between storeys.
  * **the roof floated.** The roof seats at `floors * storey_h`; the head of
    the top wall is at `(floors-1) * storey_h + wall`. Those are equal only
    when the storey *is* the wall. Pitched at wall+floor they differ by
    exactly a deck, and the attic deck had been quietly filling the gap --
    so taking the attic deck away left a half-tile of daylight under every
    roof. (That was mine, and it shipped.)

  Pitched at the wall alone the courses touch, the facade is unbroken from the
  ground to the eaves, and the roof seats by arithmetic rather than by luck.
  Decks go on **interior cells only** -- cells with no exposed side -- so no
  deck ever reaches a facade; the cost is an upper floor stopping one cell
  short of the wall, which shows only through a window. And **an attic gets no
  floor at all**: the roof seats on the wall head, so a deck at the top storey
  only floors the roof void. Forest Church went from 30,565 assets to 27,964.
  `tools/storey_probe.py` puts the three stacks side by side;
  `test_the_roof_sits_on_the_wall_head` and
  `test_no_upper_deck_reaches_the_outside_of_a_building` guard the result.

- **The hip convention is PER KIT, and there are four roof materials, not
  one.** `ROOF_EDGE_ROT` / `ROOF_CORNER_ROT` were read out of one
  community-built cottage, and that cottage is thatched. Nothing had ever
  checked whether another kit shares the convention. None of them does:
  dropped onto Village pieces the Thatched rotations produce a rank of red
  fins. Measured with `tools/roofrot_probe.py --hips`, which lays the same 6x6
  hip four times, once per quarter turn, so exactly one closes:

  | kit | edge | corner | material |
  |---|---|---|---|
  | `Rural` | +0 | +0 | thatch (the baseline) |
  | `Tavern` | +6 | +6 | terracotta tile |
  | `Castle Fortified` | +6 | +0 | brown shingle |
  | `Abandoned Village` | +6 | +0 | grey slate |

  `build.ROOF_ROT_OFFSET` holds it, keyed on `folder` -- **the kit is the
  folder**, the same rule that found the facade's corner. `roof_set` picks the
  material per tier and `_roof_piece` takes the turn.

  **I got this wrong first and wrote the wrong thing here.** I tried one
  guessed corner per kit (`Skirt_1x1_corner out` for Castle), saw fins, and
  recorded "only Thatched has a 1x1 hip vocabulary; the others are 2x2 kits
  with 1x1 offcuts; do not fix the rotations, the pieces are not there." Every
  clause of that is false. Tavern ships the same five pieces as Rural, one for
  one -- slope, corner, inner corner, flat cap, chimney -- and Castle has two
  1x1 corners I never tried. **The user pushed back with "it seems to me the
  pieces exist, but the rotations are wrong", and was right.** The failure was
  reporting one guess as a survey; the fix was an artifact that sweeps the
  whole space instead of asserting about it.

  Two probe-reading rules came out of the same session:
  * **Run one dark kit at a time against the tan Thatched control.** Castle
    and Haunted are both dark weathered timber and were told apart on a
    four-kit board only by counting a tally stack at a grazing angle, which
    read wrong. `roofkit_probe.py --kits thatched,castle` makes it a colour.
  * **A hip is judged in plan, so number the candidates on the ground.** A
    vertical tally stack reads at an oblique and vanishes from overhead; a bar
    of N cells running east is unmistakable. Zoom-out is capped, so size the
    probe to fit one frame rather than expecting to fly around it.

- **The last roof course is a ridge CAP, not another ring.** `_roof_rings`
  steps every course one cell in and one rise up, and the top ring was being
  roofed in slopes like any other -- so along the apex you saw their
  undersides, bare timber the length of every ridge. The cap piece goes on the
  top ring instead, seated so its **top** is flush with the ring height
  (`y = roof_y + r*rise - cap.size_y`), which is the same rule
  `Builder.surface()` follows for anything laid flat. Read off a hand-built
  correction the user made to one of these roofs and decoded: the caps sat at
  0.5 where the ring would have been 1.0. Ridge tiles lap from the ends
  towards the stack, so the caps mirror about the chimney
  (`_ridge_rotations`), and the chimney is two courses lapped `CHIMNEY_LAP`
  rather than one piece sitting on the ridge as a stub.

- **A gate passage has to be cut SQUARE, or it can never have a door.** The
  raster used to clear a *disc* of wall cells, which on a circuit that runs
  diagonally leaves jambs that are a 45-degree stair-step: on Forest Church an
  18-cell hole with a 7x4 bounding box. `Door - Portcullis double` is flat and
  4 wide and had nowhere to sit, which is why "no portcullis" stood in the
  backlog for eleven revisions as an *asset* problem when it was a *raster*
  problem. `raster._carve_gate` cuts a rectangle along whichever cardinal is
  closest to the wall's own normal -- measured from the band around the gate,
  because MFCG puts this gate on a ring *vertex* and a vertex has no
  direction. Two straight jambs, a 4-cell carriageway, one grille.
  **Read the opening axis off the jambs, not the bounding box**: a square
  passage through a band its own width comes out 4x4 and the box has no long
  axis to pick from.
  `GATE_HEADROOM_TILES` is 4.0 and the number is set by the door, not by
  taste: the grille is 3.75 tall, so a 15 ft opening drives it three quarters
  of a tile into the lintel.

- **Nothing could get onto the wall-walk.** 341 cells of paved, battlemented
  rampart 35 ft up, with no stair, ramp or ladder anywhere -- a defenders'
  platform no defender could reach. `verify` did not catch it because its
  access check asks whether *buildings* can be entered, not whether the wall
  can. `_lay_wall_stairs` runs one flight per tower, filled solid underneath.
  Where a flight goes was wrong three ways, and each is now an invariant with
  a test:
  * **Inside, as a hard filter and not a preference.** A stair on the field
    side of a town wall is a siege ramp for the enemy. It started as a term in
    the score, and on Forest Church one tower had *no* inside option under the
    old perpendicular scheme -- so it scored the field and built there.
  * **Parallel to the curtain, not perpendicular.** The run used to march
    straight out from the tower's face into the town: it hugged the wall for
    one cell in six and ate 35 ft of street. Flights now search the inner face
    near the tower for the straightest stretch, which is where a real rampart
    stair goes. Score on *distance* to the wall, not on a count of orthogonal
    touches -- beside a stair-stepped diagonal a straight flight touches on
    alternate cells, so two runs that both read as hugging scored 2 and 4 of
    six for no visible reason. Forest Church: every tread within 2 cells,
    mean 1.47.
  * **Land against the CURTAIN, never a tower.** A tower crowns
    `WALL_TOWER_RISE` courses higher, so a flight arriving at its flank stops
    ten feet below anywhere you can stand. A `city_wall_walk` tile caps the
    top tread so the landing is flush rather than half a tile down.

- **A tower footprint is not always part of the mass.** `pick_wall_towers`
  lets a tower stand on open ground beside the wall (`usable` accepts any
  unblocked surface), so a cell can be tower-but-not-wall. Excluding only
  `mass` when siting stairs put three treads exactly where a tower was about
  to be built -- entombed in solid block, invisible in the file and on the
  board. Anything that reserves ground near the circuit has to exclude
  `tower_cells` as well as `mass`.

- **The rampart is built SOLID, and the buried core is deliberately not an
  optimisation.** 38% of the body cells have no face anyone can see, so their
  lower courses were dropped for a while: 495 blocks, 1.8% of the board, and
  it read fine because the faces seal the void. It also empties exactly the
  cells `verify.town_wall_gaps` samples (mid-second-course), and that check
  cannot tell a sealed void from daylight straight through the circuit -- it
  exists because see-through wall shipped once, on 1,234 tiles. Reverted;
  `test_the_rampart_is_built_solid_all_the_way_through` holds the line.
  A hollow core is also a trap for whoever next cuts a postern.

- **A wide gate apron paves the ground a tower needs.** The postern's approach
  was first paved as a 5x5 halo round every gate cell; `pick_wall_towers`
  rejects paved ground, so the circuit went from five towers to two. The
  approach is paved along the passage line only.

- **A porch needs a storey to carry it.** The hood seats at `storey_h + 0.5`,
  which on a one-storey cottage is level with its own eaves -- a second roof
  grafted onto the first. Single-storey buildings get a lantern on the
  doorpost instead (`_hang_lanterns`), and never both a lantern and a sign:
  a signed trade already says who it is.

- **A corner piece eats the facade on a small footprint, so a glazing *rate*
  cannot say much.** Windows are dealt one-in-N per segment, and on Forest
  Church the front face of a trade building came out 27% glazed against a
  one-in-2 rate. The hash is exact (checked); the loss is corners and doors.
  A median footprint is 28 cells, so a face is five or six cells of which two
  are corner pieces and, at ground level, one more is the door. Widening the
  rate gap between tiers therefore buys less than it looks like it should.

The general form: **an asset's `ColliderBoundsBound` is data, and shape
assumptions are bugs waiting for a big enough map to become visible.**

## Building style: four tiers, and what each axis is allowed to carry

Style used to be one binary -- `kind in CIVIC_KINDS` -- plus a three-way wall
deal that collapsed to two near-identical panels, because
`Palette.resolve(role, v)` seeds a choice *inside the first matching query* and
that query pins two names. Measured on Forest Church: 5 civic, 46 identical.
Every roof on the map was `Thatched Roof 01`, since `_lay_roofs` resolves the
roof set once for the map rather than once per building.

`tier_of` now deals four fabrics -- civic 5, trade 12, common 28, utility 6 --
and each axis carries a different thing, rather than all three fighting over
the wall material:

- **Importance (kind) -> the wall kit.** The tier decides the *whole* facade,
  because a facade that changes material at the corner reads as a mistake.
  Utility (warehouse, stable, shed) is Rural boarding, one storey, no glass:
  **that kit's missing window is the reason it is right here and wrong
  everywhere else.** Trade shares the house's wall, because exactly two 1-cell
  windows exist in the whole Medieval Fantasy pack (Tavern's and the castle's)
  -- so a tier that wants glass is built from one of those two, and trade is
  told apart by its door and its street front instead.
- **Where -> the glazing, not the material.** `GLAZE_RATE` is keyed on which
  face a segment is on: dense at the front, sparse on the flank, **never on
  the back**. Windows used to be dealt by a hash over every exposed segment,
  so a town looked identical from all four sides.
- **Frontage -> the show facade.** `_main_street_frontage` reads
  `tm.street_class` at each doorway; 8 of 51 buildings front the through road
  and get one step denser glazing.

**Ward is not a usable axis and it is worth saying why.** 47 of Forest
Church's 51 buildings fall in a single ward, and `inside_walls` is true for
exactly one -- correctly, since that export's "wall" is a small citadel ring,
not a town circuit. A district-keyed style would be a no-op dressed as a
feature.

**The storey cap belongs in `storeys_of`, not at the shell.** Three passes read
it -- the shell, the upper floors and the roof -- and capping only the shell
leaves the roof three courses up with nothing under it. Same lesson as
`footprints` and *where* a building is.

## Check the community before reverse-engineering the host

**The paste-anchoring work should never have happened.** Several sessions went
into measuring how `Ctrl+V` seats a slab -- the ray-hit anchor, the slide on a
tilted camera, the bounding-box centre, the even-extent tie -- and then into
the machinery that works around it: two registration markers per chunk, the
`_even_ceil` box, `_anchor_last`, `paste-order.txt`, `chunk_anchors`,
`chunk_datum`, `anchor_on_a_whole_tile`, and the camera discipline in
`review.ps1`.

LordAshes' `MultiPasteSlabsPlugin` and `SlabPlugin_CCM` (Thunderstore, BepInEx)
have read a JSON document that states each slab's position for years::

    {"autoDrop": true, "dropX": 0, "dropY": 0, "dropZ": 0,
     "slabs": [{"code": "<base64>", "offsetX": 0, "offsetY": 0, "offsetZ": 0}]}

`slab.multislab()` emits it and `build --multi-slab` writes it. Because a map
is normalised **once** and chunks keep their true in-map coordinates, every
offset is zero and `drop` alone moves the town -- the integration is a dozen
lines. That mode also skips the markers, and `cli` skips the three
registration checks there, since there is no shared box to agree on.

The rule: **before reverse-engineering a host application's behaviour, spend
twenty minutes on its modding community.** Thunderstore, its GitHub org, and
the community wikis. Other things found the same way, worth knowing:
Tales Tavern's asset archive browses every in-game tile *with pictures*, which
would have shortened several of the probe sessions below; `SlabelFish` and
`talespireDeserialize` are existing Python slab codecs; `TaleSpire_Generator`
already reads `index.json` and emits slabs (terrain only -- its city generator
is listed as planned).

**The vanilla path stays the default**, because the plugin needs BepInEx and
breaks on game updates, and a colleague cloning this should not have to mod
their game to see a town. None of the registration machinery is deleted.

### The asset archive is now wired in (2026-08-24)

That paragraph named Tales Tavern and then did nothing about it for months,
which is the same failure it is warning about. `tools/asset_shots.py` closes
it: give it a role, a kit or a name and it prints the archive page for each
candidate beside the catalog's own dimensions.

- **The slug rule, measured against the site's sitemap (3,596 pages).** Name
  lowercased, runs of non-alphanumerics to one hyphen, **underscores kept**.
  That exception is the whole thing -- `castle merlon 1x1` ->
  `castle-merlon-1x1` but `md_wall_1x1_diag_01` -> `md_wall_1x1_diag_01`.
  Hyphenating underscores too resolves 77.2% of the catalog; keeping them
  resolves **95.9%**, and **100% of every kit the generator builds from**
  (Rural, Tavern, Castle Fortified, Abandoned Village, MegaDungeon,
  CastleRuins, Harbor, Furniture, Food & Drink). The 131 misses are creature
  minis and twelve oddly-punctuated Doors.
- **The link is self-verifying, which is the only reason a slug rule is
  allowed.** Each page carries the asset's UUID *in our own namespace* --
  `castle-merlon-1x1` reports `fc6e9582-...`, exactly what our catalog holds --
  so `--verify` checks the link landed on the asset we meant instead of
  trusting the rule.
- **It reproduces two of this file's own hard-won findings from the render
  alone.** `castle merlon 1x1` is visibly boarded timber on a stone base -- the
  hoarding that crowned the circuit in crates for eleven revisions.
  `md_tower_wall_01` is visibly a curved quarter-arc -- the "quarter of an 8x8
  drum" that `tower_probe.py` was built to establish.
- **371 of 3,200 assets share a name with another asset** (139 names, up to
  nine deep -- `Aberration Floor 2x2` spans 2x0.5x2 to 2x2.5x2). Of the pieces
  the generator pins, only `md_wall_1x1_diag_01` is ambiguous, and it is
  already rejected -- but this is the quantified form of "asset names are
  inconsistent", and it is why nothing should be keyed on a name. `--verify`
  prints AMBIGUOUS for a duplicated name and MISMATCH when the page's UUID is
  the *other* one, which is exactly what it does on that asset today.
- **It emits links; it does not mirror.** `docs/asset-index.md` is regenerated
  rather than committed because it is an extraction of someone else's packs,
  and someone else's renders are more so. The site's REST API is closed to
  unauthenticated callers and nothing here goes near it; `robots.txt` is
  `Disallow:` (empty) and advertises the sitemap, which is the sanctioned
  route, used one polite request at a time behind `--verify`.

**This does not replace a single probe, and none was deleted.** A render
answers *what is this piece*; it cannot answer which quarter turn closes a hip
(`roofrot_probe.py`), how a bed reads through translucent water
(`water_probe.py`), or whether a run one cell thick shows daylight
(`wall_probe.py`) -- and "a probe read from one angle is a probe that lies"
applies to a render read from one angle most of all. The archive is the
**shortlist**, the probe is the **verification**, and the standard above --
orbit four sides plus overhead, keep known-bad pieces as controls -- is
unchanged. What it buys is probing three candidates instead of eight, and never
building a slab for a piece a picture rules out in a second.

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
6. `enclosed_voids` built its barrier from each written chunk's `(row, col)`.
   Packing fuses a run of cells into one chunk named for its first cell, so a
   five-slab plan over 64 cells presented five barrier cells and the flood
   walked through the other 59: on a packed plan the check could never fire.
   It reads `covers` now.
7. The off-grid canary had a standing exception nobody had run it against: the
   far registration marker hugged a pine canopy's fractional overhang at
   x=187.51. It is rounded out to the half-tile lattice; the canary prints 0
   on every chunk again, and it should be run on every chunk, every build.

`verify.check_placements` and `verify.enclosed_voids` measure emitted boxes and
the written chunk plan respectively. **New checks go there, not into the
TileMap pass.** `verify._Occupancy` reconstructs solid geometry from the
placements and is the tool for "is there actually anything here".

**OPEN: `entombed()` hollows the rampart and the masonry check catches it.**
Bisected on Forest Church, which is an MFCG map, so this has nothing to do with
the second import format:

| commit | `entombed()` | masonry check | Forest Church |
|---|---|---|---|
| `93ccba6` cap the ridge | absent | present | **clean** |
| `8412ce9` square the gate, stairs on the wall | **added** | present | 85 of 300 holed |

`_lay_city_wall`'s `entombed()` lays **only the top course** in a wall cell
walled in on all four sides, because nothing can ever see the rest -- 111 of
Forest Church's 300 body cells. `check_placements` samples **mid-height of the
second course**, so every entombed cell reads as a hole; the first one it
reports, (82, 85), is entombed. East Tradebourne is 732 of 2367, and the count
scales with wall thickness because a thicker band entombs more cells.

**The check is the older thing and it is doing its job.** It passed before the
optimisation landed, so this is a regression rather than two defensible rules
disagreeing -- the resolution is on the `entombed()` side, most simply by
keeping the course the check samples as well as the one the walk rests on.
Cost is small: 111 cells x 4 courses on Forest Church, and about 3,900 blocks
on a 387,381-asset East Tradebourne.

Until then a walled town still builds and pastes, and the void is invisible:
the rampart was read from a low oblique outside, from plan and at eye level on
`Probe - East Tradebourne rampart`, and it is unbroken coursed stone from
ground to parapet.

Corollary that cost an hour: **when a measurement looks wrong, check for stale
artifacts before debugging the code.** A tree-species count read 80/15/5
against a 62/30/8 target purely because a previous build's chunk files were
still in `out/`.


## Scenes: one building, and the board the party stands on

`citysmith scene` takes one building out of an imported town and builds the
board a session happens on -- the interior, an apron of ground, four marks on
the floor where the tokens go, a manifest, and a GM brief.
`docs/scenes.md` is the user-facing guide; what belongs here is what the first
one on a real board taught, because none of it was visible from the files.

**The export has no occupants, and that was checked rather than assumed.**
Across all three FTG exports every `BUILDING` feature carries exactly `id`,
`type`, `name`, `buildingType` and `material` -- 1,007 of them in East
Tradebourne, no sixth key. MFCG has no names at all. So a roster is *derived*
from the trade, the authored name and the footprint, stably per seed, and the
brief says so on the page rather than presenting it as exported. An authored
sidecar keyed on building id (`interior.load_roster`) wins where it has an
entry.

**Levels go side by side, not stacked.** Same argument as the roof being off:
TaleSpire cannot hide an upper floor, so a stacked inn is one visible attic and
two rooms the camera has to be flown inside to use. `Floorplan.rect_on(level)`
is what makes it work -- every pass that asks "is this cell on the outside
wall" now asks it of the level's own rect, and `build_interior(stack=False)`
builds each at ground height.

Three things the first scene on a board showed, in the order they were found:

- **`wall_interior` was the last place `md_wall_1x1_02` survived.** MegaDungeon
  masonry -- the same deep-relief blocks taken off the facade for leaving a
  jumbled seam at every join. Read from overhead, the four rooms of a cream
  timber-framed tavern were a heap of pale rubble filling the floor. It could
  not have shown up before: a town's partitions are inside a closed shell with
  a roof on. It is `Village Roof Side Wall 01` now, the facade's own panel.
  **The kit rule reaches inside the building too.**

- **The campaign board list clips a row at SIXTEEN capital letters.** Measured
  by renaming a board to `ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop` and
  reading the list: `ABCDEFGHIJKLMNOP...`. The clip is on pixel width and the
  list is set in small capitals, so mixed case reaches about twenty-two --
  `The Halfling and the Fox - Graybank interior` shows as
  `The Halfling and the F...` -- but sixteen is what to design to. So
  `Interior - Graybank - <anything>` is one row repeated, in the one list that
  is the only thing TaleSpire will tell you about a board.

  So the name leads with a town tag and a building code:
  `GRB/T14 The Halfling and the Fox Interior`, which the list shows as
  `GRB/T14 The Half...`. The tag is three letters derived from the town name;
  the code is the kind's initial and the building's number, and **the number
  alone is already unique** -- both importers number every footprint from one
  global counter, so `tavern-0014` and `temple-0014` cannot coexist. Zero
  clashes across all 1,227 buildings in the four towns here.

  What that replaced added a number only when two buildings shared a *name*,
  and appended it. Both halves were wrong: 44% to 77% of buildings in a town
  share their first sixteen characters (`Residence` occurs 129 times in East
  Tradebourne), and an identifier at the end is one the list never shows.
  `scene.VISIBLE_CHARS`, and `docs/scenes.md` has the whole scheme in a table.

- **The same plan did not build the same board twice.** `_interior_walls`
  returns a set keyed partly on a string; Python randomises string hashing per
  process, so the partitions were emitted in a different order every run --
  231 placements, identical multiset, different bytes. Every scene therefore
  read STALE after a rebuild that changed nothing. Sorted at the source, and
  `boards.digest_of` hashes the decoded placements rather than the file,
  because a digest that holds only while every set happens to iterate the same
  way will lie again later. **A process fixes its hash seed once, so the whole
  suite was blind to this**; `tests/test_determinism.py` builds in two
  subprocesses with different `PYTHONHASHSEED`.

**A big building needs a different plan, and the BSP does not scale.** Rooms
per level over East Tradebourne's 991 buildings, by footprint: 3.3 under 50
tiles, 5.0 at 50-80, 6.5 at 80-120, 10.8 at 120-200 and **23.5 above 200**. A
29x15 warehouse -- 145 x 75 ft -- was 31 rooms of about 15x25 ft, four purpose
names cycled seven times each, 52 doorways, no room bigger than any other.
`floorplan.hall_layout` plans around a principal space instead, and **which
form it takes depends on the proportions, which is the part I got wrong
first**: a nave running in from the door suits a deep building, but a wide
shallow one wants a broad hall spanning the entrance wall. Entered from its
long side, the warehouse was given a 10x10 nave -- 23% of the floor, ringed by
335 tiles of service rooms. Either form touches the wall the door is in, so
everything else opens off it; an upper level narrows the hall to a
`CORRIDOR` and becomes a landing. Doors follow (every room onto the hall: 52
became 8), and names follow (`purposes[i % len]` gave a floor seven Offices;
the principal name is spent once and the menus are long enough for a hall
plan). After: 6.5 rooms above 200 tiles, biggest room 49% of the floor, and
every room name unique within its level across all 1,176 buildings.

**The tier picks the whole interior fabric, not just the wall.**
`build_interior` read `wall` and `wall_interior` and nothing else, so a
dressed-stone temple had Village partitions on Rural planking -- three kits in
one room, which is the facade rule unlearned indoors. `interior_fabric()` is
the per-tier set, and it added `floor_civic` because the floor was the one
surface the kit rule had never reached. Windows were missing entirely: every
interior wall blind, on a board whose purpose is being looked into. Storeys
were capped at 2 while 352 of 1,176 buildings have three.

**`campaign/boards.json` is the only record of which board holds what**, and
there cannot be another: the campaign list has no size, no date, no contents
and no API. Four states -- NEW pastes, READY switches, STALE *still reuses*
(a board is where something happened, and there is no erase, so `-Rebuild`
makes a second board and leaves the first), MOVED reports that a re-import may
have renumbered the building underneath the id. Nothing in this deletes a
board.

**Furnishing is measured but not done, and `docs/interior-slabs.md` is the
evidence.** Decoded from the community slabs in `library/` (2,382 interior-kit
props) plus two published interiors: **0.1% of hand-placed interior props sit
on a cell centre** and **84% are on a quarter turn**, against our 100% centred
and uniformly random over 24 steps. One placed prop in five spans more than a
cell -- 67% of the `Furniture` kit does -- while `_dress` centres every prop in
one cell at a random angle, which is the prop version of the shape-assumption
rule this file states for tiles, and the likeliest cause of furniture reading
as debris. Props also *stack*: two thirds of them sit on a table or a shelf
rather than the floor, and `Food & Drink` is 44/45 smaller than a cell because
it is tabletop dressing. Density runs 0.41-0.66 per cell against our 0.12.

Worth knowing before spending a session on it: **an unfurnished interior with
named rooms is one of the two forms people publish**, not a failure state.

## A feature can be correct and absent, and nothing used to say so

**Fences were built, shipped, reviewed over two sessions and written up while
being absent from every board looked at.** Both crops chosen to review them
were dense town centre, where a field boundary does not go: 22 runs on East
Tradebourne, **zero in either frame**, and no part of the build report
mentioned it. The code was right, the screenshots were real, and the conclusion
drawn from them was worthless.

`verify.feature_report` is the answer, and it asks a different question from
every other check here. The others ask whether the geometry is *correct*; this
asks whether it is *there*, by comparing what the input offered against what
the output used:

    offered and built      -> ok
    offered and not built  -> FAIL, something is broken or switched off
    not offered            -> ok, and say which, because "no fences in this
                              crop" is a fact about the map and not about the
                              code

On the block that fooled us it now prints *"field walls: none here; the layout
has 21, all outside this crop"*, which is the sentence that was missing.
`tests/test_features.py` covers all three branches, including the fail.

## What was designed but not built is tracked, and the tracker is checked

**"Still open" is not a state anything verifies.** Yards were designed in
`docs/building-massing.md`, listed as outstanding in two documents, twice
described as "waiting on nothing", and deferred by three consecutive passes --
because a paragraph of prose saying a thing is unbuilt looks exactly like a
paragraph saying it is built. This file's own doc index called three finished
designs "not built" at the same time.

`tasks.json` is the record and every entry carries **evidence**: the dotted
path of the symbol that exists when it is done, or `test:<name>`.

    citysmith tasks              grouped by state
    citysmith tasks --check      import every claim and report the liars

A task marked `done` whose evidence is missing is a false claim; one marked
`open` whose evidence already exists is stale bookkeeping. Both are reported,
and `tests/test_tasks.py::test_the_shipped_backlog_is_honest` runs the same
check in the suite, so the file cannot quietly drift from the code.

Two things learned seeding it:

- **A test that already exists is not evidence that a bug is fixed.** The
  rampart task first pointed at `test_the_rampart_is_built_solid_all_the_way_through`,
  which passes today while `entombed()` still hollows the wall. Evidence has to
  be something that can only exist *after* the work.
- **The evidence field is the design review.** Writing one forces you to name
  what the finished thing is called, which is most of deciding whether the task
  is real.

## Testing

`python -m pytest -q`. Tests assert invariants (no overlapping buildings, no
unreachable rooms, walls resting on floors), not exact output — so generator
aesthetics can change freely while real bugs stay caught. `tests/fixtures/*.slab`
are genuine TaleSpire slabs and are the ground truth for the codec.

## Not built yet

- Local UI. Deliberately deferred; `cli.py` is a thin shell over the core
  modules so a UI can be added without touching generation code.
- Creature/mini placement (`creatureCount` is always 0 in a v2 slab). This is
  why a scene pastes *marks* rather than a party: four contrasting floor tiles
  by the door, and the minis go on them by hand.
- Switching to an existing board, unattended. `scene.ps1 enter` drives
  everything else and stops at exactly one place, because the campaign list
  re-sorts on every rename and nothing here can read text off the screen. Needs
  either OCR or a keybind we have not found.
- A tapered map edge. The border ring still ends on a hard straight cut, so
  the map reads as a cropped rectangle from outside (finding 8).
- A portcullis winch, murder holes and an arch ring. The gate has doors now
  (the passage is cut square, so the grille finally has jambs to hang on) but
  the opening is still a plain rectangle in the masonry.
  `city_gate_arch` is pinned to `Castle Ruins Arch 02` and remains *unused*:
  it is 1 x 3 x 2, so it is a tunnel segment rather than a jamb piece, and
  four would be needed to span the carriageway. It has never been probed --
  do that before placing it.
- Gate doors. The gate has its towers now -- square bastions on both jambs,
  and on every corner of the ring (`pick_wall_towers`; the raster records the
  ring's vertices in `TileMap.wall_corners`) -- and three tiles of headroom
  under the lintel. What it does not have is a portcullis or an arch, and the
  reason is the data: MFCG puts Forest Church's only gate *on a vertex* of the
  ring, where the road ends, so the raster's disc clearing removes the corner
  and the opening faces north-west across a 45-degree stair-step. A flat
  4-wide panel (`Door - Portcullis double`, pinned and unused in the palette)
  has no straight jamb-to-jamb line to hang on. A gate cut through a straight
  run of wall would take one; nothing builds it yet.
