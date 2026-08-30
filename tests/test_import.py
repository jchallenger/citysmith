"""Tests for the two GeoJSON import paths and the sniffer that picks between them.

Invariants, not golden output: the vocabulary tables and the scale defaults can
change, but a silently dropped feature, a road segment lost in chaining, or a
market square built as a roofed box are always bugs.

`tests/fixtures/ftg_pelvesthollow_corner.geojson` is a real Fantasy Town
Generator export, trimmed to the middle of the village so the file stays small.
`samples/forest_church.json` is the MFCG counterpart.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from citysmith import ftg, importers
from citysmith.layout import Layout, TILE_FEET, oriented_extent, point_in_polygon

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FTG_FILE = FIXTURES / "ftg_pelvesthollow_corner.geojson"
MFCG_FILE = pathlib.Path(__file__).parent.parent / "samples" / "forest_church.json"


@pytest.fixture(scope="module")
def ftg_features() -> dict[str, list[dict]]:
    return ftg.load_features(FTG_FILE)


@pytest.fixture(scope="module")
def village() -> Layout:
    return ftg.import_layout(FTG_FILE, seed=3)


# -- format detection ---------------------------------------------------------
#
# The extension is not a discriminator: both generators ship as .json and as
# .geojson, and citysmith has been handed all four combinations.

def test_detects_ftg():
    assert importers.detect_format(FTG_FILE) == importers.FTG


def test_detects_mfcg():
    assert importers.detect_format(MFCG_FILE) == importers.MFCG


def test_extension_does_not_decide(tmp_path):
    """The same bytes classify the same way under either extension."""
    for name in ("town.json", "town.geojson"):
        copy = tmp_path / name
        copy.write_bytes(FTG_FILE.read_bytes())
        assert importers.detect_format(copy) == importers.FTG
    for name in ("city.json", "city.geojson"):
        copy = tmp_path / name
        copy.write_bytes(MFCG_FILE.read_bytes())
        assert importers.detect_format(copy) == importers.MFCG


def test_garbage_is_named_not_guessed(tmp_path):
    p = tmp_path / "not-a-town.geojson"
    p.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"type": "LAMPPOST"},
                      "geometry": {"type": "Point", "coordinates": [0, 0]}}],
    }), encoding="utf-8")
    with pytest.raises(importers.SourceError) as exc:
        importers.detect_format(p)
    assert "LAMPPOST" in str(exc.value)


def test_not_json_at_all(tmp_path):
    p = tmp_path / "junk.geojson"
    p.write_text("this is not json", encoding="utf-8")
    with pytest.raises(importers.SourceError):
        importers.detect_format(p)


def test_dispatch_reaches_both_readers():
    assert importers.import_layout(FTG_FILE).source == "ftg"
    assert importers.import_layout(MFCG_FILE).source.startswith("mfcg")


# -- the format's own invariants ----------------------------------------------
#
# These guard the reader against FTG changing under it. Every one was measured
# across three real exports; if one starts failing, the schema moved and the
# reader's assumptions need revisiting rather than patching around.

def test_every_polygon_is_one_closed_ring(ftg_features):
    for kind in ("BUILDING", "BACKGROUND", "WATER"):
        for feature in ftg_features.get(kind, []):
            geometry = feature["geometry"]
            assert geometry["type"] == "Polygon"
            assert len(geometry["coordinates"]) == 1, f"{kind} has a hole"
            ring = geometry["coordinates"][0]
            assert ring[0] == ring[-1], f"{kind} ring is not closed"


def test_every_edge_is_a_single_segment(ftg_features):
    for feature in ftg_features["EDGE"]:
        assert feature["geometry"]["type"] == "LineString"
        assert len(feature["geometry"]["coordinates"]) == 2


def test_grass_is_the_base_and_nothing_stacks_deeper_than_two(ftg_features):
    """Ground cover is one base sheet plus at most one thing on it.

    This is what makes the compositing rule "paint grass, then let anything
    else win" correct, and it is the reason no draw order has to be resolved --
    which matters, because the file's own feature order is not one.
    """
    polys = [
        (f["properties"]["backgroundType"], f["geometry"]["coordinates"][0])
        for f in ftg_features["BACKGROUND"]
    ]
    xs = [p[0] for _, ring in polys for p in ring]
    ys = [p[1] for _, ring in polys for p in ring]
    steps = 24
    for i in range(steps):
        for j in range(steps):
            x = min(xs) + (max(xs) - min(xs)) * (i + 0.5) / steps
            y = min(ys) + (max(ys) - min(ys)) * (j + 0.5) / steps
            hits = {t for t, ring in polys if point_in_polygon(ring, (x, y))}
            assert len(hits) <= 2, f"{len(hits)} deep at {x:.1f},{y:.1f}: {hits}"
            if len(hits) == 2:
                assert "GRASS" in hits, f"depth-2 without grass: {hits}"


# -- scale --------------------------------------------------------------------

def test_metric_scale_is_the_default(village: Layout):
    assert village.feet_per_unit == pytest.approx(ftg.FEET_PER_METRE)
    assert "metric" in village.scale_anchor


def test_a_house_measures_thirty_odd_feet(village: Layout):
    """FTG's declared metre and citysmith's 35 ft play anchor agree.

    An FTG house is 10.3-10.7 m across, and the pipeline is pinned to a 5 ft
    tile, so a correctly scaled import puts a median house at 34-35 ft. This is
    the check that catches a scale that has silently gone metric-to-feet the
    wrong way round -- which would read as a 10 ft house or a 115 ft one.
    """
    shorts = sorted(oriented_extent(b.ring)[1] for b in village.buildings)
    median_ft = shorts[len(shorts) // 2] * TILE_FEET
    assert 30.0 <= median_ft <= 40.0, f"median house is {median_ft:.0f} ft"


def test_house_frontage_anchor_overrides_the_metric_one():
    anchored = ftg.import_layout(FTG_FILE, house_frontage_ft=50.0)
    shorts = sorted(oriented_extent(b.ring)[1] for b in anchored.buildings)
    assert shorts[len(shorts) // 2] * TILE_FEET == pytest.approx(50.0, abs=0.5)


def test_explicit_feet_per_unit_wins():
    layout = ftg.import_layout(FTG_FILE, feet_per_unit=1.0)
    assert layout.feet_per_unit == 1.0
    assert layout.units_per_tile == pytest.approx(TILE_FEET)


def test_negative_scale_rejected():
    with pytest.raises(ftg.FTGError):
        ftg.import_layout(FTG_FILE, feet_per_unit=-1.0)


# -- geometry -----------------------------------------------------------------

def test_chaining_consumes_every_segment_exactly_once():
    """Chaining must not lose a road, and must not duplicate one.

    A dropped segment is a gap in a street that nothing downstream reports:
    the rasteriser paves what it is given, so a lost chain is just a road that
    was never there.
    """
    segments = [
        ((0.0, 0.0), (1.0, 0.0)),   # a run of three
        ((1.0, 0.0), (2.0, 0.0)),
        ((2.0, 0.0), (3.0, 0.0)),
        ((5.0, 5.0), (6.0, 6.0)),   # an isolated one
        ((1.0, 0.0), (1.0, 1.0)),   # a spur, making (1,0) a junction
    ]
    chains = ftg.chain_segments(segments)
    consumed = sum(len(c) - 1 for c in chains)
    assert consumed == len(segments)
    assert all(len(c) >= 2 for c in chains)


def test_chaining_stops_at_a_junction():
    """A fork stays a fork; walking through one invents a road that bends."""
    segments = [
        ((0.0, 0.0), (1.0, 0.0)),
        ((1.0, 0.0), (2.0, 0.0)),
        ((1.0, 0.0), (1.0, 1.0)),
    ]
    chains = ftg.chain_segments(segments)
    assert len(chains) == 3
    assert all(len(c) == 2 for c in chains)


def test_chaining_handles_a_closed_loop():
    ring = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    segments = [(ring[i], ring[(i + 1) % 4]) for i in range(4)]
    chains = ftg.chain_segments(segments)
    assert sum(len(c) - 1 for c in chains) == 4


def test_chaining_survives_a_degenerate_segment():
    chains = ftg.chain_segments([((0.0, 0.0), (0.0, 0.0)), ((0.0, 0.0), (1.0, 0.0))])
    assert sum(len(c) - 1 for c in chains) == 1


def test_no_two_buildings_overlap(village: Layout):
    """Footprints share bounding boxes freely -- they are rotated quads -- so
    this is a polygon test, not a box test."""
    for i, a in enumerate(village.buildings):
        for b in village.buildings[i + 1:]:
            for point in a.ring:
                assert not point_in_polygon(b.ring, point), f"{a.id} inside {b.id}"


def test_buildings_land_inside_the_frame(village: Layout):
    for b in village.buildings:
        for x, y in b.ring:
            assert -0.001 <= x <= village.width + 0.001
            assert -0.001 <= y <= village.depth + 0.001


# -- the settled core ---------------------------------------------------------

def test_core_cluster_finds_the_settlement_not_the_outliers():
    centres = [(0.0, 0.0), (5.0, 0.0), (0.0, 5.0), (5.0, 5.0),  # a village
               (500.0, 500.0), (900.0, 100.0)]                   # two far farms
    core = ftg.core_cluster(centres, 60.0)
    assert sorted(core) == [0, 1, 2, 3]


def test_core_crop_drops_the_outlying_farms(tmp_path):
    """The crop window is the difference between a board and an impossibility.

    An FTG canvas is mostly farmland, and it is the *outliers* that cost the
    board: on the full Graybank export nine straggler farms stretch the window
    from 400x272 tiles to 853x1013, which is 755,000 tiles of empty ground. The
    trimmed fixture has no stragglers, so two are added here -- otherwise this
    test passes without measuring anything.
    """
    src = json.loads(FTG_FILE.read_text(encoding="utf-8"))
    house = next(f for f in src["features"] if f["properties"]["type"] == "BUILDING")
    for n, (dx, dy) in enumerate(((900.0, 0.0), (0.0, -900.0)), start=1):
        farm = json.loads(json.dumps(house))
        farm["properties"] = dict(farm["properties"], id=5000 + n, name=f"Far Farm {n}",
                                  buildingType="FARM")
        farm["geometry"]["coordinates"] = [
            [[x + dx, y + dy] for x, y in house["geometry"]["coordinates"][0]]
        ]
        src["features"].append(farm)
    p = tmp_path / "sprawl.geojson"
    p.write_text(json.dumps(src), encoding="utf-8")

    core = ftg.import_layout(p)
    whole = ftg.import_layout(p, core_only=False)

    assert len(whole.buildings) == len(core.buildings) + 2
    assert not any(b.name.startswith("Far Farm") for b in core.buildings)
    # The two stragglers cost an order of magnitude in board area.
    assert core.width * core.depth < 0.2 * whole.width * whole.depth
    assert len(core.buildings) >= 0.9 * len(whole.buildings)


def test_margin_is_kept_in_feet(village: Layout):
    """Sixty feet of margin is twelve tiles, on every side."""
    xs = [x for b in village.buildings for x, _ in b.ring]
    ys = [y for b in village.buildings for _, y in b.ring]
    margin_tiles = 60.0 / TILE_FEET
    assert min(xs) == pytest.approx(margin_tiles, abs=0.1)
    assert min(ys) == pytest.approx(margin_tiles, abs=0.1)


# -- authored types and names -------------------------------------------------

def test_every_building_keeps_its_authored_name(village: Layout):
    assert village.buildings
    assert all(b.name for b in village.buildings)


def test_building_ids_carry_the_kind(village: Layout):
    """``build.py`` reads a building's kind back out of its id prefix."""
    for b in village.buildings:
        assert b.id.split("-")[0] == b.kind


