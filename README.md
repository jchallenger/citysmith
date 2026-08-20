# citysmith

Turn a Watabou **Medieval Fantasy City Generator** export into a playable
TaleSpire board. citysmith reads the MFCG GeoJSON, scales it to a 5 ft tile
grid, rasterises it into ground/street/water/building cells, dresses it with
real assets from your own TaleSpire install, and emits `.slab.txt` files you
paste into the game with `Ctrl+V`.

It also has a self-contained procedural city generator and an interior
floorplan builder, but the MFCG path is the one that produces a whole town.

```
forest_church.json ──▶ layout.json ──▶ raster ──▶ forest-r00c00+17.slab.txt ──▶ Ctrl+V
  (Watabou export)      + layout.svg    + city-raster.svg   forest-r02c02+14.slab.txt
                                                            forest-r04c02+20.slab.txt
```

## Prerequisites

- **TaleSpire, installed.** citysmith reads asset ids, names, tags and collider
  bounds straight out of `<install>/Taleweaver/<pack-uuid>/index.json`, so
  whatever packs you own are what it can build with. Nothing is bundled.
- **Python 3.10+** (developed on 3.14).
- **No dependencies.** The core is stdlib-only. `anthropic` is an optional
  extra used by one command (`brief`); everything else works offline.

```bash
pip install -e .          # or just run from the repo with python -m citysmith
```

## Quick start (end to end)

**1. Get a map.** Open <https://watabou.github.io/city-generator/>, generate a
town you like, then use its export menu to save the **JSON** (GeoJSON) export.
Verified against MFCG v0.11.5 exports. Call it `mytown.json`.

**2. Index your TaleSpire assets.** Once per install, or after buying packs:

```bash
python -m citysmith catalog build
```

Writes `catalog.json`. It finds TaleSpire via Steam automatically; if it can't,
set `TALESPIRE_PATH` or pass `--talespire-path "D:\SteamLibrary\steamapps\common\TaleSpire"`.

**3. Import the export into a scaled layout.**

```bash
python -m citysmith import mytown.json
```

Writes `out/layout.json` and `out/layout.svg`. The tile scale is *derived*: the
map is anchored to a real median building width (`--house-ft`, default 35 ft),
and the tile count follows. 35 is chosen for play: below about 30, most
buildings are too small to stand a party in — at 20 only 31% of them clear a
3×3 interior, at 35 it is 94%, and above 35 buys no further playability. `--margin-ft` (default 60) sets how much suburb is
kept outside the walls; `--no-clip` keeps the entire export. Any playability
warnings (buildings too small to fight in, streets too narrow) print here.

**4. Check it plays before you build it** — optional but cheap:

```bash
python -m citysmith verify out/layout.json
```

**5. Build the slabs.**

```bash
python -m citysmith build out/layout.json --stem mytown
```

Writes one file per chunk — `out/mytown-r00c00+17.slab.txt` and friends —
plus `out/city-raster.svg`, and prints the verification report followed by a
map of which chunk covers which tile range.

Chunks are cut on a **spatial grid** and the printed map shows which cells are
written and which were skipped as open country — on a typical town about a
fifth of the map is empty field and is never emitted at all.

Detection and pasting pull opposite ways: a fine grid can see (and skip) more
empty ground, but writing one file per cell would mean dozens of pastes. So
`build` detects on a fine grid, then **packs** the surviving cells back up to
the asset budget, walking the grid so each emitted slab is a contiguous run of
neighbouring cells. Each file is therefore a connected swathe of the map — you
can paste a subset and get a coherent piece of town, though the split is chosen
by the byte budget, not by district boundaries.

Useful flags: `--style {medieval,cyberpunk}`, `--seed N`, `--storeys N`
(ceiling on building height, default 3), `--no-roofs`, `--no-bridges`,
`--max-assets N` (per-chunk asset cap), `--chunk-tiles N` (detection grid, 8
skips more but emits more before packing), `--keep-open-country` to write the
empty chunks too, `--crop X,Z,W,D` to build one region for a staged in-game
test, `--scale N` for the raster SVG.

**6. Paste into TaleSpire.** Open a slab file, copy the whole contents, and in
build mode press `Ctrl+V`. The slab arrives in hand at the cursor — commit it
with a left press held for about a fifth of a second. Every chunk is committed
at the *same* grid cell without moving the camera; order does not matter, and
you may paste a subset.

**Read [docs/pasting-into-talespire.md](docs/pasting-into-talespire.md) before
your first paste.** The interaction is unforgiving and the failure modes look
like the tool is broken.

## Outputs

