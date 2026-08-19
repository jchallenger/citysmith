"""Convert a polygonal :class:`~citysmith.layout.Layout` into a tile grid.

This is the bridge between the two coordinate worlds: MFCG produces arbitrary
polygons, TaleSpire wants axis-aligned 5 ft squares. Everything downstream --
asset placement and verification alike -- works from the :class:`TileMap` this
produces, so both see exactly the same geometry.

Rasterising is where playability is won or lost. A street that rounds down to a
single tile is impassable abreast; a building whose walls round away has no
inside. The rules here are therefore biased toward preserving *openness*:
streets round up, and a building keeps at least its perimeter.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .layout import TILE_FEET, Layout, Point, open_ring

#: Surface classes a cell can hold. Order matters where they compete.
VOID = "void"
GROUND = "ground"
FIELD = "field"
WATER = "water"
STREET = "street"
PLAZA = "plaza"
PIER = "pier"
FLOOR = "floor"

#: Surfaces a creature can stand and walk on.
WALKABLE = frozenset({GROUND, FIELD, STREET, PLAZA, PIER, FLOOR})

#: Surfaces that count as public open space for door placement and routing.
OPEN = frozenset({GROUND, STREET, PLAZA, PIER})

SIDES = (("n", 0, -1), ("s", 0, 1), ("w", -1, 0), ("e", 1, 0))

#: A creature occupies one tile, so a street must be two wide to be walked
#: abreast. Streets narrower than this are widened when rasterised.
MIN_STREET_TILES = 2.0


@dataclass
class TileMap:
    """A rasterised city: one record per 5 ft square."""

    width: int
    depth: int
    name: str = ""
    surface: list[list[str]] = field(default_factory=list)
    building: list[list[str]] = field(default_factory=list)
    wall: list[list[bool]] = field(default_factory=list)
    gates: set[tuple[int, int]] = field(default_factory=set)
    #: building id -> list of (x, z, side) perimeter cells that are doorways.
    doors: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    #: building id -> perimeter cells as (x, z, side).
    perimeter: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    #: building id -> storeys above ground. Carried through from the layout so
    #: the builder can raise walls per building instead of stamping every
    #: structure to one height, which turns cottages into towers.
    floors: dict[str, int] = field(default_factory=dict)
    #: Bridges added to reconnect districts split by water: (x0, z0, x1, z1).
    bridges: list[tuple[int, int, int, int]] = field(default_factory=list)

    @classmethod
    def blank(cls, width: int, depth: int, name: str = "") -> "TileMap":
        return cls(
            width=width, depth=depth, name=name,
            surface=[[GROUND] * width for _ in range(depth)],
            building=[[""] * width for _ in range(depth)],
            wall=[[False] * width for _ in range(depth)],
        )

    def inside(self, x: int, z: int) -> bool:
        return 0 <= x < self.width and 0 <= z < self.depth

    def is_walkable(self, x: int, z: int) -> bool:
        """Open ground a creature can occupy -- excludes wall and building cells."""
        if not self.inside(x, z):
            return False
        if self.wall[z][x] and (x, z) not in self.gates:
            return False
        return self.surface[z][x] in OPEN

    def crop(self, x0: int, z0: int, width: int, depth: int) -> "TileMap":
        """Return a rectangular region as its own map.

        Used to stage a rollout: pasting one district into TaleSpire proves the
        placement conventions before committing to a whole city's worth of
        assets. Perimeters and doors are recomputed, so a building sliced by the
        crop boundary still gets a wall along the cut rather than an open edge.
        """
        x0 = max(0, min(x0, self.width - 1))
        z0 = max(0, min(z0, self.depth - 1))
        width = max(1, min(width, self.width - x0))
        depth = max(1, min(depth, self.depth - z0))

        out = TileMap.blank(width, depth, f"{self.name} (crop)")
        for z in range(depth):
            for x in range(width):
                out.surface[z][x] = self.surface[z0 + z][x0 + x]
                out.building[z][x] = self.building[z0 + z][x0 + x]
                out.wall[z][x] = self.wall[z0 + z][x0 + x]
        out.gates = {
            (x - x0, z - z0) for x, z in self.gates
            if x0 <= x < x0 + width and z0 <= z < z0 + depth
        }
        out.bridges = [
            (a - x0, b - z0, c - x0, d - z0) for a, b, c, d in self.bridges
            if x0 <= a < x0 + width and z0 <= b < z0 + depth
        ]
        _find_perimeters(out, None)
        _place_doors(out, None)
        return out

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.surface:
            for s in row:
                out[s] = out.get(s, 0) + 1
        return out

    @property
    def width_feet(self) -> float:
        return self.width * TILE_FEET

    @property
    def depth_feet(self) -> float:
        return self.depth * TILE_FEET

    def summary(self) -> str:
        c = self.counts()
        parts = ", ".join(f"{v} {k}" for k, v in sorted(c.items(), key=lambda kv: -kv[1]))
        walls = sum(1 for row in self.wall for v in row if v)
        return (
            f"{self.name}: {self.width}x{self.depth} tiles "
            f"({self.width_feet:.0f}x{self.depth_feet:.0f} ft)\n"
            f"  surfaces: {parts}\n"
            f"  {walls} wall cells, {len(self.gates)} gate cells, "
            f"{len(self.doors)} buildings with doors"
        )


# -- scan conversion ----------------------------------------------------------

def _fill_polygon(ring: list[Point], width: int, depth: int) -> list[tuple[int, int]]:
    """Scanline fill, sampling at cell centres.

    Sampling centres (rather than corners) means a cell belongs to whichever
    polygon actually covers the square a creature would stand on.
    """
    pts = open_ring(ring)
    if len(pts) < 3:
        return []
    ys = [p[1] for p in pts]
    z0 = max(0, int(math.floor(min(ys))))
    z1 = min(depth - 1, int(math.ceil(max(ys))))
    cells: list[tuple[int, int]] = []
    n = len(pts)
    for z in range(z0, z1 + 1):
        cy = z + 0.5
        xs: list[float] = []
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            if (ay > cy) == (by > cy):
                continue
            t = (cy - ay) / (by - ay)
            xs.append(ax + t * (bx - ax))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            sx = max(0, int(math.floor(xs[i] - 0.5)) + 1)
            ex = min(width - 1, int(math.ceil(xs[i + 1] - 0.5)) - 1)
            # Never let a thin polygon vanish: keep at least one cell.
            if ex < sx:
                mid = int(round((xs[i] + xs[i + 1]) / 2 - 0.5))
                if 0 <= mid < width:
                    cells.append((mid, z))
                continue
            for x in range(sx, ex + 1):
                cells.append((x, z))
    return cells


def _stroke_line(
    points: list[Point], width_tiles: float, grid_w: int, grid_d: int
) -> list[tuple[int, int]]:
    """Cells within ``width_tiles`` of a polyline.

    Width rounds *up* to a whole tile: a 12 ft street must stay passable after
    rasterising, and losing half a tile to rounding is what makes a map feel
    cramped in play.
    """
    half = max(0.5, width_tiles / 2.0)
    cells: set[tuple[int, int]] = set()
    for i in range(len(points) - 1):
        ax, ay = points[i]
        bx, by = points[i + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        x0 = max(0, int(math.floor(min(ax, bx) - half)))
        x1 = min(grid_w - 1, int(math.ceil(max(ax, bx) + half)))
        z0 = max(0, int(math.floor(min(ay, by) - half)))
        z1 = min(grid_d - 1, int(math.ceil(max(ay, by) + half)))
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                px, py = x + 0.5, z + 0.5
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (length * length)))
                qx, qy = ax + t * dx, ay + t * dy
                if math.hypot(px - qx, py - qy) <= half:
                    cells.add((x, z))
    return sorted(cells)


# -- rasterisation ------------------------------------------------------------

def rasterize(layout: Layout, *, pad: int = 0, bridges: bool = True) -> TileMap:
    """Rasterise a layout into a :class:`TileMap`."""
    width = int(math.ceil(layout.width)) + pad * 2
    depth = int(math.ceil(layout.depth)) + pad * 2
    tm = TileMap.blank(width, depth, layout.name)

    def shift(ring: list[Point]) -> list[Point]:
        return [(x + pad, y + pad) for x, y in ring]

    def paint(cells, surface: str, over: frozenset[str] | None = None) -> None:
        for x, z in cells:
            if not tm.inside(x, z):
                continue
            if over is not None and tm.surface[z][x] not in over:
                continue
            tm.surface[z][x] = surface

    # Terrain, coarse to fine.
    for area in layout.areas_of("field"):
        paint(_fill_polygon(shift(area.ring), width, depth), FIELD)
    for area in layout.areas_of("water"):
        paint(_fill_polygon(shift(area.ring), width, depth), WATER)

    # Order is load-bearing: rivers carve terrain first, then piers and streets
    # are laid on top. Painting a river *after* a road erases the road cells
    # exactly where it crosses -- which deletes the bridges and cuts the town
    # into disconnected halves. Streets therefore paint last and may sit on
    # water, which is precisely what a bridge is.
    order = {"river": 0, "plank": 1, "road": 2}
    for road in sorted(layout.roads, key=lambda r: order.get(r.kind, 2)):
        # A street narrower than two tiles cannot be walked abreast, so the
        # party strings out single file down the one road through town. MFCG
        # picks its road width for a drawing, not for a grid -- Forest Church
        # exports 8 ft -- so widen streets to the playable minimum. Rivers and
        # planks keep their true width: those are terrain, not thoroughfares.
        stroke = road.width
        if road.kind not in ("river", "plank"):
            stroke = max(stroke, MIN_STREET_TILES)
        cells = _stroke_line(shift(road.points), stroke, width, depth)
        if road.kind == "river":
            paint(cells, WATER)
        elif road.kind == "plank":
            paint(cells, PIER)
        else:
            paint(cells, STREET, over=frozenset({GROUND, FIELD, WATER}))

    for area in layout.areas_of("plaza"):
        paint(_fill_polygon(shift(area.ring), width, depth), PLAZA)
    for area in layout.areas_of("park"):
        paint(_fill_polygon(shift(area.ring), width, depth), GROUND)

    # Buildings last: where a footprint and a street disagree after rounding,
    # the building wins, so structures stay whole rather than gaining holes.
    for b in layout.buildings:
        cells = _fill_polygon(shift(b.ring), width, depth)
        for x, z in cells:
            if not tm.inside(x, z):
                continue
            if tm.building[z][x]:
                continue  # first building to claim a cell keeps it
            tm.building[z][x] = b.id
            tm.surface[z][x] = FLOOR
        tm.floors[b.id] = max(1, b.floors)

    _regularise_buildings(tm)
    _absorb_fragments(tm)
    _rasterize_walls(tm, layout, shift, width, depth)
    if bridges:
        tm.bridges = _bridge_water_gaps(tm, layout)
    _find_perimeters(tm, layout)
    _place_doors(tm, layout)
    return tm


def components(tm: TileMap, min_size: int = 1) -> list[list[tuple[int, int]]]:
    """Connected regions of walkable space, largest first."""
    seen = [[False] * tm.width for _ in range(tm.depth)]
    out: list[list[tuple[int, int]]] = []
    for z0 in range(tm.depth):
        for x0 in range(tm.width):
            if seen[z0][x0] or not tm.is_walkable(x0, z0):
                continue
            queue = deque([(x0, z0)])
            seen[z0][x0] = True
            cells: list[tuple[int, int]] = []
            while queue:
                x, z = queue.popleft()
                cells.append((x, z))
                for _, dx, dz in SIDES:
                    nx, nz = x + dx, z + dz
                    if tm.inside(nx, nz) and not seen[nz][nx] and tm.is_walkable(nx, nz):
                        seen[nz][nx] = True
                        queue.append((nx, nz))
            if len(cells) >= min_size:
                out.append(cells)
    out.sort(key=len, reverse=True)
    return out


def _bridge_water_gaps(
    tm: TileMap, layout: Layout, *, min_component: int = 400, max_span: int = 60
) -> list[tuple[int, int, int, int]]:
    """Connect districts that water has cut apart.

    A river running through a town splits the street network in two. MFCG marks
    the river but does not always route a road across it, so the rasterised town
    can end up as two halves with no way to walk between them -- fatal for a map
    the party is supposed to cross. Every remaining large district is therefore
    joined to the main one by the shortest crossing over water.
    """
    width = max(2, int(round(max((r.width for r in layout.roads if r.kind == "road"), default=2.0))))
    built: list[tuple[int, int, int, int]] = []

    for _ in range(8):  # bounded: a town needs a handful of bridges, not dozens
        comps = [c for c in components(tm) if len(c) >= min_component]
        if len(comps) < 2:
            break

        main = set(comps[0])
        target = comps[1]

        def shore(cells) -> list[tuple[int, int]]:
            out = []
            for x, z in cells:
                for _, dx, dz in SIDES:
                    nx, nz = x + dx, z + dz
                    if tm.inside(nx, nz) and tm.surface[nz][nx] == WATER:
                        out.append((x, z))
                        break
            return out

        a_shore = shore(comps[0])
        b_shore = shore(target)
        if not a_shore or not b_shore:
            break
        # Sample: shorelines run to thousands of cells and the nearest pair does
        # not need every candidate to be exact.
        step_a = max(1, len(a_shore) // 400)
        step_b = max(1, len(b_shore) // 400)

        best: tuple[float, tuple[int, int], tuple[int, int]] | None = None
        for ax, az in a_shore[::step_a]:
            for bx, bz in b_shore[::step_b]:
                d = math.hypot(ax - bx, az - bz)
                if d <= max_span and (best is None or d < best[0]):
                    best = (d, (ax, az), (bx, bz))
        if best is None:
            break

        _, (ax, az), (bx, bz) = best
        for x, z in _stroke_line([(ax + 0.5, az + 0.5), (bx + 0.5, bz + 0.5)],
                                 float(width), tm.width, tm.depth):
            if tm.surface[z][x] == WATER and not tm.building[z][x]:
                tm.surface[z][x] = STREET
        built.append((ax, az, bx, bz))

        if set(comps[0]) == main and not built:
            break
    return built


def _regularise_buildings(tm: "TileMap") -> None:
    """Reduce each building footprint to its largest inscribed rectangle.

    MFCG's polygons rasterise into blobby crosses -- a stable came out with
    row widths 1-4-5-2-1. Assembled cell-by-cell, a blob is unbuildable: roof
    courses float over one-wide spurs, walls wrap every notch as freestanding
    columns, and the door lands on a single-cell tail. Every working house
    slab in the community, and every generator we surveyed, assembles from
    rectangles; this is the layout convention that makes wall runs straight,
    roofs seat, and pieces seam. Cells outside the rectangle return to open
    ground (they become yard, which reads fine).

    Largest-rectangle-under-histogram per building; blobs that cannot yield
    at least 2x3 are left for :func:`_absorb_fragments` to fold away.
    """
    cells_of: dict[str, list[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                cells_of.setdefault(bid, []).append((x, z))

    for bid, cells in sorted(cells_of.items()):
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        x0, x1 = min(xs), max(xs)
        z0, z1 = min(zs), max(zs)
        w, d = x1 - x0 + 1, z1 - z0 + 1
        mask = [[False] * w for _ in range(d)]
        for x, z in cells:
            mask[z - z0][x - x0] = True
        if all(all(row) for row in mask):
            continue  # already a solid rectangle

        # Largest rectangle of True cells (histogram of column heights).
        best = (0, 0, 0, 0, 0)  # area, bx0, bz0, bw, bd
        heights = [0] * w
        for r in range(d):
            for c in range(w):
                heights[c] = heights[c] + 1 if mask[r][c] else 0
            stack: list[int] = []
            c = 0
            while c <= w:
                h = heights[c] if c < w else 0
                if not stack or h >= heights[stack[-1]]:
                    stack.append(c)
                    c += 1
                    continue
                top = stack.pop()
                width_run = c if not stack else c - stack[-1] - 1
                area = heights[top] * width_run
                if area > best[0]:
                    left = 0 if not stack else stack[-1] + 1
                    best = (area, left, r - heights[top] + 1, width_run, heights[top])
        _, bx, bz, bw, bd = best
        keep = {
            (x0 + bx + dx, z0 + bz + dz)
            for dx in range(bw) for dz in range(bd)
        } if best[0] >= 6 and min(bw, bd) >= 2 else set()

        for x, z in cells:
            if (x, z) not in keep:
                tm.building[z][x] = ""
                tm.surface[z][x] = GROUND
        if not keep:
            tm.floors.pop(bid, None)


def _absorb_fragments(tm: "TileMap") -> None:
    """Fold sliver building fragments into their dominant neighbour.

    Footprints overlap once rasterised, and first-claim dedup leaves the
    loser as an L-shape or a 1-wide strip. Assembled independently, those
    slivers produce the worst geometry on the board: single columns of roof
    with mixed slopes, orphaned gables, walls rising through a neighbour's
    roof. A fragment too thin to hold a room -- under 2 tiles across or under
    4 cells -- is not a building; give its cells to the adjacent building
    that touches it most, or return them to open ground.
    """
    from collections import Counter

    cells_of: dict[str, list[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                cells_of.setdefault(bid, []).append((x, z))

    for bid, cells in sorted(cells_of.items()):
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        if min(w, d) >= 2 and len(cells) >= 4:
            continue
        votes: Counter = Counter()
        for x, z in cells:
            for dx, dz in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, nz = x + dx, z + dz
                if 0 <= nx < tm.width and 0 <= nz < tm.depth:
                    nb = tm.building[nz][nx]
                    if nb and nb != bid:
                        votes[nb] += 1
        heir = votes.most_common(1)[0][0] if votes else ""
        for x, z in cells:
            tm.building[z][x] = heir
            if not heir:
                tm.surface[z][x] = GROUND
        tm.floors.pop(bid, None)


def _rasterize_walls(tm: TileMap, layout: Layout, shift, width: int, depth: int) -> None:
    thickness = max(1.0, layout.wall_thickness)
    for ring in layout.walls:
        for x, z in _stroke_line(shift(ring), thickness, width, depth):
            tm.wall[z][x] = True

    # Carve gates. A gate must be wide enough to march through, so it is opened
    # to the width of the roads that meet it rather than a single cell.
    road_width = max(
        [r.width for r in layout.roads if r.kind == "road"] or [2.0]
    )
    radius = max(1, int(math.ceil(road_width / 2.0)) + 1)
    for gx, gy in layout.gates:
        cx, cz = int(round(gx)) + 0, int(round(gy)) + 0
        for dz in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                x, z = cx + dx, cz + dz
                if not tm.inside(x, z):
                    continue
                if math.hypot(dx, dz) > radius:
                    continue
                if tm.wall[z][x]:
                    tm.wall[z][x] = False
                    tm.gates.add((x, z))
                    if tm.surface[z][x] not in (WATER,):
                        tm.surface[z][x] = STREET


def _find_perimeters(tm: TileMap, layout: Layout | None) -> None:
    """Record which cell edges form each building's outer shell."""
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if not bid:
                continue
            for side, dx, dz in SIDES:
                nx, nz = x + dx, z + dz
                if not tm.inside(nx, nz) or tm.building[nz][nx] != bid:
                    tm.perimeter.setdefault(bid, []).append((x, z, side))


