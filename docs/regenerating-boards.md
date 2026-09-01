# Regenerating every board

The 2026-08-30 branch changed what a town looks like — the market square is
grown between the frontages instead of stamped, the plaza carries stall rows,
FTG towns gained their authored bridges, forest-shaped tree cover and trodden
trails, and the fence machinery is built and waiting on one measurement. None
of that is on a board yet. This is the ordered path from here to four fresh
town boards and new README screenshots, written so it can be run top to bottom
without re-deriving anything.

Two rules shape the order, both already paid for elsewhere in these docs:

- **One driver in the game at a time.** Every probe and every paste below is
  sequential. Nothing here parallelises in-game work, because two hands on one
  cursor is how a held slab gets stamped somewhere invisible.
- **Probes before towns.** Two of the changes deliberately stop short of an
  asset decision that only a paste can settle (the stall pick, the fence
  collider question). Answering them first means the towns are built once,
  not rebuilt after the answer.

## Phase 0 — sync and self-check (no game)

```powershell
git fetch origin claude/merchant-market-square-designs-7z0b1h
git checkout claude/merchant-market-square-designs-7z0b1h
python -m pytest -q
```

Expect the whole suite green on a machine with TaleSpire installed. In the
container the branch was built in, exactly 20 tests fail, every one with
`CatalogError: Could not find your TaleSpire install` — those are the tests
that need the real catalog, and this machine is where they finally run.
`catalog.json` regenerates automatically on the first build.

Landing the branch on `main` is `worktrees.ps1 land` territory and the
repo owner's call; everything below runs the same either way.

## Phase 1 — the two gating probes, then a taste call (game)

Fresh board per probe, `review.ps1 360` per probe, `ts.ps1 clear` after every
paste. In this order:

1. **Fence spacing probe** — `out/fence/spacing-probe.slab.txt` (regenerate
   with `python tools/fence_sections.py` if `out/` is stale). Count panels
   **from overhead**: eight in every row is the answer that lets diagonal
   fences run butted; a gappy row 1 with a whole row 3 means runs must be
   spaced. `docs/fencing.md` §9 reads the result. This gates the standing
   `[FAIL] placements: N props overlap` on fenced builds — after the answer,
   teach `verify._prop_collisions` the exemption *with the evidence*.