| File | What it is |
|---|---|
| `catalog.json` | Your TaleSpire asset index. Machine-local; gitignored. |
| `out/layout.json` | The imported town in 5 ft tiles: walls, gates, roads, districts, buildings, areas. The stable intermediate — hand-edit it if you like. |
| `out/layout.svg` | Polygonal reference map of the import. |
| `out/city-raster.svg` | The rasterised tile grid, with unreachable pockets in red, gates in yellow, added bridges in blue. This is the file to look at when something is wrong. |
| `out/<stem>-rNNcNN[+N].slab.txt` | The pasteable chunks, base64 of gzipped binary. The name is the grid cell it starts at; `+N` means it spans N more cells. |

The procedural path additionally writes `out/city.json` / `city.svg`,
`out/<site>.plan.json` / `.plan.svg`, and `out/<site>.slab.txt`.

## Known limits

- **30,720 compressed bytes per slab.** A whole town does not fit, so `build`
  cuts it on a spatial grid. Every chunk carries a registration marker tile at
  the *whole map's* origin so all chunks share one bounding box — paste them at
  one anchor and they assemble, in any order. Move the camera between pastes
  and they don't.
- **Chunk size trades two ways.** Detection can only skip a region it can see,
  so a fine grid skips more open country; pasting wants few files. `build`
  detects fine and then packs surviving chunks back up to the asset budget,
  walking the grid so each emitted slab is a contiguous region.
- **1 tile = 5 ft, and a creature occupies one tile.** That is the scale
  everything is derived from. A town whose median house is under ~4 tiles across
  has no room to fight indoors; `import` warns when the derived scale lands there.
- **Board limits:** 2000 × 2000 grid units, 1,000,000 assets. `verify` checks both.
- **Pasting is the only ingestion path.** `talespire://` links do not import
  boards, and there is no file-drop or API. Everything goes through `Ctrl+V`.
- **No creatures.** `creatureCount` is always 0 in the slabs we emit.
- **No UI.** `cli.py` is a thin shell over the core modules; a UI would slot in
  without touching generation code, but it does not exist yet.
- **Pasting is manual.** Each chunk is a separate `Ctrl+V` and commit; a
  typical town is a handful of them.

## The other pipeline: procedural city + interiors

Generates a town from a seed instead of importing one, then builds a battle-map
interior for one building.

```bash
python -m citysmith city --seed 42 --size town --style medieval
python -m citysmith sites out/city.json --top 10
python -m citysmith plan out/city.json --site tavern-015
python -m citysmith design out/tavern-015.plan.json
python -m citysmith board out/city.json          # coarse 3D city shell
```

`--size` takes `hamlet | village | town | city | metropolis` (48/72/104/144/200
tiles) or a raw tile count. `sites` scores every building on encounter potential
and shows its reasoning, so you can disagree with the ranking and see which
signal to override. `pipeline` runs city → sites → plan → design in one command.

## Optional: natural language

With `pip install -e ".[ai]"` and `ANTHROPIC_API_KEY` set:

```bash
python -m citysmith brief "a rainy harbour town run by three smuggling families" --describe
```

Claude only chooses generator parameters and writes GM notes. It never emits
coordinates, asset ids, or slab bytes — all geometry is deterministic Python, so
a bad model response gives you a boring city, never a broken one.

## Docs

- [docs/pasting-into-talespire.md](docs/pasting-into-talespire.md) — how to get
  a slab into the game without fighting it.
- [docs/asset-conventions.md](docs/asset-conventions.md) — the footprint,
  pinning, normalization and roof-rotation rules that keep geometry valid.
- [docs/slab-format-v2.md](docs/slab-format-v2.md) — BouncyRock's official slab
  format spec, kept alongside the implementation in `citysmith/slab.py`.
- [CLAUDE.md](CLAUDE.md) — internal engineering notes and module map.

## Testing

```bash
python -m pytest -q
```

84 tests. The slab codec is tested against real TaleSpire slabs in
`tests/fixtures/` — decoding and re-encoding reproduces the original binary byte
for byte. Generator tests assert invariants (no overlapping buildings, no
unreachable rooms, walls resting on floors) rather than exact output, so the
aesthetics can change freely but real bugs cannot come back silently.

## Searching your assets

```bash
python -m citysmith catalog search --group wall --tag stone --limit 10
python -m citysmith catalog search thatched --kind tile
```

## Verifying placement in-game

```bash
python -m citysmith calibrate
```

Emits `out/calibrate.slab.txt`: a 9×3 floor pad with four walls in the middle
row, each hugging one named edge of its own tile. Paste it, look straight down,
and confirm the rotation convention still holds for your packs.

## License

MIT.
