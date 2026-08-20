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
import math
import random
import zlib
from dataclasses import dataclass, field

from .catalog import Asset
from .city import Building, City, Rect
from .palette import Palette
from .slab import MAX_COMPRESSED_BYTES, Placement, Slab, SlabError, encode

#: Rotation steps per quarter turn (24 steps in a full turn).
_QUARTER = 6

#: Rotation step indices for the four cardinal facings.
ROT_N, ROT_E, ROT_S, ROT_W = 0, 6, 12, 18

SIDES = ("n", "e", "s", "w")
_SIDE_ROT = {"n": ROT_N, "e": ROT_E, "s": ROT_S, "w": ROT_W}

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
    together: the outer rings step *down*, and the outermost ring is **ragged**
    -- a stable per-cell nudge moves each cell in or out of the falloff, and
    the cells that fall past the end are not laid at all. A straight edge one
    tile lower is still a straight edge.

    Cells carrying a building or a wall, or next to one, are left at grade: a
    foundation half a tile down its own footprint is worse than a hard edge.
    Only plain ground and field may be dropped entirely -- a street that ends
    in a hole is a bug, however ragged the meadow beside it looks.
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

    out: dict[tuple[int, int], float | None] = {}
    for bz in range(blocks_z):
        for bx in range(blocks_x):
            depth_in = min(bx, bz, blocks_x - 1 - bx, blocks_z - 1 - bz)
            noise = zlib.crc32(f"edge:{bx}:{bz}".encode())

            # The nudge varies how far *in* the falloff reaches, never whether
            # the outermost block is lowered. Letting it do the latter left a
            # third of the border sitting at full grade against the void --
            # which is the sheer edge this exists to remove. Ground drops
            # monotonically outward; only the reach is ragged.
            reach = rings + noise % 3
            if depth_in >= reach:
                continue
            # **One step down, however far the falloff reaches.** Stepping per
            # ring gave terraces up to four deep, and a 4-8 tile wide flat
            # terrace half a tile below grade does not read as land falling
            # away -- it reads as a second layer of land laid over the first,
            # which is what it was called twice. The raggedness is what stops
            # the map looking cropped; the height was never doing that work.
            drop = min((reach - depth_in) * step, EDGE_TAPER_MAX_DROP)
            if depth_in == 0 and (noise >> 8) % 4 == 0:
                drop = None                   # a bite out of the outer fringe

            for dz in (0, 1):
                for dx in (0, 1):
                    x, z = bx * 2 + dx, bz * 2 + dz
                    if not (0 <= x < tm.width and 0 <= z < tm.depth):
                        continue
                    if (x, z) in protected:
                        continue
                    if drop is None and tm.surface[z][x] not in (R.GROUND, R.FIELD):
                        # A street that ends in a hole is a bug however ragged
                        # the meadow beside it looks; keep it, at the same
                        # single step as everything else so it does not build
                        # its own terrace across the road.
                        out[(x, z)] = EDGE_TAPER_MAX_DROP
                    else:
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


def storeys_of(tm, bid: str | None, ceiling: int) -> int:
    """A building's own storey count, clamped to ``1..ceiling``.

    ``ceiling`` is the tallest building allowed on the map, not the height of
    every building: a village is mostly single-storey cottages, and giving
    them all the same wall made the first board look like a field of towers.
    """
    if not bid:
        return 0
    return min(max(1, tm.floors.get(bid, 1)), ceiling)


