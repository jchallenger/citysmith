"""Did this build actually contain the features it was supposed to?

**This file exists because a feature was built, shipped, reviewed and written
up while being absent from every board looked at.** Fences worked; both crops
chosen to review them were dense town centre, where a field boundary does not
go. Twenty-two runs on the map, zero in the frame, and nothing in the build
report said so.

Every other check in `verify` asks whether the geometry is *correct*.
`feature_report` asks whether it is *there*, by comparing what the input
offered against what the output used -- which is the only way to tell an absent
feature from an inapplicable one.
"""

from __future__ import annotations

import pytest

from citysmith import raster as R
from citysmith.build import build_from_tilemap, yard_cells
from citysmith.catalog import load_or_build
from citysmith.layout import Layout, LayoutBuilding
from citysmith.palette import MEDIEVAL, Palette
from citysmith.verify import feature_report


def _palette():
    return Palette(load_or_build(), MEDIEVAL)


def _town(*, fences=(), spacing=8, count=4, width=60, depth=60):
    """A tiny town: `count` buildings in a row, `spacing` cells apart."""
    layout = Layout(name="probe")
    layout.width, layout.depth = float(width), float(depth)
    for i in range(count):
        x = 6 + i * spacing
        layout.buildings.append(LayoutBuilding(
            id=f"house-{i + 1:04d}",
            ring=[(x, 10), (x + 5, 10), (x + 5, 15), (x, 15)],
            kind="house", floors=1,
        ))
    layout.fences = [list(line) for line in fences]
    return layout


def _levels(report):
    return {name: (level, detail) for level, name, detail in report}


def test_a_feature_present_in_the_source_and_absent_from_the_build_fails():
    """The whole point. If the map has boundaries and the build laid none,
    something is broken or switched off, and it must be loud."""
    layout = _town(fences=[[(2.0, 40.0), (55.0, 44.0)]])
    tm = R.rasterize(layout)
    assert tm.fences, "the fixture needs a fence run to be meaningful"

    builder = build_from_tilemap(tm, _palette(), storeys=1)
    # Strip every fence placement, which is what a broken pass looks like from
    # the outside: the run is on the tilemap and nothing was built from it.
    fence_ids = {a.id for r in ("field_wall", "field_wall_post")
                 for a in (builder.palette.resolve(r),) if a is not None}
    keep = [i for i, p in enumerate(builder.placements)
            if p.asset_id not in fence_ids]
    builder.placements = [builder.placements[i] for i in keep]

    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "field walls")
    assert level == "fail", detail
    assert "nothing was built" in detail


def test_a_feature_absent_because_the_crop_has_none_does_not_fail():
    """A city-centre crop has no field boundaries in it, and that is a fact
    about the map rather than a defect. It still has to be *said* -- this is
    the exact case that went unnoticed."""
    layout = _town(fences=[[(2.0, 55.0), (20.0, 58.0)]])
    whole = R.rasterize(layout)
    assert whole.fences

    # Crop away from the boundary entirely.
    tm = whole.crop(0, 0, 40, 30)
    assert not tm.fences

    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "field walls")
    assert level == "pass"
    assert "outside this crop" in detail, detail
    assert "1" in detail, "the report must say how many the layout does have"


def test_a_feature_with_nothing_in_the_source_says_so_plainly():
    layout = _town()
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "field walls")
    assert level == "pass"
    assert "none in the source" in detail


def test_yards_appear_when_buildings_stand_apart_and_not_when_they_do_not():
    """The gate, which took three attempts: no gate gave every building in a
    991-building city a gravel apron, and local built density did not
    discriminate at all."""
    apart = R.rasterize(_town(spacing=12))
    tight = R.rasterize(_town(spacing=6))
    assert yard_cells(apart), "buildings 12 cells apart should each own a yard"
    assert len(yard_cells(tight)) < len(yard_cells(apart)), \
        "buildings packed together should not each claim a yard"


def test_a_yard_is_surfaced_and_bounded_when_it_is_reported():
    layout = _town(spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)

    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "yards")
    assert level == "pass", detail

    fence = builder.palette.resolve("yard_fence")
    assert fence is not None
    assert any(p.asset_id == fence.id for p in builder.placements), \
        "a reported yard must actually be fenced"


def test_a_town_whose_trades_do_not_cluster_reports_no_quarters():
    """`None` from `quarter_map` is the correct answer on a village, and the
    report has to say that rather than going quiet."""
    layout = _town(count=6)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "quarters")
    assert level == "pass"
    assert "no quarters" in detail


def test_the_surface_palette_is_no_longer_two_materials():
    """A town used to arrive as cobble, gravel and grass, with `lane`,
    `gravel` and `field_1x1` all the same asset and `plaza` resolving to
    nothing."""
    layout = _town(spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "surfaces")
    assert level == "pass", detail


def test_every_building_the_same_height_is_reported_as_a_warning():
    """Every town used to come out ~33/33/33 with a mean of 2.0 whatever its
    size. A build where nothing varies is not necessarily wrong -- a hamlet of
    cottages is uniform on purpose -- but it must not pass silently."""
    layout = _town(count=4)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "storeys")
    assert level == "warn"
    assert "same height" in detail


# -- interior furnishing ------------------------------------------------------


