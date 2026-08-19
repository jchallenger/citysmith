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
`import`: `--house-ft` (scale anchor, default 20), `--feet-per-unit` (override),
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

**"I want an interior battle map for the tavern"**
→ `plan` then `design`. `design --roof` includes a roof (which hides the
interior — usually not what they want for a battle map); `--prop-density`
defaults to 0.12.

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
python -m pytest -q
```
84 tests. `tests/fixtures/*.slab` are genuine TaleSpire slabs and are the codec's
ground truth: decode → encode reproduces the original *binary* byte for byte (the
base64 differs, because .NET's deflate and zlib's differ — that is expected).

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

**3. Building access.** The `build`/`verify` report line reading
`building access: N of M buildings (P%) can be entered from the street network`.
`verify` fails below 90% and warns below 98%. Report the actual percentage — do
not say "looks good". Also read `slab export` (largest chunk must be ≤ 30,720
compressed bytes) and `street width`.

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

The roof kit rotations (`ROOF_EDGE_ROT` N=6 E=0 S=18 W=12, `ROOF_CORNER_ROT`
NW=12 NE=6 SW=18 SE=0 in `build.py`) are a quarter turn off the wall convention
(`ROT_N,E,S,W = 0,6,12,18`). That is measured. Do not "fix" it.

## Handing off to the user

Pasting is the only ingestion path — `talespire://` links do not import boards.
When you deliver slabs, tell the user: run TaleSpire **windowed**, `Ctrl+V` puts
the slab in hand, a left press **held ~0.2 s** commits it (an instant click is
swallowed), right-click clears the hand, and multi-chunk maps must all be
committed at the **same grid cell without moving the camera**. Point them at
`docs/pasting-into-talespire.md`.
