"""Preparing one building as the board a party walks onto.

The invariants here are about *reuse and arrival*: the same building has to
produce the same scene id and the same board name every time, or the second
visit builds a second board; and the four marks have to be somewhere a party
can actually stand -- inside, together, not in the doorway, and not inside a
barrel.
"""

from __future__ import annotations

import json

import pytest

from citysmith import interior, scene as scene_mod
from citysmith.build import cell_of
from citysmith.config import Config
from citysmith.layout import Layout, LayoutBuilding, LayoutRoad


def _square(x, z, w, d):
    return [(x, z), (x + w, z), (x + w, z + d), (x, z + d), (x, z)]


@pytest.fixture
def town() -> Layout:
    lay = Layout(name="Graybank", source="ftg", width=60, depth=60)
    lay.buildings = [
        LayoutBuilding(id="tavern-0014", ring=_square(10, 10, 9, 7), kind="tavern",
                       floors=2, name="The Halfling and the Fox"),
        LayoutBuilding(id="house-0002", ring=_square(30, 10, 6, 5), kind="house",
                       floors=1, name="Farm"),
    ]
    lay.roads = [LayoutRoad(points=[(0, 5), (60, 5)], width=2.0, kind="road")]
    return lay


@pytest.fixture
def cfg() -> Config:
    return Config.defaults()


def _built(town, cfg, palette, ref="tavern-0014"):
    building = interior.find(town, ref)
    return scene_mod.build(town, building, palette, cfg)


# -- identity -----------------------------------------------------------------

def test_the_same_building_gives_the_same_scene_id_and_board(town, cfg):
    """Reuse is built on this. If the id moves, the second visit to a room
    builds a second board and the first one is orphaned."""
    b = interior.find(town, "tavern-0014")
    assert scene_mod.scene_id(town.name, b) == "graybank-tavern-0014"
    assert scene_mod.scene_id(town.name, b) == scene_mod.scene_id(town.name, b)
    assert scene_mod.board_name(cfg, town, b) == \
        "Interior - Graybank - The Halfling and the Fox"


def test_two_buildings_never_share_a_scene_id(town, cfg):
    ids = {scene_mod.scene_id(town.name, b) for b in town.buildings}
    assert len(ids) == len(town.buildings)


def test_a_nameless_building_gets_an_invented_name_and_its_id(town, cfg):
    """Every MFCG building is nameless, so the name here is invented -- and two
    of them can collide. The campaign list shows a name and nothing else, so
    the id rides along to tell them apart."""
    b = LayoutBuilding(id="house-0042", ring=_square(0, 0, 6, 5), kind="house")
    lay = Layout(name="Forest Church", source="mfcg")
    lay.buildings = [b]
    name = scene_mod.board_name(cfg, lay, b)
    assert name.startswith("Interior - Forest Church - ")
    assert name.endswith("(house-0042)")
    assert scene_mod.board_name(cfg, lay, b) == name, "an invented name must be stable"


def test_a_name_the_export_repeats_gets_its_id(town, cfg):
    """FTG names six of Graybank's buildings 'Farm'."""
    lay = Layout(name="Graybank", source="ftg")
    lay.buildings = [
        LayoutBuilding(id="stable-0003", ring=_square(0, 0, 6, 5), kind="stable",
                       name="Farm"),
        LayoutBuilding(id="stable-0004", ring=_square(9, 0, 6, 5), kind="stable",
                       name="Farm"),
    ]
    names = {scene_mod.board_name(cfg, lay, b) for b in lay.buildings}
    assert names == {"Interior - Graybank - Farm (stable-0003)",
                     "Interior - Graybank - Farm (stable-0004)"}


def test_a_long_board_name_is_truncated_not_rejected(town, cfg):
    b = LayoutBuilding(id="tavern-0001", ring=_square(0, 0, 6, 5), kind="tavern",
                       name="The Exceptionally Long Sign Over The Door Of This Inn")
    lay = Layout(name="Graybank", source="ftg")
    lay.buildings = [b]
    name = scene_mod.board_name(cfg, lay, b)
    assert len(name) <= int(cfg.get("board.max_name"))


# -- where the party stands ---------------------------------------------------

def test_four_marks_inside_the_building_and_not_in_the_doorway(town, cfg, catalog_palette):
    sc, b, fp = _built(town, cfg, catalog_palette)
    rect = fp.rect_on(0)
    door = scene_mod._door_cell(fp)

    assert len(sc.party) == 4
    for m in sc.party:
        assert rect.contains(m.x, m.z), f"{m.name} is outside the building"
        assert (m.x, m.z) != (door[0], door[1]), "a mini is standing in the doorway"
    assert len({(m.x, m.z) for m in sc.party}) == 4, "two characters on one square"


def test_the_marks_are_together_not_scattered(town, cfg, catalog_palette):
    """A party arrives as a party. Four marks in four corners is four separate
    entrances, which is not what walking through a door looks like."""
    sc, _b, _fp = _built(town, cfg, catalog_palette)
    xs = [m.x for m in sc.party]
    zs = [m.z for m in sc.party]
    assert max(xs) - min(xs) <= 2 and max(zs) - min(zs) <= 2


