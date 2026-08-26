# citysmith

Turn a fantasy-town map export into a playable **TaleSpire** board.

citysmith reads a GeoJSON town from [Watabou's Medieval Fantasy City
Generator][mfcg] or from [Fantasy Town Generator][ftg], scales it to a 5 ft tile
grid, rasterises it into ground / street / water / building / wall cells,
dresses it with real assets **from your own TaleSpire install**, and emits
`.slab.txt` files you paste into the game with `Ctrl+V`.

It also has a self-contained procedural city generator and an interior
floorplan builder, but the import path is the one that produces a whole town.

[mfcg]: https://watabou.github.io/city-generator/
[ftg]: https://www.fantasytowngenerator.com/

---

## The process, end to end

```mermaid
flowchart TB
    TS["TaleSpire install<br/>your packs, your assets"]
    TS -->|"citysmith catalog build — once per machine"| CAT[("catalog.json")]

    IMP["citysmith import<br/>sniffs the generator, derives the tile scale"]
    M["Watabou MFCG export"] --> IMP
    F["Fantasy Town Generator export"] --> IMP
    IMP --> LAY[("out/layout.json<br/>the stable intermediate, in 5 ft tiles")]
    IMP -.-> LSVG["out/layout.svg"]
    LAY --> VER["citysmith verify<br/>optional: does it play?"]

    subgraph BUILDSTEP["citysmith build"]
        RAS["raster<br/>polygons to cells: footprints, doors, gates"]
        PAL["palette<br/>semantic roles to real assets"]
        BLD["builder<br/>cells to placements"]
        CHK["verify<br/>measures the emitted boxes, not the plan"]
        RAS --> BLD
        PAL --> BLD
        BLD --> CHK
    end

    LAY --> RAS
    CAT --> PAL
    CHK --> SLAB[("slab.txt chunks<br/>+ paste-order.txt")]
    CHK -.-> RSVG["out/city-raster.svg"]

    SLAB --> PASTE["Ctrl+V in TaleSpire<br/>every file at the same cursor cell,<br/>in the listed order, camera straight down"]
    PASTE --> BOARD(["A town you can walk around"])
```

Three things in that diagram are load-bearing and easy to miss:

- **`catalog.json` is built from *your* TaleSpire install** and is not
  committed. Two people with different DLC will get different-looking towns
  from the same layout, and that is by design — citysmith can only build with
  what you own.
- **`layout.json` is the stable intermediate.** It is plain JSON in 5 ft tiles.
  Hand-edit it, keep it in version control, diff it. Everything downstream is
  deterministic from it plus a seed.
- **The paste order is not the filename order.** `build` writes
  `<stem>-paste-order.txt` next to the slabs; follow it.

---

## Setup

### Prerequisites

| | |
|---|---|
| **TaleSpire, installed** | citysmith reads asset ids, names, tags and collider bounds straight out of `<install>/Taleweaver/<pack-uuid>/index.json`. Whatever packs you own are what it can build with. Nothing is bundled. |
| **Python 3.10+** | Developed on 3.14. |
| **Dependencies** | None. The core is stdlib-only. `anthropic` is an optional extra used by one command (`brief`); everything else works offline. |

### Install

```bash
git clone https://github.com/jchallenger/citysmith.git
cd citysmith
pip install -e .
```

Or skip installing and run `python -m citysmith ...` from the repo root.

### Index your assets (once per install, or after buying packs)

```bash
python -m citysmith catalog build
```

Writes `catalog.json`. It finds TaleSpire via Steam automatically; if it can't,
set the `TALESPIRE_PATH` environment variable or pass
`--talespire-path "D:\SteamLibrary\steamapps\common\TaleSpire"`.

Check it worked:

```bash
python -m citysmith catalog search --group wall --tag stone --limit 5
```

---

## Quick start

**1 · Get a map.** Generate a town at <https://watabou.github.io/city-generator/>
and use its export menu to save the **JSON** (GeoJSON) export. Fantasy Town
Generator exports work too — citysmith sniffs which generator a file came from
by reading its features, because **the file extension does not tell them
apart** (both generators ship both `.json` and `.geojson`).

**2 · Import it.**

```bash
python -m citysmith import mytown.json
```

Writes `out/layout.json` and `out/layout.svg`.

