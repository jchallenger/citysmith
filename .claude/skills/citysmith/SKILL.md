---
name: citysmith
description: Generate a city, town, or village map and build it as a TaleSpire board or slab. Use when the user wants a TaleSpire map, board, or battle map for a settlement; wants to import or convert a Watabou Medieval Fantasy City Generator (MFCG) GeoJSON export; wants to build, rasterise, verify, or debug .slab.txt files; wants to pick an encounter location in a generated town; or asks about TaleSpire asset palettes, roof/wall rotation conventions, or slab size limits.
---

# citysmith

Python toolset at the repo root that turns a settlement into pasteable TaleSpire
slabs. Core is stdlib-only; `anthropic` is an optional extra used by one command.
Run everything as `python -m citysmith <cmd>` from the repo root.

Read `README.md`, `docs/pasting-into-talespire.md` and `docs/asset-conventions.md`
before changing generation code. `CLAUDE.md` holds the module map and the
verified slab format.

## Which command

Start here. Do not invent flags — check `python -m citysmith <cmd> --help`.

**"Build me a map of this town" / user has a Watabou/MFCG JSON export**
→ the main path.
```bash
python -m citysmith catalog build                       # once per TaleSpire install
python -m citysmith import mytown.json                  # -> out/layout.json + layout.svg
python -m citysmith verify out/layout.json              # cheap sanity check
python -m citysmith build out/layout.json --stem mytown # -> out/mytown-rNNcNN[+N].slab.txt
```
`import`: `--house-ft` (scale anchor, default 35), `--feet-per-unit` (override),
`--margin-ft` (suburb kept outside walls, default 60), `--no-clip`, `--name`,
`--seed`, `--scale`, `--no-svg`.
`build`: `--style {medieval,cyberpunk}`, `--seed`, `--storeys` (default 3),
`--no-roofs`, `--no-bridges`, `--max-assets` (default 9000), `--scale`,
`--crop X,Z,W,D`, `--stem`.

**"Try just one district / test in-game before committing"**
→ `python -m citysmith build out/layout.json --crop 40,40,40,40 --stem probe`.
Fast, one slab, one paste.

**"Does this map play well?" / "why is that quarter unreachable?"**
→ `python -m citysmith verify out/layout.json`, then open `out/city-raster.svg`
(red = unreachable pockets, yellow = gates, blue = added bridges). `build` prints
the same report, so do not run `verify` separately after a build.

**"Generate a town from scratch" (no MFCG export)**
→ the procedural path.
```bash
python -m citysmith city --seed 42 --size town --style medieval
python -m citysmith sites out/city.json --top 10
python -m citysmith plan out/city.json --site tavern-015
python -m citysmith design out/tavern-015.plan.json
```
`--size` takes `hamlet|village|town|city|metropolis` or a raw tile count.
`pipeline` runs all four in one command. `board` builds the coarse 3D city shell
from a `city.json`.

**"Take the party into the tavern" / "I need the inside of that building"**
→ `scene`, for a building from an **imported** town. It writes the interior, an
apron of ground, four marks where the tokens go, a manifest, a GM brief and a
plan — one slab, one paste.
```bash
python -m citysmith scene out/graybank/layout.json --list      # what is worth entering
python -m citysmith scene out/graybank/layout.json "halfling"  # -> out/scenes/<id>/
```
```powershell
.\tools\scene.ps1 enter -Scene graybank-tavern-0014
```
Name the building by id, by an unambiguous piece of its name, or `kind:tavern`.
An ambiguous name is refused rather than guessed — FTG calls six of Graybank's
buildings `Farm`.

Three things to say to the user rather than let them discover:
- **The minis are not in the slab and cannot be.** A v2 slab's creature count is
  always zero. The four marks are contrasting floor tiles inside the door; the
  tokens get dropped on them by hand.
- **The occupants are derived, not exported.** The GeoJSON names buildings and
  gives their trade and carries no people at all (checked across all three FTG
  exports). `--roster <file>` replaces them with real ones, keyed on building id.
- **The board is reused, never rebuilt over.** Second visit switches to the
  board that is already there. `-Rebuild` makes a *second* board and leaves the
  first alone, because there is no erase in TaleSpire.

Settings live in `config/scene.json` — party size and names, storeys, apron,
prop density, board naming. `docs/scenes.md` is the full guide.

**"I want an interior battle map for a procedurally generated town"**
→ `plan` then `design` (the `city.json` path, not the imported one).
`design --roof` includes a roof (which hides the interior — usually not what
they want for a battle map); `--prop-density` defaults to 0.12.

**"Which building should the encounter be in?"**
→ `sites` — it prints the score and the reasoning per building, so the ranking
can be argued with. Filters: `--kind`, `--district`, `--min-floors`, `--top`.

**"The walls/floors/water look wrong"**
→ a palette problem, not a geometry problem. `python -m citysmith catalog search
<terms> --group <tag> --kind tile --limit 25` to find the exact asset name, then
pin it by `name=` in `citysmith/palette.py`. See "asset selection" below.

