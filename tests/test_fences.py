"""Fences: the geometry, which is the part that can be checked without a board.

`docs/fencing.md` is the design. These assert the invariants it turns on --
that a fence follows its surveyed line, that it stops at the map edge, and that
a rigid panel never straddles a corner.
"""

from __future__ import annotations

import math

import pytest

from citysmith.build import (
    FENCE_MODULE,
    ROOF_EDGE_ROT,
    bearing_rot,
    run_along_polyline,
)
from citysmith.raster import clip_polyline


def test_bearing_matches_the_rotation_table_the_roofs_use():
    """The sign of the rotation is read off the roof table, not guessed.

    Get it backwards and every panel crosses its own fence at twice the
    bearing -- which looks like a data problem and is not.
    """
    assert bearing_rot(1.0, 0.0) == ROOF_EDGE_ROT["e"]
    assert bearing_rot(0.0, 1.0) == ROOF_EDGE_ROT["s"]
    assert bearing_rot(-1.0, 0.0) == ROOF_EDGE_ROT["w"]
    assert bearing_rot(0.0, -1.0) == ROOF_EDGE_ROT["n"]


def test_a_bearing_snaps_to_within_half_a_rotation_step():
    for deg in range(0, 360, 7):
        t = math.radians(deg)
        rot = bearing_rot(math.cos(t), math.sin(t))
        # The piece is symmetric end for end, so 180 degrees out is the same
        # panel; the error that matters is modulo half a turn.
        err = min((deg + rot * 15) % 180, 180 - (deg + rot * 15) % 180)
        assert err <= 7.5 + 1e-9, f"{deg} deg snapped to rot {rot}"


def test_panels_run_the_length_of_a_straight_line():
    panels, joints = run_along_polyline([(0.0, 0.0), (10.0, 0.0)])
    assert len(panels) == 5
    assert {rot for _, _, rot in panels} == {ROOF_EDGE_ROT["e"]}
    spacing = [panels[i + 1][0] - panels[i][0] for i in range(len(panels) - 1)]
    assert all(abs(s - FENCE_MODULE) < 1e-9 for s in spacing)
    # Both ends are joints, and an end counts as a full stop.
    assert joints[0][:2] == (0.0, 0.0)
    assert joints[-1][:2] == (10.0, 0.0)
    assert joints[0][2] == joints[-1][2] == 180.0


def test_no_panel_straddles_a_corner():
    """A rigid 2-tile piece laid across a vertex cuts the corner off.

    Walking the whole polyline by arc length is the simpler implementation and
    this is why it is not the one.
    """
    corner = (6.0, 0.0)
    panels, _ = run_along_polyline([(0.0, 0.0), corner, (6.0, 6.0)])
    half = FENCE_MODULE / 2.0
    for cx, cz, rot in panels:
        t = math.radians(-rot * 15.0)
        ux, uz = math.cos(t) * half, math.sin(t) * half
        ends = ((cx - ux, cz - uz), (cx + ux, cz + uz))
        # Every panel lies on one leg or the other, never spanning the turn.
        on_first = all(abs(z) < 1e-6 for _, z in ends)
        on_second = all(abs(x - corner[0]) < 1e-6 for x, _ in ends)
        assert on_first or on_second, f"panel at {cx},{cz} straddles the corner"


def test_a_vertex_reports_the_angle_it_turns_through():
    _, joints = run_along_polyline([(0.0, 0.0), (6.0, 0.0), (6.0, 6.0)])
    turns = [t for _, _, t in joints]
    assert pytest.approx(90.0, abs=1e-6) == turns[1]


def test_a_segment_shorter_than_half_a_panel_gets_no_panel():
    panels, joints = run_along_polyline([(0.0, 0.0), (0.4, 0.0)])
    assert panels == []
    assert len(joints) == 2       # still bookended, so a post can mark it


def test_clipping_keeps_only_what_is_on_the_board():
    """A quarter of every surveyed fence line lies outside the crop window.

    Everything else in the raster clips by writing into a bounded grid; a run
    of props has no grid to fall off, and an off-map prop drags the bounding
    box every registration check is measured against.
    """
    assert clip_polyline([(-50.0, -50.0), (-40.0, -40.0)], 0, 0, 10, 10) == []
    inside = clip_polyline([(1.0, 1.0), (5.0, 5.0)], 0, 0, 10, 10)
    assert inside == [[(1.0, 1.0), (5.0, 5.0)]]
    crossing = clip_polyline([(-5.0, 5.0), (15.0, 5.0)], 0, 0, 10, 10)
    assert crossing == [[(0.0, 5.0), (10.0, 5.0)]]


def test_a_line_that_leaves_and_returns_comes_back_as_two_runs():
    """Two runs, not one -- or the fence jumps the gap it just left."""
    runs = clip_polyline(
        [(-5.0, 5.0), (5.0, 5.0), (5.0, -5.0), (8.0, -5.0), (8.0, 5.0), (15.0, 5.0)],
        0, 0, 10, 10,
    )
    assert len(runs) == 2
    assert runs[0][0] == (0.0, 5.0)
    assert runs[1][-1] == (10.0, 5.0)


def test_cropping_a_map_reclips_its_fences():
    """A crop cuts through the middle of a run, and the rest needs its own end.

    Filtering by vertex would drop a boundary that crosses the crop without
    having a vertex inside it -- which is most of them.
    """
    from citysmith.layout import Layout, LayoutBuilding
    from citysmith.raster import rasterize

    layout = Layout(name="fenced")
    layout.width = layout.depth = 40.0
    layout.buildings.append(
        LayoutBuilding(id="house-0001", ring=[(2, 2), (6, 2), (6, 6), (2, 6)])
    )
    layout.fences.append([(-20.0, 20.0), (60.0, 20.0)])

    tm = rasterize(layout)
    assert tm.fences and tm.fences[0][0][0] == pytest.approx(0.0)
    assert tm.fences[0][-1][0] == pytest.approx(float(tm.width))

    cropped = tm.crop(10, 10, 12, 20)
    assert cropped.fences, "a run crossing the crop must survive it"
    xs = [x for run in cropped.fences for x, _ in run]
    assert min(xs) == pytest.approx(0.0)
    assert max(xs) == pytest.approx(12.0)
