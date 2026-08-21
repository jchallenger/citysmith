# Brief: autonomous iteration on citysmith

You are picking up a TaleSpire map generator mid-flight. This brief exists so
you do not re-derive what has already cost days, and do not repeat mistakes that
are written down. Read `CLAUDE.md` first â€” it is the engineering notes and it is
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

- Chunks are split **by layer first, region second** â€” `landscape` and
  `structure` (see `CLAUDE.md`, "Chunking"). This removed terrain-meets-terrain
  seams structurally: all ground is one body, so it cannot disagree with itself.
- Open-country trimming is judged **across layers**. Layering broke the old
  test: a landscape chunk under a town holds nothing but grass, so it read as
  empty and got dropped, leaving buildings on nothing. A 40x40 crop lost half
  its ground that way before it was caught.
- Rampart mass is `md_stairblock_01`, a plain solid cube, verified solid from
  four sides, overhead, and in section with the cut box.
- Parapet is `Castle Ruins Crenellation - Small` on the outward *edge* of the
  cell with the wall-walk paved behind â€” a parapet is a thin thing on a lip.
- River bed has its own palette role and a filled water column, so depth reads.
  The old grass bed under translucent water was "a second layer of land".
- Edge taper drops the outermost ring only, and never touches water or paving â€”
  only land falls away.
- 137 tests pass. They assert invariants, not exact output.

## Paste height - FIXED, but understand why before changing it

**A shared corner is not a shared box, and the paste uses the box.**

Every chunk used to carry one registration marker, at the map's low corner, so
all their *minima* agreed. Their maxima did not, by a long way: the landscape
layer topped out around y=7 (a pine) and the structure layer around y=20 (a
roof). Pasted at the same cursor cell they seated at different heights - which
is how a whole layer of roofs ended up lying in the grass with trees growing
through them, and, at region-chunk scale, how a copy-out measured a 3.5 tile
relief where the source's maximum possible was 3.0.

The fix is **two markers per chunk**, one at each corner of the whole map, so
every chunk presents an identical bounding box and the paste has nothing left to
disagree about. `verify.chunk_anchors` now checks the far corner as well as the
near one, and `test_every_chunk_presents_the_same_bounding_box` guards it.

Verified: all five chunks of Forest Church report `(0,0,0)` to
`(188.51, 20.00, 183.04)`, and a 40x40 sample board pastes landscape-then-
structure with everything seated on ground.

**The cost, which is visible:** two grass tiles float in mid-air at the map's far
corner - the high markers. They are synthetic, they are the price of
deterministic placement, and they can be deleted after pasting. Do not remove
them from the generator without replacing the guarantee.

**Do not trust the old "a slab rests on whatever is under the cursor" model.** It
fit some evidence and was refuted by other evidence; the box explanation covers
both. If a layer ever does land wrong, `Ctrl`+scroll (`ts.ps1 nudge -Mode
vertical`) is the correction, and the held-slab preview is a validity display:
solid = not intersecting, translucent mesh = intersecting. Chunks that share
cells are *supposed* to intersect, so mesh is not automatically an error.

## Open problem 1 - copy-out driven synthetically

Copy-out is the **only** way to read what actually landed on the board. A human
does it fine; driven synthetically it fails.

`X` + drag draws a selection marquee, a submenu appears, and its button's own
tooltip reads "copies a slab into the clipboard" â€” but the result is a valid
*empty* slab (31 bytes) over open ground, and real content only when the region
contains a building. Tried and did not help: working plane above the terrain,
below it, `Ctrl+C` instead of the button, a slow drag, several camera heights.

The marquee draws and the button is right, so the selection itself is empty.
Suspect the X+drag is not committing a real selection. Worth investigating the
`M` / `N` controls bottom-right (N is the cut box; M is unidentified) in case one
sets the selection volume's height range.

Until this works, a measurement can be obtained by asking the user to copy a
region out and paste the base64 into chat â€” they have done this twice and it
settled questions nothing else could.

## Open problem 2 - the board still does not look right, and the file says it is

**This is the live investigation. Do not start it by re-measuring the file.**

The user reports, from close range in the town, that surfaces sit at different
levels: grass appearing to stand above adjacent paving, building floors and the
ground beside them reading as a step rather than one plane. It has been reported
in some form for many revisions and has survived several fixes, each of which was
a real bug but evidently not *this* bug.

