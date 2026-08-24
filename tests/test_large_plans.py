"""Big buildings get a plan, not a honeycomb.

The BSP that suits a cottage does not scale. Measured over East Tradebourne's
991 buildings before this: rooms per level ran 3.3 under 50 tiles, 5.0 at
50-80, 6.5 at 80-120, 10.8 at 120-200 and **23.5 above 200**. A 29x15
warehouse -- 145 x 75 ft -- came out as 31 rooms of about 15x25 ft, four
purpose names cycled seven times each, and 52 doorways. Every room the same
size as every other, and no way through.
"""

from __future__ import annotations

import collections

import pytest

from citysmith.city import Building, Rect
from citysmith.floorplan import (CORRIDOR, LARGE_TILES, generate, hall_layout,
                                 wants_hall)


def _building(kind="warehouse", w=29, d=15, floors=1, entrance="s") -> Building:
    return Building(id=f"{kind}-0001", name="Mule Depot", kind=kind, district="",
                    rect=Rect(0, 0, w, d), floors=floors, entrance=entrance)


def _plan(**kw):
    return generate(_building(**kw), seed=33)


# -- the layout is a partition ------------------------------------------------

@pytest.mark.parametrize("entrance", ["n", "s", "e", "w"])
@pytest.mark.parametrize("w,d", [(29, 15), (20, 15), (15, 15), (12, 8), (20, 10)])
def test_the_rooms_tile_the_footprint_exactly(entrance, w, d):
    """No gaps and no overlaps. The builder walls *shared edges*, so a gap
    between two rooms is a wall with nothing either side of it, and an overlap
    is two floors in one cell."""
    rect = Rect(0, 0, w, d)
    rects, hall = hall_layout(rect, entrance, 0, 3)
    if not rects:
        pytest.skip("too small for a hall; the BSP handles it")

    seen: collections.Counter = collections.Counter()
    for r in rects:
        for cell in r.tiles():
            seen[cell] += 1
    assert set(seen) == set(rect.tiles()), "the rooms do not cover the footprint"
    assert max(seen.values()) == 1, "two rooms claim the same cell"


@pytest.mark.parametrize("entrance", ["n", "s", "e", "w"])
def test_the_hall_touches_the_wall_the_door_is_in(entrance):
    """The point of a hall is that the door opens onto it and everything else
    opens off it. A hall the door does not reach is just a big room."""
    rect = Rect(0, 0, 29, 15)
    rects, hall = hall_layout(rect, entrance, 0, 3)
    h = rects[hall]
    touching = {
        "n": h.z == rect.z, "s": h.z2 == rect.z2,
        "w": h.x == rect.x, "e": h.x2 == rect.x2,
    }[entrance]
    assert touching, f"the hall does not reach the {entrance} wall: {h}"


@pytest.mark.parametrize("entrance", ["n", "s", "e", "w"])
def test_the_hall_is_a_third_of_the_building_whichever_wall_the_door_is_in(entrance):
    """A 29x15 warehouse entered from its long side was given a 10x10 nave --
    23% of the floor, with 335 tiles of service rooms around it. A warehouse is
    a loading floor with bays at the back."""
    rect = Rect(0, 0, 29, 15)
    rects, hall = hall_layout(rect, entrance, 0, 3)
    areas = sorted((r.area for r in rects), reverse=True)
    assert rects[hall].area == areas[0], "the hall is not the biggest room"
    assert rects[hall].area >= rect.area * 0.3,         f"the hall is {100 * rects[hall].area / rect.area:.0f}% of the floor"


def test_an_upper_level_gets_a_corridor_not_a_hall():
    """The same construction one floor up is a landing with rooms off it --
    which is exactly the shape of an inn's bedroom floor."""
    ground, gi = hall_layout(Rect(0, 0, 29, 15), "s", 0, 3)
    upper, ui = hall_layout(Rect(0, 0, 29, 15), "s", 1, 3)
    assert min(upper[ui].w, upper[ui].d) == CORRIDOR
    assert upper[ui].area < ground[gi].area


# -- what it does to a real plan ----------------------------------------------