The tile scale is *derived*, not guessed. MFCG exports have no real-world
units, so the map is anchored to a median building width (`--house-ft`, default
35 ft) and the tile count follows. 35 is chosen for play: below about 30 most
buildings are too small to stand a party in — at 20 ft only 31% clear a 3×3
interior, at 35 ft 94% do, and above 35 buys nothing further. FTG states its
own scale (1 unit = 1 metre) so nothing is inferred there; the two routes agree
within 4% on every export tested.

`--margin-ft` (default 60) sets how much suburb is kept outside the walls;
`--no-clip` keeps the whole export. Playability warnings print here.

**3 · Check it plays** — optional but cheap:

```bash
python -m citysmith verify out/layout.json
```

**4 · Build the slabs.**

```bash
python -m citysmith build out/layout.json --by-region --stem mytown
```

Writes `out/mytown-rNNcNN.slab.txt`, `out/mytown-paste-order.txt` and
`out/city-raster.svg`, then prints the verification report and a table of which
chunk covers which tile range.

**5 · Paste into TaleSpire.** Open each slab file, copy the whole contents, and
in build mode press `Ctrl+V`. The slab arrives *in hand at the cursor*; commit
it with a left press **held for about a fifth of a second** — a zero-duration
click is swallowed by Unity's input polling.

> **Read [docs/pasting-into-talespire.md](docs/pasting-into-talespire.md)
> before your first paste.** The interaction is unforgiving and every failure
> mode looks like the tool is broken rather than the input.

The three rules that matter most:

1. **Pitch the camera straight down and leave it there.** A paste anchors on
   the cursor's *ray hit*, so with the camera tilted, anything already on the
   ground slides the anchor toward you.
2. **Paste every file at the same cursor cell**, in the order
   `paste-order.txt` lists. Every chunk carries the map's registration markers,
   so they present an identical bounding box and assemble with no measuring.
   The last file covers the anchor cell and must land last.
3. **Empty the hand after each paste** with a right-click *tap*. A held
   right-click reads as a drag and the slab stays in hand — so every later
   click stamps another copy of the town.

---

## Build modes

| Mode | Flag | What it is for |
|---|---|---|
| **Tiled** *(recommended)* | `--by-region` | One slab per map region, every layer together. Chunks tile the map without overlapping, so nothing is ever pasted over anything and no chunk can inherit another's height. |
| Layered | *(default)* | Landscape first, then structures over it. Fewer, larger files, and lets you re-paste just the buildings after a change — at the cost of the second layer resting on the first. |
| Per building | `--per-building` | One slab per building plus one for the rampart. Dozens of files; the mode for checking a single house without re-laying the town. |

**`--multi-slab`** additionally writes `<stem>.multislab.slab` — one JSON
document holding every chunk *with the position it belongs at*. Paste it with
LordAshes' [MultiPasteSlabsPlugin][mps] or [SlabPlugin_CCM][ccm] (Thunderstore,
needs BepInEx) and the whole town lands in one keystroke: no cursor aiming, no
shared bounding box, no paste order, no camera discipline. Builds in this mode
carry **no registration markers**, so the two stray marker tiles outside the
map corners are gone too.

The plugin is third-party and does break on TaleSpire updates, so the ordinary
chunk files remain the default and a vanilla install needs nothing extra.

[mps]: https://thunderstore.io/c/talespire/p/LordAshes/MultiPasteSlabsPlugin/
[ccm]: https://thunderstore.io/c/talespire/p/LordAshes/SlabPlugin_CCM/

Other useful flags: `--style {medieval,cyberpunk}`, `--seed N`, `--storeys N`
(ceiling on building height, default 3), `--no-roofs`, `--no-bridges`,
`--max-assets N`, `--chunk-tiles N`, `--keep-open-country`,
`--crop X,Z,W,D` (build one region for a staged in-game test), `--scale N`,
`--fence-style NAME` (how field boundaries are built — see
[docs/fencing.md](docs/fencing.md)), `--no-quarters` (do not vary lane and yard
surfaces by derived quarter; the measurement already switches this off on a
town whose trades do not cluster).

---

## What it actually builds

**Buildings are dealt one of four fabrics** by kind, so importance reads off the
architecture rather than off storey count:

| Tier | Kinds | Walls | Roof |
|---|---|---|---|
| civic | temple, guildhall, manor, barracks | dressed castle stone, arched windows, fancy door | grey slate |
| trade | tavern, shop, apothecary, smithy | timber frame, better door, glazed street front | terracotta tile |
| common | house and everything else | timber frame, peasant door | thatch |
| utility | warehouse, stable, shed | dark boarding, **one storey, no windows** | thatch |