### What has been measured on the emitted geometry, and is clean

Run these before doubting them, but do not expect them to find it — they have
been run and they pass:

| check | result |
|---|---|
| top height per surface class | ground, field, lane, pier, plaza, street **all 0.5** |
| the exceptions | 718 ground + 144 field cells at 0.0 — the intended one-course border taper |
| building floor vs adjacent ground | 1,179 boundary pairs, **every one flush** (0.0 step) |
| two surfaces sharing a cell at one height | **0 cells** |
| chunk bounding boxes | all five identical, `(0,0,0)`–`(188.51, 20.00, 183.04)` |
| water | one level, bed stepped below it by design |

So: the file does not contain the step the screenshots show. Either it is
introduced at paste time, or it is not geometry at all.

### The two live hypotheses

1. **Paste still moves things.** The bounding-box fix made every chunk present
   the same box, which is necessary but may not be sufficient. Note that a real
   half-tile discrepancy *was* measured on a board once (3.5 relief against a
   source maximum of 3.0) — that number is trustworthy and unexplained by
   anything now in the code.
2. **It is rendering.** TaleSpire's lighting produces hard boundaries that
   rotate with the world and vanish at low angles. One such band was chased for
   most of a session and turned out not to be geometry. Shadowed floor tiles
   beside sunlit grass look exactly like a step in a screenshot.

### How to tell them apart — and why copy-out is the priority

Only one instrument distinguishes them: **copy the affected region off the board
and compare it to the source slab.** If the copied relief matches the file, the
geometry is right and the eye is being fooled. If it does not, the paste moved
it and the difference names the offset.

That is why the copy-out problem above is the top of the backlog rather than a
convenience. Everything else here is opinion; that measurement is not.

Failing that, the cheap discriminator is **angle**: a half-tile step is
unmistakable from a low oblique and stays put as the camera orbits. A lighting
boundary flattens out at low angles and can sit at a fixed screen orientation.
Take both before concluding anything — this exact mistake has been made in both
directions on this project.

### What not to do

- Do not "fix" it by nudging pastes until a screenshot looks right. That hides
  whichever of the two causes it is and makes the next report harder to read.
- Do not re-run the table above and report it as progress. It is already known.

## Known cosmetic defects (not blocking)

- **Rampart scale.** Four cells thick, ~25 ft tall against two-storey cottages;
  forms dark canyons. Height is now a named parameter, `TOWN_WALL_TILES`. This
  is a taste call â€” surface it to the user rather than deciding it.
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
calls** â€” every rule below was learned painfully and is encoded there.

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
  well as a hold; camera drags must be *slow* (60 steps Ã— 40 ms); the drop
  right-click must be a **tap** (40 ms â€” a 250 ms hold reads as a drag and does
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
  void wondering where the map went — this happened repeatedly. `camerastate`
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
  is written in that style throughout â€” match it.
- **Stage files explicitly by path. Never `git add -A`.**
- **Add a test with every behaviour change**, and make sure it fails before it
  passes â€” a check nobody has seen fail is worth nothing. One in this repo
  reported all 25,150 placements as broken on its first draft.
- **Correct the record when you are wrong.** `CLAUDE.md` contains notes that
  were written confidently and later refuted; the refutations are written in
  beside them rather than the notes being quietly deleted. Do the same.
- **Surface taste calls, do not make them.** Wall height, town density and
  palette choices are the user's.
- When a probe and a screenshot disagree, get a third angle before believing
  either.

## Suggested next iterations

1. **Get copy-out working synthetically** (Open problem 1). It is the only
   instrument that can tell "the file is wrong" from "the paste is wrong", and
   Open problem 2 cannot be settled without it. Everything else is opinion.
2. **Settle Open problem 2** with that instrument: copy the affected region,
   compare relief against the source slab, and let the number decide.
3. Then wire the comparison into `verify` as a standing board-vs-file check, so
   a bad paste reports itself instead of waiting to be noticed in a screenshot.
4. Only then, cosmetics. The glazed facades read worst at play height, and the
   rampart reads as a maze of parallel runs from directly above — worth checking
   whether that is a real shape problem or an artifact of cropping across a
   stair-stepped diagonal.
