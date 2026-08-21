# Brief: autonomous iteration on citysmith

You are picking up a TaleSpire map generator mid-flight. This brief exists so
you do not re-derive what has already cost days, and do not repeat mistakes that
are written down. Read `CLAUDE.md` first — it is the engineering notes and it is
current. This file is the *situation*: what is true right now, what is broken,
and what to do next.

## What the project does

`citysmith` turns a Watabou MFCG town export into a TaleSpire board. Source of
truth is `samples/forest_church.json`; `python -m citysmith import` rasterises
it to `out/layout.json`, `python -m citysmith build` emits `.slab.txt` chunks,
and those chunks are pasted into TaleSpire by driving its UI. Pasting is the
only ingestion path. There is no API.

```bash
python -m citysmith import samples/forest_church.json
python -m citysmith build out/layout.json --stem forest --seed 33
python -m pytest -q
```

## The one rule that matters most

**Measure the artifact, not the plan.** Every serious error in this project's
history came from checking a model instead of the thing. `CLAUDE.md` lists five
instances. The current frontier of that rule:

- The **slab files** are not the board. A file can be perfect and the board
  wrong, because pasting can move things. Reading the file proves nothing about
  what a player sees.
- A **screenshot from one angle** is not the board either. Three wall blocks in
  a row were approved from views where each hid its own holes.

So: for geometry questions, build the shape the generator actually builds, orbit
all four sides plus overhead plus a cutaway (`tools/review.ps1 360`). For
placement questions, get the board back out and measure it (see *Open problem 1*).

## Current state

Verified working, do not re-litigate:

- Chunks are split **by layer first, region second** — `landscape` and
  `structure` (see `CLAUDE.md`, "Chunking"). This removed terrain-meets-terrain
  seams structurally: all ground is one body, so it cannot disagree with itself.
- Rampart mass is `md_stairblock_01`, a plain solid cube, verified solid from
  four sides, overhead, and in section with the cut box.
- Parapet is `Castle Ruins Crenellation - Small` on the outward *edge* of the
  cell with the wall-walk paved behind — a parapet is a thin thing on a lip.
- River bed has its own palette role and a filled water column, so depth reads.
  The old grass bed under translucent water was "a second layer of land".
- Edge taper drops the outermost ring only, and never touches water or paving —
  only land falls away.
- 137 tests pass. They assert invariants, not exact output.

## Open problem 1 — paste height across layers (BLOCKING)

**This is the thing to fix first.** Everything else is cosmetic by comparison.

Symptom, current board: paste the landscape layer onto a fresh board, then the
structure layer over it, and the structures land at the wrong height. On the last
run the roofs lay at ground level with trees growing through them and one
building hung in mid-air.

What is known:

- A copy-out of a region-chunked board carried a **3.5 tile relief** where the
  source's maximum possible is **3.0** — so half a tile was introduced *at paste
  time*, not in the file. That measurement is solid.
- The explanation is not. "A slab comes to rest on whatever is under the cursor"
  fits some evidence and is refuted by others (the structure layer's own lowest
  point is a registration marker at y=0, so resting-on-top would lift buildings
  by the whole terrain height — sometimes it does not).
- `Ctrl` + scroll moves a held slab **vertically** before committing
  (`ts.ps1 nudge -Mode vertical`). `Ctrl` + right-drag moves the working plane
  (`ts.ps1 elev`). The user's guidance: set elevation deliberately with Ctrl
  before committing rather than trusting the snap.
- The held-slab preview is a **validity display**: solid = not intersecting,
  translucent mesh = intersecting. Chunks that share cells are *supposed* to
  intersect, so for a layer that overlaps existing geometry the mesh state may
  be the correct one.

Approaches worth trying, roughly in order:

1. Make the height explicit rather than inferred. Determine empirically how many
   `nudge` ticks equal one tile, then compute the required offset per layer from
   the slab's own coordinates and apply it before every commit. A driver that
   *sets* the height beats one that hopes the snap is right.
2. Consider a generator-side datum: e.g. every layer carries a full-height
   registration column at the map's low corner, so all layers present the same
   vertical extent to the snap and cannot be resolved differently.
3. Verify with copy-out, not with eyes (see below).

## Open problem 2 — copy-out driven synthetically

Copy-out is the **only** way to read what actually landed on the board. A human
does it fine; driven synthetically it fails.

`X` + drag draws a selection marquee, a submenu appears, and its button's own
tooltip reads "copies a slab into the clipboard" — but the result is a valid
*empty* slab (31 bytes) over open ground, and real content only when the region
contains a building. Tried and did not help: working plane above the terrain,
below it, `Ctrl+C` instead of the button, a slow drag, several camera heights.

