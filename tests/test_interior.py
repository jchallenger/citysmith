"""One imported building, turned into somewhere the party can stand.

The invariants here are the ones that make an interior playable rather than
merely generated: every room reachable, no wall where a doorway is, levels that
do not overlap when they are laid side by side, and a roster that is the same
people every time you walk back in.
"""

from __future__ import annotations

import json

import pytest

from citysmith import interior
from citysmith.build import build_interior
from citysmith.floorplan import Floorplan
from citysmith.layout import Layout, LayoutBuilding, LayoutRoad


def _square(x: float, z: float, w: float, d: float) -> list[tuple[float, float]]:
    return [(x, z), (x + w, z), (x + w, z + d), (x, z + d), (x, z)]


def _town() -> Layout:
    lay = Layout(name="Testbury", source="ftg", width=60, depth=60)
    lay.buildings = [
        LayoutBuilding(id="tavern-0001", ring=_square(10, 10, 9, 7), kind="tavern",
                       floors=2, name="The Halfling and the Fox"),
        LayoutBuilding(id="house-0002", ring=_square(30, 10, 6, 5), kind="house",
                       floors=1, name="Farm"),
        LayoutBuilding(id="house-0003", ring=_square(40, 10, 6, 5), kind="house",
                       floors=1, name="Farm"),
        LayoutBuilding(id="shed-0004", ring=_square(50, 10, 2, 2), kind="shed",
                       floors=1, name=""),
    ]
    lay.roads = [LayoutRoad(points=[(0, 20), (60, 20)], width=2.0, kind="road")]
    return lay


# -- finding it ---------------------------------------------------------------

def test_a_building_is_found_by_id_name_or_kind():
    lay = _town()
    assert interior.find(lay, "tavern-0001").id == "tavern-0001"
    assert interior.find(lay, "halfling").id == "tavern-0001"
    assert interior.find(lay, "The Halfling and the Fox").id == "tavern-0001"
    assert interior.find(lay, "kind:tavern").id == "tavern-0001"


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """FTG names six of Graybank's buildings 'Farm'. Picking the first one
    silently is how the party ends up in the wrong barn."""
    lay = _town()
    with pytest.raises(interior.InteriorError) as exc:
        interior.find(lay, "Farm")
    assert "house-0002" in str(exc.value) and "house-0003" in str(exc.value)


def test_an_unknown_reference_names_what_to_try_instead():
    with pytest.raises(interior.InteriorError) as exc:
        interior.find(_town(), "the moon")
    assert "kind:tavern" in str(exc.value)


# -- the shape of it ----------------------------------------------------------

def test_the_interior_is_the_building_not_its_bounding_box():
    """A footprint at 40 degrees has a bounding box half again its own size.
    Built to that, the interior is a room with a house-shaped hole in it."""
    import math

    a = math.radians(40)
    w, d = 9.0, 7.0
    corners = [(0, 0), (w, 0), (w, d), (0, d)]
    turned = [(x * math.cos(a) - z * math.sin(a), x * math.sin(a) + z * math.cos(a))
              for x, z in corners]
    b = LayoutBuilding(id="tavern-0009", ring=turned + [turned[0]], kind="tavern")

    rect = interior.interior_rect(b)
    assert (rect.w, rect.d) == (9, 7)


def test_a_tiny_footprint_still_makes_a_room():
    b = LayoutBuilding(id="shed-0004", ring=_square(0, 0, 2, 1), kind="shed")
    rect = interior.interior_rect(b)
    assert rect.w >= interior.MIN_INTERIOR and rect.d >= interior.MIN_INTERIOR


def test_the_door_faces_the_road():
    lay = _town()
    # The road runs east-west at z=20, south of every building in the fixture.
    for b in lay.buildings:
        assert interior.entrance_side(lay, b) == "s"


def test_the_storey_cap_is_applied_once_and_holds():
    lay = _town()
    tavern = interior.find(lay, "tavern-0001")
    fp = interior.plan(lay, tavern, seed=1, max_levels=1)
    assert fp.levels == 1
    assert {r.level for r in fp.rooms} == {0}


# -- levels side by side ------------------------------------------------------

def test_spread_levels_do_not_overlap():
    """A stacked storey is a storey the camera cannot see into, and TaleSpire
    cannot hide one -- so levels are laid out in a row. They must not touch."""
    lay = _town()
    fp = interior.plan(lay, interior.find(lay, "tavern-0001"), seed=3, max_levels=2)
    assert fp.levels == 2
    ground, upper = fp.rect_on(0), fp.rect_on(1)
    assert upper.x >= ground.x2, "levels overlap"
    assert ground.z == upper.z, "levels should sit in a row, not a diagonal"