def test_known_types_map_to_kinds_build_understands(village: Layout):
    assert not village.unmapped
    assert set(b.kind for b in village.buildings) <= set(ftg.BUILDING_KINDS.values()) | {"shed"}


def test_a_paved_building_becomes_a_plaza(tmp_path):
    """FTG exports a market square as a BUILDING. Built as one it becomes a
    roofed box over the square, so it has to be diverted into an area."""
    src = json.loads(FTG_FILE.read_text(encoding="utf-8"))
    market = None
    for feature in src["features"]:
        if feature["properties"]["type"] == "BUILDING":
            market = json.loads(json.dumps(feature))
            break
    market["properties"] = dict(market["properties"])
    market["properties"].update(id=9999, name="Warden Market",
                                buildingType="MARKET", material="PAVEMENT")
    # move it clear of the houses so it cannot be confused with one
    market["geometry"]["coordinates"] = [
        [[x + 0.001, y + 0.001] for x, y in market["geometry"]["coordinates"][0]]
    ]
    src["features"].append(market)
    p = tmp_path / "with-market.geojson"
    p.write_text(json.dumps(src), encoding="utf-8")

    layout = ftg.import_layout(p)
    assert not any(b.name == "Warden Market" for b in layout.buildings)
    assert len(layout.areas_of("plaza")) == 1


