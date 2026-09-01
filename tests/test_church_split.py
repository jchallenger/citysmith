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


# ------------------------------------------------------------- roof valleys


def test_a_roof_does_not_eave_into_a_taller_wall():
    """A side running into masonry is not an eave.

    Left as a frontier, the ring flood starts an eaves course against the
    taller wall, so the slope turns over and sheds *towards* it -- and its
    underside, plus the ridge pieces that had nothing to lap onto, stand proud
    of the masonry. That is what every junction in the five-volume church
    looked like from overhead.

    Asserted on the rings rather than on placements, because that is where the
    decision is made and the pieces only follow it.
    """
    from citysmith.build import _roof_rings

    lower = {(x, 1) for x in range(5)} | {(x, 2) for x in range(5)}
    taller = frozenset((x, 0) for x in range(5))

    open_side = _roof_rings(lower)
    # With nothing to abut, the row against the wall is an eaves course.
    assert all(open_side[(x, 1)] == 0 for x in range(5))

    against = _roof_rings(lower, taller)
    # Abutting the taller block, that row is no longer the outside of the roof:
    # at least one cell on it has climbed off the eaves course.
    assert any(against[(x, 1)] > 0 for x in range(5)), \
        "the roof still eaves into the wall it runs into"


def test_the_valley_is_not_a_church_special_case():
    """A block is a set of cells at ONE storey count, so a two-storey house
    beside a one-storey one is the same shape as a nave beside a chancel. The
    argument is "against a taller thing", not "against a sibling"."""
    from citysmith.build import _roof_rings

    cottage = {(x, z) for x in range(3) for z in range(3)}
    assert _roof_rings(cottage) == _roof_rings(cottage, frozenset())

    # A taller neighbour along the north edge. The middle of that row stops
    # being an eaves cell, because it no longer has an outside to shed to.
    neighbour = frozenset((x, -1) for x in range(3))
    walled = _roof_rings(cottage, neighbour)
    assert _roof_rings(cottage)[(1, 0)] == 0
    assert walled[(1, 0)] > 0, "the cottage still eaves into its neighbour"


def test_placement_cells_does_not_count_the_cell_a_box_merely_touches():
    """A box ending at x=6.0 does not occupy cell 6; it ends on its near edge.

    This function exists because doing it by hand invented a defect. Measuring
    "roof pieces overhanging a taller neighbour" with `range(int(x0),
    int(x1) + 1)` counted every cell a box touches, reported 28 overhangs
    across four buildings, and had a task filed against it. With the half-open
    range the answer is zero: all 28 were the off-by-one.
    """
    from citysmith import slab as slab_mod
    from citysmith.build import placement_cells

    from conftest import FLOOR, GATE

    one = slab_mod.Placement(asset_id=FLOOR.id, x=5.0, y=0.0, z=5.0)
    assert placement_cells(FLOOR, one) == {(5, 5)}

    # A 4x0.5 gate laid flat spans four cells in x and one in z -- not five
    # and two.
    wide = slab_mod.Placement(asset_id=GATE.id, x=5.0, y=0.0, z=5.0)
    cells = placement_cells(GATE, wide)
    assert len({c[0] for c in cells}) == 4
    assert len({c[1] for c in cells}) == 1


def test_no_roof_piece_occupies_a_taller_neighbours_cell():
    """A roof piece standing inside the wall next door.

    Currently zero on all four plans, and this is the guard that keeps it so
    -- the property is easy to break and was, for a while, believed broken.
    """
    _assert_roofs_are_sound(overhang=True)


def test_every_building_cell_is_roofed():
    """The failure that is worse than an overlap: a hole shows as sky.

    Any fix for an overhang that works by *dropping* a piece trades a seam for
    a hole, so the two have to be asserted together or the cure ships as the
    disease.
    """
    _assert_roofs_are_sound(overhang=False)


def _assert_roofs_are_sound(*, overhang: bool) -> None:
    import contextlib
    import io
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
    try:
        import church_plans as CP
    except Exception:                       # pragma: no cover -- no catalog
        pytest.skip("church_plans needs the local catalog")

    from citysmith.build import placement_cells, footprints, pick_towers, storeys_of

    for plan in sorted(CP.PLANS):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tm, b, _blurb, _parts, _piers, _sp = CP.build(plan, seed=33, storeys=3)
        byid = {a.id: a for a in b.palette.catalog.assets}
        plan_cells = footprints(tm)
        heads = {bid: 0.5 + storeys_of(tm, bid, 3) * 2.0 for bid in plan_cells}
        owner = {c: bid for bid, cs in plan_cells.items() for c in cs}

        roofed: set[tuple[int, int]] = set()
        intruding = []
        for p in b.placements:
            asset = byid.get(p.asset_id)
            if asset is None:
                continue
            label = f"{asset.group_tag or ''} {asset.name}".lower()
            if "roof" not in label and "chimney" not in label:
                continue
            cells = placement_cells(asset, p)
            roofed |= cells
            home = owner.get((int(p.x), int(p.z)))
            for c in cells:
                nb = owner.get(c)
                if nb and nb != home and p.y + 0.01 < heads[nb] - 0.01:
                    intruding.append((plan, asset.name, c))
                    break

        if overhang:
            assert not intruding, f"{plan}: roof inside a taller wall: {intruding[:3]}"
        else:
            bare = ({c for cs in plan_cells.values() for c in cs}
                    - roofed - set(pick_towers(tm, 3)))
            assert not bare, f"{plan}: {len(bare)} unroofed cell(s): {sorted(bare)[:5]}"