2. **Market stall probe** — `python tools/market_probe.py --seed 33` (the
   build's own seed; the probe resolves on the palette the build will use).
   Paste, orbit. Pads are numbered by east-running cobble bars; the facing
   rank names each mesh's true front; `castle merlon 1x1` is the control and
   must read wrong. The listing's `<- the build's pick` line says which
   candidate the seed will place — pin the winner **by kit** in
   `palette.py`'s `market_stall` / `plaza_well` roles afterwards. If the role
   resolves to nothing, towns get the degraded goods-cluster market; look at
   one before deciding it needs fixing.
3. **Fence style** — the A/B/C sections from `tools/fence_sections.py`,
   pasted in a row per style. Which style is a taste call and the owner's;
   `docs/fencing.md` §9 lists what each comparison is asking.

## Phase 2 — rebuild and verify all four towns (no game)

```bash
python -m citysmith import samples/forest_church.json
python -m citysmith build out/layout.json --stem forest --seed 33 --by-region

python -m citysmith import <pelvesthollow export>   # then build --by-region --chunk-tiles 64
python -m citysmith import <graybank export>        # then build --by-region --chunk-tiles 80
python -m citysmith import <tradebourne export>     # then build --by-region --chunk-tiles 112 --max-assets 6500
```

The FTG exports live on this machine, not in the repo. Chunk sizes are the
measured picks from `docs/ftg-geojson-import.md`; keep them unless the byte
check below says otherwise.

What the verify output should say now, and why:

- **Masonry green everywhere, walls included.** The "entombed rampart" FAIL
  recorded against East Tradebourne was a stale note — the hollowing was
  reverted in `e83671a` and the record now says so.
- **The fence overlap FAIL persists until Phase 1 item 1 is acted on.** The
  check names how many overlapping pairs are consecutive fence panels so it
  cannot be misread as a scatter regression.
- **Re-check the largest slab's bytes on every town.** The standing rule is
  to size the largest slab near two thirds of the cap and re-check after any
  change that adds dressing — and this branch adds market rows to every town
  with a plaza. Forest Church's largest slab was already at 30,210 of 30,720
  before the market grew from 49 to 130 cells; if it busts, drop
  `--chunk-tiles` or `--max-assets` one step. The FTG towns have headroom,
  and the forest change *reduces* their tree totals (462 → 284 pieces on the
  fixture corner), so pressure there is down, not up.
- **Stale artifacts lie.** Clear `out/` per town before judging counts; a
  previous build's chunks alongside new ones has cost an hour before.

### Phase 2 RUN, 2026-08-31 -- all four green enough to paste

Built with `--seed 33 --by-region --fence-style drystone`, each town into its
own `out/regen/<town>/` so no previous build's chunks could be counted.

| town | assets | chunks | largest slab | of cap |
|---|---|---|---|---|
| Forest Church | 27,130 | 54 | 4,737 | 15% |
| Pelvesthollow | 21,874 | 9 | 14,517 | 47% |
| Graybank | 91,777 | 22 | 23,559 | 77% |
| East Tradebourne | 407,396 | 114 | 24,180 | 79% |

**The byte worry above was misplaced, and the measured chunk sizes all hold.**
Forest Church's largest slab is 15% of the cap, not the 30,210 this file
feared -- that figure came from a different chunking, not from `--by-region`
at the default cell. East Tradebourne lands at 24,180 bytes against the 24,204
`docs/ftg-geojson-import.md` predicts for 112 tiles at `--max-assets 6500`,
which is the same number after a market, a flue, yards and field walls were
added to every town. Nothing needed dropping a step.

**Every town reports the same three FAIL lines, and they are understood:**

- `N of M props overlap` -- 4, 2, 48 and 144 pairs against 3,989 to 39,317
  props. Section 9.1 of `docs/fencing.md` settles what these are: corners
  penetrating past one thickness, and a panel clipping a scatter prop. Not
  butted fence runs, which the board says are never dropped.
- `(N boundary corner join(s) not counted)` -- the continuation line of the
  finding above, which the CLI prints as its own `[FAIL]` row. Cosmetic; it
  inflates a FAIL count by one.
- `shell footing: N building(s) do not stand on their own floor`, every one at
  y=0.47 over a floor top of 0.5 and every one `Tavern Wall 01`, which is 2.03
  tall where every other medieval wall is 2.00. The head is seated on the
  course line and the 0.03 is absorbed at the base where the floor hides it.
  `shell-footing-tolerance` in `tasks.json`.

Storeys on East Tradebourne now read `1:456 2:340 3:193`, mean 1.73, against
the `1:167 2:575 3:247`, mean 2.08 in CLAUDE.md. That is expected and not a
regression: **storeys are fixed at IMPORT**, and this is a fresh import.

## Phase 3 — paste, one town per board (game)

Per town: name the board per `.claude/skills/talespire-boards/SKILL.md`,
paste with `review.ps1 tiled` (it reads `<stem>-paste-order.txt`; never an
alphabetical glob), camera pitched straight down for every paste, anchor
cell bare until its own chunk goes down last. `-ShotEvery` thins the
screenshots on the two big towns.

What is new on each board, and where to point the camera for the README:

- **Forest Church** — the market square: an 18x13 irregular room against the
  buildings where the 7x7 stamp stood, stall rows with two-cell aisles, the
  well, four doorways opening onto it. The gate: the portcullis is hung and
  seated on the lattice. The rampart: one cutaway (`ts.ps1 cutbox`) to show
  coursed stone through, as the record now claims.
- **Pelvesthollow** — the three authored forest rings as closed stands with
  visibly thinned pasture outside the line; any TRAIL as a one-tile trodden
  path meeting grass flush and stopping at the stream bank rather than
  fording it.
- **Graybank** — the chosen fence style on real parcel lines; the market
  square if its export authored one.
- **East Tradebourne** — the five authored crossings decked in harbour
  planking flush with both banks, rails on the open-water sides; Warden
  Market untouched by the carve (the guard: an authored plaza disarms it);
  the wall verify line finally green.

Every new feature gets the standard read — four faces, overhead, eye level —
before its screenshot is trusted; a probe read from one angle is a probe that
lies, and that applies to a beauty shot most of all.

The two scene boards in `campaign/boards.json` (GRB/T14, GRB/T123) should
report READY, not STALE — nothing on this branch touches interiors. If one
reads STALE, that is a finding, not a rebuild instruction: diff the digest
first.

### Phase 3 RUN: Pelvesthollow, 2026-08-31

Board **`Pelvesthollow 08-31`** -- a third name, because `Pelvesthollow` and
`Pelvesthollow (old)` were both already in the index and two boards with one
name is worse than a dated one. The older pair are prune candidates, not
deletions: a board is where something happened and there is no erase.

`review.ps1 tiled` pasted all 9 chunks in manifest order at one cursor cell,
camera vertical throughout, `r01c01` last as the manifest says. The rename was
verified by reading the title bar back rather than trusting the script's own
return string -- `grab.ps1 -Name titlecheck -X 1500 -Y 0 -W 420 -H 34`, 4 KB
against 440 for a whole frame.

**The board assembled with no step and no seam at any chunk join**, read from
a high oblique across three of the nine regions. What is on it, and visible:
three roof materials including the trade tier's terracotta beside common
thatch; chimney stacks seated on the ridge rather than standing proud of it;
enclosed yards with a well, a workbench and clutter in them; hedged property
boundaries; cobbled lanes with carts and stumps along them.

`docs/images/pelvesthollow-lane.jpg` is the first capture, cropped 1250x703
from (200,150) so no HUD is in it.

**NOT yet checked on this board**, and listed so nobody reads the above as
more than it is: the three authored forest rings as closed stands with thinned
pasture outside the line, and a TRAIL meeting grass flush and stopping at the
stream bank rather than fording it. Both need flying to the map edges -- the
camera caps at about 40 tiles of frame and this town is 176x184.

**Camera note that cost several frames.** `nudge -Mode vertical` does nothing
useful without `-X`/`-Y`: the scroll needs a cursor anchor. `panel_review.ps1`
passes them and is the form to copy -- height first, then pitch, then never
touch the height again, because Ctrl+scroll moves the camera without moving
the focal target and a later height change throws the whole frame out of
focus.

### Phase 3 RUN: Graybank and East Tradebourne, 2026-08-31

Both rebuilt first, because the boards standing before this carried neither
the chimney caps nor the yard pass.

| town | board | assets | chunks | largest slab | of cap |
|---|---|---|---|---|---|
| Graybank | `Graybank 08-31` | 91,147 | 22 | 22,611 | 74% |
| East Tradebourne | `East Tradebourne 08-31` | 402,644 | 114 | 23,931 | 78% |

Both came DOWN from the Phase 2 figures (91,777 and 407,396): the chimney and
yard passes remove more than the new yard goods add, and Graybank's largest
slab gained headroom from 23,559.

The design work reads in the reports. Chimneys: Graybank `219 stack(s) -- 64
of 1 course, 155 of 2`; East Tradebourne `1696 stack(s) -- 489 of 1, 1207 of
2`, and on ET `placed end/lateral/ridge by quarter`, it being the one town
whose trades cluster hard enough to key on. Yards: 89 of 150 and 230 of 989.

**East Tradebourne's 114 chunks take about seventeen minutes to paste**, which
is past a foreground command's limit -- run it in the background rather than
splitting it, because a split run has to re-establish the camera and the
anchor cell and that is where a tiled paste goes wrong.

Seen on the board:

- **The yard pass.** A gravel yard behind post-and-rail with a cart, a log
  pile and a barrel in it. No bed, no dresser, no chest anywhere outdoors.
- **An authored crossing**, decked in timber with rope rails on both sides,
  running the raster's stair-stepped diagonal over open water.
- The civic tier's dressed castle stone with its crenellated head, beside
  terracotta trade roofs each carrying a single stack.

**NOT checked on these two**, and listed rather than implied: Warden Market
untouched by the carve, the wall verify line, and Graybank's drystone
boundaries on real parcel lines. Each needs flying to a known place on a
739x598 town at a camera that frames about 40 tiles.

### All four, rebuilt and re-pasted on one command, 2026-08-31

`tools/paste_all.ps1`. Four towns, 199 chunks, one game window and one cursor,
so the run is sequential by construction rather than by discipline.

| town | board | assets | chunks | largest slab |
|---|---|---|---|---|
| Forest Church | `Forest Church 08-31c` | 26,860 | 54 | 4,593 |
| Pelvesthollow | `Pelvesthollow 08-31c` | 21,707 | 9 | 13,818 |
| Graybank | `Graybank 08-31c` | 91,159 | 22 | 22,626 |
| East Tradebourne | `East Tradebourne 08-31c` | 402,693 | 114 | 23,883 |

All four now carry the same code: capped chimneys, outdoor yards, outdoor
frontages and reserved door aprons.

**Two things the run taught, both about the order of a check.**

`-WhatIf` prints the plan and the board names and touches nothing. It caught a
name collision before the run rather than after -- four boards already carried
the plain date from earlier the same day.

And the first real attempt died on its first town with the build plane
verified moments earlier. `review.ps1 tiled` does its own `newboard` and THEN
checks the plane; `newboard` can drop build mode, and the plane probe reads a
toolbar icon that only exists in build mode. So a check made before that call
describes the wrong moment entirely. The check now self-heals where it
belongs -- read, and if the toolbar is not there to read, toggle once and read
again; still unreadable is a stop, an unreadable probe not being a pass. The
wrapper's own pre-check was deleted rather than left to disagree.

### Re-pasted on the gable work, 2026-08-31 (`08-31d`)

All four again, after the gable became the wall carried up and then learned to
follow the building's own fabric.

| town | assets | was | largest slab |
|---|---|---|---|
| Forest Church | 26,860 | 26,860 | 4,593 |
| Pelvesthollow | 21,707 | 21,707 | 13,818 |
| Graybank | 91,159 | 91,159 | 22,626 |
| East Tradebourne | **393,185** | 402,693 | 23,553 |

**East Tradebourne lost 9,508 assets and gained byte headroom**, which is the
gable change paying for itself: a 2.0-tall wall panel covers two courses where
the stacked flat cap needed four pieces to cover the same. The smaller towns
do not move because their gables are rare -- gables are dealt by QUARTER and
only East Tradebourne has any.

**NOT SEEN: a gable end in a real town.** That is the one thing this pass was
for and the one thing still unphotographed. They are dealt by quarter, so on
a 739x598 board at a camera that frames about 40 tiles, finding one is a
flight and not a pan. The probe boards show the geometry; a town does not yet.

### Phase 2+3 RUN: all four, on the church work, 2026-09-01

Rebuilt into `out/regen2/<town>/` and pasted with `tools/paste_all.ps1`, one
town per board, boards `<Town> 09-01e`.

| town | assets | chunks | largest slab | of cap | churches |
|---|---|---|---|---|---|
| Forest Church | 28,444 | 9 | 22,179 | 72% | 1 split, tower + spire |
| Pelvesthollow | 21,707 | 9 | 13,818 | 45% | none in source |
| Graybank | 91,114 | 22 | 22,752 | 74% | 1 split, tower + spire |
| East Tradebourne | 391,643 | 114 | 23,739 | 77% | 4, 3 split, 2 towers + 2 spires |

**The rebuild is what caught the regression, and the churches line is what
reported it.** Splitting a chancel takes about a quarter of the footprint, and
every church that had earned a tower as one box then failed a gate as a
shortened nave -- Graybank 65 cells to a 53-cell nave, under
`TOWER_MIN_TILES`; East Tradebourne's two largest, 88 and 81, to 70 and 69,
under `TOWER_ASPECT_EXEMPT_TILES` so the 1.12 aspect bit. **Three of the four
towns came out with no tower and no spire on any church.** `pick_towers` gates
on the complex and sites on the nave now, at the end furthest from the rest of
the complex, which is a west tower. Then the towers had no spires, because
`span // 3` gave them 3 cells of width and the cap needs a whole 4x4; the
floor is `SPIRE_SIDE`, and `across - 1` buys the set-back at the same time.

**Pelvesthollow is the control and it is worth keeping.** It has no church in
its source, and its seam count is unchanged at 2,355 while the other three
rose in proportion to church count (+169, +101, +635). That accounts for the
whole change with nothing left over.

Remaining FAILs are the documented prop and fence-corner overlaps, 0% on
every town, unchanged from the 08-31 run.

Two process notes:

- **A mid-paste screenshot shows the board named `Unknown Realm NN`**, because
  `review.ps1 tiled` renames at the END. Read that as a failed rename once;
  the campaign list showed all four correctly named. Judge a rename from the
  list, not from a progress shot.
- **`paste_all.ps1` silently pointed at the previous build.** The first fix to
  its output path missed because `"out\regen\..."` written in a non-raw
  Python string carries a CARRIAGE RETURN, so the replacement never matched
  and the driver resolved to `out/regen/` -- it would have re-pasted the
  08-31 slabs while reporting success. Caught on `-WhatIf`, which is the
  argument for running it.

`ET church 09-01` holds a 21x24 crop of East Tradebourne's `temple-0027` for
close review: from overhead the spire is a dark pyramid with its finial rising
inside the ring of crenellations, which is the arrangement `lay_spire` was
designed around -- the parapet takes the margin the fixed 4x4 cap leaves.


## Phase 4 — README

New screenshots into `docs/images`, and the feature list catches up: grown
market squares, field walls, authored bridges, forest-shaped woods, trails,
the gate grille. That edit happens after the captures exist, against real
pictures.