def _furnished(kind: str = "tavern", w: int = 16, d: int = 12, floors: int = 2):
    """A building big enough to have several rooms, planned and furnished.

    Synthetic rather than loaded from `out/`: a test that skips when a local
    artifact is missing is a test that quietly stops guarding anything, and the
    door-clearance rule is one nobody should be able to break unnoticed.
    """
    from citysmith import interior as I
    from citysmith.build import build_interior

    layout = Layout(name="probe")
    layout.width, layout.depth = 60.0, 60.0
    building = LayoutBuilding(
        id=f"{kind}-0001",
        ring=[(6, 6), (6 + w, 6), (6 + w, 6 + d), (6, 6 + d)],
        kind=kind, floors=floors,
    )
    layout.buildings.append(building)
    fp = I.plan(layout, building, seed=0)
    return fp, build_interior(fp, _palette(), seed=0)


def _prop_cells(builder):
    from citysmith.build import collider_offset

    cat = builder.palette.catalog
    for p in builder.placements:
        asset = cat.by_id(p.asset_id)
        if asset is None or asset.kind != "prop":
            continue
        ox, oz = collider_offset(asset, p.rot)
        yield p, asset, (p.x + ox, p.z + oz)


def test_nothing_is_furnished_into_a_doorway_or_a_stair():
    """A slab has no physics, so a chair dropped in a door is a door that does
    not open -- for the whole session, with nobody able to move it."""
    from citysmith.build import _door_keepout

    fp, builder = _furnished()
    keepout = set()
    for level in range(4):
        keepout |= _door_keepout(fp, level)
    assert keepout, "the fixture needs doors to be meaningful"

    blocking = [(a.name, cx, cz) for _, a, (cx, cz) in _prop_cells(builder)
                if (int(cx), int(cz)) in keepout]
    assert not blocking, f"props standing in a doorway or on a stair: {blocking[:5]}"


def test_furniture_does_not_sit_on_cell_centres():
    """0.1% of hand-placed interior props are centred; this pass centred 100%
    of them, which is most of why furniture read as debris in a grid."""
    fp, builder = _furnished()
    cells = list(_prop_cells(builder))
    if not cells:
        pytest.skip("nothing furnished")
    centred = sum(1 for _, _, (cx, cz) in cells
                  if abs(cx % 1 - 0.5) < 1e-6 and abs(cz % 1 - 0.5) < 1e-6)
    assert centred / len(cells) < 0.05, f"{centred} of {len(cells)} props are centred"


def test_most_furniture_is_on_a_quarter_turn():
    """84% in the community slabs, against a uniform draw over all 24 steps."""
    from citysmith.build import QUARTER_TURN_SHARE

    fp, builder = _furnished()
    rots = [p.rot for p, _, _ in _prop_cells(builder)]
    if len(rots) < 20:
        pytest.skip("too few props to measure a share")
    quarter = sum(1 for r in rots if r % 6 == 0) / len(rots)
    assert abs(quarter - QUARTER_TURN_SHARE) < 0.15, (
        f"{quarter:.0%} on a quarter turn against a target of "
        f"{QUARTER_TURN_SHARE:.0%}")


def test_furniture_stands_inside_the_room_not_in_the_wall():
    """Nothing may reach into a wall band.

    An interior wall sits on the *cell's own edge*, not between cells, so a
    prop on a room's first row has masonry at its own boundary. The first
    version of the set-back pushed it *toward* that boundary on all four sides
    and buried most of the furniture in the walls.

    "Not on a cell centre" passed throughout that, because a piece 0.32 into
    the wall is exactly as far off-centre as one 0.32 into the room. The
    invariant that bites is the prop's **box against the wall's**: a wall is
    0.5 thick, so the clear floor is the room rect inset by that.

    **Worth knowing what this does and does not catch.** Re-introducing the
    inverted sign alone no longer fails it, because `_fit` clamps every piece
    into the clear floor whichever way the offset pointed -- the invariant is
    structural now, not a consequence of getting a sign right, which is the
    stronger arrangement. What it catches is any placement that escapes that
    clamp: a new wall kit thicker than the cell inset, a prop positioned
    outside `_dress`, or a change to the clamp itself. It also caught the
    second bug on the way in -- a wide piece set back from the *north* wall
    while standing in the first column still reached into the *west* one.
    """
    from citysmith.build import rotated_footprint

    fp, builder = _furnished()
    partition = builder.palette.resolve("wall_interior")
    thick = min(partition.size_x, partition.size_z)

    buried = []
    for p, asset, (cx, cz) in _prop_cells(builder):
        room = next((r for r in fp.rooms
                     if r.rect.x <= cx <= r.rect.x2 and r.rect.z <= cz <= r.rect.z2),
                    None)
        if room is None:
            continue
        r = room.rect
        sx, sz = rotated_footprint(asset, p.rot)
        # A room only one or two cells across has no clear floor once both
        # walls are taken off it; nothing can satisfy the rule there.
        if r.w >= 3 and (cx - sx / 2 < r.x + thick - 1e-6
                         or cx + sx / 2 > r.x2 - thick + 1e-6):
            buried.append((asset.name, "x", round(cx, 2), (r.x, r.x2)))
        elif r.d >= 3 and (cz - sz / 2 < r.z + thick - 1e-6
                           or cz + sz / 2 > r.z2 - thick + 1e-6):
            buried.append((asset.name, "z", round(cz, 2), (r.z, r.z2)))
    assert not buried, (
        f"{len(buried)} prop(s) reaching into a wall band: {buried[:6]}")