def test_stone_material_is_recorded(tmp_path):
    src = json.loads(FTG_FILE.read_text(encoding="utf-8"))
    for feature in src["features"]:
        if feature["properties"]["type"] == "BUILDING":
            feature["properties"]["material"] = "STONE_BRICK"
            break
    p = tmp_path / "stone.geojson"
    p.write_text(json.dumps(src), encoding="utf-8")
    assert sum(1 for b in ftg.import_layout(p).buildings if b.stone) == 1


# -- unknown vocabulary -------------------------------------------------------
#
# FTG's vocabulary grew between the three exports citysmith has seen: five
# building types, two edge types and a material appear only in the largest. An
# unmapped value must import under a default and be *reported*. Raising loses
# the map; dropping loses the feature, which is invisible on the board.

@pytest.mark.parametrize("prop, key, value", [
    ("buildingType", "BUILDING", "ZEPPELIN_HANGAR"),
    ("edgeType", "EDGE", "CANAL"),
    ("backgroundType", "BACKGROUND", "VINEYARD"),
])
def test_unknown_vocabulary_is_reported_not_dropped(tmp_path, prop, key, value):
    src = json.loads(FTG_FILE.read_text(encoding="utf-8"))
    changed = 0
    for feature in src["features"]:
        if feature["properties"]["type"] == key and changed < 1:
            feature["properties"][prop] = value
            changed += 1
    assert changed == 1
    p = tmp_path / "novel.geojson"
    p.write_text(json.dumps(src), encoding="utf-8")

    layout = ftg.import_layout(p)          # must not raise
    assert value in layout.unmapped.get(prop, [])


