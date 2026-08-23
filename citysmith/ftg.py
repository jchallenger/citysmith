"""Import a Fantasy Town Generator (FTG) GeoJSON export.

FTG does not publish its property schema, so this reader is written against
three real exports (a hamlet, a village and a town). The measurements behind
every rule here are in `docs/ftg-geojson-import.md`; the short version:

===========  ==============================  ==================================
type         geometry                        meaning
===========  ==============================  ==================================
``BUILDING`` Polygon, one closed ring        a footprint, with an authored
                                             ``name``, ``buildingType`` and
                                             ``material``
``EDGE``     LineString of **exactly two     one boundary segment of a
             points**                        background polygon, classified by
                                             ``edgeType`` -- roads, fences,
                                             shoreline, or nothing at all
``BACKGROUND`` Polygon, one closed ring      ground cover. ``GRASS`` is the base
                                             sheet; everything else sits on it
``WATER``    Polygon, one closed ring        a lake, river or sea
===========  ==============================  ==================================

Three things separate this from the MFCG path:

**The scale is real.** FTG's docs state 1 unit = 1 metre, so the tile size is
not inferred. As a cross-check, citysmith's own median-house-frontage anchor at
35 ft lands within 4% of the metric scale on all three exports -- an FTG house
measures 34-35 ft across. The metric scale is the default; the anchor remains
available as an override.

**Types and names are authored.** MFCG exports geometry only, so `mfcg.py`
invents wards and rolls building kinds against quotas. None of that runs here:
the export says what each building is and what it is called.

**The canvas is mostly farmland.** The settlement occupies a small part of a
large square, and the outliers are isolated farms that can drag a bounding box
across the whole map -- Graybank's nine stragglers cost 755,000 tiles of empty
board. The crop window therefore comes from the *settled core*, found by
clustering building centroids, not from the building bounding box.
"""

from __future__ import annotations

import math
import os
import pathlib
import random
from collections import defaultdict
from typing import Iterable

from . import importers
from .layout import (
    TILE_FEET,
    Layout,
    LayoutArea,
    LayoutBuilding,
    LayoutRoad,
    Point,
    bounds,
    close_ring,
    centroid,
    oriented_extent,
    point_in_polygon,
)

#: FTG's own coordinate system: "1 unit = 1 meter", per its export docs.
FEET_PER_METRE = 3.280839895

#: A footprint whose short side is under this (in tiles) cannot hold a usable
#: interior; it becomes a shed rather than being dropped.
MIN_PLAYABLE_SHORT_SIDE = 2.5

#: Gap at which two buildings still count as the same settlement, in metres.
#: 60 m separates a village from the farm up the lane on all three exports;
#: below ~40 m a village starts shedding its own outskirts.
DEFAULT_CLUSTER_GAP_M = 60.0


class FTGError(ValueError):
    """Raised when a file is not a usable FTG export."""


# -- vocabularies -------------------------------------------------------------
#
# All three tables map an FTG enum onto something citysmith already builds. The
# vocabulary demonstrably grows between exports -- five building types, two edge
# types and a material appear only in the largest of the three files -- so an
# unmapped value must fall through to a safe default and be *reported*, never
# raise and never be silently dropped. A dropped feature is invisible on the
# board, which is the failure mode CLAUDE.md records four separate times.

#: ``buildingType`` -> citysmith building kind. ``build.py`` reads the kind back
#: out of the building id prefix, so these must be kinds it knows.
BUILDING_KINDS: dict[str, str] = {
    "RESIDENCE": "house",
    "SHOP": "shop",
    "SERVICE": "shop",
    "ARTISAN": "smithy",
    "INDUSTRIAL": "warehouse",
    "WAREHOUSE": "warehouse",
    "FARM": "stable",
    "TAVERN": "tavern",
    "INN": "tavern",
    "RELIGIOUS": "temple",
    "EDUCATIONAL": "guildhall",
    "FACTION": "guildhall",
    "LAW_ENFORCEMENT": "barracks",
}
DEFAULT_BUILDING_KIND = "house"

#: A market square is exported as a ``BUILDING``. Built as one it becomes a
#: roofed box over the plaza, so it has to be diverted into an area instead.
#: The material is the test -- ``buildingType`` is corroboration only, because
#: that vocabulary grows and this one has not.
PAVED_MATERIALS = frozenset({"PAVEMENT"})

#: Materials that call for the civic (stone) wall and door roles.
STONE_MATERIALS = frozenset({"STONE_BRICK"})