def rotated_footprint(asset: Asset, rot: int) -> tuple[float, float]:
    """The asset's ground footprint after ``rot``, as ``(size_x, size_z)``.

    Odd quarter turns swap the axes; even ones leave them alone.
    """
    if ((rot // _QUARTER) % 4) % 2:
        return (asset.size_z, asset.size_x)
    return (asset.size_x, asset.size_z)


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

    @property
    def byid(self) -> dict[str, Asset]:
        """Catalog assets by id, for anything that needs a placement's shape."""
        if self._byid is None:
            self._byid = {a.id: a for a in self.palette.catalog.assets}
        return self._byid

    def add(self, placement: Placement, *, prop: bool = False) -> None:
        self.placements.append(placement)
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
                   pack: bool = True) -> "ChunkPlan":
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
        scatter dressing is not somewhere anyone plays, so it is dropped rather
        than encoded, and counted in :class:`BuildStats`. If *every* chunk is
        open country they are all kept instead -- the map is all terrain, and
        skipping everything would emit nothing at all.

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

        buckets: dict[tuple[int, int], list[Placement]] = {}
        for p in slab.placements:
            c = min(cols - 1, max(0, int((p.x - ox) // size)))
            r = min(rows - 1, max(0, int((p.z - oz) // size)))
            buckets.setdefault((r, c), []).append(p)

        cells: list[_Cell] = []
        for (r, c), items in sorted(buckets.items()):
            cells.extend(_subdivide(_Cell(
                r, c, "",
                ox + c * size, oz + r * size,
                min(ox + (c + 1) * size, ex), min(oz + (r + 1) * size, ez),
                items,
            ), max_assets))
        cells.sort(key=lambda cell: (cell.row, cell.col, cell.quad))

        terrain, grade = self._grade_terrain(slab.placements)
        # Placements were translated to whole tiles above; the baseline was
        # recorded in the untranslated frame, so move it to match.
        idx, idz = int(round(dx)), int(round(dz))
        baseline = {(kx + idx, kz + idz): v + dy
                    for (kx, kz), v in self.ground_baseline.items()}
        made = [
            SlabChunk(
                row=cell.row, col=cell.col, quad=cell.quad,
                x0=cell.x0 - dx, z0=cell.z0 - dz,
                x1=cell.x1 - dx, z1=cell.z1 - dz,
                slab=Slab(cell.items),
                open_country=_is_open_country(
                    cell.items, terrain, self.prop_ids, grade, baseline),
            )
            for cell in cells
        ]

        kept, skipped = _trim_open_country(made, rows, cols)
        if not skip_open_country or not kept:
            kept, skipped = made, []

        # Detection wants small chunks; pasting wants few. Those pull opposite
        # ways -- at 8 tiles this map skips 15% of its assets but emits 139
        # files, at 32 tiles it emits 15 files and skips 2%. So detect fine,
        # then pack the survivors back up to the per-slab budget: the skipping
        # is decided at chunk resolution, the paste count at budget resolution.
        # Packing walks the grid boustrophedon (row-major, alternate rows
        # reversed) so consecutive chunks in a slab are physically adjacent and
        # a partial paste still lands as a contiguous piece of town.
        if pack:
            kept = _pack_chunks(kept, max_assets, cols)

        if register and len(kept) > 1:
            marker = self.palette.resolve("ground") or self.palette.resolve("floor")
            anchor = Placement(
                marker.id if marker is not None
                else min(slab.placements, key=lambda p: (p.y, p.z, p.x)).asset_id,
                0.0, 0.0, 0.0, 0,
            )
            for piece in kept:
                piece.slab.add(anchor)
            self.stats.registration_markers = len(kept)

        self.stats.slabs = len(kept)
        self.stats.chunks_skipped = len(skipped)
        self.stats.assets_skipped = sum(ch.count for ch in skipped)
        return ChunkPlan(kept, skipped, rows, cols, size, (ox - dx, oz - dz))

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
        # Decode to count the bytes rather than scaling the base64 length by
        # 3/4: padding makes that estimate optimistic, and it was optimistic
        # by exactly three bytes on a chunk that then failed to encode. The
        # whole point of measuring instead of counting assets is defeated by
        # measuring approximately.
        try:
            size = len(base64.b64decode(encode(_fuse(run).slab)))
        except SlabError:
            return False
        # Registration markers are added to every chunk *after* packing, so
        # the run measured here is one placement short of the slab that will
        # actually be written. Leaving no room for it put a chunk three bytes
        # over the limit and failed the export outright.
        return size <= MAX_COMPRESSED_BYTES - _REGISTRATION_MARGIN

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
    covers = tuple(c for ch in run for c in (ch.covers or ((ch.row, ch.col),)))
    return SlabChunk(
        row=first.row, col=first.col,
        quad=f"+{len(run) - 1}",
        x0=x0, z0=z0, x1=x1, z1=z1, slab=Slab(placements), open_country=False,
        covers=covers,
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
    #: Grid cells this chunk covers. One cell normally; packing
    #: fuses many, and the map must still mark all of them.
    covers: tuple[tuple[int, int], ...] = ()

    @property
    def label(self) -> str:
        """Region name, e.g. ``r02c03`` -- or ``r02c03ne`` once subdivided."""
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


def _subdivide(cell: _Cell, max_assets: int) -> list[_Cell]:
    """Halve a cell until each piece holds at most ``max_assets`` placements.

    Splitting is quadtree-style: both axes at once where both span more than a
    tile, one axis where only one does. A piece already down to a single tile
    is returned as-is even if it is still over budget -- there is nowhere left
    to cut, and the encoder refuses that slab with a clearer message than an
    endless subdivision would give.
    """
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
                    if (len(xs) == 1 or (p.x < x1 if xi == 0 else p.x >= x0))
                    and (len(zs) == 1 or (p.z < z1 if zi == 0 else p.z >= z0))
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

def build_interior(
    floorplan,
    palette: Palette,
    *,
    seed: int = 0,
    roof: bool = False,
    prop_density: float = 0.12,
) -> Builder:
    """Build a playable interior from a :class:`~citysmith.floorplan.Floorplan`.

    The roof is off by default: a covered interior is nearly unusable at the
    table because the camera cannot see in.
    """
    b = Builder(palette, seed)
    rect = floorplan.rect

    floor = palette.require("floor")
    upper = palette.resolve("floor_upper") or floor
    ext_wall = palette.require("wall")
    int_wall = palette.resolve("wall_interior") or ext_wall
    door_asset = palette.resolve("door")
    stair_asset = palette.resolve("stairs")

    storey_h = ext_wall.size_y
    level_base: list[float] = []

    for level in range(floorplan.levels):
        slab_asset = floor if level == 0 else upper
        base = level * (storey_h + slab_asset.size_y)
        level_base.append(base)

        for tx, tz in rect.tiles():
            b.add(place_tile(slab_asset, tx, tz, base))
        wall_y = base + slab_asset.size_y

        doors = {(d.x, d.z, d.side) for d in floorplan.doors if d.level == level}

        # Exterior shell.
        for tx, tz in rect.tiles():
            for side, present in (
                ("n", tz == rect.z), ("s", tz == rect.z2 - 1),
                ("w", tx == rect.x), ("e", tx == rect.x2 - 1),
            ):
                if not present:
                    continue
                if (tx, tz, side) in doors:
                    if door_asset is not None:
                        b.add(place_wall(door_asset, tx, tz, side, wall_y))
                    continue
                b.add(place_wall(ext_wall, tx, tz, side, wall_y))

        # Interior partitions on shared room edges, skipping doorways.
        for wall_cell in _interior_walls(floorplan, level):
            tx, tz, side = wall_cell
            if (tx, tz, side) in doors:
                if door_asset is not None:
                    b.add(place_wall(door_asset, tx, tz, side, wall_y))
                continue
            b.add(place_wall(int_wall, tx, tz, side, wall_y))

        # Dress rooms with props.
        _dress(b, floorplan, level, wall_y, prop_density)

    for stair in floorplan.stairs:
        if stair_asset is None:
            break
        y = level_base[stair.from_level] + floor.size_y
        b.add(place_tile(stair_asset, stair.x, stair.z, y))

    if roof:
        roof_asset = palette.resolve("roof")
        if roof_asset is not None:
            top = level_base[-1] + storey_h + floor.size_y
            for tx, tz in rect.tiles():
                b.add(place_tile(roof_asset, tx, tz, top))

    return b


def _interior_walls(floorplan, level: int) -> set[tuple[int, int, str]]:
    """Cells+sides where two rooms meet, deduplicated so no wall is doubled."""
    rooms = floorplan.rooms_on(level)
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
                    (side == "n" and tz == floorplan.rect.z)
                    or (side == "s" and tz == floorplan.rect.z2 - 1)
                    or (side == "w" and tx == floorplan.rect.x)
                    or (side == "e" and tx == floorplan.rect.x2 - 1)
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


def _dress(b: Builder, floorplan, level: int, y: float, density: float) -> None:
    """Scatter props inside rooms, keeping the middle of small rooms clear."""
    for room in floorplan.rooms_on(level):
        category = _PROP_CATEGORY.get(room.purpose, _PROP_CATEGORY_BY_KIND.get(floorplan.kind, "house"))
        cells = [(tx, tz) for tx, tz in room.rect.tiles()]
        if not cells:
            continue
        count = max(1, int(len(cells) * density))
        # Prefer cells against a wall so the floor stays playable.
        edge_cells = [
            (tx, tz) for tx, tz in cells
            if tx in (room.rect.x, room.rect.x2 - 1) or tz in (room.rect.z, room.rect.z2 - 1)
        ] or cells
        b.rng.shuffle(edge_cells)
        for tx, tz in edge_cells[:count]:
            b.prop(category, tx + 0.5, tz + 0.5, y)


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
_BLOCK_SURFACES = {"ground": "ground_2x2", "field": "field"}

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

    depth: dict[tuple[int, int], int] = {}
    frontier: list[tuple[int, int]] = []
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] != R.WATER:
                continue
            if any(not (0 <= x + dx < tm.width and 0 <= z + dz < tm.depth)
                   or tm.surface[z + dz][x + dx] != R.WATER
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
                if tm.surface[n[1]][n[0]] != R.WATER or n in depth:
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


def _lay_terrain(b: Builder, tm, surface_roles: dict[str, str], grade: float,
                 taper: dict[tuple[int, int], float | None]) -> None:
    """Lay the ground plane, preferring 2x2 tiles over 1x1 where it can.

    Open country is most of a map by area and almost all of it by tile count:
    Candlewell spent 29,000 assets on grass alone. Where a 2x2 block is
    uniform, one 2x2 tile replaces four 1x1s for an identical result, so the
    saving is free. Edges and anything mixed fall back to 1x1, which is what
    keeps coastlines and road margins crisp instead of blocky.
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

            role = _BLOCK_SURFACES.get(s or "")
            if role is None or b.palette.resolve(role) is None:
                continue
            if any(surface_at(qx, qz) != s for qx, qz in quad):
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
            if s == R.WATER:
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
            role = surface_roles.get(s, ground_role)
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
        base_floors = storeys_of(tm, bid, ceiling)
        # From base_floors + 1: the building's own ceiling already fills the
        # gap below its top course, and laying another slab there put 36 decks
        # inside each other.
        for level in range(base_floors + 1, base_floors + 1 + TOWER_EXTRA_STOREYS):
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
        top_course = top + (base_floors + TOWER_EXTRA_STOREYS) * storey_h
        crown = top_course + face.size_y
        rings = _roof_rings(cells)
        side_piece = b.palette.resolve("roof_side")
        corner = b.palette.resolve("roof_corner")
        inner = b.palette.resolve("roof_corner_inner")
        flat = b.palette.resolve("roof")
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
            piece, rot = _roof_piece(fall, side_piece, corner, flat,
                                     b.palette.resolve("roof_corner_inner"),
                                     _is_reflex(rings, x, z, fall))
            if piece is not None:
                b.add(place_tile(piece, x, z, crown + (r - 1) * rise, rot))


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


def _lay_roofs(b: Builder, tm, base_y: float, storey_h: float, max_floors: int,
               skip: set[tuple[int, int]] | None = None) -> None:
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

    # Roof units are connected blocks sharing a storey count, so a terrace
    # gets one roof rather than one per party wall.
    seen: set[tuple[int, int]] = set()
    blocks: list[tuple[int, set[tuple[int, int]]]] = []
    for z0 in range(tm.depth):
        for x0 in range(tm.width):
            bid = tm.building[z0][x0]
            if not bid or (x0, z0) in seen or (x0, z0) in skip:
                continue
            fl = _floors_at(bid)
            comp: set[tuple[int, int]] = set()
            stack = [(x0, z0)]
            while stack:
                x, z = stack.pop()
                if (x, z) in seen or not (0 <= x < tm.width and 0 <= z < tm.depth):
                    continue
                nb = tm.building[z][x]
                if not nb or _floors_at(nb) != fl or (x, z) in skip:
                    continue
                seen.add((x, z)); comp.add((x, z))
                stack += [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)]
            if comp:
                blocks.append((fl, comp))

    side = b.palette.resolve("roof_side")
    corner = b.palette.resolve("roof_corner")
    inner = b.palette.resolve("roof_corner_inner")
    cap = b.palette.resolve("roof")
    chimney = b.palette.resolve("roof_chimney")
    rise = side.size_y if side is not None else 1.0

    for fl, cells in sorted(blocks, key=lambda t: min(t[1])):
        roof_y = base_y + fl * storey_h

        # One hip per rectangular wing, not one hip forced over the whole
        # plan. A notched footprint gets a ridge per wing and a valley where
        # they meet, which is what the building would really have and what
        # this kit of 1x1 slopes and corners can actually express.
        wings = roof_wings(cells)
        chimney_wing = max(wings, key=len) if wings else set()

        for wing in wings:
            rings = _roof_rings(wing)
            top_ring = max(rings.values())

            # One chimney per building, on its main wing.
            chimney_at = None
            if chimney is not None and wing is chimney_wing:
                crown = [c for c in sorted(wing) if rings[c] == top_ring]
                if crown:
                    chimney_at = crown[len(crown) // 2]

            for (x, z) in sorted(wing):
                r = rings[(x, z)]
                y = roof_y + r * rise
                if (x, z) == chimney_at and chimney is not None:
                    b.add(place_tile(chimney, x, z, y)); continue
                # Which way the slope falls: the sides where the roof steps
                # back down towards this wing's own eaves.
                fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                             if rings.get((x + dx, z + dz), -1) < r)
                piece, rot = _roof_piece(fall, side, corner, cap, inner,
                                         _is_reflex(rings, x, z, fall))
                if piece is not None:
                    b.add(place_tile(piece, x, z, y, rot))


#: Headroom to leave under a gate lintel, in tiles. Two tiles is 10 ft -- a
#: loaded cart with a rider on top clears it, which is the traffic the main
#: streets were widened for in the first place.
GATE_HEADROOM_TILES = 2.0

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
    mass = {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x]} | set(tm.gates)
    outside = _outside_the_wall(tm, mass)
    towers = _gatehouse_cells(mass, set(tm.gates))

    for (x, z) in sorted(mass):
        gate = (x, z) in tm.gates
        if gate and lintel_from is None:
            continue
        courses = wall_height + (GATEHOUSE_RISE if (x, z) in towers else 0)
        shows = [s for s, dx, dz in SIDE_OFFSETS if (x + dx, z + dz) not in mass]
        for level in range(lintel_from if gate else 0, courses):
            y = top + level * course
            b.add(place_tile(core, x, z, y))

        crown = top + courses * course
        looks_out = [s for s, dx, dz in SIDE_OFFSETS
                     if (x + dx, z + dz) in outside]
        if looks_out and cap_asset is not None:
            if is_curtain_piece(cap_asset) and walk is not None:
                # A parapet stands on the lip, not in place of the walk: pave
                # the cell first and stand the battlement on its outer edge.
                # A cell at a step of the stair looks out on two sides, and
                # both get one -- that is what closes the corner.
                b.add(place_tile(walk, x, z, crown))
                for side in looks_out:
                    b.add(place_wall(cap_asset, x, z, side, crown + walk.size_y))
            else:
                b.add(place_tile(cap_asset, x, z, crown))
        elif walk is not None:
            b.add(place_tile(walk, x, z, crown))


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


def _roof_piece(fall: tuple[str, ...], side, corner, cap, inner=None,
                reflex: bool = False):
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
        return side, ROOF_EDGE_ROT[fall[0]]
    if len(fall) == 2:
        which = CORNER_BY_SIDES.get(frozenset(fall))
        if which is not None:
            if reflex and inner is not None:
                # The inner piece is authored facing into the angle, so it
                # takes the rotation of the corner diagonally opposite.
                return inner, ROOF_CORNER_ROT[_OPPOSITE_CORNER[which]]
            return corner or side, ROOF_CORNER_ROT[which]
        return side, ROOF_EDGE_ROT[fall[0]]   # opposite sides: a ridge run
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


def build_from_tilemap(
    tm,
    palette: Palette,
    *,
    storeys: int = 2,
    roofs: bool = True,
    wall_tiles: float = TOWN_WALL_TILES,
    seed: int = 0,
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
    surface_roles = {
        R.GROUND: "ground",
        # NOTE: R.FIELD maps to the 1x1 fallback here, not the 2x2 "field"
        # block -- pass 2 of _lay_terrain lays one asset per leftover cell,
        # and dropping the 2x2 Tilled Earth on a 1x1 leftover overhangs its
        # neighbours (the jumbled field fringes the design review caught).
        R.FIELD: "field_1x1",
        R.STREET: "street",
        R.PLAZA: "street",
        # A lane is trodden earth, not laid cobble -- that is the whole point
        # of distinguishing it from the street it opens off.
        R.LANE: "lane",
        R.PIER: "street",
        R.FLOOR: "floor",
    }
    taper = edge_taper(tm)
    _lay_terrain(b, tm, surface_roles, grade=floor.size_y, taper=taper)
    _lay_quays(b, tm, grade=floor.size_y, taper=taper)

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
    upper = palette.resolve("floor_upper")
    deck = upper.size_y if upper is not None else 0.0
    storey_h = ext_wall.size_y + deck

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
    wall_variants = [palette.resolve("wall", v) or ext_wall for v in range(3)]

    # Outside corners are full-cell pieces, dealt per building on the same
    # variant index as the wall so a cottage's corners match its own walls.
    # A corner that is not exactly one cell square, or not the same height as
    # the wall it stacks beside, is rejected rather than placed: the first
    # would overhang its neighbours and drag the whole board off the tile grid,
    # the second would break the floor line at every storey above the ground.
    def _usable_corner(asset):
        if asset is None:
            return None
        if (asset.size_x, asset.size_z) != (1.0, 1.0):
            return None
        if abs(asset.size_y - ext_wall.size_y) > 1e-6:
            return None
        return asset

    corner_variants = [_usable_corner(palette.resolve("wall_corner", v)) for v in range(3)]
    civic_corner = _usable_corner(palette.resolve("wall_corner_civic"))

    plan = footprints(tm)
    corner_ok = {
        bid: _corners_affordable(cells) for bid, cells in plan.items()
    }

    for bid, cells in tm.perimeter.items():
        floors = storeys_of(tm, bid, storeys)
        civic = bid.split("-")[0] in CIVIC_KINDS
        if civic:
            # Fall back to the common-house piece per slot: a style with no
            # civic kit (cyberpunk has none) otherwise gets entry=None, and the
            # door branch below silently lays a solid wall across the doorway
            # -- a temple with no way in, while verify still reports it
            # enterable because verify reads the tilemap, not the placements.
            face = civic_wall or ext_wall
            glass, entry = civic_window or window, civic_door or door_asset
            nook = civic_corner
        else:
            variant = zlib.crc32(bid.encode()) % len(wall_variants)
            face = wall_variants[variant]
            glass, entry = window, door_asset
            nook = corner_variants[variant]
        if not corner_ok.get(bid, True):
            nook = None   # too small to spend cells on corners

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
        for (x, z), exposed in sides_at.items():
            corner = CORNER_BY_SIDES.get(frozenset(exposed))
            # Same reflex problem as the roof: at the elbow of an L the wall
            # turns into the building, so a full-cell outside corner there
            # looks wrong and eats a floor tile the plan needs.
            if corner is not None and _is_reflex(
                    {c: 0 for c in own}, x, z, tuple(sorted(exposed))):
                corner = None
            # A door has to keep a segment of its own, so a corner cell
            # carrying one falls back to per-side walls for the ground course
            # only; the storeys above it still get the corner piece.
            door_cell = any((x, z, s) in doors for s in exposed)
            for level in range(floors):
                y = top + level * storey_h
                if corner is not None and nook is not None and not (level == 0 and door_cell):
                    b.add(place_tile(nook, x, z, y, WALL_CORNER_ROT[corner]))
                    continue
                for side in sorted(exposed):
                    if level == 0 and (x, z, side) in doors and entry is not None:
                        b.add(place_wall(entry, x, z, side, y))
                        continue
                    # Windows break the blank masonry that made every facade
                    # read as a fortification. Roughly every third segment,
                    # chosen by a stable hash so rebuilds are identical; ground
                    # floors get fewer (privacy, and doors already break those
                    # runs). zlib.crc32, not hash(): str hashes are salted per
                    # process, so hash() would re-deal windows every rebuild.
                    key = zlib.crc32(f"{bid}:{x}:{z}:{level}:{side}".encode())
                    seg = glass is not None and key % (4 if level == 0 else 3) == 0
                    b.add(place_wall(glass if seg else face, x, z, side, y))

    # Upper-storey floors. Without these a multi-storey building is a hollow
    # box, and now that facades carry windows you can see straight through one
    # to the underside of the roof. One slab per cell per storey above ground.
    if upper is not None:
        for bid, cells_xy in sorted(plan.items()):
            # Through the top storey, not up to it: the highest slab is the
            # ceiling the roof seats on. Each sits in the gap *below* its
            # storey's wall course, resting on the wall beneath.
            for level in range(1, storeys_of(tm, bid, storeys) + 1):
                y = top + level * storey_h - deck
                for x, z in sorted(cells_xy):
                    b.add(place_tile(upper, x, z, y))

    _build_porches(b, tm, floor.size_y, taper, storey_h)
    towers = pick_towers(tm, storeys)
    if roof_asset is not None:
        _lay_roofs(b, tm, top, storey_h, storeys, skip=set(towers))
    _lay_towers(b, tm, towers, civic_wall or ext_wall, top, storey_h, storeys)

    _lay_town_wall(b, tm, town_wall, top, wall_tiles)

    _dress_districts(b, tm, grade=floor.size_y, taper=taper)

    return b


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

    def __init__(self, builder: Builder):
        self.b = builder
        self._at: dict[tuple[int, int], list[tuple[float, ...]]] = {}
        self.rejected = 0

    @staticmethod
    def box(asset: Asset, cx: float, cz: float, y: float, rot: int
            ) -> tuple[float, ...]:
        # (cx, cz) is where the collider centre will end up, so the box is
        # simply centred there -- see place_centered.
        sx, sz = rotated_footprint(asset, rot)
        return (cx - sx / 2, cz - sz / 2, y,
                cx + sx / 2, cz + sz / 2, y + asset.size_y)

    def _clear(self, box: tuple[float, ...]) -> bool:
        e = 1e-6
        for cx in range(int(math.floor(box[0])), int(math.ceil(box[3])) + 1):
            for cz in range(int(math.floor(box[1])), int(math.ceil(box[4])) + 1):
                for o in self._at.get((cx, cz), ()):
                    if (box[0] < o[3] - e and o[0] < box[3] - e
                            and box[1] < o[4] - e and o[1] < box[4] - e
                            and box[2] < o[5] - e and o[2] < box[5] - e):
                        return False
        return True

    def _record(self, box: tuple[float, ...]) -> None:
        for cx in range(int(math.floor(box[0])), int(math.ceil(box[3])) + 1):
            for cz in range(int(math.floor(box[1])), int(math.ceil(box[4])) + 1):
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
    """Cells out from the nearest building, breadth-first, capped at ``limit``.

    Density of anything scattered should fall off near the built-up area:
    that gradient is what makes a settlement look like it was cleared, and
    its absence is what made the woodland grow up to the doorsteps.
    """
    dist: dict[tuple[int, int], int] = {}
    frontier: list[tuple[int, int]] = []
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.building[z][x]:
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
                   storey_h: float) -> int:
    """Roof the cell outside the primary door of each public building.

    Every building is otherwise one flat-topped mass, and an entrance reads
    as a hole punched in a wall. The porch sits high enough to clear the
    signs hung on the same facade -- they occupy up to 2.65, so anything
    lower would have its sign silently dropped for overlapping it.
    """
    piece = b.palette.resolve("roof_side")
    if piece is None:
        return 0
    built = 0
    for bid, doors in sorted(tm.doors.items()):
        if bid.split("-")[0] not in PORCHED_KINDS or not doors:
            continue
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
                         ROOF_EDGE_ROT[side]))
        built += 1
    return built


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
                     taper: dict[tuple[int, int], float | None]) -> None:
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

    _hang_signs(b, tm, scatter, grade, taper)
    near_town = building_distance(tm)
    market = [b.palette.resolve("market_goods", v) for v in range(4)]
    market = [m for m in market if m is not None]
    yard = [b.palette.resolve("yard_clutter", v) for v in range(4)]
    yard = [y for y in yard if y is not None]

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
                roll = rng.random()
                if roll < 0.10 and wheat is not None:
                    scatter.one(wheat, x + 0.5, z + 0.5, here, rng.randrange(24))
                elif roll < 0.12 and straw is not None:
                    scatter.one(straw, x + 0.5, z + 0.5, here, rng.randrange(24))

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

            elif surf == R.PLAZA:
                # A square with nothing on it is worse than no square. Goods
                # cluster loosely, leaving room in the middle for the crowd --
                # and for whatever the party is about to do in it.
                if market and rng.random() < 0.16:
                    scatter.one(market[rng.randrange(len(market))],
                                x + 0.5 + rng.uniform(-0.2, 0.2),
                                z + 0.5 + rng.uniform(-0.2, 0.2),
                                here, rng.randrange(24))
                elif well is not None and not plaza_dressed and rng.random() < 0.06:
                    if scatter.one(well, x + 0.5, z + 0.5, here, rng.randrange(24)):
                        plaza_dressed = True

            elif surf == R.LANE:
                # Lanes are where things get left, sparsely and against a wall.
                if yard and rng.random() < 0.07:
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
                elif roll < 0.30 and barrels is not None:
                    pick = cart if rng.random() < 0.4 and cart else barrels
                    scatter.one(pick, x + 0.5, z + 0.5, here, rng.randrange(24))

    _stack_trade_goods(b, tm, scatter, rng, grade, taper)
    # Last, so it can see everything already standing and not fight it.
    _dress_seams(b, tm, scatter, rng, grade, taper)


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