# ---------------------------------------------------------- the tower design


def test_a_church_tower_is_square():
    """A tower is square; a full-width end strip is a westwork.

    The carve used to take every cell within `side` of the end -- 8x4 on East
    Tradebourne's two largest churches -- while its own comment claimed a
    set-back. The photograph that started the design pass shows what an 8x4
    top costs: a 4x4 spire covering half of it and the rest hipped in
    terracotta.
    """
    tm = _temple(8, 15)
    towers = pick_towers(tm, 3)
    cells = {c for c, b in towers.items() if b == "temple-0001"}
    assert cells, "an 8x15 temple should carry a tower"
    xs = {c[0] for c in cells}
    zs = {c[1] for c in cells}
    assert len(xs) == len(zs), f"tower is {len(xs)}x{len(zs)}, not square"
    # And set back: nave wall shows past the tower on both flanks.
    nave_x = {x for x in range(tm.width) for z in range(tm.depth)
              if tm.building[z][x] == "temple-0001"}
    assert min(xs) > min(nave_x) and max(xs) < max(nave_x), \
        "the tower is flush with the nave's flanks -- a westwork again"


def test_a_church_tower_top_is_one_kit():
    """No roof-mix piece above the parapet: spire, crenellation, pavement.

    The margin between spire and parapet used to be hipped in whatever roof
    the building was dealt -- red terracotta scraps wedged against a dark
    stone spire, on the most visible surface the church has.
    """
    from citysmith.build import build_from_tilemap

    import pathlib
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    byid = {a.id: a for a in palette.catalog.assets}

    tm = _temple(8, 15)
    R.split_churches(tm)
    R._find_perimeters(tm, None)
    R._place_doors(tm, None)
    b = build_from_tilemap(tm, palette, storeys=3)

    towers = pick_towers(tm, 3)
    cells = {c for c, bid in towers.items() if bid == "temple-0001"}
    assert cells
    crown_min = 0.5 + (storeys_of(tm, "temple-0001", 3) + 1) * 2.0
    offenders = [byid[p.asset_id].name for p in b.placements
                 if (int(p.x), int(p.z)) in cells and p.y >= crown_min
                 and byid.get(p.asset_id) is not None
                 and "roof" in (byid[p.asset_id].group_tag
                                or byid[p.asset_id].name).lower()
                 and byid[p.asset_id].folder in ("Tavern", "Rural",
                                                 "Abandoned Village")]
    assert not offenders, f"roof-mix pieces on the tower top: {offenders[:4]}"


def test_the_belfry_is_the_only_glazed_tower_stage():
    """Blind through the middle, open at the top, where the bells are.

    Every added stage used to be plain wall, which stacked with the shell's
    glazed base into an even band-on-band elevation -- the close-up reads as
    an office block. One change of texture at the head is what breaks it.
    """
    from citysmith.build import TOWER_EXTRA_STOREYS, build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    byid = {a.id: a for a in palette.catalog.assets}

    tm = _temple(8, 15)
    R.split_churches(tm)
    R._find_perimeters(tm, None)
    R._place_doors(tm, None)
    b = build_from_tilemap(tm, palette, storeys=3)

    cells = {c for c, bid in pick_towers(tm, 3).items() if bid == "temple-0001"}
    # Stage heights read off the built walls rather than predicted:
    # `_lay_towers` starts at `max(storeys_at(...))`, which `building_ranges`
    # can move, and the first draft predicted heights and read nothing.
    shell_head = 0.5 + storeys_of(tm, "temple-0001", 3) * 2.0
    wall_y = sorted({round(p.y, 1) for p in b.placements
                     if (int(p.x), int(p.z)) in cells
                     and p.y >= shell_head - 0.01
                     and byid.get(p.asset_id) is not None
                     and "wall" in (byid[p.asset_id].group_tag
                                    or byid[p.asset_id].name).lower()})
    assert len(wall_y) >= TOWER_EXTRA_STOREYS - 1, f"no tower stages: {wall_y}"
    top_y = wall_y[-1]
    glazed = {round(p.y, 1) for p in b.placements
              if (int(p.x), int(p.z)) in cells
              and round(p.y, 1) in wall_y
              and byid.get(p.asset_id) is not None
              and "window" in byid[p.asset_id].name.lower()}
    assert glazed == {round(top_y, 1)}, \
        f"tower stages glazed at {sorted(glazed)}, wanted only {top_y}"
