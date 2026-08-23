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
import zlib
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
LANE = "lane"
FLOOR = "floor"

#: Surfaces a creature can stand and walk on.
WALKABLE = frozenset({GROUND, FIELD, STREET, PLAZA, PIER, LANE, FLOOR})

#: Surfaces that count as public open space for door placement and routing.
#: A lane belongs here: it is a way people walk, and leaving it out silently
#: invalidated the doorway of every building whose only frontage got paved as
#: one -- access fell to 96% with no hint that the lanes had caused it.
OPEN = frozenset({GROUND, STREET, PLAZA, PIER, LANE})

SIDES = (("n", 0, -1), ("s", 0, 1), ("w", -1, 0), ("e", 1, 0))

# -- street widths -----------------------------------------------------------
#
# Every width below is derived from the one fixed fact the pipeline is pinned
# to: a tile is 5 ft and a creature occupies exactly one tile. What a way has to
# be wide enough for is therefore a question of what physically has to fit past
# what, not a question of taste.

#: Two people abreast (10 ft). The floor for anything a creature walks on --
#: below this the party strings out single file down the one road through town.
LANE_TILES = 2.0

#: A horse cart or carriage is two tiles wide (10 ft), so a cart with someone
#: squeezing past it needs three (15 ft). Below this a wagon parked to unload
#: closes the street.
CART_TILES = 3.0

#: Two carts passing, neither stopping: 2 + 2 tiles (20 ft). This is what a
#: market street or a gate road has to be, because that is where the carts are.
MAIN_STREET_TILES = 4.0

#: Kept as the name for the two-abreast floor; it is what the old single
#: constant meant and `docs/asset-conventions.md` still cites it.
MIN_STREET_TILES = LANE_TILES

#: Traffic classes a road can be put in, busiest first.
MAIN_ROAD = "main"
CART_ROAD = "cart"
LANE_ROAD = "lane"

#: Class -> the width that class of way has to hold, in tiles.
STREET_STANDARD = {
    MAIN_ROAD: MAIN_STREET_TILES,
    CART_ROAD: CART_TILES,
    LANE_ROAD: LANE_TILES,
}

#: Used to settle a cell two roads both claim: the busier road wins, because
#: the junction has to carry the traffic of the street that runs through it.
_CLASS_RANK = {"": 0, LANE_ROAD: 1, CART_ROAD: 2, MAIN_ROAD: 3}


