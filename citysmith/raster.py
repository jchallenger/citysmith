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
MARSH = "marsh"
STREET = "street"
PLAZA = "plaza"
#: The paved forecourt of a walled property -- see :func:`_lay_courts`.
#: Distinct from ``PLAZA`` on purpose: a market square is dressed with goods,
#: and a keep's courtyard full of market stalls is the surface-class-without-
#: context mistake this module already records against hedgerows and lilies.
COURT = "court"
PIER = "pier"
LANE = "lane"
FLOOR = "floor"

#: Surfaces a creature can stand and walk on.
#:
#: **This constant is descriptive, and `TileMap.is_walkable` does NOT read
#: it** -- that method gates on ``OPEN``, which is a strictly smaller set.
#: Nothing in the package reads ``WALKABLE`` at all; only tests do. Adding a
#: surface here therefore changes no behaviour, and believing otherwise cost
#: a wrong assertion while the marsh pass was being written. If a check ever
#: needs "can a creature stand here", it should read this and say so.
#:
#: **``MARSH`` belongs here and ``WATER`` does not**, and the difference is
#: measured rather than atmospheric: the swamp floor tiles are 1.0 and 2.0
#: wide and **0.5 tall**, exactly like grass, so a marsh cell is solid matter
#: at grade that a creature stands on with its feet dry-ish. Open water is
#: dropped ``build.WATER_SURFACE_DROP`` below grade with a bed under it.
WALKABLE = frozenset({GROUND, FIELD, MARSH, STREET, PLAZA, COURT, PIER, LANE, FLOOR})

#: Surfaces that count as public open space for door placement and routing.
#: A lane belongs here: it is a way people walk, and leaving it out silently
#: invalidated the doorway of every building whose only frontage got paved as
#: one -- access fell to 96% with no hint that the lanes had caused it.
#:
#: ``MARSH`` is deliberately absent. It is walkable, but it is not a *way*:
#: nobody puts their front door onto a bog, and routing that treats a fen as
#: public open space will happily send the street network through it.
#:
#: ``COURT`` belongs here and ``MARSH`` does not: a forecourt is exactly a way
#: -- it is the ground the front doors open onto, and the whole point of
#: laying one is that the doors are reachable across it.
OPEN = frozenset({GROUND, STREET, PLAZA, COURT, PIER, LANE})