def test_an_export_with_no_buildings_is_refused(tmp_path):
    src = json.loads(FTG_FILE.read_text(encoding="utf-8"))
    src["features"] = [f for f in src["features"]
                       if f["properties"]["type"] != "BUILDING"]
    p = tmp_path / "empty.geojson"
    p.write_text(json.dumps(src), encoding="utf-8")
    with pytest.raises(ftg.FTGError):
        ftg.import_layout(p)


# -- round trip ---------------------------------------------------------------

def test_layout_round_trips_with_the_new_fields(tmp_path, village: Layout):
    path = tmp_path / "layout.json"
    village.save(path)
    again = Layout.load(path)
    assert again.to_dict() == village.to_dict()
    assert [b.name for b in again.buildings] == [b.name for b in village.buildings]
    assert again.scale_anchor == village.scale_anchor
    assert again.fences == village.fences


def test_import_is_deterministic():
    a = ftg.import_layout(FTG_FILE, seed=11)
    b = ftg.import_layout(FTG_FILE, seed=11)
    assert a.to_dict() == b.to_dict()


# -- playability --------------------------------------------------------------

def test_the_village_is_playable(village: Layout):
    assert ftg.check_playability(village) == []


def test_a_trail_does_not_trip_the_street_width_check():
    """A footpath is *correct* at one tile; only carriageways owe two abreast."""
    from citysmith.layout import LayoutRoad
    layout = Layout(name="t", source="ftg")
    layout.buildings = list(_playable_buildings())
    layout.roads = [LayoutRoad([(0.0, 0.0), (10.0, 0.0)], 1.0, "trail")]
    assert ftg.check_playability(layout) == []


