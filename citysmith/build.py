"""Turn city/floorplan geometry into TaleSpire placements.

All offsets are derived from each asset's measured ``ColliderBoundsBound``
rather than hardcoded, because assets in the same role differ in thickness and
height. Hardcoding those constants is what makes generated maps show floating
walls and sunken floors.

Rotation and the placement coordinate
-------------------------------------
**Verified in TaleSpire, not assumed.** A slab's placement coordinate is the
min corner of the asset's bounding box *after* rotation -- so the footprint
swaps axes on odd quarter turns and the coordinate follows it.

The evidence: two walls placed by TaleSpire itself (asset ``Wall Only With
Window``, 0.5 x 2.0 footprint) copied back out as::

    rot=0    -> x=0.50, z=0.00   occupying x 0.5-1.0, z 0.0-2.0
    rot=270  -> x=0.00, z=3.50   occupying x 0.0-2.0, z 3.5-4.0

The half-tile offset moves from x to z as the piece turns, which only happens
if the stored corner belongs to the rotated box. The earlier assumption --
unrotated box plus centre pivot -- predicts x=0.25/z=2.75 for that second wall
and is wrong; it was caught by a 31-asset calibration slab before it could
ruin a 146,656-asset city.

:func:`place_centered` implements the verified rule. It is exact for quarter
turns (rot divisible by 6), which is all the generators emit; an arbitrary
angle would need a true oriented-bounds calculation.
"""

from __future__ import annotations

import base64
import collections
import contextlib
import math
import random
import weakref
import zlib
from dataclasses import dataclass, field

from .catalog import Asset
from .city import Building, City, Rect
from .palette import Palette, segment_shape
from . import walls as W
from .slab import MAX_COMPRESSED_BYTES, Placement, Slab, SlabError, encode

#: Rotation steps per quarter turn (24 steps in a full turn).
_QUARTER = 6

#: Rotation step indices for the four cardinal facings.
ROT_N, ROT_E, ROT_S, ROT_W = 0, 6, 12, 18

SIDES = ("n", "e", "s", "w")
_SIDE_ROT = {"n": ROT_N, "e": ROT_E, "s": ROT_S, "w": ROT_W}

#: What a placement *is*, for splitting a map into pasteable layers.
#:
#: Splitting by region alone was the original design and it has one structural
#: problem: every chunk after the first is pasted over ground the previous one
#: laid, and a paste comes to rest on whatever is under the cursor. So each
#: chunk can land at its own height, and a whole quarter of the map sits a
#: course above its neighbour with nothing wrong in the file. Terrain meeting
#: terrain at a seam is where that shows, and it is the one thing a reviewer
#: actually notices.
#:
#: Splitting by layer removes that seam entirely: all the ground is one body,
#: pasted once onto bare board, so it cannot disagree with itself. Region
#: splitting still happens *within* a layer when it exceeds the byte cap, but
#: the pieces of one layer are the ones sharing a registration marker and a
#: single paste height.
#:
#: It also makes the build loop usable. Changing a roof no longer means
#: re-laying 11,000 grass tiles to look at it -- paste the structure layer over
#: the landscape that is already down.
LANDSCAPE = "landscape"
STRUCTURE = "structure"
LAYERS = (LANDSCAPE, STRUCTURE)


#: Chunk edge in tiles. 24 tiles is 120 ft -- roughly a city block, so a chunk
#: reads as somewhere ("the quarter east of the bridge") and open country
#: separates into whole skippable chunks. Larger cells mean fewer pastes but
#: stop isolating empty ground; smaller ones skip more and cost more pastes.
DEFAULT_CHUNK_TILES = 24

#: Quadrant tags used when a cell is subdivided, indexed by z then x.
_QUAD_Z = ("n", "s")
_QUAD_X = ("w", "e")

#: Full-cell corner pieces cost a whole tile each. On a 2x3 footprint that is
#: four of six cells, leaving two -- not a room. Where cornering would leave
#: fewer than this many usable interior tiles, thin edge walls are used
#: instead: they sit on the cell boundary and consume no floor.
MIN_USABLE_INTERIOR = 4

#: Orthogonal neighbour offsets, in the same order as :data:`SIDES`.
SIDE_OFFSETS = (("n", 0, -1), ("e", 1, 0), ("s", 0, 1), ("w", -1, 0))

#: Just the offsets, for the many places that walk neighbours without caring
#: which side they are.
NEIGHBOURS = tuple((dx, dz) for _, dx, dz in SIDE_OFFSETS)


#: The falloff is measured in **2x2 blocks**, not tiles, because the terrain
#: optimiser lays open ground as 2x2 tiles wherever four cells agree. A height
#: field that varies per cell breaks every one of those quads: defining it per
#: cell cost a thousand extra tiles along the border and bought nothing, since
#: a quad-sized step is not visible from eye level anyway.
#:
#: Two block-rings of half a tile is a 5 ft fall over 20 ft, and the ragged
#: fringe beyond it is bitten out in 2-tile pieces -- which reads as a coast
#: rather than as the pixel noise a per-cell nudge produced.
EDGE_TAPER_BLOCKS = 2
EDGE_TAPER_STEP = 0.5

#: The most the border may drop, total. One tile-half: enough to show the map
#: ends on ground rather than on a cut, not enough to read as a terrace.
EDGE_TAPER_MAX_DROP = 0.5


def edge_taper(tm, rings: int = EDGE_TAPER_BLOCKS,
               step: float = EDGE_TAPER_STEP) -> dict[tuple[int, int], float | None]:
    """How far below grade each border cell sits; ``None`` means leave it out.

    The map used to stop on a ruler-straight line with a sheer drop to bare
    board, so from outside it read as a cropped rectangle. Two things fix that
    together: the outermost ring steps *down* by one course, and it is
    **ragged** -- a stable per-block nudge takes bites out of it, and the cells
    in a bite are not laid at all. A straight edge one tile lower is still a
    straight edge, so the raggedness is doing the work; the step only stops the
    last tile from ending in a sheer face.

    **The step is the outer ring and nothing else.** It used to spread across a
    falloff four to eight tiles wide, which put a half-tile cliff wherever that
    band's ragged inner boundary happened to fall -- 1,740 lips of
    grass-above-grass sitting in open country with no edge anywhere near them.

    Cells carrying a building or a wall, or next to one, keep their ground: a
    foundation over a hole is worse than a hard edge.

    Two rules keep the raggedness from turning into wreckage. A bite may not
    touch paving or sit beside a block that carries it, so a road reaches the
    edge with shoulders instead of running out over the void. And no kept block
    may be left without a kept neighbour, so the fringe is made of runs rather
    than of 2x2 teeth standing on nothing.
    """
    from . import raster as R

    protected: set[tuple[int, int]] = set()
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.building[z][x] or tm.wall[z][x]:
                for dz in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        protected.add((x + dx, z + dz))

    # Everything below is decided per 2x2 block and then written to that
    # block's four cells, so a quad always agrees with itself and the 2x2
    # terrain pass keeps working inside the falloff.
    blocks_x = (tm.width + 1) // 2
    blocks_z = (tm.depth + 1) // 2

    def cells(bx: int, bz: int) -> list[tuple[int, int]]:
        return [(bx * 2 + dx, bz * 2 + dz)
                for dz in (0, 1) for dx in (0, 1)
                if 0 <= bx * 2 + dx < tm.width and 0 <= bz * 2 + dz < tm.depth]

    def around(b: tuple[int, int]) -> list[tuple[int, int]]:
        bx, bz = b
        return [(bx + 1, bz), (bx - 1, bz), (bx, bz + 1), (bx, bz - 1)]

    border: set[tuple[int, int]] = set()
    for bz in range(blocks_z):
        for bx in range(blocks_x):
            if min(bx, bz, blocks_x - 1 - bx, blocks_z - 1 - bz) == 0:
                border.add((bx, bz))

    # -- which blocks the fringe bites out ---------------------------------
    #
    # **A bite may not touch anything paved.** Biting a block and then rescuing
    # the street cells inside it -- which is what this used to do -- removes the
    # ground either side and leaves the road running out over the void on a
    # two-tile causeway. A road leaving town is fine; a road leaving the world
    # is not. So a block carrying paving, and its neighbours along the border,
    # keep their ground: the road ends at the edge with shoulders.
    paved = {b for b in border
             if any(tm.surface[z][x] not in (R.GROUND, R.FIELD, R.MARSH)
                    for (x, z) in cells(*b))}
    sheltered = set(paved)
    for b in paved:
        sheltered |= {n for n in around(b) if n in border}

    # **A sheltered block is not lowered either, not just not bitten.**
    # Only land may fall away at the edge. A road and a river surface have to
    # stay level: dropping the outer ring regardless put a half-tile step across
    # the carriageway two tiles from the border, and a ledge straight across the
    # river -- 142 of this map's water cells sat half a tile below the other
    # 1,672, which is a waterfall running the width of an estuary. Water is in
    # `paved` for exactly this reason: it is a surface, not ground.

    bite = {b for b in border - sheltered
            if (zlib.crc32(f"edge:{b[0]}:{b[1]}".encode()) >> 8) % 4 == 0}

    # **No lone teeth.** Bites were decided per block and independently, so a
    # kept block between two bitten ones projects from the fringe as a 2x2 tab
    # standing over nothing -- a row of them reads as a comb, not a coastline.
    # The repair fills rather than cuts: give an isolated block a neighbour
    # back, so the fringe is made of runs. Cutting instead would eat inward,
    # and every cell it removed would be one more tile of missing map.
    while True:
        lonely = sorted(b for b in border - bite
                        if not any(n in border and n not in bite
                                   for n in around(b)))
        if not lonely:
            break
        for b in lonely:
            company = sorted(n for n in around(b) if n in bite)
            if not company:
                break
            bite.discard(company[0])
        else:
            continue
        break

    out: dict[tuple[int, int], float | None] = {}
    for (bx, bz) in sorted(border - sheltered):
        # **The step belongs at the edge, and nowhere else.** Stepping per ring
        # gave terraces up to four deep; flattening them to one step instead
        # spread that single step across a falloff four to eight tiles wide,
        # which did not remove the terrace -- it moved the cliff *inland*, to
        # wherever the ragged inner boundary of the band happened to fall. On
        # this map that was 1,740 half-tile lips of grass-over-grass sitting in
        # open country, well away from any edge.
        #
        # So the drop is the outermost ring only. Two tiles of ground half a
        # tile down, right where the board ends, reads as land falling away;
        # anything further in is an internal cliff nobody asked for. The
        # raggedness that stops the map looking cropped is the *bite*, which
        # takes cells out entirely, and that was always on this ring.
        drop: float | None = None if (bx, bz) in bite else EDGE_TAPER_MAX_DROP

        for (x, z) in cells(bx, bz):
            # Absent means grade. A protected cell gets no entry at all --
            # writing it the maximum drop, which is what the previous revision
            # of this function did, sinks a foundation half a tile into the
            # ground it is standing on.
            if (x, z) not in protected:
                out[(x, z)] = drop
    return out


def footprints(tm) -> dict[str, set[tuple[int, int]]]:
    """The cells belonging to each building, keyed by building id.

    Three separate passes used to rebuild this dict independently -- the wall
    shell, the upper floors and the roofs -- which is how the roofs ended up
    disagreeing with the walls about where a building was.
    """
    out: dict[str, set[tuple[int, int]]] = {}
    for z in range(tm.depth):
        row = tm.building[z]
        for x in range(tm.width):
            bid = row[x]
            if bid:
                out.setdefault(bid, set()).add((x, z))
    return out


#: What a doorway opens onto, worst to best. The order is the ranking.
FRONTAGE_RANK = ("open", "lane", "cart", "main")

#: How frontage moves a building's storey count, as ``(delta, weight)``.
#:
#: **A weighted deal, not a flat subtraction**, and that is the whole point.
#: The complaint in `docs/building-massing.md` §11 is not that the town is too
#: tall -- it is that "the craft block is 15 of 21 at three storeys and has no
#: single-storey building at all. A real street has a low workshop and an
#: outbuilding." Subtracting a storey from every back-lane building trades one
#: monotone skyline for another, one course lower. Dealing it gives a lane a
#: mix, which is what a lane looks like.
#:
#: Measured before choosing (East Tradebourne, 989 buildings): main 22 at mean
#: 2.55, cart 334 at 2.33, lane 463 at 1.94, open 170 at 1.94. So frontage
#: already correlates -- bigger buildings sit on bigger streets -- but lane and
#: open are indistinguishable from each other, and 76 lane-fronted buildings
#: stand three storeys tall.
FRONTAGE_STOREYS: dict[str, tuple[tuple[int, float], ...]] = {
    "main": ((1, 0.35), (0, 0.65)),
    "cart": ((0, 1.00),),
    "lane": ((-1, 0.55), (0, 0.45)),
    "open": ((-1, 0.70), (0, 0.30)),
}

#: Cache of per-map frontage, keyed by ``id(tm)``. `storeys_of` is read by
#: three passes over every cell, so this cannot be recomputed per call.
_FRONTAGE: dict[int, dict[str, str]] = {}


def frontage_of(tm) -> dict[str, str]:
    """Building id -> the best thing any of its doorways opens onto.

    **`street_class` alone is the wrong test and reading it that way hides
    most of the town.** It is only set for main and cart roads; a back lane is
    paved -- ``surface == "lane"`` -- and carries an *empty* class. Ranking on
    the class found 10 lane-fronted buildings on East Tradebourne. Ranking on
    the class *and* the surface finds 463. `_main_street_frontage` reads the
    class alone and is right to, because it only ever asks about main roads.
    """
    key = id(tm)
    if key in _FRONTAGE:
        return _FRONTAGE[key]
    out: dict[str, str] = {}
    for bid, doors in tm.doors.items():
        best = "open"
        for x, z, side in doors:
            dx, dz = next((d, e) for s, d, e in SIDE_OFFSETS if s == side)
            ox, oz = x + dx, z + dz
            if not tm.inside(ox, oz):
                continue
            cls = tm.street_class[oz][ox]
            surf = tm.surface[oz][ox]
            if cls in ("main", "cart"):
                got = cls
            elif surf in ("lane", "street"):
                got = "lane"
            elif surf in ("plaza", "court", "pier"):
                got = "cart"      # a square is public frontage, like a road
            else:
                got = "open"
            if FRONTAGE_RANK.index(got) > FRONTAGE_RANK.index(best):
                best = got
        out[bid] = best
    _FRONTAGE[key] = out
    return out


def storeys_by_frontage(base: int, frontage: str, bid: str) -> int:
    """``base`` moved by what this building fronts onto, dealt stably.

    Same crc32 deal as :func:`roof_suffix_for` and :func:`gable_end_for`, for
    the same reason: a town must rebuild to the same bytes. Never returns less
    than 1 -- a building with no storeys is a floor with a roof on it.
    """
    mix = FRONTAGE_STOREYS.get(frontage)
    if not mix:
        return base
    roll = (zlib.crc32(f"frontage:{frontage}:{bid}".encode()) % 10_000) / 10_000.0
    for delta, weight in mix:
        if roll < weight:
            return max(1, base + delta)
        roll -= weight
    return max(1, base + mix[-1][0])


def storeys_of(tm, bid: str | None, ceiling: int) -> int:
    """A building's own storey count, clamped to ``1..ceiling``.

    ``ceiling`` is the tallest building allowed on the map, not the height of
    every building: a village is mostly single-storey cottages, and giving
    them all the same wall made the first board look like a field of towers.
    """
    if not bid:
        return 0
    # Capped here rather than at the shell, because three passes read this --
    # the shell, the upper floors and the roof -- and a roof that disagrees
    # with the walls about how tall a building is floats or buries itself.
    # `footprints` records the same lesson about *where* a building is.
    if tier_of(bid) == "utility":
        return min(UTILITY_STOREYS, ceiling)
    base = max(1, tm.floors.get(bid, 1))
    # **Frontage is applied HERE, beside the utility cap and for the same
    # reason.** Three passes read this -- the shell, the upper floors and the
    # roof -- and a roof that disagrees with the walls about how tall a
    # building is floats or buries itself. `storeys_for` cannot do it: it runs
    # in the importer, where there is no raster and so no doorway and no
    # street to open onto.
    base = storeys_by_frontage(base, frontage_of(tm).get(bid, "open"), bid)
    return min(base, ceiling)


#: A footprint this many cells or more is built as two ranges rather than one
#: mass. 45 cells is 1,125 sq ft, which is the same threshold the lot samples
#: were chosen on; 157 of East Tradebourne's 989 buildings clear it.
RANGE_MIN_CELLS = 45

#: Where along the long axis the lower range starts, as a fraction. 0.6 makes
#: the outshut the *shorter* part, which is what an outshut is.
RANGE_SPLIT = 0.6

#: Cache of per-building range maps, keyed by (id(tm), bid, ceiling).
_RANGES: dict[tuple[int, str, int], dict[tuple[int, int], int]] = {}


def building_ranges(tm, bid: str, ceiling: int) -> dict[tuple[int, int], int]:
    """Cell -> storey count, splitting a large footprint into two ranges.

    **A big building built as one mass reads as a block, whatever it is made
    of.** The 11x11 warehouse in `tools/lot_probe.py` came out a three-storey
    slab-sided box with a flat top -- a tower block on a farm. Nothing about
    the fabric fixes that; the silhouette is the problem.

    So the far end of a long footprint drops a storey, which gives the mass a
    step and gives the roof two ridges instead of one pyramid. The roof pass
    needs no changes at all to do it: `_lay_roofs` already floods connected
    cells that *share a storey count* into one block, so two heights produce
    two roofs automatically. That is why this is a small change rather than the
    refactor `docs/building-massing.md` §11 estimated.

    Only a building over :data:`RANGE_MIN_CELLS` with at least two storeys
    splits -- a cottage has no range to lose, and one storey has nothing under
    it to keep.
    """
    key = (id(tm), bid, ceiling)
    hit = _RANGES.get(key)
    if hit is not None:
        return hit

    base = storeys_of(tm, bid, ceiling)
    cells = [(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.building[z][x] == bid]
    out = {c: base for c in cells}
    if len(cells) >= RANGE_MIN_CELLS and base >= 2:
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        along_x = (max(xs) - min(xs)) >= (max(zs) - min(zs))
        lo, hi = (min(xs), max(xs)) if along_x else (min(zs), max(zs))
        cut = lo + (hi - lo + 1) * RANGE_SPLIT
        for c in cells:
            if (c[0] if along_x else c[1]) >= cut:
                out[c] = max(1, base - 1)
    _RANGES[key] = out
    return out


def storeys_at(tm, bid: str | None, x: int, z: int, ceiling: int) -> int:
    """This *cell's* storey count -- see :func:`building_ranges`."""
    if not bid:
        return 0
    return building_ranges(tm, bid, ceiling).get((x, z), storeys_of(tm, bid, ceiling))


def rotated_footprint(asset: Asset, rot: int) -> tuple[float, float]:
    """The asset's ground footprint after ``rot``, as ``(size_x, size_z)``.

    Odd quarter turns swap the axes; even ones leave them alone.
    """
    if ((rot // _QUARTER) % 4) % 2:
        return (asset.size_z, asset.size_x)
    return (asset.size_x, asset.size_z)


def oriented_box(asset: Asset, cx: float, cz: float, rot: int) -> tuple[float, ...]:
    """A prop's collider as an *oriented* box: centre, half extents, its axis.

    **The half extents are the asset's own and are never swapped.** Swapping
    them on a quarter turn is what an axis-aligned view does, and
    :func:`rotated_footprint` only does it on quarter turns -- so for a prop at
    15 degrees it hands back the *unrotated* extent, which is smaller than the
    truth. Scenery is scattered at all 24 steps, so that under-measured every
    prop not on a quarter turn and let the scatter place things that really do
    interpenetrate.

    One implementation, used by both :class:`Scatter` when deciding whether a
    prop fits and by `verify._prop_collisions` when reporting what did not.
    Two copies of this drifting apart is the shape of half the bugs in
    `CLAUDE.md`.
    """
    ang = math.radians(rot * 15.0)
    return (cx, cz, asset.size_x / 2.0, asset.size_z / 2.0,
            math.cos(ang), math.sin(ang))


def oriented_aabb(box: tuple[float, ...]) -> tuple[float, float, float, float]:
    """A conservative axis-aligned bound on an oriented box, for bucketing.

    Conservative or the broad phase drops pairs the narrow phase would catch.
    """
    cx, cz, hx, hz, c, s = box
    ex = hx * abs(c) + hz * abs(s)
    ez = hx * abs(s) + hz * abs(c)
    return (cx - ex, cz - ez, cx + ex, cz + ez)


def oriented_depth(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """How deep two oriented boxes interpenetrate, in tiles.

    Separating-axis test. At or below zero means disjoint; the value is the
    smallest overlap over the four axes, so it is how far one box would have to
    move to come clear -- which is what tells a corner *join* from a burial.
    """
    dx, dz = b[0] - a[0], b[1] - a[1]
    best = float("inf")
    for box in (a, b):
        c, s = box[4], box[5]
        for nx, nz in ((c, s), (-s, c)):
            ra = a[2] * abs(a[4] * nx + a[5] * nz) + a[3] * abs(-a[5] * nx + a[4] * nz)
            rb = b[2] * abs(b[4] * nx + b[5] * nz) + b[3] * abs(-b[5] * nx + b[4] * nz)
            best = min(best, ra + rb - abs(dx * nx + dz * nz))
            if best <= 0.0:
                return best
    return best


def collider_offset(asset: Asset, rot: int) -> tuple[float, float]:
    """Where the collider centre sits relative to the stored coordinate."""
    ox, oz = asset.off_x, asset.off_z
    if ((rot // _QUARTER) % 4) % 2:
        ox, oz = oz, ox
    return ox, oz


def placed_bounds(asset: Asset, placement: Placement) -> tuple[float, float, float, float]:
    """The world-space ``(x0, z0, x1, z1)`` a placement actually occupies.

    The one place that knows how to turn a stored coordinate back into a box.
    A stored coordinate is the asset's *origin*: for a tile that sits on the
    collider's min corner, for a prop it sits at the collider's centre. Code
    that assumes the former for everything reports a tenth of the scenery as
    overlapping when none of it is.
    """
    sx, sz = rotated_footprint(asset, placement.rot)
    ox, oz = collider_offset(asset, placement.rot)
    return (placement.x + ox - sx / 2, placement.z + oz - sz / 2,
            placement.x + ox + sx / 2, placement.z + oz + sz / 2)


def place_centered(asset: Asset, cx: float, cz: float, y: float, rot: int) -> Placement:
    """Place ``asset`` so its collider is centred on ``(cx, cz)``.

    **The stored coordinate is the asset's origin, not its min corner.** For a
    tile those are the same thing -- the kit authors a tile with its collider's
    min corner on the origin, so ``m_Center`` equals ``m_Extent`` and
    subtracting half the footprint lands it correctly. That is the case the
    in-game measurements in the module docstring pinned down.

    A **prop** is authored the other way: its collider is centred on the
    origin, so ``m_Center`` is about zero. Subtracting half the footprint from
    one of those shifts it by half its own size. On a fern that is 0.2 tiles
    and invisible. On a 2.55-wide pine canopy it is 1.275 tiles, while the
    1.1-wide trunk beneath it moves only 0.55 -- so the two separate by three
    quarters of a tile and the trunk ends up anchored to the corner of its own
    crown, which is exactly how it looked on the board.

    Both cases are the same rule once the collider offset is honoured: step
    back from the desired centre by wherever the collider sits relative to the
    origin.
    """
    ox, oz = collider_offset(asset, rot)
    return Placement(asset.id, cx - ox, y, cz - oz, rot)


def place_tile(asset: Asset, tx: int, tz: int, y: float = 0.0, rot: int = 0) -> Placement:
    """Place a tile so it fills the grid cell whose min corner is ``(tx, tz)``."""
    return place_centered(asset, tx + asset.size_x / 2, tz + asset.size_z / 2, y, rot)


def cell_of(placement: Placement, asset: Asset) -> tuple[int, int]:
    """Which grid cell a placement's collider centre sits in.

    The inverse of :func:`place_centered`, and it has to go through the collider
    offset for the same reason that one does: a tile stores its min corner and a
    prop stores its centre, so reading ``placement.x`` as a cell number is right
    for half the board and half a footprint out for the rest.
    """
    return (int(math.floor(placement.x + (asset.off_x or 0.0))),
            int(math.floor(placement.z + (asset.off_z or 0.0))))


#: How long a fence piece is, in tiles. Every candidate in the `Fences` kit is
#: 2.0 long (`docs/fencing.md` §3), which is what lets one run-the-line routine
#: serve drystone, paling and hedge alike.
FENCE_MODULE = 2.0

#: A segment shorter than this gets a joint and no panel: a full-length piece
#: laid on a stub overhangs both its ends and reads as a fence pointing the
#: wrong way.
FENCE_MIN_SEGMENT = FENCE_MODULE * 0.5


def bearing_rot(dx: float, dz: float) -> int:
    """The rotation step whose long axis lies closest to ``(dx, dz)``.

    A piece authored along x sits along x at ``rot=0``, and the rotation runs
    the *other* way from the tile-coordinate angle: :data:`ROOF_EDGE_ROT` maps
    the four cardinals to ``e=0, s=18, w=12, n=6``, so a quarter turn from east
    towards south costs six steps *down*, not up. Reading that sign off the
    roof table rather than guessing is the difference between a panel lying
    along its fence and one crossing it at twice the bearing.

    Steps are 15 degrees, which is the finest turn TaleSpire has. Snapping the
    surveyed bearings in the three FTG exports to that grid costs at most 7.41
    degrees -- 0.26 tiles of drift across one panel, which a joint covers.
    """
    return int(round(-math.degrees(math.atan2(dz, dx)) / 15.0)) % 24


def run_along_polyline(points: list[tuple[float, float]],
                       module: float = FENCE_MODULE
                       ) -> tuple[list[tuple[float, float, int]],
                                  list[tuple[float, float, float]]]:
    """Lay a polyline out as panels along it and joints at its turns.

    Returns ``(panels, joints)``. A panel is ``(cx, cz, rot)`` -- a collider
    centre and a rotation step. A joint is ``(cx, cz, turn)``, the turn being
    the angle in degrees the line breaks through there, so the caller can put a
    post on every vertex or only on the sharp ones. Both ends of the run come
    back as joints with a turn of 180: an end is a full stop.

    **Panels are laid per segment, never across a vertex.** Walking the whole
    polyline by arc length is simpler and puts a rigid 2-tile piece straddling
    every corner, cutting it. Within a segment the spacing is the segment's
    own length divided by a whole number of panels, so a run closes exactly on
    its ends and any error is spread as a slight lap between panels rather
    than collected into a gap at one end. A lap is invisible; a gap is
    daylight.
    """
    panels: list[tuple[float, float, int]] = []
    joints: list[tuple[float, float, float]] = []
    if len(points) < 2:
        return panels, joints

    for a, b in zip(points, points[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length < FENCE_MIN_SEGMENT:
            continue
        rot = bearing_rot(dx, dz)
        count = max(1, int(round(length / module)))
        step = length / count
        ux, uz = dx / length, dz / length
        for i in range(count):
            along = (i + 0.5) * step
            panels.append((a[0] + ux * along, a[1] + uz * along, rot))

    joints.append((points[0][0], points[0][1], 180.0))
    for p, q, r in zip(points, points[1:], points[2:]):
        v1 = (q[0] - p[0], q[1] - p[1])
        v2 = (r[0] - q[0], r[1] - q[1])
        m1, m2 = math.hypot(*v1), math.hypot(*v2)
        if m1 < 1e-9 or m2 < 1e-9:
            continue
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)
        joints.append((q[0], q[1], math.degrees(math.acos(max(-1.0, min(1.0, cos))))))
    joints.append((points[-1][0], points[-1][1], 180.0))
    return panels, joints


def is_curtain_piece(asset: Asset) -> bool:
    """True when an asset is thinner than a cell and so belongs on its edge.

    The distinction runs through the whole kit. A block fills its cell and is
    placed with :func:`place_tile`; a curtain piece -- a wall panel, a fence, a
    parapet -- is authored to stand *on* a cell boundary and is placed with
    :func:`place_wall`. Getting it backwards is how a rampart came out striped
    with daylight, and how a parapet came out cantilevered over the drop.
    """
    return min(asset.size_x, asset.size_z) < 0.99


def place_wall(asset: Asset, tx: int, tz: int, side: str, y: float = 0.0) -> Placement:
    """Place a wall along one edge of grid cell ``(tx, tz)``.

    The wall's thin axis is inset to sit exactly on the cell boundary, so two
    buildings sharing a lot line do not produce overlapping geometry.

    **The mesh may be authored along either axis.** Most wall kits run their
    length along x with a thin z -- but the harbour fences are the other way
    round (0.5 x 0.5 x 1.0), and placing one of those on the wall convention
    put it a quarter tile off the grid on both axes. The quarter turn needed
    is read off which axis is thin rather than assumed, so a role can be
    pinned to either kind of piece.
    """
    rot = _SIDE_ROT[side] if side in _SIDE_ROT else None
    if rot is None:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    if asset.size_z > asset.size_x:
        rot = (rot + _QUARTER) % 24

    sx, sz = rotated_footprint(asset, rot)
    thickness = min(sx, sz)
    if side == "n":
        return place_centered(asset, tx + 0.5, tz + thickness / 2, y, rot)
    if side == "s":
        return place_centered(asset, tx + 0.5, tz + 1 - thickness / 2, y, rot)
    if side == "w":
        return place_centered(asset, tx + thickness / 2, tz + 0.5, y, rot)
    return place_centered(asset, tx + 1 - thickness / 2, tz + 0.5, y, rot)


#: Catalog -> its wall families. **A `WeakKeyDictionary`, and the first cut of
#: this was keyed on `id(catalog)`, which is a bug.** CPython reuses an id once
#: the object it belonged to is collected, so a catalog built, used and dropped
#: could hand its id to the next one and the second catalog would silently be
#: served the first one's families. Nothing held a reference to the catalog, so
#: there was nothing stopping the collection either -- the cache made the
#: aliasing *more* likely, not less.
#:
#: That is the shape of an order-dependent test failure: correct in isolation,
#: wrong only when something else ran first and freed an address. Keying on the
#: object drops the entry when the catalog goes, and keeps no catalog alive.
_WALL_FAMILIES: dict[int, dict] = {}


def wall_families(catalog) -> dict:
    """Every wall family in a catalog, built once and remembered.

    Cached because the facade pass asks for a family per building, and
    rebuilding the index 989 times on East Tradebourne is the kind of cost
    that only shows on the largest map anyone runs.

    **The entry is dropped when the catalog is, and that is not tidiness.**
    Keyed on `id(catalog)` alone -- which is what this was first -- the cache
    is wrong rather than merely stale: CPython reuses an address once the
    object at it is collected, so a catalog built, used and dropped could hand
    its id to the next one and the second catalog would silently be served the
    first one's families. Nothing here held a reference to the catalog, so
    nothing prevented the collection either; the cache made the aliasing more
    likely, not less. That is the shape of an order-dependent failure --
    correct in isolation, wrong only when something else ran first and freed
    an address.

    `Catalog` is a dataclass with `eq`, so it is unhashable and cannot key a
    `WeakKeyDictionary`. `weakref.finalize` gets the same guarantee without a
    hash: the entry cannot outlive the object whose address it is filed under.
    """
    key = id(catalog)
    got = _WALL_FAMILIES.get(key)
    if got is None:
        got = W.families(catalog)
        _WALL_FAMILIES[key] = got
        weakref.finalize(catalog, _WALL_FAMILIES.pop, key, None)
    return got


def wall_family_of(catalog, asset: Asset | None):
    """The wall family the piece ``asset`` belongs to, or None.

    **The kit is the folder**, so this is a folder lookup and nothing cleverer.
    It is what lets the palette keep choosing a tier's *material* -- its style
    queries are the only thing that knows civic means dressed stone -- while
    the catalog supplies everything else that kit ships.
    """
    if asset is None:
        return None
    return wall_families(catalog).get(asset.folder)


def place_wall_span(asset: Asset, tx: int, tz: int, side: str, span: int,
                    y: float = 0.0) -> Placement:
    """Place a wall piece covering ``span`` cells from ``(tx, tz)`` along ``side``.

    The generalisation of :func:`place_wall`, which is this with ``span=1`` and
    is kept as the name every caller already uses. Only the centre along the
    run changes; the rotation and the inset onto the cell boundary are the same
    rules and deliberately the same code, **because which axis the mesh is
    authored along is read off the collider and never assumed** -- `Rural Wall
    02` is 0.5 x 2 x 2 and `castle wall 2x2` is 2 x 2 x 0.5, and both have to
    end up lying along the run.
    """
    rot = _SIDE_ROT.get(side)
    if rot is None:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    if asset.size_z > asset.size_x:
        rot = (rot + _QUARTER) % 24

    sx, sz = rotated_footprint(asset, rot)
    thickness = min(sx, sz)
    if side in ("n", "s"):
        cx = tx + span / 2.0
        cz = tz + (thickness / 2 if side == "n" else 1 - thickness / 2)
    else:
        cz = tz + span / 2.0
        cx = tx + (thickness / 2 if side == "w" else 1 - thickness / 2)
    return place_centered(asset, cx, cz, y, rot)


@dataclass
class BuildStats:
    """What a build produced, for reporting and for slab-splitting decisions."""

    tiles: int = 0
    props: int = 0
    slabs: int = 0
    registration_markers: int = 0
    chunks_skipped: int = 0
    assets_skipped: int = 0

    @property
    def total(self) -> int:
        return self.tiles + self.props


class Builder:
    """Accumulates placements and splits them into legal-sized slabs."""

    def __init__(self, palette: Palette, seed: int = 0):
        self.palette = palette
        self.rng = random.Random(seed)
        self.placements: list[Placement] = []
        self.stats = BuildStats()
        #: Asset ids that were ever placed as scenery rather than as map.
        #: Recorded here because prop-ness is known at ``add`` time and cannot
        #: be recovered from a :class:`~citysmith.slab.Placement` afterwards.
        self.prop_ids: set[str] = set()
        #: Cell -> the y at which *background* ground was laid there. The edge
        #: taper means "unremarkable ground" is no longer one height across the
        #: map, so open-country detection compares against this rather than
        #: against a single grade. Water beds are deliberately absent: a sunken
        #: channel is a feature and must keep disqualifying its chunk.
        self.ground_baseline: dict[tuple[int, int], float] = {}
        self._byid: dict[str, Asset] | None = None
        #: How many boundary pieces `_lay_fences` actually laid, or None if
        #: that pass has not run.
        #:
        #: **Recorded rather than inferred from asset ids**, for the reason
        #: `verify._fence_roles` documents at length: the paling style builds
        #: from `yard_fence`, which `_lay_yards` *also* builds from, so
        #: "is a yard_fence on this board" cannot answer "was a field
        #: boundary built". A pass knows what it did; nothing downstream can
        #: recover it from a finished placement. Same argument as `layer_of`.
        self.fence_pieces: int | None = None
        #: How many boundary pieces `_lay_yards` laid, for the same reason
        #: `fence_pieces` exists: the yard boundary is dealt per tier now, and
        #: `field_wall` and `field_hedge` are shared with `_lay_fences`, so no
        #: asset id can say which pass placed one. Ask the pass.
        self.yard_pieces: int | None = None
        #: Which layer each placement belongs to, parallel to ``placements``.
        #: Recorded at ``add`` time because it is a property of the *pass* that
        #: emitted it, and nothing about a finished placement can recover it.
        self.layer_of: list[str] = []
        self._layer = LANDSCAPE
        #: Which building each placement belongs to, parallel to ``placements``
        #: (empty for anything that is not part of one). Set by the per-building
        #: passes through :attr:`group`. Chunking keeps a group together: a
        #: shell split across two slabs is a building that arrives in halves
        #: when one of them is not pasted, and the barracks did.
        self.group_of: list[str] = []
        self.group = ""

    @contextlib.contextmanager
    def layer(self, name: str):
        """Tag everything added inside the block as belonging to ``name``.

        Set around whole passes rather than threaded through every call: a
        pass knows what it is building, and an individual ``place_tile`` does
        not.
        """
        if name not in LAYERS:
            raise ValueError(f"unknown layer {name!r}; expected one of {LAYERS}")
        was, self._layer = self._layer, name
        try:
            yield
        finally:
            self._layer = was

    @property
    def byid(self) -> dict[str, Asset]:
        """Catalog assets by id, for anything that needs a placement's shape."""
        if self._byid is None:
            self._byid = {a.id: a for a in self.palette.catalog.assets}
        return self._byid

    def add(self, placement: Placement, *, prop: bool = False) -> None:
        self.placements.append(placement)
        self.layer_of.append(self._layer)
        self.group_of.append(self.group)
        if prop:
            self.stats.props += 1
            self.prop_ids.add(placement.asset_id)
        else:
            self.stats.tiles += 1

    def tile(self, role: str, tx: int, tz: int, y: float = 0.0, variant: int = 0, rot: int = 0) -> float:
        """Place a role tile at a cell; returns the resulting top surface height."""
        asset = self.palette.require(role, variant)
        self.add(place_tile(asset, tx, tz, y, rot))
        return y + asset.size_y

    def surface(self, role: str, tx: int, tz: int, top_y: float,
                variant: int = 0, rot: int = 0) -> float:
        """Lay a ground tile so its *top* lands on ``top_y``.

        Surface tiles are not all the same thickness -- cobble is 0.25 and
        grass is 0.5 -- so laying them all from a common bottom sank every
        street a quarter tile below the grass beside it. That is a 15 inch
        kerb along both sides of every road on the map, on 1,234 tiles. What
        has to line up is the surface a creature stands on, not the underside.
        """
        asset = self.palette.require(role, variant)
        self.add(place_tile(asset, tx, tz, top_y - asset.size_y, rot))
        return top_y

    def wall(self, role: str, tx: int, tz: int, side: str, y: float = 0.0, variant: int = 0) -> float:
        asset = self.palette.require(role, variant)
        self.add(place_wall(asset, tx, tz, side, y))
        return y + asset.size_y

    def prop(self, category: str, cx: float, cz: float, y: float, rot: int | None = None) -> bool:
        asset = self.palette.prop(category, self.rng)
        if asset is None:
            return False
        r = self.rng.randrange(24) if rot is None else rot
        self.add(place_centered(asset, cx, cz, y, r), prop=True)
        return True

    def clear_cells(self, cells: set[tuple[int, int]], *,
                    below: float | None = None, props: bool = True) -> int:
        """Take back what has been placed in ``cells``; returns how many.

        For anything that needs a square to be *empty* -- a party mark, a
        stairhead, a trapdoor. ``below`` restricts it to geometry whose top is
        at or under that height, which is how the floor tile comes up while the
        wall on the same cell's edge stays: a wall's origin lands inside the
        cell it belongs to, so a cell-only filter would demolish it.

        The three parallel lists have to stay parallel -- layer and group are
        recorded per placement at ``add`` time and there is no way to recover
        either afterwards.
        """
        keep_p, keep_l, keep_g = [], [], []
        removed = 0
        for p, layer, group in zip(self.placements, self.layer_of, self.group_of):
            asset = self.byid.get(p.asset_id)
            drop = False
            if asset is not None and cell_of(p, asset) in cells:
                is_prop = p.asset_id in self.prop_ids
                if is_prop:
                    drop = props
                elif below is None or p.y + asset.size_y <= below:
                    drop = True
            if drop:
                removed += 1
                if p.asset_id in self.prop_ids:
                    self.stats.props -= 1
                else:
                    self.stats.tiles -= 1
                continue
            keep_p.append(p)
            keep_l.append(layer)
            keep_g.append(group)
        self.placements, self.layer_of, self.group_of = keep_p, keep_l, keep_g
        return removed

    def to_slab(self) -> Slab:
        return _normalized_whole_tiles(Slab(list(self.placements)), self.byid)

    def to_slabs(self, max_assets: int = 4000, *, register: bool = True,
                 chunk_tiles: int = DEFAULT_CHUNK_TILES,
                 skip_open_country: bool = True) -> list[Slab]:
        """The pasteable slabs, in row-major order. See :meth:`chunk_plan`."""
        return self.chunk_plan(
            max_assets, register=register, chunk_tiles=chunk_tiles,
            skip_open_country=skip_open_country,
        ).slabs

    def chunk_plan(self, max_assets: int = 4000, *, register: bool = True,
                   chunk_tiles: int = DEFAULT_CHUNK_TILES,
                   skip_open_country: bool = True,
                   pack: bool = True, by_layer: bool = True,
                   per_building: bool = False) -> "ChunkPlan":
        """Cut the map into a 2D grid of pasteable chunks.

        **The grid.** Chunks are square tile regions ``chunk_tiles`` on a side,
        laid over the map from its minimum corner; every placement goes to the
        chunk containing its ``(x, z)``. That makes a chunk a *place* -- the
        market quarter, the north fields -- which is what lets a GM paste part
        of a town, or skip a region entirely. The predecessor cut the sorted
        placement list every ``max_assets`` entries, producing z-bands that ran
        the width of the map and corresponded to nothing on the ground.

        The lattice is square and uniform because the paste lattice is square
        and the binding constraint is bytes per slab. An irregular subdivision
        (golden-ratio bands and the like) would add bookkeeping without
        reducing the number of pastes.

        **Budget.** ``max_assets`` stays the hard per-chunk constraint. A cell
        holding more than that is split quadtree-style -- halved on each axis
        wider than one tile -- until every piece fits, or a piece is down to a
        single tile. Nothing subdivides at the default chunk size on a town;
        the recursion is there so a dense quarter, or a map built at larger
        scale, cannot silently produce a slab TaleSpire refuses to paste.

        **Open country.** A chunk holding nothing but ground at grade and
        scatter dressing is not somewhere anyone plays, so the map can do
        without it. It is trimmed inward from the edge only, and -- because an
        unwritten chunk is bare board, not grass -- it is dropped only when no
        kept chunk of its layer has room to carry it (see
        :func:`_absorb_open_country`); what is actually dropped is counted in
        :class:`BuildStats`. If *every* chunk is open country they are all kept
        instead -- the map is all terrain, and skipping everything would emit
        nothing at all.

        **Registration.** TaleSpire anchors a pasted slab by its own bounding
        box, not by the absolute coordinates inside it -- copying a placed slab
        back out returns it normalised to its own corner. Grid chunks each
        cover a different tile region and so have different corners; pasting
        them all at one anchor would stack them on top of each other instead of
        assembling the map.

        To make the pieces line up regardless, every chunk gets one extra tile
        at the *whole map's* minimum corner. That gives all chunks an identical
        bounding box origin, so pasting each at the same point lands every tile
        where it belongs. Dropping open-country chunks cannot break this: the
        marker is synthetic and added to whatever survives, so the shared origin
        does not depend on which regions were kept. The markers stack in a
        single corner cell and can be deleted afterwards; they are harmless if
        TaleSpire turns out to preserve absolute coordinates instead.

        **The marker has to be right under both readings of "origin".** A slab
        has two candidate corners -- the lowest stored coordinate, and the
        lowest point its geometry reaches -- and they are not the same corner,
        because a prop stores its collider centre. On the last board they
        disagreed for exactly one chunk of four, whose pines overhung the map's
        low corner by a tile: it was the only chunk with no marker (its stored
        minimum was already zero on all three axes, though no single placement
        sat there) and the only chunk whose volume started somewhere else. So
        the map is normalised by *volume* and every chunk gets a marker
        unconditionally -- a plain ground tile, which is authored with its
        collider on its origin and therefore pins both corners at once.
        """
        raw = Slab(list(self.placements))
        size = max(1, int(chunk_tiles))
        if not raw.placements:
            return ChunkPlan([], [], 0, 0, size, (0, 0))

        dx, dy, dz = _whole_tile_shift(raw, self.byid)
        slab = raw.translated(dx, dy, dz)

        # The grid is anchored on tile placements only. Props overhang their
        # cell by design (a pine canopy is 2.55 tiles wide), and letting one
        # decide the origin would shift the whole lattice off the tile grid.
        tiles = [p for p in slab.placements if p.asset_id not in self.prop_ids]
        tiles = tiles or slab.placements
        ox = math.floor(min(p.x for p in tiles))
        oz = math.floor(min(p.z for p in tiles))
        ex = math.floor(max(p.x for p in tiles)) + 1
        ez = math.floor(max(p.z for p in tiles)) + 1
        cols = max(1, math.ceil((ex - ox) / size))
        rows = max(1, math.ceil((ez - oz) / size))

        terrain, grade = self._grade_terrain(slab.placements)
        # Placements were translated to whole tiles above; the baseline was
        # recorded in the untranslated frame, so move it to match.
        idx, idz = int(round(dx)), int(round(dz))
        baseline = {(kx + idx, kz + idz): v + dy
                    for (kx, kz), v in self.ground_baseline.items()}

        # **Layer first, region second.** The grid, the origin and the whole-tile
        # shift are computed once over the whole map above, so every layer is cut
        # on the same lattice and shares a registration marker; only the
        # *contents* are partitioned. That is what makes the pieces of one layer
        # the ones that have to agree with each other.
        tagged = list(zip(self.placements, self.layer_of))
        moved = {id(a): b for a, b in zip(self.placements, slab.placements)}
        if by_layer:
            groups = [
                (name, [moved[id(pl)] for pl, lay in tagged if lay == name])
                for name in LAYERS
            ]
            groups = [(n, ps) for n, ps in groups if ps]
        else:
            groups = [("", list(slab.placements))]

        # **Which grid cells anything is built on, across every layer.**
        #
        # Open country means "nowhere anyone plays", and before the layer split
        # a chunk could answer that by looking at its own contents: a building
        # in the cell disqualified it. Layered, a landscape chunk under a town
        # holds nothing but grass -- the building is in the other layer -- so it
        # reads as open country and gets dropped, and the buildings above it are
        # left standing on nothing. A 40x40 crop lost half its ground that way.
        built: set[tuple[int, int]] = set()
        for pl, lay in tagged:
            if lay != STRUCTURE:
                continue
            q = moved[id(pl)]
            built.add((min(rows - 1, max(0, int((q.z - oz) // size))),
                       min(cols - 1, max(0, int((q.x - ox) // size)))))

        # **A building goes into one chunk, whole.** Placements are assigned
        # to grid cells by position, and a building that straddles a grid
        # line had its shell cut along it: the barracks on Forest Church went
        # 17 pieces into one structure file and 2 into the other. Every
        # placement that belongs to a building is assigned by the building's
        # low corner instead, so the whole shell -- walls, floors, roof, the
        # sign on the door -- rides in the same slab.
        # A placement is filed by the corner of the box it *occupies*, not by
        # its stored coordinate. A prop stores its collider centre, so a pine
        # on a chunk's western edge reaches a tile and a half further west
        # than any number in the file -- and a chunk whose geometry starts
        # before its own region cannot be tiled onto the grid, because the
        # paste anchors the box and the box is not where the region is.
        boxmin: dict[int, tuple[float, float]] = {}
        for q in slab.placements:
            asset = self.byid.get(q.asset_id)
            if asset is None:
                boxmin[id(q)] = (q.x, q.z)
                continue
            bx0, bz0, _, _ = placed_bounds(asset, q)
            boxmin[id(q)] = (bx0, bz0)

        group_of_moved: dict[int, str] = {}
        anchors: dict[str, tuple[float, float]] = {}
        for pl, g in zip(self.placements, self.group_of):
            if not g:
                continue
            q = moved[id(pl)]
            group_of_moved[id(q)] = g
            qx, qz = boxmin[id(q)]
            ax, az = anchors.get(g, (qx, qz))
            anchors[g] = (min(ax, qx), min(az, qz))
        anchor_of = {pid: anchors[g] for pid, g in group_of_moved.items()}

        kept: list[SlabChunk] = []
        skipped: list[SlabChunk] = []
        for name, group in groups:
            # **One building, one slab.** A chunk of forty buildings lands or
            # fails as one thing: if the paste is off, every building in it is
            # off together, and nothing about the result says which. Cut the
            # structure layer by building instead and each one is pasted, seen
            # and corrected on its own -- at the cost of a paste apiece. The
            # town wall, its towers and anything else not part of a building
            # keep the region grid.
            if per_building and name == STRUCTURE:
                kept.extend(self._building_chunks(
                    group, group_of_moved, dx, dz, max_assets))
                continue

            buckets: dict[tuple[int, int], list[Placement]] = {}
            for p in group:
                ax, az = anchor_of.get(id(p), boxmin.get(id(p), (p.x, p.z)))
                c = min(cols - 1, max(0, int((ax - ox + 1e-6) // size)))
                r = min(rows - 1, max(0, int((az - oz + 1e-6) // size)))
                buckets.setdefault((r, c), []).append(p)

            cells: list[_Cell] = []
            for (r, c), items in sorted(buckets.items()):
                cells.extend(_subdivide(_Cell(
                    r, c, "",
                    ox + c * size, oz + r * size,
                    min(ox + (c + 1) * size, ex), min(oz + (r + 1) * size, ez),
                    items,
                ), max_assets, anchor_of))
            cells.sort(key=lambda cell: (cell.row, cell.col, cell.quad))

            made = [
                SlabChunk(
                    row=cell.row, col=cell.col, quad=cell.quad,
                    x0=cell.x0 - dx, z0=cell.z0 - dz,
                    x1=cell.x1 - dx, z1=cell.z1 - dz,
                    slab=Slab(cell.items),
                    open_country=(
                        (cell.row, cell.col) not in built
                        and _is_open_country(
                            cell.items, terrain, self.prop_ids, grade, baseline)),
                    layer=name,
                    buildings=len({group_of_moved[id(p)] for p in cell.items
                                   if id(p) in group_of_moved}),
                )
                for cell in cells
            ]

            # Open country is a *landscape* judgement -- a region of nothing but
            # grass and trees. The structure layer is empty over open country by
            # construction, so there is nothing there to skip and no flood to
            # run; trimming it would only ever drop real building chunks whose
            # neighbours happen to be sparse.
            if name == STRUCTURE:
                layer_kept, layer_skipped = made, []
            else:
                layer_kept, layer_skipped = _trim_open_country(made, rows, cols)
                if not skip_open_country or not layer_kept:
                    layer_kept, layer_skipped = made, []

            # Detection wants small chunks; pasting wants few. Those pull opposite
            # ways -- at 8 tiles this map skips 15% of its assets but emits 139
            # files, at 32 tiles it emits 15 files and skips 2%. So detect fine,
            # then pack the survivors back up to the per-slab budget: the skipping
            # is decided at chunk resolution, the paste count at budget resolution.
            # Packing walks the grid boustrophedon (row-major, alternate rows
            # reversed) so consecutive chunks in a slab are physically adjacent and
            # a partial paste still lands as a contiguous piece of town.
            if pack:
                layer_kept = _pack_chunks(layer_kept, max_assets, cols)
                # Packing leaves room in the last slab of a layer, and the
                # trimmed fringe rides in it rather than being written off.
                layer_kept, layer_skipped = _absorb_open_country(
                    layer_kept, layer_skipped, rows, cols, max_assets)

            kept.extend(layer_kept)
            skipped.extend(layer_skipped)

        anchors: tuple[Placement, ...] = ()
        if register and not by_layer and len(kept) > 1:
            # **Tiled chunks want the shared box too, and for a subtler reason
            # than the layers did.**
            #
            # Tiling was introduced because a paste comes to rest on whatever
            # the cursor's ray hits, so a layer pasted over another inherits
            # its height -- measured, from a copy-out the user took off the
            # board: the landscape landed 320 of 320 placements exactly right
            # and the structure layer +1.5 in y, 1.5 being exactly the height
            # of the terrain under the anchor. Cutting by region with every
            # layer together fixes that, because the regions do not overlap
            # and nothing is ever pasted over anything.
            #
            # It does *not* follow that each chunk should be pinned at its own
            # corner and placed by hand. **The anchor is the bounding box's
            # centre, not its corner** -- measured on the board with a 24x24
            # pad, which came to rest centred on the cursor to within half a
            # tile. Give every chunk its own box and every chunk anchors on a
            # different point, so each has to be lined up by eye, and at the
            # 17.2 px/tile the zoom-out caps at, one 72-tile region is 1239 px
            # against a 900 px-tall window: eight alignments, each needing a
            # pan, each able to be a tile out. That is what "a few tiles to the
            # South East" was.
            #
            # Give them the identical box instead and they all anchor on the
            # same point, so they go down at ONE cursor cell with no
            # measurement at all -- and, far more useful, any error in that
            # anchor is *common to all nine*. The map can land a tile off where
            # it was aimed and still be perfectly assembled with respect to
            # itself, which is the only thing that shows.
            #
            # The one thing to get right is that the anchor point must stay
            # bare board for all nine pastes, or the last ones inherit a height
            # again. The anchor sits at the centre of the map, so the chunk
            # whose region covers it is written last; see `_anchor_last`.
            kept = _anchor_last(kept, slab, self.byid, dx, dz)
        if register and len(kept) > 1:
            # **Two markers, not one: a shared corner is not a shared box.**
            #
            # Every chunk used to carry a marker at the map's low corner, which
            # made all their minima agree. Their *maxima* did not, and by a
            # long way -- the landscape layer tops out around y=7 (a pine) and
            # the structure layer around y=20 (a roof). Pasted at one cursor
            # cell they landed at different heights, which is how a whole layer
            # of roofs ended up lying in the grass with trees growing through
            # them.
            #
            # Pinning both corners makes every chunk present the *identical*
            # bounding box, so whatever rule the paste uses to seat a slab, it
            # has nothing left to disagree about. Two stray tiles per chunk,
            # both at map corners, both deletable afterwards.
            marker = self.palette.resolve("ground") or self.palette.resolve("floor")
            marker_id = (marker.id if marker is not None
                         else min(slab.placements, key=lambda p: (p.y, p.z, p.x)).asset_id)
            sx, sy, sz = (marker.size_x, marker.size_y, marker.size_z) if marker else (1.0, 1.0, 1.0)
            _, (hx, hy, hz) = volume_bounds(slab, self.byid)
            lo = Placement(marker_id, 0.0, 0.0, 0.0, 0)
            # Placed so the marker's own far face lands on the map's, since a
            # tile's stored coordinate is its min corner -- rounded *out* to an
            # even whole tile. The far face of the map is wherever some pine
            # canopy's 2.55-tile collider happens to end, which put the marker
            # at x=187.51: the one non-prop tile on the board off the grid the
            # off-grid canary exists to guard.
            #
            # **Even, because the paste anchors on the box's centre, and a
            # centre that lands on a cell boundary is a coin toss.** Rounding
            # out to the half-tile lattice gave a 189.0-wide box, so the centre
            # sat at x=94.5 -- exactly between two cells. Measured on the board
            # from two independent copy-outs: r01c00's props resolved at one
            # offset and r01c01's at one tile further east, so the tie had been
            # broken both ways in the same paste run and the map came out with
            # a one-tile step down the c00/c01 join. An even extent puts the
            # centre on a whole tile, where there is nothing to round.
            hx, hz = (_even_ceil(hx), _even_ceil(hz))
            hi = Placement(marker_id, hx - sx, hy - sy, hz - sz, 0)
            for piece in kept:
                piece.slab.add(lo)
                piece.slab.add(hi)
            self.stats.registration_markers = 2 * len(kept)
            anchors = (lo, hi)

        self.stats.slabs = len(kept)
        self.stats.chunks_skipped = len(skipped)
        self.stats.assets_skipped = sum(ch.count for ch in skipped)
        return ChunkPlan(kept, skipped, rows, cols, size,
                         (ox - dx, oz - dz), anchors)

    def _building_chunks(self, group: list[Placement],
                         group_of_moved: dict[int, str],
                         dx: float, dz: float,
                         max_assets: int) -> list["SlabChunk"]:
        """One chunk per building, plus one for the rest of the structure layer.

        The rest is the town wall, its towers, and anything else that is not
        part of a building; it keeps no region of its own because it spans the
        whole circuit, so it is emitted as a single piece named ``rampart``.

        A building is small -- the largest on Forest Church is the temple at
        577 placements -- so nothing here needs subdividing or packing. Each
        chunk still gets the map's registration markers afterwards, which is
        what lets every one of them be pasted at the same cursor cell.
        """
        by_building: dict[str, list[Placement]] = {}
        for p in group:
            by_building.setdefault(group_of_moved.get(id(p), ""), []).append(p)

        out: list[SlabChunk] = []
        for bid, items in sorted(by_building.items()):
            xs = [p.x for p in items]
            zs = [p.z for p in items]
            x0 = int(math.floor(min(xs) - dx))
            z0 = int(math.floor(min(zs) - dz))
            out.append(SlabChunk(
                row=max(0, z0 // DEFAULT_CHUNK_TILES),
                col=max(0, x0 // DEFAULT_CHUNK_TILES),
                quad="",
                x0=x0, z0=z0,
                x1=int(math.ceil(max(xs) - dx)) + 1,
                z1=int(math.ceil(max(zs) - dz)) + 1,
                slab=Slab(items),
                layer=STRUCTURE,
                buildings=1 if bid else 0,
                name=bid or "rampart",
            ))
        return out

    def _grade_terrain(
        self, placements: list[Placement]
    ) -> tuple[set[str], float | None]:
        """Ground-role asset ids, and the height most of that ground sits at.

        Height matters because ground is also laid a tile low under water: a
        chunk of sunken riverbed is a channel, not open country.
        """
        terrain = set()
        for role in ("ground", "ground_2x2"):
            asset = self.palette.resolve(role)
            if asset is not None:
                terrain.add(asset.id)
        if not terrain:
            return terrain, None
        heights = collections.Counter(
            p.y for p in placements if p.asset_id in terrain
        )
        if not heights:
            return terrain, None
        return terrain, heights.most_common(1)[0][0]


@dataclass
class _Cell:
    """A grid cell mid-subdivision: a tile box plus the placements inside it."""

    row: int
    col: int
    quad: str
    x0: int
    z0: int
    x1: int
    z1: int
    items: list[Placement]


#: Bytes held back when packing, for the registration marker each chunk gets
#: afterwards. One placement is 8 bytes on the wire; this is generous because
#: the cost of being wrong is a build that will not export at all.
_REGISTRATION_MARGIN = 64


def _within_cap(slab: Slab) -> bool:
    """True when ``slab`` encodes under the cap with room left for its markers.

    Decode to count the bytes rather than scaling the base64 length by 3/4:
    padding makes that estimate optimistic, and it was optimistic by exactly
    three bytes on a chunk that then failed to encode. The whole point of
    measuring instead of counting assets is defeated by measuring
    approximately.

    Registration markers are added to every chunk *after* packing, so the slab
    measured here is short of the one that will actually be written. Leaving
    no room for them put a chunk three bytes over the limit and failed the
    export outright.
    """
    try:
        size = len(base64.b64decode(encode(slab)))
    except SlabError:
        return False
    return size <= MAX_COMPRESSED_BYTES - _REGISTRATION_MARGIN


def _pack_chunks(chunks: list["SlabChunk"], max_assets: int, cols: int) -> list["SlabChunk"]:
    """Merge adjacent chunks up to the slab limit so fewer slabs are pasted.

    Chunks arrive at detection resolution, which is deliberately fine. Each
    output slab is still a contiguous run of neighbours, so pasting a subset
    gives a coherent region rather than scattered fragments.

    **The binding limit is bytes, not assets.** ``max_assets`` is a proxy, and
    a proxy that drifts: as the map gained height variety and dressing, the
    same asset count compressed worse, and the largest chunk crept to 29,634
    of the 30,720-byte cap on an unchanged budget. Every merge is therefore
    encoded and measured, and a run that would not fit is closed one chunk
    early. Slower than counting, and it cannot be wrong.
    """
    if not chunks:
        return chunks

    def key(ch: "SlabChunk") -> tuple[int, int]:
        row, col = ch.row, ch.col
        return (row, -col if row % 2 else col)   # serpentine

    def fits(run: list["SlabChunk"]) -> bool:
        return _within_cap(_fuse(run).slab)

    ordered = sorted(chunks, key=key)
    out: list["SlabChunk"] = []
    run: list["SlabChunk"] = []
    total = 0
    for ch in ordered:
        over_count = run and total + ch.count > max_assets
        if over_count or (run and not fits(run + [ch])):
            out.append(_fuse(run))
            run, total = [], 0
        run.append(ch)
        total += ch.count
    if run:
        out.append(_fuse(run))
    return out


def _fuse(run: list["SlabChunk"]) -> "SlabChunk":
    """Combine a run of chunks into one, keeping the covered tile box."""
    if len(run) == 1:
        return run[0]
    placements = [p for ch in run for p in ch.slab.placements]
    x0 = min(ch.x0 for ch in run); x1 = max(ch.x1 for ch in run)
    z0 = min(ch.z0 for ch in run); z1 = max(ch.z1 for ch in run)
    first = run[0]
    # quad suffixes the label, so "+3" reads as "starts here, spans 4 chunks".
    # Counted from the cells covered rather than the run, because a run can
    # hold a chunk that is already a fusion -- absorbing open country adds one
    # cell at a time to a packed slab, and "+1" would then be a lie.
    covers = tuple(c for ch in run for c in (ch.covers or ((ch.row, ch.col),)))
    return SlabChunk(
        row=first.row, col=first.col,
        quad=f"+{len(covers) - 1}",
        x0=x0, z0=z0, x1=x1, z1=z1, slab=Slab(placements), open_country=False,
        # A run only ever holds chunks from one layer -- packing runs inside a
        # layer -- so the layer carries through. Dropping it here is what made
        # the written files lose their layer while the skipped ones kept it.
        layer=first.layer,
        covers=covers,
        buildings=sum(ch.buildings for ch in run),
    )


@dataclass
class SlabChunk:
    """One pasteable piece of a map, and the tile region it covers.

    ``x0``/``z0``/``x1``/``z1`` are half-open tile bounds in the *builder's*
    coordinates -- the same tile numbers the raster and its SVG use -- so a
    chunk can be matched against the map by eye.
    """

    row: int
    col: int
    quad: str
    x0: int
    z0: int
    x1: int
    z1: int
    slab: Slab
    open_country: bool = False
    #: Which layer this chunk belongs to -- see :data:`LAYERS`. Empty when the
    #: plan was built unlayered.
    layer: str = ""
    #: Grid cells this chunk covers. One cell normally; packing
    #: fuses many, and the map must still mark all of them.
    covers: tuple[tuple[int, int], ...] = ()
    #: How many buildings have their shell in this chunk. A building is never
    #: split across chunks, so the structure files' counts add up to the
    #: town's, and a paste missing one file is diagnosable from the table.
    buildings: int = 0
    #: Overrides :attr:`region` in the label when a chunk is not a grid cell --
    #: a per-building slab is named for its building, because "house-0005" is
    #: what the paste is checking and "r03c04" is not.
    name: str = ""

    @property
    def label(self) -> str:
        """Layer and region, e.g. ``landscape-r02c03`` -- the filename stem."""
        what = self.name or self.region
        return f"{self.layer}-{what}" if self.layer else what

    @property
    def region(self) -> str:
        """Grid cell only, e.g. ``r02c03`` -- or ``r02c03ne`` once subdivided.

        Separate from :attr:`label` because two chunks in different layers cover
        the *same* region, and the map table wants to say so.
        """
        return f"r{self.row:02d}c{self.col:02d}{self.quad}"

    @property
    def count(self) -> int:
        return len(self.slab.placements)


@dataclass
class ChunkPlan:
    """The result of cutting a map into grid chunks."""

    chunks: list[SlabChunk]
    skipped: list[SlabChunk]
    rows: int
    cols: int
    tile_size: int
    origin: tuple[int, int]
    #: The synthetic tiles added to pin every chunk to the same bounding box --
    #: one at each corner of the map. Callers that want the *map* rather than
    #: the registration scaffolding filter these out; see :meth:`is_marker`.
    anchors: tuple[Placement, ...] = ()

    def is_marker(self, p: Placement) -> bool:
        """True for a registration marker rather than a piece of the map.

        Compared by *identity*: the same two objects are appended to every
        chunk, so this is exact. Matching on asset and position instead would
        also swallow a real tile that happens to sit on a map corner, which is
        precisely what the far marker is placed against.
        """
        return any(p is a for a in self.anchors)

    @property
    def slabs(self) -> list[Slab]:
        return [ch.slab for ch in self.chunks]

    @property
    def assets_emitted(self) -> int:
        return sum(ch.count for ch in self.chunks)

    @property
    def assets_skipped(self) -> int:
        return sum(ch.count for ch in self.skipped)


def _trim_open_country(
    made: list["SlabChunk"], rows: int, cols: int,
) -> tuple[list["SlabChunk"], list["SlabChunk"]]:
    """Split chunks into kept and skipped, trimming only inward from the edge.

    Open country is dropped to save bytes, but an unpasted chunk is not grass
    -- it is *nothing*, a 24-tile square of bare board. Dropping a chunk the
    town has built all the way around therefore punches a rectangular void
    into the middle of the map. That is what "half generated chunks" was: two
    enclosed cells on the Forest Church map, a 24x48 tile hole with hard
    straight edges, surrounded on every side by finished town.

    So skipping is a flood fill from outside the map rather than a per-chunk
    test. A chunk is dropped only if it is open country *and* connected to the
    edge of the grid through other open country. Anything the built map
    encloses is kept, however empty it is -- a green between two districts
    costs a few hundred assets and reads as a park; the hole where it was
    reads as a bug.

    Trimmed is not yet dropped. Once the survivors are packed,
    :func:`_absorb_open_country` carries as much of the fringe as the packed
    slabs have room for, and only the remainder is written off.
    """
    # A cell over budget is subdivided into quadrants, so one (row, col) can
    # hold several chunks. It only conducts the flood if *every* piece of it
    # is open country -- one built quadrant makes the whole cell a barrier.
    at_cell: dict[tuple[int, int], list[SlabChunk]] = {}
    for ch in made:
        at_cell.setdefault((ch.row, ch.col), []).append(ch)

    # A grid cell with no placements at all conducts too, otherwise a blank
    # column walls the fill out of the region beyond it and everything past it
    # is kept as "enclosed".
    porous = {
        (r, c)
        for r in range(rows) for c in range(cols)
        if all(ch.open_country for ch in at_cell.get((r, c), ()))
    }

    outside: set[tuple[int, int]] = set()
    stack = [(r, c) for r in range(rows) for c in (0, cols - 1)]
    stack += [(r, c) for c in range(cols) for r in (0, rows - 1)]
    while stack:
        cell = stack.pop()
        r, c = cell
        if (cell in outside or cell not in porous
                or not (0 <= r < rows and 0 <= c < cols)):
            continue
        outside.add(cell)
        stack += [(r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)]

    kept = [ch for ch in made if (ch.row, ch.col) not in outside]
    skipped = [ch for ch in made if (ch.row, ch.col) in outside]
    return kept, skipped


def _even_ceil(v: float) -> float:
    """Round up to an even whole tile.

    The registration box is anchored by its centre, so its extent has to be
    even for that centre to land on a tile rather than on the boundary between
    two -- see the marker code in :meth:`Builder.chunk_plan`.
    """
    return 2.0 * math.ceil(v / 2.0)


def _kit_of(asset) -> str:
    """The kit a piece belongs to: the catalog's ``folder``.

    Not the name, and not ``pack``. ``pack`` is the DLC -- "Medieval Fantasy"
    covers castle, rural, tavern and thatch alike -- and ``group_tag`` names a
    *form*, so the same tag covers castle stone, rural boarding and a
    spaceship bulkhead. ``folder`` is the family, and it is what the game's own
    asset library lists down its left-hand side.

    This was a name heuristic first, and the name lies: `Village Roof Side
    Wall 02` sits in folder **Tavern**, so matching on the first word looked
    for a corner called "village *", found none, and mitred one -- while
    `Tavern no floor (1x1 a)`, the kit's own corner, sat unused.
    """
    return (getattr(asset, "folder", "") or "").strip().lower()


def _anchor_last(kept: list[SlabChunk], whole: Slab,
                 byid: dict[str, Asset], dx: float, dz: float) -> list[SlabChunk]:
    """Put the chunk covering the paste anchor at the end of the order.

    Every chunk carries the map's two registration markers, so they all present
    the identical bounding box and all nine go down at one cursor cell. The
    point TaleSpire anchors on is that box's *centre*, which lands somewhere in
    the middle of the map -- and the anchor has to still be bare board when each
    chunk arrives, or the paste inherits the height of whatever is under it.
    Every region but one is somewhere else entirely; the one that covers the
    centre only has to go last.
    """
    (lox, _, loz), (hix, _, hiz) = volume_bounds(whole, byid)
    # volume_bounds reads the *normalised* slab; the chunks carry the builder's
    # tile numbers, which is the same lattice shifted by (dx, dz).
    cx, cz = (lox + hix) / 2.0 - dx, (loz + hiz) / 2.0 - dz
    covering = [c for c in kept if c.x0 <= cx < c.x1 and c.z0 <= cz < c.z1]
    if not covering:
        return kept
    return [c for c in kept if c not in covering] + covering


def _absorb_open_country(
    kept: list["SlabChunk"], skipped: list["SlabChunk"],
    rows: int, cols: int, max_assets: int,
) -> tuple[list["SlabChunk"], list["SlabChunk"]]:
    """Carry trimmed open country in the kept chunks that have room for it.

    Trimming says what the map could do without; it does not say the bytes
    were needed. On Forest Church it dropped ten edge chunks -- 1,618 assets
    of plain grass and trees -- while the kept landscape chunk beside them
    held 5.8 KB of a 30 KB budget. An unwritten chunk is not grass but bare
    board, so the south-west of that map was a hard-edged notch of nothing,
    saved for the sake of bytes nobody needed. So each trimmed chunk is fused
    into a kept chunk of its own layer when one can take it -- measured the
    way packing measures, by encoding -- and dropped only when none can. Its
    own layer, because the layers are pasted separately and the ground goes
    down first; grass in a structure slab would arrive a paste late.

    Two rules keep this from undoing what the trim guarantees. Chunks are
    taken **from the inside out** -- deepest into the map first, by flood
    distance from the grid's edge -- so that when the room runs out, what is
    still dropped is the outermost ring and the map merely ends sooner
    instead of notching inward. And a chunk is taken only if the ones left
    behind still reach the edge without it: a skipped chunk the written map
    surrounds is exactly the rectangular hole the trim exists to prevent.

    A host that touches the chunk is preferred, so a subset paste still lands
    as one piece; otherwise the smallest kept chunk will do, since every
    chunk of a layer shares the registration box and lands where it belongs
    whichever file carries it.
    """
    if not kept or not skipped:
        return kept, skipped

    def cells(ch: "SlabChunk") -> tuple[tuple[int, int], ...]:
        return ch.covers or ((ch.row, ch.col),)

    def neighbours(cell: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        r, c = cell
        return ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1))

    def reach(blocked: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
        """Flood in from outside the grid over every cell nobody has written.

        Breadth-first, so the value is a distance: how deep into the map a
        cell sits, counted in cells of open country from the edge.
        """
        depth: dict[tuple[int, int], int] = {}
        queue: collections.deque = collections.deque()
        for r in range(rows):
            for c in range(cols):
                if ((r in (0, rows - 1) or c in (0, cols - 1))
                        and (r, c) not in blocked):
                    depth[(r, c)] = 0
                    queue.append((r, c))
        while queue:
            cell = queue.popleft()
            for nxt in neighbours(cell):
                nr, nc = nxt
                if (0 <= nr < rows and 0 <= nc < cols
                        and nxt not in blocked and nxt not in depth):
                    depth[nxt] = depth[cell] + 1
                    queue.append(nxt)
        return depth

    def touches(ch: "SlabChunk", host: "SlabChunk") -> bool:
        around = {n for cell in cells(ch) for n in neighbours(cell)}
        return any(cell in around for cell in cells(host))

    kept = list(kept)
    covered = {cell for ch in kept for cell in cells(ch)}
    depth = reach(covered)
    # Cells the trimmed chunks occupy, counted because a subdivided cell can
    # hold several of them, and one is not gone until all of them are.
    pending = collections.Counter((ch.row, ch.col) for ch in skipped)

    still: list[SlabChunk] = []
    order = sorted(skipped, key=lambda ch: (-depth.get((ch.row, ch.col), 0),
                                            ch.row, ch.col, ch.quad))
    for ch in order:
        cell = (ch.row, ch.col)
        left = {c for c, n in pending.items() if n - (c == cell) > 0}
        reached = reach(covered | {cell})
        if any(c not in reached for c in left):
            still.append(ch)            # taking it would strand what stays
            continue
        hosts = sorted(range(len(kept)),
                       key=lambda i: (not touches(ch, kept[i]), kept[i].count))
        for i in hosts:
            host = kept[i]
            if host.count + ch.count > max_assets:
                continue
            fused = _fuse([host, ch])
            if _within_cap(fused.slab):
                kept[i] = fused
                covered.add(cell)
                pending[cell] -= 1
                break
        else:
            still.append(ch)
    still.sort(key=lambda ch: (ch.row, ch.col, ch.quad))
    return kept, still


def _is_open_country(
    items: list[Placement], terrain: set[str], props: set[str],
    grade: float | None, baseline: dict[tuple[int, int], float] | None = None,
) -> bool:
    """True when a chunk holds only background ground and scatter dressing.

    Ferns and pines count as dressing, not as features: a stand of trees on
    open grass is still ground nobody stands on. Anything built -- a floor, a
    wall, a street, a tilled field, water -- disqualifies the chunk at once.

    **Height is checked against the cell's own baseline, not a single grade.**
    The map edge tapers, so "unremarkable ground" is no longer one height
    everywhere; testing against a global grade would mark every tapered border
    cell as a feature and stop the border chunks -- precisely the ones worth
    dropping -- from ever being skipped. A cell with no baseline recorded is
    not background: that is how a sunken riverbed still disqualifies its chunk.
    """
    if grade is None:
        return False
    for p in items:
        if p.asset_id in terrain:
            want = grade
            if baseline:
                # Empty means the slab was not laid by _lay_terrain -- a probe,
                # an interior, a test fixture -- so there is no height field to
                # consult and the single grade is the best answer available.
                key = (int(math.floor(p.x)), int(math.floor(p.z)))
                if key not in baseline:
                    return False        # ground off the baseline is a feature
                want = baseline[key]
            if abs(p.y - want) > 1e-6:
                return False
        elif p.asset_id not in props:
            return False
    return True


def _subdivide(cell: _Cell, max_assets: int,
               anchor_of: dict[int, tuple[float, float]] | None = None) -> list[_Cell]:
    """Halve a cell until each piece holds at most ``max_assets`` placements.

    Splitting is quadtree-style: both axes at once where both span more than a
    tile, one axis where only one does. A piece already down to a single tile
    is returned as-is even if it is still over budget -- there is nowhere left
    to cut, and the encoder refuses that slab with a clearer message than an
    endless subdivision would give.

    ``anchor_of`` maps a placement (by ``id``) to the point it is sorted by --
    its building's low corner -- so a building is never cut by the quadtree
    either. Placements without one sort by their own position.
    """
    anchor_of = anchor_of or {}

    def at(p: Placement) -> tuple[float, float]:
        return anchor_of.get(id(p), (p.x, p.z))

    out: list[_Cell] = []
    stack = [cell]
    while stack:
        cur = stack.pop()
        span_x, span_z = cur.x1 - cur.x0, cur.z1 - cur.z0
        if len(cur.items) <= max_assets or (span_x <= 1 and span_z <= 1):
            out.append(cur)
            continue
        xs = ([(cur.x0, cur.x1)] if span_x <= 1 else
              [(cur.x0, cur.x0 + span_x // 2), (cur.x0 + span_x // 2, cur.x1)])
        zs = ([(cur.z0, cur.z1)] if span_z <= 1 else
              [(cur.z0, cur.z0 + span_z // 2), (cur.z0 + span_z // 2, cur.z1)])
        for zi, (z0, z1) in enumerate(zs):
            for xi, (x0, x1) in enumerate(xs):
                tag = ((_QUAD_Z[zi] if len(zs) > 1 else "")
                       + (_QUAD_X[xi] if len(xs) > 1 else ""))
                items = [
                    p for p in cur.items
                    if (len(xs) == 1 or (at(p)[0] < x1 if xi == 0 else at(p)[0] >= x0))
                    and (len(zs) == 1 or (at(p)[1] < z1 if zi == 0 else at(p)[1] >= z0))
                ]
                if items:
                    stack.append(_Cell(
                        cur.row, cur.col, cur.quad + tag, x0, z0, x1, z1, items))
    return out


def volume_bounds(
    slab: Slab, byid: dict[str, Asset]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The box the slab's geometry actually occupies.

    ``Slab.bounds()`` is the box over *stored coordinates*, and a stored
    coordinate is an origin, not a corner: a tile is authored with its collider
    on the origin but a prop is authored around it, so a pine beside the map's
    low corner occupies a tile and a bit further out than any number in the
    file. Anything reasoning about where a slab starts -- normalisation, and
    the registration marker every chunk carries -- has to use this instead.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for p in slab.placements:
        asset = byid.get(p.asset_id)
        if asset is None:
            continue
        sx, sz = rotated_footprint(asset, p.rot)
        ox, oz = collider_offset(asset, p.rot)
        box = (
            (p.x + ox - sx / 2, p.x + ox + sx / 2),
            (p.y + asset.off_y - asset.size_y / 2,
             p.y + asset.off_y + asset.size_y / 2),
            (p.z + oz - sz / 2, p.z + oz + sz / 2),
        )
        for i, (a, b) in enumerate(box):
            lo[i] = min(lo[i], a)
            hi[i] = max(hi[i], b)
    if lo[0] == float("inf"):
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return tuple(lo), tuple(hi)   # type: ignore[return-value]


def _whole_tile_shift(slab: Slab,
                      byid: dict[str, Asset] | None = None) -> tuple[int, int, int]:
    """The whole-tile translation that brings ``slab`` to the origin.

    Whole tiles, because translating by the exact minimum would drag every tile
    on the board off the grid by some prop's fractional overhang. With a
    catalog to hand the minimum is the one geometry reaches; without one it
    falls back to stored coordinates.
    """
    if not slab.placements:
        return (0, 0, 0)
    if byid:
        (mx, my, mz), _ = volume_bounds(slab, byid)
    else:
        (mx, my, mz), _ = slab.bounds()
    return (-math.floor(mx), -math.floor(my), -math.floor(mz))


def _normalized_whole_tiles(slab: Slab,
                            byid: dict[str, Asset] | None = None) -> Slab:
    """Shift a slab toward the origin by whole tiles only.

    ``Slab.normalized()`` translates by the exact min corner -- and the min
    corner belongs to whichever placement sticks out furthest, which for a
    dressed map is some pine canopy with a 2.55-tile footprint. Translating by
    its fractional overhang drags every *tile* on the board off the grid by
    that fraction: the board stays self-consistent, so it looks right, but
    minis with grid snap no longer land on the floors. (The door-footprint bug
    was this same failure; props reintroduced it through the scenery.)

    Snapping the translation to whole tiles keeps every tile exactly on-grid
    and lets props keep their intentional fractional positions.
    """
    if not slab.placements:
        return slab
    return slab.translated(*_whole_tile_shift(slab, byid))


# -- city board ---------------------------------------------------------------

def build_city_board(
    city: City,
    palette: Palette,
    *,
    include_ground: bool = True,
    include_streets: bool = True,
    building_height: int = 2,
    seed: int = 0,
) -> Builder:
    """Build the coarse 3D city -- ground, streets and building shells.

    Interiors are deliberately omitted: at city scale they are invisible and
    would blow the asset budget. Use :func:`build_interior` for a site the
    party actually enters.
    """
    b = Builder(palette, seed)
    occupied: set[tuple[int, int]] = set()

    for building in city.buildings:
        for tx, tz in building.rect.tiles():
            occupied.add((tx, tz))

    street_tiles: set[tuple[int, int]] = set()
    for street in city.streets:
        for tx, tz in street.rect.tiles():
            if (tx, tz) not in occupied:
                street_tiles.add((tx, tz))

    ground_h = 0.0
    if include_ground:
        ground = palette.require("ground")
        for tz in range(city.depth):
            for tx in range(city.width):
                if (tx, tz) in street_tiles and include_streets:
                    continue
                b.add(place_tile(ground, tx, tz, 0.0))
        ground_h = ground.size_y

    if include_streets:
        street = palette.require("street")
        for tx, tz in sorted(street_tiles):
            b.add(place_tile(street, tx, tz, 0.0))
        if not include_ground:
            ground_h = street.size_y

    for building in city.buildings:
        _build_shell(b, building, ground_h, building_height)

    if city.walled and city.wall_rect:
        _build_city_wall(b, city, ground_h)

    return b


def _build_shell(b: Builder, building: Building, base_y: float, storeys: int) -> None:
    """Walls around the perimeter plus a roof -- no interior at city scale."""
    rect = building.rect
    wall = b.palette.require("wall")
    floor = b.palette.require("floor")

    b_floors = min(building.floors, storeys)
    storey_h = wall.size_y

    for tx, tz in rect.tiles():
        b.add(place_tile(floor, tx, tz, base_y))
    top = base_y + floor.size_y

    for level in range(b_floors):
        y = top + level * storey_h
        for tx, tz in rect.tiles():
            on_n, on_s = tz == rect.z, tz == rect.z2 - 1
            on_w, on_e = tx == rect.x, tx == rect.x2 - 1
            if not (on_n or on_s or on_w or on_e):
                continue
            for side, present in (("n", on_n), ("s", on_s), ("w", on_w), ("e", on_e)):
                if present:
                    b.add(place_wall(wall, tx, tz, side, y))

    roof_y = top + b_floors * storey_h
    roof = b.palette.resolve("roof")
    if roof is not None:
        for tx, tz in rect.tiles():
            b.add(place_tile(roof, tx, tz, roof_y))


# -- interiors ----------------------------------------------------------------

@dataclass(frozen=True)
class Fabric:
    """The pieces one building is built from, chosen together.

    A facade that changes material at the corner reads as a mistake rather
    than as variety -- which is why the *tier* picks the whole set and not
    just the wall. The town build has the same idea inline in
    `build_from_tilemap`; this is the version an interior can use, and the two
    should converge the next time either is touched.
    """

    tier: str
    wall: Asset
    partition: Asset
    door: Asset
    floor: Asset
    window: Asset | None = None
    corner: Asset | None = None


def interior_fabric(palette: Palette, tier: str, variant: int = 0) -> Fabric:
    """Resolve one tier's pieces for an interior.

    **The partition comes from the wall's own kit**, the same rule that put a
    Village panel inside a Village shell: a stone temple whose rooms are
    divided by timber framing is two buildings in one footprint. Where the
    declared `wall_interior` belongs to a different kit than this tier's wall,
    the wall itself is used instead -- a plain partition of the right material
    beats a detailed one of the wrong material.
    """
    wall = palette.require("wall", variant)
    door = palette.resolve("door")
    window = palette.resolve("wall_window")
    floor = palette.require("floor")
    corner_role = "wall_corner"

    if tier == "civic":
        wall = palette.resolve("wall_civic") or wall
        window = palette.resolve("wall_window_civic") or window
        door = palette.resolve("door_civic") or door
        floor = palette.resolve("floor_civic") or floor
        corner_role = "wall_corner_civic"
    elif tier == "utility":
        wall = palette.resolve("wall_utility") or wall
        # No window in this kit and none wanted: a barn with glass in it stops
        # being a barn.
        window = None
        corner_role = "wall_corner_utility"
    elif tier == "trade":
        # Trade shares the house's wall -- it is the only kit with a 1-cell
        # window -- and is told apart by its door.
        door = palette.resolve("door_civic") or door

    partition = palette.resolve("wall_interior")
    if partition is None or _kit_of(partition) != _kit_of(wall):
        partition = wall
    if window is not None and segment_shape(window) != segment_shape(wall):
        window = None      # would overhang the cell; see Palette.validate
    if door is None or segment_shape(door) != segment_shape(wall):
        door = palette.require("door")

    corner = palette.resolve(corner_role)
    if corner is None or (corner.size_x, corner.size_z) != (1.0, 1.0)             or abs(corner.size_y - wall.size_y) > 1e-6             or _kit_of(corner) != _kit_of(wall):
        corner = None
    return Fabric(tier=tier, wall=wall, partition=partition, door=door,
                  floor=floor, window=window, corner=corner)


#: Props per *room cell* asked for. Not the same as props per cell delivered:
#: placement is limited to cells against a wall and the `Scatter` rejects a
#: piece that will not fit, so this saturates.
#:
#: **The target is a third of the published figure, and that is arithmetic
#: rather than timidity.** `docs/interior-slabs.md` measures hand-built
#: interiors at 0.41-0.66 props per cell -- but **two thirds of those sit on a
#: table or a shelf**, not on the floor, and nothing stacks yet. So the
#: floor-only share to aim at is about 0.14-0.22 per cell.
#:
#: Measured on a 15-room tavern (864 room cells): 0.12 delivers 0.07 per cell,
#: 0.35 delivers 0.19, and 0.50 delivers 0.23 -- the wall cells run out. 0.35
#: sits inside the band; going higher only packs the walls.
#:
#: On a board at 0.12 the rooms read as empty shells with one bench each, which
#: is what `tools/interior_probe.py` was built to show and did.
INTERIOR_DENSITY = 0.35


def build_interior(
    floorplan,
    palette: Palette,
    *,
    seed: int = 0,
    roof: bool = False,
    prop_density: float = INTERIOR_DENSITY,
    stack: bool = True,
    tier: str | None = None,
) -> Builder:
    """Build a playable interior from a :class:`~citysmith.floorplan.Floorplan`.

    The roof is off by default: a covered interior is nearly unusable at the
    table because the camera cannot see in.

    ``stack`` is the same argument one storey further. With it on, level 1
    sits on top of level 0, which is what a building does and what the camera
    then cannot see into -- TaleSpire has no way to hide an upper floor. With
    it off every level is built at ground height, and the levels are expected
    to have already been moved apart in the plan
    (:func:`citysmith.interior.spread_levels`), so the whole building reads
    from directly overhead like a battle map.
    """
    b = Builder(palette, seed)

    stair_asset = palette.resolve("stairs")

    # The fabric, and the face the front door is on. Both were missing: an
    # interior was built from `wall` and `wall_interior` whatever the building
    # was, so a stone temple came out in the same timber panels as a cottage,
    # and no interior had a single window in it -- every wall blind, on a board
    # whose whole purpose is to be looked into.
    if tier is None:
        tier = tier_of(f"{floorplan.kind}-0000")
    fabric = interior_fabric(
        palette, tier, zlib.crc32(floorplan.building_id.encode()) % 3
    )
    front = next((d.side for d in floorplan.doors if d.exterior), None)

    ext_wall = fabric.wall
    floor = fabric.floor
    # The upper deck follows the ground floor's material, not the generic
    # role: a stone building with plank upper storeys is the same kit mismatch
    # one level up.
    upper = floor if fabric.tier == "civic" else (
        palette.resolve("floor_upper") or floor)
    storey_h = ext_wall.size_y
    level_base: list[float] = []

    for level in range(floorplan.levels):
        # Each level's own footprint. Identical for every level when they are
        # stacked; yards apart when they have been spread for play.
        rect = floorplan.rect_on(level)
        slab_asset = floor if level == 0 else upper
        base = level * (storey_h + slab_asset.size_y) if stack else 0.0
        level_base.append(base)

        for tx, tz in rect.tiles():
            b.add(place_tile(slab_asset, tx, tz, base))
        wall_y = base + slab_asset.size_y

        doors = {(d.x, d.z, d.side) for d in floorplan.doors if d.level == level}

        # Exterior shell: one corner piece where two adjacent sides are
        # exposed, a door where the plan puts one, a window on a stable hash,
        # and a plain panel otherwise. Same order of preference as the town's
        # facade, and for the same reasons -- two wall ends in one square where
        # a corner piece would do, and a blank back wall where a front wants
        # glass, are both things a board shows immediately.
        for tx, tz in rect.tiles():
            exposed = {side for side, present in (
                ("n", tz == rect.z), ("s", tz == rect.z2 - 1),
                ("w", tx == rect.x), ("e", tx == rect.x2 - 1),
            ) if present}
            if not exposed:
                continue
            door_cell = any((tx, tz, s) in doors for s in exposed)
            corner = CORNER_BY_SIDES.get(frozenset(exposed))
            if corner is not None and fabric.corner is not None and not door_cell:
                b.add(place_tile(fabric.corner, tx, tz, wall_y,
                                 WALL_CORNER_ROT[corner]))
                continue
            for side in sorted(exposed):
                if (tx, tz, side) in doors:
                    b.add(place_wall(fabric.door, tx, tz, side, wall_y))
                    continue
                # crc32, not hash(): str hashes are salted per process, so
                # hash() would re-deal the windows on every rebuild -- the same
                # trap that made the partitions non-deterministic.
                key = zlib.crc32(
                    f"{floorplan.building_id}:{tx}:{tz}:{level}:{side}".encode())
                rate = glaze_rate(tier, side, front, False)
                # A ground floor keeps one fewer window: privacy, and the
                # doorway already breaks that run.
                if rate and level == 0:
                    rate += 1
                glazed = fabric.window is not None and rate and key % rate == 0
                b.add(place_wall(fabric.window if glazed else ext_wall,
                                 tx, tz, side, wall_y))

        # Interior partitions on shared room edges, skipping doorways.
        # **Sorted**, because `_interior_walls` returns a set of
        # ``(x, z, side)`` and `side` is a string: Python randomises string
        # hashing per process, so the same plan emitted its partitions in a
        # different order every run. The geometry was identical every time --
        # measured, 231 placements, same multiset -- but the bytes were not,
        # which makes a build undiffable and makes any digest of the file read
        # as a change that never happened.
        for wall_cell in sorted(_interior_walls(floorplan, level)):
            tx, tz, side = wall_cell
            if (tx, tz, side) in doors:
                b.add(place_wall(fabric.door, tx, tz, side, wall_y))
                continue
            b.add(place_wall(fabric.partition, tx, tz, side, wall_y))

        # Dress rooms with props.
        _dress(b, floorplan, level, wall_y, prop_density)

    for stair in floorplan.stairs:
        if stair_asset is None:
            break
        y = level_base[stair.from_level] + floor.size_y
        b.add(place_tile(stair_asset, stair.x, stair.z, y))
        if not stack:
            # Spread levels are not above each other, so one stair tile says
            # nothing about where you come out. The pair does. The offset is
            # read off the two levels' rects rather than from the gap, so it
            # is right whatever the plan did with them.
            dx = floorplan.rect_on(stair.to_level).x - floorplan.rect_on(stair.from_level).x
            b.add(place_tile(stair_asset, stair.x + dx, stair.z,
                             level_base[stair.to_level] + floor.size_y))

    if roof:
        roof_asset = palette.resolve("roof")
        if roof_asset is not None:
            levels = [floorplan.levels - 1] if stack else range(floorplan.levels)
            for level in levels:
                top = level_base[level] + storey_h + floor.size_y
                for tx, tz in floorplan.rect_on(level).tiles():
                    b.add(place_tile(roof_asset, tx, tz, top))

    return b


def _interior_walls(floorplan, level: int) -> set[tuple[int, int, str]]:
    """Cells+sides where two rooms meet, deduplicated so no wall is doubled."""
    rooms = floorplan.rooms_on(level)
    shell = floorplan.rect_on(level)
    walls: set[tuple[int, int, str]] = set()
    seen_edges: set[tuple[float, float, str]] = set()

    for room in rooms:
        r = room.rect
        for tx, tz in r.tiles():
            for side, on_edge in (
                ("n", tz == r.z), ("s", tz == r.z2 - 1),
                ("w", tx == r.x), ("e", tx == r.x2 - 1),
            ):
                if not on_edge:
                    continue
                # Skip the building's outer shell -- already built.
                outer = (
                    (side == "n" and tz == shell.z)
                    or (side == "s" and tz == shell.z2 - 1)
                    or (side == "w" and tx == shell.x)
                    or (side == "e" and tx == shell.x2 - 1)
                )
                if outer:
                    continue
                # Normalise so the same physical edge from either room maps to
                # one key; otherwise adjacent rooms each build their own wall.
                if side == "n":
                    key = (tx + 0.5, float(tz), "h")
                elif side == "s":
                    key = (tx + 0.5, float(tz + 1), "h")
                elif side == "w":
                    key = (float(tx), tz + 0.5, "v")
                else:
                    key = (float(tx + 1), tz + 0.5, "v")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                walls.add((tx, tz, side))
    return walls


#: Cells kept clear around a doorway: the threshold cell itself, the cell the
#: door opens into, and the cell on the far side. A door needs somewhere to
#: swing and somewhere to stand while it does.
DOOR_CLEARANCE = 1

#: Cells kept clear around the foot and head of a stair, for the same reason.
STAIR_CLEARANCE = 1

#: The least a prop is pushed off its cell centre, away from the wall it
#: stands against, in tiles.
#:
#: **Hand-built interiors put 0.1% of props on a cell centre**
#: (`docs/interior-slabs.md`, decoded from 2,382 community props) while this
#: pass put 100% of them there -- which is most of why our furniture read as
#: debris dropped in a grid rather than as a furnished room.
#:
#: It is a *minimum*, not the distance: the real set-back is half the prop's
#: own depth perpendicular to the wall, so a chest clears the masonry by as
#: much as a stool does. A constant cannot do that -- a piece deeper than
#: twice the constant ends up inside the wall however the sign is written,
#: and 67% of the `Furniture` kit is wider than a cell.
WALL_SET_BACK = 0.32

#: Share of props laid on an exact quarter turn. Measured at 84% in the
#: community slabs; the remainder are angled, which is what stops a room
#: looking like a showroom.
QUARTER_TURN_SHARE = 0.84


def _door_keepout(floorplan, level: int) -> set[tuple[int, int]]:
    """Cells no prop may stand on, because a door or a stair uses them.

    A chair in a doorway is not dressing, it is a blocked door -- and on a
    board nobody can move it, because a slab has no physics. This is the one
    rule in the pass that is about *play* rather than about looks.

    Both sides of the opening are taken: the door's own cell and the cell it
    swings into, plus a ring of :data:`DOOR_CLEARANCE` around each.
    """
    out: set[tuple[int, int]] = set()

    def ring(x: int, z: int, r: int) -> None:
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                out.add((x + dx, z + dz))

    for door in floorplan.doors:
        if door.level != level:
            continue
        dx, dz = next((a, c) for sd, a, c in SIDE_OFFSETS if sd == door.side)
        ring(door.x, door.z, DOOR_CLEARANCE)
        ring(door.x + dx, door.z + dz, DOOR_CLEARANCE)

    for stair in floorplan.stairs:
        if getattr(stair, "level", level) != level:
            continue
        ring(stair.x, stair.z, STAIR_CLEARANCE)
    return out


def _dress(b: Builder, floorplan, level: int, y: float, density: float) -> None:
    """Furnish the rooms on one level, keeping doors and stairs clear.

    Three things here are measured rather than chosen, all from
    `docs/interior-slabs.md`, which decoded 2,382 interior-kit props out of
    published community slabs:

    * **props sit against walls, not on cell centres** -- 0.1% of hand-placed
      props are centred and this pass used to centre every one of them;
    * **84% are on an exact quarter turn**, against our uniform draw over all
      24 steps, which is what made a room read as scattered rubble;
    * **a prop is often bigger than its cell** -- 67% of the `Furniture` kit is
      -- so one must be checked against what is already there rather than
      assumed to fit.

    The fourth rule is not from the slabs but from play: **nothing stands in a
    doorway or on a stair.** A slab has no physics, so a chair dropped in a
    door is a door that does not open, for the whole session.
    """
    keepout = _door_keepout(floorplan, level)
    scatter = Scatter(b)
    # The wall's own depth, so the set-back is measured from the face a prop
    # can actually touch rather than from the cell boundary. These happen to
    # be equal for a 0.5-thick panel in a 1.0 cell, and relying on that
    # coincidence is how the next kit with thicker walls buries the furniture.
    partition = b.palette.resolve("wall_interior") or b.palette.resolve("wall")
    wall_t = min(partition.size_x, partition.size_z) if partition else 0.5

    for room in floorplan.rooms_on(level):
        category = _PROP_CATEGORY.get(
            room.purpose, _PROP_CATEGORY_BY_KIND.get(floorplan.kind, "house"))
        cells = [c for c in room.rect.tiles() if c not in keepout]
        if not cells:
            continue

        # Against a wall, so the middle of the room stays walkable -- and so
        # the set-back below has a wall to be set back against.
        x0, z0, x1, z1 = room.rect.x, room.rect.z, room.rect.x2 - 1, room.rect.z2 - 1
        edge = [(tx, tz) for tx, tz in cells
                if tx in (x0, x1) or tz in (z0, z1)] or cells
        b.rng.shuffle(edge)

        for tx, tz in edge[:max(1, int(len(cells) * density))]:
            asset = b.palette.prop(category, b.rng)
            if asset is None:
                continue
            # Which wall this cell is against decides which way the piece runs
            # and which way it is pushed. A cell on two walls is a corner; take
            # the first, so the piece stands along one of them rather than
            # diagonally across the angle. The rotation puts the prop's long
            # axis *parallel* to its wall, which is why the perpendicular
            # clearance below is measured on the other axis.
            if tz == z0:
                rot, wall = ROT_S, "n"
            elif tz == z1:
                rot, wall = ROT_N, "s"
            elif tx == x0:
                rot, wall = ROT_E, "w"
            else:
                rot, wall = ROT_W, "e"

            if b.rng.random() > QUARTER_TURN_SHARE:
                rot = (rot + b.rng.choice((-1, 1))) % 24

            # **A wall sits on the cell's own edge, not between cells.** So a
            # prop on the first row of a room has masonry at its own cell
            # boundary, and pushing it *toward* that boundary buries it --
            # which is exactly what the first version of this did, with all
            # four signs inverted. It is pushed away from the wall, by half
            # its own depth so that the piece clears rather than its centre.
            sx, sz = rotated_footprint(asset, rot)
            span = sz if wall in ("n", "s") else sx
            # Measured from the wall's inner face: the piece stands against
            # the masonry, so its near edge lands there and its centre is half
            # its depth further in. WALL_SET_BACK is the floor, for a piece so
            # shallow that sitting flush would still read as centred.
            back = wall_t - 0.5 + max(WALL_SET_BACK, span / 2.0)
            ox, oz = 0.0, 0.0
            if wall == "n":
                oz = back
            elif wall == "s":
                oz = -back
            elif wall == "w":
                ox = back
            else:
                ox = -back

            # Clamped to the **clear floor** -- the room inset by the wall on
            # every side -- rather than to the room rect. A piece is only ever
            # set back from the *one* wall its cell is against, so a wide one
            # standing along the north wall in the first column still reaches
            # into the west wall, which is a second way to end up inside the
            # masonry and does not look any different from the first.
            #
            # A room too narrow to hold the piece between two walls gets it
            # centred: there is no honest answer there, and centred is the one
            # that is wrong symmetrically.
            def _fit(want: float, lo: float, hi: float, span_: float) -> float:
                lo, hi = lo + wall_t + span_ / 2.0, hi - wall_t - span_ / 2.0
                if lo > hi:
                    return (lo + hi) / 2.0
                return min(max(want, lo), hi)

            cx = _fit(tx + 0.5 + ox, float(x0), float(x1 + 1), sx)
            cz = _fit(tz + 0.5 + oz, float(z0), float(z1 + 1), sz)

            # **Re-checked where it ended up, not where it started.** The
            # set-back moves a piece off its own cell, so a candidate cell
            # clear of every door can still put the prop in one. The keepout
            # is about the square a creature walks through, so it is the
            # final position that has to satisfy it.
            if (int(cx), int(cz)) in keepout:
                continue
            # A prop wider than its cell has to be checked, not assumed: the
            # Scatter is the same collision bookkeeping the outdoor scatter
            # uses, and TaleSpire silently drops a prop that overlaps one
            # already placed.
            if not scatter.one(asset, cx, cz, y, rot):
                continue


_PROP_CATEGORY: dict[str, str] = {
    "common room": "tavern", "bar": "tavern", "snug": "tavern",
    "kitchen": "tavern", "forge": "smithy", "workshop": "smithy",
    "shop floor": "shop", "counter": "shop", "store room": "warehouse",
    "main store": "warehouse", "loading bay": "warehouse",
    "nave": "temple", "shrine": "temple", "vestry": "temple",
    "bedroom": "house", "living room": "house", "private room": "house",
    "hall": "house", "dining room": "house", "study": "house",
}

_PROP_CATEGORY_BY_KIND: dict[str, str] = {
    "tavern": "tavern", "shop": "shop", "smithy": "smithy",
    "warehouse": "warehouse", "temple": "temple", "house": "house",
    "manor": "house", "guildhall": "house", "barracks": "house",
    "stable": "warehouse", "apothecary": "shop",
}


# -- imported city boards -----------------------------------------------------

#: Surfaces that can be tiled with a 2x2 asset, and the role that does it.
#: Surfaces tiled with a 2x2 asset. Parks are painted as GROUND by the
#: raster (there is no PARK surface), so they tile as ground.
_BLOCK_SURFACES = {"ground": "ground_2x2", "field": "field", "marsh": "marsh_2x2"}

#: Which role each of those blocks actually lays, so `_block_role` can tell a
#: cell that still wants the class default from one a yard or a quarter has
#: repainted. These mirror `base_roles` in `build_from_tilemap`; they are
#: separate because the block pass runs before that closure is in scope, and
#: because getting them out of step is exactly the bug §10.2 records.
_CLASS_DEFAULT_ROLE = {"ground": "ground", "field": "field_edge", "marsh": "marsh"}

#: How much deeper the bed goes per cell away from the bank, and the most it
#: is allowed to drop. **TaleSpire's water tile is translucent and tints with
#: what is under it** -- verified with a three-channel probe whose beds sat 0,
#: 1 and 2 tiles down under one flat water surface: pale, teal, deep teal.
#: So depth is free. The surface stays a single layer of tiles and only the
#: bed moves, which is why a river can read as deep without costing a thing.
#:
#: Flush bed was the palest of the three, and every cell of the old river was
#: flush -- a uniform, washed-out ditch. Shallows still are: a bank cell keeps
#: its flush bed, which is what makes a ford read as crossable.
WATER_DEEPEN_STEP = 0.5
WATER_MAX_DEEPEN = 1.5


def water_depth(tm) -> dict[tuple[int, int], int]:
    """Cells away from the nearest bank, for every water cell.

    Breadth-first out from the land, so 1 is a cell touching the shore. The
    river bed follows this, which turns a rectangular trench into something
    with shallows at the edges and a channel down the middle.
    """
    from . import raster as R

    # A plank crossing is water with a deck over it: the river runs on
    # underneath, so for the bed it is part of the channel, not a bank.
    wet = (R.WATER, R.PIER)
    depth: dict[tuple[int, int], int] = {}
    frontier: list[tuple[int, int]] = []
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] not in wet:
                continue
            if any(not (0 <= x + dx < tm.width and 0 <= z + dz < tm.depth)
                   or tm.surface[z + dz][x + dx] not in wet
                   for dx, dz in NEIGHBOURS):
                depth[(x, z)] = 1
                frontier.append((x, z))

    step = 1
    while frontier:
        step += 1
        nxt: list[tuple[int, int]] = []
        for x, z in frontier:
            for dx, dz in NEIGHBOURS:
                n = (x + dx, z + dz)
                if not (0 <= n[0] < tm.width and 0 <= n[1] < tm.depth):
                    continue
                if tm.surface[n[1]][n[0]] not in wet or n in depth:
                    continue
                depth[n] = step
                nxt.append(n)
        frontier = nxt
    return depth


#: How far the water's underside sits below the bank top. The bank tile is 0.5
#: thick and the water tile is 0.5 thick, so the old 1.0 put the waterline
#: level with the *underside* of the turf beside it -- half a tile of bank
#: showing, which on the board read as a wet lawn rather than a river. A full
#: tile of bank above the waterline is what makes it read as a channel.
WATER_SURFACE_DROP = 1.5


def _bed_drop(depth: dict[tuple[int, int], int], cell: tuple[int, int]) -> float:
    """How far below the water's underside this cell's bed sits."""
    return min((depth.get(cell, 1) - 1) * WATER_DEEPEN_STEP, WATER_MAX_DEEPEN)


def _fill_water(b: "Builder", asset: Asset, x: int, z: int,
                surface_y: float, bed_y: float) -> None:
    """Water from the bed up to the waterline, one tile per step.

    TaleSpire's water tile is translucent and half a tile thick, so a single
    sheet is exactly the same colour over a ford as over the deepest part of
    the channel -- depth was in the geometry but invisible. Filling the column
    puts it on show: the shallows stay pale and the middle goes dark, which is
    the one cue that tells a party where the river can be waded.
    """
    y = bed_y
    while y <= surface_y + 1e-6:
        b.add(place_tile(asset, x, z, y))
        y += asset.size_y


def _bed_role(b: "Builder", preferred: str, fallback: str) -> str:
    """The riverbed role if the style has one, else whatever the ground is.

    A style is not obliged to ship a bed material, and a river with a grass
    bottom is still better than a build that raises on a missing role.
    """
    return preferred if b.palette.resolve(preferred) is not None else fallback


def _block_role(b: Builder, role: str, surface: str) -> str | None:
    """The 2x2 block that lays ``role``, or None if it has to go a tile at a time.

    ``_BLOCK_SURFACES`` is keyed on the surface *class*, which is right only
    while the class's default role is the one being laid. A yard repaints a
    GROUND cell with `lane_earth` or `yard_gravel`, and the class's block is
    grass -- see the note in pass 1.
    """
    block = _BLOCK_SURFACES.get(surface or "")
    if block is not None and role == _CLASS_DEFAULT_ROLE.get(surface or ""):
        return block if b.palette.resolve(block) is not None else None
    twin = f"{role}_2x2"
    return twin if b.palette.resolve(twin) is not None else None


def _lay_terrain(b: Builder, tm, surface_role, grade: float,
                 taper: dict[tuple[int, int], float | None],
                 reserved: set[tuple[int, int]] | None = None) -> None:
    """Lay the ground plane, preferring 2x2 tiles over 1x1 where it can.

    Open country is most of a map by area and almost all of it by tile count:
    Candlewell spent 29,000 assets on grass alone. Where a 2x2 block is
    uniform, one 2x2 tile replaces four 1x1s for an identical result, so the
    saving is free. Edges and anything mixed fall back to 1x1, which is what
    keeps coastlines and road margins crisp instead of blocky.

    **A cell a later pass will take back is never fused into a block.** That is
    what ``reserved`` is for, and it is not a nicety: `Builder.clear_cells`
    identifies a placement by its collider centre, which for a 2x2 block is one
    of its four cells, so clearing one cell of a block deletes the whole block
    and strips the ground from the other three. Measured on Forest Church
    before this argument existed: 53 posts landed on six 2x2 grass blocks and
    took all six up, orphaning eighteen cells of which **fifteen were left with
    no ground in them at all** -- and the only two anyone could see were under
    the town wall's stair, which then stood over nothing and failed
    `verify.floating_placements`. The other thirteen were bare board in open
    country with nothing standing on them to give them away, which is why this
    survived several reviews. The same trap is written up for yards in
    `build_from_tilemap`, where it was avoided by deciding the material once
    instead of clearing and re-laying; a mark cannot do that, because clearing
    is also how it takes the props out of the cell, so here the block is
    refused. Forest Church pays 44 tiles in 28,586 for it.
    """
    from . import raster as R

    covered: set[tuple[int, int]] = set()
    water_tile = b.palette.resolve("water")
    water_block = b.palette.resolve("water_2x2")
    wdepth = water_depth(tm)

    # A grass tile that ends flush at a sunken watercourse shows its bare side
    # to anyone standing on the bank. A course of shingle along the waterline
    # reads as a shore and hides the cut -- and gives a party somewhere to
    # stand that is visibly not the river.
    bank: set[tuple[int, int]] = set()
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] not in (R.GROUND, R.FIELD) or tm.building[z][x]:
                continue
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = x + dx, z + dz
                if 0 <= nx < tm.width and 0 <= nz < tm.depth                         and tm.surface[nz][nx] == R.WATER:
                    bank.add((x, z))
                    break

    def surface_at(x: int, z: int) -> str | None:
        if not (0 <= x < tm.width and 0 <= z < tm.depth):
            return None
        return tm.surface[z][x]

    # Pass 1: 2x2 blocks on an even grid, only where all four cells agree.
    for z in range(0, tm.depth - 1, 2):
        for x in range(0, tm.width - 1, 2):
            s = surface_at(x, z)
            quad = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]

            # Before every other test, including the water one: a block laid
            # over a cell something will clear later takes its neighbours with
            # it when it goes. See the note on ``reserved``.
            if reserved and any(q in reserved for q in quad):
                continue

            # Open water is the largest single surface on a river map and it
            # tiles perfectly, so a 2x2 quad of it saves three water tiles and
            # three bed tiles. It only qualifies where the whole quad is the
            # same depth, since one 2x2 bed cannot step.
            # The 2x2 shortcut needs a 2x2 *bed* as well as 2x2 water. Without
            # one the quad would be floored in the ground block while the cells
            # around it got the 1x1 bed, so the river would run over two
            # different materials. Falling through costs tiles, not looks.
            if (s == R.WATER and water_block is not None
                    and b.palette.resolve(_bed_role(b, "riverbed_2x2", "ground_2x2")) is not None
                    and all(surface_at(qx, qz) == R.WATER for qx, qz in quad)):
                drops = {taper.get(q, 0.0) for q in quad}
                beds = {_bed_drop(wdepth, q) for q in quad}
                if len(drops) == 1 and None not in drops and len(beds) == 1:
                    here = grade - drops.pop()
                    bed = here - WATER_SURFACE_DROP - beds.pop()
                    b.surface(_bed_role(b, "riverbed_2x2", "ground_2x2"), x, z, bed)
                    _fill_water(b, water_block, x, z,
                                here - WATER_SURFACE_DROP, bed)
                    covered.update(quad)
                continue

            if any(surface_at(qx, qz) != s for qx, qz in quad):
                continue
            # Same surface is not the same *material*: a yard is GROUND and so
            # is the lawn beside it, and a 2x2 block laid across both puts
            # grass over worked ground. Four cells have to agree on the role,
            # not just the class.
            agreed = {surface_role(s, qx, qz) for qx, qz in quad}
            if len(agreed) != 1:
                continue
            # **And agreeing is not enough -- the block has to be that role's
            # own.** The check above was written against a MIXED quad and
            # passes a uniform one, after which `_BLOCK_SURFACES` looked the
            # block up by the surface CLASS: four cells that all agree on
            # `lane_earth` sailed through and were then sheeted in
            # `ground_2x2`, which is grass. Between 41% and 60% of every yard
            # on all four towns came out as lawn that way
            # (`docs/fencing.md` §10.2), and on the board that is the whole
            # of "the surface is contributing nothing".
            #
            # A role with a 2x2 of its own still gets one; anything else falls
            # through to pass 2 and is laid a tile at a time, which costs
            # tiles and is the only way to get the material right.
            role = _block_role(b, agreed.pop(), s)
            if role is None:
                continue
            if any(tm.building[qz][qx] for qx, qz in quad):
                continue
            if any(q in bank for q in quad):
                continue   # shingle is laid one tile at a time
            # A 2x2 lies flat, so it can only serve a quad that sits at one
            # height. Inside the falloff most quads do -- the rings are wide --
            # and refusing all of them cost a thousand extra tiles along the
            # border, which is byte budget spent on ground nobody walks on.
            drops = {taper.get(q, 0.0) for q in quad}
            if len(drops) != 1 or None in drops:
                continue
            here = grade - drops.pop()
            b.surface(role, x, z, here)
            for q in quad:
                b.ground_baseline[q] = here - b.palette.require(role).size_y
            covered.update(quad)

    # Pass 2: everything the blocks did not take.
    ground_role = "ground"
    for z in range(tm.depth):
        row = tm.surface[z]
        for x in range(tm.width):
            if (x, z) in covered:
                continue
            s = row[x]
            if s == R.VOID:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue                    # ragged fringe: nothing laid here
            here = grade - drop
            if s in (R.WATER, R.PIER):
                # A plank cell is bedded and filled like the water either side
                # of it, so the channel runs unbroken under the crossing; the
                # deck itself goes on afterwards (see ``_lay_bridges``). It
                # used to be laid as cobble at grade -- a quarter-tile slab
                # with a tile of air under it and no bed below.
                #
                # Water sits below grade so it reads as a channel a creature
                # can be pulled into, not a hole punched through the board.
                # The bed and its water fall with the land, so a river does
                # not end up perched above a tapered bank. No baseline is
                # recorded: a channel is a feature, not background ground.
                #
                # Only the *bed* varies with distance from the bank; the
                # surface stays one flat layer. The water tile is translucent,
                # so that alone gives shallows at the edges and a dark channel
                # down the middle without a single extra tile.
                bed = here - WATER_SURFACE_DROP - _bed_drop(wdepth, (x, z))
                b.surface(_bed_role(b, "riverbed", ground_role), x, z, bed)
                if water_tile is not None:
                    _fill_water(b, water_tile, x, z,
                                here - WATER_SURFACE_DROP, bed)
                continue
            role = surface_role(s, x, z)
            if (x, z) in bank and b.palette.resolve("field_1x1") is not None:
                role = "field_1x1"          # shingle shore
            b.surface(role, x, z, here)
            b.ground_baseline[(x, z)] = here - b.palette.require(role).size_y


#: A civic building earns a tower if it is at least this many tiles and this
#: much longer than it is wide. A long narrow plan is a nave, and a nave with
#: a tower at one end is the one silhouette nobody mistakes for a barn. The
#: Forest Church's temple is 6x19; nothing else on the map qualifies, which is
#: the point -- a landmark stops being one if every building has a tower.
TOWER_MIN_TILES = 60
TOWER_MIN_ASPECT = 2.5
TOWER_EXTRA_STOREYS = 3


def pick_towers(tm, ceiling: int) -> dict[tuple[int, int], str]:
    """Cells that carry a tower, keyed to the building they belong to.

    The tower is a square block at the *narrow* end of the plan, sized to the
    building's width, so it sits over the end of the nave rather than beside
    it. Roofs skip these cells: the tower carries its own.
    """
    towers: dict[tuple[int, int], str] = {}
    for bid, cells in footprints(tm).items():
        if bid.split("-")[0] not in CIVIC_KINDS or len(cells) < TOWER_MIN_TILES:
            continue
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        long_axis_z = d >= w
        span, across = (d, w) if long_axis_z else (w, d)
        if span < across * TOWER_MIN_ASPECT:
            continue

        side = min(across, span // 3)
        if side < 2:
            continue
        # Whichever end has more of its footprint intact takes the tower.
        if long_axis_z:
            near = {c for c in cells if c[1] < min(zs) + side}
            far = {c for c in cells if c[1] > max(zs) - side}
        else:
            near = {c for c in cells if c[0] < min(xs) + side}
            far = {c for c in cells if c[0] > max(xs) - side}
        block = near if len(near) >= len(far) else far
        for cell in block:
            towers[cell] = bid
    return towers


def _lay_towers(b: Builder, tm, towers: dict[tuple[int, int], str], face,
                top: float, storey_h: float, ceiling: int) -> None:
    """Raise the tower blocks and roof each one separately.

    The building's own walls already reach its eaves; a tower carries on from
    there. Its perimeter is computed against the *tower* block rather than the
    footprint, so the wall that separates tower from nave is built too --
    without it the tower would be an open-sided box sat on the roof.
    """
    if not towers:
        return
    cap = b.palette.resolve("city_wall_cap")
    floor_tile = b.palette.resolve("floor_upper") or b.palette.require("floor")

    by_building: dict[str, set[tuple[int, int]]] = {}
    for cell, bid in towers.items():
        by_building.setdefault(bid, set()).add(cell)

    for bid, cells in sorted(by_building.items()):
        b.group = bid
        # The *tallest* shell course under the tower, not the building's
        # nominal count: a large footprint is built as two ranges
        # (`building_ranges`), and a tower standing on the lower one would
        # start above its own walls.
        base_floors = max(storeys_at(tm, bid, x, z, ceiling) for x, z in cells)
        # **From base_floors, not base_floors + 1.** The building's top wall
        # course is level `base_floors - 1`, whose head is at
        # `top + base_floors * storey_h` -- so starting a course later leaves
        # exactly one storey of daylight between the nave's eaves and the
        # tower's base. Measured on Forest Church's temple: the shell tops out
        # at y=4.5 and the tower's first floor was at y=6.0.
        #
        # The old comment blamed the building's own ceiling for filling that
        # gap. It does not: upper decks run levels 1..floors-1, so the level
        # this now occupies carries no deck of its own.
        for level in range(base_floors, base_floors + TOWER_EXTRA_STOREYS):
            y = top + level * storey_h
            for (x, z) in sorted(cells):
                # Below the course, in the gap the storey pitch leaves for it,
                # exactly as the building's own floors sit.
                b.add(place_tile(floor_tile, x, z, y - floor_tile.size_y))
                for side, dx, dz in SIDE_OFFSETS:
                    if (x + dx, z + dz) not in cells:
                        b.add(place_wall(face, x, z, side, y))

        # Above the top course, not level with it. Crowning at the course's
        # own base put every merlon inside the wall it was supposed to sit on
        # -- the same buried-geometry mistake as the rampart facing, one
        # storey up.
        top_course = top + (base_floors + TOWER_EXTRA_STOREYS - 1) * storey_h
        crown = top_course + face.size_y
        rings = _roof_rings(cells)
        # **The building's own roof set, not the default one.** These four
        # were unsuffixed `resolve` calls, which is the thatched baseline --
        # so a dressed-stone temple got a *thatched* cap inside its stone
        # battlements while the nave below it was roofed in slate. The kit
        # rule reaches the tower too.
        side_piece, corner, inner, flat, _chimney = roof_set(
            b.palette, tier_of(bid), bid)
        rise = side_piece.size_y if side_piece is not None else 1.0
        # Battlements round the parapet, roof only *inside* them. Doing both
        # on the same cell put a merlon and a roof piece at one height, 20
        # pairs of them intersecting -- and a hip roof laid over a parapet is
        # not what a bell tower looks like anyway.
        parapet = {c for c in cells
                   if any((c[0] + dx, c[1] + dz) not in cells
                          for _, dx, dz in SIDE_OFFSETS)}
        for (x, z) in sorted(cells):
            if (x, z) in parapet:
                if cap is not None:
                    b.add(place_tile(cap, x, z, crown))
                continue
            r = rings[(x, z)]
            fall = tuple(sd for sd, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            edge_off, corner_off = roof_offsets(side_piece)
            piece, rot = _roof_piece(fall, side_piece, corner, flat, inner,
                                     _is_reflex(rings, x, z, fall),
                                     edge_off, corner_off)
            if piece is not None:
                b.add(place_tile(piece, x, z, crown + (r - 1) * rise, rot))


#: How far a yard reaches out from its building, in cells, when nothing is
#: measured. Two is 10 ft -- enough for a wood stack and somewhere to stand.
#: **This is now only the fallback**, for a building with no footprint to
#: measure clearance from; :func:`yard_reach_by_side` is what a real building
#: gets. See :data:`YARD_MAX_REACH`.
YARD_REACH = 2

#: The most ground a yard may take on ONE side, in cells. Four is 20 ft.
#:
#: **A single reach for every building was the whole of the sizing, and on a
#: board it is the wrong shape twice over.** At 2 cells a plot is an L round
#: one corner of the house -- a corner, not an enclosure; reviewed on four
#: boards at reach 1 to 4, nothing reads until 3 (`docs/fencing.md` §10.6).
#: And a uniform apron gives a farmstead standing in open country the same
#: 10 ft skirt as a house wedged between two neighbours, so every yard in a
#: town is the same yard.
#:
#: Cost was expected to be the objection and is not, because `YARD_MIN_GAP`
#: already gates *which* buildings qualify: across all four towns a uniform
#: reach of 4 is 7-10% of open ground against reach 2's 3-5%.
YARD_MAX_REACH = 4

#: How deep the yard on the DOOR's side is allowed to be.
#:
#: A house fronting a street keeps a shallow strip in front and puts its ground
#: behind: that is where the wood, the midden and the work go, and it is the
#: difference between a front yard and a back one. Capping the door side is the
#: whole of that distinction -- everything else falls out of the clearance
#: measurement, so a building with room only in front still gets a front yard,
#: it is just a shallow one.
YARD_FRONT_REACH = 2

#: Clearance below this does not make a yard on that side: one cell of worked
#: ground against a wall is a verge, not somewhere to stand.
YARD_MIN_SIDE = 2

#: How long a stretch of STREET FRONTAGE has to run straight before it is
#: worth fencing, in cells.
#:
#: **The old rule left every edge onto a way open, and that is two failures in
#: one line** (`docs/fencing.md` §10.4). A plot fronting a lane along its whole
#: side had that whole side missing -- 27-29% of every town's yard perimeter,
#: and on the board a three-sided pen rather than an enclosure. And a yard
#: that touches no way at all had nothing to borrow an opening from, so 17 of
#: East Tradebourne's 230 were sealed rings.
#:
#: Closing the ring outright is not the answer either, and the board said so:
#: a frontage against a *diagonal* lane is a stair-step, and a stair-step built
#: from 2-tile panels is a comb of crossed pieces lying over the paving. So the
#: frontage is fenced where it actually runs straight and left open where it
#: steps -- which is what a street frontage is, and it makes the openings fall
#: where the boundary was ragged anyway. Three cells is 15 ft: enough to read
#: as a length of wall rather than as one more step.
FRONTAGE_MIN_RUN = 3

#: The shortest boundary run worth building when nothing meets either end of
#: it, in cells. Four is two panels: enough to read as a length of fence.
#:
#: A single panel standing alone is the stub this pass exists to stop building,
#: and chaining the runs did not remove it -- it removed the one-cell version
#: and left the two-cell one, 11% of Graybank's kept runs. A run with a real
#: corner at either end is a different thing and has no minimum: the
#: perpendicular run holds it, which is what a boundary turning a corner looks
#: like.
FENCE_MIN_ISOLATED = 4

#: The shortest boundary run worth building at all, in cells.
#:
#: A run with nothing at either end and less than a panel in it is not a fence,
#: it is a panel lying in the grass -- and there were a lot of them: 22-36% of
#: every town's yard boundary runs are one or two cells
#: (`docs/fencing.md` §10.3). A short run that *is* part of a continuous
#: boundary is kept, because the perpendicular runs meeting it take the
#: overhang as an ordinary corner. Two cells is exactly one panel, so this
#: only ever drops the ones that cannot be built without overhanging both
#: their own ends -- which is the rule `FENCE_MIN_SEGMENT` states for field
#: walls, applied to a boundary made of cell edges rather than of a polyline.
FENCE_MIN_RUN = 2

#: A yard is only worth surfacing if this many of its cells survive. A single
#: cell of gravel beside a wall is a smudge, not a yard.
YARD_MIN_CELLS = 3

#: How far a building must stand from its nearest neighbour, in cells, before
#: the ground round it is *its* yard rather than the gap in a terrace.
#:
#: **This gate is the whole of the feature and it was got wrong twice.** With
#: no gate at all, a 2-cell apron round every building gave 100% of every town
#: a yard -- 31,927 cells on East Tradebourne, four fifths as much ground as
#: all its paving, which on a board is a gravelled city. Local built *density*
#: was tried next and does not discriminate: measured within 6 cells it is 0.25
#: median on Pelvesthollow against 0.30 on East Tradebourne, because FTG
#: footprints are all much the same size.
#:
#: What does discriminate is the gap to the nearest *other* building, measured
#: on the raster:
#:
#:     gap >= 3 cells    Pelvesthollow 57%   Graybank 59%
#:                       Forest Church 29%   East Tradebourne 23%
#:
#: which is the shape the design argued for -- a hamlet's buildings mostly
#: stand apart, a city's mostly do not -- arrived at by measurement rather than
#: by keying on the settlement band, so an outlying farm on a city's edge still
#: gets its yard.
YARD_MIN_GAP = 3

#: Which surface a trade works on. Anything not here gets the default.
YARD_SURFACE = {
    # A walled property's ground is swept hard standing, not a trodden lane.
    # `yard_cells` keys a compound's pooled yard as "compound-NN".
    "compound": "yard_gravel",
    "smithy": "yard_gravel",
    "stable": "lane_earth",
    "warehouse": "yard_gravel",
    "shed": "lane_earth",
}
DEFAULT_YARD_SURFACE = "lane_earth"


def _clearance(tm, cells: set, side: str, dx: int, dz: int) -> int:
    """How far open ground reaches out from one face of a building, in cells.

    Walked from every footprint cell that actually has that face exposed, and
    reported as the **median** of those runs rather than the least of them. A
    face with one corner clipped by a neighbour still has a yard on it; taking
    the minimum would let a single blocked cell veto the whole side, which on
    a rasterised L-shaped footprint is most of them.

    The walk stops at anything that is not open ground -- a building, a road, a
    watercourse, the map edge -- so the number is "how much of somebody's own
    ground is out this way", which is exactly what decides how big a yard can
    be. It stops early at :data:`YARD_MAX_REACH`, since nothing above that is
    used and open country would otherwise walk to the edge of the map.
    """
    from . import raster as R

    runs = []
    for x, z in cells:
        if (x + dx, z + dz) in cells:
            continue                      # interior: this face is not exposed
        n = 0
        nx, nz = x + dx, z + dz
        while (tm.inside(nx, nz) and not tm.building[nz][nx]
               and tm.surface[nz][nx] == R.GROUND and n < YARD_MAX_REACH):
            n += 1
            nx += dx
            nz += dz
        runs.append(n)
    if not runs:
        return 0
    runs.sort()
    return runs[len(runs) // 2]


def yard_reach_by_side(tm, bid: str, cells: set | None = None) -> dict[str, int]:
    """How far this building's yard reaches on each of its four sides.

    **The variance comes from the site, not from a seed**, and that is
    deliberate: two farmsteads with the same room round them should get the
    same yard, and the thing that ought to differ between a farmstead and a
    terrace house is the ground each actually has. `docs/district-surfaces.md`
    makes the same argument about wards -- an axis that does not discriminate
    is a knob dressed as a feature.

    Three inputs, in order of how much they decide:

    * **Clearance per side** (:func:`_clearance`) sets the reach. A side with
      20 ft of its own ground gets 20 ft of yard; a side against a neighbour's
      wall gets none.
    * **The door's side is capped** at :data:`YARD_FRONT_REACH`, so a house on
      a street keeps a shallow frontage and puts its ground round the back.
    * **A side under :data:`YARD_MIN_SIDE` gets nothing**, so a one-cell gap
      between two buildings is not fenced as though it were a yard.

    Returns ``{"n": r, "e": r, "s": r, "w": r}``; :func:`yard_form` names the
    shape that comes out.
    """
    if cells is None:
        cells = {(x, z) for z in range(tm.depth) for x in range(tm.width)
                 if tm.building[z][x] == bid}
    if not cells:
        return {side: 0 for side, _, _ in SIDE_OFFSETS}

    doors = tm.doors.get(bid) or []
    front = doors[0][2] if doors else None

    out: dict[str, int] = {}
    for side, dx, dz in SIDE_OFFSETS:
        reach = _clearance(tm, cells, side, dx, dz)
        if side == front:
            reach = min(reach, YARD_FRONT_REACH)
        out[side] = reach if reach >= YARD_MIN_SIDE else 0
    return out


#: What a set of per-side reaches is called, for the build report and the
#: scene brief. The names are the ones a person uses about a plot, so a report
#: line reads as a description of the town rather than as four integers.
def yard_form(reaches: dict[str, int], front: str | None = None) -> str:
    """Name the shape of a yard: full, back, front, through, corner, side."""
    live = [s for s, r in reaches.items() if r > 0]
    if not live:
        return "none"
    if len(live) == 4:
        return "full"
    opposite = {"n": "s", "s": "n", "e": "w", "w": "e"}
    if len(live) == 1:
        if front is None:
            return "side"
        return "front" if live[0] == front else (
            "back" if live[0] == opposite[front] else "side")
    if len(live) == 2:
        return "through" if opposite[live[0]] == live[1] else "corner"
    return "wrapped"


def yard_cells(tm) -> dict[str, set[tuple[int, int]]]:
    """Building id -> the open ground worth calling its yard.

    **Only where there is room, which is measured rather than assumed.** On
    East Tradebourne 94% of buildings touch a neighbour and the 90th-percentile
    gap is two thirds of a tile, so almost nothing here qualifies and almost
    nothing is built -- correctly. On Pelvesthollow the median gap is 2.6 tiles
    and one building in seven stands in over 40 ft of clear ground, which is
    where yards actually are. `docs/building-massing.md` §4.

    Street and lane cells are excluded: the ground in front of a shop is the
    street's, not the shop's, and paving it as a yard would eat the way.

    **The apron is not square.** Each side reaches as far as that side's own
    open ground allows (:func:`yard_reach_by_side`), capped in front of the
    door, so a farmstead in open country gets a full yard up to 20 ft deep and
    a house wedged into a terrace gets a back yard and nothing else. The forms
    that come out are named by :func:`yard_form`.
    """
    from . import raster as R

    apart = _standing_apart(tm)

    # **Measured once per building, not once per cell.** The apron is walked
    # cell by cell below, and `yard_reach_by_side` walks the whole footprint;
    # doing it inside the sweep made a 989-building town quadratic in its own
    # footprints.
    footprints: dict[str, set[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid in apart:
                footprints.setdefault(bid, set()).add((x, z))
    reaches = {bid: yard_reach_by_side(tm, bid, cells)
               for bid, cells in footprints.items()}

    claimed: dict[tuple[int, int], str] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if not bid or bid not in apart:
                continue
            reach = reaches[bid]
            for dz in range(-reach["n"], reach["s"] + 1):
                for dx in range(-reach["w"], reach["e"] + 1):
                    nx, nz = x + dx, z + dz
                    if not tm.inside(nx, nz):
                        continue
                    if tm.building[nz][nx] or tm.surface[nz][nx] != R.GROUND:
                        continue
                    # First building to reach a cell keeps it, so two
                    # neighbours do not both fence the same strip.
                    claimed.setdefault((nx, nz), bid)

    # **Buildings inside one enclosure share one yard.** Keyed per building,
    # a keep and its garrison range each claimed their own apron and each
    # fenced it, so the board showed two paling rectangles nested inside the
    # barricade that already enclosed them both -- three fences deep, and the
    # two halves of one property reading as two smallholdings. Pooling them
    # under the compound id makes the ground between the buildings *theirs*
    # rather than a no-man's-land the first one to reach it happened to win.
    inside = R.compounds(tm)
    out: dict[str, set[tuple[int, int]]] = {}
    for cell, bid in claimed.items():
        out.setdefault(inside.get(bid, bid), set()).add(cell)
    return {b: cs for b, cs in out.items() if len(cs) >= YARD_MIN_CELLS}


def _standing_apart(tm) -> set[str]:
    """Buildings whose nearest neighbour is at least :data:`YARD_MIN_GAP` away.

    One multi-source flood from every building cell at once, each cell carrying
    the id of the building that reached it; where two floods meet, the sum of
    their depths is the gap between those buildings.
    """
    from collections import deque

    owner: dict[tuple[int, int], str] = {}
    depth: dict[tuple[int, int], int] = {}
    queue: deque[tuple[int, int]] = deque()
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                owner[(x, z)] = bid
                depth[(x, z)] = 0
                queue.append((x, z))

    nearest: dict[str, int] = {}
    while queue:
        x, z = queue.popleft()
        if depth[(x, z)] >= YARD_MIN_GAP:
            continue
        for dx, dz in NEIGHBOURS:
            n = (x + dx, z + dz)
            if not tm.inside(*n):
                continue
            if n in owner:
                if owner[n] != owner[(x, z)]:
                    gap = depth[(x, z)] + depth[n]
                    for who in (owner[(x, z)], owner[n]):
                        nearest[who] = min(nearest.get(who, 99), gap)
                continue
            owner[n] = owner[(x, z)]
            depth[n] = depth[(x, z)] + 1
            queue.append(n)

    everyone = {v for row in tm.building for v in row if v}
    return {b for b in everyone if nearest.get(b, 99) >= YARD_MIN_GAP}


#: The four cardinal sides, as the world segment each cell edge lies on.
#: ``(t along the run, the boundary line, which way is inward, runs along x)``
#: -- the whole of what :func:`_run_panels` needs to know about a side.
_RUN_AXIS = {
    "n": (0.0, +1.0, True),
    "s": (1.0, -1.0, True),
    "w": (0.0, +1.0, False),
    "e": (1.0, -1.0, False),
}


def boundary_runs(tm, cells: set, all_yard: set, ways: frozenset,
                  *, skip_ways: bool = True
                  ) -> list[tuple[str, int, int, int]]:
    """One yard's edge, chained into maximal straight runs.

    ``(side, fixed coordinate, first, last)`` -- for n/s a run travels along x
    at a fixed z, for e/w along z at a fixed x.

    **A boundary is a set of cell edges and a fence is a run of panels, and
    those are not the same thing.** Laying one piece per cell edge is what the
    yard pass used to do, and with a 2-tile panel on a 1-tile edge it built
    every fence twice over -- 507 of Pelvesthollow's 599 panels had another
    lying on them lengthwise, which on the board is posts every 5 ft instead of
    every 10 and a stub overhanging past every corner (`docs/fencing.md` §10.1).
    Chaining first is what lets the run be stepped at the panel's own length.

    An edge is on the boundary when the cell across it is neither yard nor
    building; an edge onto a way is left open, because that is the way in.
    """
    lanes: dict[tuple[str, int], list[int]] = {}
    for x, z in cells:
        for side, dx, dz in SIDE_OFFSETS:
            nx, nz = x + dx, z + dz
            if not tm.inside(nx, nz):
                continue
            if (nx, nz) in all_yard or tm.building[nz][nx]:
                continue
            if skip_ways and tm.surface[nz][nx] in ways:
                continue
            key = (side, z) if side in ("n", "s") else (side, x)
            lanes.setdefault(key, []).append(x if side in ("n", "s") else z)

    runs: list[tuple[str, int, int, int]] = []
    for (side, fixed), vals in sorted(lanes.items()):
        vals.sort()
        start = prev = vals[0]
        for v in vals[1:]:
            if v == prev + 1:
                prev = v
                continue
            runs.append((side, fixed, start, prev))
            start = prev = v
        runs.append((side, fixed, start, prev))
    return runs


def facing_a_way(tm, run, ways: frozenset) -> int:
    """How many cells of this run look out onto a street, lane, plaza or pier."""
    side, fixed, a, b = run
    dx, dz = {s: (x, z) for s, x, z in SIDE_OFFSETS}[side]
    n = 0
    for v in range(a, b + 1):
        x, z = (v, fixed) if side in ("n", "s") else (fixed, v)
        nx, nz = x + dx, z + dz
        if tm.inside(nx, nz) and tm.surface[nz][nx] in ways:
            n += 1
    return n


def _run_ends(run) -> tuple[tuple[int, int], tuple[int, int]]:
    """The two cells a run starts and ends at, for the isolation test."""
    side, fixed, a, b = run
    if side in ("n", "s"):
        return (a, fixed), (b, fixed)
    return (fixed, a), (fixed, b)


def _stub(run, runs) -> bool:
    """Is this run too short to build, with nothing at either end to hold it?

    Two thresholds, because there are two ways a short run goes wrong. A run
    under :data:`FENCE_MIN_RUN` cannot be built at all without the panel
    overhanging both its own ends; a run under :data:`FENCE_MIN_ISOLATED` can,
    but with nothing at either end it is one panel lying in the grass rather
    than a fence. Together they were 22-36% of every town's yard boundary runs
    (`docs/fencing.md` §10.3). A run with a real corner at either end is held
    by it and has no minimum.

    **The neighbour has to be long enough itself, and that is not pedantry.**
    The first cut only asked whether any perpendicular run shared an endpoint,
    which a LONE YARD CELL satisfies four times over -- its own four sides all
    meet at itself. Graybank built 21 of those, each a cross of four 2-tile
    panels centred on one 5 ft square, and because such a cell is usually an
    island cut off by a road, the arms landed in the carriageway. That is the
    comb this whole pass exists to avoid, arriving from the one direction the
    run-chaining did not cover.
    """
    length = run[3] - run[2] + 1
    if length >= FENCE_MIN_ISOLATED:
        return False
    mine = set(_run_ends(run))
    for other in runs:
        if other is run or other[0] == run[0]:
            continue
        if other[3] - other[2] + 1 < FENCE_MIN_RUN:
            continue
        if mine & set(_run_ends(other)):
            return False               # a corner holds it, whatever its length
    return True


def _run_panels(piece, run) -> list[tuple[float, float, int]]:
    """Where pieces go along one run, stepped at the piece's own length.

    Not at :data:`FENCE_MODULE`, though for every piece in the `Fences` kit it
    is the same 2.0: the length is read off the collider so a per-tier boundary
    built from some other kit still steps correctly. Same rule as everywhere
    else here -- an asset's shape is data, and assuming it is the bug.

    **Panels butt outward from BOTH ends and the remainder is one gap in the
    middle** -- not a last panel pulled back over its neighbour, which was the
    first cut. A boundary is only ever an odd number of cells long half the
    time, and rounding *up* laps one panel per odd run: 70 pairs on
    Pelvesthollow, every one of them a genuine collinear lap that
    `_prop_collisions` is right to fail now that it can tell a lap from a
    corner. Rounding down instead puts a 5 ft gap mid-run, where a gate would
    be, and keeps both corners flush -- and a corner is the part that reads.

    A run shorter than one panel gets a single centred piece. That is a jog
    inside a longer boundary (`_stub` drops the ones with nothing at either
    end), and the perpendicular runs meeting it take the overhang as an
    ordinary corner.
    """
    plen = max(piece.size_x, piece.size_z)
    thick = min(piece.size_x, piece.size_z)
    at_end, inward, along_x = _RUN_AXIS[run[0]]
    side, fixed, a, b = run

    t0, t1 = float(a), float(b + 1)
    line = float(fixed) + at_end
    off = line + inward * thick / 2.0
    rot = _SIDE_ROT[side]
    if piece.size_z > piece.size_x:
        rot = (rot + _QUARTER) % 24

    length = t1 - t0
    n = math.floor(length / plen + 1e-9)
    if n <= 1:
        ts = [(t0 + t1) / 2.0]
    else:
        front = (n + 1) // 2
        ts = [t0 + plen / 2.0 + i * plen for i in range(front)]
        ts += [t1 - plen / 2.0 - i * plen for i in range(n - front)][::-1]
    return [(t, off, rot) if along_x else (off, t, rot) for t in ts]


#: How much of a way cell a boundary piece has to cover before it counts as
#: standing in the road. A 2.06-long hedge on a 2.0 run overhangs its
#: neighbours by 0.03 -- an inch and a half -- and calling that an obstruction
#: would fail every build for nothing.
WAY_INTRUSION = 0.25


def covered_cells(piece, cx: float, cz: float, rot: int,
                  threshold: float = WAY_INTRUSION):
    """Which cells a boundary piece substantially stands on.

    The panel's own body, rather than points sampled along it. Sampling is what
    `_lay_fences` did -- centre and both ends -- and it cannot see a panel that
    crosses the corner of a road cell between two samples: 14 of
    Pelvesthollow's field-wall panels were standing in a lane, found by a check
    that measured the box.
    """
    sx, sz = rotated_footprint(piece, rot)
    x0, x1 = cx - sx / 2, cx + sx / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    for x in range(math.floor(x0), math.ceil(x1)):
        for z in range(math.floor(z0), math.ceil(z1)):
            if (min(x1, x + 1) - max(x0, x) > threshold
                    and min(z1, z + 1) - max(z0, z) > threshold):
                yield x, z


def blocks_a_way(tm, piece, cx: float, cz: float, rot: int,
                 ways: frozenset) -> bool:
    """Would this boundary piece stand in a street, lane, plaza or pier?

    A wall across a road is an impassable line through the one thing the map
    exists to let people walk down, so both boundary passes refuse the panel
    rather than explain the exception, and `verify` measures that they did.
    """
    return any(tm.inside(x, z) and tm.surface[z][x] in ways
               for x, z in covered_cells(piece, cx, cz, rot))


def _lay_yards(b: Builder, tm, grade: float,
               taper: dict[tuple[int, int], float | None]) -> int:
    """Surface the worked ground round a building and fence it off.

    Until now a yard got *props* and nothing else -- `_dress_districts` keeps
    trees back and drops a log pile in the gap -- so the grass ran right up to
    every wall and the space between two cottages read as a gap rather than as
    somebody's yard.

    Two things make it a place: a surface that is not lawn, and an edge.

    **The edge follows cell edges, and that is the opposite of what
    `_lay_fences` does -- deliberately.** A field boundary is a surveyed line
    at an arbitrary bearing, and stroking one into cells stair-steps it
    (`docs/fencing.md` §2.2). A yard boundary is the outline of a rasterised
    region round a rectangular building: it *is* axis-aligned, so cell edges
    are its true shape rather than an approximation of one.

    **What it does not do is step by the cell.** The boundary is chained into
    straight runs (:func:`boundary_runs`) and each run is stepped at the
    panel's own length (:func:`_run_panels`), which is the rule
    `FENCE_MODULE` already states for field walls. One piece per cell edge
    built every fence twice; §10.1.

    The edge onto a street or a lane is left open. That is the way in, and a
    yard sealed on all four sides is a courtyard nobody can enter.

    Returns the number of cells surfaced.
    """
    from . import raster as R

    yards = yard_cells(tm)
    if not yards:
        return 0

    all_yard = {c for cs in yards.values() for c in cs}
    ways = frozenset({R.STREET, R.PLAZA, R.LANE, R.PIER})
    laid = 0
    pieces = 0

    # **A compound is already enclosed, so its yard is not fenced again.**
    # The barricade round a keep *is* the yard fence; putting a paling round
    # the buildings inside it as well is the third boundary in twenty feet.
    enclosed = set(R.compounds(tm).values())

    for bid, cells in sorted(yards.items()):
        fence_this = None if bid in enclosed else yard_boundary(b.palette, bid)
        # **The surface is laid by `_lay_terrain`, not here.** It used to be
        # laid here, over ground that pass had already sheeted -- two coplanar
        # tiles in every yard cell, which TaleSpire keeps and lets z-fight.
        # `surface_role` decides the yard's material with everything else now,
        # so the cell gets exactly one tile. This pass fences and nothing more.
        # **Not tagged with the building id.** `Builder.group` exists so a
        # building's *shell* is never split across chunks; a yard is terrain,
        # and tagging it made the landscape chunk claim the building and count
        # it in `SlabChunk.buildings`, which is the number a missing structure
        # paste is diagnosed from.
        for x, z in sorted(cells):
            if taper.get((x, z), 0.0) is None:
                continue
            laid += 1

        if fence_this is None:
            continue
        # The whole ring, frontage included -- then the frontage is opened
        # again wherever it does not run straight. See `FRONTAGE_MIN_RUN`.
        runs = boundary_runs(tm, cells, all_yard, ways, skip_ways=False)
        keep, opened = [], False
        for run in runs:
            if _stub(run, runs):
                opened = True
                continue
            if (facing_a_way(tm, run, ways)
                    and run[3] - run[2] + 1 < FRONTAGE_MIN_RUN):
                opened = True
                continue
            keep.append(run)

        # **Every yard gets a way in.** If nothing above opened one -- a plot
        # ringed by its own straight boundary, or one that touches no way at
        # all -- a gate is cut in the longest run, on the side facing the most
        # paving. A yard sealed on four sides is a courtyard nobody can enter,
        # which is what this pass has said since it was written and what 17 of
        # East Tradebourne's 230 yards were.
        gate = None
        if not opened and keep:
            gate = max(keep, key=lambda r: (facing_a_way(tm, r, ways),
                                            r[3] - r[2]))

        for run in keep:
            panels = _run_panels(fence_this, run)
            if run is gate and panels:
                panels.pop(len(panels) // 2)
            for cx, cz, rot in panels:
                cell = (int(math.floor(cx)), int(math.floor(cz)))
                drop = taper.get(cell, 0.0)
                if drop is None:
                    continue
                if blocks_a_way(tm, fence_this, cx, cz, rot, ways):
                    continue
                b.add(place_centered(fence_this, cx, cz, grade - drop, rot),
                      prop=True)
                pieces += 1
    b.yard_pieces = pieces
    return laid


#: What each tier's yard is bounded with.
#:
#: **The facade has dealt a kit per tier for a long time and the yard dealt one
#: piece for everything**: 3.4 ft of `Wooden Fence` round a temple precinct, a
#: smithy and a cottage alike. Read against the alternatives on one board, at
#: the distance a party sees them from, the paling is the weakest of four --
#: low and see-through, closer to decoration than to a boundary
#: (`docs/fencing.md` §10.5). All four pieces were already pinned.
#:
#: The assignment is what each boundary IS, not a ranking:
#:
#: * **civic** -- a precinct wall. `Stone Wall 02`, 7 ft, and grand, which is
#:   wrong on a cottage and right round a temple.
#: * **trade** -- a working yard with stock and tools in it wants a real wall.
#:   Drystone, 5 ft.
#: * **common** -- a garden. The hedge is a living boundary and it breaks up a
#:   town that would otherwise be masonry from end to end.
#: * **utility** -- a paddock or a stock pen behind a shed, which is what
#:   timber paling actually is. The weakest read, on the buildings that carry
#:   the least.
#:
#: A compound is not here: it is enclosed already, and its barricade *is* its
#: yard fence.
YARD_BOUNDARY = {
    "civic": "field_wall_tall",
    "trade": "field_wall",
    "common": "field_hedge",
    "utility": "yard_fence",
}

#: When a tier's piece is missing from the installed packs. Every style falls
#: back to the one every medieval catalog has.
DEFAULT_YARD_BOUNDARY = "yard_fence"


def yard_boundary(palette, bid: str):
    """The piece this building's yard is bounded with, by tier."""
    role = YARD_BOUNDARY.get(tier_of(bid), DEFAULT_YARD_BOUNDARY)
    return palette.resolve(role) or palette.resolve(DEFAULT_YARD_BOUNDARY)


#: What gathers in a yard, by trade, as palette prop categories. A yard with
#: nothing in it is a fenced field: the first lot probe showed 100-cell
#: enclosures containing a couple of stray barrels, because the only clutter
#: pass aimed at the *street frontage* and the yard was never a target.
#:
#: `TRADE_CLUTTER` is the model and this is the same idea pointed at the back
#: of the plot rather than the front of it.
YARD_CLUTTER = {
    "smithy": ("smithy", "smithy", "house"),
    "stable": ("house", "house", "tavern"),
    "warehouse": ("shop", "shop", "house"),
    "shed": ("house", "smithy"),
    "tavern": ("tavern", "tavern", "shop"),
    "shop": ("shop", "house"),
    "apothecary": ("shop", "house"),
    "house": ("house",),
}
DEFAULT_YARD_CLUTTER = ("house",)

#: Chance a yard cell gets something standing on it. A yard is worked ground,
#: not a junkyard; `docs/interior-slabs.md` measures hand-built *interiors* at
#: 0.41-0.66 props per cell, and outdoors wants far less than that.
YARD_CLUTTER_RATE = 0.16

#: A board this size or smaller gets the full detail multiplier.
DETAIL_SMALL_TILES = 40_000
#: A board this size or larger gets none of it.
DETAIL_LARGE_TILES = 200_000
#: What a small board's human dressing is multiplied by.
DETAIL_MAX_SCALE = 2.0


#: Assets per chunk on a small board and on a large one. Interpolated between
#: `DETAIL_SMALL_TILES` and `DETAIL_LARGE_TILES`, the same two thresholds
#: `detail_scale` uses -- deliberately, because they are two halves of one
#: question: how much can this board afford, and how finely does it have to be
#: cut to stay under the slab cap while carrying it.
BUDGET_SMALL_BOARD = 9000
BUDGET_LARGE_BOARD = 6000


def asset_budget(tm) -> int:
    """Assets per chunk, from board size.

    **MEASURED 2026-08-25, and it corrects the obvious move.** After the yard,
    fence and surface work, East Tradebourne's largest slab reached 30,546 of
    30,720 bytes -- 99.4%, valid with nothing to spare. The intuitive fix is a
    smaller `--chunk-tiles`, and it makes things *worse*: at 96 tiles the build
    fails outright with a chunk at 31,739 bytes, because a smaller cell leaves
    more trimmed open-country chunks for `_absorb_open_country` to fuse back
    into the kept ones. The lever that works is the per-chunk asset budget,
    which is what the quadtree splits on.

    One number for every board is wrong in both directions, measured on the
    three towns at 112- and 80-tile cells:

    ======  ==============  ==============  =========================
    budget  East Tradeb.    Graybank        note
    ======  ==============  ==============  =========================
    9000    102 ch, 99.4%   22 ch, 81%      the town is a rounding error from failing
    6500    114 ch, 79%     --              the town is safe
    6000    135 ch, 70%     37 ch, 68%      the *village* pays 15 extra pastes for headroom it does not need
    5500    159 ch, 67%     --              two thirds, at 45 more pastes than 6500
    ======  ==============  ==============  =========================

    A paste is about forty seconds of driving, so 159 against 114 is half an
    hour of somebody's evening. A small board splits into so few chunks that
    the budget never binds on it at all, and giving it a tight one only costs
    pastes. So the budget follows the board, and the failure it guards against
    is loud rather than silent -- an over-cap slab aborts the build with the
    flag to change named in the message.
    """
    area = float(tm.width * tm.depth)
    if area <= DETAIL_SMALL_TILES:
        return BUDGET_SMALL_BOARD
    if area >= DETAIL_LARGE_TILES:
        return BUDGET_LARGE_BOARD
    span = DETAIL_LARGE_TILES - DETAIL_SMALL_TILES
    t = (DETAIL_LARGE_TILES - area) / span
    return int(round(BUDGET_LARGE_BOARD + t * (BUDGET_SMALL_BOARD - BUDGET_LARGE_BOARD)))


def detail_scale(tm) -> float:
    """How much human dressing a board of this size can afford.

    **The budgets that bind are per board, so the room to spend is a function
    of board area and nothing else.** Measured on the three towns:
    Pelvesthollow is 32k tiles and spends 2.1% of the per-board asset limit,
    with its largest slab at 14,112 of 30,720 bytes; Graybank 133k tiles and
    9.4%; East Tradebourne 442k tiles, 41.1%, and a largest slab at *99.4%* of
    the cap. A single dressing rate for all three therefore leaves the small
    boards nearly empty while the large one has no headroom at all -- which is
    the wrong way round, because the small board is the one a party actually
    stands on and looks at.

    Interpolated rather than stepped, so a town near the boundary does not
    change character on one extra field.

    **It scales the HUMAN dressing only** -- market goods, lane and yard
    clutter, the barrels against a street wall, the wheat and straw in a
    worked field. Not the trees, stumps and ferns: woodland density already
    comes from the canopy field, which closes into stands and opens into
    glades, and doubling it would turn a hamlet's pasture into thicket. The
    thing a small board is short of is evidence of *people*.
    """
    area = float(tm.width * tm.depth)
    if area <= DETAIL_SMALL_TILES:
        return DETAIL_MAX_SCALE
    if area >= DETAIL_LARGE_TILES:
        return 1.0
    span = DETAIL_LARGE_TILES - DETAIL_SMALL_TILES
    t = (DETAIL_LARGE_TILES - area) / span
    return 1.0 + t * (DETAIL_MAX_SCALE - 1.0)

#: Kept clear of the building's own wall, so nothing stands in a doorway.
YARD_CLUTTER_CLEARANCE = 1


def _dress_yards(b: Builder, tm, scatter: "Scatter", rng, grade: float,
                 taper: dict[tuple[int, int], float | None]) -> int:
    """Put the working life of a trade into its own yard.

    Returns the number of props placed.
    """
    yards = yard_cells(tm)
    if not yards:
        return 0

    # A yard is the clearest evidence of somebody's working life, so it is
    # exactly what a small board has budget to say more of. Capped short of
    # certainty: a yard packed edge to edge is a junkyard.
    rate = min(0.5, YARD_CLUTTER_RATE * detail_scale(tm))
    placed = 0
    for bid, cells in sorted(yards.items()):
        kinds = YARD_CLUTTER.get(bid.split("-")[0], DEFAULT_YARD_CLUTTER)
        for x, z in sorted(cells):
            # A prop against the wall blocks the door it might be standing in.
            if any(tm.inside(x + dx, z + dz) and tm.building[z + dz][x + dx]
                   for dx in range(-YARD_CLUTTER_CLEARANCE, YARD_CLUTTER_CLEARANCE + 1)
                   for dz in range(-YARD_CLUTTER_CLEARANCE, YARD_CLUTTER_CLEARANCE + 1)):
                continue
            if rng.random() > rate:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue
            category = kinds[rng.randrange(len(kinds))]
            # **Through the scatter, not `b.prop`.** This pass was handed a
            # `scatter` and then placed with `b.prop` anyway, which does no
            # collision test at all -- so yard clutter was dropped straight
            # through the yard's own fence. Beds, crates and straw against
            # `Wooden Fence` were the largest group of real overlaps left on
            # Pelvesthollow after the collision test itself was corrected.
            asset = b.palette.prop(category, b.rng)
            if asset is None:
                continue
            if scatter.one(asset, x + 0.5 + rng.uniform(-0.25, 0.25),
                           z + 0.5 + rng.uniform(-0.25, 0.25),
                           grade - drop, rng.randrange(24)):
                placed += 1
    return placed


def _lay_quays(b: Builder, tm, grade: float,
               taper: dict[tuple[int, int], float | None]) -> None:
    """Rail the edge where paved ground meets water.

    The shingle shore only forms on soft ground -- a street is not going to
    become gravel -- so a road running along the bank simply stopped at a
    half-tile cliff over the river, which is most of why the channel read as a
    trench cut through the town rather than as a river it was built beside.

    Piers are left open on purpose: a pier exists to get to the water.
    """
    from . import raster as R

    rail = b.palette.resolve("quay_rail")
    if rail is None:
        return
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] not in (R.STREET, R.PLAZA) or tm.building[z][x]:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue
            for side, dx, dz in SIDE_OFFSETS:
                nx, nz = x + dx, z + dz
                if (0 <= nx < tm.width and 0 <= nz < tm.depth
                        and tm.surface[nz][nx] == R.WATER):
                    b.add(place_wall(rail, x, z, side, grade - drop))


@dataclass(frozen=True)
class FenceStyle:
    """One way of building a field boundary.

    The geometry is fixed -- every style is the same run of 2-tile pieces along
    the same surveyed line -- so a style is entirely a question of which pieces
    and which joint policy. That is what makes them comparable on a board.
    """

    #: Palette role for the pieces along the run.
    panel: str
    #: Palette role for the joints, or None to leave the run bare.
    post: str | None = None
    #: Only joint a vertex whose turn exceeds this, in degrees. 0 posts every
    #: vertex; 30 posts only real corners; ends are always jointed when a post
    #: role is set.
    post_min_turn: float = 0.0
    #: Chance a panel is simply left out, for a boundary that has been standing
    #: a while.
    gap: float = 0.0
    #: Lateral wander either side of the line, in tiles, plus or minus one
    #: rotation step. A surveyed line built exactly is an extruded ribbon; a
    #: hedge in particular wants to have grown rather than been placed.
    jitter: float = 0.0
    #: Lay on the cell lattice rather than along the surveyed bearing.
    #:
    #: **Set by whether the pieces are tiles or props, and that is not a
    #: detail.** Every other style here resolves to a `kind="prop"` asset, and
    #: a prop is *allowed* off the lattice -- it stores its collider centre, so
    #: `verify`'s off-grid canary exempts it. The palisade kit is `kind="tile"`,
    #: and laying tiles along an arbitrary bearing put **166 of them off the
    #: half-tile grid** on the first build: a real FAIL, from the check that
    #: exists because one fractional overhang drags a whole board off the grid
    #: and breaks mini snapping.
    #:
    #: So a tile boundary stair-steps, and for this kit that is right rather
    #: than a compromise. `docs/fencing.md` §2.2 argues against stair-stepping
    #: because a thin panel run leaves daylight at every step -- the comb, the
    #: rank of fins. A palisade piece is a **full cell deep**, so there is no
    #: daylight to leave, which is the same reasoning `city_wall_core` is
    #: built on.
    on_cells: bool = False


#: The designs. Named so `--fence-style` reads as a choice about the map
#: rather than about the code.
FENCE_STYLES: dict[str, FenceStyle] = {
    # Drystone field wall, jointed at every vertex. The default, and what the
    # FTG `STONE_FENCE` edge type actually describes.
    "drystone": FenceStyle("field_wall", "field_wall_post"),
    # The same wall with nothing at the joints, to answer whether a post at a
    # 5-degree vertex reads as a gate post or as clutter. 48% of vertices turn
    # less than 5 degrees, so this is not a small question.
    "drystone-plain": FenceStyle("field_wall", None),
    # Posts only where the line really turns a corner.
    "drystone-corner": FenceStyle("field_wall", "field_wall_post",
                                  post_min_turn=30.0),
    # Heavier and 7 ft tall: an estate wall rather than a field one.
    "drystone-tall": FenceStyle("field_wall_tall", "field_wall_post"),
    # Timber paling, cornered from its own kit at hard turns only -- the piece
    # is an L and there is nothing else in that kit to joint with.
    "paling": FenceStyle("yard_fence", "yard_fence_corner", post_min_turn=45.0),
    # A barricade, and the only style here you cannot step over: the palisade
    # wall is **2.0 tall against paling's 0.68**. That gap is why a keep's
    # enclosure read as a garden fence on the board -- `paling` was the
    # tallest timber the palette had, and nothing had measured it. Corners are
    # a bundle of posts, so unlike the paling L they can take any turn.
    "palisade": FenceStyle("palisade_wall", "palisade_corner",
                           post_min_turn=60.0, on_cells=True),
    # A hedge built exactly like the wall, to see whether a living boundary
    # survives being laid on a survey line.
    "hedge": FenceStyle("field_hedge", None),
    # The same hedge with gaps and wander. If the regular one reads as extruded
    # green plastic, this is why.
    "hedgerow": FenceStyle("field_hedge", None, gap=0.10, jitter=0.30),
}

#: Default when nothing asks for another.
DEFAULT_FENCE_STYLE = "drystone"

#: What a CLOSED run is built from, whatever the field walls are.
#: A perimeter round a property is a barricade and a field wall is a
#: field wall; see `_lay_fences`.
DEFAULT_ENCLOSURE_STYLE = "palisade"


def _is_closed(run) -> bool:
    """Does this boundary run come back to where it started?

    The one test that separates a *perimeter* from a *field wall*, and it is
    the same test `raster.compounds` uses to decide what a property is.
    """
    if len(run) < 4:
        return False
    (x0, z0), (x1, z1) = run[0], run[-1]
    return abs(x0 - x1) <= 0.01 and abs(z0 - z1) <= 0.01


def _bearing_rot(cells: set, x: int, z: int, cx: float, cz: float) -> int:
    """Which of the 24 steps turns this piece along the run, braced side in.

    **A 1x1 tile may be turned to ANY of the 24 steps and stay on the
    half-tile lattice** -- `rotated_footprint` returns 1.000 x 1.000 at every
    step, so the min corner never moves. Fractional *position* is what breaks
    the off-grid canary; fractional *rotation* costs nothing. Those two were
    conflated, and the conflation is the whole reason a barricade was ever
    stair-stepped: the piece can simply be turned to follow the line.

    The direction is a LINE THROUGH the neighbours, not a vector sum. Summing
    offsets makes the two neighbours of any straight or diagonal run cancel
    exactly, which sends every cell to the axis fallback -- a version of this
    produced a probe with no 45 degree rotation in it at all, and was caught
    by counting rotations in the emitted slab rather than by looking.

    `rot=0` lays the stake plane east-west and the braced face south, so the
    bearing is the step directly; the half-turn that puts the bracing inside
    is chosen against the enclosure's centroid.
    """
    nb = [(nx, nz) for nx in (x - 1, x, x + 1) for nz in (z - 1, z, z + 1)
          if (nx, nz) in cells and (nx, nz) != (x, z)]
    if not nb:
        return 0
    if len(nb) >= 2:
        far = max(((a, b) for i, a in enumerate(nb) for b in nb[i + 1:]),
                  key=lambda ab: (ab[0][0] - ab[1][0]) ** 2 + (ab[0][1] - ab[1][1]) ** 2)
        dx, dz = far[0][0] - far[1][0], far[0][1] - far[1][1]
    else:
        dx, dz = nb[0][0] - x, nb[0][1] - z
    rot = int(round(math.degrees(math.atan2(dz, dx)) / 15.0)) % 12
    # The braced face belongs toward the middle of what is enclosed. The
    # plane's normal at `rot` points to rot+6; take the half turn whose
    # normal points away from the centroid.
    nx_, nz_ = math.cos(math.radians((rot + 6) * 15)), math.sin(math.radians((rot + 6) * 15))
    if (cx - x) * nx_ + (cz - z) * nz_ > 0:
        rot += 12
    return rot % 24


def _lay_palisade(b: Builder, tm, grade: float,
                  taper: dict[tuple[int, int], float | None],
                  scatter: "Scatter | None",
                  panel, post, paved: frozenset, runs,
                  min_turn: float = 0.0) -> int:
    """Lay a boundary of full-cell tiles on the cell lattice.

    The counterpart to the surveyed-line pass above, for a kit whose pieces
    are tiles rather than props. See `FenceStyle.on_cells` for why the two
    cannot share one placement rule: a prop may sit off the half-tile grid and
    a tile may not, and 166 palisade pieces failed the off-grid canary before
    this existed.

    One piece per cell of the stroked run, turned so its face is square to the
    run rather than to the world -- a stair-stepped line of full-cell pieces
    has no daylight in it, but a rank of pieces all facing north through a
    corner still reads as a mistake.

    **A STAIR-STEP IS NOT A CORNER, and reading it as one is what put two
    materials in a single run.** The first version asked whether a cell had
    both an east-west and a north-south neighbour on the run, and called that
    a turn. On a rasterised diagonal that is true at *every step*, so the
    shoulders of a ring came out speckled with round-log corner bundles
    between flat stake panels -- visibly two different materials on one
    barricade, and the defect this docstring exists to prevent recurring.
    Measured on Sedgewater: the source ring is a smooth 16-gon whose turns run
    1.2 to 53.9 degrees, **not one of them a real corner**, and 21 of its 116
    cells were built as corners anyway.

    So a corner is decided on the **source polyline**, where an angle actually
    exists, against ``min_turn`` -- the same policy `FenceStyle.post_min_turn`
    states for the surveyed pass. A smooth ring gets none and a square pen
    gets four, which is what each should have.
    """
    from . import raster as R

    laid = 0
    for run in runs:
        # **No diagonal connectors.** They exist to patch a stair-step, and
        # a run whose pieces follow the bearing does not stair-step. Probed
        # side by side on their own boards: connectors sit at quarter turns
        # while their neighbours sit at 45, so they stand out as T-junctions
        # and reintroduce exactly the artifact the rotation removes -- the
        # connectored specimen read WORSE than the plain one, at 23 pieces
        # against 16.
        on_run = {c for c in R._stroke_line(run, 1.0, tm.width, tm.depth)}
        if not on_run:
            continue

        # Real corners: source vertices whose turn is sharp enough to want a
        # piece of its own, mapped onto the cell they fall in.
        corners: set[tuple[int, int]] = set()
        if post is not None and min_turn > 0:
            pts = run[:-1] if _is_closed(run) else run
            n = len(pts)
            for i in range(n):
                if not _is_closed(run) and (i == 0 or i == n - 1):
                    continue
                a, c0 = pts[i - 1], pts[(i + 1) % n]
                bx, bz = pts[i]
                v1 = (bx - a[0], bz - a[1])
                v2 = (c0[0] - bx, c0[1] - bz)
                turn = abs(math.degrees(math.atan2(
                    v1[0] * v2[1] - v1[1] * v2[0],
                    v1[0] * v2[0] + v1[1] * v2[1])))
                if turn < min_turn:
                    continue
                # **Snap to the nearest cell actually on the run, rather than
                # truncating.** A vertex often lands exactly on a cell corner
                # -- (50.0, 56.0) is the meeting point of four -- and `int()`
                # then names one the stroke did not include, so the corner
                # silently became a wall panel. Three of a square pen's four
                # corners appeared; the fourth was this.
                best = min(on_run,
                           key=lambda c: (c[0] + 0.5 - bx) ** 2
                           + (c[1] + 0.5 - bz) ** 2)
                if (best[0] + 0.5 - bx) ** 2 + (best[1] + 0.5 - bz) ** 2 <= 2.0:
                    corners.add(best)
        # **Which way the bracing faces, and it is not cosmetic.** The panel
        # is directional -- pointed stakes on one face, diagonal bracing and a
        # walk on the other -- so a rank of them turned the wrong way reads as
        # scaffolding stood outside the wall, which is what the first run
        # looked like on the board. For a closed run, "the wrong way" has a
        # definition: the braced side belongs toward the middle of what is
        # being enclosed.
        cx = sum(p[0] for p in run) / len(run)
        cz = sum(p[1] for p in run) / len(run)

        for x, z in sorted(on_run):
            if not tm.inside(x, z):
                continue
            # The same three exemptions the surveyed pass makes: a boundary
            # never crosses a carriageway, stands in a building, or floats
            # where the border taper took the ground away.
            if tm.building[z][x] or tm.wall[z][x]:
                continue
            if tm.surface[z][x] in paved:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue

            piece = panel
            if (x, z) in corners and post is not None:
                piece = post                  # a real turn in the source line
                rot = 0
            else:
                rot = _bearing_rot(on_run, x, z, cx, cz)
            here = grade - drop
            b.add(place_tile(piece, x, z, here, rot))
            if scatter is not None:
                scatter.reserve(piece, x + 0.5, z + 0.5, here, rot)
            laid += 1

        laid += _hang_palisade_gate(b, tm, grade, taper, on_run, paved)
    return laid


#: The widest opening still read as a gateway rather than as a road the
#: boundary runs alongside. A carriageway is at most 4 tiles; 6 leaves room
#: for the stroke to clip a cell either side without swallowing a whole edge.
GATE_MAX_CELLS = 6


def _hang_palisade_gate(b: Builder, tm, grade: float,
                        taper: dict[tuple[int, int], float | None],
                        on_run: set, paved: frozenset) -> int:
    """Put a gate in the opening a road leaves through a palisade.

    **A barricade with a hole in it is not enclosed.** The ring skips its own
    cells wherever a carriageway crosses -- correctly, or the road would be
    walled off -- and on Sedgewater that leaves a three-cell, fifteen-foot gap
    with nothing whatever in it. This project has been here before: `CLAUDE.md`
    records the town gate standing open for eleven revisions with
    `city_gate_arch` pinned and unused.

    The piece is 2.00 x 2.75 x 0.50 -- two cells wide, and **taller than the
    2.0 wall it hangs in**, so the lintel stands proud the way a gate should.
    It is seated on a cell EDGE rather than centred in the cell: a 0.5-deep
    tile centred in a 1.0 cell puts its min corner at a quarter tile, which is
    precisely what the off-grid canary exists to catch.
    """
    gate = b.palette.resolve("palisade_gate")
    if gate is None:
        return 0

    # The opening: cells the run wanted but a road took.
    gap = {c for c in on_run
           if tm.inside(*c) and tm.surface[c[1]][c[0]] in paved}
    if len(gap) < 2:
        return 0

    # **Group the opening, and hang ONE gate in it.** Cell by cell this put
    # seventeen gates along a single boundary, because a road running *beside*
    # a fence paves every cell of that stretch and every pair of them looked
    # like a crossing. An opening is a contiguous run, a crossing is a SHORT
    # one, and a long paved stretch is a road the boundary happens to follow
    # -- which wants no gate at all.
    runs: list[list[tuple[int, int]]] = []
    seen: set = set()
    for cell in sorted(gap):
        if cell in seen:
            continue
        group = [cell]
        seen.add(cell)
        queue = [cell]
        while queue:
            x, z = queue.pop()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, z + dz)
                if n in gap and n not in seen:
                    seen.add(n)
                    group.append(n)
                    queue.append(n)
        runs.append(group)

    laid = 0
    for group in runs:
        if not 2 <= len(group) <= GATE_MAX_CELLS:
            continue
        xs = sorted({c[0] for c in group})
        zs = sorted({c[1] for c in group})
        mid = sorted(group)[len(group) // 2]
        x, z = mid
        if len(xs) >= len(zs):                       # the opening runs E-W
            cx, cz, rot = x + 0.0, z + 0.25, 0
        else:
            cx, cz, rot = x + 0.25, z + 0.0, 6
        drop = taper.get((x, z), 0.0)
        if drop is None:
            continue
        b.add(place_centered(gate, cx, cz, grade - drop, rot))
        laid += 1
    return laid


def _lay_fences(b: Builder, tm, grade: float,
                taper: dict[tuple[int, int], float | None],
                scatter: "Scatter | None" = None,
                style: str = DEFAULT_FENCE_STYLE,
                enclosure_style: str | None = DEFAULT_ENCLOSURE_STYLE) -> int:
    """Build the field boundaries along their surveyed lines.

    This is the one pass that does not work in cells, and `docs/fencing.md` §4
    is the argument for it: 97-100% of fence segments run off-axis, a fence is
    one piece thick with nothing behind it, and a stair-stepped run of thin
    pieces is the failure this project has already shipped three times.

    **It cannot go through `Scatter`, and that is measured rather than
    assumed.** `Scatter._clear` tests axis-aligned bounding boxes, and two
    2-tile panels butted end to end on any off-axis bearing overlap as boxes
    while their meshes are disjoint -- +0.29 on both axes at 45 degrees. Laid
    through the scatter, every second panel of every fence on the map would be
    dropped, silently, and reported only as a `rejected` count. The run knows
    its own panels are collinear and end to end, so it does its own
    bookkeeping; the boxes are then handed to the scatter so that *trees* still
    keep clear of the fence, which is the direction of the test that matters.

    Returns the number of pieces laid.
    """
    from . import raster as R

    spec = FENCE_STYLES.get(style)
    if spec is None:
        raise ValueError(f"unknown fence style {style!r}; "
                         f"expected one of {sorted(FENCE_STYLES)}")
    panel_asset = b.palette.resolve(spec.panel)
    if panel_asset is None:
        return 0
    post_asset = b.palette.resolve(spec.post) if spec.post else None

    # A fence across a carriageway is a line through the one thing the map
    # exists to let people walk down, so the road takes a gate out of the wall.
    #
    # **The gap is the road's own width, measured, not a fixed spread.** The
    # first rule here suppressed the panel on the paving plus one either side,
    # which is right for a boundary that crosses a road once and catastrophic
    # for one that runs beside it: section C of `tools/fence_sections.py` is a
    # 48-tile boundary grazing a winding road six times, and a three-panel
    # demolition per graze left 9 panels of 24 standing. Testing each panel at
    # its centre *and both ends* instead opens exactly as much wall as the
    # paving actually covers -- a wide road takes several panels, a graze takes
    # none -- and needs no number to tune.
    paved = frozenset({R.STREET, R.PLAZA, R.LANE, R.PIER})
    rng = random.Random(f"fences:{style}")
    laid = 0

    # **A closed run and an open run are different things, and one style
    # cannot serve both.** `--fence-style palisade` built the outlying farms'
    # field boundaries as ten-foot timber stockades: correct for the keep's
    # barricade, absurd across a wheat field, and visible from the air as a
    # fortification cutting through somebody's crop. The same closed-versus-
    # open test that decides what a property is decides what fences it.
    field_runs = [r for r in tm.fences if not _is_closed(r)]
    ring_runs = [r for r in tm.fences if _is_closed(r)]

    if spec.on_cells:
        # The chosen style is a cell-laid one, so it was asked for by name:
        # honour it on everything.
        return _lay_palisade(b, tm, grade, taper, scatter, panel_asset,
                             post_asset, paved, tm.fences, spec.post_min_turn)

    laid_rings = 0
    if ring_runs and enclosure_style and enclosure_style != style:
        ring_spec = FENCE_STYLES.get(enclosure_style)
        if ring_spec is not None:
            ring_panel = b.palette.resolve(ring_spec.panel)
            ring_post = b.palette.resolve(ring_spec.post) if ring_spec.post else None
            if ring_panel is not None and ring_spec.on_cells:
                laid_rings = _lay_palisade(b, tm, grade, taper, scatter,
                                           ring_panel, ring_post, paved,
                                           ring_runs, ring_spec.post_min_turn)
                ring_runs = []

    for run in field_runs + ring_runs:
        panels, joints = run_along_polyline(run)
        if not panels:
            continue

        # A panel is out if any part of it stands on paving or in a building,
        # so the gap a road opens is as wide as the road.
        #
        # **Measured on the panel's BODY, and it used to be three points on
        # it** -- the centre and both ends, which was already a correction on
        # sampling the centre alone. Three points still cannot see a panel that
        # crosses the corner of a road cell between two of them, and on a
        # surveyed line at an arbitrary bearing that is the common case rather
        # than the rare one: 14 of Pelvesthollow's field-wall panels were
        # standing in a lane, and it took a check that measured the box to
        # find them. Third time this project has replaced a sample with an
        # extent, and the general form is in `CLAUDE.md`: metrics must read
        # the artifact.
        #
        # **Tested after the jitter, not before.** A hedgerow wanders up to
        # 0.30 tiles off its line and takes a rotation step either way, and the
        # old order tested the panel where it was *going* to be rather than
        # where it ended up.
        def obstructed(cx, cz, rot, piece=panel_asset):
            for x, z in covered_cells(piece, cx, cz, rot):
                if (not tm.inside(x, z) or tm.surface[z][x] in paved
                        or tm.building[z][x]):
                    return True
            return False

        for i, (cx, cz, rot) in enumerate(panels):
            if spec.gap and rng.random() < spec.gap:
                continue
            if spec.jitter:
                cx += rng.uniform(-spec.jitter, spec.jitter)
                cz += rng.uniform(-spec.jitter, spec.jitter)
                rot = (rot + rng.choice((-1, 0, 0, 1))) % 24
            if obstructed(cx, cz, rot):
                continue
            here = _fence_ground(tm, taper, grade, cx, cz)
            if here is None:
                continue
            b.add(place_centered(panel_asset, cx, cz, here, rot), prop=True)
            laid += 1
            if scatter is not None:
                scatter.reserve(panel_asset, cx, cz, here, rot)

        if post_asset is None:
            continue
        for jx, jz, turn in joints:
            if turn < spec.post_min_turn:
                continue
            here = _fence_ground(tm, taper, grade, jx, jz)
            if here is None:
                continue
            x, z = int(math.floor(jx)), int(math.floor(jz))
            if not tm.inside(x, z) or tm.surface[z][x] in paved or tm.building[z][x]:
                continue
            b.add(place_centered(post_asset, jx, jz, here, rng.randrange(24)),
                  prop=True)
            laid += 1
            if scatter is not None:
                scatter.reserve(post_asset, jx, jz, here, 0)
    return laid + laid_rings


def _fence_ground(tm, taper: dict[tuple[int, int], float | None], grade: float,
                  cx: float, cz: float) -> float | None:
    """The height to stand a fence piece at, or None where nothing is built.

    Fences run out across open country, which is exactly where the edge taper
    drops the ground away -- so a fence that ignores it walks out over the
    falloff on stilts. Same rule every other landscape pass follows: read the
    taper, and where it is None the ground was never laid and nor is this.
    """
    x, z = int(math.floor(cx)), int(math.floor(cz))
    if not tm.inside(x, z):
        return None
    drop = taper.get((x, z), 0.0)
    if drop is None:
        return None
    return grade - drop


def _lay_bridges(b: Builder, tm, grade: float,
                 taper: dict[tuple[int, int], float | None]) -> int:
    """Deck and rail the planks MFCG draws across the river.

    A plank cell used to be laid as cobble at grade: a quarter-tile slab with
    a full tile of air beneath it, because the waterline sits a tile below the
    bank, and nothing under that -- the bed stopped either side of it. From
    the bank it was a paper-thin strip hanging over the channel. The river now
    runs on beneath the crossing (``_lay_terrain`` beds and fills plank cells
    as water) and the deck is a harbour tile a whole tile thick laid *by its
    top*, so its planking meets the bank flush and its underside rests on the
    water. Rails stand on every side that faces open water: a plank here is a
    bridge, not a mooring, and a bridge has a parapet.

    Returns the number of deck cells laid.
    """
    from . import raster as R

    deck_role = "bridge_deck" if b.palette.resolve("bridge_deck") is not None else "street"
    rail = b.palette.resolve("bridge_rail") or b.palette.resolve("quay_rail")
    laid = 0
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] != R.PIER:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue
            here = grade - drop
            b.surface(deck_role, x, z, here)
            laid += 1
            if rail is None:
                continue
            for side, dx, dz in SIDE_OFFSETS:
                nx, nz = x + dx, z + dz
                if (0 <= nx < tm.width and 0 <= nz < tm.depth
                        and tm.surface[nz][nx] == R.WATER):
                    b.add(place_wall(rail, x, z, side, here))
    return laid


#: How far either side of a tower to sample the wall band when working out
#: which way the curtain runs there, so a stair can be laid parallel to it.
WALL_STAIR_SAMPLE = 6

#: How far from a tread to look for the wall when scoring how close a flight
#: runs to it. Anything further off than this is equally bad.
WALL_STAIR_REACH = 4

#: How far the upper chimney course laps the lower one. A single 0.5-tall
#: piece sitting on the ridge reads as a stub; two lapped a quarter stand
#: proud of it and still leave no joint to see.
CHIMNEY_LAP = 0.25

def place_roof_piece(piece: Asset, tx: int, tz: int, course_y: float,
                     rot: int = 0, *, rise: float = 1.0) -> Placement:
    """Place a roof piece for the course whose top is at ``course_y``.

    **Which end of a piece seats at the course depends on the piece, and
    getting it wrong is invisible in the file.** A slope, a corner and a
    roof-and-chimney combination all seat by their BASE and rise above the
    course. A flat CAP seats by its TOP: it closes a ridge rather than
    climbing it, which is the rule `Builder.surface` follows for anything laid
    flat and the one CLAUDE.md states as "surface tiles align at the top, not
    the bottom".

    The test is the piece's own height against the course rise. A piece
    shorter than one rise is a lid; a piece a rise or more is a course. That
    holds across every kit measured -- 1x1 slopes and corners are 1.0 and 2x2
    ones are 2.0, while flat caps are 0.5 at both scales.

    **This exists because it was re-derived and got wrong twice in one file.**
    `tools/chimney_probe.py` seated a cap by its base and a combination by its
    top; the ridge stood half a tile proud and the chimney was buried, and a
    reviewer spent four rounds diagnosing an instrument rather than the town.
    Ten probe tools re-derive this today. This is meant to be the only copy.
    """
    seat = course_y - piece.size_y if piece.size_y < rise else course_y
    return place_tile(piece, tx, tz, seat, rot)


#: How far a roof-and-chimney COMBINATION sinks below the roof course it
#: replaces. Picked off a board (`PROBE chimney seated by base`, back row
#: middle): flush reads as a stack perched on the ridge, half a tile in reads
#: as buried, and a quarter is the one that looks like a flue coming through
#: thatch.
CHIMNEY_SINK = 0.25


def is_roof_chimney(asset) -> bool:
    """True when a chimney piece carries its own roof course.

    **The role it arrived in cannot answer this, because the two roles mean
    opposite things depending on the material.** Measured:

        thatch   roof_stack = `Thatched Roof Chimney`   (a combination)
                 roof_chimney = `Thatched Chimney`      (a bare stack)
        tile     roof_stack = `Chimney 01`              (a bare stack)
                 roof_chimney = `Village Roof Side/Chimney` (a combination)

    So a caller that trusts `roof_stack` to be free-standing lays a flat cap
    and stands a second roof course on it -- which is what put a thatch skirt
    round every chimney in the town, and what a reviewer called "chimney plus
    slant pieces instead of a lowered chimney".

    The kit's own word is the authority, the same rule `walls._role_of` uses
    where a collider cannot tell a wall from a roof: a chimney piece that says
    "roof" in its name brings one.
    """
    n = (asset.name or "").lower()
    return "chimney" in n and "roof" in n


def _bare_stack_courses(bare) -> tuple[float, ...]:
    """Y offsets for a free-standing stack, lapped if one course is a stub.

    `Thatched Chimney` is half a tile -- under three feet of flue, which the
    palette already records as reading like a stub. Two of them lapped a
    quarter make 0.75. A piece that is a tile or more stands on its own.
    """
    return (0.0,) if bare.size_y >= 1.0 else (0.0, CHIMNEY_LAP)


def chimney_pieces(chimney, stack):
    """``(combination, bare)`` from whatever the two roles resolved to."""
    got = [a for a in (chimney, stack) if a is not None]
    return (next((a for a in got if is_roof_chimney(a)), None),
            next((a for a in got if not is_roof_chimney(a)), None))


#: How far a free-standing stack's base sits BELOW the ridge top.
#:
#: **A chimney emerges from a roof; it does not stand on one.** Seated with its
#: base level with the ridge the whole 1.5-tall stack is proud -- 7.5 ft of
#: flue on a cottage, which is what reads as a chimney on a stick. Measured on
#: Forest Church before this: 25 of 49 chimneys sat at +0.00 against the local
#: ridge and 24 at -1.00, because the gabled path and the hip path carry the
#: same three branches independently and had drifted apart. The number matters
#: less than the fact that there is now one of it.
CHIMNEY_SEAT = 0.5


def _ridge_rotations(wing, rings, top_ring, chimney_at):
    """Which way each ridge cap faces, mirrored about the chimney.

    Ridge tiles are lapped from the ends of the ridge towards the stack, so
    the joints face away from the weather on both slopes. Along z that is
    rot 12 on one side and rot 0 on the other; along x, 18 and 6. With no
    chimney the mirror is the ridge's own midpoint, which comes to the same
    thing on a symmetrical plan and is at least consistent on an asymmetric
    one.
    """
    crown = [c for c in sorted(wing) if rings[c] == top_ring]
    if not crown:
        return {}
    xs = {c[0] for c in crown}
    zs = {c[1] for c in crown}
    along_z = len(zs) >= len(xs)
    axis = 1 if along_z else 0
    near, far = (12, 0) if along_z else (18, 6)
    pivot = (chimney_at[axis] if chimney_at is not None
             else (min(c[axis] for c in crown) + max(c[axis] for c in crown)) / 2.0)
    return {c: (near if c[axis] < pivot else far) for c in crown}


#: A wing needs this many cells along its ridge before a gable means anything.
#: Below it the "ridge" is a point or two and the end treatment has nothing to
#: terminate, so the wing stays hipped whatever its quarter deals.
GABLE_MIN_RIDGE = 4

#: ... and this many across, or there is no slope either side to step down.
GABLE_MIN_SPAN = 3


def crowstep_tread(palette, wall_asset):
    """The half-height panel of ``wall_asset``'s own kit, or ``None``.

    A crow-step is one cell in and one course up, so its tread is a wall piece
    exactly **1.0 tall** in the building's own fabric. **Only two medieval kits
    ship one** -- Castle Fortified and Marble Palace -- so a boarded barn or a
    timber-framed house asking for a crow-step gets ``None`` here and falls
    back to a flush gable, which is correct rather than a shortfall:
    crow-stepping is a masonry form.

    The kit is the folder, the same rule that found the facade's own corner.
    Broken and ruined variants are refused by name: `Abandoned Village` ships
    `haunted wall 1x1 broken` at exactly this size, and a broken wall makes a
    crow-step read as damage.
    """
    if wall_asset is None:
        return None
    kit = wall_asset.folder or ""
    best = None
    for a in getattr(palette.catalog, "assets", ()):
        if a.kind != "tile" or (a.folder or "") != kit:
            continue
        if round(a.size_y, 2) != 1.0:
            continue
        if "wall" not in (a.group_tag or "").lower():
            continue
        if min(a.size_x, a.size_z) > 0.6 or max(a.size_x, a.size_z) > 1.0:
            continue
        low = a.name.lower()
        if any(w in low for w in ("broken", "ruin", "window", "door")):
            continue
        if best is None or len(a.name) < len(best.name):
            best = a
    return best


#: Which palette role supplies each tier's exterior wall, for the crow-step
#: tread lookup. The tread has to come from the same kit as the wall or the
#: parapet is a different material from the gable it stands on.
_WALL_ROLE_BY_TIER = {"civic": "wall_civic", "utility": "wall_utility"}


def _tread_for(palette, tier: str, cache: dict):
    """`crowstep_tread` for a tier, memoised."""
    if tier not in cache:
        role = _WALL_ROLE_BY_TIER.get(tier, "wall")
        wall = palette.resolve(role) or palette.resolve("wall")
        cache[tier] = crowstep_tread(palette, wall)
    return cache[tier]


def gable_infill(palette, tier: str, tread=None, cap=None):
    """What closes the triangle between a gable's wall head and its roof.

    **A gable always needs one.** The hole is not an oversight in the geometry:
    a gable is exactly the case where the roof rises above the wall at the end
    of a building, and a hip has no triangle only because its boundary cells
    sit at the wall head. So "gable without infill" is not a thing that can be
    built, and the first wiring of this shipped a 1.5-tile hole at every flush
    gable end on East Tradebourne because it tried.

    Two answers, in order:

    * **The wall kit's own half-height panel**, where it has one. Castle
      Fortified's `castle wall 1x1 half` is the gable wall carried up, which is
      what a masonry gable actually is. This is also the crow-step tread, so
      civic gets one piece for both jobs.
    * **The roof kit's flat cap**, where it does not. `Tavern` and `Rural` ship
      no wall piece under two tiles -- only floors, roofs and stairs -- so the
      house and the barn cannot carry their wall up. Closing the verge in the
      *roof's* material instead is not a fallback dressed as a feature: tile
      hanging and wrapped thatch are both how a real gable of those materials
      is finished, and the cap is 0.5 tall so two of them make a course
      exactly.

    Returns ``None`` only if the tier has neither, in which case the caller
    must fall back to a hip -- a gable it cannot close is worse than a hip.
    """
    if tread is not None:
        return tread
    # **The cap the ROOF was dealt, not the one the tier usually wears.**
    # `roof_set(palette, tier)` with no bid resolves ROOF_BY_TIER, the tier
    # default, while `_lay_roofs` deals the material per BUILDING through
    # `roof_suffix_for` and `roof_override` -- so a verge was finished in
    # whatever the tier usually wears rather than in the roof above it.
    # Measured on East Tradebourne: 153 common houses dealt a tile roof got a
    # THATCH verge and 60 trade buildings dealt thatch got a tile one. The
    # caller has the dealt cap in hand two lines before it asks, so it passes
    # it; the lookup here is only the fallback for a caller that does not.
    if cap is None:
        cap = roof_set(palette, tier)[3]
    if cap is not None and (cap.size_x, cap.size_z) == (1.0, 1.0):
        return cap
    return None


#: Cells a double-course end piece spans ACROSS the slope, and the number of
#: single-course courses it therefore stands over. They are the same 2, and
#: that is the whole reason the two scales mix: the piece is 2 tiles tall and
#: 2 cells deep, so it covers exactly two 1x1x1 courses of the field beside it.
#: Measured off a hand-build the user made and handed over -- `docs/roofscape.md`
#: §8.2 -- after `docs/great-buildings.md` §3.1 had concluded from a ring flood
#: that the scales could not mix at all.
END_PIECE_CELLS = 2


def gable_end_piece(palette, side):
    """The 1-cell double-course end piece of ``side``'s own kit, or ``None``.

    A verge closed in another kit's material is the mismatch the tier system
    exists to prevent, so the kit is the folder -- the same rule that found the
    facade's corner and the crow-step's tread.

    **Only `Tavern` ships one**, which `docs/great-buildings.md` §3.4c already
    records from the other direction ("Tavern is the ONLY kit in the library
    with a roof end piece"). So a thatched or slated wing gets ``None`` here and
    falls back to a flush gable, exactly as `crow` falls back where the fabric
    ships no tread. That is a property of the library, not a shortfall in this
    function.

    The shape test is the piece's own geometry rather than its name: one cell
    along the ridge, two cells across the slope, and twice the field's rise.
    `Thatched Roof Wall` is tagged `end` in the same way and is 2 x 2 x 1 -- a
    verge board, not a double-course end -- and the collider is what tells them
    apart.
    """
    if side is None:
        return None
    kit = (side.folder or "").lower()
    want_y = round(side.size_y * END_PIECE_CELLS, 2)
    best = None
    for a in getattr(palette.catalog, "assets", ()):
        if a.kind != "tile" or (a.folder or "").lower() != kit:
            continue
        if "roof" not in (a.group_tag or "").lower():
            continue
        if "end" not in a.name.lower():
            continue
        if round(a.size_y, 2) != want_y:
            continue
        # One cell along the ridge, END_PIECE_CELLS across. The 2-cell partner
        # (`Village Roof Side End 02`) is deliberately not taken: a verge two
        # cells wide eats a cell of the field, and the field has to stay wide
        # enough to carry a ridge. `roof-end-wide-verge` is where that goes.
        span = (round(min(a.size_x, a.size_z), 2), round(max(a.size_x, a.size_z), 2))
        if span != (1.0, float(END_PIECE_CELLS)):
            continue
        if best is None or len(a.name) < len(best.name):
            best = a
    return best


def _end_pairs(verge, courses, across_i):
    """Pair a verge column's cells from the eaves inward, per slope half.

    Returns ``[(low_across, base_course, fall), ...]`` -- one entry per pair a
    double-course end piece can cover, and nothing for the cells it cannot.

    **Leftovers are left**, which is the point. A slope half with an odd number
    of cells, and the single ridge cell an odd-depth wing carries, have no whole
    piece that fits; they keep the flush treatment and the verge comes out
    part end-piece and part infill. That is the same rule `walls.pack` follows
    for a wide panel and `lay_flat_deck` for a 2x2 cap: lay the wide piece
    where all of its cells fit, and fill the remainder with the narrow one.
    Measured over the three towns, pairing covers both halves whole on 34% of
    gable-eligible wings and all but one ridge cell on a further 45%.
    """
    out = []
    for fall in ("n", "s", "e", "w"):
        half = [c for c in verge if courses[c][1] == fall]
        if not half:
            continue
        # From the eaves inward, which is the order the courses climb.
        half.sort(key=lambda c: courses[c][0])
        for i in range(0, len(half) - 1, 2):
            a, b = half[i], half[i + 1]
            if courses[b][0] - courses[a][0] != 1:
                break                      # not consecutive courses; stop
            if abs(a[across_i] - b[across_i]) != 1:
                break                      # not adjacent cells
            out.append((min(a[across_i], b[across_i]), courses[a][0], fall))
    return out


def _wing_gable(wing: set[tuple[int, int]],
                quarter_at: dict[tuple[int, int], str] | None,
                seed: int) -> str:
    """How this wing ends its ridge: ``hip``, ``flush`` or ``crow``."""
    if not quarter_at:
        return "hip"
    xs = [x for x, _ in wing]
    zs = [z for _, z in wing]
    w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    if max(w, d) < GABLE_MIN_RIDGE or min(w, d) < GABLE_MIN_SPAN:
        return "hip"
    # The quarter of the wing's own low corner. One lookup per wing rather
    # than a vote, because a wing is small enough that its cells agree.
    quarter = quarter_at.get(min(wing))
    if quarter is None:
        return "hip"
    return gable_end_for(quarter, seed)


def _lay_gabled_wing(b: Builder, wing: set[tuple[int, int]], treatment: str,
                     roof_y: float, rise: float, side, cap,
                     edge_off: int, tread, chimney=None, infill=None,
                     end=None, stack=None) -> None:
    """One gabled wing: ridge along the long axis, ends per ``treatment``.

    ``crow`` carries the end wall one course proud of the roof and **owns the
    end column** -- the roof stops against it. Standing the parapet beside a
    roofed end column instead makes the roof rise between every pair of steps,
    and the staircase reads as detached lumps rather than one wall. Measured on
    `PROBE crow-step`; `docs/great-buildings.md` §3.4c.

    Falls back to ``flush`` when the fabric ships no tread, which is every kit
    but Castle Fortified and Marble Palace.
    """
    if treatment == "crow" and tread is None:
        treatment = "flush"
    # **A verge needs the piece to close it with.** Only `Tavern` ships a
    # double-course end, so a thatched or slated wing falls back to flush the
    # same way a timber one falls back from `crow`. See `gable_end_piece`.
    if treatment == "endmix" and end is None:
        treatment = "flush"

    xs = [x for x, _ in wing]
    zs = [z for _, z in wing]
    w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    axis = "x" if w >= d else "z"          # the ridge runs along the long side
    cpc = roof_course_cells(side) if side is not None else 1

    courses = roof_courses(wing, axis, cpc)
    anchors = roof_course_anchors(courses, axis, cpc)
    ends = ((min(xs), max(xs)) if axis == "x" else (min(zs), max(zs)))
    on_end = (lambda c: c[0] in ends) if axis == "x" else (lambda c: c[1] in ends)
    crow = treatment == "crow"

    # **The double-course verge.** Pair each verge column's cells from the
    # eaves inward and stand one end piece over every pair -- 2 cells across,
    # 2 courses tall, in the field's own kit. `covered` is the cells an end
    # piece owns, and they take no slope, no cap and no infill: the piece IS
    # the roof there and it closes its own triangle, which is what makes this
    # the only treatment that reads as a gable on a board (`PROBE roof mix`,
    # `docs/roofscape.md` §9).
    covered: set[tuple[int, int]] = set()
    if treatment == "endmix":
        across_i = 1 if axis == "x" else 0
        for e in ends:
            verge = [c for c in courses if (c[0] if axis == "x" else c[1]) == e]
            for low, base, fall in _end_pairs(verge, courses, across_i):
                a = e
                rot = (ROOF_EDGE_ROT[fall] + edge_off) % 24
                # **The footprint is read off the rotation, never inferred
                # from the fall.** The piece is 1 x 2, so which way round it
                # lands depends on whether the fall's rotation is an even
                # quarter turn -- and that depends on the KIT's own offset.
                # Tavern's is +6, which makes n and s both even and the piece
                # land 1 along the ridge by 2 across; at an offset of 0 the
                # same fall gives an odd turn, the piece lies ACROSS the
                # ridge, and its min corner lands half a tile outside the
                # wing. Caught by test_an_end_piece_never_overhangs_its_wing
                # before it reached a board. A kit whose end cannot face the
                # right way keeps the flush treatment for that verge rather
                # than being laid wrong -- the same "a gable it cannot close
                # is worse than a hip" rule, one step down.
                fw, fd = rotated_footprint(end, rot)
                want = ((1.0, float(END_PIECE_CELLS)) if axis == "x"
                        else (float(END_PIECE_CELLS), 1.0))
                if (round(fw, 2), round(fd, 2)) != want:
                    continue
                cx = (a + 0.5) if axis == "x" else (low + END_PIECE_CELLS / 2.0)
                cz = (low + END_PIECE_CELLS / 2.0) if axis == "x" else (a + 0.5)
                # `place_centered`, not `place_tile`: `place_tile` offsets by
                # the UNROTATED size, which lands a non-square piece off the
                # grid on an odd quarter turn.
                b.add(place_centered(end, cx, cz, roof_y + base * rise, rot))
                for k in range(END_PIECE_CELLS):
                    covered.add((a, low + k) if axis == "x" else (low + k, a))

    for cell, (course, fall) in sorted(anchors.items()):
        if cell in covered:
            continue
        if crow and on_end(cell):
            continue
        if side is not None:
            b.add(place_roof_piece(side, cell[0], cell[1],
                                   roof_y + course * rise,
                                   (ROOF_EDGE_ROT[fall] + edge_off) % 24,
                                   rise=rise))
    # **One chimney, on the ridge.** The hip path places one per building and
    # the first cut of this one placed none, which took East Tradebourne from
    # 1,578 chimneys to 40 -- a town losing 97%% of its chimneys is the most
    # visible thing on a roofscape, and no check would have caught it because
    # every chimney that remained was correct.
    chimney_at = None
    if chimney is not None:
        top = max(c for c, _ in courses.values())
        crown = [c for c in sorted(courses) if courses[c][0] == top
                 and not (crow and on_end(c)) and c not in covered]
        if crown:
            chimney_at = crown[len(crown) // 2]
            y = roof_y + top * rise
            fall = courses[chimney_at][1]
            # **The same two cases as the hip path.** A ridge cell caps and
            # takes the free-standing stack; a sloped cell already has its
            # slope from the anchors loop above, so the combination piece goes
            # over it at that slope's own rotation rather than at rot 0. See
            # `roof_stack`.
            combo, bare = chimney_pieces(chimney, stack)
            rot = (ROOF_EDGE_ROT[fall] + edge_off) % 24 if fall is not None else 0
            if combo is not None:
                # **It REPLACES the course, and it seats by its BASE.** A
                # combination is a roof piece with a stack on it, so laying a
                # cap under it puts two roof courses at the ridge, and seating
                # it by its top buries the stack in the thatch. Both were done
                # here. See `is_roof_chimney` and `CHIMNEY_SINK`.
                b.add(place_tile(combo, chimney_at[0], chimney_at[1],
                                 y - CHIMNEY_SINK, rot))
            elif bare is not None:
                if cap is not None:
                    b.add(place_tile(cap, chimney_at[0], chimney_at[1],
                                     y - cap.size_y))
                for dy in _bare_stack_courses(bare):
                    b.add(place_tile(bare, chimney_at[0], chimney_at[1],
                                     y - CHIMNEY_SEAT - dy))

    if cap is not None:
        for cell, (course, fall) in sorted(courses.items()):
            if fall is not None or (crow and on_end(cell)) or cell in covered:
                continue
            if cell == chimney_at:
                continue
            b.add(place_tile(cap, cell[0], cell[1],
                             roof_y + course * rise - cap.size_y))
    if not crow:
        # **The gable triangle.** Stack the infill from the wall head up to the
        # roof line at each across-position, so the end closes and steps with
        # the slope. Without it the end is a triangular hole up to
        # (span/2 x rise) tall -- measured at 1.5 tiles on guildhall-0001,
        # visible on the board as a void under every gable.
        if infill is not None:
            for cell, (course, fall) in sorted(courses.items()):
                if not on_end(cell) or cell in covered:
                    continue
                # Stop at the roof's UNDERSIDE, which is half a tile lower on
                # a capped ridge cell than on a sloped one -- a cap is seated
                # by its top (`Builder.surface`'s rule) and a slope by its
                # base. Filling to the course height regardless buries the
                # infill in the roof: +1,020 tile seams on East Tradebourne,
                # about five per gabled wing, every one of them a pair the
                # camera can shimmer between.
                head = roof_y + course * rise
                if fall is None and cap is not None:
                    head -= cap.size_y
                # **A thin panel goes on the wall line, a full cell fills the
                # cell.** `castle wall 1x1 half` is 1 x 1 x 0.5 -- a wall
                # piece -- and `place_tile` would sit it at the cell's corner
                # rather than on the gable face, half a tile inside the
                # building. The roof caps that close a timber verge ARE full
                # cells and want `place_tile`. Reading which off the collider
                # is the same rule `place_wall` itself follows.
                thin = min(infill.size_x, infill.size_z) < 1.0
                if axis == "x":
                    face = "w" if cell[0] == ends[0] else "e"
                else:
                    face = "n" if cell[1] == ends[0] else "s"
                for k in range(int((head - roof_y) / infill.size_y + 1e-6)):
                    y = roof_y + k * infill.size_y
                    if thin:
                        b.add(place_wall(infill, cell[0], cell[1], face, y))
                    else:
                        b.add(place_tile(infill, cell[0], cell[1], y))
        return

    for cell, (course, _fall) in sorted(courses.items()):
        if not on_end(cell):
            continue
        if axis == "x":
            face = "w" if cell[0] == ends[0] else "e"
        else:
            face = "n" if cell[1] == ends[0] else "s"
        for k in range(int(round((course + 1) * rise / tread.size_y))):
            b.add(place_wall(tread, cell[0], cell[1], face,
                             roof_y + k * tread.size_y))
            b.add(place_wall(tread, cell[0], cell[1],
                             {"w": "e", "e": "w", "n": "s", "s": "n"}[face],
                             roof_y + k * tread.size_y))


def _lay_roofs(b: Builder, tm, base_y: float, storey_h: float, max_floors: int,
               skip: set[tuple[int, int]] | None = None,
               roof_override: dict[str, str] | None = None,
               quarter_at: dict[tuple[int, int], str] | None = None,
               seed: int = 0) -> None:
    """Roof each block as concentric rings, the way hand-builders do.

    The convention here is not inferred from screenshots -- it is read out of
    a real community-built cottage (``library/cabin/small-forest-cottage``),
    decoded and measured. That build stacks a hip roof as rings: each course
    steps one cell in and one piece-height up, corners take the corner piece,
    straight runs take the slope, and the innermost ring is closed with a flat
    cap. Its rotations, identical across all three of its courses, are:

        edges    N=6   E=0   S=18  W=12
        corners  NW=12 NE=6  SW=18 SE=0

    which is a quarter turn off the wall convention -- exactly the error that
    made our slopes look mis-set. The Thatched kit is used because it is the
    one with a flat cap piece; the Village kit has none, which is why our
    ridges showed an open trough.
    """
    skip = skip or set()

    def _floors_at(bid: str) -> int:
        return storeys_of(tm, bid, max_floors)

    def _floors_cell(bid: str, x: int, z: int) -> int:
        return storeys_at(tm, bid, x, z, max_floors)

    # Roof units are connected blocks sharing a storey count, so a terrace
    # gets one roof rather than one per party wall.
    seen: set[tuple[int, int]] = set()
    blocks: list[tuple[int, set[tuple[int, int]]]] = []
    for z0 in range(tm.depth):
        for x0 in range(tm.width):
            bid = tm.building[z0][x0]
            if not bid or (x0, z0) in seen or (x0, z0) in skip:
                continue
            fl = _floors_cell(bid, x0, z0)
            comp: set[tuple[int, int]] = set()
            stack = [(x0, z0)]
            while stack:
                x, z = stack.pop()
                if (x, z) in seen or not (0 <= x < tm.width and 0 <= z < tm.depth):
                    continue
                nb = tm.building[z][x]
                if not nb or _floors_cell(nb, x, z) != fl or (x, z) in skip:
                    continue
                seen.add((x, z)); comp.add((x, z))
                stack += [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)]
            if comp:
                blocks.append((fl, comp))

    # Resolved per building, not once per tier -- see ROOF_MIX. Cached,
    # because a town is 989 buildings and a palette lookup is not free.
    _cache: dict[tuple[str, str], tuple] = {}
    _tread_cache: dict[str, object] = {}

    def sets_for(bid: str):
        tier = tier_of(bid)
        # **A derelict wall under a sound roof is worse than either.** The
        # roof is dealt independently of the wall on purpose -- ROOF_MIX
        # exists because a per-tier constant makes a whole quarter monochrome
        # -- but a building clad in the `poor` fabric is authored damaged, and
        # the pack ships the matching roof: `roof_side_slate` resolves to
        # `Haunted roof 1x1`, the same Abandoned Village kit as the walls.
        # This is the one case where the roof follows the fabric.
        forced = (roof_override or {}).get(bid)
        key = (tier, forced if forced is not None else roof_suffix_for(tier, bid))
        if key not in _cache:
            _cache[key] = (roof_set_named(b.palette, key[1])
                           if forced is not None
                           else roof_set(b.palette, tier, bid))
        return _cache[key]

    for fl, cells in sorted(blocks, key=lambda t: min(t[1])):
        b.group = tm.building[min(cells)[1]][min(cells)[0]]
        # The material follows the block's owning building, which is the same
        # id the group is tagged with. A terrace shares one roof, so a block
        # spanning two tiers takes the first one's -- deliberately, because a
        # roof that changes material mid-slope is worse than one that does not
        # match its neighbour.
        side, corner, inner, cap, chimney = sets_for(b.group)
        edge_off, corner_off = roof_offsets(side)
        rise = side.size_y if side is not None else 1.0
        roof_y = base_y + fl * storey_h
        # The crow-step tread comes from the building's OWN wall kit, so a
        # boarded barn cannot end up with a dressed-stone parapet. Resolved
        # per block and cached, because a town is 989 buildings.
        tread = _tread_for(b.palette, tier_of(b.group), _tread_cache)
        infill = gable_infill(b.palette, tier_of(b.group), tread, cap)
        # The double-course end comes from the ROOF's kit, not the tier's, so
        # it follows the material this block was actually dealt. That is the
        # bug `gable-infill-follows-the-tier-not-the-roof` records against
        # `infill` one line up, not repeated here.
        end = gable_end_piece(b.palette, side)
        # The free-standing stack for this block's own material -- see
        # `roof_stack`. Keyed on the suffix the block was dealt, not the tier,
        # so it cannot repeat `gable-infill-follows-the-tier-not-the-roof`.
        forced_suffix = (roof_override or {}).get(b.group)
        stack = roof_stack(b.palette, forced_suffix if forced_suffix is not None
                           else roof_suffix_for(tier_of(b.group), b.group))

        # One hip per rectangular wing, not one hip forced over the whole
        # plan. A notched footprint gets a ridge per wing and a valley where
        # they meet, which is what the building would really have and what
        # this kit of 1x1 slopes and corners can actually express.
        wings = roof_wings(cells)
        chimney_wing = max(wings, key=len) if wings else set()

        for wing in wings:
            # **How this wing ends its ridge, dealt by QUARTER.** `quarter_at`
            # is None on a settlement whose kinds do not cluster, which is
            # most of them -- and then there is nothing to key on and the wing
            # is hipped exactly as it always was. That is the honest fallback
            # rather than a degradation: see `citysmith/quarters.py`.
            treatment = _wing_gable(wing, quarter_at, seed)
            # **A gable it cannot CLOSE is worse than a hip.** The end column's
            # wall stops at the wall head and the roof climbs away from it, so
            # a gable leaves a triangular hole up to (span/2 x rise) tall at
            # each end unless something fills it. `tread` is that something --
            # a wall piece exactly one course tall -- and it doubles as the
            # crow parapet, which is why crow is self-closing.
            #
            # **Tavern and Rural ship no such piece**, so the house and the
            # barn cannot gable at the single-course scale at all. That is the
            # same shortfall as "only Tavern ships a roof `end`" arriving from
            # the other side, and it is why `gable-single-course-infill` is
            # open: the double-course family HAS a matching 2.0 infill
            # (`Village Roof Side Wall`), so the fix is a scale change rather
            # than a hunt for a piece. Measured on the board first --
            # guildhall-0001 had a 1.5-tile hole at its ridge end.
            # A gable it cannot CLOSE is worse than a hip -- see
            # `gable_infill`, which is why this is a gate and not a warning.
            if treatment == "crow" and tread is None:
                treatment = "flush"
            if treatment != "hip" and infill is None:
                treatment = "hip"
            if treatment != "hip":
                _lay_gabled_wing(b, wing, treatment, roof_y, rise, side, cap,
                                 edge_off, tread,
                                 chimney if wing is chimney_wing else None,
                                 infill, end, stack)
                continue

            rings = _roof_rings(wing)
            top_ring = max(rings.values())

            # One chimney per building, on its main wing.
            chimney_at = None
            if chimney is not None and wing is chimney_wing:
                crown = [c for c in sorted(wing) if rings[c] == top_ring]
                if crown:
                    chimney_at = crown[len(crown) // 2]

            # **The last course is a ridge cap, not another ring.** Stepping
            # the top ring up a full rise and roofing it in slopes leaves
            # their undersides on show along the apex -- the bare timber that
            # showed at the top of every slate roof. A ridge is capped, and
            # the cap is seated so its *top* is flush with the ring height,
            # which is the same rule `Builder.surface()` follows for anything
            # laid flat. Read off a hand-built correction to one of these
            # roofs: the caps sat at 0.5 where the ring would have been 1.0.
            ridge_rot = _ridge_rotations(wing, rings, top_ring, chimney_at)

            for (x, z) in sorted(wing):
                r = rings[(x, z)]
                y = roof_y + r * rise
                # Which way the slope falls: the sides where the roof steps
                # back down towards this wing's own eaves.
                fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                             if rings.get((x + dx, z + dz), -1) < r)
                if (x, z) == chimney_at and chimney is not None:
                    # **Which chimney piece, and which way round.** The tile
                    # kit's `roof_chimney` is a COMBINATION -- a slope with a
                    # stack on it -- so on a sloped cell it doubles as the roof
                    # and must take that cell's own rotation, while on a capped
                    # ridge it stands a bare slope on end beside the flue. The
                    # free-standing `stack` is what a cap wants. Both cases are
                    # in the user's hand-build: it lays the combination over an
                    # ordinary slope at the slope's rotation, never on a ridge
                    # and never at rot 0 regardless.
                    sloped = roof_top_is_supported(rings, x, z, fall) or r < top_ring
                    combo, bare = chimney_pieces(chimney, stack)
                    rot = ((ROOF_EDGE_ROT[fall[0]] + edge_off) % 24
                           if sloped and len(fall) == 1 else 0)
                    if combo is not None:
                        # Replaces the course, seats by its base. The slope
                        # that used to be laid underneath is gone with it: a
                        # combination IS the slope.
                        b.add(place_tile(combo, x, z, y - CHIMNEY_SINK, rot))
                    elif bare is not None:
                        if cap is not None:
                            b.add(place_tile(cap, x, z, y - cap.size_y,
                                             ridge_rot.get((x, z), 0)))
                        for dy in _bare_stack_courses(bare):
                            b.add(place_tile(bare, x, z,
                                             y - CHIMNEY_SEAT - dy))
                    continue
                # Cap the top ring only where a slope there would have nothing
                # to lean on. See `roof_top_is_supported` -- capping the whole
                # ring is what put a 4 x 2 flat deck on every 6 x 4 wing.
                if (r == top_ring and cap is not None
                        and not roof_top_is_supported(rings, x, z, fall)):
                    b.add(place_tile(cap, x, z, y - cap.size_y,
                                     ridge_rot.get((x, z), 0)))
                    continue
                piece, rot = _roof_piece(fall, side, corner, cap, inner,
                                         _is_reflex(rings, x, z, fall),
                                         edge_off, corner_off)
                if piece is not None:
                    b.add(place_tile(piece, x, z, y, rot))


#: Headroom to leave under a gate lintel, in tiles. Two tiles (10 ft) was the
#: minimum a loaded cart with a rider on top clears, and it looked like it:
#: a 25 ft-wide mouth 10 ft high reads as a culvert, not a gate.
#:
#: Four tiles is 20 ft, and the number is set by the *door* rather than by
#: taste: `Door - Portcullis double` is 4 x 3.75 x 0.5, so a 15 ft opening
#: cannot take one -- it would drive three quarters of a tile up into the
#: lintel. At four the grille clears by a quarter tile and the wall still
#: carries two courses over the road.
GATE_HEADROOM_TILES = 4.0

#: Extra courses on the wall flanking a gate. Without them the curtain runs
#: over the opening at its ordinary height and nothing on the board says a
#: gate is there -- the tunnel mouth alone reads as damage rather than as an
#: entrance. Two courses is enough to be unmistakable from across the map.
GATEHOUSE_RISE = 2


def _corners_affordable(cells: set[tuple[int, int]]) -> bool:
    """Whether a footprint can spare a whole tile for each outside corner.

    Corner pieces fill their cell. On a 2x3 cottage the four corners are four
    of six cells, leaving two -- not a room. See :data:`MIN_USABLE_INTERIOR`.
    """
    xs = [x for x, _ in cells]
    zs = [z for _, z in cells]
    corners = sum(1 for (x, z) in cells
                  if x in (min(xs), max(xs)) and z in (min(zs), max(zs)))
    return (len(cells) - corners) >= MIN_USABLE_INTERIOR


#: How tall the rampart stands, in tiles. Five feet to a tile, so this is a
#: thirty-foot wall -- what the circuit has been all along, but it used to be
#: expressed as "three courses" and a course is however tall the block happens
#: to be. Swapping a 2.0 block for a 2.5 one would then have raised the whole
#: circuit by a quarter without anyone asking for it. The height is the
#: decision; the course count is derived from whatever block the palette gives.
TOWN_WALL_TILES = 6.0

#: A mural tower's footprint, in tiles a side, and how many courses it stands
#: above the curtain. Four cells is the rampart's own thickness, so a tower
#: set on the wall line protrudes a little either side instead of swelling
#: into a keep; two courses is enough to read as a tower from across the map
#: without dwarfing the houses it guards.
#:
#: The towers are square and built from the rampart's own block on purpose.
#: The kit's round-tower pieces (``md_tower_*``) are *quadrants* of an
#: eight-tile drum -- `tools/tower_probe.py` stacked them and got quarter
#: shells and fan-shaped floors -- and a 40 ft drum on a 20 ft rampart is a
#: castle, not a town wall. Their stone does not match the block either.
WALL_TOWER_TILES = 4
WALL_TOWER_RISE = 2


def _components8(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Split cells into 8-connected groups, in a stable order."""
    left = set(cells)
    out: list[set[tuple[int, int]]] = []
    for start in sorted(cells):
        if start not in left:
            continue
        group: set[tuple[int, int]] = set()
        stack = [start]
        while stack:
            c = stack.pop()
            if c not in left:
                continue
            left.discard(c)
            group.add(c)
            stack += [(c[0] + dx, c[1] + dz)
                      for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                      if (dx or dz) and (c[0] + dx, c[1] + dz) in left]
        out.append(group)
    return out


def pick_wall_towers(tm, mass: set[tuple[int, int]], gates: set[tuple[int, int]],
                     size: int = WALL_TOWER_TILES) -> list[frozenset[tuple[int, int]]]:
    """Square footprints for the circuit's towers: a pair flanking every gate,
    one on every corner of the ring.

    A gate's towers stand on its *jambs* -- the wall cells either side of the
    opening, which ``_gatehouse_cells`` finds and the passage splits in two.
    A corner's tower stands on the wall cell nearest the ring's vertex, which
    the raster records in ``tm.wall_corners`` because the band of cells has
    no memory of where the polygon turned. A vertex inside a gate's reach is
    the gate itself (MFCG puts the road through the corner here) and gets no
    third tower.

    A footprint is the ``size``-square box that covers the most wall while
    touching its seed, never a gate cell, a building, or anything paved or
    wet -- a tower may stand on open ground beside the wall, not in the
    street it guards -- and never another tower. A seed with no such box
    simply gets none.
    """
    from . import raster as R

    blocked = {R.STREET, R.LANE, R.PLAZA, R.FLOOR, R.WATER, R.MARSH, R.PIER, R.VOID}

    def usable(cell: tuple[int, int]) -> bool:
        x, z = cell
        if not (0 <= x < tm.width and 0 <= z < tm.depth):
            return False
        if cell in gates or tm.building[z][x]:
            return False
        return tm.wall[z][x] or tm.surface[z][x] not in blocked

    seeds: list[set[tuple[int, int]]] = []
    for jamb in _components8(_gatehouse_cells(mass, gates)):
        seeds.append(jamb)
    reach = {(gx + dx, gz + dz) for gx, gz in gates
             for dx in range(-size, size + 1) for dz in range(-size, size + 1)}
    wall_only = mass - gates
    for cx, cz in getattr(tm, "wall_corners", ()):
        if not wall_only:
            break
        nearest = min(wall_only, key=lambda c: ((c[0] - cx) ** 2 + (c[1] - cz) ** 2, c))
        if nearest in reach:
            continue
        seeds.append({nearest})

    towers: list[frozenset[tuple[int, int]]] = []
    taken: set[tuple[int, int]] = set()
    for seed in seeds:
        xs = [c[0] for c in seed]
        zs = [c[1] for c in seed]
        scx = sum(xs) / len(xs)
        scz = sum(zs) / len(zs)
        best: tuple[tuple[int, float], frozenset[tuple[int, int]]] | None = None
        for x0 in range(min(xs) - size + 1, max(xs) + 1):
            for z0 in range(min(zs) - size + 1, max(zs) + 1):
                box = frozenset((x, z) for x in range(x0, x0 + size)
                                for z in range(z0, z0 + size))
                if not box.isdisjoint(taken) or not (box & seed):
                    continue
                if not all(usable(c) for c in box):
                    continue
                off = (x0 + size / 2 - scx) ** 2 + (z0 + size / 2 - scz) ** 2
                score = (len(box & mass), -off)
                if best is None or score > best[0]:
                    best = (score, box)
        if best is not None:
            towers.append(best[1])
            taken |= best[1]
    return towers


def _lay_town_wall(b: Builder, tm, facing, top: float,
                   wall_tiles: float = TOWN_WALL_TILES) -> None:
    """Build the town wall as a faced rampart, carried over its gates.

    **The mass.** The raster gives the wall as a band of cells several thick --
    a rampart, not a fence. The castle kit's wall pieces are 0.5 deep because
    they are curtain wall, authored to stand *on* a cell boundary; laying one
    per cell left a 0.5-tile slot between every pair of cells across the band,
    so the whole circuit was daylight-striped. The mass is therefore a
    full-cell block.

    It was faced with the thin pieces for a while, and they did nothing: a
    facing inset into its own cell sits entirely *inside* the block that fills
    that cell. The interpenetration check found 928 of them buried, one whole
    cubic tile of overlap each -- 928 assets nobody could ever see, on a map
    already at its byte ceiling. The block's own face is what shows, and always
    was.

    **The gates.** A gate is where a main street crosses, so its cells are as
    wide as the carriageway -- eighteen of them on the Forest Church map. They
    used to be skipped and nothing took their place: a 35 ft breach open to the
    sky. A gate is a *tunnel*. The passage courses stay clear so carts still
    get through and every course above is built like the rest of the wall, so
    the rampart runs unbroken over the road. A wall too low to give both
    headroom and a lintel course keeps an open gate rather than a blocked one.

    **The walk.** Battlements crown the cells that actually face out of town,
    found by flooding the map from its border. Capping every cell with an
    exposed side instead put a merlon on 61% of the mass; because the circuit
    is a stair-stepped diagonal, those teeth pointed in every direction at
    once and the rampart read as a comb. The cells behind the parapet are
    paved instead, which is what makes the top of the wall a wall-walk.

    **The towers.** A pair on the jambs of every gate and one on every corner
    of the ring (:func:`pick_wall_towers`), built from the same block two
    courses higher, paved and battlemented on all four sides. Without them
    the circuit was one unbroken band, and the gate -- which MFCG puts at a
    corner of this ring, so the opening is a missing corner rather than a
    tunnel -- read as damage. Flanked, it reads as an entrance.
    """
    core = b.palette.resolve("city_wall_core") or facing
    cap_asset = b.palette.resolve("city_wall_cap")
    walk = b.palette.resolve("city_wall_walk") or b.palette.resolve("street")
    course = core.size_y
    wall_height = max(1, round(wall_tiles / course)) if course > 0 else 1

    clear = math.ceil(GATE_HEADROOM_TILES / course) if course > 0 else wall_height
    lintel_from = clear if clear < wall_height else None

    # Gate cells are *not* flagged in ``tm.wall`` -- the raster clears the flag
    # where a street crosses. They are still part of the mass, or the lintel
    # has nowhere to sit and the breach stays open.
    gates = set(tm.gates)
    mass = {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x]} | gates
    towers = pick_wall_towers(tm, mass, gates)
    tower_cells = {c for t in towers for c in t}
    # The towers count as circuit when deciding what is outside it. A flood
    # that ran through a tower's footprint would put a merlon on every wall
    # cell facing it, buried against the tower's flank.
    outside = _outside_the_wall(tm, mass | tower_cells)

    # A gate nothing could flank -- a crop that cut its jambs off, a ring too
    # cramped for a footprint -- keeps the old rise on its jamb cells, so the
    # opening still reads as an entrance rather than as damage.
    ring = _gatehouse_cells(mass, gates)
    if any(t & ring for t in towers):
        ring = set()

    def crown_cell(x: int, z: int, crown: float, lips: list[str]) -> None:
        """Pave the top of a cell and stand a battlement on each lip."""
        if lips and cap_asset is not None:
            if is_curtain_piece(cap_asset) and walk is not None:
                # A parapet stands on the lip, not in place of the walk: pave
                # the cell first and stand the battlement on its outer edge.
                # A cell at a step of the stair looks out on two sides, and
                # both get one -- that is what closes the corner.
                b.add(place_tile(walk, x, z, crown))
                for side in lips:
                    b.add(place_wall(cap_asset, x, z, side, crown + walk.size_y))
            else:
                b.add(place_tile(cap_asset, x, z, crown))
        elif walk is not None:
            b.add(place_tile(walk, x, z, crown))

    # **The rampart is built solid, and the 495 blocks that would save are not
    # worth it.** 38% of the body cells have no face anyone can see, so their
    # lower courses were dropped for a while. It reads fine -- the faces seal
    # the void -- but it empties exactly the cells `verify.town_wall_gaps`
    # samples, and that check cannot tell a sealed void from daylight straight
    # through the circuit. It exists because see-through wall shipped once
    # already, on 1,234 tiles. A 1.8% asset saving is not worth blinding it,
    # and a hollow core is a trap for whoever next cuts a postern through.
    for (x, z) in sorted(mass - tower_cells):
        gate = (x, z) in gates
        if gate and lintel_from is None:
            continue
        courses = wall_height + (GATEHOUSE_RISE if (x, z) in ring else 0)
        for level in range(lintel_from if gate else 0, courses):
            b.add(place_tile(core, x, z, top + level * course))
        crown_cell(x, z, top + courses * course,
                   [s for s, dx, dz in SIDE_OFFSETS if (x + dx, z + dz) in outside])

    # The towers: the same block and the same parapet, two courses higher.
    # Above the curtain a tower is exposed on all four faces, so every side
    # not shared with the rest of its footprint is a lip and gets a merlon;
    # the curtain's own walk runs into the tower's flank below.
    courses = wall_height + WALL_TOWER_RISE
    for footprint in towers:
        for (x, z) in sorted(footprint):
            for level in range(courses):
                b.add(place_tile(core, x, z, top + level * course))
            crown_cell(x, z, top + courses * course,
                       [s for s, dx, dz in SIDE_OFFSETS
                        if (x + dx, z + dz) not in footprint])

    _lay_wall_stairs(b, tm, towers, mass, outside, top, course, wall_height)
    _hang_portcullises(b, tm, gates, mass, top)


def _cells_near(cx: float, cz: float, r: int, tm) -> list[tuple[int, int]]:
    """Every cell of the map within ``r`` of a point, in a stable order."""
    return [(x, z)
            for z in range(max(0, int(cz - r)), min(tm.depth, int(cz + r) + 1))
            for x in range(max(0, int(cx - r)), min(tm.width, int(cx + r) + 1))]


def _lay_wall_stairs(b: Builder, tm, towers, mass, outside, top: float,
                     course: float, wall_height: int) -> int:
    """A flight up the inside of the wall at every tower.

    **Nothing could get onto the wall-walk.** The circuit carries 341 cells of
    paved, battlemented rampart 35 ft above the street and had no stairs, no
    ramp and no ladder anywhere on it -- a defenders' platform no defender
    could reach, on a board whose whole point is that a party stands on it.
    `verify` did not catch it either, because its access check asks whether
    *buildings* can be entered, not whether the wall can.

    Three things about where a flight goes, and each was wrong once:

    * **Inside, always.** A stair on the field side of a town wall is a siege
      ramp for the enemy, which is the one thing it must not be. This started
      as a *preference* in the scoring, and a preference is not good enough:
      on Forest Church one tower had no inside option at all under the old
      scheme, so it scored the field side and built there. A tower that cannot
      be served from inside now gets no flight, and the count is reported.
    * **Parallel to the wall.** The run used to march straight out from the
      tower's face into the town, which hugged the curtain for one cell of six
      and ate 35 ft of street. A rampart stair runs *along* the inner face --
      it is how a real one is built, it keeps the street, and the flight has
      the wall at its shoulder the whole way up.
    * **Climbing towards the tower**, so the top tread lands against the
      tower's flank and you step onto the walk rather than off the end into
      air. A `city_wall_walk` tile caps the top tread so the landing is flush
      with the rampart instead of half a tile below it.

    The flight is filled solid underneath -- a stair tile is a tread, not a
    stringer, and a run of them hanging over air reads as a folded ribbon.
    """
    stair = b.palette.resolve("city_wall_stair")
    core = b.palette.resolve("city_wall_core")
    walk = b.palette.resolve("city_wall_walk")
    if stair is None or core is None or wall_height < 1:
        return 0

    from . import raster as R
    blocked = {R.WATER, R.MARSH, R.PIER, R.VOID, R.FLOOR}
    taken: set[tuple[int, int]] = set()
    side_of = {(dx, dz): s for s, dx, dz in SIDE_OFFSETS}

    # **A tower footprint is not always part of the mass.** `pick_wall_towers`
    # lets a tower stand on open ground beside the wall, so excluding only
    # `mass` let three treads be laid where a tower was about to be built --
    # entombed in solid block, invisible in the file and invisible on the
    # board.
    tower_cells = {c for t in towers for c in t}
    curtain = mass - tower_cells

    def free(cell: tuple[int, int]) -> bool:
        x, z = cell
        if not tm.inside(x, z) or cell in mass or cell in taken:
            return False
        if cell in tower_cells or cell in outside:
            return False              # solid tower, or the field side
        return not tm.building[z][x] and tm.surface[z][x] not in blocked

    def hugs(cell: tuple[int, int]) -> bool:
        return any((cell[0] + dx, cell[1] + dz) in mass for _, dx, dz in SIDE_OFFSETS)

    def gap(cell: tuple[int, int]) -> int:
        """How far this cell stands off the wall, in cells.

        Scoring on *touches* rather than distance was too blunt: beside a
        stair-stepped diagonal a straight flight touches on alternate cells,
        so two runs that both read as hugging could score 2 and 4 out of six
        for no visible reason. Distance is the thing being minimised, so
        minimise it.
        """
        return min((max(abs(cell[0] - m[0]), abs(cell[1] - m[1]))
                    for m in _cells_near(cell[0], cell[1], WALL_STAIR_REACH, tm)
                    if m in mass),
                   default=WALL_STAIR_REACH + 1)

    # **Land against the curtain, not against a tower.** A tower crowns two
    # courses above the curtain (`WALL_TOWER_RISE`), so a flight that arrives
    # at a tower's flank stops ten feet short of anywhere you can stand.
    built = 0
    for footprint in towers:
        cx = sum(c[0] for c in footprint) / len(footprint)
        cz = sum(c[1] for c in footprint) / len(footprint)

        # Any inside cell that already touches the curtain is a candidate foot
        # of a flight -- not just the cells jammed against the tower. The
        # circuit is a stair-stepped diagonal, so no straight cardinal run
        # hugs it for long; searching the inner face near the tower finds the
        # straightest stretch there is, which is where a real rampart stair
        # goes anyway.
        starts = [c for c in _cells_near(cx, cz, WALL_STAIR_SAMPLE, tm)
                  if free(c) and hugs(c)]
        best = None
        for start in sorted(starts):
            for _, dx, dz in SIDE_OFFSETS:
                run = [(start[0] + dx * i, start[1] + dz * i)
                       for i in range(wall_height)]
                if not all(free(c) for c in run):
                    continue
                # The top tread has to arrive beside the curtain, or there is
                # nothing at that height to step onto.
                if not any((run[0][0] + ex, run[0][1] + ez) in curtain
                           for _, ex, ez in SIDE_OFFSETS):
                    continue
                score = (-sum(gap(c) for c in run),
                         -round(abs(run[0][0] - cx) + abs(run[0][1] - cz)))
                if best is None or score > best[0]:
                    best = (score, (dx, dz), run)
        if best is None:
            continue
        _, d, run = best
        # **The rotation names the way you climb, not the way the run goes.**
        # The flight descends along ``d``, so the ascent -- and the tread --
        # faces the other way. Probed with `out/stairrot2.slab.txt`: four
        # flights, one per quarter turn, each carrying its own count in pips
        # on the wall it climbs to, because a marker on the ground cannot be
        # matched to a flight at the low side-on angle a tread is read from.
        climb = side_of[(-d[0], -d[1])]
        for i, (x, z) in enumerate(run):
            level = wall_height - 1 - i
            for under in range(level):
                b.add(place_tile(core, x, z, top + under * course))
            b.add(place_tile(stair, x, z, top + level * course, _SIDE_ROT[climb]))
            taken.add((x, z))
        if walk is not None:
            # Flush landing: the rampart's own walk sits at this height, so
            # capping the top tread means you step across rather than up.
            b.add(place_tile(walk, run[0][0], run[0][1],
                             top + wall_height * course))
        built += 1
    return built


def _hang_portcullises(b: Builder, tm, gates, mass, top: float) -> int:
    """Drop a grille across each gate passage.

    The palette has carried `Door - Portcullis double` unused since the gates
    were first built, because there was nowhere to hang it: the passage was
    cleared as a *disc*, so on a diagonal circuit its jambs were a 45-degree
    stair-step and a flat 4-wide panel has no straight line to sit on. The
    raster cuts a square passage now (`raster._carve_gate`), so the grille
    spans jamb to jamb in one placement.
    """
    grille = b.palette.resolve("city_gate")
    if grille is None or not gates:
        return 0
    hung = 0
    for passage in _components8(set(gates)):
        xs = sorted({c[0] for c in passage})
        zs = sorted({c[1] for c in passage})
        # Which axis is the opening? Read it off the *jambs*, not the bounding
        # box: a passage cut square through a band of its own width comes out
        # 4x4, and a box that square has no long axis to pick from. The jambs
        # are the sides the wall still stands on.
        jamb_x = any((min(xs) - 1, z) in mass or (max(xs) + 1, z) in mass
                     for z in zs)
        jamb_z = any((x, min(zs) - 1) in mass or (x, max(zs) + 1) in mass
                     for x in xs)
        if jamb_x == jamb_z:
            continue                      # ambiguous; leave the gate open
        across_x = jamb_x
        span = len(xs) if across_x else len(zs)
        if abs(grille.size_x - span) > 0.51:
            continue                      # the grille does not fit this mouth
        cx = (min(xs) + max(xs) + 1) / 2.0
        cz = (min(zs) + max(zs) + 1) / 2.0
        rot = 0 if across_x else _QUARTER
        # **Seat the grille like a wall: min corner on the tile lattice.**
        # Centring it on the boundary between two cells put its stored corner
        # at z=84.75, the one tile on the board off the half-tile grid -- which
        # is what `check_placements` guards, because a mini with grid snap then
        # does not line up with the floor it is standing on. A curtain piece
        # belongs *on* a cell edge, occupying the near half of one cell, which
        # is exactly what `place_wall` does for the 1-wide pieces.
        thin = min(rotated_footprint(grille, rot))
        if across_x:
            cz = min(zs) + (max(zs) - min(zs) + 1) // 2 + thin / 2.0
        else:
            cx = min(xs) + (max(xs) - min(xs) + 1) // 2 + thin / 2.0
        b.add(place_centered(grille, cx, cz, top, rot))
        hung += 1
    return hung


def _gatehouse_cells(mass: set[tuple[int, int]],
                     gates: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Wall cells that flank a gate, and so rise into gatehouse towers.

    A ring one cell deep around the opening, diagonals included: the wall
    circuit is a stair-stepped diagonal, so a jamb is often only diagonally
    adjacent to the passage it guards, and an orthogonal-only ring left the
    towers with gaps in them.
    """
    ring: set[tuple[int, int]] = set()
    for (gx, gz) in gates:
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cell = (gx + dx, gz + dz)
                if cell in mass and cell not in gates:
                    ring.add(cell)
    return ring


def _outside_the_wall(tm, mass: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Open ground the wall shuts out, by flooding in from the map border.

    What is left over inside the circuit is the town. The distinction is what
    lets battlements face outwards: a parapet on every exposed cell is not a
    battlement, it is a hedge of teeth.
    """
    out: set[tuple[int, int]] = set()
    stack = [(x, z) for x in range(tm.width) for z in (0, tm.depth - 1)]
    stack += [(x, z) for z in range(tm.depth) for x in (0, tm.width - 1)]
    while stack:
        x, z = stack.pop()
        if (not (0 <= x < tm.width and 0 <= z < tm.depth)
                or (x, z) in out or (x, z) in mass):
            continue
        out.add((x, z))
        stack += [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)]
    return out


def largest_rectangle(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The biggest axis-aligned rectangle wholly inside ``cells``.

    Largest-rectangle-under-a-histogram, the same method the rasteriser uses
    to regularise a footprint -- reused here because a roof wants the same
    answer the plan did.
    """
    if not cells:
        return set()
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    w = x1 - x0 + 1
    best = (0, 0, 0, 0, 0)                        # area, bx, bz, bw, bd
    heights = [0] * w
    for z in range(z0, z1 + 1):
        for i in range(w):
            heights[i] = heights[i] + 1 if (x0 + i, z) in cells else 0
        stack: list[int] = []
        i = 0
        while i <= w:
            h = heights[i] if i < w else 0
            if not stack or h >= heights[stack[-1]]:
                stack.append(i)
                i += 1
            else:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = heights[top] * (i - left)
                if area > best[0]:
                    best = (area, x0 + left, z - heights[top] + 1,
                            i - left, heights[top])
    _, bx, bz, bw, bd = best
    return {(bx + dx, bz + dz) for dx in range(bw) for dz in range(bd)}


def roof_wings(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Split a footprint into the rectangles a roof should actually be built from.

    A hip roof is a rectangle's answer to being roofed. Forced over a notched
    plan it produces a valid height *field* and incoherent ridges: probed in
    isolation, a 6x6 roofs as a clean pyramid while an L and a U come out with
    ridge lines meeting at angles that resolve into nothing. No choice of
    corner piece repairs that, because with axis-aligned notches the reflex
    corner falls on a *vertex between* cells and no single cell can carry it.

    A real L-shaped building has two ridges meeting at a valley. So the plan
    is cut into maximal rectangles, largest first, and each is roofed as its
    own hip -- which is both what the kit can express and what the building
    would actually have.
    """
    wings: list[set[tuple[int, int]]] = []
    left = set(cells)
    while left:
        rect = largest_rectangle(left)
        if not rect:
            break
        wings.append(rect)
        left -= rect
    if left:                                      # anything a rectangle missed
        wings.append(left)
    return wings


def _roof_rings(cells: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """How many courses in from the eaves each cell sits, by breadth-first
    search inward from the block's real boundary.

    The predecessor measured distance to the block's *bounding box* instead.
    On anything but a rectangle that is wrong twice over: cells on a real edge
    get counted as interior and float a course too high, and the box's empty
    corners are roofed over nothing. One L-shaped terrace on the Forest Church
    map had 27 such cells.
    """
    rings: dict[tuple[int, int], int] = {}
    frontier = [c for c in cells
                if any((c[0] + dx, c[1] + dz) not in cells for dx, dz in NEIGHBOURS)]
    depth = 0
    while frontier:
        nxt: list[tuple[int, int]] = []
        for c in frontier:
            if c in rings:
                continue
            rings[c] = depth
            for dx, dz in NEIGHBOURS:
                n = (c[0] + dx, c[1] + dz)
                if n in cells and n not in rings:
                    nxt.append(n)
        frontier, depth = nxt, depth + 1
    return rings


#: How many cells one roof piece spans down the slope, keyed on its rise.
#:
#: **Every medieval roof kit ships at two scales and only one of them can end a
#: ridge**, which is the finding `docs/great-buildings.md` §3.1 records: the
#: single-course family is 1x1x1 and rises 1.0, the double-course family is
#: 1x2x2 / 2x2x2 and rises 2.0, and *both* `end` pieces -- the only gable
#: terminators in the catalog -- are in the double-course one. A piece that
#: rises two tiles also reaches two cells down the slope, so a flood that
#: steps one cell per course cannot place it. This is the table that says so.
ROOF_COURSE_CELLS: dict[float, int] = {1.0: 1, 2.0: 2}


def roof_course_cells(piece) -> int:
    """How many cells ``piece`` spans down the slope. Unknown rises give 1."""
    return ROOF_COURSE_CELLS.get(round(piece.size_y, 2), 1)


def roof_courses(cells: set[tuple[int, int]], gable_axis: str,
                 cells_per_course: int = 1
                 ) -> dict[tuple[int, int], tuple[int, str | None]]:
    """``cell -> (course, fall)`` for a GABLED roof over a rectangle.

    ``gable_axis`` is the axis the **ridge** runs along, so its two ends are
    gables rather than eaves and the roof falls only across it. ``fall`` is the
    side a cell's slope drops toward, or ``None`` for a ridge cell -- one that
    belongs to neither side's courses and takes a flat cap, exactly as
    `_lay_roofs` already caps its innermost ring.

    **This is deliberately not a flag on `_roof_rings`.** That function floods
    from a block's whole boundary and is what every hip on every board is built
    from; a gable is a different question asked of the same rectangle, and
    `roof_wings` has already cut the plan into rectangles by the time either is
    called. Keeping them apart is what let the gable be probed with the hip
    beside it as a control.

    The arithmetic, and it is the whole of `roof-rings-two-cell-step`:

        W        span across the ridge
        full     complete courses per side = (W // 2) // cells_per_course
        covered  cells each side's courses actually reach = full * cpc
        middle   W - 2 * covered, the ridge band that takes the cap

    At ``cells_per_course = 1`` that reduces to the familiar case and
    ``middle`` is 0 or 1 -- a ridge line on an even span, a ridge cell on an
    odd one. At 2 it is what makes a double-course gable reach a span wider
    than four cells, which is the constraint that blocked the whole design:
    measured, a 4-cell span tiled and 5, 6 and 8 did not.

    **The middle band is capped flat rather than pitched**, and that is a
    choice with a cost. A span of 4k tiles with no cap at all; 4k+2 leaves two
    cells of flat ridge; odd spans leave one or three. On a wide barn that is a
    small flat deck along the ridge, which is a real roof form and is not what
    a tithe barn has. Finishing the remainder with a *single-course* pair --
    mixing the two scales at the ridge only -- is the better answer and is
    `roof-ridge-mixed-scale` in `tasks.json`.
    """
    if cells_per_course < 1:
        raise ValueError(f"cells_per_course must be >= 1, got {cells_per_course}")
    if gable_axis not in ("x", "z"):
        raise ValueError(f"gable_axis must be 'x' or 'z', got {gable_axis!r}")
    if not cells:
        return {}

    # Across the ridge: z when the ridge runs along x, and vice versa.
    across = (lambda c: c[1]) if gable_axis == "x" else (lambda c: c[0])
    lo_side, hi_side = ("n", "s") if gable_axis == "x" else ("w", "e")
    lo = min(across(c) for c in cells)
    hi = max(across(c) for c in cells)

    width = hi - lo + 1
    full = (width // 2) // cells_per_course
    covered = full * cells_per_course

    out: dict[tuple[int, int], tuple[int, str | None]] = {}
    for c in sorted(cells):
        a = across(c)
        from_lo, from_hi = a - lo, hi - a
        depth = min(from_lo, from_hi)
        if depth >= covered:
            # The ridge band. Its course is the one above the last full
            # course, so a cap laid there sits on top of the slopes rather
            # than inside them.
            out[c] = (full, None)
        else:
            side = lo_side if from_lo < from_hi else hi_side
            out[c] = (depth // cells_per_course, side)
    return out


def roof_course_anchors(courses: dict[tuple[int, int], tuple[int, str | None]],
                        gable_axis: str, cells_per_course: int = 1
                        ) -> dict[tuple[int, int], tuple[int, str]]:
    """The cells that actually carry a piece, and which way each falls.

    A piece ``cells_per_course`` cells deep is placed once per band, on the
    band's **outermost** cell; the cells behind it are covered by the same
    piece and must not place one of their own. Returning them separately is
    what stopped the first double-course sweep laying two pieces over the same
    pair at the ridge.
    """
    across = (lambda c: c[1]) if gable_axis == "x" else (lambda c: c[0])
    lo = min(across(c) for c in courses) if courses else 0
    hi = max(across(c) for c in courses) if courses else 0

    out: dict[tuple[int, int], tuple[int, str]] = {}
    for c, (course, fall) in courses.items():
        if fall is None:
            continue
        a = across(c)
        depth = min(a - lo, hi - a)
        if depth % cells_per_course == 0:
            out[c] = (course, fall)
    return out


#: Quarter-step turns to add to the Thatched convention, per kit, as
#: ``(edge, corner)``. Keyed on the catalog's ``folder``, because **the kit is
#: the folder** -- the same rule that found the facade's own corner piece.
#:
#: The rotations in :data:`ROOF_EDGE_ROT` were read out of one community-built
#: cottage, and that cottage is thatched. Nothing had ever checked whether
#: another kit shares the convention, and reading a Village hip built on it as
#: "this kit has no 1x1 hip pieces" was wrong twice over: the pieces are there,
#: one for one with Rural's, and each kit simply authors them facing its own
#: way. Measured with `tools/roofrot_probe.py --hips`, which lays the same hip
#: once per offset so exactly one closes:
#:
#:     Rural (thatch)                  edge +0   corner +0   <- the baseline
#:     Tavern (terracotta tile)        edge +6   corner +6
#:     Castle Fortified (shingle)      edge +6   corner +0
#:     Abandoned Village (slate)       edge +6   corner +0
#:
#: An unlisted kit gets ``(0, 0)`` and looks wrong rather than crashing, which
#: is the right failure: it shows up in the first screenshot.
ROOF_ROT_OFFSET: dict[str, tuple[int, int]] = {
    "rural": (0, 0),
    "tavern": (6, 6),
    "castle fortified": (6, 0),
    "abandoned village": (6, 0),
}


def roof_offsets(side) -> tuple[int, int]:
    """The ``(edge, corner)`` turn for whichever kit ``side`` came from."""
    return ROOF_ROT_OFFSET.get(_kit_of(side), (0, 0)) if side is not None else (0, 0)


#: Roof material per building tier, as the palette-role suffix. Thatch is the
#: bare role and stays the default, so a style that declares no second roof
#: keeps working unchanged.
#:
#: The hierarchy is the real one: thatch is what a cottage and a barn are
#: roofed in, tile is what a shop that can afford it buys, and slate is the
#: dearest -- which is why the civic tier gets it. Before this, *every* roof on
#: the map was `Thatched Roof 01`, because the set was resolved once for the
#: map rather than once per building, so the temple was thatched too.
ROOF_BY_TIER = {
    "civic": "slate",
    "trade": "tile",
    "common": "",
    "utility": "",
}

#: How a tier's roofs are actually dealt: (palette suffix, weight), where the
#: empty suffix is the thatched baseline.
#:
#: **A quarter is 98-100% one tier, so a per-tier constant makes every quarter
#: monochrome by construction.** Measured on East Tradebourne: the craft
#: quarter is 98% trade, the market 99%, residential 98% common. Tier is keyed
#: on kind and a quarter *is* a clump of one kind, so the very clustering that
#: makes a quarter legible guarantees every building inside it is built the
#: same. On a board that is 19 of 21 buildings in one block under identical
#: terracotta, which reads as a housing estate rather than as a craft quarter.
#:
#: The fabric therefore needs an axis that is not kind. This is the cheapest
#: one there is: deal the material per building, weighted so the tier still
#: dominates. A trade street stays visibly tiled and a common street visibly
#: thatched, but neither is uniform -- which is what a street built over two
#: centuries looks like.
#:
#: Dealt from the building id, so it is stable across rebuilds and independent
#: of the map seed; two towns that share a building id get the same roof, which
#: is harmless and keeps `tests/test_determinism.py` meaningful.
ROOF_MIX: dict[str, tuple[tuple[str, float], ...]] = {
    "common":  (("", 0.80), ("tile", 0.20)),
    "trade":   (("tile", 0.70), ("", 0.25), ("slate", 0.05)),
    "civic":   (("slate", 0.70), ("tile", 0.30)),
    "utility": (("", 0.90), ("tile", 0.10)),
}


def roof_suffix_for(tier: str, bid: str) -> str:
    """Which roof material this particular building gets."""
    mix = ROOF_MIX.get(tier)
    if not mix:
        return ROOF_BY_TIER.get(tier, "")
    # A stable deal per building rather than a random one: the same town must
    # rebuild to the same bytes, and `boards.digest_of` depends on it.
    roll = (zlib.crc32(f"roof:{tier}:{bid}".encode()) % 10_000) / 10_000.0
    for suffix, weight in mix:
        if roll < weight:
            return suffix
        roll -= weight
    return mix[-1][0]


#: How a building's ridge is ended, as ``(treatment, weight)`` per quarter.
#:
#: **Dealt by QUARTER, not by building**, which is the whole point and is the
#: same argument `QUARTER_SURFACE` makes about paving: a district that ends its
#: ridges one way reads as a district from across the board, and a roofline
#: dealt per building reads as noise. `ROOF_MIX` already varies the *material*
#: per building; this varies the *silhouette* per quarter, and the two axes
#: stay separate on purpose.
#:
#: `crow` is the crow-stepped parapet -- the gable that needs no end piece, and
#: therefore the only one available to a stone or boarded fabric, since
#: **`Tavern` is the only kit in the library that ships a roof `end`**
#: (`docs/great-buildings.md` §3.4c). Weighting it toward `civic` is not taste:
#: crow-stepping is a masonry form and civic is the dressed-stone tier.
#:
#: `outskirts` is deliberately all hip. A crow-stepped gable is a town
#: building's gesture; a cottage in the fields does not make it.
#: `endmix` closes both verges with the double-course end piece over a
#: single-course field (`docs/roofscape.md` §8.2). It takes its share from
#: `flush`, because it *is* a flush gable -- the same silhouette, closed with
#: the kit's own end rather than with stacked infill, and the only one of the
#: three that read as a gabled house on `PROBE roof mix`.
#:
#: **It self-gates and needs no quarter of its own.** Only `Tavern` ships an
#: end piece, so a wing whose roof was dealt thatch or slate falls back to
#: flush inside `_lay_gabled_wing` -- which is why it can be weighted freely
#: here without a rule about which quarters get tile. `civic` is left alone:
#: crow-stepping is the masonry form and endmix is a tiled one.
GABLE_ENDS: dict[str, tuple[tuple[str, float], ...]] = {
    "civic":       (("crow", 0.70), ("flush", 0.20), ("hip", 0.10)),
    "market":      (("endmix", 0.30), ("flush", 0.20), ("crow", 0.30),
                    ("hip", 0.20)),
    "craft":       (("endmix", 0.35), ("flush", 0.20), ("hip", 0.30),
                    ("crow", 0.15)),
    "docks":       (("endmix", 0.35), ("flush", 0.25), ("hip", 0.30),
                    ("crow", 0.10)),
    "residential": (("hip", 0.55), ("endmix", 0.25), ("flush", 0.15),
                    ("crow", 0.05)),
    "outskirts":   (("hip", 1.00),),
}

#: What a quarter with no entry in the table gets.
DEFAULT_GABLE_END = "hip"


def gable_end_for(quarter: str, seed: int = 0) -> str:
    """How this quarter ends its ridges, dealt stably from ``seed``.

    Stable per ``(quarter, seed)`` and **not** per building: two buildings in
    the same quarter of the same town get the same treatment, and the same town
    rebuilds to the same bytes -- which `boards.digest_of` depends on. Same
    crc32 deal as :func:`roof_suffix_for`, for the same reason.
    """
    mix = GABLE_ENDS.get(quarter)
    if not mix:
        return DEFAULT_GABLE_END
    roll = (zlib.crc32(f"gable:{quarter}:{seed}".encode()) % 10_000) / 10_000.0
    for treatment, weight in mix:
        if roll < weight:
            return treatment
        roll -= weight
    return mix[-1][0]


def roof_stack(palette, suffix: str):
    """The free-standing stack for a roof material, or ``None``.

    **A chimney is two different pieces and which one you want depends on the
    cell it lands on.** `Village Roof Side/Chimney` is a *combination* -- a
    roof slope with a stack cast onto it -- and on a sloped cell it is exactly
    right, because the slope half IS the roof there. Dropped on a capped ridge
    cell it stands a bare slope on end beside the flue, which reads as a pale
    skirt hanging off the stack. `Chimney 01` is the free-standing one and is
    what a ridge wants.

    Falls back to the thatched set a piece at a time, the same way `roof_set`
    does and for the same reason: a missing stack is invisible in the file and
    a bare hole in the roof on the board.
    """
    asset = palette.resolve(f"roof_stack_{suffix}") if suffix else None
    return asset if asset is not None else palette.resolve("roof_stack")


def roof_set(palette, tier: str, bid: str = ""):
    """The ``(side, corner, inner, cap, chimney)`` a tier is roofed in.

    Falls back a piece at a time to the thatched set, so a style that declares
    only some of a material still builds a whole roof rather than a roof with
    holes in it -- a missing slope is invisible in the file and a hole on the
    board.
    """
    suffix = roof_suffix_for(tier, bid) if bid else ROOF_BY_TIER.get(tier, "")
    base = ("roof_side", "roof_corner", "roof_corner_inner", "roof",
            "roof_chimney")
    out = []
    for role in base:
        asset = palette.resolve(f"{role}_{suffix}") if suffix else None
        out.append(asset if asset is not None else palette.resolve(role))
    return tuple(out)


def roof_set_named(palette, suffix: str):
    """The roof pieces for one named material, whatever tier asked for it.

    `roof_set` picks the suffix from the tier; this takes it. Same
    piece-at-a-time fallback to the thatched set, for the same reason -- a
    missing slope is invisible in the file and a hole on the board.
    """
    base = ("roof_side", "roof_corner", "roof_corner_inner", "roof",
            "roof_chimney")
    out = []
    for role in base:
        asset = palette.resolve(f"{role}_{suffix}") if suffix else None
        out.append(asset if asset is not None else palette.resolve(role))
    return tuple(out)


#: Which cell backs a slope that falls toward each side -- the one its high
#: edge leans on.
_BACK_OF = {"n": (0, 1), "s": (0, -1), "e": (-1, 0), "w": (1, 0)}

#: The diagonal a corner piece's high point leans on, per pair of falls.
_BACK_OF_CORNER = {
    frozenset(("n", "w")): (1, 1), frozenset(("n", "e")): (-1, 1),
    frozenset(("s", "w")): (1, -1), frozenset(("s", "e")): (-1, -1),
}


def roof_top_is_supported(rings, x: int, z: int, fall: tuple[str, ...]) -> bool:
    """Whether a sloped piece here would have its high edge covered.

    **This is what decides a ridge from a plateau.** `_roof_rings` steps one
    cell in and one course up, so on a wing whose short side is EVEN the flood
    stops with a band TWO cells wide at the top -- and capping all of it flat
    loses a whole course and leaves a deck where the ridge belongs. A 6 x 4
    came out a flat-topped box with a 4 x 2 plateau, which is what most of a
    town looks like from the side: 5 x 6 is the commonest wing shape on every
    board measured.

    Capping the top ring was not wrong, it was too broad. The reason it exists
    is real -- "a slope at the apex shows its open underside", the bare timber
    that showed at the top of every slate roof -- but that only happens where
    nothing backs the slope up. Two slopes on the same ring falling opposite
    ways lean on each other and form a proper ridge; a corner leans on its
    diagonal. So the test is the neighbour, not the ring index.

    Odd-short-side wings are unaffected: their flood already pinches to a
    one-cell ridge line, every cell of which falls two or three ways and is
    capped exactly as before.
    """
    r = rings.get((x, z), -1)
    if len(fall) == 1:
        dx, dz = _BACK_OF[fall[0]]
        return rings.get((x + dx, z + dz), -1) >= r
    if len(fall) == 2:
        back = _BACK_OF_CORNER.get(frozenset(fall))
        if back is None:                  # opposite sides: a one-cell ridge run
            return False
        return rings.get((x + back[0], z + back[1]), -1) >= r
    # No fall at all is a cell with roof on every side and needs no support;
    # three or four is the tip of an arm and can never have any.
    return not fall


def _roof_piece(fall: tuple[str, ...], side, corner, cap, inner=None,
                reflex: bool = False, edge_off: int = 0, corner_off: int = 0):
    """The roof asset and rotation for a cell, given the sides it slopes to.

    Two adjacent falls are a corner; one is a straight slope; none is a cell
    with roof all round it, which takes the flat cap. Three or four -- the tip
    of a one-cell-wide arm -- also takes the cap: no single hip piece describes
    a point, and a slope there would show its open underside.

    **A corner is only an *outside* corner if the block turns away there.** At
    the elbow of an L the roof turns the other way, and that reflex corner has
    its own piece in the kit. Half the corner cells on the Forest Church map
    are reflex -- 223 of 467 -- and building them with the outside piece is
    what made the roofscape read as jumbled.
    """
    if len(fall) == 1:
        return side, (ROOF_EDGE_ROT[fall[0]] + edge_off) % 24
    if len(fall) == 2:
        which = CORNER_BY_SIDES.get(frozenset(fall))
        if which is not None:
            if reflex and inner is not None:
                # The inner piece is authored facing into the angle, so it
                # takes the rotation of the corner diagonally opposite.
                return inner, (ROOF_CORNER_ROT[_OPPOSITE_CORNER[which]]
                               + corner_off) % 24
            return corner or side, (ROOF_CORNER_ROT[which] + corner_off) % 24
        # Opposite sides: a ridge run, which is an edge piece, not a corner.
        return side, (ROOF_EDGE_ROT[fall[0]] + edge_off) % 24
    return cap, 0


#: The corner facing the other way, used to orient a reflex piece.
_OPPOSITE_CORNER = {"nw": "se", "ne": "sw", "sw": "ne", "se": "nw"}


def _is_reflex(rings: dict[tuple[int, int], int], x: int, z: int,
               fall: tuple[str, ...]) -> bool:
    """Whether a two-fall corner turns *into* the roof rather than away.

    The test is the diagonal between the two falling sides -- but against the
    **ring numbers**, not against mere membership of the block. Testing
    membership marks every corner of every inner course as reflex, because on
    a plain rectangle the diagonal from a ring-1 corner is the ring-0 cell,
    which is of course still part of the building. A 6x6 square came out with
    eight "inner" corners and a square has none; that mistake put the wrong
    piece on 212 cells across the map.

    A corner is reflex only where the diagonal sits at the *same or higher*
    course. Where it sits lower, the roof is falling away on that diagonal
    too, which is what an outside corner is.
    """
    if len(fall) != 2:
        return False
    off = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}
    a, b = off[fall[0]], off[fall[1]]
    if a[0] + b[0] == 0 and a[1] + b[1] == 0:
        return False                      # opposite sides: a ridge, not a corner
    here = rings.get((x, z), 0)
    return rings.get((x + a[0] + b[0], z + a[1] + b[1]), -1) >= here


#: Roof rotations, measured from a hand-built community cottage. A quarter
#: turn off the wall convention -- do not "fix" these to match walls.
ROOF_EDGE_ROT = {"n": 6, "e": 0, "s": 18, "w": 12}
ROOF_CORNER_ROT = {"nw": 12, "ne": 6, "sw": 18, "se": 0}

#: Wall-corner rotations, keyed by which two sides of the cell face outwards.
#:
#: **Measured, not assumed.** Every 1x1 wall-corner instance in ``library/``
#: was decoded and classified by which of its four neighbouring cells held
#: wall geometry at the same height -- a corner with walls continuing east and
#: south is the north-west corner of its ring. That test is independent of any
#: bounding-box guess, so it reads courtyards (reflex corners) correctly
#: instead of mislabelling them as outside corners, which a bbox test does.
#:
#: Three asset families agree unanimously over 18 clean instances with zero
#: contradictions: ``Rural Corner`` and ``Rural Corner Floor 01`` from
#: ``cabin/small-forest-cottage.slab``, and
#: ``abandoned_village_wall_1x1_corner_01`` from the two modular-viking slabs.
#: The same procedure reproduces the already-recorded :data:`ROOF_CORNER_ROT`
#: from ``haunted roof corner out tip``, which is how we know it is sound.
#:
#: The result is self-consistent: a +6 step maps N->W, W->S, S->E, E->N, so
#: nw(0) -> sw(6) -> se(12) -> ne(18) is one rotation cycle of a single mesh.
#: It is a *half* turn off :data:`ROOF_CORNER_ROT` -- the roof kit's corners
#: are authored facing the other way. Do not "fix" either to match the other.
WALL_CORNER_ROT = {"nw": 0, "ne": 18, "sw": 6, "se": 12}

#: Exposed-side pairs that make a cell an outside corner. Two *opposite* sides
#: (a one-cell-thick spur) are deliberately absent: no single corner piece
#: describes them, so those cells keep the two-segment treatment.
CORNER_BY_SIDES = {
    frozenset(("n", "w")): "nw",
    frozenset(("n", "e")): "ne",
    frozenset(("s", "w")): "sw",
    frozenset(("s", "e")): "se",
}


#: Building kinds built in civic fabric rather than common house fabric.
CIVIC_KINDS = frozenset({"temple", "guildhall", "manor", "barracks"})

#: Kinds that trade with the public: a better door and a glazed street front,
#: in the same timber as a house. They are *not* given their own wall kit,
#: and the reason is the library rather than taste -- exactly two 1-cell
#: windows exist in the whole Medieval Fantasy pack (the Tavern one and the
#: castle one), so a tier that wants glass is built from one of those two.
#: Trade shares the wall with `common` and differs by door and glazing.
TRADE_KINDS = frozenset({"tavern", "shop", "apothecary", "smithy"})

#: Kinds with no public face: boarding, one storey, no glass. Rural ships a
#: wall and a matching corner and no window at all, which is precisely what a
#: barn is. See `tools/facade_probe.py`, candidate 3.
UTILITY_KINDS = frozenset({"warehouse", "stable", "shed"})

#: An outbuilding is a single storey whatever the layout dealt it. A three-
#: storey stable reads as a tenement, and the height is what you see first
#: from across a street.
UTILITY_STOREYS = 1


#: How often a wall segment is glazed, as one-in-N, by tier and by which face
#: of the building it sits on. ``0`` means never.
#:
#: **The point is the asymmetry, not the numbers.** Windows used to be dealt
#: by a hash over every exposed segment, so the back of a building was as
#: glazed as its front and a town looked identical from all four sides. A real
#: street has its glass on the street: shutters and a shopfront at the front,
#: a blank gable to the neighbour, and almost nothing at the back.
#:
#: Ground floors are one step sparser again (privacy, and doors already break
#: those runs) -- that rule predates the tiers and is applied on top.
GLAZE_RATE: dict[str, dict[str, int]] = {
    "civic":   {"front": 2, "flank": 3, "back": 0},
    "trade":   {"front": 2, "flank": 4, "back": 0},
    "common":  {"front": 3, "flank": 4, "back": 0},
    "utility": {"front": 0, "flank": 0, "back": 0},
}

#: The face opposite each side, used to find a building's back.
OPPOSITE_SIDE = {"n": "s", "s": "n", "e": "w", "w": "e"}


def _main_street_frontage(tm) -> set[str]:
    """Buildings with a doorway opening onto a main street.

    These get the show facade -- the better door and the denser glazing --
    because a frontage on the through road is the one "where" signal that
    actually varies on a real export. Ward membership does not: on Forest
    Church 47 of 51 buildings fall in a single ward, so a district-keyed style
    would be a no-op dressed up as a feature.
    """
    out: set[str] = set()
    for bid, doors in tm.doors.items():
        for x, z, side in doors:
            dx, dz = next((d, e) for s, d, e in SIDE_OFFSETS if s == side)
            ox, oz = x + dx, z + dz
            if tm.inside(ox, oz) and tm.street_class[oz][ox] == "main":
                out.add(bid)
                break
    return out


def glaze_rate(tier: str, side: str, front: str | None, main: bool) -> int:
    """One-in-N glazing for one wall segment, or 0 for never."""
    rates = GLAZE_RATE.get(tier, GLAZE_RATE["common"])
    if front is None:
        face = "flank"
    elif side == front:
        face = "front"
    elif side == OPPOSITE_SIDE[front]:
        face = "back"
    else:
        face = "flank"
    rate = rates[face]
    # A show facade on the through road: one step denser at the front, and
    # never denser than every other segment -- a wall of glass reads as a
    # conservatory, which is what the probe's "front only" candidate looked
    # like at one-in-one.
    if main and face == "front" and rate > 2:
        rate -= 1
    return rate


def tier_of(bid: str | None) -> str:
    """Which of the four fabrics a building is built in.

    The tier decides the *whole* facade -- wall, corner, window and door come
    from one kit together, because a facade that changes material at the
    corner reads as a mistake rather than as variety. That was the finding
    behind `_usable_corner`, and the tiers are the same rule at map scale.
    """
    kind = (bid or "").split("-")[0]
    if kind in CIVIC_KINDS:
        return "civic"
    if kind in UTILITY_KINDS:
        return "utility"
    if kind in TRADE_KINDS:
        return "trade"
    return "common"


#: What each quarter paves its LANES with. Lanes only, and that is a finding
#: rather than a simplification.
#:
#: The first version repainted open ground too, so a craft quarter's grass
#: became gravel. On the board (`out/flyby/blockq-eye.jpg`) that is a bald,
#: hard-edged sandy patch in a lawn with pine trees growing out of it -- the
#: override has no shape of its own, so it cuts an arbitrary blob wherever the
#: influence field happens to fall. A lane already has a shape somebody laid,
#: so repainting one reads as a decision; repainting open country reads as a
#: texture bug.
#:
#: A main road is left alone for the same kind of reason: it runs *between*
#: quarters and belongs to the town, so changing its surface at a boundary
#: claims a change the road does not have.
#:
#: A quarter with no entry keeps the town's own surfaces, which is what
#: `residential` does -- it is the default, so it should look like the default.
QUARTER_SURFACE: dict[str, str] = {
    "craft": "yard_gravel",
    "market": "plaza",
    "civic": "plaza",
    "docks": "yard_gravel",
}


def build_from_tilemap(
    tm,
    palette: Palette,
    *,
    storeys: int = 2,
    roofs: bool = True,
    wall_tiles: float = TOWN_WALL_TILES,
    seed: int = 0,
    layout=None,
    quarters: bool = True,
    fence_style: str = DEFAULT_FENCE_STYLE,
    npc_population=None,
) -> Builder:
    """Build a TaleSpire city board from a rasterised :class:`~citysmith.raster.TileMap`.

    Surfaces map to palette roles, building footprints get a perimeter shell
    with a doorway, and the town wall is stacked to ``wall_tiles``. Water sits
    below grade so it reads as a channel a creature can be pulled into
    rather than a hole in the board.
    """
    from . import raster as R

    b = Builder(palette, seed)

    ground = palette.require("ground")
    street = palette.require("street")
    floor = palette.require("floor")
    ext_wall = palette.require("wall")
    town_wall = palette.require("city_wall")
    door_asset = palette.resolve("door")
    roof_asset = palette.resolve("roof") if roofs else None

    # Surfaces map to roles, not assets, so the 2x2 pass can swap in a block
    # tile for the same surface where one exists.
    #
    # **Keyed on the road's class as well as the surface**, because the raster
    # already tells main from cart from lane and all three used to arrive as
    # one cobble. Six distinctions, three materials, and `lane`, `gravel` and
    # `field_1x1` all resolving to the same asset -- see
    # `docs/district-surfaces.md` §1.
    base_roles = {
        R.GROUND: "ground",
        # NOTE: R.FIELD maps to the 1x1 fallback here, not the 2x2 "field"
        # block -- pass 2 of _lay_terrain lays one asset per leftover cell,
        # and dropping the 2x2 Tilled Earth on a 1x1 leftover overhangs its
        # neighbours (the jumbled field fringes the design review caught).
        R.FIELD: "field_edge",
        R.STREET: "street",
        R.PLAZA: "plaza",
        # A walled property's forecourt. Its own role rather than the plaza's,
        # so a keep's courtyard and a market square are not the same stone.
        R.COURT: "court",
        # A lane is trodden earth, not laid cobble -- that is the whole point
        # of distinguishing it from the street it opens off. It used to be
        # `lane`, which is the same gravel the field edge was built from.
        R.LANE: "lane_earth",
        # Wet ground, at grade. The 2x2 twin in `_BLOCK_SURFACES` carries most
        # of a fen -- this is the 1x1 fringe, and it is the same asset
        # `lane_earth` uses, which is correct: a back lane and a bog are both
        # trodden wet mud, and the swamp kit ships exactly one 1x1 floor.
        R.MARSH: "marsh",
        # R.PIER is deliberately absent: a plank is water with a deck on it,
        # and both halves are laid by name rather than as a surface.
        R.FLOOR: "floor",
    }

    # Quarters, where the town has any. `quarter_map` measures the clustering
    # first and returns None on a settlement whose kinds do not cluster --
    # which is most of them, and is the correct answer rather than a
    # degradation. See `citysmith/quarters.py`.
    quarter_at = None
    if quarters:
        from .quarters import quarter_map
        quarter_at = quarter_map(tm)

    # **The terrain pass owns the ground sheet, so the yard's material has to
    # be decided here rather than laid over the top afterwards.**
    # `_lay_yards` used to surface its cells after `_lay_terrain` had already
    # sheeted them in grass, leaving two coplanar 1x1 tiles per cell -- 365 on
    # Pelvesthollow. TaleSpire keeps both and they z-fight, dithering as the
    # camera moves, so every yard on every board built so far did it.
    # Clearing the cell first is *not* the fix: open country is sheeted in 2x2
    # grass blocks, and taking one up because a single corner of it is a yard
    # strips the ground from three cells that are not, which is what left ferns
    # standing over nothing. One pass, one tile, decided once.
    yard_role_at: dict[tuple[int, int], str] = {}
    for bid, cells in yard_cells(tm).items():
        role = YARD_SURFACE.get(bid.split("-")[0], DEFAULT_YARD_SURFACE)
        if palette.resolve(role) is None:
            role = "ground"
        for cell in cells:
            yard_role_at[cell] = role

    def surface_role(surface: str, x: int, z: int) -> str:
        """Which role paves this cell."""
        role = base_roles.get(surface, "ground")

        # A cart street is humbler than a through road, and the class is the
        # only place that distinction has ever been recorded.
        if surface == R.STREET and tm.street_class[z][x] == R.CART_ROAD:
            if palette.resolve("street_cart") is not None:
                role = "street_cart"

        # A quarter repaints its lanes, and nothing else. See QUARTER_SURFACE.
        if quarter_at is not None and surface == R.LANE:
            override = QUARTER_SURFACE.get(quarter_at.get((x, z)))
            if override and palette.resolve(override) is not None:
                role = override

        # Worked ground beats lawn, and only lawn: a yard never repaints a
        # street or a watercourse that happens to clip it.
        if surface == R.GROUND and (x, z) in yard_role_at:
            role = yard_role_at[(x, z)]
        return role

    taper = edge_taper(tm)
    # An NPC mark replaces the cell it stands on rather than covering it, so
    # `_lay_npc_marks` clears the cell first -- and a 2x2 grass block cleared
    # by one of its four cells takes the other three away with it. The terrain
    # pass is told which cells that will happen to and lays them a tile at a
    # time. See the note on `_lay_terrain`'s ``reserved``.
    marks = ({(p.x, p.z) for p in npc_population.posts}
             if npc_population is not None else set())
    with b.layer(LANDSCAPE):
        _lay_terrain(b, tm, surface_role, grade=floor.size_y, taper=taper,
                     reserved=marks)
        _lay_yards(b, tm, grade=floor.size_y, taper=taper)
        _lay_quays(b, tm, grade=floor.size_y, taper=taper)
        _lay_bridges(b, tm, grade=floor.size_y, taper=taper)

    top = floor.size_y
    # A storey is a wall *plus the floor above it*. They were the same height
    # for a long time, which meant the wall column was continuous and there was
    # nowhere a floor slab could go without cutting through it: the floor fills
    # its cell (1.0 x 0.5 x 1.0) and the wall sits on the cell boundary
    # (1.0 x 2.0 x 0.5), so they shared a quarter of a cubic tile and the slab
    # edge showed as a band slicing through the masonry.
    #
    # The pack says the same thing in its own vocabulary: all 75 of its
    # Wall/Floor combination pieces are 2.5 tall, which is exactly wall plus
    # floor. Pitching the storey at that leaves a floor-thick gap between wall
    # courses, and the slab drops into it touching both and intersecting
    # neither.
    # **That gap is the thing you can see, so it is gone.** Pitching the storey
    # at wall+floor left a floor-thick slot between wall courses, and the deck
    # dropped into it filling its whole cell -- which means the deck's edge sat
    # flush with the wall face, a band of floorboards running right round every
    # building between storeys. Probed against the alternatives
    # (`tools/storey_probe.py`): with the courses touching, the facade is
    # unbroken from the ground to the eaves and the only horizontal line left
    # is the panel's own frame, which is what a timber-framed wall should look
    # like.
    #
    # It fixes the roof too, and by arithmetic rather than by luck. The roof is
    # seated at `floors * storey_h`; the head of the top wall is at
    # `(floors-1) * storey_h + wall`. Those are the same number only when the
    # storey *is* the wall. Pitched at wall+floor they differed by exactly a
    # deck, which is why the roofs floated a half tile once the attic deck that
    # had been filling the gap was taken away.
    upper = palette.resolve("floor_upper")
    deck = upper.size_y if upper is not None else 0.0
    storey_h = ext_wall.size_y

    # The shell, the roof and the circuit are one layer: a building is a
    # thing you stand *on* the ground, not part of it.
    with b.layer(STRUCTURE):
        # Building shells: perimeter only -- interiors are their own boards.
        #
        # Height comes from the building's own storey count, not a single figure
        # applied to the whole town. A village is mostly single-storey cottages
        # with a couple of two-storey inns; giving every structure the same wall
        # made the first board look like a field of towers. ``storeys`` is now a
        # ceiling for the tallest building rather than the height of every one.
        # Every doorway, not just the first: large buildings get a second
        # entrance and reading only index 0 silently dropped them.
        doors = {cell for cells in tm.doors.values() for cell in cells}

        # Civic buildings are built in dressed stone with arched openings and a
        # fancier door, so importance reads off the architecture rather than off
        # storey count alone. Everything else is a common house: plastered wall,
        # timber-framed window, peasant door -- with the wall variant dealt per
        # building so a row of cottages is not one repeated texture.
        window = palette.resolve("wall_window")
        civic_wall = palette.resolve("wall_civic")
        civic_window = palette.resolve("wall_window_civic")
        civic_door = palette.resolve("door_civic")
        util_wall = palette.resolve("wall_utility")
        wall_variants = [palette.resolve("wall", v) or ext_wall for v in range(3)]

        # Every course in the map is pitched at ``ext_wall.size_y``, so a tier
        # whose wall is a different height would put its own upper storeys and
        # its roof out of line with the arithmetic. Checked rather than
        # assumed: a style that pins a 2.5-tall combination piece here would
        # otherwise raise a whole tier by half a tile and look fine in the
        # file.
        def _usable_wall(asset):
            if asset is None or abs(asset.size_y - ext_wall.size_y) > 1e-6:
                return None
            return asset

        util_wall = _usable_wall(util_wall)

        # Outside corners are full-cell pieces, dealt per building on the same
        # variant index as the wall so a cottage's corners match its own walls.
        # A corner that is not exactly one cell square, or not the same height as
        # the wall it stacks beside, is rejected rather than placed: the first
        # would overhang its neighbours and drag the whole board off the tile grid,
        # the second would break the floor line at every storey above the ground.
        def _usable_corner(asset, wall):
            if asset is None or wall is None:
                return None
            if (asset.size_x, asset.size_z) != (1.0, 1.0):
                return None
            if abs(asset.size_y - ext_wall.size_y) > 1e-6:
                return None
            # **And it has to come from the wall's own kit.** Under seed 33 the
            # facade deals `Village Roof Side Wall 01/02` while every corner
            # variant resolves to `Rural Corner` -- cream timber-framed panels
            # with dark horizontal boarding at all four corners, a different
            # material and a different relief. Probed side by side against the
            # same box mitred from its own panels (`tools/corner_probe.py`,
            # read from two faces): the mismatch is obvious from any angle and
            # the mitre is clean, because the Village panel carries an edge
            # timber that meets its neighbour as a corner post.
            #
            # There is no Village corner to find -- that family is entirely
            # `group='roof'`, three flat panels and nothing else. Rural and
            # Brick each ship a wall *and* a matching corner and neither has a
            # 1-cell window, which is why the facade is Village in the first
            # place. So when the kits disagree the corner is dropped rather
            # than swapped, and the cell falls back to a panel per exposed
            # side. That costs two wall ends in one square, which is what the
            # corner piece was introduced to avoid -- but a buried seam warns
            # where a two-material corner shows.
            if _kit_of(asset) != _kit_of(wall):
                return None
            return asset

        corner_variants = [_usable_corner(palette.resolve("wall_corner", v),
                                          wall_variants[v]) for v in range(3)]
        civic_corner = _usable_corner(palette.resolve("wall_corner_civic"),
                                      civic_wall or ext_wall)
        util_corner = _usable_corner(palette.resolve("wall_corner_utility"),
                                     util_wall or ext_wall)

        # Built once: the facade asks for a fabric per building and a town is
        # 989 of them.
        fabrics = wall_families(palette.catalog)

        plan = footprints(tm)
        corner_ok = {
            bid: _corners_affordable(cells) for bid, cells in plan.items()
        }
        fronts = {bid: doors[0][2] for bid, doors in tm.doors.items() if doors}
        on_main = _main_street_frontage(tm)

        for bid, cells in tm.perimeter.items():
            b.group = bid
            floors = storeys_of(tm, bid, storeys)
            tier = tier_of(bid)
            # Every slot falls back to the common-house piece: a style with no
            # civic kit (cyberpunk has none) otherwise gets entry=None, and the
            # door branch below silently lays a solid wall across the doorway
            # -- a temple with no way in, while verify still reports it
            # enterable because verify reads the tilemap, not the placements.
            glazes = True
            if tier == "civic":
                face = civic_wall or ext_wall
                glass, entry = civic_window or window, civic_door or door_asset
                nook = civic_corner
            elif tier == "utility":
                # No window in this kit, and none wanted: a barn with glass in
                # it stops being a barn. This skips the whole glazing branch
                # below rather than dealing a window from another kit -- and it
                # is a flag now rather than `glass = None`, because a FABRIC
                # can supply a window of its own and would otherwise put glass
                # in every barn built from a kit that has one.
                glazes = False
                face = util_wall or ext_wall
                glass, entry = None, door_asset
                nook = util_corner if util_wall is not None else None
            else:
                variant = zlib.crc32(bid.encode()) % len(wall_variants)
                face = wall_variants[variant]
                # Trade shares the house's wall -- it is the only kit with a
                # 1-cell window -- and is told apart by its door and by a
                # street front with twice the glass in it.
                entry = (civic_door or door_asset) if tier == "trade" else door_asset
                glass = window
                nook = corner_variants[variant]
            if not corner_ok.get(bid, True):
                nook = None   # too small to spend cells on corners

            # The family the tier is built from. **The palette picks the KIT
            # and `walls.families` supplies the rest of it** -- both panel
            # widths, the window at each, the corner and the base/mid/top
            # course variants. Neither half can do the other's job: the
            # palette's style queries are what decide that civic is dressed
            # stone, and only the catalog knows what else that kit ships.
            fvar = zlib.crc32(bid.encode())
            # **The tier deals a FABRIC, not a kit.** A tier used to resolve
            # exactly one kit, so 46 of Forest Church's 51 buildings were the
            # same two panels -- and before the wide-panel work the common
            # house at least dealt two, so across-building variety had gone
            # 2 -> 1 while within-wall variety went up. `walls.TIER_FABRICS`
            # gives each tier a weighted set and this deals one per building,
            # stably, the way the wall variant used to be.
            fabric = W.fabric_for(tier, fvar, fabrics)
            fam = fabric or wall_family_of(b.palette.catalog, face)
            if fabric is not None:
                # A fabric is an explicit CROSS-KIT choice, so every piece the
                # palette resolved for this tier is the wrong material by
                # construction. Re-point the fallbacks at the fabric's own, and
                # leave them None where it has none -- a facade that falls back
                # to another kit's window is the mismatch this whole section
                # exists to prevent.
                face = fabric.piece("wall", 1, "mid", fvar) or face
                nook = fabric.piece("corner", 1, "mid", fvar)
                # Only ever the 1-CELL window here: `glass` is used as a
                # single-segment fallback, and a 2-cell piece dropped into one
                # cell overhangs its neighbour and drags the board off the grid.
                glass = fabric.piece("window", 1, "mid", fvar)
            # A tier that glazes still needs something to glaze with. The
            # fabric's 2-cell window counts even when it has no 1-cell one --
            # that is Abandoned Village, which has exactly that.
            if glazes and fam is not None:
                glazes = bool(glass is not None
                              or fam.all("window", 1) or fam.all("window", 2))
            else:
                glazes = glazes and glass is not None

            def _piece(role, span, course, fallback=None, _f=fam, _v=fvar):
                got = _f.piece(role, span, course, _v) if _f else None
                return got if got is not None else fallback

            def _deal(role, span, course, key, _f=fam, _h=storey_h):
                """One of the slot's interchangeable siblings, per PANEL.

                `_piece` deals per *building*, which is right for a corner --
                a facade that changes material at the corner reads as a
                mistake -- and wrong for a run, where nothing distinguishes
                the siblings and repeating one is just a repeated texture.
                Keyed on the cell so a rebuild is identical; `zlib.crc32`
                rather than `hash()`, because str hashes are salted per
                process and would re-deal the whole town every build.
                """
                got = _f.deal(role, span, course, key) if _f else None
                if got is None:
                    return None
                slop = W.WIDE_HEIGHT_SLOP if span == 2 else 1e-6
                return got if abs(got.size_y - _h) <= slop else None

            def _wide(role, course, _f=fam, _v=fvar, _h=storey_h):
                """The 2-cell piece, only if it can share this course.

                Height is checked here rather than trusted, for the reason
                `_usable_wall` gives one width down: `Tavern Wall 01` is 2.03
                against its own kit's 2.00, which is inside the slop and lands
                as an invisible overlap -- but a piece further out would raise
                a storey and take the roof up with it.
                """
                got = _f.piece(role, 2, course, _v) if _f else None
                if got is None or abs(got.size_y - _h) > W.WIDE_HEIGHT_SLOP:
                    return None
                return got

            # Group the building's exposed edges by cell. A cell with two adjacent
            # sides exposed is an outside corner, and placing a wall along each of
            # them puts two wall ends in the same square -- the doubled geometry
            # that showed on a third of our ground-course cells, and that the
            # hand-built community slabs never contain. One full-cell corner piece
            # replaces the pair. dict preserves the raster's cell order, so
            # placements come out in the order they did before.
            sides_at: dict[tuple[int, int], set[str]] = {}
            for x, z, side in cells:
                sides_at.setdefault((x, z), set()).add(side)

            own = plan.get(bid, set())
            corner_at: dict[tuple[int, int], str] = {}
            door_at: set[tuple[int, int]] = set()
            for (x, z), exposed in sides_at.items():
                turn = CORNER_BY_SIDES.get(frozenset(exposed))
                # Same reflex problem as the roof: at the elbow of an L the wall
                # turns into the building, so a full-cell outside corner there
                # looks wrong and eats a floor tile the plan needs.
                if turn is not None and not _is_reflex(
                        {c: 0 for c in own}, x, z, tuple(sorted(exposed))):
                    corner_at[(x, z)] = turn
                if any((x, z, s) in doors for s in exposed):
                    door_at.add((x, z))

            # **Built course by course, and each course packed as RUNS rather
            # than cell by cell.** Three things follow from that, and none of
            # them can be done in a per-cell loop:
            #
            #  * a run is covered by the kit's own 2-cell panel wherever one
            #    fits, which is what the kit is authored for -- runs average
            #    4.9 cells on these maps and not one is shorter than 2;
            #  * the odd cell left over **moves between courses**
            #    (`walls.pack`, rule `shift`), because a remainder in the same
            #    slot on every storey draws a full-height column of a visibly
            #    different panel;
            #  * pieces come from the course the storey stands in, so a plinth
            #    goes at the bottom and a cornice at the head instead of one
            #    course repeated all the way up.
            top_level = max((storeys_at(tm, bid, x, z, storeys)
                             for (x, z) in sides_at), default=0)
            for level in range(top_level):
                y = top + level * storey_h
                here = {c: sides for c, sides in sides_at.items()
                        if storeys_at(tm, bid, c[0], c[1], storeys) > level}
                if not here:
                    continue

                # A door has to keep a segment of its own, so a corner cell
                # carrying one falls back to per-side walls for the ground
                # course only; the storeys above it still get the corner piece.
                turned = {c: t for c, t in corner_at.items()
                          if c in here and nook is not None
                          and not (level == 0 and c in door_at)}
                for (x, z), turn in sorted(turned.items()):
                    course = W.course_at(
                        level, storeys_at(tm, bid, x, z, storeys))
                    b.add(place_tile(_piece("corner", 1, course, nook),
                                     x, z, y, WALL_CORNER_ROT[turn]))

                for (x, z), sides in here.items():
                    if level:
                        continue
                    for side in sorted(sides):
                        if (x, z, side) in doors and entry is not None:
                            b.add(place_wall(entry, x, z, side, y))

                segs = [(x, z, side) for (x, z), sides in here.items()
                        if (x, z) not in turned for side in sorted(sides)
                        if not (level == 0 and (x, z, side) in doors)]
                for side, rx, rz, length in W.runs_of(segs):
                    course = W.course_at(
                        level, storeys_at(tm, bid, rx, rz, storeys))
                    narrow = _piece("wall", 1, course, face)
                    wide = _wide("wall", course)
                    rule = W.DEFAULT_PACK if wide is not None else "single"
                    for off, span in W.pack(length, level, rule):
                        cx = rx + off if side in ("n", "s") else rx
                        cz = rz if side in ("n", "s") else rz + off
                        # Windows break the blank masonry that made every
                        # facade read as a fortification. Dealt by a stable
                        # hash so rebuilds are identical -- zlib.crc32, not
                        # hash(), because str hashes are salted per process.
                        #
                        # **The rate carries from cells to panels unchanged,
                        # and that is arithmetic rather than an assumption.**
                        # A run of six at one-in-three is two 1-cell windows or
                        # one 2-cell window: the count halves and the glazed
                        # *area* is identical. Fewer, wider openings is the
                        # point rather than a side effect.
                        rate = glaze_rate(tier, side, fronts.get(bid),
                                          bid in on_main)
                        # Ground floors keep one fewer window than the storeys
                        # above: privacy, and the doorway already breaks those
                        # runs. Rounded rather than skipped, so a one-storey
                        # cottage does not end up blank.
                        if rate and level == 0:
                            rate += 1
                        key = zlib.crc32(
                            f"{bid}:{cx}:{cz}:{level}:{side}".encode())
                        lit = glazes and rate and key % rate == 0
                        piece = None
                        if lit:
                            piece = _deal("window", span, course, key)
                            if piece is None:
                                piece = (_wide("window", course) if span == 2
                                         else _piece("window", 1, course, glass))
                        if piece is None and lit and span == 2:
                            # The kit ships no 2-cell window. Rather than drop
                            # the glazing, split the panel: one narrow window
                            # beside one narrow wall, which is what this run
                            # did before wide packing existed.
                            small = _piece("window", 1, course, glass)
                            if small is not None and narrow is not None:
                                b.add(place_wall(small, cx, cz, side, y))
                                nx = cx + 1 if side in ("n", "s") else cx
                                nz = cz if side in ("n", "s") else cz + 1
                                b.add(place_wall(narrow, nx, nz, side, y))
                                continue
                        if piece is None:
                            piece = _deal("wall", span, course, key)
                        if piece is None:
                            piece = (wide if span == 2 else narrow) or face
                        # **A piece taller than the storey is sunk, not raised.**
                        # `Tavern Wall 01` is 2.03 against its kit's 2.00, which
                        # `WIDE_HEIGHT_SLOP` admits because the storey is
                        # pitched at the 1-cell piece. Left sitting on the
                        # course line that excess goes UPWARDS, and at the top
                        # storey there is no course above it -- the wall head
                        # IS the roof line, so the panel stands 1.8 inches
                        # proud of its own thatch on every eave of every
                        # building in the kit. Measured on Forest Church: 697
                        # pieces. Dropped by the excess instead, the overlap
                        # goes into the course below, where it is inside the
                        # floor or the wall beneath and nothing can see it --
                        # and the head lands exactly on the arithmetic the
                        # roof pass uses.
                        b.add(place_wall_span(
                            piece, cx, cz, side,
                            span, y - max(0.0, piece.size_y - storey_h)))

        # Upper-storey floors. Without these a multi-storey building is a hollow
        # box, and now that facades carry windows you can see straight through one
        # to the underside of the roof. One slab per cell per storey above ground.
        if upper is not None:
            for bid, cells_xy in sorted(plan.items()):
                b.group = bid
                # Up to the top storey, not through it. **An attic needs no
                # floor**: the highest slab used to go in as "the ceiling the
                # roof seats on", but the roof seats on the wall head, not on
                # it, so all it did was deck the roof void -- a room nothing
                # stands in, under a roof you cannot see past. On Forest Church
                # that is one slab per cell per building, over a thousand tiles
                # spent on a surface no one sees. A single-storey cottage now
                # gets no upper slab at all, which is what a cottage is.
                #
                # **Interior cells only.** A deck fills its whole cell, so on a
                # perimeter cell its edge lands flush with the outside face of
                # the wall and reads as a band of floorboards round the
                # building -- the floor, seen from outside, which is the one
                # thing it should never be. Laid on the cells that have no
                # exposed side it never reaches the facade at all. The cost is
                # an upper floor that stops one cell short of the wall, and
                # that shows only through a window.
                edge = {(x, z) for x, z, _ in tm.perimeter.get(bid, ())}
                inner = sorted(c for c in cells_xy if c not in edge)
                for x, z in inner:
                    for level in range(1, storeys_at(tm, bid, x, z, storeys)):
                        b.add(place_tile(upper, x, z, top + level * storey_h))

    with b.layer(STRUCTURE):
        _build_porches(b, tm, floor.size_y, taper, storey_h, storeys)
        towers = pick_towers(tm, storeys)
        if roof_asset is not None:
            # Which buildings the fabric deal put in a derelict kit, so the
            # roof can follow. Recomputed rather than passed down because the
            # facade pass owns its own loop and this is a different one.
            poor = {bid: "slate" for bid in tm.perimeter
                    if (lambda f: f is not None
                        and W.KIT_ROLE.get(f.kit) == "poor")(
                        W.fabric_for(tier_of(bid),
                                     zlib.crc32(bid.encode()), fabrics))}
            _lay_roofs(b, tm, top, storey_h, storeys, skip=set(towers),
                       roof_override=poor, quarter_at=quarter_at, seed=seed)
        _lay_towers(b, tm, towers, civic_wall or ext_wall, top, storey_h, storeys)
        b.group = ""
        _lay_town_wall(b, tm, town_wall, top, wall_tiles)

    b.group = ""
    _dress_districts(b, tm, grade=floor.size_y, taper=taper, storeys=storeys,
                     fence_style=fence_style)

    # Last of all, because a mark takes a cell back off whatever the dressing
    # put there. A guard standing inside a barrel is worse than no guard.
    if npc_population is not None:
        _lay_npc_marks(b, tm, npc_population, grade=floor.size_y, taper=taper)

    return b


#: duty -> palette role. Three roles rather than one so the populations read
#: apart on the board without opening the manifest.
NPC_MARK_ROLE = {
    "guard": "npc_guard_mark",
    "working": "npc_work_mark",
    "off_duty": "npc_idle_mark",
}


def _lay_npc_marks(b: Builder, tm, population, *, grade: float,
                   taper: dict[tuple[int, int], float | None]) -> int:
    """One contrasting tile per NPC post; returns how many landed.

    **A v2 slab carries no creatures**, so this is the same device a scene uses
    for the party: the tile says "a mini goes here" and `npcs.manifest` says
    who. Laid last and *replacing* the cell rather than covering it -- two
    coplanar surfaces in one square is the seam that shifts with the camera,
    and the cell's props come up too, because a person does not arrive inside
    a barrel.
    """
    cells: dict[tuple[int, int], str] = {}
    for post in population.posts:
        role = NPC_MARK_ROLE.get(post.duty)
        if role is None or b.palette.resolve(role) is None:
            continue
        # Where the border taper has dropped the ground away to nothing there
        # is no ground to stand on, so there is no post either.
        if taper.get((post.x, post.z), 0.0) is None:
            continue
        cells[(post.x, post.z)] = role

    if not cells:
        return 0

    with b.layer(LANDSCAPE):
        b.clear_cells(set(cells), below=grade + 0.01)
        for (x, z), role in sorted(cells.items()):
            drop = taper.get((x, z), 0.0) or 0.0
            b.surface(role, x, z, grade - drop)
    return len(cells)


class Scatter:
    """Places scenery only where nothing already stands.

    **TaleSpire silently drops a prop whose collider overlaps one already in
    the slab.** No error, no warning -- the prop simply is not in the pasted
    result. So scatter with no collision test does not produce a dense wood:
    it produces a thin one, plus a pile of assets that never arrive, plus the
    half-there mess of whatever did. On the Forest Church map 1,000 of 2,137
    props were inside another prop's collider.

    Placement is **all or nothing** per group, so a tree assembled from a
    trunk and a crown either arrives whole or is not attempted. A crown that
    landed while its trunk was rejected is exactly the "leaves that do not
    line up with a trunk" this class exists to prevent.
    """

    def __init__(self, builder: Builder, *, seed_from_builder: bool = True):
        """Knows what is already on the board, not only what it puts there.

        **A fresh `Scatter` used to start blind, and that was most of the
        remaining overlaps.** Yard fences are laid by `_lay_yards`, which runs
        before `_dress_districts` and has no scatter of its own;
        `_dress_districts` then made a new one, so every fern, crate, stump and
        tree it planted was free to grow straight through a fence that was
        already standing. Measured on Pelvesthollow, that was the single
        largest group left after the collision test was corrected -- ferns,
        crates and stumps against `Wooden Fence`.

        Seeding costs one pass over the placements at construction, which is
        nothing beside laying them.
        """
        self.b = builder
        self._at: dict[tuple[int, int], list[tuple[tuple[float, ...], float, float]]] = {}
        self.rejected = 0
        if seed_from_builder:
            # `builder.byid` rather than the catalog: the Builder already keeps
            # every asset it has placed, so this needs nothing of the catalog
            # and works against the stub ones the tests use.
            for p in builder.placements:
                asset = builder.byid.get(p.asset_id)
                if asset is None or getattr(asset, "kind", "") != "prop":
                    continue
                ox, oz = collider_offset(asset, p.rot)
                self._record((oriented_box(asset, p.x + ox, p.z + oz, p.rot),
                              p.y, p.y + asset.size_y))

    @staticmethod
    def box(asset: Asset, cx: float, cz: float, y: float, rot: int
            ) -> tuple[tuple[float, ...], float, float]:
        """``(oriented box, y0, y1)``.

        **Oriented, not axis-aligned, and that is a correction.** It used to
        take `rotated_footprint`, which swaps the axes on quarter turns and
        does nothing on the other eighteen -- so a fern at 15 degrees was
        measured at its unrotated extent and the scatter let it through where
        it did not fit. Scenery is dealt at all 24 steps, so that was most of
        it: 1,179 props on Pelvesthollow were interpenetrating something after
        the scatter had passed them.

        (cx, cz) is where the collider centre ends up -- see `place_centered`.
        """
        return (oriented_box(asset, cx, cz, rot), y, y + asset.size_y)

    def _clear(self, box: tuple[tuple[float, ...], float, float]) -> bool:
        obb, y0, y1 = box
        e = 1e-6
        x0, z0, x1, z1 = oriented_aabb(obb)
        for cx in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
            for cz in range(int(math.floor(z0)), int(math.ceil(z1)) + 1):
                for o_obb, o_y0, o_y1 in self._at.get((cx, cz), ()):
                    if not (y0 < o_y1 - e and o_y0 < y1 - e):
                        continue          # one is above the other
                    if oriented_depth(obb, o_obb) > e:
                        return False
        return True

    def _record(self, box: tuple[tuple[float, ...], float, float]) -> None:
        x0, z0, x1, z1 = oriented_aabb(box[0])
        for cx in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
            for cz in range(int(math.floor(z0)), int(math.ceil(z1)) + 1):
                self._at.setdefault((cx, cz), []).append(box)

    def place(self, pieces: list[tuple[Asset, float, float, float, int]]) -> bool:
        """Place every piece, or none of them. True if it went down."""
        pieces = [p for p in pieces if p[0] is not None]
        if not pieces:
            return False
        boxes = [self.box(*p) for p in pieces]
        if not all(self._clear(bx) for bx in boxes):
            self.rejected += 1
            return False
        for (asset, cx, cz, y, rot), bx in zip(pieces, boxes):
            self.b.add(place_centered(asset, cx, cz, y, rot), prop=True)
            self._record(bx)
        return True

    def reserve(self, asset: Asset, cx: float, cz: float, y: float,
                rot: int) -> None:
        """Record a box without placing anything, so scenery keeps clear of it.

        For geometry laid outside the scatter's own collision test -- a fence
        run, whose consecutive panels overlap as bounding boxes while their
        meshes are disjoint (`_lay_fences`). The test is wrong in that
        direction and right in this one: a pine has no business growing through
        a field wall, and a box is exactly the shape a canopy should avoid.
        """
        self._record(self.box(asset, cx, cz, y, rot))

    def one(self, asset, cx: float, cz: float, y: float, rot: int) -> bool:
        return self.place([(asset, cx, cz, y, rot)])


def _plant_conifer(scatter: Scatter, palette: Palette, cx: float, cz: float,
                   y: float, rot: int, tall: bool) -> bool:
    """Stack a pine out of its kit so it has a trunk under its leaves.

    Piece heights come from the assets, so the crown sits exactly on top of
    what is below it: stump 1.3, optional middle 1.3, crown 2.42. Stacking by
    measured height is also what keeps the pieces from overlapping each other
    and being dropped -- the failure that made two-piece pines lose roughly a
    third of their canopies the first time this was tried.
    """
    trunk = palette.resolve("tree_conifer_trunk")
    crown = palette.resolve("tree_conifer_crown")
    if crown is None:
        return False
    if trunk is None:
        return scatter.one(crown, cx, cz, y, rot)

    # Stump and crown only. The kit's Middle section is a bare trunk: stacked
    # between them it shows as a dark gap under the foliage, which is the
    # "tree that does not match its trunk" -- probed side by side against the
    # two-piece pine, which meets the ground cleanly. Height variety has to
    # come from somewhere that does not break the tree.
    return scatter.place([(trunk, cx, cz, y, rot),
                          (crown, cx, cz, y + trunk.size_y, rot)])


#: Building kinds that trade with the public, and so hang a sign. A smithy or
#: an inn that looks exactly like the 28 cottages either side of it gives a
#: party nothing to navigate by, and "which door is the tavern" is the single
#: most common question asked of a town map.
SIGNED_KINDS = frozenset({"tavern", "shop", "smithy", "apothecary", "stable",
                          "warehouse", "guildhall"})


#: Woodland stops this many cells short of a building. Trees scattered at a
#: flat rate grew tight against walls and filled the yards the notches cut,
#: which reads as a village abandoned to the forest rather than one clearing
#: ground to live in.
TREE_CLEARANCE = 3


def building_distance(tm, limit: int = 8) -> dict[tuple[int, int], int]:
    """Cells out from the nearest **built** thing, breadth-first, capped at ``limit``.

    Density of anything scattered should fall off near the built-up area:
    that gradient is what makes a settlement look like it was cleared, and
    its absence is what made the woodland grow up to the doorsteps.

    **The town wall counts as built.** Seeded from ``tm.building`` alone, the
    falloff cleared woodland off every doorstep and left it growing flush
    against the rampart -- pines standing in the ditch with their canopies
    over the masonry, which is the one place a defender needs open ground and
    the one structure the eye reads as a silhouette. A wall is a building for
    this purpose even though it has no ``building`` id.
    """
    dist: dict[tuple[int, int], int] = {}
    frontier: list[tuple[int, int]] = []
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.building[z][x] or tm.wall[z][x]:
                dist[(x, z)] = 0
                frontier.append((x, z))
    step = 0
    while frontier and step < limit:
        step += 1
        nxt: list[tuple[int, int]] = []
        for x, z in frontier:
            for dx, dz in NEIGHBOURS:
                n = (x + dx, z + dz)
                if (0 <= n[0] < tm.width and 0 <= n[1] < tm.depth
                        and n not in dist):
                    dist[n] = step
                    nxt.append(n)
        frontier = nxt
    return dist


#: Lattice spacing, in tiles, for the two woodland fields. The canopy field is
#: coarse enough to make stands and glades you can walk between; the stand
#: field is finer, so a species patch is a copse rather than a whole quarter.
CANOPY_CELL = 14
STAND_CELL = 9

#: Lattice spacing for the reed field. Finer than the canopy, because a reed
#: bed is a smaller thing than a stand of pines: at CANOPY_CELL a whole fen
#: came out either uniformly thick or uniformly bare.
REED_CELL = 7


def _value_noise(x: int, z: int, cell: int, salt: str) -> float:
    """Smooth deterministic noise in 0..1, on a lattice of ``cell`` tiles.

    Bilinear between hashed lattice corners with a smoothstep fade. Hashed
    rather than seeded so it is stable across runs and independent of how many
    random draws happened before it -- the same reason ``zlib.crc32`` is used
    everywhere else here instead of ``hash``.
    """
    gx, gz = x // cell, z // cell
    fx, fz = (x % cell) / cell, (z % cell) / cell

    def corner(ax: int, az: int) -> float:
        return (zlib.crc32(f"{salt}:{ax}:{az}".encode()) % 1000) / 999.0

    sx = fx * fx * (3 - 2 * fx)
    sz = fz * fz * (3 - 2 * fz)
    top = corner(gx, gz) * (1 - sx) + corner(gx + 1, gz) * sx
    bot = corner(gx, gz + 1) * (1 - sx) + corner(gx + 1, gz + 1) * sx
    return top * (1 - sz) + bot * sz


def canopy_at(x: int, z: int) -> float:
    """How thick the wood is here, 0..1.

    A flat scatter rate produced an orchard: nearest-neighbour spacing ran
    3.4 to 4.9 tiles with almost no spread, and density measured 2.1-3.1%
    at every distance from town. Woods are not uniform -- they have closed
    stands and open glades, and the walk between them is most of what makes
    a forest feel like somewhere. This is that variation.
    """
    return _value_noise(x, z, CANOPY_CELL, "canopy")


def species_at(x: int, z: int) -> str:
    """Which species dominates here.

    Chosen from a smooth field rather than per tree, so neighbours agree and
    the wood grows in stands. Drawing per tree gave species agreement of 46%
    between nearest neighbours -- exactly the random rate, which is what
    salt-and-pepper looks like when you measure it.
    """
    v = _value_noise(x, z, STAND_CELL, "stand")
    if v < 0.55:
        return "tree_conifer"
    if v < 0.88:
        return "tree_broadleaf"
    return "tree_dead"


def _dress_seams(b: Builder, tm, scatter: "Scatter", rng, grade: float,
                 taper: dict[tuple[int, int], float | None]) -> None:
    """Break the hard tile line where one surface meets another.

    Grass meets lane meets cobble on a ruler-straight edge everywhere, which
    is the most reliable tell that a map was rasterised rather than built. The
    shore course proved a one-cell transition works; this is the same idea for
    seams that have no natural material of their own, done with low growth and
    spill rather than by retexturing the cell.
    """
    from . import raster as R

    verges = [b.palette.resolve("verge", v) for v in range(4)]
    verges = [v for v in verges if v is not None]
    if not verges:
        return

    hedges = [b.palette.resolve("hedge", v) for v in range(4)]
    hedges = [h for h in hedges if h is not None]

    # A field ending on a straight edge against grass is the same ruler line
    # as grass against cobble, and wants the same treatment -- but a field
    # boundary is a *boundary*, so it gets a hedgerow rather than weeds.
    if hedges:
        for z in range(tm.depth):
            for x in range(tm.width):
                if tm.surface[z][x] != R.FIELD or tm.building[z][x]:
                    continue
                if not any(0 <= x + dx < tm.width and 0 <= z + dz < tm.depth
                           and tm.surface[z + dz][x + dx] == R.GROUND
                           for dx, dz in NEIGHBOURS):
                    continue
                if rng.random() > 0.35:
                    continue
                drop = taper.get((x, z), 0.0)
                if drop is None:
                    continue
                scatter.one(hedges[rng.randrange(len(hedges))],
                            x + 0.5 + rng.uniform(-0.2, 0.2),
                            z + 0.5 + rng.uniform(-0.2, 0.2),
                            grade - drop, rng.randrange(24))

    soft = (R.GROUND, R.FIELD)
    hard = (R.STREET, R.PLAZA, R.LANE)
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] not in soft or tm.building[z][x]:
                continue
            if not any(0 <= x + dx < tm.width and 0 <= z + dz < tm.depth
                       and tm.surface[z + dz][x + dx] in hard
                       for dx, dz in NEIGHBOURS):
                continue
            if rng.random() > 0.22:
                continue
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue
            scatter.one(verges[rng.randrange(len(verges))],
                        x + 0.5 + rng.uniform(-0.3, 0.3),
                        z + 0.5 + rng.uniform(-0.3, 0.3),
                        grade - drop, rng.randrange(24))


#: Buildings that get a porch over the door. A blank wall with a hole in it is
#: a warehouse; an entrance somebody sheltered under is a place of business.
PORCHED_KINDS = frozenset({"tavern", "shop", "apothecary", "guildhall",
                           "temple", "manor", "smithy"})


#: What gathers outside each trade, as palette prop categories. Uniform
#: scatter never clusters, and real scenery does: a smithy has fuel stacked
#: against it, a warehouse has crates waiting, an inn has empties out the
#: back. A cluster also tells a party what the building *is* from further off
#: than a sign does.
TRADE_CLUTTER = {
    "smithy": "smithy",
    "warehouse": "shop",
    "shop": "shop",
    "tavern": "tavern",
    "stable": "house",
    "apothecary": "shop",
}


def _stack_trade_goods(b: Builder, tm, scatter: "Scatter", rng, grade: float,
                       taper: dict[tuple[int, int], float | None]) -> int:
    """Gather a few props against the wall of each trade building.

    Placed on the open cells the building's own perimeter touches, so the pile
    leans on the wall rather than floating in the road, and capped at a few
    per building so a workshop reads as busy rather than barricaded.
    """
    placed = 0
    for bid, cells in sorted(tm.perimeter.items()):
        b.group = bid
        category = TRADE_CLUTTER.get(bid.split("-")[0])
        if category is None:
            continue
        spots: list[tuple[int, int]] = []
        for x, z, side in cells:
            dx, dz = next((d, e) for sd, d, e in SIDE_OFFSETS if sd == side)
            ox, oz = x + dx, z + dz
            if not (0 <= ox < tm.width and 0 <= oz < tm.depth):
                continue
            if tm.building[oz][ox] or tm.wall[oz][ox]:
                continue
            if taper.get((ox, oz), 0.0) is None:
                continue
            if (ox, oz, side) in [(dx_, dz_, s_) for dx_, dz_, s_ in
                                  tm.doors.get(bid, [])]:
                continue                      # keep the doorway clear
            spots.append((ox, oz))
        if not spots:
            continue
        rng.shuffle(spots)
        for ox, oz in spots[:rng.randint(2, 4)]:
            asset = b.palette.prop(category, rng)
            if asset is None:
                continue
            drop = taper.get((ox, oz), 0.0) or 0.0
            if scatter.one(asset, ox + 0.5, oz + 0.5, grade - drop,
                           rng.randrange(24)):
                placed += 1
    return placed


def _build_porches(b: Builder, tm, grade: float,
                   taper: dict[tuple[int, int], float | None],
                   storey_h: float, max_floors: int) -> int:
    """Roof the cell outside the primary door of each public building.

    Every building is otherwise one flat-topped mass, and an entrance reads
    as a hole punched in a wall. The porch sits high enough to clear the
    signs hung on the same facade -- they occupy up to 2.65, so anything
    lower would have its sign silently dropped for overlapping it.
    """
    built = 0
    for bid, doors in sorted(tm.doors.items()):
        b.group = bid
        if bid.split("-")[0] not in PORCHED_KINDS or not doors:
            continue
        # **A single storey has nothing to carry a porch.** The hood seats at
        # `storey_h + 0.5`, which on a one-storey cottage is level with its
        # own eaves -- a second roof grafted onto the first at the same
        # height. Those buildings get a lantern by the door instead, which is
        # what says "you may knock here" at cottage scale.
        if storeys_of(tm, bid, max_floors) < 2:
            continue
        # The porch is a slope off the building's own roof, so it takes that
        # building's material and that kit's turn -- a thatched hood on a
        # slate hall is exactly the mismatch the tiers exist to remove.
        #
        # **The bid is the whole fix.** Without it `roof_set` falls back to
        # ROOF_BY_TIER, the per-tier constant, while `_lay_roofs` passes the
        # bid and gets the per-building deal from ROOF_MIX -- so the porch was
        # dealt the tier default and the roof it hangs off was dealt something
        # else. The comment above has always said what this should do; it
        # missed by one argument. Measured on Forest Church: 2 of 5 porches
        # mismatched, one of them a red Village tile awning under a Thatched
        # roof.
        piece = roof_set(b.palette, tier_of(bid), bid)[0]
        if piece is None:
            continue
        edge_off, _ = roof_offsets(piece)
        x, z, side = doors[0]
        dx, dz = next((d, e) for sd, d, e in SIDE_OFFSETS if sd == side)
        ox, oz = x + dx, z + dz
        if not (0 <= ox < tm.width and 0 <= oz < tm.depth):
            continue
        if tm.building[oz][ox] or tm.wall[oz][ox]:
            continue
        drop = taper.get((ox, oz), 0.0)
        if drop is None:
            continue
        # Slopes away from the wall it is attached to.
        b.add(place_tile(piece, ox, oz, grade - drop + storey_h + 0.5,
                         (ROOF_EDGE_ROT[side] + edge_off) % 24))
        built += 1
    return built


#: Head height for a lantern on a doorpost, in tiles above the threshold.
LANTERN_Y = 1.5


def _hang_lanterns(b: Builder, tm, scatter: "Scatter", grade: float,
                   taper: dict[tuple[int, int], float | None],
                   max_floors: int) -> int:
    """A lantern on the doorpost of every single-storey house.

    These are the buildings a porch cannot serve -- the hood would seat level
    with their own eaves -- and without it a cottage is a blank wall with a
    hole in it, which is the complaint the porch was built to answer in the
    first place. A signed trade already gets its board; this is for everyone
    else, so the two are mutually exclusive.
    """
    lantern = b.palette.resolve("door_lantern")
    if lantern is None:
        return 0
    hung = 0
    for bid, doors in sorted(tm.doors.items()):
        b.group = bid
        if not doors or storeys_of(tm, bid, max_floors) >= 2:
            continue
        if bid.split("-")[0] in SIGNED_KINDS:
            continue                       # its sign already says who it is
        x, z, side = doors[0]
        dx, dz = next((d, e) for sd, d, e in SIDE_OFFSETS if sd == side)
        ox, oz = x + dx, z + dz
        if not tm.inside(ox, oz) or tm.building[oz][ox] or tm.wall[oz][ox]:
            continue
        drop = taper.get((ox, oz), 0.0)
        if drop is None:
            continue
        # Against the facade and off to one side, clear of the doorway --
        # the same placement the sign uses, so a building can never get both
        # in the same square.
        cx = ox + 0.5 - dx * 0.34 + (0.32 if dx == 0 else 0.0)
        cz = oz + 0.5 - dz * 0.34 + (0.32 if dz == 0 else 0.0)
        if scatter.one(lantern, cx, cz, grade - drop + LANTERN_Y,
                       _SIDE_ROT[side]):
            hung += 1
    return hung


def _hang_signs(b: Builder, tm, scatter: "Scatter", grade: float,
                taper: dict[tuple[int, int], float | None]) -> None:
    """Hang a trade sign beside the primary door of each public building.

    The sign goes in the cell the door opens *onto*, pushed against the
    facade, at head height. It is dealt from the building's id so a rebuild
    hangs the same sign on the same inn, and it goes through the collision
    scatter like any other prop -- a sign inside a barrel is still a dropped
    sign.
    """
    signs = [b.palette.resolve("shop_sign", v) for v in range(6)]
    signs = [s for s in signs if s is not None]
    if not signs:
        return

    for bid, doors in sorted(tm.doors.items()):
        b.group = bid
        if bid.split("-")[0] not in SIGNED_KINDS or not doors:
            continue
        x, z, side = doors[0]
        dx, dz = next((d, e) for sd, d, e in SIDE_OFFSETS if sd == side)
        ox, oz = x + dx, z + dz
        if not (0 <= ox < tm.width and 0 <= oz < tm.depth):
            continue
        drop = taper.get((ox, oz), 0.0)
        if drop is None:
            continue
        sign = signs[zlib.crc32(bid.encode()) % len(signs)]
        # Just off the facade, and to one side so it does not block the door.
        cx = ox + 0.5 - dx * 0.3 + (0.3 if dx == 0 else 0.0)
        cz = oz + 0.5 - dz * 0.3 + (0.3 if dz == 0 else 0.0)
        scatter.one(sign, cx, cz, grade - drop + 1.4, _SIDE_ROT[side])


def _dress_districts(b: Builder, tm, grade: float,
                     taper: dict[tuple[int, int], float | None],
                     storeys: int = 3,
                     fence_style: str = DEFAULT_FENCE_STYLE) -> None:
    """Scatter district-appropriate props so each quarter reads as itself.

    Bare surfaces carry no story: a field of Tilled Earth is just brown, a
    park is just a green rectangle. A thin, deterministic scatter -- wheat in
    the fields, pines and ferns in the parks, a well and market clutter on the
    plaza -- tells the party what each district *is* at a glance. Density is
    deliberately low: props are dressing, not obstacles, and TaleSpire minis
    are free-placed so nothing here can block movement anyway.
    """
    from . import raster as R

    cat = b.palette.catalog

    def named(n: str):
        hits = cat.find(name=n, pack="Medieval Fantasy")
        return hits[0] if hits else None

    wheat = named("Wheat Bunch")
    straw = named("Straw stacks 01")
    pine_stump = named("Stackable Pine Stump")
    pine_top = named("Stackable Pine Top")
    fern_small = named("Fern 01")
    fern_big = named("Fern 02")
    well = named("Well 01")
    barrels = named("Barrels")
    cart = named("Wooden Cart")

    rng = random.Random("dress:stable")  # deterministic across rebuilds
    scatter = Scatter(b)
    # A small board has budget a large one does not; see `detail_scale`. This
    # multiplies the *human* dressing below and never the woodland.
    detail = detail_scale(tm)

    # Fences first, and the order is the point: a field wall is surveyed
    # geometry and a pine is dressing, so the wall is laid and its boxes
    # reserved before anything is planted. Planting first would put trees in
    # the line of the boundary and then reject the wall panels that hit them.
    with b.layer(LANDSCAPE):
        b.fence_pieces = _lay_fences(b, tm, grade, taper, scatter, fence_style)
    #: Where a tree stands, so a *felled* stump is never dropped beside one.
    #: A cut stump within a canopy's reach reads as that tree's trunk, badly
    #: aligned -- which is what "trees that do not match their trunks" turned
    #: out to be. The conifer stack itself is concentric at every rotation and
    #: from every angle; it was the loose stumps all along, and a check for
    #: stumps strictly *under* a canopy missed them because beside is enough.
    planted: list[tuple[float, float, float]] = []
    felled: list[tuple[float, float]] = []

    def _clear_of_stumps(cx: float, cz: float, r: float) -> bool:
        return not any((cx - sx) ** 2 + (cz - sz) ** 2 < (r + 1.0) ** 2
                       for sx, sz in felled)

    # Signs hang off a building and the goods are stacked against one, so they
    # belong with the structures; the trees, verges and seam dressing below are
    # landscape. This pass is the one place the two mix.
    with b.layer(STRUCTURE):
        _hang_signs(b, tm, scatter, grade, taper)
        _hang_lanterns(b, tm, scatter, grade, taper, storeys)
        b.group = ""
    near_town = building_distance(tm)
    market = [b.palette.resolve("market_goods", v) for v in range(4)]
    market = [m for m in market if m is not None]
    yard = [b.palette.resolve("yard_clutter", v) for v in range(4)]
    yard = [y for y in yard if y is not None]
    reeds = [b.palette.resolve("marsh_reed", v) for v in range(6)]
    reeds = [r for r in reeds if r is not None]
    lilies = [b.palette.resolve("marsh_lily", v) for v in range(6)]
    lilies = [l for l in lilies if l is not None]

    # The waterline, derived rather than assumed. `_fill_water` steps up from
    # the bed by the water tile's own height, and every bed drop is a whole
    # multiple of that step (`WATER_DEEPEN_STEP` == the tile's 0.5), so the
    # topmost tile always seats with its underside at `here -
    # WATER_SURFACE_DROP` whatever the depth. A pad floats on its top face.
    water_tile = b.palette.resolve("water")
    lily_lift = (water_tile.size_y - WATER_SURFACE_DROP) if water_tile else None

    def pool_cell(x: int, z: int, r: int = 2) -> bool:
        """Is this open water part of a fen rather than a river or a harbour?

        Lily pads belong in still water inside a wetland. Scattered on every
        WATER cell they would carpet a tidal quay and a mill race alike, which
        is the surface-class-without-context mistake `_dress_seams` already
        records against hedgerows.
        """
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                nx, nz = x + dx, z + dz
                if (0 <= nx < tm.width and 0 <= nz < tm.depth
                        and tm.surface[nz][nx] == R.MARSH):
                    return True
        return False

    # Parks come from the layout as GROUND repainted by area polygons; the
    # raster keeps no park mask, so rediscover them cheaply: ground cells not
    # adjacent to any street or building read as open green.
    def near(x: int, z: int, kinds: frozenset, r: int = 1) -> bool:
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                nx, nz = x + dx, z + dz
                if 0 <= nx < tm.width and 0 <= nz < tm.depth:
                    if tm.surface[nz][nx] in kinds or tm.building[nz][nx]:
                        return True
        return False

    plaza_dressed = False
    for z in range(tm.depth):
        for x in range(tm.width):
            surf = tm.surface[z][x]
            # Scenery stands on the ground as laid, which near the border is
            # the tapered height -- otherwise a fringe of trees floats over
            # the falloff. Where the fringe is unbuilt, nothing is planted.
            drop = taper.get((x, z), 0.0)
            if drop is None:
                continue
            here = grade - drop

            if surf == R.FIELD:
                # A worked field is human dressing: somebody cut this.
                roll = rng.random()
                if roll < 0.10 * detail and wheat is not None:
                    scatter.one(wheat, x + 0.5, z + 0.5, here, rng.randrange(24))
                elif roll < 0.12 * detail and straw is not None:
                    scatter.one(straw, x + 0.5, z + 0.5, here, rng.randrange(24))

            elif surf == R.MARSH:
                # **Reeds grow in beds.** Same argument as the canopy field
                # above, for the same reason: a flat rate produced an orchard
                # there, and here it would produce a lawn of reeds at one
                # spacing over the whole fen. The noise field gives thickets
                # you cannot see through and open water-meadow between them,
                # and the walk between the two is most of what makes a
                # wetland somewhere rather than a texture.
                #
                # NOT scaled by `detail`: reeds are the fen's own vegetation,
                # not human dressing, so they follow the woodland convention
                # of being budget-independent.
                if reeds:
                    thickness = _value_noise(x, z, REED_CELL, "reeds")
                    if rng.random() < 0.05 + 0.40 * thickness ** 2:
                        scatter.one(reeds[rng.randrange(len(reeds))],
                                    x + 0.5 + rng.uniform(-0.32, 0.32),
                                    z + 0.5 + rng.uniform(-0.32, 0.32),
                                    here, rng.randrange(24))

            elif surf == R.WATER:
                # Standing water inside a fen gets floating cover. Every pad
                # is under 0.3 tall and under a tile across, so this is the
                # one place a prop is laid on the waterline rather than on
                # the ground -- see `lily_lift`.
                if lilies and lily_lift is not None and pool_cell(x, z):
                    if rng.random() < 0.22:
                        scatter.one(lilies[rng.randrange(len(lilies))],
                                    x + 0.5 + rng.uniform(-0.28, 0.28),
                                    z + 0.5 + rng.uniform(-0.28, 0.28),
                                    here + lily_lift, rng.randrange(24))

            elif surf == R.GROUND and not near(x, z, frozenset({R.STREET, R.PLAZA})):
                # Density follows the canopy field, so the wood closes up in
                # stands and opens into glades instead of covering the map at
                # one flat rate.
                thickness = canopy_at(x, z)
                # Cumulative bands, not independent thresholds. These are the
                # arms of one elif ladder, so each has to sit *above* the last
                # or it is unreachable -- with the tree band reaching 0.24 in a
                # thick stand and ferns fixed at 0.15, undergrowth stopped
                # appearing in exactly the places it should be thickest, and
                # the fern count fell to 88 for the whole map.
                p_tree = 0.010 + 0.230 * thickness ** 3
                p_stump = p_tree + 0.006
                p_fern = p_stump + 0.030 + 0.130 * thickness ** 2
                roll = rng.random()
                if roll < p_tree:
                    # A forest of one species is a plantation. Weighted so
                    # conifer still dominates -- this is pine country -- with
                    # broadleaf for relief and the occasional dead trunk,
                    # which is also the best cover a scout gets out here.
                    # A yard is worked ground, not woodland. The cells a
                    # notch opened sit right against a wall, and filling them
                    # with pines made the cut read as neglect rather than as
                    # somebody's back yard.
                    if near_town.get((x, z), 99) <= TREE_CLEARANCE:
                        if yard and rng.random() < 0.28:
                            scatter.one(yard[rng.randrange(len(yard))],
                                        x + 0.5, z + 0.5, here, rng.randrange(24))
                        continue
                    jx, jz = rng.uniform(-0.35, 0.35), rng.uniform(-0.35, 0.35)
                    cx, cz = x + 0.5 + jx, z + 0.5 + jz
                    rot = rng.randrange(24)
                    # The stand decides the species; a one-in-eight stray
                    # keeps a stand from reading as a plantation.
                    role = (species_at(x, z) if rng.random() > 0.12
                            else ("tree_broadleaf", "tree_conifer",
                                  "tree_dead")[rng.randrange(3)])
                    if role == "tree_conifer":
                        crown = b.palette.resolve("tree_conifer_crown")
                        r = max(crown.size_x, crown.size_z) / 2 if crown else 1.0
                        if (_clear_of_stumps(cx, cz, r)
                                and _plant_conifer(scatter, b.palette, cx, cz,
                                                   here, rot, tall=False)):
                            planted.append((cx, cz, r))
                    else:
                        tree = b.palette.resolve(role)
                        if tree is not None:
                            r = max(tree.size_x, tree.size_z) / 2
                            if (_clear_of_stumps(cx, cz, r)
                                    and scatter.one(tree, cx, cz, here, rot)):
                                planted.append((cx, cz, r))
                elif roll < p_stump and pine_stump is not None:
                    # A cut tree, and it has to read as one: clear of any
                    # standing tree, or it looks like that tree's trunk.
                    sx, sz = x + 0.5, z + 0.5
                    if (not any((sx - tx) ** 2 + (sz - tz) ** 2 < (r + 1.0) ** 2
                                for tx, tz, r in planted)
                            and scatter.one(pine_stump, sx, sz, here,
                                            rng.randrange(24))):
                        felled.append((sx, sz))
                elif fern_small is not None and roll < p_fern:
                    # Undergrowth belongs under the canopy, not spread evenly
                    # over open pasture.
                    fern = fern_big if rng.random() < 0.3 and fern_big else fern_small
                    scatter.one(fern, x + 0.5 + rng.uniform(-0.3, 0.3),
                                z + 0.5 + rng.uniform(-0.3, 0.3),
                                here, rng.randrange(24))

            elif surf == R.GROUND and detail > 1.0:
                # THE VERGE, and until now it was the one surface with no rule
                # of its own. Everything above is either woodland (GROUND *away*
                # from the town) or a paved class; the grass strip between a
                # road and a building fell through every branch and got
                # nothing. Measured on Pelvesthollow: 2,086 cells, 8% of the
                # board, and it is the empty green in every screenshot of the
                # place.
                #
                # It is dressed only where there is budget for it (`detail`),
                # because on East Tradebourne the same rule would be 20,000
                # more props on a board already at 99.4% of the slab cap.
                #
                # Undergrowth dominates and the rest is what gets left at the
                # edge of a road. Deliberately built from pieces already
                # standing elsewhere on this map -- widening the vocabulary
                # here needs a probe first, per the standing rule that an
                # asset's shape is read and not assumed.
                roll = rng.random()
                if fern_small is not None and roll < 0.06 * detail:
                    fern = fern_big if rng.random() < 0.25 and fern_big else fern_small
                    scatter.one(fern, x + 0.5 + rng.uniform(-0.3, 0.3),
                                z + 0.5 + rng.uniform(-0.3, 0.3),
                                here, rng.randrange(24))
                elif yard and roll < 0.075 * detail:
                    scatter.one(yard[rng.randrange(len(yard))],
                                x + 0.5 + rng.uniform(-0.2, 0.2),
                                z + 0.5 + rng.uniform(-0.2, 0.2),
                                here, rng.randrange(24))

            elif surf == R.PLAZA:
                # A square with nothing on it is worse than no square. Goods
                # cluster loosely, leaving room in the middle for the crowd --
                # and for whatever the party is about to do in it.
                if market and rng.random() < 0.16 * detail:
                    scatter.one(market[rng.randrange(len(market))],
                                x + 0.5 + rng.uniform(-0.2, 0.2),
                                z + 0.5 + rng.uniform(-0.2, 0.2),
                                here, rng.randrange(24))
                elif well is not None and not plaza_dressed and rng.random() < 0.06:
                    if scatter.one(well, x + 0.5, z + 0.5, here, rng.randrange(24)):
                        plaza_dressed = True

            elif surf == R.LANE:
                # Lanes are where things get left, sparsely and against a wall.
                if yard and rng.random() < 0.07 * detail:
                    scatter.one(yard[rng.randrange(len(yard))],
                                x + 0.5, z + 0.5, here, rng.randrange(24))

            elif surf == R.STREET:
                # This export has no plaza cells (MFCG's squares came through
                # empty), so market clutter leans against buildings along the
                # streets instead: barrels and carts where a street cell
                # touches a wall, and one well at the busiest such spot.
                if not near(x, z, frozenset()):  # building adjacency only
                    continue
                # Only the street cells that *touch* a building are eligible,
                # and once main streets widened to four tiles that is a small
                # set -- 39 cells on this map, against 1,234 street tiles. At
                # the old 2.5% the entire town got one barrel. The rate is a
                # share of an already narrow set, so it has to be high.
                roll = rng.random()
                if not plaza_dressed and well is not None and surf == R.STREET:
                    if scatter.one(well, x + 0.5, z + 0.5, here, rng.randrange(24)):
                        plaza_dressed = True
                elif roll < min(0.85, 0.30 * detail) and barrels is not None:
                    # Capped: this rate is already a share of a *narrow* set
                    # (only street cells touching a building), so scaling it
                    # freely would line every wall in town with barrels.
                    pick = cart if rng.random() < 0.4 and cart else barrels
                    scatter.one(pick, x + 0.5, z + 0.5, here, rng.randrange(24))

    with b.layer(STRUCTURE):
        _stack_trade_goods(b, tm, scatter, rng, grade, taper)
        b.group = ""
    # Last, so it can see everything already standing and not fight it.
    _dress_seams(b, tm, scatter, rng, grade, taper)
    _dress_yards(b, tm, scatter, rng, grade, taper)


def _build_city_wall(b: Builder, city: City, base_y: float) -> None:
    wall = b.palette.require("city_wall")
    rect = city.wall_rect
    assert rect is not None
    gates = set(city.gates)
    height = 2

    for level in range(height):
        y = base_y + level * wall.size_y
        for tx in range(rect.x, rect.x2):
            if (tx, rect.z) not in gates:
                b.add(place_wall(wall, tx, rect.z, "n", y))
            if (tx, rect.z2 - 1) not in gates:
                b.add(place_wall(wall, tx, rect.z2 - 1, "s", y))
        for tz in range(rect.z + 1, rect.z2 - 1):
            if (rect.x, tz) not in gates:
                b.add(place_wall(wall, rect.x, tz, "w", y))
            if (rect.x2 - 1, tz) not in gates:
                b.add(place_wall(wall, rect.x2 - 1, tz, "e", y))