**"Describe a town in plain English"**
→ `python -m citysmith brief "<prompt>" [--describe] [--no-design]`. Needs
`ANTHROPIC_API_KEY`. Claude picks generator parameters and writes GM prose only —
it must never emit coordinates, asset ids, or slab bytes.

**"Is the rotation convention still right?"**
→ `python -m citysmith calibrate` writes `out/calibrate.slab.txt`: a 9×3 pad with
four walls each hugging one named edge of its own tile.

## Verification that is never skipped

Run all three before reporting a build as good.

**1. Tests.**
```bash
python -m pytest -q -n auto
```
881 tests, ~24s in parallel against ~104s serial. `tests/fixtures/*.slab` are
genuine TaleSpire slabs and are the codec's ground truth: decode → encode
reproduces the original *binary* byte for byte (the base64 differs, because
.NET's deflate and zlib's differ — that is expected).

**Do not run the whole suite after every edit.** Run the file you touched, or
`--lf -x` to re-run only what failed; the full suite is the gate before a
commit, not the inner loop. See CLAUDE.md's *Testing* section for why the
slow tests are worth looking at rather than tolerating — two of them were
bugs, not cost.

**2. The off-grid canary.** Every non-prop placement must sit on a half-tile
boundary. A single fractional overhang drags the whole board off the grid, which
looks fine and breaks mini snapping.
```bash
python -c "
import sys, pathlib; sys.path.insert(0, '.')
from citysmith.slab import decode
from citysmith.catalog import load_or_build
byid = {a.id: a for a in load_or_build().assets}
s = decode(pathlib.Path('out/mytown-01.slab.txt').read_text())
bad = [p for p in s.placements
       if byid.get(p.asset_id) and byid[p.asset_id].kind != 'prop'
       and any(abs(v*2 - round(v*2)) > 0.01 for v in (p.x, p.z))]
print('placements', len(s.placements), 'off-grid', len(bad))
"
```
Must print `off-grid 0`. Check every chunk, not just the first.

**Read the whole report. Do not grep it for the lines you want.** Two defects
were shipped in one session because the build was filtered with
`grep -E "assets in|slab export"`, which hides every `[FAIL]` above it: a
portcullis sitting a quarter tile off the lattice, and a rampart optimisation
that emptied exactly the cells the masonry check samples. The report is short.
Read it.

**3. Building access.** The `build`/`verify` report line reading
`building access: N of M buildings (P%) can be entered from the street network`.
`verify` fails below 90% and warns below 98%. Report the actual percentage — do
not say "looks good". Also read `slab export` (largest chunk must be ≤ 30,720
compressed bytes) and `street width`.

**4. The placement checks.** `build` runs two families of check that read the
*emitted geometry* rather than the tile grid, and both fail the build:

- `placements` — off-grid tiles, doorways that resolved to nothing and got
  built as solid wall, and town-wall cells with gaps in the masonry. The last
  one exists because a wall built from 0.5-deep curtain pieces laid one per
  cell scores perfectly on any check that reads the grid, while being visibly
  see-through on the board.
- `chunk coverage` — chunks dropped as open country that the map encloses.
  An unpasted chunk is bare board, not grass, so an enclosed one pastes as a
  rectangular hole in the ground.

Never add a new check to the TileMap pass. If it can be fooled by geometry
that was never built, it belongs in `verify.check_placements`, which has
`_Occupancy` for asking "is there solid matter at this point".

**Never optimise away geometry a check samples.** 38% of the rampart's body
cells have no visible face, so dropping their lower courses saved 495 blocks
and looked identical — and emptied precisely the cells `town_wall_gaps` reads,
which cannot tell a sealed void from daylight straight through the circuit.
That check exists because see-through wall shipped once, on 1,234 tiles. If an
optimisation makes a check fail, the check is usually the older and wiser of
the two; the burden is on the optimisation.

## Before reverse-engineering the game, check the modding community