def test_arrival_outside_puts_them_on_the_apron(town, cfg, catalog_palette):
    cfg.data["party"]["arrival"] = "outside"
    sc, _b, fp = _built(town, cfg, catalog_palette)
    rect = fp.rect_on(0)
    for m in sc.party:
        assert not rect.contains(m.x, m.z), f"{m.name} is indoors"


def test_party_names_from_the_config_reach_the_marks(town, cfg, catalog_palette):
    cfg.data["party"]["names"] = ["Cinder", "Ilian", "Karai", "Lilli"]
    sc, _b, _fp = _built(town, cfg, catalog_palette)
    assert [m.name for m in sc.party] == ["Cinder", "Ilian", "Karai", "Lilli"]


def test_party_size_is_a_setting(town, cfg, catalog_palette):
    cfg.data["party"]["size"] = 6
    sc, _b, _fp = _built(town, cfg, catalog_palette)
    assert len(sc.party) == 6


# -- what the marks are made of ----------------------------------------------

def _cells(builder, predicate=None):
    """Every placement's cell, with its asset, for geometry assertions."""
    out = []
    for p in builder.placements:
        asset = builder.byid.get(p.asset_id)
        if asset is None:
            continue
        if predicate and not predicate(p, asset):
            continue
        out.append((cell_of(p, asset), p, asset))
    return out


def test_a_mark_replaces_the_floor_rather_than_covering_it(town, cfg, catalog_palette):
    """Two coplanar surfaces in one cell is the seam that shifts with the
    camera. The mark is the floor in that square, not a sticker on it."""
    sc, b, _fp = _built(town, cfg, catalog_palette)
    marks = {(m.x, m.z) for m in sc.party}

    at_floor_level = [
        (cell, p, a) for cell, p, a in _cells(b)
        if cell in marks and p.y + a.size_y <= 0.51
    ]
    for cell in marks:
        here = [x for x in at_floor_level if x[0] == cell]
        assert len(here) == 1, f"{len(here)} surfaces stacked on the mark at {cell}"
        assert here[0][2].name.startswith("Moorgoth"), "that is not the mark tile"


def test_nobody_arrives_inside_a_barrel(town, cfg, catalog_palette):
    sc, b, _fp = _built(town, cfg, catalog_palette)
    marks = {(m.x, m.z) for m in sc.party}
    props = [cell for cell, p, _a in _cells(b) if p.asset_id in b.prop_ids]
    assert not (marks & set(props)), "a prop is standing on a party mark"


def test_the_marks_are_at_the_same_height_as_the_floor(town, cfg, catalog_palette):
    """A mark half a tile proud of the boards is a step, and a mini on a step
    is a mini nobody can line up with the grid."""
    sc, b, _fp = _built(town, cfg, catalog_palette)
    marks = {(m.x, m.z) for m in sc.party}
    tops = {round(p.y + a.size_y, 3) for cell, p, a in _cells(b) if cell in marks
            and p.y + a.size_y <= 0.51}
    assert tops == {0.5}


# -- the apron ----------------------------------------------------------------

def test_the_door_opens_onto_ground_not_the_void(town, cfg, catalog_palette):
    _sc, b, fp = _built(town, cfg, catalog_palette)
    dx, dz, side = scene_mod._door_cell(fp)
    ox, oz = scene_mod._OUTWARD[side]
    outside = (dx + ox, dz + oz)
    laid = {cell for cell, _p, _a in _cells(b)}
    assert outside in laid, "there is nothing to stand on outside the front door"


def test_the_apron_is_flush_with_the_floor(town, cfg, catalog_palette):
    """The kerb defect, one storey down: ground laid from a common bottom sits
    a quarter tile below a floor of a different thickness."""
    _sc, b, fp = _built(town, cfg, catalog_palette)
    inside = set(fp.rect_on(0).tiles())
    tops = {round(p.y + a.size_y, 3) for cell, p, a in _cells(b)
            if cell not in inside and p.y + a.size_y <= 0.51}
    assert tops == {0.5}, f"the apron is not flush: {sorted(tops)}"


def test_no_cell_anywhere_has_two_surfaces_in_it(town, cfg, catalog_palette):
    """One rule, whole board: nothing a creature stands on is doubled.

    Caught the paved approach laying cobble on top of the grass it had just
    laid -- coplanar faces, which is the seam that shifts with the camera,
    and the exact thing the marks are careful about two cells away.
    """
    _sc, b, _fp = _built(town, cfg, catalog_palette)
    seen: dict[tuple[int, int], int] = {}
    for cell, p, a in _cells(b):
        if p.y + a.size_y <= 0.51:
            seen[cell] = seen.get(cell, 0) + 1
    doubled = [c for c, n in seen.items() if n > 1]
    assert not doubled, f"{len(doubled)} cell(s) with stacked surfaces: {doubled[:5]}"


