# Marsh: a wetland surface, and the exclusion that hid one

A design pass over standing water and the ground around it. Every number here
was measured off `catalog.json` and off a built board (Sedgewater, 227x204
tiles), not estimated.

Companion docs: `docs/district-surfaces.md` (what a town is paved with),
`docs/fencing.md` (boundaries, and why they are not on the grid),
`CLAUDE.md` (the module map and the asset-geometry rules this follows).

## 1. Why a fen could not be built

citysmith had two watery things and neither is a wetland. `WATER` is open
water: dropped `build.WATER_SURFACE_DROP` (1.5) below grade, filled with a
translucent column, floored with a bed chosen so the depth reads from above.
`GROUND` is everything else. A marsh is neither, and asking for one produced a
pond in a field.

The assets were there the whole time. The Nature kit ships a complete wetland
vocabulary -- four swamp floors, ten reeds and horsetails, four lily pads, and
a modular swamp tree -- and **not one of them could be reached**:

```python
_WRONG_SETTING = (
    "desert", "shogun", "jungle", "snow", "palace", "marble", "moorgoth",
    "dungeon", "harbor", "ship", "sand", "swamp", "pirate", "temple of",
)
```

`swamp` is on the list of *another place*, applied by name to every
village-appropriate role. That guard earned its position -- it is what stops a
free-text query for a floor wandering off into a desert, which is how
Candlewell ended up paved in desert tiles with shogun interiors -- and this
pass does **not** relax it.

**A `name=` pin is not a search.** `_tile(name="Swamp floor 1x1")` never
consults `exclude`, which is how `lane_earth` has been built from a swamp tile
since long before this work: a back lane is trodden wet mud and that is the
asset for it. The marsh roles use the same door. The exclusion is untouched and
still protects every free-text query in the file.

## 2. What the assets actually measure

Read before pinning, per the standing rule that shape assumptions are bugs
waiting for a big enough map.

| asset | footprint | height |
|---|---|---|
| `Swamp floor 1x1` | 1.0 x 1.0 | **0.5** |
| `Swamp floor 2x2`, `2x2 puddle`, `2x2 puddles` | 2.0 x 2.0 | **0.5** |
| `swamp reed 01` | 1.28 x 1.03 | 1.60 |
| `swamp reed 02` | 0.67 x 0.59 | 1.59 |
| `swamp reed horsetail 01-05` | 0.08-0.58 square | 0.79-1.59 |
| `swamp lily pad 01-03`, `lily flower` | 0.46-0.90 | 0.04-0.27 |

**The floors are 0.5 tall, exactly like grass and tilled earth.** That single
fact is why the pass is small: a marsh cell needs no special casing anywhere.
It goes through `_lay_terrain` as an ordinary surface with a 1x1 role and a 2x2
block twin, `Builder.surface()` lays it by its top so it meets grass flush, and
a creature stands on it at grade.

The puddled 2x2 variants are what make a fen read as a fen without a single
prop: they carry standing water in the tile itself.

## 3. The surface class

`raster.MARSH`, painted **after field and before water** -- a wetland is a
sheet of wet ground with pools in the hollows of it, and reversing those two
lines paints the fen over its own ponds.

Three set memberships decide everything else, and each is a different question:

- **`WALKABLE`: yes.** The tile is solid matter at grade.
- **`OPEN`: no.** A marsh is not a *way*. Nobody's front door opens onto a bog
  and the street network must not be routed through one. This is exactly how
  `FIELD` has always behaved -- also walkable terrain, also excluded -- so the
  precedent was already set.
- **the street pass's `over` set: yes.** A causeway or a reedcutters' drove has
  to be able to cross the fen. Left out, every way into a wetland stops dead at
  its edge.

Also excluded from: market-square siting (`_place_plaza`), wall-tower siting
and rampart stairs (nothing is founded in a bog), and `npcs._standable` (no
villager is stood in the reeds waiting for the party).

### The consequence, and it is not a bug

**A marsh is a barrier to the walkable network.** On the first Sedgewater with
a real fen, `verify` failed the build twice over: the three reedcutters' cots
standing in the fen dropped building access to 87.5% with "three doorways
opening into sealed courtyards", and 790 cells of dry ground behind the fen
were reported as a second disconnected district.

Both were right, and both were fixed on the *map* rather than by weakening the
rule:

- the cots got droves out to them, which is what a reedcutter's cot has in life
- the fen was carried to the board edge, because a wetland either runs off the
  map or it fences off whatever is behind it

## 4. Dressing

**Reeds grow in beds.** A flat rate produced an orchard when the woodland pass
tried it, and it produces a lawn of reeds here for the same reason. Density
follows a value-noise field at `REED_CELL = 7` -- finer than `CANOPY_CELL`,
because a reed bed is a smaller thing than a stand of pines, and at 14 a whole
fen came out either uniformly thick or uniformly bare. Not scaled by `detail`:
reeds are the fen's own vegetation, not human dressing.