#: ``edgeType`` -> (road kind, carriageway width in metres). FTG ships no road
#: width at all, so these are chosen: a cart is 2 tiles and a creature 1, and
#: `raster.classify_roads` widens through-routes further from the network shape.
ROAD_WIDTHS_M: dict[str, tuple[str, float]] = {
    "MAIN_ROAD": ("road", 6.0),
    "ROAD": ("road", 4.5),
    "SMALL_ROAD": ("road", 3.0),
    "DIRT_ROAD": ("road", 3.0),
    "TRAIL": ("trail", 1.5),
}

#: ``edgeType`` values that are deliberately not geometry we want.
#: ``INVISIBLE`` is an undrawn parcel boundary and is the bulk of the layer;
#: ``BORDER`` is the canvas edge; ``WATERFRONT`` restates the water polygons.
IGNORED_EDGES = frozenset({"INVISIBLE", "BORDER", "WATERFRONT"})

#: ``edgeType`` values that are masonry rather than route.
WALL_EDGES = frozenset({"STONE_WALL"})
FENCE_EDGES = frozenset({"STONE_FENCE"})

#: Town wall thickness in metres. FTG draws its wall as a bare polyline and
#: ships no thickness, so this is chosen -- and it is **not** a free parameter.
#:
#: A rampart is built as a full-cell core with thin curtain pieces hung on the
#: faces that show (`build.is_curtain_piece`). At :class:`Layout`'s 2.0-tile
#: default the band is two cells wide, so on a circuit that runs diagonally
#: almost every cell is an edge cell, there is nothing behind the thin pieces,
#: and `verify.check_placements` fails 149 of East Tradebourne's 1605 wall
#: cells for daylight through the masonry. At 4.5 m the band carries a core.
#: MFCG arrives at the same place from its own metadata: Forest Church's
#: `wallThickness` works out at 2.77 tiles.
DEFAULT_WALL_THICKNESS_M = 4.5

#: ``backgroundType`` -> :class:`LayoutArea` kind, or ``None`` to drop it.
#:
#: ``GRASS`` is dropped on purpose: it is the base sheet under everything (92-94%
#: of the canvas on all three exports), and the rasteriser already lays ground
#: everywhere. Recording it would be 74 polygons saying "grass is grass".
#:
#: ``forest``, ``pasture`` and ``lawn`` are carried but not yet built: all three
#: are grass underfoot, and the rasteriser paints them as ground. Keeping them
#: distinct in the layout is what lets a later pass modulate tree density by the
#: forest outline instead of scattering across every open cell.
BACKGROUND_AREAS: dict[str, str | None] = {
    "GRASS": None,
    "LAWN_TEXTURE_TYPE": "lawn",
    "FOREST": "forest",
    "WHEAT": "field",
    "GRAIN": "field",
    "TILLED": "field",
    "SHEEP_TEXTURE_TYPE": "pasture",
    "PIGS_TEXTURE_TYPE": "pasture",
    "CATTLE_TEXTURE_TYPE": "pasture",
    "ROAD_TEXTURE_TYPE": "plaza",
}
DEFAULT_BACKGROUND_AREA = "park"

#: A raised ``ROAD_TEXTURE_TYPE`` quad is a bridge -- that is the only thing
#: ``raised`` has ever been true for, across all three exports.
RAISED_BACKGROUND_AREA = "bridge"


# -- reading ------------------------------------------------------------------

def load_features(path: str | os.PathLike[str]) -> dict[str, list[dict]]:
    """Read an FTG export and group its features by ``properties.type``."""
    try:
        data = importers.read_collection(path)
    except importers.SourceError as exc:
        raise FTGError(str(exc)) from exc

    grouped: dict[str, list[dict]] = defaultdict(list)
    for feature in data["features"]:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        grouped[str(props.get("type"))].append(feature)

    if not grouped.get("BUILDING"):
        raise FTGError(
            f"{pathlib.Path(path)} has no BUILDING features -- this does not "
            f"look like a Fantasy Town Generator export (found: "
            f"{', '.join(sorted(grouped)) or 'nothing'})."
        )
    return dict(grouped)


def _ring(feature: dict) -> list[Point]:
    """The single closed ring of an FTG polygon.

    Every polygon in every export measured has exactly one ring and it is
    already closed, so this is a check rather than a repair -- but a ring that
    ever arrives open is closed rather than rejected.
    """
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if geom.get("type") != "Polygon" or not coords:
        raise FTGError(f"Expected a Polygon, got {geom.get('type')!r}.")
    ring = [(float(x), float(y)) for x, y in coords[0]]
    return close_ring(ring)


