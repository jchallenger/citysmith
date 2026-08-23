"""Polygonal city layout -- the model between a layout source and the tile grid.

The TaleSpire side of citysmith works in axis-aligned tiles, but real city
layouts are polygonal: streets bend, wards are irregular, buildings sit at
whatever angle their plot allows. This module is that polygonal world. A layout
source (currently the MFCG importer) produces a :class:`Layout`; the rasteriser
turns it into tiles.

Coordinates here are already in **tile units** -- the importer does the scaling,
so everything downstream can reason in the same units as the slab format.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

LAYOUT_VERSION = 2

#: A creature occupies 5 ft, and TaleSpire's grid square is one creature wide.
#: This is the fixed constant the whole pipeline is pinned to: everything else
#: -- how many tiles a town spans -- is *derived* from real-world dimensions.
TILE_FEET = 5.0

Point = tuple[float, float]


# -- geometry helpers ---------------------------------------------------------

def close_ring(ring: list[Point]) -> list[Point]:
    """Ensure a polygon ring is explicitly closed.

    MFCG's export is known to omit the closing point; the reference Unity
    importer patches the same way.
    """
    if len(ring) >= 2 and ring[0] != ring[-1]:
        return [*ring, ring[0]]
    return ring


def open_ring(ring: Sequence[Point]) -> list[Point]:
    """Drop a duplicated closing point, for algorithms that want it open."""
    pts = list(ring)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def bounds(points: Iterable[Point]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def centroid(ring: Sequence[Point]) -> Point:
    pts = open_ring(ring)
    if not pts:
        return (0.0, 0.0)
    area = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-12:  # degenerate: fall back to the mean
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    area *= 0.5
    return (cx / (6 * area), cy / (6 * area))


def polygon_area(ring: Sequence[Point]) -> float:
    pts = open_ring(ring)
    n = len(pts)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) * 0.5


def point_in_polygon(ring: Sequence[Point], pt: Point) -> bool:
    """Standard ray-cast test. ``ring`` may be open or closed."""
    pts = open_ring(ring)
    x, y = pt
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            denom = y1 - y0
            if abs(denom) < 1e-12:
                continue
            if x < (x1 - x0) * (y - y0) / denom + x0:
                inside = not inside
    return inside


def oriented_extent(ring: Sequence[Point]) -> tuple[float, float]:
    """Return ``(long, short)`` side of the tightest edge-aligned box.

    A rotating-calipers approximation: for a convex-ish footprint the minimum
    bounding box shares an edge with the hull, so testing every edge direction
    is exact enough and needs no hull implementation.
    """
    pts = open_ring(ring)
    if len(pts) < 3:
        return (0.0, 0.0)
    best: tuple[float, float, float] | None = None
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        length = math.hypot(ex, ey)
        if length < 1e-9:
            continue
        ux, uy = ex / length, ey / length
        us = [(p[0] - x0) * ux + (p[1] - y0) * uy for p in pts]
        vs = [-(p[0] - x0) * uy + (p[1] - y0) * ux for p in pts]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0]:
            best = (w * h, max(w, h), min(w, h))
    if best is None:
        return (0.0, 0.0)
    return (best[1], best[2])


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# -- model --------------------------------------------------------------------

@dataclass
class LayoutBuilding:
    id: str
    ring: list[Point]
    kind: str = "house"
    district: str = ""
    floors: int = 1
    inside_walls: bool = False
    #: The name the source authored, where it has one. MFCG exports geometry
    #: only and leaves this empty; FTG names every building, and "The Halfling
    #: and the Fox" is a better encounter hook than "tavern-0042".
    name: str = ""
    #: Built of masonry rather than timber, so the civic wall and door roles
    #: apply. FTG says so per building; MFCG does not.
    stone: bool = False

    @property
    def centroid(self) -> Point:
        return centroid(self.ring)

    @property
    def area(self) -> float:
        return polygon_area(self.ring)

    @property
    def extent(self) -> tuple[float, float]:
        return oriented_extent(self.ring)


@dataclass
class LayoutRoad:
    points: list[Point]
    width: float = 2.0
    kind: str = "road"


@dataclass
class LayoutDistrict:
    id: str
    name: str
    kind: str
    ring: list[Point]


@dataclass
class LayoutArea:
    """A filled region -- plaza, water body, field, park."""

    kind: str
    ring: list[Point]


@dataclass
class Layout:
    """A city as polygons, in tile units."""

    name: str
    source: str = "mfcg"
    units_per_tile: float = 1.0
    feet_per_unit: float = 1.0
    width: float = 0.0
    depth: float = 0.0
    walls: list[list[Point]] = field(default_factory=list)
    gates: list[Point] = field(default_factory=list)
    roads: list[LayoutRoad] = field(default_factory=list)
    districts: list[LayoutDistrict] = field(default_factory=list)
    buildings: list[LayoutBuilding] = field(default_factory=list)
    areas: list[LayoutArea] = field(default_factory=list)
    wall_thickness: float = 2.0
    #: Field boundaries -- drystone walls and hedgerows -- as open polylines.
    #: Distinct from ``walls``, which is the defensive circuit.
    fences: list[list[Point]] = field(default_factory=list)
    #: What decided the scale, for the import report. Sources differ: FTG
    #: declares a metric one, MFCG has to be anchored to a real dimension.
    scale_anchor: str = ""
    #: Source vocabulary this importer did not recognise, by property name.
    #: Reported rather than raised: the value was still imported, under a
    #: default, and a silently dropped feature is invisible on the board.
    unmapped: dict[str, list[str]] = field(default_factory=dict)

    def areas_of(self, kind: str) -> list[LayoutArea]:
        return [a for a in self.areas if a.kind == kind]

    def building(self, building_id: str) -> LayoutBuilding | None:
        for b in self.buildings:
            if b.id == building_id:
                return b
        return None

    @property
    def width_feet(self) -> float:
        return self.width * TILE_FEET

    @property
    def depth_feet(self) -> float:
        return self.depth * TILE_FEET

    def summary(self) -> str:
        kinds: dict[str, int] = {}
        for b in self.buildings:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
        inside = sum(1 for b in self.buildings if b.inside_walls)
        area_kinds: dict[str, int] = {}
        for a in self.areas:
            area_kinds[a.kind] = area_kinds.get(a.kind, 0) + 1
        anchor = f", {self.scale_anchor}" if self.scale_anchor else ""
        fences = f", {len(self.fences)} fence line(s)" if self.fences else ""
        return (
            f"{self.name} [{self.source}] -- {self.width:.0f}x{self.depth:.0f} tiles "
            f"= {self.width_feet:.0f}x{self.depth_feet:.0f} ft "
            f"(1 tile = {TILE_FEET:.0f} ft = {self.units_per_tile:.2f} source "
            f"units{anchor})\n"
            f"  {len(self.buildings)} buildings ({inside} inside the walls): {parts}\n"
            f"  {len(self.districts)} districts, {len(self.roads)} roads, "
            f"{len(self.gates)} gates, {len(self.walls)} wall ring(s){fences}\n"
            f"  areas: {', '.join(f'{v} {k}' for k, v in sorted(area_kinds.items())) or 'none'}"
        )

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "layout_version": LAYOUT_VERSION,
            "name": self.name,
            "source": self.source,
            "units_per_tile": self.units_per_tile,
            "feet_per_unit": self.feet_per_unit,
            "width": self.width,
            "depth": self.depth,
            "wall_thickness": self.wall_thickness,
            "scale_anchor": self.scale_anchor,
            "unmapped": self.unmapped,
            "walls": [[list(p) for p in ring] for ring in self.walls],
            "fences": [[list(p) for p in line] for line in self.fences],
            "gates": [list(g) for g in self.gates],
            "roads": [
                {"points": [list(p) for p in r.points], "width": r.width, "kind": r.kind}
                for r in self.roads
            ],
            "districts": [
                {"id": d.id, "name": d.name, "kind": d.kind,
                 "ring": [list(p) for p in d.ring]}
                for d in self.districts
            ],
            "buildings": [
                {"id": b.id, "kind": b.kind, "district": b.district, "floors": b.floors,
                 "inside_walls": b.inside_walls, "name": b.name, "stone": b.stone,
                 "ring": [list(p) for p in b.ring]}
                for b in self.buildings
            ],
            "areas": [
                {"kind": a.kind, "ring": [list(p) for p in a.ring]} for a in self.areas
            ],
        }

    def save(self, path: str | os.PathLike[str]) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Layout":
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if data.get("layout_version") != LAYOUT_VERSION:
            raise ValueError("layout file version mismatch; re-import it")

        def ring(raw) -> list[Point]:
            return [(float(x), float(y)) for x, y in raw]

        layout = cls(
            name=data["name"], source=data.get("source", "mfcg"),
            units_per_tile=data["units_per_tile"],
            feet_per_unit=data.get("feet_per_unit", 1.0),
            width=data["width"], depth=data["depth"],
            wall_thickness=data.get("wall_thickness", 2.0),
            scale_anchor=data.get("scale_anchor", ""),
            unmapped=data.get("unmapped", {}),
        )
        layout.walls = [ring(r) for r in data["walls"]]
        layout.fences = [ring(r) for r in data.get("fences", [])]
        layout.gates = [(float(x), float(y)) for x, y in data["gates"]]
        layout.roads = [
            LayoutRoad(points=ring(r["points"]), width=r["width"], kind=r["kind"])
            for r in data["roads"]
        ]
        layout.districts = [
            LayoutDistrict(d["id"], d["name"], d["kind"], ring(d["ring"]))
            for d in data["districts"]
        ]
        layout.buildings = [
            LayoutBuilding(
                id=b["id"], ring=ring(b["ring"]), kind=b["kind"],
                district=b["district"], floors=b.get("floors", 1),
                inside_walls=b.get("inside_walls", False),
                name=b.get("name", ""), stone=b.get("stone", False),
            )
            for b in data["buildings"]
        ]
        layout.areas = [LayoutArea(a["kind"], ring(a["ring"])) for a in data["areas"]]
        return layout
