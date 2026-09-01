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
    # What a broken pass looks like from the outside: the run is on the
    # tilemap and `_lay_fences` laid nothing off it. The placements are
    # stripped too, so the fallback path in `_fences_built` -- used for a
    # builder with no recorded count -- sees the same thing.
    fence_ids = {a.id for r in ("field_wall", "field_wall_post")
                 for a in (builder.palette.resolve(r),) if a is not None}
    builder.placements = [p for p in builder.placements
                          if p.asset_id not in fence_ids]
    builder.fence_pieces = 0

    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "field walls")
    assert level == "fail", detail
    assert "nothing was built" in detail


def test_a_boundary_built_in_any_style_counts_as_built():
    """A false FAIL, and it was inside `feature_report` itself.

    `FEATURE_ROLES["field walls"]` listed the drystone and hedge roles by
    hand, so a build run with `--fence-style paling` reported "nothing was
    built from them" while 782 `Wooden Fence` panels stood on the board --
    among them the barricade the map was made for. The list of styles and the
    list of roles to look for were the same fact written twice.

    Every style has to pass, so adding one cannot reintroduce this.
    """
    from citysmith.build import FENCE_STYLES

    layout = _town(fences=[[(2.0, 40.0), (55.0, 44.0)]])
    tm = R.rasterize(layout)
    assert tm.fences

    for style in FENCE_STYLES:
        builder = build_from_tilemap(tm, _palette(), storeys=1,
                                     fence_style=style)
        assert builder.fence_pieces, f"--fence-style {style} laid nothing"
        level, _, detail = next(
            r for r in feature_report(builder, tm, layout)
            if r[1] == "field walls")
        assert level == "pass", f"--fence-style {style}: {detail}"


def test_garden_fences_are_not_mistaken_for_field_walls():
    """The opposite error, and the one the second attempt at this shipped.

    The paling style builds boundaries from `yard_fence`; `_lay_yards` builds
    garden fences from the same role. A check that asks "is a yard_fence on
    this board" answers yes for a town with gardens and no field walls at
    all -- so it has to ask the *pass*, not the asset.
    """
    layout = _town(fences=[[(2.0, 40.0), (55.0, 44.0)]], spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1,
                                 fence_style="paling")
    yard = builder.palette.resolve("yard_fence")
    assert yard is not None
    assert any(p.asset_id == yard.id for p in builder.placements)

    builder.fence_pieces = 0          # the boundary pass laid nothing...
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "field walls")
    assert level == "fail", detail    # ...and the garden fences must not cover for it


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

    # **Ask the pass, not an asset id.** This used to look for `yard_fence`,
    # which was right while every yard on every board was paling. The boundary
    # is dealt per tier now, so a town of houses builds hedges and no paling at
    # all -- and the converse trap is live too, since `field_wall` and
    # `field_hedge` are also what `_lay_fences` builds from. Same lesson as
    # `_fences_built`, arriving one pass over.
    assert builder.yard_pieces, "a reported yard must actually be bounded"


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


def test_the_chimney_line_reports_the_shape_of_the_stack_not_just_a_count():
    """"1,084 stacks" reads as a success; "1,084 stacks of 1 course" does not.

    This line exists because of a measurement. The four-course flue was built,
    tested and written up, and reached 26 of 1,274 stacks across the three
    towns -- 2% -- because it was wired into one branch of `_lay_roofs` and
    nothing counted the others. Every one of East Tradebourne's 1,084 chimneys
    was a single piece. That is this function's own failure mode arriving from
    a direction it did not cover: not a feature absent from the crop, but one
    present in the code and absent from the output.

    So the report has to carry the flue's SHAPE, which is the thing that
    differed. A count alone could not have said anything was wrong.
    """
    layout = _town(count=6)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=2)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "chimneys")
    assert level == "pass", detail
    assert "course(s)" in detail, detail
    assert "piece(s)" in detail, detail


def test_a_town_with_roofs_and_no_chimneys_fails():
    """The other branch, which is the one that would have fired.

    A chimney is not something a map can decline to offer, the way it can
    decline to offer a field boundary: anything with a roof on it has a
    hearth. So an absent stack is a fail rather than a "none here".
    """
    from citysmith import build as B

    layout = _town(count=6)
    tm = R.rasterize(layout)
    lay_flue = B.lay_flue
    B.lay_flue = lambda *a, **k: None
    try:
        builder = build_from_tilemap(tm, _palette(), storeys=2)
    finally:
        B.lay_flue = lay_flue
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "chimneys")
    assert level == "fail", detail
    assert "not one chimney" in detail, detail


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


# -- chunk budget -------------------------------------------------------------


class _Board:
    """Just enough of a TileMap for the size-derived budgets."""

    def __init__(self, width: int, depth: int):
        self.width, self.depth = width, depth


def test_a_dressed_town_keeps_byte_cap_headroom():
    """The budget follows the board, and it has to, in both directions.

    Measured 2026-08-25 after the yard, fence and surface work: East
    Tradebourne's largest slab hit 30,546 of 30,720 bytes at the flat default
    of 9000 -- 99.4%, valid with nothing to spare. The intuitive fix, a smaller
    ``--chunk-tiles``, makes it *worse*: at 96 tiles the build fails outright
    at 31,739 bytes, because a smaller cell leaves more trimmed open-country
    chunks for ``_absorb_open_country`` to fuse back into the kept ones.

    But a single tight number is wrong the other way: at 6000 Graybank went
    from 22 chunks to 37 -- fifteen extra pastes, about ten minutes of driving
    -- to buy headroom a village does not need.
    """
    from citysmith.build import (BUDGET_LARGE_BOARD, BUDGET_SMALL_BOARD,
                                 asset_budget)

    hamlet = asset_budget(_Board(176, 184))       # Pelvesthollow, 32k tiles
    village = asset_budget(_Board(434, 306))      # Graybank, 133k
    town = asset_budget(_Board(739, 598))         # East Tradebourne, 442k

    assert hamlet == BUDGET_SMALL_BOARD
    assert town == BUDGET_LARGE_BOARD
    assert town < village < hamlet, (
        f"the budget must tighten as the board grows: {hamlet}, {village}, {town}"
    )