def _segment(feature: dict) -> tuple[Point, Point]:
    """The two endpoints of an FTG edge. Every edge is one segment."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if geom.get("type") != "LineString" or len(coords) < 2:
        raise FTGError(f"Expected a 2-point LineString, got {geom.get('type')!r}.")
    a, b = coords[0], coords[-1]
    return (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))


# -- road chaining ------------------------------------------------------------

def chain_segments(segments: list[tuple[Point, Point]]) -> list[list[Point]]:
    """Join two-point segments into polylines through shared endpoints.

    FTG exports the road network as individual boundary segments, not as paths,
    so nothing can be given a width until they are chained. Endpoints are shared
    *exactly* between segments -- measured across all three exports -- so this
    keys on the raw coordinate pair and needs no snapping tolerance.

    A chain stops at a junction (any vertex of degree other than two) so that a
    fork stays a fork; walking through one would invent a road that bends where
    the network actually branches.
    """
    adjacency: dict[Point, list[tuple[Point, int]]] = defaultdict(list)
    for i, (a, b) in enumerate(segments):
        if a == b:
            continue  # degenerate; nothing to walk along
        adjacency[a].append((b, i))
        adjacency[b].append((a, i))

    used: set[int] = set()
    paths: list[list[Point]] = []

    def walk(start: Point) -> None:
        for first, edge in adjacency[start]:
            if edge in used:
                continue
            path = [start]
            nxt, e = first, edge
            while True:
                used.add(e)
                path.append(nxt)
                if len(adjacency[nxt]) != 2:
                    break  # junction or dead end: the chain ends here
                onward = [(v, i) for v, i in adjacency[nxt] if i not in used]
                if not onward:
                    break  # closed loop, back at the start
                nxt, e = onward[0]
            if len(path) >= 2:
                paths.append(path)

    # Open runs first, from their loose ends, so a road is not cut in half at an
    # arbitrary midpoint. Whatever is left is a closed loop and starts anywhere.
    ends = sorted((v for v, e in adjacency.items() if len(e) != 2))
    for vertex in ends:
        walk(vertex)
    for vertex in sorted(adjacency):
        walk(vertex)
    return paths


# -- the settled core ---------------------------------------------------------

def core_cluster(centres: list[Point], gap: float) -> list[int]:
    """Indices of the largest group of buildings within ``gap`` of each other.

    Single-link clustering, grid-accelerated so a thousand-building town does
    not become a million distance tests. This is the crop window's real source:
    an FTG canvas is mostly farmland, and a handful of outlying farms otherwise
    stretch the bounding box across the whole map.
    """
    if not centres:
        return []
    parent = list(range(len(centres)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(centres):
        cells[(int(x // gap), int(y // gap))].append(i)

    for (cx, cy), members in cells.items():
        neighbours = [
            j
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for j in cells.get((cx + dx, cy + dy), ())
        ]
        for i in members:
            for j in neighbours:
                if i >= j:
                    continue
                if math.dist(centres[i], centres[j]) < gap:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(centres)):
        groups[find(i)].append(i)
    return max(groups.values(), key=len)


# -- scale --------------------------------------------------------------------

def resolve_feet_per_unit(
    rings: list[list[Point]],
    *,
    house_frontage_ft: float | None = None,
    feet_per_unit: float | None = None,
) -> tuple[float, str]:
    """Feet per FTG unit, and the name of the anchor that decided it."""
    if feet_per_unit is not None:
        if feet_per_unit <= 0:
            raise FTGError("--feet-per-unit must be positive.")
        return feet_per_unit, "explicit"
    if house_frontage_ft is not None:
        shorts = sorted(s for s in (oriented_extent(r)[1] for r in rings) if s > 0)
        if not shorts:
            raise FTGError("Export has no usable building footprints.")
        median = shorts[len(shorts) // 2]
        return house_frontage_ft / median, f"house frontage = {house_frontage_ft:.0f} ft"
    return FEET_PER_METRE, "FTG metric scale (1 unit = 1 m)"


# -- import -------------------------------------------------------------------

def import_layout(
    path: str | os.PathLike[str],
    *,
    house_frontage_ft: float | None = None,
    feet_per_unit: float | None = None,
    margin_feet: float = 60.0,
    clip: bool = True,
    core_only: bool = True,
    cluster_gap_ft: float = DEFAULT_CLUSTER_GAP_M * FEET_PER_METRE,
    fences: bool = True,
    name: str | None = None,
    seed: int = 0,
) -> Layout:
    """Import an FTG GeoJSON export as a :class:`Layout` in 5 ft tiles.

    The scale is FTG's own metric one unless ``house_frontage_ft`` or
    ``feet_per_unit`` overrides it. ``core_only`` crops to the settled core plus
    ``margin_feet``; turning it off crops to every building, which on a sprawling
    export is most of a square kilometre of empty field.
    """
    features = load_features(path)
    unmapped: dict[str, set[str]] = defaultdict(set)

    building_features = features.get("BUILDING", [])
    plaza_rings: list[list[Point]] = []
    footprints: list[tuple[list[Point], dict]] = []
    for feature in building_features:
        props = feature.get("properties") or {}
        ring = _ring(feature)
        if str(props.get("material", "")).upper() in PAVED_MATERIALS:
            plaza_rings.append(ring)  # a paved "building" is a market square
        else:
            footprints.append((ring, props))
    if not footprints:
        raise FTGError("Every BUILDING in this export is paved; nothing to build.")

    per_unit, anchor = resolve_feet_per_unit(
        [r for r, _ in footprints],
        house_frontage_ft=house_frontage_ft,
        feet_per_unit=feet_per_unit,
    )
    units_per_tile = TILE_FEET / per_unit

    # The crop window, in source units, established before scaling so the margin
    # can be given in feet.
    centres = [centroid(r) for r, _ in footprints]
    if core_only:
        keep_idx = core_cluster(centres, cluster_gap_ft / per_unit)
    else:
        keep_idx = list(range(len(centres)))
    window: tuple[float, float, float, float] | None = None
    if clip:
        x0, y0, x1, y1 = bounds([p for i in keep_idx for p in footprints[i][0]])
        m = margin_feet / per_unit
        window = (x0 - m, y0 - m, x1 + m, y1 + m)

    def inside_window(points: Iterable[Point]) -> bool:
        if window is None:
            return True
        x0, y0, x1, y1 = bounds(points)
        return not (x1 < window[0] or x0 > window[2] or y1 < window[1] or y0 > window[3])

    # Everything kept, so the frame can be measured before anything is scaled.
    kept_buildings = [
        (ring, props) for ring, props in footprints if inside_window(ring)
    ]
    kept_plazas = [r for r in plaza_rings if inside_window(r)]
    if not kept_buildings:
        raise FTGError("Nothing survived clipping; try --no-clip or a larger --margin.")

    if window is not None:
        ox, oy, mx, my = window
    else:
        ox, oy, mx, my = bounds([p for ring, _ in kept_buildings for p in ring])

    def T(ring: Iterable[Point]) -> list[Point]:
        """Source units -> tiles, Y flipped so north is up, origin at (0, 0)."""
        return [((x - ox) / units_per_tile, (my - y) / units_per_tile) for x, y in ring]

    layout = Layout(
        name=name or pathlib.Path(path).stem.replace("_", " ").title(),
        source="ftg",
        units_per_tile=units_per_tile,
        feet_per_unit=per_unit,
        wall_thickness=max(1.0, DEFAULT_WALL_THICKNESS_M * FEET_PER_METRE / TILE_FEET),
    )
    layout.width = (mx - ox) / units_per_tile
    layout.depth = (my - oy) / units_per_tile
    layout.scale_anchor = anchor

    _read_edges(layout, features.get("EDGE", []), T, inside_window, fences, unmapped)
    _read_areas(layout, features.get("WATER", []), features.get("BACKGROUND", []),
                T, inside_window, unmapped)
    layout.areas += [LayoutArea("plaza", T(r)) for r in kept_plazas]
    _read_buildings(layout, kept_buildings, T, seed, unmapped)

    layout.unmapped = {k: sorted(v) for k, v in sorted(unmapped.items())}
    return layout


def _read_edges(layout, edges, T, inside_window, fences, unmapped) -> None:
    """Roads (chained and widened), town wall, and field boundaries."""
    by_type: dict[str, list[tuple[Point, Point]]] = defaultdict(list)
    for feature in edges:
        edge_type = str((feature.get("properties") or {}).get("edgeType", ""))
        if edge_type in IGNORED_EDGES:
            continue
        segment = _segment(feature)
        if not inside_window(segment):
            continue
        by_type[edge_type].append(segment)

    for edge_type, segments in sorted(by_type.items()):
        if edge_type in WALL_EDGES:
            # FTG's town wall is open polylines, not the closed rings MFCG gives
            # -- East Tradebourne's is two arcs. They are kept as-is; a ring
            # that closes will close on its own.
            layout.walls += [T(p) for p in chain_segments(segments)]
            continue
        if edge_type in FENCE_EDGES:
            if fences:
                layout.fences += [T(p) for p in chain_segments(segments)]
            continue
        spec = ROAD_WIDTHS_M.get(edge_type)
        if spec is None:
            unmapped["edgeType"].add(edge_type)
            spec = ROAD_WIDTHS_M["ROAD"]
        kind, width_m = spec
        # A carriageway is a real width, so it stays fixed in metres whatever
        # scale anchor is in force; only the tile size moves under it.
        width = max(1.0, width_m * FEET_PER_METRE / TILE_FEET)
        layout.roads += [
            LayoutRoad(T(points), width, kind) for points in chain_segments(segments)
        ]


def _read_areas(layout, water, backgrounds, T, inside_window, unmapped) -> None:
    """Water bodies and ground cover.

    ``GRASS`` is dropped: the sample grid over all three exports never found a
    point more than two backgrounds deep, and every depth-2 point was GRASS plus
    one other -- so grass is the base sheet and anything else simply wins. There
    is no draw order to resolve, and the order features appear in the file is
    not one (FOREST is last in one export, LAWN first in another, and both
    belong on top).
    """
    for feature in water:
        ring = _ring(feature)
        if inside_window(ring):
            layout.areas.append(LayoutArea("water", T(ring)))

    for feature in backgrounds:
        props = feature.get("properties") or {}
        background = str(props.get("backgroundType", ""))
        ring = _ring(feature)
        if not inside_window(ring):
            continue
        if background not in BACKGROUND_AREAS:
            unmapped["backgroundType"].add(background)
            kind: str | None = DEFAULT_BACKGROUND_AREA
        else:
            kind = BACKGROUND_AREAS[background]
        if props.get("raised"):
            kind = RAISED_BACKGROUND_AREA
        if kind is not None:
            layout.areas.append(LayoutArea(kind, T(ring)))


def _read_buildings(layout, footprints, T, seed, unmapped) -> None:
    """Footprints with the type and name FTG authored for them.

    No quotas, no wards, no weighted rolls -- the export already says what every
    building is. Only ``floors`` is invented, and only from footprint area.
    """
    rng = random.Random(seed)
    for index, (ring, props) in enumerate(footprints):
        building_type = str(props.get("buildingType", ""))
        kind = BUILDING_KINDS.get(building_type)
        if kind is None:
            unmapped["buildingType"].add(building_type)
            kind = DEFAULT_BUILDING_KIND

        tiles = T(ring)
        long_side, short_side = oriented_extent(tiles)
        if short_side < MIN_PLAYABLE_SHORT_SIDE:
            kind = "shed"

        area = long_side * short_side
        floors = 1
        if kind != "shed":
            if area >= 40:
                floors = rng.randint(1, 3)
            elif area >= 20:
                floors = rng.randint(1, 2)

        inside = any(point_in_polygon(w, centroid(tiles)) for w in layout.walls)
        layout.buildings.append(
            LayoutBuilding(
                id=f"{kind}-{index + 1:04d}",
                ring=tiles,
                kind=kind,
                district="",
                floors=floors,
                inside_walls=inside,
                name=str(props.get("name", "")),
                stone=str(props.get("material", "")).upper() in STONE_MATERIALS,
            )
        )


def check_playability(layout: Layout) -> list[str]:
    """Warn where the resolved scale makes the map unplayable at 5 ft a tile."""
    problems: list[str] = []
    shorts = sorted(b.extent[1] for b in layout.buildings if b.kind != "shed")
    if shorts:
        median = shorts[len(shorts) // 2]
        if median < 3.0:
            problems.append(
                f"median building is {median:.1f} tiles ({median * TILE_FEET:.0f} ft) "
                "across -- under 3 tiles there is no room to fight indoors"
            )
    # Trails are footpaths and are correct at one tile; only carriageways have to
    # take two abreast.
    roads = [r.width for r in layout.roads if r.kind == "road"]
    if roads and max(roads) < 2.0:
        problems.append(
            f"widest street is {max(roads):.1f} tiles "
            f"({max(roads) * TILE_FEET:.0f} ft) -- creatures cannot pass abreast"
        )
    return problems