Twenty minutes on [Thunderstore](https://thunderstore.io/c/talespire/) and
GitHub, *first*. Several sessions went into measuring how `Ctrl+V` seats a slab
— the ray-hit anchor, the slide on a tilted camera, the bounding-box centre,
the even-extent tie — and then into the machinery that works around it. That
whole problem is solved: LordAshes' `MultiPasteSlabsPlugin` and
`SlabPlugin_CCM` read a JSON document stating each slab's position.
`build --multi-slab` emits it.

Also worth knowing before you build a tool: **Tales Tavern's asset archive**
browses every in-game tile *with pictures* (many probe sessions were spent
answering questions a picture answers in a second); `SlabelFish` and
`talespireDeserialize` are existing Python slab codecs; `TaleSpire_Generator`
already reads `index.json` and emits slabs.

## Debugging technique

**Decode the slab and measure it. Do not judge from a screenshot.** Screenshots
have been misread repeatedly on this project: a half-tile offset, a roof course
one unit too low, and a silently dropped prop all look "close enough" in an image
and are unambiguous in the coordinates.

```python
from citysmith.slab import decode
s = decode(open('out/mytown-01.slab.txt').read())
mn, mx = s.bounds()
```

**Derive conventions by correlating placement position against rotation.** When
you do not know which way a piece faces at `rot=0`, do not guess:

- Emit a probe slab laying the asset at all four quarter turns, each on its own
  pad with a stub wall marking north. `tools/roof_probe.py` and
  `tools/rev6_probe.py` are the working templates.
- **Sweep the whole space; never report one guess as a survey.** Trying a
  single candidate corner per roof kit, seeing it fail and concluding "these
  kits have no 1x1 hip pieces" was wrong in every clause — the pieces exist
  one-for-one and only the rotation differed. The user pushed back with "it
  seems to me the pieces exist, but the rotations are wrong", and was right.
  The fix is an artifact that enumerates the space (`roofrot_probe.py --hips`
  lays the same hip once per offset, so exactly one closes), not an assertion.
- **Label a probe so the label survives the angle you read it from.** A
  vertical tally stack reads at a low oblique and vanishes in plan; a bar of N
  cells on the ground reads in plan and vanishes at an oblique. Put the count
  on the thing being judged when you can — `stairrot2` stacks pips on each
  flight's own wall — because a marker you cannot match to a candidate makes
  the probe worthless. Two findings were nearly misattributed this way.
- **Run one dark kit at a time against a light control.** Castle and Haunted
  are both dark weathered timber and were told apart on a four-kit board only
  by counting a tally at a grazing angle, which read wrong.
- Or decode a build that already works. `tools/harvest_slabs.py` pulls community
  slabs into `library/`; `tools/analyse_library.py` decodes them and prints, per
  group tag, the asset names used plus rotation and `y` histograms. That is how
  the roof convention was settled after two revisions of guessing.

The convention falls out of how a piece's min corner moves as `rot` changes.

## Asset selection

Pin by **exact name**, never by tag. Tags describe material, not look — selecting
by tag once produced a medieval town with desert floors, shogun interiors and a
fishing net for water. `Palette.validate()` enforces footprints and will refuse
to build: `CELL_ROLES` must resolve to 1.0×1.0 assets (this includes the
wall-course corner roles, which must also match the wall's height, or every
storey above a corner drifts), `BLOCK_ROLES` to 2.0×2.0,
and `WALL_SEGMENT_ROLES` must match the wall's footprint.

Derive every placement offset from the asset's collider bounds
(`size_x/size_y/size_z`); never hardcode one.

**The kit is the catalog's `folder`.** `pack` is the DLC ("Medieval Fantasy"
covers castle, rural, tavern and thatch alike) and `group_tag` is a *form*, so
neither says whether two pieces belong together. `Village Roof Side Wall 02`
lives in folder **Tavern**. Getting this wrong meant hunting a corner named
"village *", finding none, and mitring one — while the kit's own corner sat
unused. `python tools/kit_index.py --complete` answers "which kits can build a
whole house"; `--kit X` dumps one.

**The roof rotations are PER KIT.** `ROOF_EDGE_ROT` (N=6 E=0 S=18 W=12) and
`ROOF_CORNER_ROT` (NW=12 NE=6 SW=18 SE=0) were read out of one community-built
cottage, and that cottage is *thatched*. No other kit shares them:

| kit | edge | corner | material |
|---|---|---|---|
| `Rural` | +0 | +0 | thatch — the baseline the constants encode |
| `Tavern` | +6 | +6 | terracotta tile |
| `Castle Fortified` | +6 | +0 | brown shingle |
| `Abandoned Village` | +6 | +0 | grey slate |

`build.ROOF_ROT_OFFSET` holds the table, keyed on `folder`. Measure a new kit
with `tools/roofrot_probe.py --hips`, which lays the same hip once per quarter
turn so exactly one closes — and note the edge and corner do **not** have to
share an offset, so sweep them separately (`--edge-off`).

## Handing off to the user

Pasting is the only ingestion path — `talespire://` links do not import boards
(confirmed against the official scheme docs: dice, single assets by UUID,
published-board links, bookmarks; no slab import).

If they are willing to run BepInEx, `build --multi-slab` writes one JSON
document that a paste plugin places in a single keystroke — no cursor aiming,
no shared bounding box, no paste order, no camera discipline. Offer it, but
keep the chunk files as the default: the plugin breaks on game updates, and
nobody should have to mod their game to see a town.
When you deliver slabs, tell the user: run TaleSpire **windowed**, `Ctrl+V` puts
the slab in hand, a left press **held ~0.2 s** commits it (an instant click is
swallowed), right-click clears the hand, and multi-chunk maps must all be
committed at the **same grid cell without moving the camera**. Point them at
`docs/pasting-into-talespire.md`.