def _facade_runs(
    cells: list[tuple[int, int, str]]
) -> list[tuple[str, int, list[int]]]:
    """Split a building's perimeter into contiguous runs, one per facade stretch.

    A run is what a human reads as a single wall: the cells of one side that
    touch each other end to end. Returned as ``(side, fixed, positions)`` where
    ``fixed`` is the constant coordinate (z for n/s, x for w/e) and
    ``positions`` is the sorted varying coordinate. Knowing the run is what
    lets a door sit in the middle of a facade instead of against a corner.
    """
    lines: dict[tuple[str, int], list[int]] = {}
    for x, z, side in cells:
        if side in ("n", "s"):
            lines.setdefault((side, z), []).append(x)
        else:
            lines.setdefault((side, x), []).append(z)

    runs: list[tuple[str, int, list[int]]] = []
    for (side, fixed), vals in lines.items():
        vals.sort()
        current = [vals[0]]
        for v in vals[1:]:
            if v == current[-1] + 1:
                current.append(v)
            else:
                runs.append((side, fixed, current))
                current = [v]
        runs.append((side, fixed, current))
    return runs


#: Footprint area (in tiles) at which a building earns a second entrance. A
#: 3x5 house is the smallest shape with two facades long enough to centre a
#: door on, and at that size one doorway is both a bottleneck for the party
#: and a missed tactical option for whatever lives inside.
SECOND_DOOR_AREA = 15

