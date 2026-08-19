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

import collections
import math
import random
import zlib
from dataclasses import dataclass, field

from .catalog import Asset
from .city import Building, City, Rect
from .palette import Palette
from .slab import Placement, Slab

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


def rotated_footprint(asset: Asset, rot: int) -> tuple[float, float]:
    """The asset's ground footprint after ``rot``, as ``(size_x, size_z)``.

    Odd quarter turns swap the axes; even ones leave them alone.
    """
    if ((rot // _QUARTER) % 4) % 2:
        return (asset.size_z, asset.size_x)
    return (asset.size_x, asset.size_z)


def place_centered(asset: Asset, cx: float, cz: float, y: float, rot: int) -> Placement:
    """Place ``asset`` so its *rotated* footprint is centred on ``(cx, cz)``.

    The stored coordinate is the min corner of the rotated box -- see the
    module docstring for the in-game measurements this reproduces.
    """
    sx, sz = rotated_footprint(asset, rot)
    return Placement(asset.id, cx - sx / 2, y, cz - sz / 2, rot)


def place_tile(asset: Asset, tx: int, tz: int, y: float = 0.0, rot: int = 0) -> Placement:
    """Place a tile so it fills the grid cell whose min corner is ``(tx, tz)``."""
    return place_centered(asset, tx + asset.size_x / 2, tz + asset.size_z / 2, y, rot)


def place_wall(asset: Asset, tx: int, tz: int, side: str, y: float = 0.0) -> Placement:
    """Place a wall along one edge of grid cell ``(tx, tz)``.

    The wall's thin axis is inset to sit exactly on the cell boundary, so two
    buildings sharing a lot line do not produce overlapping geometry.
    """
    thickness = min(asset.size_x, asset.size_z)
    if side == "n":
        return place_centered(asset, tx + 0.5, tz + thickness / 2, y, ROT_N)
    if side == "s":
        return place_centered(asset, tx + 0.5, tz + 1 - thickness / 2, y, ROT_S)
    if side == "w":
        return place_centered(asset, tx + thickness / 2, tz + 0.5, y, ROT_W)
    if side == "e":
        return place_centered(asset, tx + 1 - thickness / 2, tz + 0.5, y, ROT_E)
    raise ValueError(f"side must be one of {SIDES}, got {side!r}")


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
        return _normalized_whole_tiles(Slab(list(self.placements)))

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
        """
        raw = Slab(list(self.placements))
        size = max(1, int(chunk_tiles))
        if not raw.placements:
            return ChunkPlan([], [], 0, 0, size, (0, 0))

        dx, dy, dz = _whole_tile_shift(raw)
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
        made = [
            SlabChunk(
                row=cell.row, col=cell.col, quad=cell.quad,
                x0=cell.x0 - dx, z0=cell.z0 - dz,
                x1=cell.x1 - dx, z1=cell.z1 - dz,
                slab=Slab(cell.items),
                open_country=_is_open_country(
                    cell.items, terrain, self.prop_ids, grade),
            )
            for cell in cells
        ]

        kept = [ch for ch in made if not ch.open_country]
        skipped = [ch for ch in made if ch.open_country]
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
            marker = min(slab.placements, key=lambda p: (p.y, p.z, p.x))
            anchor = Placement(marker.asset_id, 0.0, 0.0, 0.0, 0)
            for piece in kept:
                (mx, my, mz), _ = piece.slab.bounds()
                if (mx, my, mz) != (0.0, 0.0, 0.0):
                    piece.slab.add(anchor)
            self.stats.registration_markers = sum(
                1 for piece in kept if any(
                    (p.x, p.y, p.z) == (0.0, 0.0, 0.0)
                    for p in piece.slab.placements
                )
            )

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


def _pack_chunks(chunks: list["SlabChunk"], max_assets: int, cols: int) -> list["SlabChunk"]:
    """Merge adjacent chunks up to ``max_assets`` so fewer slabs are pasted.

    Chunks arrive at detection resolution, which is deliberately fine. Each
    output slab is still a contiguous run of neighbours, so pasting a subset
    gives a coherent region rather than scattered fragments.
    """
    if not chunks:
        return chunks

    def key(ch: "SlabChunk") -> tuple[int, int]:
        row, col = ch.row, ch.col
        return (row, -col if row % 2 else col)   # serpentine

    ordered = sorted(chunks, key=key)
    out: list["SlabChunk"] = []
    run: list["SlabChunk"] = []
    total = 0
    for ch in ordered:
        if run and total + ch.count > max_assets:
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


def _is_open_country(
    items: list[Placement], terrain: set[str], props: set[str],
    grade: float | None,
) -> bool:
    """True when a chunk holds only ground at grade and scatter dressing.

    Ferns and pines count as dressing, not as features: a stand of trees on
    open grass is still ground nobody stands on. Anything built -- a floor, a
    wall, a street, a tilled field, water -- disqualifies the chunk at once.
    """
    if grade is None:
        return False
    for p in items:
        if p.asset_id in terrain:
            if p.y != grade:
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


def _whole_tile_shift(slab: Slab) -> tuple[int, int, int]:
    """The whole-tile translation that brings ``slab`` to the origin."""
    if not slab.placements:
        return (0, 0, 0)
    (mx, my, mz), _ = slab.bounds()
    return (-math.floor(mx), -math.floor(my), -math.floor(mz))


def _normalized_whole_tiles(slab: Slab) -> Slab:
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
    return slab.translated(*_whole_tile_shift(slab))


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


def _lay_terrain(b: Builder, tm, surface_roles: dict[str, str]) -> None:
    """Lay the ground plane, preferring 2x2 tiles over 1x1 where it can.

    Open country is most of a map by area and almost all of it by tile count:
    Candlewell spent 29,000 assets on grass alone. Where a 2x2 block is
    uniform, one 2x2 tile replaces four 1x1s for an identical result, so the
    saving is free. Edges and anything mixed fall back to 1x1, which is what
    keeps coastlines and road margins crisp instead of blocky.
    """
    from . import raster as R

    covered: set[tuple[int, int]] = set()

    def surface_at(x: int, z: int) -> str | None:
        if not (0 <= x < tm.width and 0 <= z < tm.depth):
            return None
        return tm.surface[z][x]

    # Pass 1: 2x2 blocks on an even grid, only where all four cells agree.
    for z in range(0, tm.depth - 1, 2):
        for x in range(0, tm.width - 1, 2):
            s = surface_at(x, z)
            role = _BLOCK_SURFACES.get(s or "")
            if role is None or b.palette.resolve(role) is None:
                continue
            quad = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
            if any(surface_at(qx, qz) != s for qx, qz in quad):
                continue
            if any(tm.building[qz][qx] for qx, qz in quad):
                continue
            b.tile(role, x, z, 0.0)
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
            if s == R.WATER:
                # Water sits a tile low so it reads as a channel a creature can
                # be pulled into, not a hole punched through the board.
                b.tile(ground_role, x, z, -1.0)
                water = b.palette.resolve("water")
                if water is not None:
                    b.add(place_tile(water, x, z, -1.0 + water.size_y))
                continue
            b.tile(surface_roles.get(s, ground_role), x, z, 0.0)


def _lay_roofs(b: Builder, tm, base_y: float, storey_h: float, max_floors: int) -> None:
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
    footprints: dict[str, set[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                footprints.setdefault(bid, set()).add((x, z))

    def _floors_at(bid: str) -> int:
        return min(max(1, tm.floors.get(bid, 1)), max_floors)

    # Roof units are connected blocks sharing a storey count, so a terrace
    # gets one roof rather than one per party wall.
    seen: set[tuple[int, int]] = set()
    blocks: list[tuple[int, set[tuple[int, int]]]] = []
    for z0 in range(tm.depth):
        for x0 in range(tm.width):
            bid = tm.building[z0][x0]
            if not bid or (x0, z0) in seen:
                continue
            fl = _floors_at(bid)
            comp: set[tuple[int, int]] = set()
            stack = [(x0, z0)]
            while stack:
                x, z = stack.pop()
                if (x, z) in seen or not (0 <= x < tm.width and 0 <= z < tm.depth):
                    continue
                nb = tm.building[z][x]
                if not nb or _floors_at(nb) != fl:
                    continue
                seen.add((x, z)); comp.add((x, z))
                stack += [(x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)]
            if comp:
                blocks.append((fl, comp))

    side = b.palette.resolve("roof_side")
    corner = b.palette.resolve("roof_corner")
    cap = b.palette.resolve("roof")
    chimney = b.palette.resolve("roof_chimney")
    rise = side.size_y if side is not None else 1.0

    for fl, cells in sorted(blocks, key=lambda t: min(t[1])):
        roof_y = base_y + fl * storey_h
        xs = [x for x, _ in cells]; zs = [z for _, z in cells]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)

        rings = {c: min(c[0] - x0, x1 - c[0], c[1] - z0, z1 - c[1]) for c in cells}
        top_ring = max(rings.values())

        chimney_at = None
        if chimney is not None:
            crown = [c for c in sorted(cells) if rings[c] == top_ring]
            if crown:
                chimney_at = crown[len(crown) // 2]

        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            y = roof_y + r * rise
            if (x, z) == chimney_at and chimney is not None:
                b.add(place_tile(chimney, x, z, y)); continue
            if r == top_ring and cap is not None:
                b.add(place_tile(cap, x, z, y)); continue
            n, e, sth, w = (z - z0 == r), (x1 - x == r), (z1 - z == r), (x - x0 == r)
            piece, rot = side, ROOF_EDGE_ROT["n"]
            if n and w:   piece, rot = corner, ROOF_CORNER_ROT["nw"]
            elif n and e: piece, rot = corner, ROOF_CORNER_ROT["ne"]
            elif sth and w: piece, rot = corner, ROOF_CORNER_ROT["sw"]
            elif sth and e: piece, rot = corner, ROOF_CORNER_ROT["se"]
            elif n:   rot = ROOF_EDGE_ROT["n"]
            elif e:   rot = ROOF_EDGE_ROT["e"]
            elif sth: rot = ROOF_EDGE_ROT["s"]
            elif w:   rot = ROOF_EDGE_ROT["w"]
            if piece is None:
                piece, rot = cap, 0
            if piece is not None:
                b.add(place_tile(piece, x, z, y, rot))


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

#: Neighbour offsets in the same order as :data:`SIDES`.
SIDE_OFFSETS = (("n", 0, -1), ("e", 1, 0), ("s", 0, 1), ("w", -1, 0))


def build_from_tilemap(
    tm,
    palette: Palette,
    *,
    storeys: int = 2,
    roofs: bool = True,
    wall_height: int = 3,
    seed: int = 0,
) -> Builder:
    """Build a TaleSpire city board from a rasterised :class:`~citysmith.raster.TileMap`.

    Surfaces map to palette roles, building footprints get a perimeter shell
    with a doorway, and the town wall is stacked to ``wall_height``. Water sits
    one tile below grade so it reads as a channel a creature can be pulled into
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
        R.PIER: "street",
        R.FLOOR: "floor",
    }
    _lay_terrain(b, tm, surface_roles)

    top = floor.size_y
    storey_h = ext_wall.size_y

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

    # Full-cell corner pieces cost a whole tile each. On a 2x3 footprint that
    # is four of six cells, leaving two -- not a room. Where cornering would
    # leave fewer than this many usable interior tiles, fall back to thin edge
    # walls, which sit on the cell boundary and consume no floor.
    MIN_USABLE_INTERIOR = 4
    _fp: dict[str, list[tuple[int, int]]] = {}
    for _z in range(tm.depth):
        for _x in range(tm.width):
            _b = tm.building[_z][_x]
            if _b:
                _fp.setdefault(_b, []).append((_x, _z))
    _corner_ok: dict[str, bool] = {}
    for _b, _cs in _fp.items():
        _xs = [c[0] for c in _cs]; _zs = [c[1] for c in _cs]
        _c = sum(1 for (x, z) in _cs
                 if x in (min(_xs), max(_xs)) and z in (min(_zs), max(_zs)))
        _corner_ok[_b] = (len(_cs) - _c) >= MIN_USABLE_INTERIOR

    for bid, cells in tm.perimeter.items():
        floors = min(max(1, tm.floors.get(bid, 1)), storeys)
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
        if not _corner_ok.get(bid, True):
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

        for (x, z), exposed in sides_at.items():
            corner = CORNER_BY_SIDES.get(frozenset(exposed))
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
    upper = palette.resolve("floor_upper")
    if upper is not None:
        footprint: dict[str, list[tuple[int, int]]] = {}
        for z in range(tm.depth):
            for x in range(tm.width):
                bid = tm.building[z][x]
                if bid:
                    footprint.setdefault(bid, []).append((x, z))
        for bid, cells_xy in sorted(footprint.items()):
            floors = min(max(1, tm.floors.get(bid, 1)), storeys)
            for level in range(1, floors):
                y = top + level * storey_h
                for x, z in cells_xy:
                    b.add(place_tile(upper, x, z, y))

    if roof_asset is not None:
        _lay_roofs(b, tm, top, storey_h, storeys)

    # Town wall: stacked, with gate cells left open, finished with a half-
    # height parapet course so the wall top reads as a battlement rather than
    # a sheared-off stack.
    cap = b.palette.catalog.find(name="castle wall 1x1 half", pack="Medieval Fantasy")
    cap_asset = cap[0] if cap else None  # absent pack -> no cap, harmless
    for z in range(tm.depth):
        for x in range(tm.width):
            if not tm.wall[z][x] or (x, z) in tm.gates:
                continue
            for level in range(wall_height):
                b.add(place_tile(town_wall, x, z, top + level * town_wall.size_y))
            if cap_asset is not None:
                b.add(place_tile(cap_asset, x, z, top + wall_height * town_wall.size_y))

    _dress_districts(b, tm)

    return b


def _dress_districts(b: Builder, tm) -> None:
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

            if surf == R.FIELD:
                roll = rng.random()
                if roll < 0.10 and wheat is not None:
                    b.add(place_centered(wheat, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)
                elif roll < 0.12 and straw is not None:
                    b.add(place_centered(straw, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)

            elif surf == R.GROUND and not near(x, z, frozenset({R.STREET, R.PLAZA})):
                roll = rng.random()
                if roll < 0.04 and pine_top is not None:
                    # One piece, not a stump+canopy stack: TaleSpire's paste
                    # drops props whose colliders overlap (the community's
                    # "copy paste: missing parts" bug), which decapitated a
                    # third of the forest. The canopy piece alone reads as a
                    # full pine at ground level -- verified by probe paste.
                    jx, jz = rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25)
                    b.add(place_centered(pine_top, x + 0.5 + jx, z + 0.5 + jz,
                                         0.5, rng.randrange(24)), prop=True)
                elif roll < 0.045 and pine_stump is not None:
                    # The stump stands alone as an occasional cut tree.
                    b.add(place_centered(pine_stump, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)
                elif roll < 0.07 and fern_small is not None:
                    fern = fern_big if rng.random() < 0.3 and fern_big else fern_small
                    b.add(place_centered(fern, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)

            elif surf in (R.PLAZA, R.STREET):
                # This export has no plaza cells (MFCG's squares came through
                # empty), so market clutter leans against buildings along the
                # streets instead: barrels and carts where a street cell
                # touches a wall, and one well at the busiest such spot.
                if not near(x, z, frozenset()):  # building adjacency only
                    continue
                roll = rng.random()
                if not plaza_dressed and well is not None and surf == R.STREET                         and roll < 0.02:
                    b.add(place_centered(well, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)
                    plaza_dressed = True
                elif roll < 0.025 and barrels is not None:
                    pick = cart if rng.random() < 0.4 and cart else barrels
                    b.add(place_centered(pick, x + 0.5, z + 0.5, 0.5,
                                         rng.randrange(24)), prop=True)


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