#: Bare earth: the surfaces the map's edge is made of, and the only ones
#: :func:`build.edge_taper` may lower or bite out. Everything else is a
#: *surface laid on* the ground and has to stay level -- dropping the outer
#: ring of a river gives a waterfall the width of an estuary, and dropping it
#: under a carriageway gives a half-tile step across the road.
#:
#: **``MARSH`` is land here, and that is a decision rather than an oversight.**
#: A fen is not open water: `docs/marsh.md` measures the swamp floors at 0.5
#: tall, exactly like grass and tilled earth, so a marsh cell is solid matter
#: at grade and lowering it half a tile is the same operation as lowering a
#: field. Water is the opposite case -- a level sheet over a dropped bed --
#: which is why it is absent. And a fen *reaches* the border by design ("a
#: wetland either runs off the map or it fences off whatever is behind it"),
#: so excluding it would shelter the whole west side of Sedgewater and end
#: the map there on the sheer cut the taper exists to remove.
#:
#: This is only the edge taper's question. The verge pass and the shingle-bank
#: pass ask a different one about the same surfaces and answer it their own
#: way; neither should read this.
EDGE_LAND = frozenset({GROUND, FIELD, MARSH})

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
    #: building id -> the ROLE it plays in a church complex ("nave",
    #: "chancel", "transept", "aisle", "chapel", "vestry", "porch", "range").
    #: Empty for every ordinary building.
    #:
    #: A church is several abutting volumes, and the step between them is what
    #: says church from outside. That step cannot come from footprint AREA:
    #: a chancel is SMALL IN PLAN AND TALL IN SECTION because it is the most
    #: important space in the building, so area banding gets it exactly
    #: backwards -- 24 cells lands in the `chapel` band and draws a 30 ft step
    #: where 10 is right. Two independent reviews reached that separately.
    #:
    #: So the raster carries the role and `build.subordinate_courses` turns it
    #: into a height RELATIVE to the nave. Here rather than in the builder for
    #: the reason `floors` is here: the shell, the upper floors and the roof
    #: all read it, and a roof that disagrees with the walls floats.
    church_parts: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: Bridges added to reconnect districts split by water: (x0, z0, x1, z1).
    bridges: list[tuple[int, int, int, int]] = field(default_factory=list)
    #: The vertices of each wall ring, as cells. A rasterised wall is a band of
    #: cells with no memory of where the polygon turned, and a turn is where a
    #: mural tower goes; the builder cannot recover that from the band.
    wall_corners: list[tuple[int, int]] = field(default_factory=list)
    #: Field boundaries as polylines in tile coordinates, clipped to the map.
    #: Carried here for the same reason as ``wall_corners``: the builder needs
    #: geometry the cell grid cannot express. 97-100% of these run off-axis
    #: (`docs/fencing.md` §2.2), so stroking them into cells would stair-step
    #: every field wall on the map -- they are laid along their true bearing
    #: instead, and a bearing does not survive rasterisation.
    fences: list[list[Point]] = field(default_factory=list)
    #: Cells inside an authored forest outline (FTG's FOREST rings, carried as
    #: ``LayoutArea("forest")``). A *mask*, not a surface: forest floor is
    #: grass underfoot, so the cells stay GROUND and everything that walks,
    #: builds or verifies treats them as open ground -- only the tree scatter
    #: reads this, to put the wood inside the line the export drew. Empty on
    #: MFCG maps, and empty means neutral: the scatter must not change by a
    #: byte when no forest is authored.
    forest: set[tuple[int, int]] = field(default_factory=set)

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
        out.forest = {
            (x - x0, z - z0) for x, z in self.forest
            if x0 <= x < x0 + width and z0 <= z < z0 + depth
        }
        # Re-clipped rather than filtered: a fence run is a line, not a point,
        # so a crop cuts through the middle of one and the part that survives
        # needs its own ends. Filtering by vertex would drop a boundary that
        # crosses the crop without having a vertex inside it.
        out.fences = [
            run for line in self.fences
            for run in clip_polyline([(x - x0, z - z0) for x, z in line],
                                     0.0, 0.0, float(width), float(depth))
        ]
        # Storey counts must survive the crop, or every building in a staged
        # test comes out single-storey -- which silently defeats the point of
        # --crop, since the paste then exercises no wall stacking, no upper
        # floor and no raised roof course.
        kept = {v for row in out.building for v in row if v}
        out.floors = {
            bid: self.floors[bid] for bid in kept if bid in self.floors
        }
        # **Church roles ride along, or the crop rebuilds the church wrong.**
        # A crop makes a fresh TileMap and copies field by field, so anything
        # not listed here is silently lost -- and losing `church_parts` means
        # `_find_perimeters` below closes a ring round each part again (five
        # sealed rooms), `_place_doors` gives the chancel its own street door,
        # and `storeys_of` bands it on its own area. Caught by the churches
        # line in `feature_report`, which read "big enough to split and not
        # split" on a crop of a church that had been split perfectly well.
        #
        # A part whose nave fell outside the crop keeps its role but points at
        # a missing nave; `church_courses` falls back to banding it on its own
        # area there, which is the right answer for half a church.
        out.church_parts = {
            bid: self.church_parts[bid] for bid in kept
            if bid in self.church_parts
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


def clip_polyline(points: list[Point], x0: float, z0: float,
                  x1: float, z1: float) -> list[list[Point]]:
    """Split a polyline into the runs of it that lie inside a rectangle.

    **Everything else in this module clips for free and that is why this is
    needed.** ``_fill_polygon`` and ``_stroke_line`` write into a bounded grid,
    so whatever falls outside it is simply never written; areas overhang the
    map by up to 784 tiles today and nothing has ever noticed. A fence is laid
    as props along its true line, which has no grid to fall off the edge of --
    and a quarter of every fence line lies outside the crop window, reaching
    188 tiles past it on East Tradebourne, because ``ftg.inside_window`` keeps
    a whole segment that merely clips a corner (`docs/fencing.md` §2.4).

    An off-map prop is not a cosmetic problem: it drags the build's bounding
    box, and the bounding box is what every registration marker and chunk
    anchor is measured against.

    A polyline may leave and re-enter, so this returns a *list* of runs; each
    is laid as its own fence with its own end posts.
    """
    runs: list[list[Point]] = []
    current: list[Point] = []
    for a, b in zip(points, points[1:]):
        piece = _clip_segment(a, b, x0, z0, x1, z1)
        if piece is None:
            if len(current) > 1:
                runs.append(current)
            current = []
            continue
        pa, pb = piece
        if current and _same_point(current[-1], pa):
            current.append(pb)
        else:
            if len(current) > 1:
                runs.append(current)
            current = [pa, pb]
    if len(current) > 1:
        runs.append(current)
    return runs


def _same_point(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def _clip_segment(a: Point, b: Point, x0: float, z0: float,
                  x1: float, z1: float) -> tuple[Point, Point] | None:
    """Liang-Barsky: the part of ``a``-``b`` inside the rectangle, or None."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]),
                 (-dz, a[1] - z0), (dz, z1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0.0:
                return None      # parallel to this edge and outside it
            continue
        t = q / p
        if p < 0.0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    if t1 - t0 < 1e-9:
        return None              # touches a corner; nothing to build
    return ((a[0] + t0 * dx, a[1] + t0 * dz),
            (a[0] + t1 * dx, a[1] + t1 * dz))


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
    # Marsh before water, so the pools sit *in* the fen rather than being
    # painted over by it. A wetland is a sheet of wet ground with standing
    # water in the hollows, and that is the order it has to be laid in.
    for area in layout.areas_of("marsh"):
        paint(_fill_polygon(shift(area.ring), width, depth), MARSH)
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
        elif road.kind == "trail":
            # A trail is trodden earth, not laid cobble -- FTG draws it 1.5 m
            # wide, exactly one tile, and the else branch below was paving it
            # as STREET. LANE is the surface that already means "worn ground
            # people walk" (`_trace_lanes` makes the same class between back
            # gardens, and the two cannot fight: the tracer only seeds and
            # grows over GROUND, so an imported trail cell is simply already
            # done). No class is set: a footpath owes nobody two abreast, and
            # a classed cell would be held to the carriageway standard.
            # And no WATER in the over set -- a footpath does not bridge.
            # Before this a trail crossing a stream was paved straight over
            # it, a cobble ford at grade with nothing beneath; now the cells
            # stay water and the path resumes on the far bank. (Trails rank
            # with planks in the paint order, so a later road may still pave
            # its own junction over the path -- the busier way wins the cell,
            # same rule the street classes settle collisions by.)
            paint(cells, LANE, over=frozenset({GROUND, FIELD}))
        else:
            # MARSH is in this set so a causeway or a reedcutters' track can
            # cross the fen. Leave it out and every way into a wetland stops
            # dead at its edge, which is the "silently dropped feature" shape
            # this project keeps rediscovering.
            paint(cells, STREET, over=frozenset({GROUND, FIELD, MARSH, WATER, LANE}))
            for x, z in cells:
                if (tm.inside(x, z) and tm.surface[z][x] == STREET
                        and _CLASS_RANK[cls] > _CLASS_RANK[tm.street_class[z][x]]):
                    tm.street_class[z][x] = cls

    # Authored bridges: FTG's `raised` quads arrive as LayoutArea("bridge")
    # and become PIER, which is all `_lay_bridges` needs -- the deck, the
    # rails and the channel bedded on beneath all follow from the surface
    # class, exactly as they do for the planks MFCG draws. Two rules:
    #
    # * **Only water becomes deck.** The authored quad overhangs its banks --
    #   a ~20x20 m quad is wider than the stream it crosses, so its abutments
    #   land on dry ground -- and a pier tile over grass is a timber platform
    #   on a lawn. The overhang simply stays whatever the bank is; the deck is
    #   laid by its top at grade (`_lay_bridges`), so it meets that bank flush
    #   with nothing added.
    # * **Painted after the roads**, because rivers arrive as *roads* on MFCG
    #   maps and the road loop paints them unconditionally -- PIER laid before
    #   it would be erased by the very channel it is meant to cross. After the
    #   loop, `over={WATER}` reads the finished water extent, and a road that
    #   already claimed its own crossing (STREET paints over WATER; that is
    #   today's road bridge) keeps it -- the quad decks the water around it.
    for area in layout.areas_of("bridge"):
        paint(_fill_polygon(shift(area.ring), width, depth), PIER,
              over=frozenset({WATER}))

    for area in layout.areas_of("plaza"):
        paint(_fill_polygon(shift(area.ring), width, depth), PLAZA)
    for area in layout.areas_of("park"):
        paint(_fill_polygon(shift(area.ring), width, depth), GROUND)

    # Forest rings become a mask, not a surface. Underfoot a forest is the
    # same grass the rasteriser lays everywhere (`ftg.BACKGROUND_AREAS` says
    # why the kinds are carried at all), so painting a surface would change
    # nothing and cost the distinction; the outline rides to the builder on
    # `TileMap.forest` instead, and `build._dress_districts` reads it to put
    # the wood inside the line and the glades outside it.
    for area in layout.areas_of("forest"):
        tm.forest.update(_fill_polygon(shift(area.ring), width, depth))

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

    # Field boundaries ride through as geometry rather than as cells, clipped
    # to the board here so nothing downstream has to think about the overhang.
    tm.fences = [
        run for line in layout.fences
        for run in clip_polyline(shift(line), 0.0, 0.0, float(width), float(depth))
    ]

    _regularise_buildings(tm)
    _notch_buildings(tm)
    _absorb_fragments(tm)
    _rasterize_walls(tm, layout, shift, width, depth)
    if bridges:
        tm.bridges = _bridge_water_gaps(tm, layout)
    _carve_plaza(tm)
    _trace_lanes(tm)
    # Before the perimeters: the split changes which cells belong to which
    # id, and the shell is computed off that.
    split_churches(tm)
    _find_perimeters(tm, layout)
    _place_doors(tm, layout)
    # After the doors, because a court is laid to reach them.
    _lay_courts(tm)
    return tm


#: How wide a court corridor is laid, in cells. Two is 10 ft -- the same floor
#: every other way on the map is held to, because a forecourt somebody cannot
#: walk down two abreast is a path, not a court.
COURT_WIDTH = 2


def compounds(tm: TileMap) -> dict[str, str]:
    """Building id -> the id of the enclosure it stands in, where there is one.

    **A closed boundary run means one property, and that is read from the
    input rather than declared.** A keep and its garrison range inside a
    barricade are not two houses that happen to stand near each other: they
    share a wall, a gate and a forecourt, and on the board they have to read
    that way -- rather than as two cottages that each fenced their own garden
    inside somebody else's stockade, which is what they did look like.

    The signal is a *closed* run in :attr:`TileMap.fences`. An open run is a
    field boundary and encloses nothing; a closed one is somebody's perimeter.
    That distinction already survives the import and the clip, so nothing new
    has to be carried, and anything else with a perimeter -- a stock pen, a
    temple precinct, a walled farmstead -- gets the same treatment for free,
    because the test is the geometry and never the kind.

    Buildings are tested by centroid, which is enough: a footprint straddling
    an enclosure line is a map error rather than a case to handle.
    """
    from .layout import point_in_polygon

    rings: list[tuple[str, list[Point]]] = []
    for i, run in enumerate(tm.fences or ()):
        if len(run) < 4:
            continue
        (x0, z0), (x1, z1) = run[0], run[-1]
        if abs(x0 - x1) > 0.01 or abs(z0 - z1) > 0.01:
            continue                      # an open run is a boundary, not a pen
        rings.append((f"compound-{i:02d}", list(run)))
    if not rings:
        return {}

    cells: dict[str, list[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                cells.setdefault(bid, []).append((x, z))

    out: dict[str, str] = {}
    for bid, cs in cells.items():
        cx = sum(c[0] for c in cs) / len(cs) + 0.5
        cz = sum(c[1] for c in cs) / len(cs) + 0.5
        for cid, ring in rings:
            if point_in_polygon(ring, (cx, cz)):
                out[bid] = cid
                break
    return out


def _walk_to(tm: TileMap, starts: set[tuple[int, int]], goal,
             passable: frozenset[str]) -> list[tuple[int, int]]:
    """Shortest path from any of ``starts`` to the first cell ``goal`` accepts.

    **Breadth-first and not a straight line, because a compound has buildings
    in it.** The first cut of this ran an L-shaped corridor between doors, and
    on Sedgewater the L from the keep's door to the garrison's north door goes
    straight through the garrison: every cell of it is a building cell, every
    one is skipped, and the court silently stopped short. A courtyard path has
    to go *round* what is standing in it, which is what a path is.

    Returns the cells walked, excluding the goal itself -- the goal is either
    an existing way or already-laid court, and neither wants repainting.
    Empty when there is no route.
    """
    prev: dict[tuple[int, int], tuple[int, int] | None] = {c: None for c in starts}
    queue = deque(starts)
    found: tuple[int, int] | None = None
    while queue and found is None:
        x, z = queue.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in prev or not tm.inside(*n):
                continue
            if tm.building[n[1]][n[0]] or tm.wall[n[1]][n[0]]:
                continue
            if goal(n):
                prev[n] = (x, z)
                found = n
                break
            if tm.surface[n[1]][n[0]] not in passable:
                continue
            prev[n] = (x, z)
            queue.append(n)
    if found is None:
        return []
    out: list[tuple[int, int]] = []
    node = prev[found]
    while node is not None:
        out.append(node)
        node = prev[node]
    return out


def _widen(cells, width: int) -> set[tuple[int, int]]:
    """Fatten a one-cell path to ``width`` across, so it is a court and not a trail."""
    out: set[tuple[int, int]] = set()
    half = width // 2
    for x, z in cells:
        for dz in range(-half, width - half):
            for dx in range(-half, width - half):
                out.add((x + dx, z + dz))
    return out


def _lay_courts(tm: TileMap) -> None:
    """Pave the forecourt of every enclosed property.

    A compound's buildings share one entrance, so the ground between their
    doors is a *court* -- laid stone somebody keeps swept -- and not the strip
    of trodden earth each would have claimed on its own. Three things get
    paved, and each answers a different half of "can you walk in and get to
    the doors":

    * the cell each door opens onto, so no door opens onto grass
    * a corridor joining every door to the first, so the doors connect to each
      other rather than to two separate patches of paving. On Sedgewater the
      keep and its garrison stand 6.4 tiles apart while a yard apron reaches
      2, so without this there is a bare strip between two paved aprons.
    * a spur from the court to the nearest way outside, which is the walkway
      in. Without it the court is a paved island inside a stockade.

    Only ``GROUND`` and ``MARSH`` are overpainted. A court never eats a
    street, a building, water or the enclosure itself.
    """
    from collections import deque

    by_compound: dict[str, list[str]] = {}
    for bid, cid in compounds(tm).items():
        by_compound.setdefault(cid, []).append(bid)
    if not by_compound:
        return

    pavable = frozenset({GROUND, MARSH})

    def paint(cells) -> None:
        for x, z in cells:
            if not tm.inside(x, z):
                continue
            if tm.building[z][x] or tm.wall[z][x]:
                continue
            if tm.surface[z][x] in pavable:
                tm.surface[z][x] = COURT

    for cid, bids in sorted(by_compound.items()):
        aprons: list[tuple[int, int]] = []
        for bid in sorted(bids):
            for x, z, side in tm.doors.get(bid, ()):
                dx, dz = next((d, e) for s, d, e in SIDES if s == side)
                aprons.append((x + dx, z + dz))
        if not aprons:
            continue

        ways = frozenset({STREET, LANE, PLAZA, PIER})
        paint(aprons)

        # Join every door to the court already laid, going round whatever
        # stands between them.
        #
        # **Seeded with ONE apron and grown, not with all of them.** Seeded
        # with the lot, each apron only had to reach *some* other apron, so a
        # four-door compound came out as two joined pairs -- a court in two
        # pieces, which is the failure this is supposed to prevent and which
        # passed on the real map by luck of the geometry. The region has to be
        # a single growing thing for "one property" to mean anything.
        crossable = pavable | {COURT}
        laid = {aprons[0]}
        for apron in aprons[1:]:
            if apron in laid:
                continue
            walk = _walk_to(tm, {apron}, lambda n: n in laid, crossable)
            if not walk:
                continue
            fat = _widen(walk, COURT_WIDTH)
            paint(fat)
            laid |= fat | {apron}

        # The way in. **Started from the court and not from the doors**: on
        # Sedgewater the garrison's north door already opens straight onto the
        # road, so a search seeded with the doors found a way in its first step
        # and paved nothing, while the court itself stayed an island behind the
        # buildings. The question is whether the *court* reaches a way.
        if laid and not any(
                tm.inside(x + dx, z + dz) and tm.surface[z + dz][x + dx] in ways
                for (x, z) in laid for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            walk = _walk_to(tm, set(laid),
                            lambda n: tm.surface[n[1]][n[0]] in ways, pavable)
            paint(_widen(walk, COURT_WIDTH))


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


#: Bounds on the carved square's area, in cells. The floor is a widened
#: junction -- room for a well, one stall row and a brawl -- which is all the
#: market a hamlet has. The ceiling stops a fallback from paving a quarter of
#: a big town; a town that big has an authored square in its export, and the
#: carve never runs against one (it is a fallback, guarded below).
PLAZA_MIN_AREA = 24
PLAZA_MAX_AREA = 256

#: Tiles of market per sqrt(building). Fixed by the one authored market on
#: hand: East Tradebourne's "Warden Market" rasterises to 631 plaza tiles in a
#: town of 991 buildings, and 631 / sqrt(991) = 20.0. The *shape* of the law
#: is chosen by a frontage measurement rather than by the data point (one
#: point fits any curve): scaling linearly from that same market gives Forest
#: Church 33 cells, and a 33-cell square at its busiest junction touches zero
#: building frontage, because main streets are four tiles wide and buildings
#: stand a verge back from the carriageway -- the square drowns in its own
#: junction. At 20*sqrt(51) = 143 cells it reaches the facades (18 of 39
#: perimeter cells against a frontage, measured).
PLAZA_TILES_PER_ROOT_BUILDING = 20.0

#: Seed scoring radii, in cells (Chebyshev). Traffic is street cells within 3
#: -- the same 7x7 window the fixed carve scored. Frontage is building cells
#: within 5, because buildings stand back from the carriageway: scored on
#: traffic alone the seed lands mid-junction and the grown square touches
#: 0-11 frontage cells on the two towns measured; adding the frontage term
#: moves it to where the busiest street meets the densest block and the same
#: growth touches 18 and 26.
PLAZA_TRAFFIC_RADIUS = 3
PLAZA_FRONTAGE_RADIUS = 5

#: What a plaza may pave over: public open ground only. FIELD is someone's
#: crop, PIER is over water, and a building or town-wall cell is never
#: touched, so the square is by construction the leftover room between
#: frontages -- which is what a medieval market square is.
_PLAZA_GROWS_OVER = frozenset({STREET, GROUND, LANE})


def _plaza_target_area(tm: "TileMap") -> int:
    buildings = len({b for row in tm.building for b in row if b})
    want = round(PLAZA_TILES_PER_ROOT_BUILDING * math.sqrt(max(1, buildings)))
    return max(PLAZA_MIN_AREA, min(PLAZA_MAX_AREA, want))


def _carve_plaza(tm: "TileMap") -> None:
    """Open a market square where the busiest street meets the densest block.

    MFCG's squares can come through an export empty, so the town has no plaza
    at all -- no public room, nowhere for the well and the market the dressing
    pass lays, and nowhere for a party to be accosted. This is a **fallback**:
    a town whose export authored its own square (FTG's MARKET/PAVEMENT
    polygons, or an MFCG file whose squares survive) keeps it untouched.

    The old carve stamped a fixed 7x7 block onto the clearest patch of street,
    which put an axis-aligned rectangle in the middle of a four-tile
    carriageway: 1 of its 24 perimeter cells touched a building on Forest
    Church. A market square is not a stamped shape -- it is the leftover space
    between frontages. So the square is *grown* outward from the seed over
    open ground, sized to the town, and takes whatever outline the
    surrounding buildings give it.
    """
    # The fallback guard. Authored plazas were painted from the layout's own
    # polygons before buildings; carving another square onto a town that has
    # one puts two markets a street apart and the second is a lie.
    if any(s == PLAZA for row in tm.surface for s in row):
        return

    # Seed: the street cell with the most traffic *and* the most frontage
    # around it. Strictly-greater comparison in scan order, so ties resolve
    # the same way on every run.
    best: tuple[int, tuple[int, int]] | None = None
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] != STREET:
                continue
            score = 0
            r = PLAZA_TRAFFIC_RADIUS
            for dz in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, nz = x + dx, z + dz
                    if tm.inside(nx, nz) and tm.surface[nz][nx] == STREET:
                        score += 1
            r = PLAZA_FRONTAGE_RADIUS
            for dz in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, nz = x + dx, z + dz
                    if tm.inside(nx, nz) and tm.building[nz][nx]:
                        score += 1
            if best is None or score > best[0]:
                best = (score, (x, z))
    if best is None:
        return
    seed = best[1]

    cells = _grow_plaza(tm, seed, _plaza_target_area(tm))
    for x, z in cells:
        tm.surface[z][x] = PLAZA


def _grow_plaza(tm: "TileMap", seed: tuple[int, int],
                target: int) -> set[tuple[int, int]]:
    """The open room around ``seed``: grown, smoothed, one connected piece.

    Breadth-first over public open ground, nearest cells first, stopping at
    ``target`` cells -- so the region is a disc clipped by whatever frontages,
    walls and water surround the junction. The radius cap keeps the disc from
    running off down the streets when the junction is open on one side: a
    disc of area A has radius about sqrt(A/2) under the BFS metric, and
    anything much past that is a corridor, not a square.
    """
    max_r = math.ceil(math.sqrt(target / 2.0)) + 2

    def open_ground(x: int, z: int) -> bool:
        return (tm.inside(x, z) and not tm.building[z][x] and not tm.wall[z][x]
                and (x, z) not in tm.gates
                and tm.surface[z][x] in _PLAZA_GROWS_OVER)

    dist = {seed: 0}
    order = [seed]
    queue = deque([seed])
    while queue:
        x, z = queue.popleft()
        if dist[(x, z)] >= max_r:
            continue
        for _, dx, dz in SIDES:
            n = (x + dx, z + dz)
            if n not in dist and open_ground(*n):
                dist[n] = dist[(x, z)] + 1
                order.append(n)
                queue.append(n)
    region = set(order[:target])

    # Smooth: every plaza cell must belong to a full 2x2 block of plaza. A
    # cell that does not is the tip of a tentacle one cell wide -- the disc
    # leaking down a lane -- and a market square with pseudopodia reads as a
    # paving error. Iterated to a fixpoint because removing a tip exposes the
    # cell behind it.
    while True:
        keep = {
            (x, z) for (x, z) in region
            if any((x + i, z + j) in region and (x + i, z) in region
                   and (x, z + j) in region
                   for i in (-1, 1) for j in (-1, 1))
        }
        if keep == region:
            break
        region = keep

    # One room. Smoothing can cut the region in two; the piece the seed is in
    # is the market, anything else was a pocket reached through a gap the
    # smoothing closed.
    if seed not in region:
        return set()
    comp = {seed}
    queue = deque([seed])
    while queue:
        x, z = queue.popleft()
        for _, dx, dz in SIDES:
            n = (x + dx, z + dz)
            if n in region and n not in comp:
                comp.add(n)
                queue.append(n)
    return comp


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


#: A plot this many cells across or fewer cannot hold a room. 3 cells is 15 ft,
#: and once both walls are taken the interior is ONE cell wide -- a corridor,
#: not somewhere a party stands. `docs/building-massing.md` anchors the whole
#: scale on a 35 ft median frontage for exactly this reason, and until now
#: nothing checked the other end.
THIN_PLOT_TILES = 3


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

        # Only a plot too thin to stand in is widened, and only until it is
        # not. Growing every inscribed rectangle out to its polygon's box is
        # a town-wide re-massing, not a fix: measured on East Tradebourne it
        # took the floor area from 33,702 tiles to 60,845 and the median
        # footprint from 30 to 54. The defect is the sliver, so the sliver is
        # what gets treated.
        if keep and min(bw, bd) <= THIN_PLOT_TILES:
            keep = _grow_rect(tm, keep, cells, (x0, z0, x1, z1), bid)

        for x, z in cells:
            if (x, z) not in keep:
                tm.building[z][x] = ""
                tm.surface[z][x] = GROUND
        for x, z in keep:
            tm.building[z][x] = bid
            tm.surface[z][x] = FLOOR
        if not keep:
            tm.floors.pop(bid, None)


def _grow_rect(tm: "TileMap", keep: set, blob: list, box: tuple, bid: str) -> set:
    """Widen the inscribed rectangle back out, inside the polygon's own box.

    **The largest inscribed rectangle of a ROTATED building is a sliver, and
    that -- not terracing -- is where thin plots come from.** Measured on East
    Tradebourne before this existed: 80 of 989 buildings had a short side of
    3 cells or less, and **all 80 came from a polygon that was not thin**.
    `house-0562` rasterises to an 11x10 blob of 38 cells and the best
    axis-aligned rectangle inside it is 3x4 -- twelve cells, 68% of the
    building thrown away. FTG exports buildings at arbitrary angles; the raster
    is axis-aligned; a diagonal band has no fat rectangle in it.

    So the plot is widened again, and the rule is what keeps it honest: a cell
    may be claimed only if it is **inside the polygon's own bounding box** and
    is either part of that polygon's blob or open ground nobody else holds. No
    road, no wall, no neighbour, and nothing outside the footprint the export
    actually drew. A whole row or column at a time, so the result stays the
    rectangle `_regularise_buildings` exists to produce; widest side last, so
    a sliver squares up rather than growing longer.

    Order matters and is deterministic: buildings are processed sorted by id,
    and a building already reduced has released its offcuts to ground, so a
    later neighbour may claim them. That is the same first-claim rule the rest
    of the raster runs on.
    """
    bx0, bz0, bx1, bz1 = box
    inside = set(blob)
    xs = [c[0] for c in keep]
    zs = [c[1] for c in keep]
    kx0, kx1, kz0, kz1 = min(xs), max(xs), min(zs), max(zs)

    def free(x, z):
        if not (bx0 <= x <= bx1 and bz0 <= z <= bz1):
            return False
        if (x, z) in inside:
            return True                  # the polygon's own ground
        if not (0 <= x < tm.width and 0 <= z < tm.depth):
            return False
        held = tm.building[z][x]
        return ((not held or held == bid) and tm.surface[z][x] == GROUND
                and not tm.wall[z][x])

    grew = True
    while grew and min(kx1 - kx0, kz1 - kz0) + 1 <= THIN_PLOT_TILES:
        grew = False
        # Take the short axis first: a sliver wants to square up, not lengthen.
        sides = ("w", "e", "n", "s")
        if (kx1 - kx0) > (kz1 - kz0):
            sides = ("n", "s", "w", "e")
        for side in sides:
            if side == "w":
                line, span = kx0 - 1, [(kx0 - 1, z) for z in range(kz0, kz1 + 1)]
            elif side == "e":
                line, span = kx1 + 1, [(kx1 + 1, z) for z in range(kz0, kz1 + 1)]
            elif side == "n":
                line, span = kz0 - 1, [(x, kz0 - 1) for x in range(kx0, kx1 + 1)]
            else:
                line, span = kz1 + 1, [(x, kz1 + 1) for x in range(kx0, kx1 + 1)]
            if not all(free(x, z) for x, z in span):
                continue
            keep |= set(span)
            if side == "w":
                kx0 -= 1
            elif side == "e":
                kx1 += 1
            elif side == "n":
                kz0 -= 1
            else:
                kz1 += 1
            grew = True
            break
    return keep


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

#: What a successful carve hands back: the cells cut, the passage direction,
#: the across-the-passage direction, and the ``(lo, width)`` of the strip --
#: everything `_add_second_gate` needs to pave the approach along the same
#: line. ``None`` means nothing was cut, and it is the *only* failure value.
GateCut = tuple[
    list[tuple[int, int]], tuple[int, int], tuple[int, int], tuple[int, int]
]


def _carve_gate(tm: TileMap, cx: int, cz: int, road_width: float) -> GateCut | None:
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
        # None, not False: `_add_second_gate` tests ``carved is None`` and
        # then unpacks, so a bare False here was a latent TypeError -- the
        # annotation said bool while the success path returned a tuple.
        return None

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


#: A temple below this many cells is not split. Measured on the seven real
#: temples across five towns: a 75/25 cut gives 102 -> 77/25, 88 -> 66/22,
#: 81 -> 61/20, 65 -> 49/16 and 52 -> 39/13, all of which are two usable
#: rooms; 30 -> 23/8 is not, because an 8-cell chancel is a cupboard. 50 is
#: the floor that keeps every real split honest, and it leaves 2 of 7
#: unsplit -- which is a fact about those churches and is REPORTED rather
#: than fixed by lowering the bar.
CHURCH_MIN_SPLIT_CELLS = 50

#: How much of the long axis the chancel takes. A real chancel runs a third to
#: a half of the nave's length; a quarter of the WHOLE gives about a third of
#: what is left, which is the low end of that and keeps the nave dominant.
CHANCEL_SHARE = 0.25

#: The chancel is narrower than the nave by this many cells on each side,
#: where there is room. The inset is what makes the join read as a step from
#: outside rather than as one long box with a change of roof height.
CHANCEL_INSET = 1


def split_churches(tm: TileMap) -> int:
    """Cut a chancel off every temple big enough for one. Returns the count.

    **Derived from the imported polygon, not from a template.** The footprints
    come from MFCG and FTG as real outlines at real angles, and the largest
    temple in five towns is 102 cells; every hand-authored plan tried here was
    176-312, so templating would mean scaling a rectangle onto someone else's
    outline and throwing away the one thing the export actually gave us.
    Subdividing keeps it.

    The chancel goes at the end of the long axis FURTHEST FROM THE STREET,
    which is the liturgical east end in practice: a church is entered from the
    public side and the altar is at the far one. Measured against the door
    would be circular -- doors are placed after this runs.

    Both parts keep the imported id as a stem (`temple-0002`,
    `temple-0002+chancel`) rather than minting a new number. Both importers
    number every footprint from one global counter and `boards.json` MOVED
    detection keys on it, so a new id would renumber the town.
    """
    from collections import defaultdict

    cells: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid and bid.split("-")[0] == "temple":
                cells[bid].append((x, z))

    split = 0
    for bid, own in sorted(cells.items()):
        if len(own) < CHURCH_MIN_SPLIT_CELLS:
            continue
        xs = [c[0] for c in own]
        zs = [c[1] for c in own]
        w, d = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        along_z = d >= w
        span = d if along_z else w
        cut = max(2, int(round(span * CHANCEL_SHARE)))
        if span - cut < 3:
            continue

        # Which end is furthest from a street: that is the altar end.
        def street_gap(lo_end: bool) -> int:
            best = 10**6
            for x, z in own:
                v = z if along_z else x
                lim = (min(zs if along_z else xs) + cut) if lo_end \
                    else (max(zs if along_z else xs) - cut)
                if (v < lim) if lo_end else (v > lim):
                    for dz in range(-6, 7):
                        for dx in range(-6, 7):
                            nx, nz = x + dx, z + dz
                            if tm.inside(nx, nz) and tm.surface[nz][nx] == STREET:
                                best = min(best, abs(dx) + abs(dz))
            return best

        far_low = street_gap(True) > street_gap(False)
        lo = min(zs if along_z else xs)
        hi = max(zs if along_z else xs)
        keep = (lambda v: v < lo + cut) if far_low else (lambda v: v > hi - cut)

        # Inset the chancel off both flanks where there is room to.
        cross_lo = min(xs if along_z else zs)
        cross_hi = max(xs if along_z else zs)
        inset = CHANCEL_INSET if (cross_hi - cross_lo + 1) >= 5 else 0

        chancel = f"{bid}+chancel"
        moved = 0
        for x, z in own:
            v = z if along_z else x
            c = x if along_z else z
            if not keep(v):
                continue
            if c < cross_lo + inset or c > cross_hi - inset:
                tm.building[z][x] = ""      # trimmed by the inset
                tm.surface[z][x] = GROUND
                continue
            tm.building[z][x] = chancel
            moved += 1
        if moved < 6:
            # Not enough left to be a room; put it back rather than ship a
            # cupboard with its own roof.
            for x, z in own:
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
            continue
        tm.floors[chancel] = tm.floors.get(bid, 1)
        tm.church_parts[bid] = (bid, "nave")
        tm.church_parts[chancel] = (bid, "chancel")
        split += 1
    return split


def _find_perimeters(tm: TileMap, layout: Layout | None) -> None:
    """Record which cell edges form each building's outer shell.

    **A church complex is ONE shell, not one per volume.** Two abutting parts
    each closing their own ring is what made a five-volume church five sealed
    rooms: the nave's south wall and the crossing's north wall were both
    built, back to back, and `_place_doors` opens only onto outdoors -- so
    nothing led from one to the next. Measured before this: 8 nave cells
    abutting the crossing, and not one door among them.

    Treating siblings as the same building drops the shared wall entirely,
    which is the chancel arch by omission. The parts keep their own ids for
    everything else -- roofs hip per part, `SUBORDINATE_STEP` steps per part
    -- because those are the two things that SHOULD differ across the join.
    """
    complexes = {bid: nave for bid, (nave, _r) in tm.church_parts.items()}

    def same(a: str, b: str) -> bool:
        if a == b:
            return True
        return bool(a) and bool(b) and complexes.get(a) == complexes.get(b) \
            and a in complexes
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if not bid:
                continue
            for side, dx, dz in SIDES:
                nx, nz = x + dx, z + dz
                if not tm.inside(nx, nz) or not same(bid, tm.building[nz][nx]):
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

    **A subordinate church part gets none.** You enter a church through its
    nave; a chancel with its own street door is a shed that happens to touch a
    church. Before this every volume got its own external doorway -- five
    volumes, nine doors, and not one of them between two parts.

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
        # A subordinate church part is entered through its nave, not off the
        # street. Skipping it here is the other half of dropping the shared
        # wall in `_find_perimeters`: one shell, one way in.
        role = tm.church_parts.get(bid, ("", ""))[1]
        if role and role != "nave":
            continue
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