Glazing is keyed on which face a wall segment sits on — dense at the front,
sparse on the flank, **never at the back** — and a building fronting a main
street gets the show facade. Single-storey buildings get a lantern by the door
where a porch would sit level with their own eaves.

**How tall a building is depends on the settlement, not on a die roll.** The
importer classifies the town by size and by how tightly it is built, and deals
storeys from that: a hamlet is single-storey cottages with a taller inn or
hall, a village stacks its trades on the through road, a town stacks inside its
walls where the plot is expensive and stays low on the outskirts. Warehouses,
stables and sheds are one storey everywhere. `--storeys N` still caps the lot.

**A building standing clear of its neighbours gets a yard** — enclosed with
timber fence, surfaced, and dressed with whatever its trade works with. The
test is a measurement: three clear cells to the nearest neighbour. On East
Tradebourne that is 230 of 989 buildings, and the packed centre keeps its
street wall.

**The ground is paved by what the ground is for.** Main street, cart street,
lane, plaza, yard and field edge each get their own material, and where a town
is big enough for its trades to actually cluster, the lanes in a craft quarter
are surfaced differently from the ones in a market quarter. That last part is
switched on by measurement and not by hope: `quarters.clustering_lift` compares
the observed clustering against a shuffled baseline and returns nothing at all
below 1.20×, which is what it does on both the hamlet and the village.

**Field boundaries** are built from the export's own boundary polylines, laid
along each segment at its true bearing so a wall turns a corner once instead of
stair-stepping the grid. `--fence-style` picks from seven: `drystone` (the
default), `drystone-plain`, `drystone-tall`, `drystone-corner`, `hedge`,
`hedgerow` and `paling`. A *yard* fence is a different role and a different
material — the timber post-and-rail in the shots below.

**The town wall** is a faced rampart 35 ft to the wall-walk and 45 ft to the top
of the merlons, with square mural towers at every corner and flanking every
gate. Gate passages are cut square through the band so the portcullis has
straight jambs to hang on, and a stair runs up the inside of the wall at each
tower — parallel to the curtain, landing flush with the wall-walk, and never
on the field side, because a stair outside a town wall is a siege ramp for the
enemy.

### What that looks like at three scales

Three real Fantasy Town Generator exports, built and pasted end to end. The
numbers are what the tool actually reported, not estimates; every screenshot
below is off the finished board:

| Town | Tiles | Buildings | Assets | Chunks | `--chunk-tiles` |
|---|---|---|---|---|---|
| Pelvesthollow — a hamlet in woodland | 176 × 184 | 35 | 20,514 | 8 | 64 |
| Graybank — a river village | 434 × 306 | 150 | 94,139 | 22 | 80 |
| East Tradebourne — a walled town | 739 × 598 | 989 | 411,106 | 102 | 112 |

East Tradebourne is 41.1% of the per-board asset limit, so a town roughly two
and a half times its size is the ceiling.

**The three are not the same town at three sizes, and that is the point.** Run
`build` on each and the report says so in its own words:

| | Pelvesthollow | Graybank | East Tradebourne |
|---|---|---|---|
| storeys | `1:34 2:1`, mean **1.03** | `1:78 2:71 3:1`, mean **1.49** | `1:167 2:575 3:247`, mean **2.08** |
| yards | 20 of 35 | 89 of 150 | 230 of 989 |
| field walls | 9 runs | 5 runs | 22 runs |
| quarters | none — trades cluster at 0.00× | none — 0.86× | **1.28×** → residential 70%, craft 15%, market 9% |

Height used to be `randint(1, 3)` gated on footprint area, which gave every
town the same skyline — about a third at each height, mean 2.0, whether it had
thirty-five buildings or a thousand. A hamlet is single-storey cottages with a
taller hall; a walled town stacks inside its circuit where the plot is
expensive. The quarters row is the same idea for trades: they are *derived* and
only used when they are actually there, and on two of these three towns the
measurement says they are not.

#### Pelvesthollow — 35 buildings

![Two single-storey thatched cottages either side of a cobbled crossroads, each
inside a post-and-rail fence, with a market stall and a table of wares in the
yards](docs/images/pelvesthollow-yards.jpg)

