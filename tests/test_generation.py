"""Tests for city, floorplan, site scoring and slab building.

These deliberately assert *invariants* rather than exact output: the generator
is free to change its aesthetics, but overlapping buildings, unreachable rooms,
and floating geometry are always bugs.
"""

from __future__ import annotations

import pytest

from citysmith import floorplan as fp_mod
from citysmith import sites
from citysmith.build import (
    ROT_E, ROT_N, ROT_S, ROT_W, place_centered, place_tile, place_wall, rotated_footprint,
)
from citysmith.catalog import Asset
from citysmith.city import CityParams, City, Rect, generate


def overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.x2 and b.x < a.x2 and a.z < b.z2 and b.z < a.z2


# -- city ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def town() -> City:
    return generate(CityParams(size="town"), seed=1234)


def test_city_is_deterministic():
    a = generate(CityParams(size="village"), seed=7)
    b = generate(CityParams(size="village"), seed=7)
    assert a.to_dict() == b.to_dict()


def test_different_seeds_differ():
    a = generate(CityParams(size="village"), seed=1)
    b = generate(CityParams(size="village"), seed=2)
    assert a.to_dict() != b.to_dict()


def test_buildings_never_overlap(town: City):
    rects = [b.rect for b in town.buildings]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not overlaps(a, b), f"{a} overlaps {b}"


def test_buildings_never_overlap_streets(town: City):
    for building in town.buildings:
        for street in town.streets:
            assert not overlaps(building.rect, street.rect)


def test_everything_stays_inside_the_city(town: City):
    bounds = Rect(0, 0, town.width, town.depth)
    for b in town.buildings:
        assert b.rect.x >= bounds.x and b.rect.x2 <= bounds.x2
        assert b.rect.z >= bounds.z and b.rect.z2 <= bounds.z2


def test_walled_city_keeps_buildings_inside_the_wall(town: City):
    assert town.walled and town.wall_rect is not None
    inner = town.wall_rect.inset(1)
    for b in town.buildings:
        assert b.rect.x >= inner.x and b.rect.x2 <= inner.x2
        assert b.rect.z >= inner.z and b.rect.z2 <= inner.z2


def test_buildings_are_playable_size(town: City):
    for b in town.buildings:
        assert b.rect.w >= 3 and b.rect.d >= 3


def test_every_building_has_a_known_district(town: City):
    names = {d.name for d in town.districts}
    assert all(b.district in names for b in town.buildings)


def test_entrances_are_cardinal(town: City):
    assert all(b.entrance in ("n", "s", "e", "w") for b in town.buildings)


def test_city_round_trips_through_json(tmp_path, town: City):
    path = tmp_path / "city.json"
    town.save(path)
    loaded = City.load(path)
    assert loaded.to_dict() == town.to_dict()


def test_tiny_city_rejected():
    with pytest.raises(ValueError, match="too small"):
        generate(CityParams(size=12), seed=0)


def test_unknown_size_rejected():
    with pytest.raises(ValueError, match="Unknown size"):
        CityParams(size="enormous").resolved_size()


@pytest.mark.parametrize("size", ["hamlet", "village", "town", "city"])
def test_all_sizes_produce_buildings(size: str):
    city = generate(CityParams(size=size), seed=3)
    assert len(city.buildings) > 0


def test_unwalled_city_has_no_gates():
    city = generate(CityParams(size="village", walled=False), seed=5)
    assert city.gates == []
    assert city.wall_rect is None


# -- sites --------------------------------------------------------------------

