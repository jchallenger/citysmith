# Interior slabs — what hand-builders do that we do not

Research, 2026-08-24. Two sources: the nine community slabs in `library/`
(decoded, 2,382 interior-kit props measured) and two published interiors on
Tales Tavern read for their stated figures. Nothing here is implemented — it is
the evidence a furnishing pass would be built from, and the numbers are the
point.

**Read the caveats at the end before treating any single number as settled.**

---

## The measurements

### 1. Interior props are never on a cell centre

**2 of 2,382** hand-placed interior-kit props sit on an exact cell centre —
0.1%. The x and z fractions are spread across the whole range with no mode
worth naming.

`build._dress` places every prop at `(tx + 0.5, tz + 0.5)`. Every one, exactly
centred, on a 5 ft square. That is not how a room is furnished; it is how a
board game lays out counters.

### 2. But they *are* squared up: 84% on quarter turns

Of the same 2,382: **84% are at 0, 6, 12 or 18** — the four quarter turns.
The remaining 16% are scattered across the other twenty steps, which reads as
deliberate: a chair pushed back from a table, a crate dropped at an angle.

`build._dress` uses `rng.randrange(24)`. Uniform. So five sixths of our
furniture sits at 15°, 45°, 105° — which reads as debris rather than as
furniture, and is the single cheapest thing on this page to fix.

### 3. Props stack on other props

Measured against the floor tile beneath each prop, in the three genuinely
interior builds: **18 of 26**, **19 of 30** and **4 of 8** props sit *above*
the surface under them rather than on it. Tankards on tables, books on shelves,
a lantern on a crate.

Ours all sit at floor level, because `_dress` passes one `y` for the whole
room. `Food & Drink` is the kit that proves the point: **44 of its 45 medieval
props are smaller than a cell** — they are tabletop items, and we put them on
the floor.

### 4. Furniture hugs walls, except when the room is a display

| build | median distance to nearest wall | within one tile |
|---|---|---|
| shogun guard house | 0.71 tiles | 78% |
| small cabin (cellar entrance) | 1.06 tiles | 47% |
| feywild bastion | 2.34 tiles | 22% |

The first two are ordinary rooms; the third is a show space with furniture set
out in the middle on purpose. So "against the wall" is the rule and the
exception is a room whose *purpose* is the middle.

`_dress` already prefers edge cells — but it then centres the prop in that
cell, which puts it half a tile off the wall rather than against it.

### 5. Density is four to six times ours

Props per footprint cell, interior kits only:

| build | footprint | interior props | per cell |
|---|---|---|---|
| shogun guard house | 10x16 | 77 | 0.48 |
| feywild bastion | 12x12 | 59 | 0.41 |
| small cabin | 14x12 | 111 | 0.66 |

`interior.prop_density` is **0.12**.

The published *Tavern and Inn Combo* (Tales Tavern) states its split directly:
**369 tiles and 142 props**, 511 assets over three storeys in 16x18 — 28% props,
and 3.5k of the 30k byte cap. Our Chapel of Hermes is 1,289 assets for a
comparable building, about 1% props: we spend our budget on floor decks and an
apron, they spend it on contents.

### 6. One placed prop in five is bigger than a cell

**468 of 2,382** placed interior props span more than one cell. In the medieval
catalogue the ratio is worse: **48 of the 72 `Furniture` props (67%)** are
bigger than a cell — a table is 1.97 x 1.00, a double bed 1.98 x 2.00.

We place every prop centred on one cell at a random angle. A two-cell table
centred on one cell and rotated 15° overhangs its neighbour and pushes through
the wall behind it. **This is the most likely root cause of "furniture mostly
doesn't work now"** — it is the prop version of the rule CLAUDE.md already
states for tiles: read `ColliderBoundsBound`, do not assume the shape.

### 7. The kit we never open

Medieval interior prop kits, by size:

| kit | props | bigger than a cell | what is in it |
|---|---|---|---|
| `Misc. Interior` | 163 | 37% | rugs (15), paintings (13), banners (16), books (15), papers (11), household (31) |
| `Furniture` | 72 | 67% | tables (18), chairs (15), beds (9), cabinets (9) |
| `Containers` | 56 | 25% | crates, barrels, sacks, jugs, pottery |
| `Food & Drink` | 45 | 2% | food (32), drink (8), dinnerware (4) |
| `Lights` | 43 | 23% | torches, lanterns, candles, braziers |
| `Crafting` | 14 | 50% | alchemy benches, smithy tools |

`Misc. Interior` is the largest of them and our palette essentially never
reaches it. Rugs, paintings and banners are exactly what makes a room read as
lived-in rather than as a floor with objects on it — and `palette.props` asks
for them with free-text terms (`_prop("table")`), which is the loose matching
CLAUDE.md warns about everywhere else.

### 8. There are two published forms, and empty is one of them

- **Furnished**: *Tavern and Inn Combo* — 142 props in 288 cells over three
  storeys, "tavern-themed assets 56.8% of the build".
- **Empty shell**: *Player Residence [Empty Interior]* — 10 rooms and a cellar,
  deliberately unfurnished. The author's framing is that the GM decides whether
  a room is "a trophy hall, alchemy lab, library, or torture… er, guest rooms".

So an unfurnished interior with named rooms is **not a failure state**; it is
one of the two things people publish. What we currently emit is closer to the
empty shell than to a broken furnished one, which is worth knowing before
spending a session on props.

A third pattern worth noting: *Black Ivory Inn* is published as **three
separate maps**, one per floor. That is a third option beside stacking and
spreading levels — a board per storey — and it is what a big multi-level
building probably wants.

---

## What this implies, in order of cost

Not implemented. Ordered cheapest-first, with the finding each rests on.

1. **Quarter turns by default** (§2). One line: deal `rng.choice((0, 6, 12, 18))`
   and take an off-axis step one time in six.
2. **Stop centring** (§1, §4). Offset the prop within its cell toward the wall
   it is against, rather than to the middle of the cell.
3. **Respect the footprint** (§6). A prop wider than a cell needs its cells
   reserved and its rotation chosen so it lies along the wall, not across it —
   `build.rotated_footprint` already does the geometry for tiles.
4. **Surfaces, then things on surfaces** (§3). Place tables and cabinets first,
   record their top height, then put `Food & Drink` and books on them. This is
   the one that needs new structure rather than a tweak.
5. **Raise the density** (§5) — but only after 1-4, because more of a wrong
   placement is worse than less of it.
6. **Pin the prop queries to kits** (§7), the same way the tile roles are
   pinned, and open `Misc. Interior` for rugs, paintings and banners.

---

## What this evidence cannot tell you

- **The sample is small and skewed.** Nine slabs; three are genuinely interior
  rooms, and of those one is Shogun, one is Feywild and one is modern. The
  density figures (§5) rest on n=3. The placement figures (§1, §2, §6) rest on
  2,382 props and are the ones worth trusting.
- **`library/` has empty folders** — `farm`, `inn`, `village` — from a harvest
  that did not find those pages. Tales Tavern demonstrably has tavern and inn
  interiors; more samples would sharpen §4 and §5 considerably.
- **Whether a playable interior should be roofed is unresolved.** The shogun
  guard house is roofed (112 roof pieces, walls topping at 3.0); the feywild
  bastion is open. Two builds, two answers, and neither was built as a battle
  map. Our roof-off default rests on a different argument — that the camera
  cannot see in — which stands on its own.
- **Nothing here measures play.** Every figure is about how a build looks
  decoded, not about whether a room runs well at the table.