def test_more_detail_goes_on_a_small_board_and_not_a_large_one():
    """`detail_scale` and `asset_budget` are two halves of one question and
    share their thresholds: how much can this board afford to carry, and how
    finely does it have to be cut to stay under the slab cap while carrying
    it. They must therefore move in opposite directions."""
    from citysmith.build import asset_budget, detail_scale

    small, large = _Board(176, 184), _Board(739, 598)
    assert detail_scale(small) > detail_scale(large)
    assert asset_budget(small) > asset_budget(large)
    assert detail_scale(large) == 1.0, "a large board gets no extra dressing"


# -- the ground sheet ---------------------------------------------------------


def test_no_cell_holds_two_ground_tiles():
    """**Every yard on every board built so far was z-fighting.**

    `_lay_yards` surfaced its cells after `_lay_terrain` had already sheeted
    them in grass, leaving two coplanar 1x1 tiles per cell -- 365 of them on
    Pelvesthollow. TaleSpire does not drop a co-located *tile* the way it drops
    a colliding prop: it keeps both, and the dithering moves with the camera.
    Designed in `docs/district-surfaces.md` 6 and unbuilt for three passes,
    which is why it went unseen.
    """
    from citysmith.verify import _ground_sheet

    layout = _town(spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    assert yard_cells(tm), "the fixture needs yards, which is where this bit"

    doubled = [p for p in _ground_sheet(builder, tm) if "more than one ground tile" in p]
    assert not doubled, doubled


def test_adjacent_ground_tiles_are_flush():
    """Surface tiles align at the *top*: cobble is 0.25 thick and grass 0.5, so
    laid from a common bottom every street sat a quarter tile under the grass
    beside it -- a 15 inch kerb along both sides of every road, on 1,234 tiles.
    That rule has never been checked and now carries nine materials."""
    from citysmith.verify import _ground_sheet

    layout = _town(spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    steps = [p for p in _ground_sheet(builder, tm) if "top height" in p]
    assert not steps, steps


def test_a_second_ground_tile_in_one_cell_is_caught():
    """The check has to actually fire, or it is decoration."""
    from citysmith.verify import _ground_sheet

    layout = _town(spacing=12)
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    assert not [p for p in _ground_sheet(builder, tm) if "more than one" in p]

    # Lay a second ground tile squarely on top of an existing one.
    ground = builder.palette.require("ground")
    first = next(p for p in builder.placements if p.asset_id == ground.id)
    builder.add(type(first)(asset_id=ground.id, x=first.x, y=first.y,
                            z=first.z, rot=first.rot))
    caught = [p for p in _ground_sheet(builder, tm) if "more than one" in p]
    assert caught, "a duplicated ground tile must be reported"


def test_a_wall_taller_than_its_course_is_sunk_not_floated():
    """`shells_rest_on_their_floors` allows the panel's own excess, one way.

    `Tavern Wall 01` is 2.03 tall where every other panel in the medieval set
    is 2.00, so seating its head on the course line puts its base 0.03 low --
    27 buildings on Forest Church and 614 on East Tradebourne, every one that
    piece. The head is the end that must be right, because the roof seats at
    `floors * storey_h`; the excess is absorbed at the base where the floor
    hides it rather than left proud of the eaves, which is the end that shows.

    So the allowance is the panel's excess over a whole course and nothing
    more, and it is ONE-SIDED. This check exists to catch two pastes
    disagreeing about height -- a whole course -- and it was firing on 1.8
    inches.
    """
    from citysmith.verify import shells_rest_on_their_floors

    class _A:
        kind = "tile"
        off_x = off_z = 0.0
        def __init__(self, i, y):
            self.id, self.size_y = i, y
            self.size_x = self.size_z = 1.0

    FLOOR, ODD, PLAIN = _A("f", 0.5), _A("w", 2.03), _A("p", 2.0)

    BYID = {a.id: a for a in (FLOOR, ODD, PLAIN)}

    class _Pl:
        def __init__(self, aid, y):
            self.asset_id, self.x, self.y, self.z, self.rot = aid, 0.0, y, 0.0, 0

    class _B:
        byid = BYID
        prop_ids = frozenset()
        def __init__(self, wall, y):
            self.placements = [_Pl("f", 0.0), _Pl(wall.id, y)]
            self.layer_of = ["landscape", "structure"]
            self.group_of = [None, "house-0001"]

    class _TM:
        width = depth = 1

    # Sunk by exactly its own excess: allowed.
    assert shells_rest_on_their_floors(_B(ODD, 0.47), _TM()) == []
    # A plain 2.0 panel has no excess, so it must sit exactly on the floor.
    assert shells_rest_on_their_floors(_B(PLAIN, 0.5), _TM()) == []
    assert shells_rest_on_their_floors(_B(PLAIN, 0.47), _TM()) != []
    # Floating is never allowed, however odd the panel.
    assert shells_rest_on_their_floors(_B(ODD, 0.53), _TM()) != []
    # And a whole course out is still the thing this check is for.
    assert shells_rest_on_their_floors(_B(ODD, 0.0), _TM()) != []
