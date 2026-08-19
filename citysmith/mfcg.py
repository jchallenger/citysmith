"""Import a Medieval Fantasy City Generator (MFCG) GeoJSON export.

Verified against a real MFCG **v0.11.5** export. The format is a
``FeatureCollection`` whose features are identified by a top-level ``id``
rather than by ``properties``:

===============  =========================  ==================================
feature id       geometry                   meaning
===============  =========================  ==================================
``values``       (none -- bare Feature)     metadata: roadWidth, wallThickness,
                                            towerRadius, riverWidth, version
``earth``        Polygon                    the landmass
``walls``        GeometryCollection[Polygon] wall circuits (town, then citadel)
``roads``        GeometryCollection[LineString]
``districts``    GeometryCollection[Polygon] wards
``buildings``    MultiPolygon               one entry per building
``prisms``       MultiPolygon               landmark structures (church, keep)
``squares``      MultiPolygon               plazas / market squares
``fields``       MultiPolygon               farmland
``planks``       GeometryCollection[LineString] piers and boardwalks
``rivers``       GeometryCollection[LineString]
``water``        MultiPolygon               lakes and sea
``greens``       MultiPolygon               parks (may be empty)
``trees``        MultiPoint                 (may be empty)
===============  =========================  ==================================

Two robustness notes carried over from the reference Unity importer, both
confirmed necessary: polygon rings are not always closed, and nesting depth is
inconsistent between feature types, so coordinates are extracted by descending
until a coordinate pair is reached rather than by assuming a fixed shape.

MFCG does not label building *types* -- it produces footprints only -- so kinds
are assigned here from footprint size and district, the same weighted model the
procedural generator used.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
from dataclasses import dataclass
from typing import Any, Iterable

from .layout import (
    TILE_FEET,
    Layout,
    LayoutArea,
    LayoutBuilding,
    LayoutDistrict,
    LayoutRoad,
    Point,
    bounds,
    centroid,
    close_ring,
    distance,
    oriented_extent,
    point_in_polygon,
)

#: Default target: the town wall's longest axis spans this many tiles.
DEFAULT_TARGET_TILES = 200

#: A building smaller than this (in tiles) cannot hold a usable interior and is
#: treated as a shed or outbuilding rather than dropped -- it still renders.
MIN_PLAYABLE_SHORT_SIDE = 2.5


class MFCGError(ValueError):
    """Raised when a file is not a usable MFCG export."""


# -- coordinate extraction ----------------------------------------------------

def _is_point(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and all(isinstance(v, (int, float)) for v in value[:2])
    )


def _rings(coords: Any) -> list[list[Point]]:
    """Collect every coordinate ring/line found at any nesting depth.

    MFCG mixes ``Polygon`` (``[ring]``), ``MultiPolygon`` (``[[ring]]``) and
    bare ``LineString`` (``[pt, ...]``) across features, so descending until a
    list of points appears is more reliable than branching on geometry type.
    """
    if not isinstance(coords, list) or not coords:
        return []
    if _is_point(coords[0]):
        return [[(float(p[0]), float(p[1])) for p in coords if _is_point(p)]]
    out: list[list[Point]] = []
    for item in coords:
        out.extend(_rings(item))
    return out


def _geometry_rings(feature: dict) -> list[list[Point]]:
    """All rings/lines in a feature, flattening any GeometryCollection."""
    if feature.get("type") == "GeometryCollection" or "geometries" in feature:
        out: list[list[Point]] = []
        for geom in feature.get("geometries") or ():
            out.extend(_rings(geom.get("coordinates")))
        return out
    return _rings(feature.get("coordinates"))


def _polygons(feature: dict | None) -> list[list[Point]]:
    """Closed rings with degenerate shapes removed."""
    if not feature:
        return []
    out = []
    for ring in _geometry_rings(feature):
        if len(ring) < 3:
            continue  # a polygon needs three distinct corners
        out.append(close_ring(ring))
    return out


def _lines(feature: dict | None) -> list[list[Point]]:
    if not feature:
        return []
    return [line for line in _geometry_rings(feature) if len(line) >= 2]


# -- building kinds -----------------------------------------------------------

#: District kind -> (building kind, weight). Mirrors the procedural generator so
#: site scoring behaves identically whichever source produced the layout.
_DISTRICT_BUILDINGS: dict[str, list[tuple[str, int]]] = {
    "civic": [("guildhall", 3), ("manor", 3), ("barracks", 2), ("temple", 2), ("house", 2)],
    "market": [("shop", 6), ("tavern", 3), ("warehouse", 2), ("apothecary", 2), ("house", 2)],
    "craft": [("smithy", 4), ("shop", 3), ("warehouse", 2), ("house", 3), ("stable", 2)],
    "residential": [("house", 8), ("tavern", 2), ("shop", 2), ("apothecary", 1)],
    "docks": [("warehouse", 5), ("tavern", 3), ("shop", 2), ("house", 2)],
    "slums": [("house", 6), ("tavern", 2), ("warehouse", 1)],
    "temple": [("temple", 4), ("house", 2), ("guildhall", 1)],
    "farm": [("house", 4), ("stable", 3), ("warehouse", 2)],
}

#: Radial bands used to name and type wards, by normalised distance from centre.
_BANDS: list[tuple[float, list[str]]] = [
    (0.25, ["civic", "market"]),
    (0.50, ["market", "craft"]),
    (0.78, ["craft", "residential"]),
    (1.01, ["residential", "slums"]),
]


def _weighted(options: list[tuple[str, int]], rng: random.Random) -> str:
    total = sum(w for _, w in options)
    roll = rng.randrange(total)
    upto = 0
    for name, weight in options:
        upto += weight
        if roll < upto:
            return name
    return options[-1][0]


# -- import -------------------------------------------------------------------

def load_geojson(path: str | os.PathLike[str]) -> dict[str, dict]:
    """Read an MFCG export and index its features by id."""
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MFCGError(f"Could not read {p}: {exc}") from exc

    if data.get("type") != "FeatureCollection":
        raise MFCGError(
            f"{p} is not a GeoJSON FeatureCollection. Export from MFCG with "
            "Settlement > Export as > JSON."
        )
    features = {f.get("id"): f for f in data.get("features", []) if isinstance(f, dict)}
    if "buildings" not in features:
        raise MFCGError(
            f"{p} has no 'buildings' feature -- this does not look like an MFCG "
            f"export (found: {', '.join(sorted(str(k) for k in features)) or 'nothing'})."
        )
    return features


#: Real-world dimensions used to anchor MFCG's arbitrary world units. A town's
#: tile count is *derived* from these -- a creature occupies 5 ft and a tile is
#: one creature wide, so the map's real size dictates its size in tiles, never
#: the other way round.
DEFAULT_HOUSE_FRONTAGE_FT = 20.0

#: Minimums below which the map stops working as a battle map: a creature needs
#: a 5 ft square, so a room narrower than 3 tiles cannot be fought in and a
#: street narrower than 2 tiles cannot be passed abreast.
MIN_HOUSE_FRONTAGE_TILES = 3.0
MIN_ROAD_TILES = 2.0


@dataclass
class Scale:
    """Resolved mapping between MFCG units, feet, and 5 ft tiles."""

    feet_per_unit: float
    anchor: str

    @property
    def units_per_tile(self) -> float:
        return TILE_FEET / self.feet_per_unit

    def tiles(self, units: float) -> float:
        return units / self.units_per_tile

    def feet(self, units: float) -> float:
        return units * self.feet_per_unit


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def resolve_scale(
    features: dict[str, dict],
    *,
    house_frontage_ft: float = DEFAULT_HOUSE_FRONTAGE_FT,
    feet_per_unit: float | None = None,
) -> Scale:
    """Work out how many feet one MFCG world unit represents.

    MFCG's units are arbitrary and undocumented, so the scale is pinned to a
    real-world dimension instead. Median house frontage is the default anchor
    because building size is what decides whether the result is playable --
    a room has to hold creatures on a 5 ft grid.
    """
    if feet_per_unit is not None:
        if feet_per_unit <= 0:
            raise MFCGError("--feet-per-unit must be positive.")
        return Scale(feet_per_unit, "explicit")

    rings = _polygons(features.get("buildings"))
    shorts = [oriented_extent(r)[1] for r in rings]
    shorts = [s for s in shorts if s > 0]
    if not shorts:
        raise MFCGError("Export has no usable building footprints to derive scale from.")
    return Scale(house_frontage_ft / _median(shorts), f"house frontage = {house_frontage_ft:.0f} ft")


def check_playability(layout: Layout, meta: dict) -> list[str]:
    """Warn where the derived scale makes the map unplayable at 5 ft per tile."""
    problems: list[str] = []
    shorts = [b.extent[1] for b in layout.buildings if b.kind != "shed"]
    if shorts:
        median_short = _median(shorts)
        if median_short < MIN_HOUSE_FRONTAGE_TILES:
            problems.append(
                f"median building is {median_short:.1f} tiles "
                f"({median_short * TILE_FEET:.0f} ft) across -- under "
                f"{MIN_HOUSE_FRONTAGE_TILES:.0f} tiles there is no room to fight indoors"
            )
    roads = [r.width for r in layout.roads if r.kind == "road"]
    if roads and max(roads) < MIN_ROAD_TILES:
        problems.append(
            f"widest street is {max(roads):.1f} tiles "
            f"({max(roads) * TILE_FEET:.0f} ft) -- creatures cannot pass abreast"
        )
    if layout.wall_thickness < 1.0:
        problems.append(f"town wall is under one tile thick ({layout.wall_thickness:.1f})")
    return problems


def import_layout(
    path: str | os.PathLike[str],
    *,
    house_frontage_ft: float = DEFAULT_HOUSE_FRONTAGE_FT,
    feet_per_unit: float | None = None,
    margin_feet: float = 60.0,
    clip: bool = True,
    name: str | None = None,
    seed: int = 0,
) -> Layout:
    """Import an MFCG GeoJSON export as a :class:`Layout` in 5 ft tiles.

    The town's size in tiles is *derived*: the scale is anchored to a real
    dimension (median house frontage by default), and the tile count follows
    from how many 5 ft squares the result occupies. ``margin_feet`` keeps that
    much suburb around the walls; ``clip`` disabled keeps the whole export.
    """
    features = load_geojson(path)
    meta = features.get("values") or {}
    scale = resolve_scale(
        features, house_frontage_ft=house_frontage_ft, feet_per_unit=feet_per_unit
    )
    units_per_tile = scale.units_per_tile
    wall_rings = _polygons(features.get("walls"))

    # Establish the crop window before scaling, so margins are in tile units.
    #
    # The window is the *settlement* extent -- walls and buildings together --
    # not the walls alone. Many exports wall only a citadel: Forest Church rings
    # a castle 120 units across while its village sprawls 500 units past it, so
    # clipping to the wall would throw the whole town away. Taking the union
    # keeps a walled town identical (its wall already contains its buildings)
    # while making an unwalled or part-walled one crop to where people live,
    # instead of to the fields, which reach far beyond anything playable.
    settlement = list(wall_rings) + _polygons(features.get("buildings"))
    if settlement and clip:
        wx0, wy0, wx1, wy1 = bounds([p for ring in settlement for p in ring])
        m = (margin_feet / TILE_FEET) * units_per_tile
        window = (wx0 - m, wy0 - m, wx1 + m, wy1 + m)
    else:
        window = None

    def keep(ring: list[Point]) -> bool:
        if window is None:
            return True
        x0, y0, x1, y1 = bounds(ring)
        return not (x1 < window[0] or x0 > window[2] or y1 < window[1] or y0 > window[3])

    kept: list[list[Point]] = []

    def collect(rings: Iterable[list[Point]]) -> list[list[Point]]:
        out = [r for r in rings if keep(r)]
        kept.extend(out)
        return out

    wall_polys = collect(wall_rings)
    road_lines = collect(_lines(features.get("roads")))
    plank_lines = collect(_lines(features.get("planks")))
    river_lines = collect(_lines(features.get("rivers")))
    district_polys = collect(_polygons(features.get("districts")))
    building_polys = collect(_polygons(features.get("buildings")))
    square_polys = collect(_polygons(features.get("squares")))
    prism_polys = collect(_polygons(features.get("prisms")))
    water_polys = collect(_polygons(features.get("water")))
    field_polys = collect(_polygons(features.get("fields")))
    green_polys = collect(_polygons(features.get("greens")))

    if not kept:
        raise MFCGError("Nothing survived clipping; try --no-clip or a larger --margin.")

    # Normalise: scale to tiles, flip Y so north is up, and shift to the origin.
    # When clipping, the frame is the *window* -- not the kept geometry. A ring
    # that merely overlaps the window (a lake reaching far north, say) is kept
    # for rasterising but must not stretch the city's bounds to its own extent.
    if window is not None:
        ox, oy, mx, my = window
    else:
        ox, oy, mx, my = bounds([p for ring in kept for p in ring])

    def T(ring: list[Point]) -> list[Point]:
        return [((x - ox) / units_per_tile, (my - y) / units_per_tile) for x, y in ring]

    layout = Layout(
        name=name or pathlib.Path(path).stem.replace("_", " ").title(),
        source=f"mfcg {meta.get('version', '?')}",
        units_per_tile=units_per_tile,
        feet_per_unit=scale.feet_per_unit,
        wall_thickness=max(1.0, float(meta.get("wallThickness", 7.6)) / units_per_tile),
    )
    layout.width = (mx - ox) / units_per_tile
    layout.depth = (my - oy) / units_per_tile

    layout.walls = [T(r) for r in wall_polys]

    road_w = max(1.0, float(meta.get("roadWidth", 8.0)) / units_per_tile)
    river_w = max(1.0, float(meta.get("riverWidth", 30.0)) / units_per_tile)
    layout.roads = [LayoutRoad(T(line), road_w, "road") for line in road_lines]
    layout.roads += [LayoutRoad(T(line), max(1.0, road_w * 0.6), "plank") for line in plank_lines]
    layout.roads += [LayoutRoad(T(line), river_w, "river") for line in river_lines]

    for kind, polys in (
        ("plaza", square_polys), ("landmark", prism_polys), ("water", water_polys),
        ("field", field_polys), ("park", green_polys),
    ):
        layout.areas += [LayoutArea(kind, T(r)) for r in polys]

    layout.gates = _find_gates(layout.walls, [r for r in layout.roads if r.kind == "road"],
                               layout.wall_thickness)
    _assign_districts(layout, [T(r) for r in district_polys])
    _assign_buildings(layout, [T(r) for r in building_polys], seed)
    return layout


def _find_gates(walls: list[list[Point]], roads: list[LayoutRoad], thickness: float) -> list[Point]:
    """Gates sit where a road crosses the wall circuit.

    MFCG encodes gatehouses implicitly: towers are wall vertices, and a gate is
    where a road polyline meets one. Matching road vertices to wall vertices
    reproduces that without needing segment intersection.
    """
    if not walls:
        return []
    tolerance = max(thickness * 2.0, 3.0)
    gates: list[Point] = []
    for ring in walls:
        for vertex in ring:
            for road in roads:
                if any(distance(vertex, p) <= tolerance for p in road.points):
                    if all(distance(vertex, g) > tolerance for g in gates):
                        gates.append(vertex)
                    break
    return gates


def _assign_districts(layout: Layout, rings: list[list[Point]]) -> None:
    """Name and type the wards MFCG exports, by distance from the centre."""
    if not rings:
        return
    cx = layout.width / 2
    cy = layout.depth / 2
    half_w = max(layout.width / 2, 1.0)
    half_d = max(layout.depth / 2, 1.0)

    scored = []
    for ring in rings:
        px, py = centroid(ring)
        radius = max(abs(px - cx) / half_w, abs(py - cy) / half_d)
        scored.append((radius, ring))
    scored.sort(key=lambda item: item[0])

    # MFCG marks the plaza and landmark structures explicitly. Those are far
    # better signals for what a ward *is* than distance from the centre, so
    # they win where they apply and the radial bands only fill the remainder.
    plazas = [a.ring for a in layout.areas_of("plaza")]
    landmarks = [a.ring for a in layout.areas_of("landmark")]

    def contains_any(ring: list[Point], others: list[list[Point]]) -> bool:
        return any(point_in_polygon(ring, centroid(o)) for o in others)

    counts: dict[str, int] = {}
    rng = random.Random(len(rings))
    for i, (radius, ring) in enumerate(scored):
        kind = "residential"
        for limit, kinds in _BANDS:
            if radius <= limit:
                kind = rng.choice(kinds)
                break
        if contains_any(ring, landmarks):
            kind = "temple"
        elif contains_any(ring, plazas):
            kind = "market"
        # Wards reaching the waterfront are docks.
        for area in layout.areas_of("water"):
            if point_in_polygon(area.ring, centroid(ring)):
                kind = "docks"
                break
        counts[kind] = counts.get(kind, 0) + 1
        layout.districts.append(
            LayoutDistrict(
                id=f"d{i:02d}", name=f"{kind.title()} Ward {counts[kind]}",
                kind=kind, ring=ring,
            )
        )


#: Scarce building kinds, as ``(kind, one per N playable buildings, floor)``.
#: A settlement has one notable temple and a handful of taverns -- not a ward
#: full of each. Rolling a weighted choice per building (the obvious approach)
#: produces exactly that failure, so scarce kinds are allocated by quota to the
#: most prominent footprints instead, and everything else defaults to housing.
_QUOTAS: list[tuple[str, int, int]] = [
    ("temple", 400, 1),
    ("guildhall", 500, 1),
    ("manor", 260, 2),
    ("barracks", 800, 1),
    ("apothecary", 340, 1),
    ("smithy", 180, 2),
    ("stable", 200, 2),
    ("tavern", 90, 3),
    ("warehouse", 70, 3),
    ("shop", 12, 6),
]

#: Districts each scarce kind prefers, used to break ties in prominence.
_KIND_AFFINITY: dict[str, set[str]] = {
    "temple": {"temple", "civic"},
    "guildhall": {"civic", "market"},
    "manor": {"civic", "temple"},
    "barracks": {"civic"},
    "apothecary": {"market", "craft"},
    "smithy": {"craft"},
    "stable": {"craft", "farm", "residential"},
    "tavern": {"market", "docks", "craft"},
    "warehouse": {"docks", "craft", "market"},
    "shop": {"market", "craft"},
}


def _assign_buildings(layout: Layout, rings: list[list[Point]], seed: int) -> None:
    """Turn footprints into typed buildings.

    MFCG exports geometry only -- no building types -- so kind, storeys and ward
    membership are all derived here. Footprints marked as landmarks (``prisms``)
    are forced to temples, which is what they represent in an MFCG town.
    """
    rng = random.Random(seed)
    landmark_rings = [a.ring for a in layout.areas_of("landmark")]
    plaza_centres = [centroid(a.ring) for a in layout.areas_of("plaza")]

    @dataclass
    class Candidate:
        index: int
        ring: list[Point]
        centre: Point
        district: str
        district_kind: str
        inside: bool
        area: float
        short: float
        prominence: float

    candidates: list[Candidate] = []
    forced: dict[int, str] = {}

    for i, ring in enumerate(rings):
        c = centroid(ring)
        district, district_kind = "", "residential"
        for d in layout.districts:
            if point_in_polygon(d.ring, c):
                district, district_kind = d.name, d.kind
                break
        inside = any(point_in_polygon(w, c) for w in layout.walls)
        if not inside and not district:
            district_kind = "farm"

        long_side, short_side = oriented_extent(ring)
        area = long_side * short_side

        # Prominence: how much this footprint stands out as a candidate for
        # something more interesting than a house.
        prominence = area
        if plaza_centres:
            nearest = min(distance(c, p) for p in plaza_centres)
            prominence += max(0.0, 60.0 - nearest)
        if inside:
            prominence += 10.0

        candidates.append(
            Candidate(i, ring, c, district, district_kind, inside, area,
                      short_side, prominence)
        )
        if any(point_in_polygon(lr, c) for lr in landmark_rings):
            forced[i] = "temple"

    playable = [c for c in candidates if c.short >= MIN_PLAYABLE_SHORT_SIDE]
    n = max(1, len(playable))
    kinds: dict[int, str] = dict(forced)

    for kind, per, floor in _QUOTAS:
        quota = max(floor, n // per) - sum(1 for k in kinds.values() if k == kind)
        if quota <= 0:
            continue
        affinity = _KIND_AFFINITY.get(kind, set())
        pool = [c for c in playable if c.index not in kinds]
        pool.sort(
            key=lambda c: (
                -(c.prominence + (35.0 if c.district_kind in affinity else 0.0)),
                c.index,
            )
        )
        for c in pool[:quota]:
            kinds[c.index] = kind

    for c in candidates:
        kind = kinds.get(c.index)
        if kind is None:
            kind = "shed" if c.short < MIN_PLAYABLE_SHORT_SIDE else "house"

        floors = 1
        if kind != "shed":
            if c.area >= 40:
                floors = rng.randint(1, 3)
            elif c.area >= 20:
                floors = rng.randint(1, 2)

        layout.buildings.append(
            LayoutBuilding(
                id=f"{kind}-{c.index + 1:04d}", ring=c.ring, kind=kind,
                district=c.district, floors=floors, inside_walls=c.inside,
            )
        )