Thirty-four of the thirty-five buildings here are one storey. Common-tier
fabric: timber frame, thatch, a peasant door. The lane is cobble laid by its
*top* surface, not its bottom — cobble is 0.25 thick and grass is 0.5, so
aligning them at the base would put a 15-inch kerb along every street.

**A building that stands clear of its neighbours gets a yard**, enclosed and
dressed with the working life of its trade. It is gated on a measurement rather
than given to everyone: a building needs three clear cells to the next one
before it claims any ground, so a packed street front stays a street front.

![The same crossroads from a lower angle, a butcher's block and carcass under an
awning in the right-hand yard, a dry-stone field wall running away to the
south-east](docs/images/pelvesthollow-crossroads.jpg)

Two kinds of boundary in one frame, and they are different features: the timber
post-and-rail around each yard, and the **dry-stone field wall** at the lower
right, which encloses farmland rather than a building. The yard dressing is
keyed on the building's trade — the butcher gets a block and an awning, not a
generic scatter of crates.

![A tilled field of wheat stooks and hay bales, ringed by a dry-stone wall that
turns a right-angled corner, pines and stumps around
it](docs/images/pelvesthollow-fields.jpg)

**Field walls are surveyed lines, not stair-steps.** A boundary in the export
is a polyline at whatever angle it likes; panels are laid along each segment at
its true bearing and never across a vertex, so a wall that turns a corner turns
it once rather than climbing a flight of grid steps. Seven styles are available
(`--fence-style`); this is the default.

#### Graybank — 150 buildings

![A two-storey terracotta-roofed trade building with a market stall, barrels
and sacks in its fenced yard, a thatched cottage with its own fence
opposite](docs/images/graybank-trade.jpg)

Trade tier beside common tier. Importance reads off the *roof material* rather
than off storey count, because a facade that changes material at the corner
reads as a mistake — and roofs are dealt per building rather than per map, so
a street is not one material end to end. Graybank is a village and so it
stacks: 78 buildings at one storey, 71 at two, one at three — neither the
hamlet's uniformity nor the town's density.

![A terracotta-roofed house and a thatched one, each in a fenced yard with a
bench, a workbench and market awnings](docs/images/graybank-yards.jpg)

Out on the edge of the village, where there is room, both of these get a yard.
89 of Graybank's 150 buildings do; the rest stand closer together than the
three-cell gap the rule asks for and get none.

A single-storey building gets a lantern by the door instead of a porch: a porch
hood seats at `storey_h + 0.5`, which on a one-storey cottage is level with its
own eaves — a second roof grafted onto the first.

#### East Tradebourne — 989 buildings, walled

![The town wall: coursed stone stepping diagonally with a crenellated parapet,
thatch and terracotta roofs and fenced yards behind it, woodland thinning
away outside](docs/images/east-tradebourne-rampart.jpg)

A faced rampart, 35 ft to the wall-walk and 45 ft to the top of the merlons.
The mass is a full-cell block with the thin curtain pieces hung on the faces
that show — a wall built from 0.5-deep curtain pieces laid one per cell scores
perfectly on any check that reads the tile grid while being visibly
see-through on the board. The woodland thins as it approaches because a rampart
counts as *built* for the falloff; seeded from buildings alone, pines grew
flush against the masonry.

![A dressed-stone civic hall with arched windows under a terracotta roof,
standing in its own fenced yard with a workbench, a grey slate roof to one side
and thatch to the other](docs/images/east-tradebourne-tiers.jpg)

Three roof materials in one frame — terracotta, slate and thatch — and the
civic tier's dressed stone with its arched windows. Roofs are dealt per
building rather than once for the map, so a street is not one material end to
end. This hall is also a yard on the town's edge: fenced, with the workbench
and crates its trade works with.

![Streets of a town centre: dark cobble carriageway, pale gravel lane, cream
flagstone paving and grass verges, with a civic building on the
left](docs/images/east-tradebourne-quarters.jpg)

Four surfaces meeting: the cobbled cart street, a pale gravel lane, flagstone
paving and the grass verge between paving and building. East Tradebourne is the
only one of the three towns whose trades cluster tightly enough to key lane
surfaces on a derived quarter — 1.28× against a shuffled baseline, against a
1.20× threshold. On the hamlet and the village the same measurement returns
nothing and the feature stays off.

![Three-storey terracotta-roofed trade buildings packed along cobbled streets
with handcarts and barrels between them](docs/images/east-tradebourne-centre.jpg)