def test_every_room_on_every_level_is_reachable():
    """A BSP plus a spanning tree of doorways; an unreachable room is a bug at
    the table, not a feature."""
    lay = _town()
    fp = interior.plan(lay, interior.find(lay, "tavern-0001"), seed=7, max_levels=2)

    for level in range(fp.levels):
        rooms = fp.rooms_on(level)
        if len(rooms) < 2:
            continue
        # Two rooms are joined when a door on a shared edge belongs to one and
        # opens onto the other.
        joined: dict[str, set[str]] = {r.id: set() for r in rooms}
        for door in fp.doors:
            if door.level != level or door.exterior:
                continue
            here = next((r for r in rooms if r.rect.contains(door.x, door.z)), None)
            if here is None:
                continue
            dx, dz = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}[door.side]
            there = next(
                (r for r in rooms if r.rect.contains(door.x + dx, door.z + dz)), None
            )
            if there is None or there.id == here.id:
                continue
            joined[here.id].add(there.id)
            joined[there.id].add(here.id)

        seen = {rooms[0].id}
        queue = [rooms[0].id]
        while queue:
            for nxt in joined[queue.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        assert seen == {r.id for r in rooms}, f"level {level} has an unreachable room"


def test_spread_levels_are_built_at_one_height(catalog_palette):
    """Side by side means side by side: every level's floor on the ground."""
    lay = _town()
    fp = interior.plan(lay, interior.find(lay, "tavern-0001"), seed=11, max_levels=2)
    b = build_interior(fp, catalog_palette, seed=11, stack=False)
    floors = [p for p in b.placements if p.y < 0.6]
    assert floors, "no ground-level geometry at all"
    for level in range(fp.levels):
        rect = fp.rect_on(level)
        assert any(rect.x <= p.x < rect.x2 for p in floors), \
            f"level {level} has nothing at ground height"


def test_stacked_levels_still_stack(catalog_palette):
    lay = _town()
    fp = interior.plan(lay, interior.find(lay, "tavern-0001"), seed=11,
                       max_levels=2, spread=False)
    b = build_interior(fp, catalog_palette, seed=11, stack=True)
    assert max(p.y for p in b.placements) > 1.5, "the upper storey did not rise"


def test_a_doorway_is_not_also_a_wall(catalog_palette):
    """The failure `verify` was written for, one building down: a door built
    over with a wall segment is a room nobody can enter."""
    lay = _town()
    fp = interior.plan(lay, interior.find(lay, "tavern-0001"), seed=5, max_levels=2)
    b = build_interior(fp, catalog_palette, seed=5, stack=False)

    door_ids = {a.id for a in b.palette.catalog.assets if "door" in a.name.lower()}
    doorways = {(round(p.x, 2), round(p.z, 2)) for p in b.placements
                if p.asset_id in door_ids}
    assert doorways, "no doors were built at all"

    walls = [p for p in b.placements
             if p.asset_id == b.palette.require("wall").id
             or p.asset_id == (b.palette.resolve("wall_interior") or
                               b.palette.require("wall")).id]
    clashes = [p for p in walls if (round(p.x, 2), round(p.z, 2)) in doorways]
    assert not clashes, f"{len(clashes)} wall segment(s) built over a doorway"


# -- who is in it -------------------------------------------------------------

def test_the_same_building_holds_the_same_people_every_time():
    """Walking back in must not reroll the room."""
    lay = _town()
    tavern = interior.find(lay, "tavern-0001")
    first = interior.occupants(tavern, seed=33)
    again = interior.occupants(tavern, seed=33)
    assert [o.describe() for o in first] == [o.describe() for o in again]
    assert first[0].role == "innkeeper"


def test_occupants_scale_with_the_building_and_stop():
    lay = _town()
    big = LayoutBuilding(id="tavern-0100", ring=_square(0, 0, 40, 30), kind="tavern")
    small = interior.find(lay, "house-0002")
    assert len(interior.occupants(big, seed=1)) > len(interior.occupants(small, seed=1))
    assert len(interior.occupants(big, seed=1)) <= interior.MAX_OCCUPANTS


def test_an_empty_shed_is_empty():
    lay = _town()
    assert interior.occupants(interior.find(lay, "shed-0004"), seed=1) == []


def test_an_authored_roster_wins(tmp_path):
    """The export carries no occupants -- checked, not assumed -- so these are
    derived. Anything a person actually wrote down outranks the derivation."""
    lay = _town()
    tavern = interior.find(lay, "tavern-0001")
    path = tmp_path / "occupants.json"
    path.write_text(json.dumps({
        "tavern-0001": [{"name": "Mathias Shore", "role": "guild speaker",
                         "doing": "waiting for the party"}]
    }), encoding="utf-8")

    roster = interior.load_roster(path)
    people = interior.occupants(tavern, seed=33, roster=roster)
    assert len(people) == 1
    assert people[0].name == "Mathias Shore"
    assert people[0].authored is True


def test_a_missing_roster_is_not_an_error(tmp_path):
    assert interior.load_roster(tmp_path / "nope.json") == {}


def test_night_thins_the_room():
    lay = _town()
    tavern = LayoutBuilding(id="tavern-0100", ring=_square(0, 0, 40, 30), kind="tavern")
    assert len(interior.occupants(tavern, seed=1, hour="night")) < \
        len(interior.occupants(tavern, seed=1, hour="day"))