def test_the_path_out_of_the_door_is_paved(town, cfg, catalog_palette):
    _sc, b, fp = _built(town, cfg, catalog_palette)
    dx, dz, side = scene_mod._door_cell(fp)
    ox, oz = scene_mod._OUTWARD[side]
    outside = (dx + ox, dz + oz)
    paved = [a.name for cell, _p, a in _cells(b) if cell == outside]
    assert any("Cobble" in n for n in paved), f"outside the door is {paved}"


def test_nothing_in_the_scene_has_a_negative_coordinate(town, cfg, catalog_palette):
    """A slab cannot store one. The encoder shifts the whole board to fix it,
    which works -- but then the tile numbers in the manifest stop matching the
    tile numbers on the board, and the brief is what the GM reads."""
    _sc, b, _fp = _built(town, cfg, catalog_palette)
    assert min(p.x for p in b.placements) >= 0
    assert min(p.z for p in b.placements) >= 0


# -- writing it out -----------------------------------------------------------

def test_a_scene_round_trips_through_its_manifest(tmp_path, town, cfg, catalog_palette):
    sc, b, fp = _built(town, cfg, catalog_palette)
    scene_mod.write(sc, b, fp, tmp_path, cfg)

    again = scene_mod.Scene.load(tmp_path / "scene.json")
    assert again.scene_id == sc.scene_id
    assert again.board == sc.board
    assert [(m.name, m.x, m.z) for m in again.party] == \
        [(m.name, m.x, m.z) for m in sc.party]
    assert again.slabs == sc.slabs


def test_an_interior_is_one_paste(tmp_path, town, cfg, catalog_palette):
    """At the 24-tile chunk default a two-level plan laid side by side spans
    two grid columns and comes out as two files, for 459 assets."""
    sc, b, fp = _built(town, cfg, catalog_palette)
    written = scene_mod.write(sc, b, fp, tmp_path, cfg)
    assert len(written) == 1
    order = (tmp_path / f"{sc.scene_id}-paste-order.txt").read_text(encoding="utf-8")
    assert order.split() == sc.slabs


def test_rebuilding_a_scene_does_not_leave_last_run_beside_this_one(
        tmp_path, town, cfg, catalog_palette):
    """Stale chunk files paste without complaint and silently mix two
    revisions of the map -- the failure `_write_chunks` clears for towns."""
    sc, b, fp = _built(town, cfg, catalog_palette)
    scene_mod.write(sc, b, fp, tmp_path, cfg)
    (tmp_path / f"{sc.scene_id}-r09c09.slab.txt").write_text("stale", encoding="utf-8")

    sc2, b2, fp2 = _built(town, cfg, catalog_palette)
    scene_mod.write(sc2, b2, fp2, tmp_path, cfg)
    assert sorted(p.name for p in tmp_path.glob("*.slab.txt")) == sorted(sc2.slabs)


def test_the_brief_says_where_the_occupants_came_from(tmp_path, town, cfg, catalog_palette):
    """The export has no occupants. A brief that presents derived people as
    exported ones is the same failure as a metric that reads the plan."""
    sc, b, fp = _built(town, cfg, catalog_palette)
    scene_mod.write(sc, b, fp, tmp_path, cfg)
    text = (tmp_path / "brief.md").read_text(encoding="utf-8")
    assert "derived, not exported" in text
    assert sc.board in text
    for m in sc.party:
        assert f"({m.x}, {m.z})" in text


def test_the_manifest_records_the_building_position(town, cfg, catalog_palette):
    """So that a re-import which renumbers the buildings can be caught rather
    than quietly reusing the wrong room's board."""
    sc, _b, _fp = _built(town, cfg, catalog_palette)
    b = interior.find(town, "tavern-0014")
    assert sc.centroid == pytest.approx(b.centroid, abs=0.01)


# -- the config ---------------------------------------------------------------

def test_defaults_work_with_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config.load()
    assert cfg.get("party.size") == 4
    assert cfg.get("board.prefix") == "Interior - "


def test_a_named_config_that_is_missing_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        Config.load(tmp_path / "nope.json")


def test_a_file_overlays_the_defaults_key_by_key(tmp_path):
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({"party": {"size": 6}}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.get("party.size") == 6
    assert cfg.get("party.arrival") == "inside", "an unrelated default was lost"
    assert cfg.get("interior.pad") == 3


def test_an_unknown_key_is_reported_not_ignored(tmp_path):
    """A typo in a config runs clean and does nothing, and the difference
    shows up on the board an hour later."""
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({"party": {"sizr": 6}, "styl": "medieval"}),
                    encoding="utf-8")
    cfg = Config.load(path)
    assert set(cfg.unknown) == {"party.sizr", "styl"}


def test_comments_in_the_config_are_not_settings(tmp_path):
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({"_comment": "hello", "party": {"_comment": "hi"}}),
                    encoding="utf-8")
    assert Config.load(path).unknown == []