The packed centre has no yards at all, which is the yard rule working rather
than failing: a building needs three clear cells to its nearest neighbour before
it claims any ground, so 230 of 989 get one and the street wall survives.

![A green ringed with cream flagstone paving, three-storey trade buildings
around it and thatched cottages in the
foreground](docs/images/east-tradebourne-green.jpg)

FTG exports a market square as a `BUILDING` with `material: PAVEMENT`. Built as
one it is a roofed box over the square, so it is diverted into a plaza instead —
this is that rule working on the file it was written for.

![The quay: a paved waterfront with rope-and-bollard railings along the river
and a timber bridge crossing at the top](docs/images/east-tradebourne-quay.jpg)

FTG marks a bridge by `raised: true` — across all three exports it is true for
exactly five features, and every one is a ~20x20 m `ROAD_TEXTURE_TYPE` quad over
water. `bridge_deck` is pinned to `Harbor Middle 06`: the harbour deck tiles are
1.0 tall and laid by their top, so the underside rests on the water and the
crossing reads as a timber pier rather than a causeway. A thin floor on dock
legs floated; a stone span read as a fortification.

**Sizing the chunks is a two-regime problem, and most people only meet the
first.** Below about 90,000 assets the *byte cap* binds: pick a cell size whose
largest slab lands near two thirds of 30,720, because a different seed can push
it over. Graybank at 80 tiles is 22 chunks and 24,192 bytes — 79% of the cap,
which is about right. Above that, `--max-assets` binds instead and the cell
size stops mattering much; pick the size that *splits least*, because an
oversized cell splits into four where a smaller one would not have split at
all.

**Anything that adds dressing eats the headroom, and it has to be re-measured
after.** These towns were rebuilt with yards, field walls and a wider surface
palette, and East Tradebourne's largest slab went from 23,085 bytes to
**30,546 against a 30,720 cap** — 99.4%, still valid but with nothing to spare,
and at 96 tiles the build now *fails* outright with a chunk at 31,739 bytes.
The fix is not a smaller cell but a tighter split: `--max-assets 6500` at 112
tiles gives 114 chunks and a largest slab of **24,204 bytes**, back at 79%.
That is the setting to use on a town this size.

### One board per town

A campaign holds many boards, and `review.ps1 tiled` creates a fresh one for
each map so nothing is ever pasted over anything:

```bash
pwsh tools/review.ps1 tiled -Name gb -Stem gb -OutDir out\graybank -ShotEvery 6
```
```bash
pwsh tools/ts.ps1 rename -Text "Graybank"
```

`-ShotEvery N` thins the progress screenshots; a 114-chunk town otherwise takes
228 of them. New boards are called `Unknown Realm N` and the numbers get
reused, so name them. See
[.claude/skills/talespire-boards/SKILL.md](.claude/skills/talespire-boards/SKILL.md).

---

## Outputs

| File | What it is |
|---|---|
| `catalog.json` | Your TaleSpire asset index. Machine-local; gitignored. |
| `out/layout.json` | The imported town in 5 ft tiles: walls, gates, roads, districts, buildings, areas. The stable intermediate — hand-edit it if you like. |
| `out/layout.svg` | Polygonal reference map of the import. |
| `out/city-raster.svg` | The rasterised tile grid, with unreachable pockets in red, gates in yellow, added bridges in blue. **This is the file to look at when something is wrong.** |
| `out/<stem>-rNNcNN.slab.txt` | The pasteable chunks: base64 of gzipped binary. |
| `out/<stem>-paste-order.txt` | The order to paste them in. Not alphabetical. |

The procedural path additionally writes `out/city.json` / `city.svg`,
`out/<site>.plan.json` / `.plan.svg`, and `out/<site>.slab.txt`.

---

## Known limits

- **30,720 compressed bytes per slab.** A whole town does not fit, so `build`
  cuts it into chunks. Every chunk carries the *whole map's* registration
  markers so they share one bounding box — paste them at one anchor and they
  assemble. Move the camera between pastes and they do not.
- **1 tile = 5 ft, and a creature occupies one tile.** Everything is derived
  from that. A town whose median house is under ~4 tiles across has no room to
  fight indoors; `import` warns when the derived scale lands there.
- **Board limits:** 2000 × 2000 grid units, 1,000,000 assets. `verify` checks
  both.
- **Pasting is the only ingestion path.** `talespire://` links do not import
  boards, and there is no file-drop or API. Everything goes through `Ctrl+V`.
