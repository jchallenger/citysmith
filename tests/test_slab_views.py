"""The two pictures the sidecar draws of a written slab.

`slab_svg` is the plan and `slab_axon` is the 3D layer. Both exist for the
same reason: this project's history is largely screenshots read wrong, and a
drawing made from the file itself cannot be read from a misleading angle or
lit by a shadow that looks like a step.

What is asserted here is the part a picture cannot be trusted on -- that the
geometry drawn is the geometry in the file, and that turning the model turns
the model. How it *looks* is not asserted; the SVG is free to change.

The catalog is the conftest stub, not the real one, for the reason stated
there: `catalog.json` is built from a local TaleSpire install and does not
exist in a fresh checkout. That also rules out the hand-built slab fixtures,
which carry real asset UUIDs a stub cannot resolve.
"""

from __future__ import annotations

import re

import pytest

from citysmith import render, slab as slab_mod
from citysmith.build import placed_bounds

from conftest import CIVIC_WALL, FLOOR, GATE, STOOL, StubCatalog


@pytest.fixture
def catalog():
    return StubCatalog()


@pytest.fixture
def cottage():
    """A floor, four wall panels round it, and a stool on top.

    Deliberately mixed: two tile shapes at different heights plus a prop, so
    the drawing has to get an off-grid box, a half-tile plate and a two-tile
    panel all right in one picture.
    """
    return slab_mod.Slab(placements=[
        slab_mod.Placement(asset_id=FLOOR.id, x=0.0, y=0.0, z=0.0),
        slab_mod.Placement(asset_id=FLOOR.id, x=1.0, y=0.0, z=0.0),
        slab_mod.Placement(asset_id=CIVIC_WALL.id, x=0.0, y=0.5, z=0.0),
        slab_mod.Placement(asset_id=CIVIC_WALL.id, x=1.0, y=0.5, z=0.0, rot=6),
        slab_mod.Placement(asset_id=STOOL.id, x=0.4, y=0.5, z=0.6),
    ])


def _polys(svg: str) -> list[str]:
    return re.findall(r'<polygon points="([^"]+)"', svg)


# --------------------------------------------------------------- the plan


def test_the_plan_draws_a_rect_for_every_placement(cottage, catalog):
    svg = render.slab_svg(cottage, catalog)
    # One <rect> is the background; the rest are pieces. The origin mark is a
    # <circle> and the grid is <line>, so neither inflates this count.
    assert svg.count("<rect") == len(cottage.placements) + 1


def test_the_plan_draws_the_footprint_after_rotation(catalog):
    """A rotated 4x0.5 gate must read four cells deep, not four cells wide.

    This is why the drawing goes through `placed_bounds` rather than the
    asset's own `size_x`/`size_z`: a placement coordinate is the min corner of
    the box AFTER rotation, so a viewer that ignores rotation draws every
    quarter-turned piece in the wrong cells. The verge finding -- two pieces
    owning a whole end column -- is only legible because of this.
    """
    flat = slab_mod.Placement(asset_id=GATE.id, x=0.0, y=0.0, z=0.0, rot=0)
    turned = slab_mod.Placement(asset_id=GATE.id, x=0.0, y=0.0, z=0.0, rot=6)
    x0, z0, x1, z1 = placed_bounds(GATE, flat)
    rx0, rz0, rx1, rz1 = placed_bounds(GATE, turned)
    assert (x1 - x0) == pytest.approx(rz1 - rz0)
    assert (z1 - z0) == pytest.approx(rx1 - rx0)


def test_the_plan_survives_a_slab_with_nothing_in_it(catalog):
    assert "empty slab" in render.slab_svg(slab_mod.Slab(placements=[]), catalog)


# ------------------------------------------------------------- the legend


def test_the_legend_counts_every_placement(cottage, catalog):
    rows = render.slab_legend(cottage, catalog)
    assert sum(r["count"] for r in rows) == len(cottage.placements)


def test_the_legend_and_both_pictures_agree_on_colour(cottage, catalog):
    """One legend serves all three views, so the colours cannot drift apart.

    They are assigned by order of first appearance in three separate walks. A
    hash would be cheaper and is deliberately not used, because it gives two
    neighbouring pieces near-identical colours often enough to defeat the
    point of the view. Order is the contract; this keeps the three walks
    honest about it.
    """
    rows = render.slab_legend(cottage, catalog)
    plan = render.slab_svg(cottage, catalog)
    axon = render.slab_axon(cottage, catalog)
    assert rows, "the stub cottage should resolve"
    for row in rows:
        assert row["colour"] in plan
        assert row["colour"] in axon