@dataclass
class TileMap:
    """A rasterised city: one record per 5 ft square."""

    width: int
    depth: int
    name: str = ""
    surface: list[list[str]] = field(default_factory=list)
    building: list[list[str]] = field(default_factory=list)
    wall: list[list[bool]] = field(default_factory=list)
    #: Traffic class of the road that paved each cell -- "main", "cart", "lane",
    #: or "" where nothing was. Only meaningful where ``surface`` is ``STREET``;
    #: a building painted over a road leaves its class behind. Verification
    #: reads it to hold each stretch to the width its own traffic needs rather
    #: than to one flat number.
    street_class: list[list[str]] = field(default_factory=list)
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
    #: The vertices of each wall ring, as cells. A rasterised wall is a band of
    #: cells with no memory of where the polygon turned, and a turn is where a
    #: mural tower goes; the builder cannot recover that from the band.
    wall_corners: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def blank(cls, width: int, depth: int, name: str = "") -> "TileMap":
        return cls(
            width=width, depth=depth, name=name,
            surface=[[GROUND] * width for _ in range(depth)],
            building=[[""] * width for _ in range(depth)],
            wall=[[False] * width for _ in range(depth)],
            street_class=[[""] * width for _ in range(depth)],
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
                out.street_class[z][x] = self.street_class[z0 + z][x0 + x]
        out.gates = {
            (x - x0, z - z0) for x, z in self.gates
            if x0 <= x < x0 + width and z0 <= z < z0 + depth
        }
        out.bridges = [
            (a - x0, b - z0, c - x0, d - z0) for a, b, c, d in self.bridges
            if x0 <= a < x0 + width and z0 <= b < z0 + depth
        ]
        out.wall_corners = [
            (x - x0, z - z0) for x, z in self.wall_corners
            if x0 <= x < x0 + width and z0 <= z < z0 + depth
        ]
        # Storey counts must survive the crop, or every building in a staged
        # test comes out single-storey -- which silently defeats the point of
        # --crop, since the paste then exercises no wall stacking, no upper
        # floor and no raised roof course.
        out.floors = {
            bid: self.floors[bid]
            for bid in {v for row in out.building for v in row if v}
            if bid in self.floors
        }
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


# -- road classification ------------------------------------------------------

#: A road counts as reaching a gate if it passes within this many tiles of one.
#: The gate point sits on the wall centreline and a road polyline usually stops
#: just short of the arch, so an exact touch is the wrong test.
GATE_REACH_TILES = 4.0

#: Length, as a share of the longest road on the board, at or above which a road
#: is a principal street; and the share below which it is a back lane. Measuring
#: against the longest road rather than an absolute tile count keeps the rule
#: scale-free: a hamlet's high street and a city's are both the longest thing
#: on their own board.
MAIN_LENGTH_SHARE = 0.5
LANE_LENGTH_SHARE = 0.2

#: Other roads feeding into this one that make it a collector, not a spur.
COLLECTOR_DEGREE = 2

#: How close two roads must come to count as meeting.
JUNCTION_TILES = 2.0


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / span))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _polyline_distance(p: Point, points: list[Point]) -> float:
    return min(
        (_point_segment_distance(p, points[i], points[i + 1])
         for i in range(len(points) - 1)),
        default=float("inf"),
    )