The marquee draws and the button is right, so the selection itself is empty.
Suspect the X+drag is not committing a real selection. Worth investigating the
`M` / `N` controls bottom-right (N is the cut box; M is unidentified) in case one
sets the selection volume's height range.

Until this works, a measurement can be obtained by asking the user to copy a
region out and paste the base64 into chat — they have done this twice and it
settled questions nothing else could.

## Known cosmetic defects (not blocking)

- **Rampart scale.** Four cells thick, ~25 ft tall against two-storey cottages;
  forms dark canyons. Height is now a named parameter, `TOWN_WALL_TILES`. This
  is a taste call — surface it to the user rather than deciding it.
- **215 tile seams** (`verify` warns, does not fail). Mostly the doubled
  crenellation where a stair-step faces out two ways. The alternative is a gap
  at every step corner.
- **Glazed facades.** Tall buildings carry a dark panel in every wall bay and
  read as modern curtain wall rather than shuttered upper storeys.
- **Map edge** is a hard straight cut into the void from outside.
- **Interiors** are unbuilt. `floorplan.py` has the room geometry; nothing wires
  it to a second board per building. Deliberately parked.

## Driving TaleSpire

`tools/ts.ps1` is the single implementation. **Do not hand-roll `mouse_event`
calls** — every rule below was learned painfully and is encoded there.

```
ts.ps1 client                    # client rect + centre; derive coords from this
ts.ps1 newboard                  # fresh realm, build mode on
ts.ps1 planestate                # is the G build plane up? it breaks pastes
ts.ps1 hold   -Slab f -X .. -Y ..# Ctrl+V, leaves it in hand, does NOT commit
ts.ps1 nudge  -Ticks -1 -Mode vertical
ts.ps1 commit -X .. -Y ..
ts.ps1 clear                     # right-click TAP; nothing else empties the hand
ts.ps1 camera -DY -300           # camera height slider; beats the capped wheel
ts.ps1 elev   -DY 200            # ctrl+right-drag: the working plane
ts.ps1 camerastate               # READ the camera back: height + compass
ts.ps1 pan / rdrag / orbit / fly / cutbox / shot
tools/review.ps1 360 -Slab f -Name n
tools/review.ps1 flyby -Name n
```

Non-obvious rules, all verified in game:

- **Input duration is the answer more often than input type.** Five times now:
  the commit click must be *held* ~200 ms; keystrokes need a **scan code** as
  well as a hold; camera drags must be *slow* (60 steps × 40 ms); the drop
  right-click must be a **tap** (40 ms — a 250 ms hold reads as a drag and does
  nothing); WASD **ramps**, so it must be held for seconds. Two of those pull in
  opposite directions. When an input appears dead, change how long it lasts
  before concluding it does not work.
- **`G` raises a build plane and pastes snap to it.** It survives making a new
  board and the only tell is one orange toolbar icon. `hold` refuses to run
  while it is up.
- **Derive screen coordinates from the window.** It gets moved and resized; a
  stale rectangle does not fail loudly, it aims clicks at the wrong thing.
- **The cut box (`N`) is a persistent toggle** and survives a new board. Left on,
  it reads as a rectangular hole in the terrain.
- **Read the camera back, do not track it in your head.** Every camera command
  is a *relative* move, and a session that only issues them ends up over the
  void wondering where the map went � this happened repeatedly. `camerastate`
  reports the height slider's handle position (numeric, comparable between
  calls) and saves a crop of the compass rose, which shows bearing by where N
  points and pitch by how squashed the circle is. Check it before concluding
  something is missing from the board; usually the camera is just somewhere
  else.
- The bottom hint bar is the best control reference in the game, but it is
  clipped unless the window is sized to fit the desktop.

## How to work

- **Commit in small, complete steps**, message-first: say what was wrong, what
  the evidence was, and what changed. The git log is the project's memory and it
  is written in that style throughout — match it.
- **Stage files explicitly by path. Never `git add -A`.**
- **Add a test with every behaviour change**, and make sure it fails before it
  passes — a check nobody has seen fail is worth nothing. One in this repo
  reported all 25,150 placements as broken on its first draft.
- **Correct the record when you are wrong.** `CLAUDE.md` contains notes that
  were written confidently and later refuted; the refutations are written in
  beside them rather than the notes being quietly deleted. Do the same.
- **Surface taste calls, do not make them.** Wall height, town density and
  palette choices are the user's.
- When a probe and a screenshot disagree, get a third angle before believing
  either.

## Suggested next iterations

1. Fix paste height across layers (Open problem 1). Nothing else is worth doing
   until a board assembles correctly twice in a row.
2. Get copy-out working synthetically (Open problem 2), then add a
   `verify`-style board-vs-file check: paste, copy back, compare relief and cell
   coverage against the source slabs. That closes the loop this project has been
   missing from the start.
3. Only then, cosmetics — the glazed facades read worst at play height.