# ------------------------------------------------------------ the 3D layer


def test_the_3d_layer_draws_three_faces_per_placement(cottage, catalog):
    svg = render.slab_axon(cottage, catalog)
    assert len(_polys(svg)) == len(cottage.placements) * 3


@pytest.mark.parametrize("azimuth", render.AXON_AZIMUTHS)
def test_every_azimuth_draws_the_same_solid(cottage, catalog, azimuth):
    """Turning the model must not add, drop or resize anything.

    This enforces the rule the view exists to serve: a probe read from one
    angle is a probe that lies, so the viewer offers four -- and four views of
    *different* geometry would be worse than one view of the right geometry.
    """
    svg = render.slab_axon(cottage, catalog, azimuth=azimuth)
    assert len(_polys(svg)) == len(cottage.placements) * 3
    assert f"azimuth {azimuth}" in svg


def test_a_quarter_turn_actually_turns_the_model(cottage, catalog):
    """Not merely relabels it.

    A viewer whose compass buttons changed the caption and nothing else would
    pass every other test in this file.
    """
    north = _polys(render.slab_axon(cottage, catalog, azimuth=0))
    east = _polys(render.slab_axon(cottage, catalog, azimuth=90))
    assert north != east


def test_the_3d_layer_reports_the_true_height(cottage, catalog):
    """The height is the whole reason this view exists, so it is stated.

    A wall panel is 2.0 tall seated on a 0.5 floor, so the solid runs 0..2.5
    whatever the prop on top of it does.
    """
    svg = render.slab_axon(cottage, catalog)
    assert "y 0..2.5" in svg
    assert "2.5 tiles tall" in svg


def test_an_azimuth_that_is_not_a_quarter_turn_is_refused(cottage, catalog):
    """Refused rather than rounded.

    A viewer that quietly snapped 37 degrees to 0 would answer a question
    nobody asked while looking like it had answered the one they did -- which
    is the exact shape of every misread probe in this project's history.
    """
    with pytest.raises(ValueError):
        render.slab_axon(cottage, catalog, azimuth=37)


def test_the_3d_layer_survives_a_slab_with_nothing_in_it(catalog):
    assert "empty slab" in render.slab_axon(slab_mod.Slab(placements=[]), catalog)


# ---------------------------------------------------- the spire, hand-built


def test_the_spire_matches_the_slab_the_user_built():
    """Four corner pieces on a 4x4, and no slopes at all.

    A rotation sweep built out of `Tall 2x2x4` SLOPES on one-cell rings failed
    to close on any of the four quarter turns -- four separate peaks with open
    undersides every time. That reads as a scale problem and it is not: a
    spire has no slope in it. The hand-build settled it in one paste, which is
    the third time on this project that asking for the slab a person laid beat
    another sweep.

    The rotations turn out to be `ROOF_CORNER_ROT` plus the kit's own corner
    offset, all four, exactly -- so the table we already had described the
    spire correctly the whole time.
    """
    import pathlib

    import pytest

    from citysmith import slab as slab_mod
    from citysmith.build import ROOF_CORNER_ROT, SPIRE_SIDE

    fixture = pathlib.Path(__file__).parent / "fixtures" / "handbuilt_spire.slab"
    if not fixture.exists():
        pytest.skip("no hand-built spire fixture in this checkout")
    hand = slab_mod.decode(fixture.read_text(encoding="utf-8").strip())

    assert len(hand.placements) == 4, "a spire cap is four pieces"
    assert len({p.asset_id for p in hand.placements}) == 1, "all one piece"

    half = SPIRE_SIDE // 2
    want = {(0, 0): ROOF_CORNER_ROT["nw"], (half, 0): ROOF_CORNER_ROT["ne"],
            (0, half): ROOF_CORNER_ROT["sw"], (half, half): ROOF_CORNER_ROT["se"]}
    got = {(int(p.x), int(p.z)): p.rot for p in hand.placements}
    assert got == want, f"hand-build {got} != table {want}"
