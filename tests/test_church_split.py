"""Splitting an imported temple into a nave and a chancel.

The footprints come from MFCG and FTG as real polygons; citysmith does not
invent them. Every hand-authored plan tried here was 176-312 cells against a
real range of 30-102, so a template would mean scaling a rectangle onto
someone else's outline. Subdividing keeps the outline.

What is asserted is the part that failed twice before it worked: that the
parts are joined rather than sealed, that the subordinate has no street door
of its own, and that the tower survives the split.
"""

from __future__ import annotations

import pytest

from citysmith import raster as R
from citysmith.build import (
    SPIRE_SIDE, TOWER_ASPECT_EXEMPT_TILES, TOWER_MIN_ASPECT, TOWER_MIN_TILES,
    lay_spire, pick_towers, storeys_of, subordinate_courses,
)


def _temple(w: int, d: int, *, street: bool = True) -> R.TileMap:
    """One rectangular temple with a street along its north edge."""
    tm = R.TileMap.blank(w + 8, d + 10)
    for z in range(tm.depth):
        for x in range(tm.width):
            tm.surface[z][x] = R.GROUND
    if street:
        for x in range(tm.width):
            tm.surface[2][x] = R.STREET
    for x in range(4, 4 + w):
        for z in range(4, 4 + d):
            tm.building[z][x] = "temple-0001"
            tm.surface[z][x] = R.FLOOR
    tm.floors["temple-0001"] = 1
    return tm


def test_a_big_enough_temple_is_split_into_two_parts():
    tm = _temple(7, 15)                       # 105 cells
    assert R.split_churches(tm) == 1
    assert tm.church_parts["temple-0001"] == ("temple-0001", "nave")
    assert tm.church_parts["temple-0001+chancel"] == ("temple-0001", "chancel")


def test_a_small_temple_is_left_whole_and_says_so():
    """Below the threshold the parts are a room and a cupboard, so it is left
    as one box. Two of the seven real temples fall here; that is reported by
    `feature_report`, not fixed by lowering the bar."""
    tm = _temple(4, 7)                        # 28 cells
    assert R.split_churches(tm) == 0
    assert tm.church_parts == {}


def test_the_chancel_goes_at_the_end_away_from_the_street():
    """A church is entered from the public side and the altar is at the far
    one. Measured against the street rather than the door, because doors are
    placed after this runs -- keying on them would be circular."""
    tm = _temple(7, 15)                       # street along the north
    R.split_churches(tm)
    chancel = [(x, z) for z in range(tm.depth) for x in range(tm.width)
               if tm.building[z][x] == "temple-0001+chancel"]
    nave = [(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.building[z][x] == "temple-0001"]
    assert min(z for _x, z in chancel) > max(z for _x, z in nave) - 1


def test_the_parts_are_joined_not_sealed():
    """The wall between two parts of one complex is never built, and that
    omission IS the chancel arch.

    A five-volume church whose parts each closed their own ring came out as
    five sealed rooms: 8 nave cells abutting the crossing and not one door
    among them. This is the assertion that would fail if `_find_perimeters`
    went back to comparing raw ids.
    """
    tm = _temple(7, 15)
    R.split_churches(tm)
    R._find_perimeters(tm, None)
    nave = {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.building[z][x] == "temple-0001"}
    chancel = {(x, z) for z in range(tm.depth) for x in range(tm.width)
               if tm.building[z][x] == "temple-0001+chancel"}
    # No perimeter edge of the nave may face a chancel cell.
    for x, z, side in tm.perimeter["temple-0001"]:
        dx, dz = next((d, e) for s, d, e in R.SIDES if s == side)
        assert (x + dx, z + dz) not in chancel, \
            "a wall was built between the nave and the chancel"


def test_a_subordinate_part_has_no_street_door():
    """You enter a church through its nave. A chancel with its own street door
    is a shed that happens to touch a church."""
    tm = _temple(7, 15)
    R.split_churches(tm)
    R._find_perimeters(tm, None)
    R._place_doors(tm, None)
    assert tm.doors.get("temple-0001")
    assert not tm.doors.get("temple-0001+chancel")


def test_the_chancel_stands_one_course_below_the_nave():
    tm = _temple(7, 15)
    R.split_churches(tm)
    nave = storeys_of(tm, "temple-0001", 3)
    assert storeys_of(tm, "temple-0001+chancel", 3) == \
        subordinate_courses("chancel", nave)


def test_a_crop_carries_the_church_roles():
    """`crop` builds a fresh TileMap field by field, so anything not copied is
    silently lost -- and losing the roles rebuilds the church as one box with
    the chancel sealed and given its own street door. Found by the churches
    line in `feature_report` on its first day."""
    tm = _temple(7, 15)
    R.split_churches(tm)
    out = tm.crop(0, 0, tm.width, tm.depth)
    assert out.church_parts.get("temple-0001+chancel") == \
        ("temple-0001", "chancel")


# ----------------------------------------------------------------- the tower


def test_a_large_compact_civic_building_gets_a_tower():
    """The aspect gate reads compactness as "not a nave", and a compact plan
    at 80+ cells is a nave WITH AISLES -- precisely the church that has a
    tower. East Tradebourne's two largest temples were towerless while a
    65-cell one was the landmark.
    """
    tm = _temple(10, 9)                       # 90 cells, aspect 1.11
    assert 90 >= TOWER_ASPECT_EXEMPT_TILES
    towers = pick_towers(tm, 3)
    assert towers, "a 90-cell compact temple should carry a tower"
    assert set(towers.values()) == {"temple-0001"}


def test_a_small_compact_building_still_gets_no_tower():
    """The waiver is for great buildings, not a repeal. Below the exemption
    the aspect rule still refuses a squat plan."""
    tm = _temple(8, 8)                         # 64 cells, aspect 1.0
    assert TOWER_MIN_TILES <= 64 < TOWER_ASPECT_EXEMPT_TILES
    assert not pick_towers(tm, 3)


def test_splitting_a_chancel_does_not_cost_the_nave_its_tower():
    """Every improvement to a church shortens its nave, and the aspect gate
    punished exactly that: the split took Forest Church's temple from aspect
    3.17 to 2.33 and deleted its tower."""
    tm = _temple(7, 15)
    before = set(pick_towers(tm, 3).values())
    R.split_churches(tm)
    after = set(pick_towers(tm, 3).values())
    assert before and after, "the tower survived neither side of the split"
    assert "temple-0001" in after


# ----------------------------------------------------------------- the spire


def test_a_spire_never_hangs_over_a_gap(catalog_palette):
    """`xs`/`zs` are DISTINCT coordinates, not a contiguous range, and
    `_lay_towers` merges a building's tower cells into one set -- so a
    west-front pair of towers would centre the cap in the hole between them,
    hovering over the nave. Refused rather than placed.
    """
    from citysmith.build import Builder

    b = Builder(catalog_palette)
    ring = {(x, z) for x in range(6) for z in range(6)} - {
        (x, z) for x in range(2, 4) for z in range(2, 4)}
    assert lay_spire(b, ring, 0.0, "civic", "temple-0001") == 0
    assert not b.placements


def test_a_spire_needs_room_for_its_cap(catalog_palette):
    from citysmith.build import Builder

    b = Builder(catalog_palette)
    small = {(x, z) for x in range(SPIRE_SIDE - 1)
             for z in range(SPIRE_SIDE - 1)}
    assert lay_spire(b, small, 0.0, "civic", "temple-0001") == 0