- **No creatures.** `creatureCount` is always 0 in the slabs we emit.
- **A walled town currently fails one placement check.** `_lay_city_wall`
  lays only the top course in a rampart cell walled in on all four sides —
  nothing can see the rest — while `verify` samples the second course and so
  reports every such cell as a hole: 732 of East Tradebourne's 2,367. The wall
  is unbroken coursed stone from the ground to the parapet when you look at it,
  and the void only exists where no camera can reach; but the build does print
  `[FAIL]` and the slabs are written anyway.
- **A fenced build fails the prop-collision check, and that one is a false
  positive.** Consecutive fence panels overlap as *bounding boxes* and not as
  meshes, and TaleSpire's drop test is on the oriented collider — measured on a
  real build, where 78 flagged pairs all came out as continuous wall on the
  board. The check does not know that yet, so it reports the pairs and the
  build prints `[FAIL]`. The report names how many of the flagged pairs are
  fence panels so the real overlaps stay visible underneath.
- **No interiors on the town board.** `floorplan.py` builds them, but nothing
  wires an interior onto a second board per building yet.
- **No UI.** `cli.py` is a thin shell over the core modules; a UI would slot in
  without touching generation code, but it does not exist yet.

---

## Walking the party into a building

A town board is where the party travels; a **scene** is where they play. One
building out of the town, opened up, with the people who are in it and four
marks on the floor for the tokens.

```bash
python -m citysmith scene out/graybank/layout.json "halfling"
```
```powershell
.\tools\scene.ps1 enter -Scene graybank-tavern-0014
```

The first writes the board (231 assets, one paste). The second puts it in
TaleSpire -- making a board the first time and **switching to the one that is
already there** every time after. Nothing is ever deleted.

You get the slab, a manifest, a GM brief naming who is inside and why the room
is worth playing in, and a floorplan with the party's starting squares drawn on
it. Occupants are *derived* -- the export names buildings and trades and
carries no people at all -- but a roster you write yourself wins over them.

**Tokens cannot be pasted**: a v2 slab's creature count is always zero. What is
pasted is the marks, and the minis go on them by hand.

[docs/scenes.md](docs/scenes.md) is the full guide, including the four states
of the board record and why a rebuild never overwrites a board.

---

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
tiles) or a raw tile count. `sites` scores every building on encounter
potential and shows its reasoning, so you can disagree with the ranking and see
which signal to override. `pipeline` runs city → sites → plan → design in one
command.

---

## Optional: natural language

With `pip install -e ".[ai]"` and `ANTHROPIC_API_KEY` set:

```bash
python -m citysmith brief "a rainy harbour town run by three smuggling families" --describe
```

Claude only chooses generator parameters and writes GM notes. It never emits
coordinates, asset ids, or slab bytes — all geometry is deterministic Python,
so a bad model response gives you a boring city, never a broken one.

---

## Tools

`tools/` holds the workshop, not the product. Two are worth knowing about:

```bash
python tools/kit_index.py --complete      # which asset kits can build a house
python tools/kit_index.py --kit Tavern    # everything in one kit
```

`kit_index.py` regenerates [docs/asset-index.md](docs/asset-index.md), the
searchable dump of your library grouped by kit. **The kit is the catalog's
`folder`** — `pack` is the DLC and `group_tag` is a form, so neither tells you
whether two pieces belong together.

The `*_probe.py` scripts each build one question as a slab you paste and look
at — roof rotations, wall masses, corner pairings, fence styles side by side,
several large buildings each on their own lot, the generated interior of one
building per trade. The standing rule on this project is that an asset's shape
is never assumed from its name or its measurements; it is probed and read from
four sides. `tools/review.ps1` and `tools/ts.ps1` drive TaleSpire over Windows
synthetic input to do that automatically (Windows only, and the game must be
windowed).

**What is designed but not yet built is tracked, and the tracker is checked
against the code:**

```bash
citysmith tasks              # grouped by state
citysmith tasks --check      # import every claim and report the liars
```

Every entry in `tasks.json` carries *evidence* — the dotted path of the symbol
that exists once it is done, or `test:<name>`. A task marked done whose
evidence is missing is a false claim; one marked open whose evidence already
exists is stale bookkeeping. Both are reported, and the same check runs in the
test suite, so the file cannot quietly drift from the code. It exists because a
feature was designed three times, described as outstanding in two documents,
and deferred by three consecutive passes — a paragraph of prose saying a thing
is unbuilt looks exactly like a paragraph saying it is built.