_OPPOSITE = {"n": "s", "s": "n", "w": "e", "e": "w"}


def _place_doors(tm: TileMap, layout: Layout | None) -> None:
    """Give every building a doorway onto the most public space it touches.

    Preferring the nearest open cell is not enough: a medieval block encloses
    courtyards, and a door opening into a sealed courtyard leaves the building
    unenterable even though it technically has one. Reachability from the gates
    is therefore computed first and dominates the ranking, so a door onto the
    real street network always beats a closer door into a pocket.

    Within an equally public facade the door is then centred on its wall run.
    Nearest-found put 38 of 48 doors hard against a corner, which reads as a
    service hatch rather than an entrance and, on a two-cell facade, means the
    first mini through the door plugs the whole opening.

    Buildings of at least :data:`SECOND_DOOR_AREA` tiles get a second doorway
    on a different side -- a back door -- preferring the opposite facade. The
    primary door stays first in ``tm.doors[bid]``; verification reads it there.
    """
    reach = reachable_from(tm, sorted(tm.gates) or _fallback_starts(tm))
    priority = {STREET: 0, PLAZA: 1, PIER: 2, GROUND: 3}

    area: dict[str, int] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                area[bid] = area.get(bid, 0) + 1

    for bid, cells in tm.perimeter.items():
        # (reachable, publicness, distance from the middle of the run) -> cell.
        # Offsets are doubled so an even-length run's two middle cells stay
        # integers and compare equal.
        candidates: list[tuple[tuple[int, int, int], tuple[int, int, str]]] = []
        for side, fixed, positions in _facade_runs(cells):
            dx, dz = next((d, e) for s, d, e in SIDES if s == side)
            span = len(positions)
            for i, p in enumerate(positions):
                x, z = (p, fixed) if side in ("n", "s") else (fixed, p)
                nx, nz = x + dx, z + dz
                if not tm.inside(nx, nz):
                    continue
                if tm.building[nz][nx] or tm.wall[nz][nx]:
                    continue
                rank = priority.get(tm.surface[nz][nx])
                if rank is None:
                    continue
                offset = abs(2 * i - (span - 1))
                key = (0 if reach[nz][nx] else 1, rank, offset)
                candidates.append((key, (x, z, side)))

        if not candidates:
            continue
        primary = min(candidates, key=lambda c: c[0])[1]
        doors = [primary]

        # A back door only earns its place if it genuinely opens onto the
        # street network; a second hole into a sealed courtyard is decoration.
        # The opposite facade is preferred over an adjacent one so the two
        # entrances sit at either end of the building rather than round a
        # corner from each other -- but publicness still outranks that.
        if area.get(bid, 0) >= SECOND_DOOR_AREA:
            others = []
            for (reached, rank, offset), cell in candidates:
                if reached or cell[2] == primary[2]:
                    continue
                back = 0 if cell[2] == _OPPOSITE[primary[2]] else 1
                others.append(((rank, back, offset), cell))
            if others:
                doors.append(min(others, key=lambda c: c[0])[1])

        tm.doors[bid] = doors


