"""Fences: the geometry, which is the part that can be checked without a board.

`docs/fencing.md` is the design. These assert the invariants it turns on --
that a fence follows its surveyed line, that it stops at the map edge, and that
a rigid panel never straddles a corner.
"""

from __future__ import annotations

import math
import pathlib

import pytest

from citysmith.build import (
    FENCE_MODULE,
    FENCE_STYLES,
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


# -- where a boundary is allowed to stand --------------------------------------
#
# `verify` reported ``2 boundary piece(s) stand in a street or lane`` on the
# Sedgewater sample under every fence style, at one coordinate, and nothing was
# standing in a street. The pieces were the palisade the enclosure ring is
# always built from -- the only boundary assets in the medieval palette that
# are ``kind="tile"`` -- and the check was reading their stored coordinate as a
# collider centre. On a prop that is what it is; on a tile it is the min
# corner, so the box came out half a cell low on both axes and straddled the
# four cells meeting at the tile's own corner instead of the one it fills.
#
# That is the failure `build.placed_bounds` was written to name and every other
# placement check in `verify` already goes through. The two boundary checks did
# not, so the tests below pin both halves: the arithmetic, cheaply, and the
# finding itself on the map it was reported from.

SEDGEWATER = pathlib.Path(__file__).resolve().parents[1] / "samples" / "sedgewater.geojson"


def _sedgewater():
    """The sample as `citysmith import --whole-canvas --margin-ft 200` makes it.

    Imported from the committed GeoJSON rather than from `out/`, so the test
    does not depend on a build somebody happened to leave behind. `CLAUDE.md`
    records an hour lost to a stale artifact in `out/` once already.
    """
    from citysmith import importers

    return importers.import_layout(SEDGEWATER, core_only=False,
                                   margin_feet=200.0, name="Sedgewater")


def _ring(cx, cz, rx, rz, n=16):
    """A closed polygon -- the repeated first point is what marks it closed."""
    pts = [(cx + rx * math.cos(2 * math.pi * i / n),
            cz + rz * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


@pytest.fixture(scope="module")
def sedgewater_tilemap():
    from citysmith.raster import rasterize

    return rasterize(_sedgewater(), bridges=True)


@pytest.fixture(scope="module")
def real_catalog():
    from citysmith.catalog import load_or_build

    return load_or_build()


def test_a_tile_boundary_piece_covers_exactly_the_cell_it_fills(real_catalog):
    """The arithmetic, on a map small enough to run every time.

    A palisade piece is laid with `place_tile`, so it fills one cell and its
    collider covers that cell and no other. Read with the stored coordinate
    mistaken for the centre it covers *four* -- which is how a wall two cells
    from a street was reported as standing in it.
    """
    from citysmith import verify as V
    from citysmith.build import build_from_tilemap, covered_cells
    from citysmith.layout import Layout, LayoutBuilding, LayoutRoad
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import rasterize

    layout = Layout(name="pen")
    layout.width = layout.depth = 70.0
    layout.buildings.append(LayoutBuilding(
        id="barracks-0001", ring=[(28.0, 40.0), (38.0, 40.0), (38.0, 49.0),
                                  (28.0, 49.0)], kind="barracks", floors=2))
    layout.roads.append(LayoutRoad(points=[(0.0, 14.0), (70.0, 14.0)], width=4.0))
    layout.fences = [_ring(33, 35, 18, 20)]

    tm = rasterize(layout)
    builder = build_from_tilemap(tm, Palette(real_catalog, MEDIEVAL, 33),
                                 storeys=2, layout=layout)

    tiles = 0
    for p, asset, cx, cz in V._boundary_boxes(builder):
        if asset.kind != "tile":
            continue
        tiles += 1
        assert set(covered_cells(asset, cx, cz, p.rot)) == {
            (math.floor(cx), math.floor(cz))
        }, (f"{asset.name} at ({cx:.2f}, {cz:.2f}) is measured across cells it "
            "does not fill")
    assert tiles, "no tile boundary piece was built, so nothing was tested"


@pytest.mark.parametrize("style", sorted(FENCE_STYLES))
def test_no_boundary_stands_in_a_way_on_sedgewater(style, sedgewater_tilemap,
                                                   real_catalog):
    """The finding itself, on the map that reported it, for every style.

    Parametrised rather than looped because the styles failed differently --
    seven pieces under `palisade`, two under the eight that only use it for
    the enclosure ring -- and a loop reports the first and hides the rest.

    This calls the reporting function, not a reimplementation of it: what has
    to stay empty is the sentence a person reads in the build report.
    """
    from citysmith import verify as V
    from citysmith.build import build_from_tilemap
    from citysmith.palette import Palette

    builder = build_from_tilemap(
        sedgewater_tilemap, Palette.named(real_catalog, "medieval", 0),
        storeys=3, seed=0, fence_style=style, quarters=True,
    )
    assert builder.fence_pieces, f"--fence-style {style} laid no boundary at all"
    assert V._boundaries_do_not_block_a_way(builder, sedgewater_tilemap) == []
    assert V._boundaries_stay_on_the_map(builder, sedgewater_tilemap) == []