def test_a_narrow_carriageway_does_trip_it():
    from citysmith.layout import LayoutRoad
    layout = Layout(name="t", source="ftg")
    layout.buildings = list(_playable_buildings())
    layout.roads = [LayoutRoad([(0.0, 0.0), (10.0, 0.0)], 1.0, "road")]
    assert any("abreast" in p for p in ftg.check_playability(layout))


def test_trails_are_not_thoroughfares():
    """`raster.classify_roads` must not widen a footpath to cart standard."""
    from citysmith import raster
    assert "trail" in raster.NOT_THOROUGHFARES


def _trail_across_field_and_water() -> Layout:
    """One trail crossing open ground, a wheat field and a stream in turn.
    The polyline runs along cell-centre row z=5, so the 1-tile stroke stays
    one row wide instead of straddling two."""
    from citysmith.layout import LayoutArea, LayoutRoad

    layout = Layout(name="path", source="ftg", width=20.0, depth=10.0)
    layout.areas = [
        LayoutArea("field", [(4.0, 0.0), (8.0, 0.0), (8.0, 10.0), (4.0, 10.0)]),
        LayoutArea("water", [(12.0, 0.0), (15.0, 0.0), (15.0, 10.0), (12.0, 10.0)]),
    ]
    layout.roads = [LayoutRoad([(0.0, 5.5), (20.0, 5.5)], 1.0, "trail")]
    return layout


def test_a_trail_rasterises_as_a_trodden_path_not_a_street():
    """A TRAIL used to fall through the road loop's else branch and get laid
    as STREET -- cobble, on a footpath the export drew 1.5 m wide. It is LANE
    now (trodden earth), it carries no street class (a footpath owes nobody
    two abreast), and it does not bridge: where it meets water the cells stay
    WATER and the path resumes on the far bank. Before this, a trail crossing
    a stream was paved straight over it as a cobble ford at grade with
    nothing beneath."""
    from citysmith import raster

    tm = raster.rasterize(_trail_across_field_and_water(), bridges=False)
    assert tm.surface[5][2] == raster.LANE, "trodden earth over open ground"
    assert tm.surface[5][6] == raster.LANE, "a footpath crosses the wheat"
    for x in (12, 13, 14):
        assert tm.surface[5][x] == raster.WATER, "a footpath does not bridge"
    assert tm.surface[5][17] == raster.LANE, "the path resumes on the far bank"
    for x in range(tm.width):
        assert tm.street_class[5][x] == "", (
            "no class: the width check would demand two abreast of a footpath")


def test_a_carriageway_paves_over_the_footpath_it_crosses():
    """At a junction the busier way wins the cell, the same rule street
    classes settle their own collisions by -- a cart road with a one-tile
    dirt gap where a path crosses would read as a pothole."""
    from citysmith import raster
    from citysmith.layout import LayoutRoad

    layout = Layout(name="junction", source="ftg", width=20.0, depth=20.0)
    layout.roads = [
        LayoutRoad([(0.0, 10.5), (20.0, 10.5)], 1.0, "trail"),
        LayoutRoad([(10.5, 0.0), (10.5, 20.0)], 3.0, "road"),
    ]
    tm = raster.rasterize(layout, bridges=False)
    assert tm.surface[10][10] == raster.STREET, "the road takes the junction"
    assert tm.surface[10][2] == raster.LANE, "the path goes on either side"
    assert tm.surface[10][17] == raster.LANE