def _onboard_length(points: list[Point], width: float, depth: float) -> float:
    """Length of a polyline that actually lands on the board, in tiles.

    MFCG's roads run far past the mapped area -- Forest Church's highways end a
    hundred tiles off the edge, and only the part that gets rasterised says
    anything about the road's role on this map. Sampled at roughly one point per
    tile, which is finer than any threshold that reads the result.
    """
    total = 0.0
    for i in range(len(points) - 1):
        (ax, ay), (bx, by) = points[i], points[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        steps = max(1, int(seg))
        for k in range(steps):
            t = (k + 0.5) / steps
            x, y = ax + t * (bx - ax), ay + t * (by - ay)
            if 0.0 <= x <= width and 0.0 <= y <= depth:
                total += seg / steps
    return total


#: Road kinds that are landscape rather than thoroughfare: they keep their true
#: width, get no street class, and are never widened for carts.
NOT_THOROUGHFARES = frozenset({"river", "plank", "trail"})


def classify_roads(layout: Layout) -> list[str]:
    """Put every road in a traffic class -- one entry per ``layout.roads``.

    MFCG draws its whole road network at a single width (Forest Church exports
    every street at 8 ft, 1.7 tiles) and its ``kind`` only separates roads from
    rivers and piers, so the class cannot be read off the export. It has to be
    inferred from the road's role in the network -- and the only roles worth
    distinguishing are the ones that change what can drive down the street:

    ``main``
        Reaches a gate, or runs at least :data:`MAIN_LENGTH_SHARE` of the
        longest road on the board. A gate is where carts queue to get in and the
        high street is where they stop to unload while others go past, so both
        have to take two carts side by side.
    ``cart``
        At least :data:`LANE_LENGTH_SHARE` of that length, or a junction that
        :data:`COLLECTOR_DEGREE` other roads feed into. A collector: one cart at
        a time, with people getting past it.
    ``lane``
        Anything shorter and unconnected -- a back way between two blocks, walked
        rather than driven, so two abreast is all it owes anyone.

    Length is measured on the board (:func:`_onboard_length`), not end to end: a
    highway that only clips the corner of the map is, on this map, a track, and
    paving it like a market street would be a lie about where the traffic is.

    Rivers, planks and trails are terrain rather than thoroughfares and get no
    class -- same reason they are excluded from widening. A footpath is
    *correct* at one tile; widening it to cart standard would invent a road the
    source does not have.
    """
    classes = [""] * len(layout.roads)
    lengths = {
        i: _onboard_length(r.points, layout.width, layout.depth)
        for i, r in enumerate(layout.roads)
        if r.kind not in NOT_THOROUGHFARES and len(r.points) >= 2
    }
    if not lengths:
        return classes
    longest = max(lengths.values()) or 1.0

    for i, length in lengths.items():
        points = layout.roads[i].points
        at_gate = any(
            _polyline_distance(g, points) <= GATE_REACH_TILES for g in layout.gates
        )
        degree = sum(
            1 for j in lengths
            if j != i and any(
                _polyline_distance(p, layout.roads[j].points) <= JUNCTION_TILES
                for p in points
            )
        )
        share = length / longest
        if at_gate or share >= MAIN_LENGTH_SHARE:
            classes[i] = MAIN_ROAD
        elif share >= LANE_LENGTH_SHARE or degree >= COLLECTOR_DEGREE:
            classes[i] = CART_ROAD
        else:
            classes[i] = LANE_ROAD
    return classes


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
    order = {"river": 0, "plank": 1, "trail": 1, "road": 2}
    road_class = classify_roads(layout)
    ranked = sorted(range(len(layout.roads)),
                    key=lambda i: order.get(layout.roads[i].kind, 2))
    for i in ranked:
        road = layout.roads[i]
        # MFCG picks one road width for a drawing, not for a grid -- Forest
        # Church exports every street at 8 ft, 1.7 tiles -- so the width has to
        # be set here from what has to use the street. A creature is one tile,
        # a cart is two: a lane must take two people abreast, a cart street a
        # cart with someone squeezing past, and a main street two carts passing.
        # Rivers and planks keep their true width; they are terrain, and a
        # footbridge is not a thoroughfare.
        cls = road_class[i]
        stroke = road.width
        if cls:
            stroke = max(stroke, STREET_STANDARD[cls])
        cells = _stroke_line(shift(road.points), stroke, width, depth)
        if road.kind == "river":
            paint(cells, WATER)
        elif road.kind == "plank":
            paint(cells, PIER)
        else:
            paint(cells, STREET, over=frozenset({GROUND, FIELD, WATER}))
            for x, z in cells:
                if (tm.inside(x, z) and tm.surface[z][x] == STREET
                        and _CLASS_RANK[cls] > _CLASS_RANK[tm.street_class[z][x]]):
                    tm.street_class[z][x] = cls

    for area in layout.areas_of("plaza"):
        paint(_fill_polygon(shift(area.ring), width, depth), PLAZA)
    for area in layout.areas_of("park"):
        paint(_fill_polygon(shift(area.ring), width, depth), GROUND)

    # Buildings last: where a footprint and a street disagree after rounding,
    # the building wins, so structures stay whole rather than gaining holes.
    #
    # With one exception. A through route is widened for carts *before* this
    # runs, so a footprint lapping over it re-narrows the street it was just
    # widened for -- and the per-class width check cannot see that, because the
    # tile is still classed a main street, it just has a house either side.
    # Through-route cells therefore hold against buildings; the footprint loses
    # the overlap instead. Lanes keep the old behaviour: they are not for carts,
    # and a building is worth more than a wider alley.
    for b in layout.buildings:
        cells = _fill_polygon(shift(b.ring), width, depth)
        for x, z in cells:
            if not tm.inside(x, z):
                continue
            if tm.building[z][x]:
                continue  # first building to claim a cell keeps it
            if tm.street_class[z][x] in ("main", "cart"):
                continue  # a cart route stays open
            tm.building[z][x] = b.id
            tm.surface[z][x] = FLOOR
        tm.floors[b.id] = max(1, b.floors)

    _regularise_buildings(tm)
    _notch_buildings(tm)
    _absorb_fragments(tm)
    _rasterize_walls(tm, layout, shift, width, depth)
    if bridges:
        tm.bridges = _bridge_water_gaps(tm, layout)
    _carve_plaza(tm)
    _trace_lanes(tm)
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
    # A bridge is the only way across the water, so a cart stopped on it stops
    # the town. It is built to the main-street standard for that reason, rather
    # than to whatever width MFCG happened to draw its roads at.
    span_width = max(
        MAIN_STREET_TILES,
        max((r.width for r in layout.roads if r.kind == "road"), default=2.0),
    )
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
                                 span_width, tm.width, tm.depth):
            if tm.surface[z][x] == WATER and not tm.building[z][x]:
                tm.surface[z][x] = STREET
                tm.street_class[z][x] = MAIN_ROAD
        built.append((ax, az, bx, bz))

        if set(comps[0]) == main and not built:
            break
    return built