def _fallback_starts(tm: TileMap) -> list[tuple[int, int]]:
    """Seed reachability from the map edge when a town has no gates."""
    out = []
    for x in range(tm.width):
        for z in (0, tm.depth - 1):
            if tm.is_walkable(x, z):
                out.append((x, z))
    for z in range(tm.depth):
        for x in (0, tm.width - 1):
            if tm.is_walkable(x, z):
                out.append((x, z))
    return out


# -- analysis -----------------------------------------------------------------

def reachable_from(tm: TileMap, starts: list[tuple[int, int]]) -> list[list[bool]]:
    """Flood fill the open street network from ``starts`` (4-connected)."""
    seen = [[False] * tm.width for _ in range(tm.depth)]
    queue: deque[tuple[int, int]] = deque()
    for x, z in starts:
        if tm.inside(x, z) and tm.is_walkable(x, z) and not seen[z][x]:
            seen[z][x] = True
            queue.append((x, z))
    while queue:
        x, z = queue.popleft()
        for _, dx, dz in SIDES:
            nx, nz = x + dx, z + dz
            if tm.inside(nx, nz) and not seen[nz][nx] and tm.is_walkable(nx, nz):
                seen[nz][nx] = True
                queue.append((nx, nz))
    return seen


def open_width_at(tm: TileMap, x: int, z: int) -> int:
    """Narrowest open span through a cell, in tiles.

    Measures horizontally and vertically and takes the smaller, which is what
    limits how many creatures can stand abreast there.
    """
    if not tm.is_walkable(x, z):
        return 0
    spans = []
    for dx, dz in ((1, 0), (0, 1)):
        span = 1
        for sign in (1, -1):
            nx, nz = x + dx * sign, z + dz * sign
            while tm.is_walkable(nx, nz):
                span += 1
                nx += dx * sign
                nz += dz * sign
        spans.append(span)
    return min(spans)