def test_a_trail_cell_builds_with_the_lane_role(catalog_palette):
    """The point of the surface class: LANE lays trodden earth by its top, so
    the path meets the grass flush instead of arriving as laid cobble."""
    import math

    from citysmith import raster
    from citysmith.build import Builder, _lay_terrain

    tm = raster.rasterize(_trail_across_field_and_water(), bridges=False)
    palette = catalog_palette
    b = Builder(palette, 0)
    grade = palette.require("floor").size_y
    # The same surface->role mapping build_from_tilemap uses for these cells.
    # `_lay_terrain` takes a *callable* (surface, x, z) -> role, not a dict:
    # a yard and the lawn beside it are both GROUND and pave differently.
    roles = {raster.GROUND: "ground", raster.LANE: "lane", raster.FIELD: "ground"}
    _lay_terrain(b, tm, lambda s, x, z: roles.get(s, "ground"),
                 grade=grade, taper={})

    lane = palette.require("lane")
    here = [p for p in b.placements
            if (int(math.floor(p.x + 1e-6)), int(math.floor(p.z + 1e-6))) == (2, 5)]
    assert [p.asset_id for p in here] == [lane.id], "one lane tile, nothing else"
    assert abs(here[0].y + lane.size_y - grade) < 1e-6, (
        "laid by its top: trodden earth flush with the grass beside it")


# -- authored bridges ---------------------------------------------------------

def _channel_and_bridge() -> Layout:
    """A water channel four tiles wide, with an authored bridge quad across it
    that overhangs both banks by two tiles -- the shape FTG's ``raised``
    ROAD_TEXTURE quads actually have (a ~20x20 m quad is wider than the
    stream it crosses, so its abutments land on dry ground)."""
    from citysmith.layout import LayoutArea

    layout = Layout(name="crossing", source="ftg", width=20.0, depth=12.0)
    layout.areas = [
        LayoutArea("water", [(8.0, 0.0), (12.0, 0.0), (12.0, 12.0), (8.0, 12.0)]),
        LayoutArea("bridge", [(6.0, 4.0), (14.0, 4.0), (14.0, 8.0), (6.0, 8.0)]),
    ]
    return layout


def test_an_authored_bridge_decks_the_water_and_only_the_water():
    """`LayoutArea("bridge")` cells become PIER exactly where the surface was
    WATER. The overhang stays bank: a pier tile over grass is a timber
    platform on dry land, and the bank itself is the approach -- the deck is
    laid by its top at grade, so it meets the bank flush with nothing added."""
    from citysmith import raster

    tm = raster.rasterize(_channel_and_bridge(), bridges=False)
    for z in range(4, 8):
        for x in range(8, 12):
            assert tm.surface[z][x] == raster.PIER, (x, z)
        for x in (6, 7, 12, 13):
            assert tm.surface[z][x] == raster.GROUND, (
                f"overhang at {(x, z)} must stay bank, not become deck over grass")
    for z in (0, 3, 8, 11):
        for x in range(8, 12):
            assert tm.surface[z][x] == raster.WATER, (x, z)


def test_the_channel_runs_on_under_an_authored_deck(catalog_palette):
    """An authored bridge cell is a plank cell once the raster paints it PIER:
    `_lay_terrain` beds and fills it like the water either side, and
    `_lay_bridges` lays the deck by its top, flush with the bank."""
    import math

    from citysmith import raster
    from citysmith.build import (
        WATER_SURFACE_DROP, Builder, _lay_bridges, _lay_terrain,
    )

    tm = raster.rasterize(_channel_and_bridge(), bridges=False)
    palette = catalog_palette
    b = Builder(palette, 0)
    grade = palette.require("floor").size_y
    roles = {raster.GROUND: "ground", raster.STREET: "street"}
    _lay_terrain(b, tm, lambda s, x, z: roles.get(s, "ground"),
                 grade=grade, taper={})
    laid = _lay_bridges(b, tm, grade=grade, taper={})
    assert laid == 16, "one deck per pier cell"

    # The stub has no bridge_deck pin, so the deck falls back to the street
    # role -- the fallback `_lay_bridges` documents.
    deck = palette.require("street")
    ground = palette.require("ground")

    def at_cell(x: int, z: int):
        return [p for p in b.placements
                if (int(math.floor(p.x + 1e-6)),
                    int(math.floor(p.z + 1e-6))) == (x, z)]

    decks = [p for p in at_cell(10, 5) if p.asset_id == deck.id]
    assert len(decks) == 1, "one deck tile on the pier cell"
    assert abs(decks[0].y + deck.size_y - grade) < 1e-6, (
        "the deck's top is the bank's top")
    beds = [p for p in at_cell(10, 5) if p.asset_id == ground.id]
    assert beds, "the channel is bedded under the deck"
    assert all(p.y + ground.size_y <= grade - WATER_SURFACE_DROP + 1e-6
               for p in beds), "the bed sits below the water column, not at grade"