#: A building has to be at least this big before a corner can be cut out of
#: it, and must keep at least this much afterwards.
NOTCH_MIN_AREA = 36
NOTCH_KEEP_AREA = 24


def _notch_opens_outward(tm: "TileMap", patch: list[tuple[int, int]],
                         reachable: set[tuple[int, int]]) -> bool:
    """Whether a proposed notch would be a yard rather than a sealed pocket.

    A yard has to touch ground that is *reachable from the gates*, not merely
    ground that is open. Testing only for openness still cut buildings off:
    the notch found its way into another sealed courtyard and access came out
    at 98% instead of 100%. Reachability is measured before any notch is cut,
    which is conservative in the right direction -- a notch can only ever add
    open space, never remove it.
    """
    inside = set(patch)
    for x, z in patch:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if (nx, nz) in inside or not tm.inside(nx, nz):
                continue
            if (nx, nz) in reachable:
                return True
    return False


def _still_enterable(tm: "TileMap", kept: list[tuple[int, int]],
                     reachable: set[tuple[int, int]],
                     yard: set[tuple[int, int]]) -> bool:
    """Whether what is left of a footprint still touches somewhere public.

    The yard the notch opens counts, since it is only cut when it joins
    reachable ground -- so a building whose street frontage is taken by the
    notch can still be entered from its own yard.
    """
    for x, z in kept:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in reachable or n in yard:
                return True
    return False