def test_ranking_is_ordered_and_explained(town: City):
    ranked = sites.rank(town, top=10)
    assert len(ranked) == 10
    scores = [s.score for s in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(s.reasons for s in ranked)


def test_ranking_is_stable(town: City):
    assert [s.id for s in sites.rank(town, top=8)] == [s.id for s in sites.rank(town, top=8)]


def test_kind_filter(town: City):
    taverns = sites.rank(town, kind="tavern")
    assert taverns and all(s.building.kind == "tavern" for s in taverns)


def test_min_floors_filter(town: City):
    multi = sites.rank(town, min_floors=2)
    assert all(s.building.floors >= 2 for s in multi)


def test_hook_raises_score(town: City):
    with_hook = [s for s in sites.rank(town) if s.building.hook]
    assert with_hook
    assert any("hook:" in r for r in with_hook[0].reasons)


def test_best_raises_when_nothing_matches(town: City):
    with pytest.raises(ValueError, match="No building"):
        sites.best(town, kind="nonexistent-kind")


# -- floorplan ----------------------------------------------------------------

@pytest.fixture(scope="module")
def plan(town: City):
    building = max(town.buildings, key=lambda b: b.rect.area)
    return fp_mod.generate(building, seed=99)


def test_floorplan_is_deterministic(town: City):
    b = town.buildings[0]
    assert fp_mod.generate(b, seed=4).to_dict() == fp_mod.generate(b, seed=4).to_dict()


def test_rooms_tile_the_footprint_without_overlap(plan):
    for level in range(plan.levels):
        rooms = plan.rooms_on(level)
        assert rooms
        area = sum(r.rect.area for r in rooms)
        assert area == plan.rect.area, "rooms must exactly tile the footprint"
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                assert not overlaps(a.rect, b.rect)


def test_rooms_stay_inside_the_building(plan):
    for room in plan.rooms:
        assert room.rect.x >= plan.rect.x and room.rect.x2 <= plan.rect.x2
        assert room.rect.z >= plan.rect.z and room.rect.z2 <= plan.rect.z2


def test_every_level_is_fully_connected(plan):
    """A room the party cannot reach is a bug at the table."""
    for level in range(plan.levels):
        rooms = plan.rooms_on(level)
        if len(rooms) < 2:
            continue
        doors = [d for d in plan.doors if d.level == level and not d.exterior]

        def room_at(x: int, z: int):
            for r in rooms:
                if r.rect.contains(x, z):
                    return r.id
            return None

        adjacency: dict[str, set[str]] = {r.id: set() for r in rooms}
        for d in doors:
            here = room_at(d.x, d.z)
            dx, dz = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}[d.side]
            there = room_at(d.x + dx, d.z + dz)
            if here and there and here != there:
                adjacency[here].add(there)
                adjacency[there].add(here)

        seen = {rooms[0].id}
        stack = [rooms[0].id]
        while stack:
            for nxt in adjacency[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        assert len(seen) == len(rooms), f"level {level}: {len(rooms) - len(seen)} unreachable room(s)"


def test_has_exactly_one_exterior_door(plan):
    assert sum(1 for d in plan.doors if d.exterior) == 1


def test_exterior_door_sits_on_the_perimeter(plan):
    d = next(d for d in plan.doors if d.exterior)
    r = plan.rect
    assert d.x in (r.x, r.x2 - 1) or d.z in (r.z, r.z2 - 1)


def test_stairs_connect_consecutive_levels(plan):
    assert len(plan.stairs) == plan.levels - 1
    for s in plan.stairs:
        assert s.to_level == s.from_level + 1


def test_floorplan_round_trips(tmp_path, plan):
    path = tmp_path / "p.json"
    plan.save(path)
    assert fp_mod.Floorplan.load(path).to_dict() == plan.to_dict()


# -- placement math -----------------------------------------------------------

FLOOR = Asset(id="a" * 8 + "-1111-2222-3333-444444444444", name="floor", kind="tile",
              pack="p", group_tag="floor", tags=(), folder="", size_x=1.0, size_y=0.5, size_z=1.0)
WALL = Asset(id="b" * 8 + "-1111-2222-3333-444444444444", name="wall", kind="tile",
             pack="p", group_tag="wall", tags=(), folder="", size_x=1.0, size_y=2.0, size_z=0.5)


def test_unit_tile_fills_its_cell():
    p = place_tile(FLOOR, 3, 5, y=0.0)
    assert (p.x, p.y, p.z) == (3.0, 0.0, 5.0)


def test_walls_sit_on_opposite_edges_of_the_same_cell():
    n = place_wall(WALL, 0, 0, "n", y=0.5)
    s = place_wall(WALL, 0, 0, "s", y=0.5)
    assert n.rot == ROT_N and s.rot == ROT_S
    # 0.5-thick walls hug opposite edges of a 1-tile cell.
    assert n.z == pytest.approx(0.0)
    assert s.z == pytest.approx(0.5)
    assert n.x == s.x


def test_east_and_west_walls_are_rotated_and_offset():
    """Quarter-turned walls hug their tile's side, spanning its full depth.

    The stored coordinate is the min corner of the *rotated* box, so a
    1.0 x 0.5 wall turned 90 degrees occupies 0.5 x 1.0.
    """
    w = place_wall(WALL, 0, 0, "w", y=0.5)
    e = place_wall(WALL, 0, 0, "e", y=0.5)
    assert w.rot == ROT_W and e.rot == ROT_E
    assert w.x == pytest.approx(0.0)   # occupies x 0.0-0.5
    assert e.x == pytest.approx(0.5)   # occupies x 0.5-1.0
    assert w.z == pytest.approx(0.0)   # spans the tile's full depth
    assert e.z == pytest.approx(0.0)


def test_placement_matches_talespire_measurements():
    """Reproduce two walls TaleSpire placed itself.

    Ground truth copied out of the game: asset 'Wall Only With Window'
    (0.5 x 2.0 footprint) at rot=0 -> (0.50, 0.00) and rot=270 -> (0.00, 3.50).
    This is the regression guard for the rotation convention.
    """
    ts_wall = Asset(
        id="c" * 8 + "-1111-2222-3333-444444444444", name="Wall Only With Window",
        kind="tile", pack="p", group_tag="wall", tags=(), folder="",
        size_x=0.5, size_y=2.0, size_z=2.0,
    )
    unrotated = place_centered(ts_wall, 0.75, 1.0, 0.0, ROT_N)
    assert (unrotated.x, unrotated.z) == pytest.approx((0.50, 0.00))

    turned = place_centered(ts_wall, 1.0, 3.75, 0.0, ROT_W)
    assert (turned.x, turned.z) == pytest.approx((0.00, 3.50))


def test_rotated_footprint_swaps_axes_on_quarter_turns():
    assert rotated_footprint(WALL, ROT_N) == (1.0, 0.5)
    assert rotated_footprint(WALL, ROT_S) == (1.0, 0.5)
    assert rotated_footprint(WALL, ROT_E) == (0.5, 1.0)
    assert rotated_footprint(WALL, ROT_W) == (0.5, 1.0)


def test_walls_rest_on_the_floor_surface():
    top = place_tile(FLOOR, 0, 0, 0.0).y + FLOOR.size_y
    assert place_wall(WALL, 0, 0, "n", y=top).y == pytest.approx(0.5)


def test_unknown_side_rejected():
    with pytest.raises(ValueError, match="side must be"):
        place_wall(WALL, 0, 0, "up")