# -- the forest mask ----------------------------------------------------------

def test_the_forest_outline_rides_the_tilemap_as_a_mask():
    """`LayoutArea("forest")` cells land in `TileMap.forest` -- a mask, not a
    surface. The floor of a wood is grass underfoot, so the cells stay GROUND
    and only the tree scatter reads the outline."""
    from citysmith import raster
    from citysmith.layout import LayoutArea

    layout = Layout(name="wood", source="ftg", width=20.0, depth=20.0)
    layout.areas = [LayoutArea(
        "forest", [(2.0, 2.0), (10.0, 2.0), (10.0, 10.0), (2.0, 10.0)])]
    tm = raster.rasterize(layout, bridges=False)

    assert (2, 2) in tm.forest and (9, 9) in tm.forest
    assert (12, 12) not in tm.forest
    assert tm.surface[4][4] == raster.GROUND, "forest floor is grass underfoot"


def test_cropping_a_map_keeps_the_forest_mask():
    """Every TileMap field has to survive `crop`, or --crop silently eats the
    feature -- the trap that took the storey counts once (`TileMap.crop`'s
    own comment). The mask is translated and filtered like the gates."""
    from citysmith import raster
    from citysmith.layout import LayoutArea

    layout = Layout(name="wood", source="ftg", width=20.0, depth=20.0)
    layout.areas = [LayoutArea(
        "forest", [(2.0, 2.0), (10.0, 2.0), (10.0, 10.0), (2.0, 10.0)])]
    tm = raster.rasterize(layout, bridges=False)

    cropped = tm.crop(5, 5, 8, 8)
    assert cropped.forest, "the mask must survive the crop"
    assert (0, 0) in cropped.forest, "cell (5,5) translated into the window"
    assert (4, 4) in cropped.forest, "cell (9,9) translated into the window"
    assert all(0 <= x < 8 and 0 <= z < 8 for x, z in cropped.forest)
    assert len(cropped.forest) == 25, "the 5x5 overlap of mask and window"


# -- the town wall ------------------------------------------------------------

def test_the_wall_band_is_thick_enough_to_have_a_core(village: Layout):
    """FTG ships no wall thickness, and the default one is too thin.

    A rampart is a full-cell core with thin curtain pieces hung on the faces
    that show. `Layout`'s 2.0-tile default gives a two-cell band, and on a
    circuit that runs diagonally that leaves almost every cell an edge cell
    with nothing behind it -- which is the "curtain-wall piece laid one per
    cell does not fill the cell" failure. Measured on East Tradebourne: at 2.0
    tiles only 13% of wall cells have all four orthogonal neighbours in the
    band; at 4.5 m it is 41%, which matches Forest Church's MFCG-derived 2.77.
    """
    assert village.wall_thickness >= 2.5, "a rampart this thin has no core"
    metric = ftg.DEFAULT_WALL_THICKNESS_M * ftg.FEET_PER_METRE / TILE_FEET
    assert village.wall_thickness == pytest.approx(metric)