def test_a_big_warehouse_is_a_floor_with_rooms_off_it_not_a_honeycomb():
    fp = _plan()
    rooms = fp.rooms_on(0)
    assert len(rooms) <= 14, f"{len(rooms)} rooms on one floor"
    biggest = max(r.rect.area for r in rooms)
    assert biggest >= fp.rect.area * 0.3, "no room is the building's main volume"


def test_the_doors_are_one_per_room_not_fifty(_=None):
    """52 doorways is a maze. One per room off the hall is a building."""
    fp = _plan(kind="temple", w=20, d=15)
    inner = [d for d in fp.doors if not d.exterior and d.level == 0]
    assert len(inner) == len(fp.rooms_on(0)) - 1


def test_no_two_rooms_on_a_level_share_a_name():
    """A name that appears seven times names nothing. The old dealer took
    `purposes[i % len(purposes)]`, so a 31-room warehouse had seven Offices."""
    for kind, w, d in [("warehouse", 29, 15), ("temple", 20, 15),
                       ("guildhall", 20, 10), ("tavern", 12, 9)]:
        fp = generate(_building(kind=kind, w=w, d=d, floors=3), seed=33)
        for level in range(fp.levels):
            names = [r.name for r in fp.rooms_on(level)]
            assert len(names) == len(set(names)), (kind, level, names)


def test_the_principal_room_is_named_once_and_for_the_hall():
    fp = _plan(kind="temple", w=20, d=15)
    rooms = fp.rooms_on(0)
    naves = [r for r in rooms if r.purpose == "nave"]
    assert len(naves) == 1
    assert naves[0].rect.area == max(r.rect.area for r in rooms)


def test_an_apostrophe_survives_the_title_case():
    """`str.title()` gives "Clerk'S Room", which reads as a typo on the GM's
    page."""
    fp = generate(_building(kind="guildhall", w=20, d=14, floors=1), seed=33)
    for room in fp.rooms:
        assert "'S" not in room.name, room.name


# -- and the small case is left alone -----------------------------------------

def test_a_cottage_still_gets_a_bsp():
    """Three to six rooms sharing walls is what a cottage looks like, and the
    hall plan would give it a corridor it has no room for."""
    small = Rect(0, 0, 7, 6)
    assert small.area < LARGE_TILES
    assert not wants_hall("house", small)
    fp = generate(_building(kind="house", w=7, d=6), seed=33)
    assert 2 <= len(fp.rooms_on(0)) <= 6


def test_a_tavern_gets_a_common_room_before_it_is_large():
    """A tavern whose common room is one quarter of four equal rooms is not a
    tavern -- so the hall-kinds do not wait for LARGE_TILES."""
    rect = Rect(0, 0, 9, 7)
    assert rect.area < LARGE_TILES
    assert wants_hall("tavern", rect)
    fp = generate(_building(kind="tavern", w=9, d=7), seed=33)
    common = [r for r in fp.rooms_on(0) if r.purpose == "common room"]
    assert len(common) == 1
    assert common[0].rect.area == max(r.rect.area for r in fp.rooms_on(0))


def test_every_room_is_reachable_on_every_level_of_a_big_plan():
    fp = generate(_building(kind="guildhall", w=20, d=14, floors=3), seed=33)
    for level in range(fp.levels):
        rooms = fp.rooms_on(level)
        joined: dict[str, set[str]] = {r.id: set() for r in rooms}
        for door in fp.doors:
            if door.level != level or door.exterior:
                continue
            here = next((r for r in rooms if r.rect.contains(door.x, door.z)), None)
            if here is None:
                continue
            dx, dz = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}[door.side]
            there = next(
                (r for r in rooms if r.rect.contains(door.x + dx, door.z + dz)), None)
            if there is None or there.id == here.id:
                continue
            joined[here.id].add(there.id)
            joined[there.id].add(here.id)

        seen, queue = {rooms[0].id}, [rooms[0].id]
        while queue:
            for nxt in joined[queue.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        assert seen == {r.id for r in rooms}, f"level {level} has an unreachable room"
