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

    def add(self, placement: Placement, *, prop: bool = False) -> None:
        self.placements.append(placement)
        if prop:
            self.stats.props += 1
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

    def to_slabs(self, max_assets: int = 4000, *, register: bool = True) -> list[Slab]:
        """Split into chunks that each encode within TaleSpire's size limit.

        Chunks are cut spatially (by z then x band) so each slab is a
        contiguous piece of the map.

        **Registration.** TaleSpire anchors a pasted slab by its own bounding
        box, not by the absolute coordinates inside it -- copying a placed slab
        back out returns it normalised to its own corner. Chunks therefore have
        different corners (each covers a different z band), and pasting them all
        at one anchor would stack them on top of each other instead of
        assembling the map.

        To make the pieces line up regardless, every chunk gets one extra tile
        at the *whole map's* minimum corner. That gives all chunks an identical
        bounding box origin, so pasting each at the same point lands every tile
        where it belongs. The markers stack at a single corner cell and can be
        deleted afterwards; they are harmless if TaleSpire turns out to preserve
        absolute coordinates instead.
        """
        slab = _normalized_whole_tiles(Slab(list(self.placements)))
        if not slab.placements:
            return []

        chunks = _chunk_spatially(slab.placements, max_assets)
        out: list[Slab] = [Slab(chunk) for chunk in chunks]

        if register and len(out) > 1:
            marker = min(slab.placements, key=lambda p: (p.y, p.z, p.x))
            anchor = Placement(marker.asset_id, 0.0, 0.0, 0.0, 0)
            for piece in out:
                (mx, my, mz), _ = piece.bounds()
                if (mx, my, mz) != (0.0, 0.0, 0.0):
                    piece.add(anchor)
            self.stats.registration_markers = sum(
                1 for piece in out if any(
                    (p.x, p.y, p.z) == (0.0, 0.0, 0.0) for p in piece.placements
                )
            )

        self.stats.slabs = len(out)
        return out


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
    (mx, my, mz), _ = slab.bounds()
    import math
    return slab.translated(-math.floor(mx), -math.floor(my), -math.floor(mz))


def _chunk_spatially(placements: list[Placement], max_assets: int) -> list[list[Placement]]:
    """Group placements into spatial bands of at most ``max_assets`` each."""
    if len(placements) <= max_assets:
        return [placements]

    ordered = sorted(placements, key=lambda p: (p.z, p.x))
    chunks: list[list[Placement]] = []
    current: list[Placement] = []
    for p in ordered:
        current.append(p)
        if len(current) >= max_assets:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


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