def test_a_wall_polyline_rasterises_to_a_band_with_an_interior():
    """The band has to be deep enough for cells that no face reaches.

    This is the invariant behind the constant: a diagonal run is the worst
    case, because a stroke of a given width covers fewer whole cells across
    a diagonal than across an axis.
    """
    from citysmith import raster
    from citysmith.layout import Layout as L

    layout = L(name="wall", source="ftg", width=60.0, depth=60.0)
    layout.wall_thickness = ftg.DEFAULT_WALL_THICKNESS_M * ftg.FEET_PER_METRE / TILE_FEET
    layout.walls = [[(6.0, 6.0), (54.0, 54.0)]]          # a pure diagonal
    layout.buildings = list(_playable_buildings())
    tm = raster.rasterize(layout)

    band = {(x, z) for z in range(tm.depth) for x in range(tm.width) if tm.wall[z][x]}
    assert band
    interior = {
        (x, z) for (x, z) in band
        if all((x + dx, z + dz) in band for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    }
    assert len(interior) >= 0.2 * len(band), (
        f"only {len(interior)} of {len(band)} wall cells have a full orthogonal "
        "cross -- the band is too thin to carry a core"
    )


def _playable_buildings():
    from citysmith.layout import LayoutBuilding
    for i in range(3):
        x = i * 20.0
        yield LayoutBuilding(
            id=f"house-{i:04d}",
            ring=[(x, 0.0), (x + 8.0, 0.0), (x + 8.0, 8.0), (x, 8.0), (x, 0.0)],
        )


# -- gates derived from where a road crosses the wall --------------------------
#
# FTG exports no gates at all, so `layout.gates` was empty on every FTG town and
# every build printed "no gates found; routing from the map edge instead".
# `raster.road_classes` decides `main` partly on `at_gate`, and the raster walks
# reachability from `sorted(tm.gates) or _fallback_starts(tm)` -- so an empty
# gate list silently changed both.


def _road(points, kind="road"):
    from citysmith.layout import LayoutRoad
    return LayoutRoad(points=list(points), kind=kind)


def _walled_town(roads, walls):
    from citysmith.layout import Layout as L
    layout = L(name="gates", source="ftg", width=100.0, depth=100.0)
    layout.walls = [list(w) for w in walls]
    layout.roads = [_road(*r) if isinstance(r, tuple) else _road(r) for r in roads]
    return layout


def test_a_gate_lands_where_a_road_actually_crosses_the_wall():
    """Not near the wall -- *on* it, and on the road too."""
    layout = _walled_town(
        roads=[[(0.0, 50.0), (100.0, 50.0)]],       # runs east, straight through
        walls=[[(40.0, 0.0), (40.0, 100.0)]],       # runs north, straight across
    )
    gates = ftg.gates_from_roads(layout)
    assert len(gates) == 1, gates
    x, z = gates[0]
    assert abs(x - 40.0) < 1e-6 and abs(z - 50.0) < 1e-6


def test_a_road_that_only_comes_near_the_wall_earns_no_gate():
    """The distinction this function exists for.

    `mfcg._find_gates` matches road vertices to *wall vertices* within a
    tolerance, because MFCG puts a gatehouse on a vertex deliberately. FTG's
    vertices are just bends, and on East Tradebourne the median wall vertex has
    a road within 7 tiles -- so a proximity rule would deal gates at bends.
    """
    layout = _walled_town(
        roads=[[(0.0, 50.0), (36.0, 50.0)]],        # stops 4 tiles short
        walls=[[(40.0, 0.0), (40.0, 100.0)]],
    )
    assert ftg.gates_from_roads(layout) == []


def test_a_trail_does_not_earn_a_gate():
    """Same exclusion `raster.NOT_THOROUGHFARES` applies to widening: a trail is
    walked, not driven, and a river through a wall is a watergate."""
    layout = _walled_town(
        roads=[([(0.0, 50.0), (100.0, 50.0)], "trail")],
        walls=[[(40.0, 0.0), (40.0, 100.0)]],
    )
    assert ftg.gates_from_roads(layout) == []


def test_an_unwalled_settlement_has_no_gates():
    """Pelvesthollow and Graybank export zero wall rings, so their "no gates"
    is a fact about the town rather than a defect -- an unwalled village is
    entered from anywhere, which `_fallback_starts` already models."""
    layout = _walled_town(roads=[[(0.0, 50.0), (100.0, 50.0)]], walls=[])
    assert ftg.gates_from_roads(layout) == []


def test_two_roads_through_one_opening_give_one_gate():
    """A fork at the gate is still one gate, and it keeps the major road's
    crossing rather than whichever segment was tested first."""
    layout = _walled_town(
        roads=[
            [(0.0, 50.0), (100.0, 50.0)],           # the long one
            [(30.0, 47.0), (60.0, 47.0)],           # a short branch alongside
        ],
        walls=[[(40.0, 0.0), (40.0, 100.0)]],
    )
    gates = ftg.gates_from_roads(layout)
    assert len(gates) == 1, gates
    assert abs(gates[0][1] - 50.0) < 1e-6, "the major road should own the gate"