def _notch_buildings(tm: "TileMap") -> None:
    """Cut a corner out of some footprints so the town is not all boxes.

    :func:`_regularise_buildings` reduces every MFCG blob to its largest
    inscribed rectangle, which is what makes wall runs straight and roofs
    seat -- and left 51 of 51 buildings a perfect rectangle. The fix is not to
    put the blobs back. An L is still assembled *from* rectangles: two of
    them. Wall runs stay straight, and roof ring depth has come from a search
    inward from the real boundary since the L-shaped-terrace bug, so an L
    roofs correctly where a blob never could.

    The notch is a clean rectangle at one corner, about a third of the plan
    each way, chosen by a stable hash so a rebuild cuts the same corner.
    Buildings too small to spare it keep their rectangle.
    """
    # reachable_from returns a bool *grid*, not a set of cells. Testing
    # ``(x, z) in grid`` is always False -- it compares a tuple against the
    # rows -- which silently rejected every notch and left all 51 footprints
    # rectangular while the code looked like it was cutting them.
    seen = reachable_from(tm, sorted(tm.gates) or _fallback_starts(tm))
    reachable = {(x, z) for z in range(tm.depth) for x in range(tm.width)
                 if seen[z][x]}

    cells_of: dict[str, list[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                cells_of.setdefault(bid, []).append((x, z))

    for bid, cells in sorted(cells_of.items()):
        if len(cells) < NOTCH_MIN_AREA:
            continue
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        w, d = x1 - x0 + 1, z1 - z0 + 1
        nw, nd = max(2, w // 3), max(2, d // 3)
        if len(cells) - nw * nd < NOTCH_KEEP_AREA:
            continue
        if w - nw < 2 or d - nd < 2:
            continue

        # Try all four corners, and only cut one whose yard actually opens
        # outward. A notch boxed in by the neighbours is not a yard, it is a
        # sealed courtyard: the first version of this cut three buildings off
        # the street network and took access from 100% to 94%, which the
        # build's own access check caught.
        pick = zlib.crc32(f"notch:{bid}".encode())
        corners = [(x0, z0), (x1 - nw + 1, z0),
                   (x0, z1 - nd + 1), (x1 - nw + 1, z1 - nd + 1)]
        for i in range(4):
            cx0, cz0 = corners[(pick + i) % 4]
            patch = [(x, z) for z in range(cz0, cz0 + nd)
                     for x in range(cx0, cx0 + nw)]
            if not _notch_opens_outward(tm, patch, reachable):
                continue
            cut = [(x, z) for x, z in patch if tm.building[z][x] == bid]
            kept = [c for c in cells if c not in set(cut)]
            # Cut it, then check the building can still be entered. Guarding
            # the *yard* is not enough: a notch can take away the very facade
            # the door was going to sit on, which cost a building its doorway
            # entirely. Anything that cannot be entered afterwards is put back.
            if not _still_enterable(tm, kept, reachable, set(cut)):
                continue
            for x, z in cut:
                tm.building[z][x] = ""
                if tm.surface[z][x] == FLOOR:
                    tm.surface[z][x] = GROUND
            break


#: Side of the square, and how far from the town's built centre it may sit.
PLAZA_SIDE = 7


def _carve_plaza(tm: "TileMap") -> None:
    """Open a market square on the busiest piece of the street network.

    MFCG's squares came through this export empty, so the town had no plaza at
    all -- no public room, nowhere for the well and the market clutter the
    dressing pass already knows how to lay, and nowhere for a party to be
    accosted. The square is placed where the most street already meets, which
    puts it on the junction the town's own road layout says is the centre.
    """
    best: tuple[int, tuple[int, int]] | None = None
    for z in range(tm.depth - PLAZA_SIDE):
        for x in range(tm.width - PLAZA_SIDE):
            block = [(x + i, z + j) for i in range(PLAZA_SIDE)
                     for j in range(PLAZA_SIDE)]
            if any(tm.building[bz][bx] or tm.wall[bz][bx]
                   or tm.surface[bz][bx] in (WATER, VOID, PIER)
                   for bx, bz in block):
                continue
            paved = sum(1 for bx, bz in block if tm.surface[bz][bx] == STREET)
            if paved == 0:
                continue
            if best is None or paved > best[0]:
                best = (paved, (x, z))

    if best is None:
        return
    _, (x0, z0) = best
    for j in range(PLAZA_SIDE):
        for i in range(PLAZA_SIDE):
            tm.surface[z0 + j][x0 + i] = PLAZA


#: A lane is at most this wide. Wider than that and it is a street that the
#: road layout simply did not name.
LANE_MAX_WIDTH = 2

#: How far a lane is walked outward from the gap that seeded it, looking for
#: a road. Buildings stand back from the carriageway, so without this a lane
#: never reaches anything.
LANE_REACH = 4


def _trace_lanes(tm: "TileMap") -> None:
    """Pave the gaps between buildings that people obviously walk down.

    The back lanes were bare grass, which reads as a gap the generator left
    rather than as a route anyone uses. A lane starts as ground pinched
    between buildings on opposite sides -- the shortcut a rogue takes during
    a chase -- and is then walked outward to the nearest road.

    That second step is the one that matters. Buildings here stand back from
    the carriageway, so of 121 pinched cells on this map **none** touched a
    street: requiring a lane to begin at a road found nothing at all. A lane
    is a corridor that *reaches* a road, not one that starts at it.
    """
    seeds: set[tuple[int, int]] = set()
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] != GROUND or tm.building[z][x] or tm.wall[z][x]:
                continue
            for dx, dz in ((1, 0), (0, 1)):
                near = far = False
                for step in range(1, LANE_MAX_WIDTH + 1):
                    ax, az = x + dx * step, z + dz * step
                    bx, bz = x - dx * step, z - dz * step
                    if tm.inside(ax, az) and tm.building[az][ax]:
                        near = True
                    if tm.inside(bx, bz) and tm.building[bz][bx]:
                        far = True
                if near and far:
                    seeds.add((x, z))
                    break

    lanes = set(seeds)
    frontier = deque((c, 0) for c in seeds)
    while frontier:
        (x, z), dist = frontier.popleft()
        if dist >= LANE_REACH:
            continue
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if not tm.inside(nx, nz) or (nx, nz) in lanes:
                continue
            if tm.building[nz][nx] or tm.wall[nz][nx]:
                continue
            here = tm.surface[nz][nx]
            if here in (STREET, PLAZA):
                continue          # arrived at the road; the lane ends here
            if here != GROUND:
                continue
            lanes.add((nx, nz))
            frontier.append(((nx, nz), dist + 1))

    for x, z in lanes:
        tm.surface[z][x] = LANE


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
        # Remember where the ring turns. MFCG closes a ring by repeating its
        # first vertex, and a vertex the clip pushed off the map is no place
        # for a tower, so both are dropped here rather than downstream.
        for vx, vz in shift(ring):
            cell = (int(round(vx)), int(round(vz)))
            if tm.inside(*cell) and cell not in tm.wall_corners:
                tm.wall_corners.append(cell)

    # Carve gates. A gate is where the through-route crosses the wall, so it is
    # opened to the main-street standard rather than to a single cell or to
    # whatever width MFCG drew: an arch narrower than the road through it
    # pinches the one way into town back down, and a cart that cannot fit
    # through the gate cannot reach the market it was driven here for.
    road_width = max(
        [r.width for r in layout.roads if r.kind == "road"] + [MAIN_STREET_TILES]
    )
    for gx, gy in layout.gates:
        _carve_gate(tm, int(round(gx)), int(round(gy)), road_width)

    _add_second_gate(tm, road_width)


#: How far either side of a gate point to sample the wall band when working
#: out which way the circuit runs there.
GATE_SAMPLE = 6

#: How far the postern's approach is paved beyond the passage, in cells. Kept
#: short and on the passage line: a wide apron paves the open ground beside the
#: wall that a mural tower needs to stand on.
GATE_APPROACH = 4


def _carve_gate(tm: TileMap, cx: int, cz: int, road_width: float) -> bool:
    """Cut a straight, axis-aligned passage through the wall band.

    **The passage has to be square, or the gate can never have doors.** The
    predecessor cleared a *disc* of wall cells, which on a circuit that runs
    diagonally leaves an opening whose jambs are a 45-degree stair-step -- on
    Forest Church an 18-cell hole with a 7x4 bounding box. There is no
    straight jamb-to-jamb line in that, so the flat 4-wide portcullis the
    palette has always carried had nothing to hang on, and the gate stayed a
    ragged notch for eleven revisions.

    So the passage is cut as a rectangle along whichever cardinal is closest
    to the wall's own normal: two straight jambs, a fixed clear width, and a
    lintel that spans in one line. The wall's run is measured from the band
    around the gate point rather than assumed from the ring's vertices,
    because MFCG puts this gate *on* a vertex and a vertex has no direction.
    """
    band = [(x, z)
            for z in range(max(0, cz - GATE_SAMPLE), min(tm.depth, cz + GATE_SAMPLE + 1))
            for x in range(max(0, cx - GATE_SAMPLE), min(tm.width, cx + GATE_SAMPLE + 1))
            if tm.wall[z][x]]
    if not band:
        return False

    # Principal axis of the band = the direction the wall runs. The passage
    # goes across it, snapped to a cardinal so the jambs come out straight.
    mx = sum(x for x, _ in band) / len(band)
    mz = sum(z for _, z in band) / len(band)
    sxx = sum((x - mx) ** 2 for x, _ in band)
    szz = sum((z - mz) ** 2 for _, z in band)
    # Runs more along x than z  ->  the passage runs along z, and vice versa.
    along_x = sxx >= szz
    pdx, pdz = (0, 1) if along_x else (1, 0)      # passage direction
    tdx, tdz = (1, 0) if along_x else (0, 1)      # across the passage

    width = max(2, int(round(road_width)))
    lo = -(width // 2)
    reach = GATE_SAMPLE + 2

    cut: list[tuple[int, int]] = []
    for t in range(lo, lo + width):
        for p in range(-reach, reach + 1):
            x = cx + tdx * t + pdx * p
            z = cz + tdz * t + pdz * p
            if not tm.inside(x, z) or not tm.wall[z][x]:
                continue
            tm.wall[z][x] = False
            tm.gates.add((x, z))
            cut.append((x, z))
            if tm.surface[z][x] not in (WATER,):
                tm.surface[z][x] = STREET
                tm.street_class[z][x] = MAIN_ROAD
    return (cut, (pdx, pdz), (tdx, tdz), (lo, width)) if cut else None


def _add_second_gate(tm: TileMap, road_width: float) -> None:
    """Cut a postern on the far side of the circuit when the export gives one
    gate.

    A walled town with a single entrance is a cul-de-sac: every approach, every
    sortie and every chase funnels through the same arch, and a party that
    wants a second way in has to go over the wall -- which, until the stairs
    went in, nothing could do. MFCG exports one gate for Forest Church, so the
    second is cut here, diametrically opposite the first, and paved through so
    it joins the street network on both faces.
    """
    if not tm.gates:
        return
    wall = [(x, z) for z in range(tm.depth) for x in range(tm.width) if tm.wall[z][x]]
    if not wall:
        return
    gx = sum(x for x, _ in tm.gates) / len(tm.gates)
    gz = sum(z for _, z in tm.gates) / len(tm.gates)
    cx = sum(x for x, _ in wall) / len(wall)
    cz = sum(z for _, z in wall) / len(wall)
    # The point on the circuit furthest from the gate we have, measured
    # through the ring's own centre so it lands opposite rather than merely
    # far away along the same stretch.
    ox, oz = 2 * cx - gx, 2 * cz - gz
    far = min(wall, key=lambda c: (c[0] - ox) ** 2 + (c[1] - oz) ** 2)
    carved = _carve_gate(tm, far[0], far[1], road_width)
    if carved is None:
        return
    cut, (pdx, pdz), (tdx, tdz), (lo, width) = carved

    # Pave the approach **along the passage line only**, not as a halo round
    # every gate cell. A square apron reads as a yard rather than a road, and
    # -- found the hard way -- it paves the open ground beside the wall that
    # `pick_wall_towers` needs to stand a tower on, so widening the approach
    # by two cells cost the circuit three of its five towers.
    px = sum(x for x, _ in cut) / len(cut)
    pz = sum(z for _, z in cut) / len(cut)
    for t in range(lo, lo + width):
        for p in range(-GATE_APPROACH, GATE_APPROACH + 1):
            x = int(round(px)) + tdx * t + pdx * p
            z = int(round(pz)) + tdz * t + pdz * p
            if not tm.inside(x, z) or tm.wall[z][x] or (x, z) in tm.gates:
                continue
            if tm.building[z][x] or tm.surface[z][x] in (WATER, PIER):
                continue
            if tm.surface[z][x] == GROUND:
                tm.surface[z][x] = LANE
                tm.street_class[z][x] = LANE_ROAD


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

#: Homes need more floor than a shop does before a second door earns its
#: place. A back door on a cottage is ordinary; two doors on a *small* one is
#: the tell that a generator sized them by a single number. 25 of 28 houses
#: had two, at a median 30-tile footprint.
SECOND_DOOR_AREA_HOME = 32
_HOME_KINDS = frozenset({"house", "shed", "stable"})

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
    priority = {STREET: 0, PLAZA: 1, LANE: 2, PIER: 3, GROUND: 4}

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
        kind = bid.split("-")[0]
        needed = (SECOND_DOOR_AREA_HOME if kind in _HOME_KINDS
                  else SECOND_DOOR_AREA)
        if area.get(bid, 0) >= needed:
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
