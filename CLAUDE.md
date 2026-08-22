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
- **The modifiers for a held object, from the game's own hint bar:**
  `Ctrl`+scroll moves it **vertically**, `Shift`+scroll moves it **on the
  plane**, `Alt`+scroll **rotates in place**. `raise`/`lower` were built on
  Shift, which is the horizontal one -- so every "nudge it down a course" test
  in a whole session was sliding the slab sideways and reading the result as
  evidence about height. `ts.ps1 nudge -Mode vertical|plane|rotate`.
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
  `M` (bottom right, a ruler icon) is still unidentified. Until this works,
  the fallbacks are the user copying a region by hand, and the angle test
  below.
- **The right-hand vertical track is a camera *height* slider, and it goes far
  higher than the wheel.** Zoom-out is capped well short of a 187-tile map;
  raising the camera is how a whole quarter fits one frame, which is what a
  paste wants -- the chunk being placed and the chunk it must line up with both
  on screen. `ts.ps1 camera -DY -300`. The handle moves with the height, so the
  command scans the track column for it rather than assuming a position.
- **Read the camera back rather than tracking it in your head.** Every camera
  command is a *relative* move; a session that only issues them ends up over the
  void wondering where the map went. `ts.ps1 camerastate` reports the height
  slider's handle position -- numeric and comparable between calls -- and saves
  a crop of the compass rose, which gives bearing by where N points and pitch by
  how squashed the circle is. Check it before concluding something is missing
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
- Bindings worth knowing: `B` build mode, `F1` help (a video overlay — it does
  not screen-capture), `F2` recentre, `Space` menus, `Ctrl+Z` undo,
  `X`+drag select, left-click pick up, middle-drag rotate camera, scroll zoom,
  `Shift`+scroll raise/lower.

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
rests on the board at the same height. What they need is not a shared box but
a shared **datum** -- one marker per chunk at the map's global floor, so a
chunk with a deep riverbed still measures from the same level.
`verify.chunk_datum` fails the build if any tiled chunk does not reach y=0.

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