**Lily pads float, and only in a fen.** Every pad is under a tile across and
under 0.3 tall. They are laid on the waterline rather than the ground -- the
one prop in the build that is -- at a lift derived rather than assumed:
`_fill_water` steps up from the bed by the water tile's own height and every
bed drop is a whole multiple of that step, so the topmost tile always seats
with its underside at `here - WATER_SURFACE_DROP` whatever the depth.
`pool_cell()` gates them on marsh within two cells, so a tidal quay and a mill
race do not get carpeted.

Measured on Sedgewater: **1,497 reeds added exactly 2 overlapping props** and
lowered the board's overall prop-overlap rate from 4% to 3%. The scatter's
oriented-box collision test handles them.

## 5. What is NOT built: the trees

`marsh-trees` in `tasks.json`, open, and deliberately not guessed at.

The swamp tree is modular like the pine -- `base 01/02`, `mid 01-04`, `top` --
but the shapes do not behave like the pine's:

| piece | footprint | height |
|---|---|---|
| `Swamp tree base 01` | 2.15 x 3.09 | 4.03 |
| `Swamp tree mid 01` | **5.25 x 6.46** | 2.01 |
| `Swamp tree mid 04` | 0.81 x 0.85 | 2.01 |
| `Swamp tree top` | 2.29 x 2.30 | 2.05 |

The canopy is **over five tiles across and wider than its own trunk**, and
`mid 04` at 0.81 wide is plainly a trunk extender rather than a canopy layer.
Which piece stacks on which, and whether they sit concentric at every rotation,
is precisely what `_plant_conifer`'s comment records as having been settled for
the pine *by probing and not by reading* -- and that kit is far better behaved
than this one. A six-tile canopy hung wrong is visible from anywhere on the
board.

So: sweep the stacking with a probe, orbit four sides plus overhead, keep a
known-good pine in frame as a control. Then place them.

Until then a fen is reed bed, puddled ground and open water with lily pads on
it, which is a water meadow rather than a cypress swamp -- a real landscape,
and an honest one.

## 5a. What it looks like on a board (2026-08-26)

Pasted as 16 tiled chunks onto a fresh board and flown. `docs/images/` is not
updated; the captures are in `out/flyby/sedgewater-*`.

**What works, read from two pitches.** Reeds stand in beds with open water
meadow between them -- the noise field is doing visibly what it was put there
for, and a flat rate would have read as a lawn. The pools carry lily pads that
are legible from a board-height oblique. The reedcutters' cots stand *in* the
fen with their droves running out to them, which is the shape the brief asked
for and the shape `verify` forced.

**What does not work, and it is the ground.** At board distance the wet sheet
**reads as a ploughed field**: the tile seams dominate, the tone is a flat dark
brown, and the puddles in `Swamp floor 2x2 puddle/puddles` do not carry at all.
Beside the real tilled earth on the east side of the same map they are hard to
tell apart, which is the one thing this pass was for. Two candidate causes, not
yet separated:

- the puddle detail is small relative to a 2x2 tile at that camera height, so
  it is a *distance* problem and the fen is fine at eye level
- the variant spread favours whichever name the resolver's hash lands on, so a
  run of plain `Swamp floor 2x2` may be dominating the puddled ones

The second is measurable off the slabs and was not measured; the first wants a
capture at eye level, which the camera's pitch clamp refused on the day. **Do
not "fix" the palette until one of the two is established** -- that is the
guess-instead-of-measure move this file already records twice.

**A quay rail is being built round the fen pools.** `_lay_quays` fires where
water meets paved ground, and a lane runs along the north edge of two pools, so
they come out with `Harbor Fence 02` along them -- 132 on this board. It reads
as a village duck pond rather than fen water. Defensible where a track really
does run along the bank; wrong for the wild parts of a marsh. The quay pass has
never been taught about wetland.

## 6. Reporting it

`verify.feature_report` gained a `marsh` entry, on the same three branches as
field walls: offered and built, offered and not built (**fail**), not offered
(and say so).

Its evidence is `marsh_2x2`, `marsh_reed` and `marsh_lily` -- **never `marsh`**,
which resolves to the same `Swamp floor 1x1` as `lane_earth`. Keyed on that, a
board with one trodden lane and no fen at all would report a wetland as built.
The first version of the *test* for this walked into the same trap and passed
with the terrain pass torn out.

That is the third time on this feature that a shared asset defeated an
id-based check. The other two are in §6 of `docs/fencing.md`'s neighbour and in
`_fences_built`, which had to stop asking "is a `yard_fence` on this board"
because `_lay_yards` builds from that role too. **An asset id cannot name the
pass that placed it.** Where that matters, the pass records what it did --
`Builder.layer_of`, `Builder.fence_pieces` -- and the check reads that.