---

## Docs

- [docs/pasting-into-talespire.md](docs/pasting-into-talespire.md) — how to get
  a slab into the game without fighting it.
- [docs/asset-conventions.md](docs/asset-conventions.md) — footprint, pinning,
  normalization and roof-rotation rules.
- `docs/asset-index.md` — a generated index of *your* asset library, by kit.
  Not in the repo, because it is a dump of your TaleSpire packs; run
  `python tools/kit_index.py` to produce it locally.
- [docs/scenes.md](docs/scenes.md) — one building as a board the party walks
  into: what is derived and what is exported, where the tokens go, and why a
  board is reused rather than rebuilt.
- [docs/ftg-geojson-import.md](docs/ftg-geojson-import.md) — the Fantasy Town
  Generator schema, reverse-engineered.
- [docs/building-massing.md](docs/building-massing.md) — storeys, footprints
  and yards by settlement size, with the measurements behind each threshold.
- [docs/district-surfaces.md](docs/district-surfaces.md) — what a town is paved
  with, and whether "district" is something we can key on at all.
- [docs/fencing.md](docs/fencing.md) — field walls: the seven styles, why a
  boundary is a surveyed line rather than a stair-step, and how TaleSpire's
  prop drop test actually works.
- [docs/interior-slabs.md](docs/interior-slabs.md) — what hand-builders put
  inside a building, decoded from 2,382 community-placed props.
- [docs/board-strategy.md](docs/board-strategy.md) — interior versus exterior
  boards and moving a party between them: what the community recommends, what
  we do instead, and why.
- [docs/branching.md](docs/branching.md) — worktrees, and the policy that
  closes them.
- [docs/slab-format-v2.md](docs/slab-format-v2.md) — BouncyRock's official slab
  format spec, kept alongside the implementation in `citysmith/slab.py`.
- [.claude/skills/talespire-boards/SKILL.md](.claude/skills/talespire-boards/SKILL.md)
  — campaigns and boards: creating, naming, switching, and a board per town.
- [CLAUDE.md](CLAUDE.md) — internal engineering notes, module map, and a long
  record of what was tried and why it failed. Read this before changing
  generation code.

---

## Testing

```bash
python -m pytest -q
```

228 tests. The slab codec is tested against real TaleSpire slabs in
`tests/fixtures/` — decoding and re-encoding reproduces the original binary
byte for byte. Generator tests assert *invariants* (no overlapping buildings,
no unreachable rooms, walls resting on floors, no window on the back of a
building) rather than exact output, so aesthetics can change freely but real
bugs cannot come back silently.

## Verifying placement in-game

```bash
python -m citysmith calibrate
```

Emits `out/calibrate.slab.txt`: a 9×3 floor pad with four walls in the middle
row, each hugging one named edge of its own tile. Paste it, look straight down,
and confirm the rotation convention holds for your packs.

## Third-party content

citysmith **ships no TaleSpire assets and no asset data.** It reads names, tags
and collider bounds out of the TaleSpire installation on the machine it runs
on, at run time, into a local `catalog.json` that is not committed. The
generated `docs/asset-index.md` is the same story and is likewise local. Slab
files the tool emits reference assets by id — they are only meaningful to
someone who already owns the packs.

What the repo does contain, and where it came from:

| | |
|---|---|
| `samples/forest_church.json` | A town exported from [Watabou's Medieval Fantasy City Generator][mfcg]. Kept as a worked example so the pipeline is runnable without generating your own first. |
| `docs/images/*.jpg` | Screenshots of boards this tool built, taken in TaleSpire. They depict TaleSpire assets. |
| `tests/fixtures/*.slab` | Real slabs, used as ground truth for the codec — a codec tested only on its own output is a codec that agrees with itself. They are data files listing asset ids and coordinates, not asset content. |
| `docs/slab-format-v2.md` | Our own description of BouncyRock's slab format, written from the implementation. The authoritative spec is BouncyRock's and lives with TaleSpire. |

TaleSpire and its asset packs are the property of BouncyRock Entertainment.
This project is not affiliated with or endorsed by them. Using it is subject to
whatever terms apply to your own TaleSpire licence, and that is on you.

## License

[Apache License 2.0](LICENSE). Use it, fork it, ship it; keep the notices, and
understand that it comes with no warranty of any kind.
