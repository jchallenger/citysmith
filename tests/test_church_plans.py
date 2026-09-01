"""The church plan proposals, and the guard that they are what they say.

The first round of these shipped a plan captioned "Aisled parish church" with
no aisles in it, and a "cruciform" whose transepts sat mid-nave with the
chancel 35 ft further on -- so there was no crossing, and both claims in the
caption were false in the geometry. Three reviewers caught it; nothing in the
suite did.

A probe whose label promises what its geometry does not contain is the same
failure as a count that is not a shape: a reviewer reads the caption, sees a
long church, and confirms a thing that was never built. These are the cheap
guards against the next one.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from church_plans import PIER_SPACING, PLANS, check  # noqa: E402

from citysmith.build import (  # noqa: E402
    SUBORDINATE_MIN_COURSES, church_band, subordinate_courses,
)


def _roles(parts):
    return {r if r == "nave" else r.split("_")[-1] for r, *_ in parts}


def test_every_church_plan_builds_what_its_name_says():
    """The caption is a claim about the geometry, so it is checked."""
    assert check() == 0


@pytest.mark.parametrize("name", sorted(PLANS))
def test_every_plan_has_exactly_one_nave(name):
    """`subordinate_courses` bands every other part against the nave, so a
    plan with two naves or none has nothing to measure from."""
    assert [r for r, *_ in PLANS[name][1]].count("nave") == 1


@pytest.mark.parametrize("name", sorted(PLANS))
def test_every_plan_terminates_in_a_chancel(name):
    """A hall church without one is a box with a lobby, and a minster's church
    arm without one is just the longest range. Two of the four had none."""
    assert "chancel" in _roles(PLANS[name][1])


@pytest.mark.parametrize("name", sorted(PLANS))
def test_no_nave_is_a_corridor(name):
    """6 cells is 30 ft: a shortbow covers it end to end from turn one and a
    20 ft fireball spans it wall to wall. The arcade needs a cell of aisle
    each side plus a nave between, so eight is the floor."""
    nave = next(p for p in PLANS[name][1] if p[0] == "nave")
    assert nave[3] >= 8, f"{name}: nave {nave[3]} cells wide is a corridor"


def test_the_arcade_leaves_an_aisle_on_each_side():
    """Piers one cell in from each long wall. At spacing 0 there is no arcade
    at all, which would quietly make every 'aisled' caption false again."""
    assert PIER_SPACING >= 2


def test_a_transept_is_the_naves_own_height():
    """Three equal gables meeting is what makes a cruciform read as cruciform.
    At two courses the arms are lean-tos that vanish from every view but
    overhead -- which is what banding by footprint area produced."""
    assert subordinate_courses("transept", 5) == 5
    assert subordinate_courses("crossing", 5) == 5


def test_a_chancel_steps_down_one_course_not_three():
    """A chancel is small in plan and TALL in section, because it is the most
    important space in the building. Banding it on its own area put a 24-cell
    chancel in the `chapel` band and drew a 30 ft step where 10 is right."""
    assert subordinate_courses("chancel", 5) == 4
    assert subordinate_courses("chancel", 3) == 2


def test_no_subordinate_is_ever_a_crawlspace():
    for role in ("chancel", "aisle", "vestry", "porch"):
        assert subordinate_courses(role, 1) >= SUBORDINATE_MIN_COURSES


def test_the_cruciform_transepts_abut_the_chancel():
    """The crossing has to be where the arms are.

    In the first round the transepts sat at z11-15 and the chancel began at
    z22, so the crossing was 35 ft from the transepts and the plan was a nave
    with two sheds stuck on it.
    """
    parts = {r: (x, z, w, d) for r, x, z, w, d in PLANS["cruciform"][1]}
    cx, cz, cw, cd = parts["crossing"]
    for arm in ("n_transept", "s_transept"):
        ax, az, aw, ad = parts[arm]
        assert az < cz + cd and az + ad > cz, \
            f"{arm} does not overlap the crossing in z"
        assert ax + aw == cx or cx + cw == ax, \
            f"{arm} does not touch the crossing in x"
    chx, chz, chw, chd = parts["chancel"]
    assert chz == cz + cd, "the chancel does not continue from the crossing"


@pytest.mark.parametrize("name", sorted(PLANS))
def test_a_plan_deals_more_than_one_height(name):
    """A plan whose parts all land on the same course count has no step in it,
    and the step is the whole point of building a church as several volumes."""
    parts = PLANS[name][1]
    nave = next(p for p in parts if p[0] == "nave")
    courses = church_band(nave[3] * nave[4])[0]
    heights = {courses if r == "nave"
               else subordinate_courses(r.split("_")[-1], courses)
               for r, *_ in parts}
    assert len(heights) > 1, f"{name} is flat at {heights}"
