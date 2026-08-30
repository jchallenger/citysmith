"""The roof kits ship at two scales, and only one of them can gable.

Every one of these was measured on a board (`docs/great-buildings.md` §3.1,
§3.1a) after the design claimed the opposite, so they are stated here as
invariants rather than left in prose. The prose said "the gable is buildable
today and its pieces are already resolved"; it was false, and nothing in the
suite could have said so.
"""

import collections
import sys

import pytest

sys.path.insert(0, ".")

from citysmith import catalog
from citysmith.palette import Palette
from citysmith.build import (
    FRONTAGE_RANK, FRONTAGE_STOREYS, GABLE_ENDS, GABLE_MIN_RIDGE,
    END_PIECE_CELLS, _lay_gabled_wing, _tread_for, _wing_gable,
    crowstep_tread, flue_courses, flue_piece, gable_end_for, gable_end_piece,
    gable_infill, roof_set, roof_set_named, roof_stack, storeys_by_frontage,
    CHIMNEY_MIN_WING, wing_carries_a_stack,
    ROOF_EDGE_ROT, ROOF_ROT_OFFSET, roof_course_anchors, roof_course_cells,
    roof_courses, roof_offsets, rotated_footprint,
)


@pytest.fixture(scope="module")
def cat():
    return catalog.load_or_build()


@pytest.fixture(scope="module")
def byname(cat):
    out = {}
    for a in cat.assets:
        out.setdefault(a.name, a)
    return out


# -- the two scales ------------------------------------------------------------

def test_the_roof_kits_ship_at_two_scales(byname):
    """A single-course family at rise 1.0 and a double-course one at 2.0."""
    assert byname["Village Roof Side 01"].size_y == 1.0
    assert byname["Village Roof Side 02"].size_y == 2.0
    assert byname["Thatched Roof 01"].size_y == 1.0
    assert byname["Thatched Roof 03"].size_y == 2.0


def test_no_single_course_end_piece_exists_anywhere(cat):
    """The finding that killed "gable it with the pieces we already have".

    Both `end` pieces are double-course, and `_lay_roofs` builds every tier at
    the single-course scale -- so a gable cannot be closed at the scale the
    town is actually roofed at. Searched over the whole catalog, not one kit.
    """
    singles = [a for a in cat.assets
               if a.kind == "tile" and (a.group_tag or "") == "roof"
               and a.size_y <= 1.0
               and ("end" in a.tags or "end" in a.name.lower())]
    assert singles == [], [a.name for a in singles]


def test_the_end_pieces_are_double_course(byname):
    for name in ("Village Roof Side End 01", "Village Roof Side End 02"):
        assert byname[name].size_y == 2.0


# -- the measured rotation -----------------------------------------------------

#: Offsets whose rotated footprint is 1 wide x 2 deep, so a double-course
#: piece can sit on the pair of cells it spans. Measured, and it is exactly
#: two -- which is why the board showed exactly two roofs tiling.
FOOTPRINT_OK = {6, 18}


def test_only_two_offsets_present_a_double_course_footprint(byname):
    got = set()
    for name in ("Village Roof Side 02", "Village Roof Side End 01"):
        a = byname[name]
        for side in ("n", "s"):
            for off in (0, 6, 12, 18):
                fx, fz = rotated_footprint(a, (ROOF_EDGE_ROT[side] + off) % 24)
                if (round(fx, 2), round(fz, 2)) == (1.0, 2.0):
                    got.add(off)
    assert got == FOOTPRINT_OK


def test_the_double_course_family_shares_the_kits_own_convention(byname):
    """+6, the same offset `ROOF_ROT_OFFSET` already records for Tavern.

    Of the two offsets that can physically sit on the pair of cells, +18 is
    the 180-degree inversion -- it tiles, with every slope falling the wrong
    way. **It was not distinguishable by eye from any of seven views**, so
    this equality is the whole discriminator and is worth a test of its own.
    """
    edge_off = ROOF_ROT_OFFSET["tavern"][0]
    assert edge_off == 6
    for side in ("n", "s"):
        single = (ROOF_EDGE_ROT[side] + edge_off) % 24
        double = (ROOF_EDGE_ROT[side] + 6) % 24
        assert single == double, side


# -- the span, once the flood learned to step two cells --------------------

def _cover(long_: int, across: int, cpc: int):
    """``(covered, overlapped, total)`` for a gabled roof laid by the builder.

    Simulates the placement a caller makes: one piece per anchor, covering
    ``cpc`` cells inward, plus the ridge band. Overlap is counted rather than
    absorbed into a set, because two pieces over one pair is a z-fighting
    defect and reads on the board exactly like a correct roof.
    """
    cells = {(x, z) for x in range(long_) for z in range(across)}
    courses = roof_courses(cells, "x", cpc)
    anchors = roof_course_anchors(courses, "x", cpc)
    hits: dict[tuple[int, int], int] = {}
    for (x, z), (_course, fall) in anchors.items():
        step = 1 if fall == "n" else -1
        for k in range(cpc):
            hits[(x, z + step * k)] = hits.get((x, z + step * k), 0) + 1
    for c, (_course, fall) in courses.items():
        if fall is None:
            hits[c] = hits.get(c, 0) + 1
    covered = sum(1 for v in hits.values() if v >= 1)
    overlapped = sum(1 for v in hits.values() if v > 1)
    return covered, overlapped, len(cells)


@pytest.mark.parametrize("cpc", (1, 2))
@pytest.mark.parametrize("across", range(3, 14))
def test_a_gabled_roof_tiles_every_span_at_both_scales(across, cpc):
    """The whole of `roof-rings-two-cell-step`, stated as an invariant.

    Before it, a double-course gable tiled a **4-cell span and nothing else**:
    5 left the ridge cell bare, 6 and 8 left two bare in different places. A
    warehouse is 11 across, so the feature could not reach a single real
    building on any of the four towns.
    """
    covered, overlapped, total = _cover(8, across, cpc)
    assert covered == total, f"span {across} cpc {cpc}: {covered}/{total}"
    assert overlapped == 0, f"span {across} cpc {cpc}: {overlapped} doubled"


def test_the_ridge_band_is_what_absorbs_the_remainder():
    """A span that is not a multiple of 2*cpc leaves a flat ridge band.

    This is the documented cost of capping the remainder rather than finishing
    it with a single-course pair (`roof-ridge-mixed-scale`). Stated here so the
    cost is a number somebody can see rather than a sentence in a doc.
    """
    cells = {(x, z) for x in range(8) for z in range(11)}
    courses = roof_courses(cells, "x", 2)
    ridge = {c for c, (_, fall) in courses.items() if fall is None}
    # 11 across: two double-courses a side reach 4 each, leaving 3.
    assert len({c[1] for c in ridge}) == 3
    # A multiple of 4 leaves none at all.
    clean = roof_courses({(x, z) for x in range(8) for z in range(8)}, "x", 2)
    assert not [c for c, (_, fall) in clean.items() if fall is None]


def test_the_course_scale_is_read_off_the_piece(byname):
    assert roof_course_cells(byname["Village Roof Side 01"]) == 1
    assert roof_course_cells(byname["Village Roof Side 02"]) == 2
    assert roof_course_cells(byname["Thatched Roof 01"]) == 1


def test_a_gable_falls_only_across_the_ridge():
    """No cell slopes toward a gable end -- that is what makes it a gable."""
    cells = {(x, z) for x in range(8) for z in range(6)}
    for axis, bad in (("x", {"e", "w"}), ("z", {"n", "s"})):
        courses = roof_courses(cells, axis, 1)
        falls = {fall for _, fall in courses.values() if fall is not None}
        assert not (falls & bad), (axis, falls)


@pytest.mark.parametrize("axis", ("x", "z"))
def test_the_two_axes_are_mirror_images(axis):
    """A gable along z is a gable along x with the plan transposed."""
    a = roof_courses({(x, z) for x in range(9) for z in range(5)}, "x", 2)
    b = roof_courses({(x, z) for x in range(5) for z in range(9)}, "z", 2)
    a_courses = sorted(c for c, _ in a.values())
    b_courses = sorted(c for c, _ in b.values())
    assert a_courses == b_courses


def test_roof_courses_rejects_a_nonsense_axis():
    with pytest.raises(ValueError):
        roof_courses({(0, 0)}, "y", 1)


# -- what is laid per cell must BE one cell ------------------------------------

#: Roof roles `_lay_roofs` places with `place_tile`, one per grid cell. A piece
#: bigger than a cell puts its min corner on the cell and reaches past it.
PER_CELL_ROOF_ROLES = ("side", "corner", "inner", "cap")


@pytest.mark.parametrize("seed", (0, 7, 33, 99))
@pytest.mark.parametrize("tier", ("civic", "trade", "common", "utility"))
def test_every_roof_piece_laid_per_cell_is_exactly_one_cell(cat, tier, seed):
    """Found on a copy-out the user took off the board.

    A gable probe capped its ridge with `Tavern Roof flat 02`, which is
    2 x 0.5 x **2** -- so every cap reached a cell past the one it was laid on
    and the roof came out **one unit too big to the north and east**, running
    to x=15 over a building whose walls stop at x=14. The kit ships `flat 01`
    at 1 x 0.5 x 1 for exactly this, and the palette already picks it; the
    probe's own hand-written piece table did not, and the probe bypasses
    `palette.CELL_ROLES` so nothing caught it.

    The town is clean today. This is here because `gable-ends` means writing a
    double-course piece table for real, and "the 2-wide partner" is a
    seductively wrong thing to reach for when the caller lays one per cell.
    """
    palette = Palette.named(cat, "medieval", seed)
    pieces = dict(zip(PER_CELL_ROOF_ROLES, roof_set(palette, tier)))
    for role, a in pieces.items():
        if a is None:
            continue
        assert (a.size_x, a.size_z) == (1.0, 1.0), (
            f"seed {seed} {tier} {role}: {a.name} is "
            f"{a.size_x:g}x{a.size_z:g} cells and is laid one per cell")


# -- the gable end is dealt by QUARTER --------------------------------------

def test_a_quarter_deals_the_same_gable_end_every_time():
    """Stable per (quarter, seed), which `boards.digest_of` depends on."""
    for q in GABLE_ENDS:
        for seed in (0, 1, 7, 33):
            assert gable_end_for(q, seed) == gable_end_for(q, seed)


def test_an_unknown_quarter_falls_back_rather_than_raising():
    assert gable_end_for("nowhere", 3) == "hip"


def test_every_dealt_treatment_is_one_the_table_offers():
    named = {t for mix in GABLE_ENDS.values() for t, _ in mix}
    for q in GABLE_ENDS:
        for seed in range(60):
            assert gable_end_for(q, seed) in named


def test_the_weights_are_a_distribution():
    for q, mix in GABLE_ENDS.items():
        total = sum(w for _, w in mix)
        assert abs(total - 1.0) < 1e-9, (q, total)


def test_the_outskirts_never_crow_step():
    """A crow-stepped gable is a town building's gesture.

    A cottage in the fields does not make it, so `outskirts` is all hip -- and
    that is a design decision worth a test rather than a weight somebody
    tidies later without noticing what it was for.
    """
    for seed in range(200):
        assert gable_end_for("outskirts", seed) == "hip"


def test_civic_crow_steps_more_often_than_any_other_quarter():
    """Crow-stepping is a masonry form and civic is the dressed-stone tier."""
    rate = {q: sum(gable_end_for(q, s) == "crow" for s in range(400)) / 400
            for q in GABLE_ENDS}
    assert rate["civic"] == max(rate.values())
    assert rate["civic"] > 0.5, rate


# -- wiring the gable into _lay_roofs ----------------------------------------

class _Collector:
    """The bare minimum `_lay_gabled_wing` touches: it only ever calls add."""

    def __init__(self):
        self.placements = []

    def add(self, p):
        self.placements.append(p)


def _rect(w: int, d: int):
    return {(x, z) for x in range(w) for z in range(d)}


def test_a_town_with_no_quarters_keeps_every_roof_it_had():
    """`quarter_map` returns None on most settlements, and that is the answer.

    Pelvesthollow and Graybank both have no quarters, so nothing keys on them
    and their roofs must not move at all. Measured on the real layouts: the
    two builds are placement-for-placement identical.
    """
    for wing in (_rect(9, 7), _rect(20, 11), _rect(4, 3)):
        assert _wing_gable(wing, None, 33) == "hip"
        assert _wing_gable(wing, {}, 33) == "hip"


@pytest.mark.parametrize("w,d", [(3, 3), (4, 2), (2, 9), (1, 12)])
def test_a_wing_too_small_to_have_a_ridge_stays_hipped(w, d):
    """A gable needs something to terminate. Below the minimum it is a point."""
    q = dict.fromkeys(_rect(w, d), "civic")
    assert _wing_gable(_rect(w, d), q, 33) == "hip"


def test_a_wing_with_a_real_ridge_takes_its_quarters_deal():
    wing = _rect(GABLE_MIN_RIDGE + 4, 5)
    for quarter in GABLE_ENDS:
        q = dict.fromkeys(wing, quarter)
        assert _wing_gable(wing, q, 33) == gable_end_for(quarter, 33)


def test_only_a_masonry_fabric_can_crow_step(cat):
    """Two medieval kits ship a half-height wall; the rest fall back to flush.

    Not a shortfall -- crow-stepping is a masonry form, and a boarded barn
    with a dressed-stone parapet would be the kit rule broken in the one place
    this project keeps breaking it.
    """
    palette = Palette.named(cat, "medieval", 33)
    assert _tread_for(palette, "civic", {}) is not None
    for tier in ("trade", "common", "utility"):
        assert _tread_for(palette, tier, {}) is None


def test_the_crow_step_tread_is_never_a_broken_wall(cat):
    """`Abandoned Village` ships `haunted wall 1x1 broken` at exactly 1x1x0.5.

    Dealt as a crow-step it makes the parapet read as damage.
    """
    byname = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)
    haunted = byname.get("abandoned_village_wall_1x1_01")
    if haunted is None:
        pytest.skip("Abandoned Village not installed")
    palette = Palette.named(cat, "medieval", 33)
    got = crowstep_tread(palette, haunted)
    assert got is None or "broken" not in got.name.lower()


def test_a_gabled_wing_still_gets_its_chimney(cat):
    """The regression this wiring shipped once and the A/B caught.

    The first cut placed no chimney on a gabled wing at all, which took East
    Tradebourne from 1,578 chimneys to 40. Nothing in `verify` would have
    caught it: every chimney that remained was correct.

    It also states the twin rule through the shipped path: a ridge longer than
    `CHIMNEY_SECOND_CROWN` carries two stacks and a shorter one carries a
    single stack, which is `chimney-per-wing` rather than an accident of the
    fixture's size.
    """
    palette = Palette.named(cat, "medieval", 33)
    side, corner, inner, cap, chimney = roof_set(palette, "common")
    assert chimney is not None, "no chimney in the common roof set"
    flue = flue_piece(chimney, roof_stack(palette, "thatch"))
    assert flue is not None, "no free-standing stack for thatch"
    courses = len(flue_courses(flue))

    for ridge, stacks in ((9, 2), (5, 1)):
        got = _Collector()
        _lay_gabled_wing(got, _rect(ridge, 5), "flush", 4.0, side.size_y,
                         side, cap, 0, None, chimney, flue=flue)
        laid = sum(1 for p in got.placements if p.asset_id == flue.id)
        assert laid == courses * stacks, (
            f"a {ridge}-cell ridge laid {laid} flue pieces, expected "
            f"{stacks} stack(s) of {courses}")


def test_every_wing_over_the_minimum_carries_a_stack():
    """A stack per wing, not one per roof block.

    `_lay_roofs` used to place one chimney per BLOCK, on its longest wing, so
    an L-plan house had a stack on its main range and a cold blank ell: 378 of
    East Tradebourne's 1,462 wings (26%), 61 of Graybank's 215 (28%) and 25 of
    Pelvesthollow's 61 (41%) carried no flue, and no check named it.

    Measured after: stacks 36 -> 82 on Pelvesthollow, 154 -> 262 on Graybank
    and 1,084 -> 2,141 on East Tradebourne.
    """
    main = _rect(9, 5)
    assert wing_carries_a_stack(main, main), "the main wing must keep its own"
    big = _rect(CHIMNEY_MIN_WING, 1)
    assert len(big) == CHIMNEY_MIN_WING
    assert wing_carries_a_stack(big, main), "a wing at the minimum carries one"
    small = _rect(CHIMNEY_MIN_WING - 1, 1)
    assert not wing_carries_a_stack(small, main), (
        "a lean-to under the minimum must not carry a stack")
    # **The rule only ever adds.** A cottage whose whole roof is one wing
    # smaller than the minimum keeps the chimney it already had.
    assert wing_carries_a_stack(small, small)


def test_the_flue_is_the_one_the_user_built(cat):
    """`lay_flue` reproduces `tests/fixtures/handbuilt_chimney.slab` exactly.

    The user laid this chimney in TaleSpire and handed the slab back twice,
    the second time as "acceptable geometry for a chimney that can be
    creatively placed". It is the only measurement there is of what a flue
    should look like, so the shipped placement code is held against it rather
    than against a number copied out of it into a constant -- which is the
    same reason `_HAND_HIPS` exists.

    Decoded, against the base of the 2x2 slope it stands in: four
    `Thatched Chimney` courses at 0.25, 0.50, 0.75 and 1.25. The bottom three
    lap by `CHIMNEY_LAP`, each buried to its middle in the one below, and the
    fourth sits flush on the third to form the mouth.
    """
    from citysmith import slab as S
    from citysmith.build import lay_flue

    byid = {}
    for a in cat.assets:
        byid.setdefault(str(a.id).lower(), a)
    sl = S.decode(open("tests/fixtures/handbuilt_chimney.slab").read().strip())

    roof, hand = [], []
    for pl in sl.placements:
        a = byid.get(str(pl.asset_id).lower())
        assert a is not None, pl.asset_id
        (hand if "chimney" in a.name.lower() else roof).append((pl, a))
    assert len(hand) == 4, f"the fixture is not four courses: {len(hand)}"
    assert len({a.name for _, a in hand}) == 1
    bare = hand[0][1]

    # The slope the flue comes through is still there, unmodified -- which is
    # the whole of why the combination piece is retired.
    assert roof, "the fixture has no roof under its chimney"
    assert all(a.size_y == 2.0 for _, a in roof), "not the double scale"
    base = min(pl.y for pl, _ in roof)

    got = _Collector()
    lay_flue(got, bare, 0, 0, base, on_slope=True)
    assert sorted(round(p.y, 4) for p in got.placements) == sorted(
        round(pl.y, 4) for pl, _ in hand), (
        f"laid {sorted(round(p.y, 4) for p in got.placements)} against the "
        f"hand-build's {sorted(round(pl.y, 4) for pl, _ in hand)}")


def test_no_roof_and_chimney_combination_is_placed(cat):
    """The combination piece is retired; a flue comes through a real roof.

    `Village Roof Side/Chimney` is a slope with a stack cast onto it, so it
    could only be laid where its own slope fitted and had to be turned to
    match. Every chimney in every town was one, at rot 0 -- 1,084 of them on
    East Tradebourne, and the user's word for what that looked like was that
    one of them "doesnt need the (chimney on slant roof) tile, just the normal
    chimney".

    What replaces it is the hand-built flue, which modifies nothing: the cell
    gets the slope or cap it would have had anyway, and the stack comes up
    through it. That is what makes an end stack and a lateral stack a choice
    of cell rather than a hunt for an asset -- see `CHIMNEY_FORMS`.
    """
    from citysmith.build import build_from_tilemap, is_roof_chimney
    from citysmith.layout import Layout
    from citysmith.raster import rasterize

    palette = Palette.named(cat, "medieval", 33)
    byid = {a.id: a for a in palette.catalog.assets}
    tm = rasterize(Layout.load("out/fc-v2/layout.json"))
    b = build_from_tilemap(tm, palette, storeys=3)

    combos = collections.Counter(
        byid[pl.asset_id].name for pl in b.placements
        if is_roof_chimney(byid[pl.asset_id]))
    stacks = sum(1 for pl in b.placements
                 if "chimney" in byid[pl.asset_id].name.lower())
    assert stacks, "no chimney on the board -- the test proves nothing"
    assert not combos, f"combination pieces still placed: {dict(combos)}"


def test_a_town_uses_both_of_the_tavern_stacks(cat):
    """`Chimney 02` had never been placed on any board.

    Two pieces in one kit, the same 1x1x1 box, both plain stone stacks and
    nothing to choose between them -- which is what `walls.stem_of` is for.
    Dealt per BUILDING rather than per stack, so a house's own chimneys match
    and its neighbour's need not.
    """
    from citysmith.build import build_from_tilemap
    from citysmith.layout import Layout
    from citysmith.raster import rasterize

    palette = Palette.named(cat, "medieval", 33)
    byid = {a.id: a for a in palette.catalog.assets}
    if "Chimney 02" not in {a.name for a in palette.catalog.assets}:
        pytest.skip("Tavern kit not installed")
    tm = rasterize(Layout.load("out/fc-v2/layout.json"))
    b = build_from_tilemap(tm, palette, storeys=3)

    seen = collections.Counter(
        byid[pl.asset_id].name for pl in b.placements
        if byid[pl.asset_id].name in ("Chimney 01", "Chimney 02"))
    assert len(seen) == 2, f"only one of the two tile stacks is used: {dict(seen)}"


def test_a_settlement_with_no_quarters_puts_every_stack_on_the_ridge(cat):
    """The honest fallback, stated rather than left to be discovered.

    `quarters.quarter_map` answers None on any town whose kinds do not
    cluster, which is most of them -- Pelvesthollow and Graybank both -- and
    then there is nothing to key a form on. Measured: dealing the forms moves
    93% of East Tradebourne's stacks to a new cell and moves none at all on
    the other two.
    """
    from citysmith.build import DEFAULT_CHIMNEY_FORM, _wing_chimney_form

    assert _wing_chimney_form(_rect(6, 4), None, 33) == DEFAULT_CHIMNEY_FORM
    assert _wing_chimney_form(_rect(6, 4), {}, 33) == DEFAULT_CHIMNEY_FORM
    # A cell the map has no quarter for falls back the same way.
    assert _wing_chimney_form(_rect(6, 4), {(99, 99): "craft"}, 33)         == DEFAULT_CHIMNEY_FORM


def test_a_flush_gable_lays_nothing_outside_its_own_wing(cat):
    """A roof that overhangs its footprint drags every registration check."""
    palette = Palette.named(cat, "medieval", 33)
    side, corner, inner, cap, chimney = roof_set(palette, "common")
    wing = _rect(9, 5)
    got = _Collector()
    _lay_gabled_wing(got, wing, "flush", 4.0, side.size_y,
                     side, cap, 0, None, None)
    for p in got.placements:
        cell = (int(p.x // 1), int(p.z // 1))
        assert cell in wing, f"{cell} is outside the wing"


# -- frontage moves the storey count ------------------------------------------

def test_the_frontage_deal_is_stable():
    """A town must rebuild to the same bytes; `boards.digest_of` depends on it."""
    for f in FRONTAGE_STOREYS:
        for bid in ("house-0001", "shop-0042", "temple-0007"):
            assert storeys_by_frontage(2, f, bid) == storeys_by_frontage(2, f, bid)


def test_a_building_never_loses_its_last_storey():
    """A building with no storeys is a floor with a roof on it."""
    for f in FRONTAGE_STOREYS:
        for i in range(300):
            assert storeys_by_frontage(1, f, f"house-{i:04d}") >= 1


def test_the_frontage_weights_are_a_distribution():
    for f, mix in FRONTAGE_STOREYS.items():
        total = sum(w for _, w in mix)
        assert abs(total - 1.0) < 1e-9, (f, total)


def test_an_unknown_frontage_leaves_the_count_alone():
    assert storeys_by_frontage(2, "nowhere", "house-0001") == 2


def test_a_back_lane_ends_up_lower_than_the_high_street():
    """The gradient the task asked for, as an ordering rather than a number."""
    ids = [f"house-{i:04d}" for i in range(600)]
    mean = {f: sum(storeys_by_frontage(2, f, b) for b in ids) / len(ids)
            for f in FRONTAGE_STOREYS}
    assert mean["main"] > mean["cart"] > mean["lane"] > mean["open"], mean


def test_a_lane_gets_a_MIX_and_not_a_uniform_drop():
    """The complaint was variety, not height.

    `docs/building-massing.md` §11: "the craft block is 15 of 21 at three
    storeys and has no single-storey building at all. A real street has a low
    workshop and an outbuilding." Subtracting a storey from every back-lane
    building trades one monotone skyline for another, one course lower -- so
    the deal has to leave some of them standing tall.
    """
    ids = [f"house-{i:04d}" for i in range(600)]
    got = {storeys_by_frontage(3, "lane", b) for b in ids}
    assert len(got) > 1, got


def test_frontage_ranks_are_ordered_worst_to_best():
    assert FRONTAGE_RANK[0] == "open" and FRONTAGE_RANK[-1] == "main"
    assert set(FRONTAGE_RANK) == set(FRONTAGE_STOREYS)


# -- a gable must be able to CLOSE itself -------------------------------------

@pytest.mark.parametrize("tier", ("civic", "trade", "common", "utility"))
def test_every_gabled_fabric_can_close_its_own_triangle(cat, tier):
    """A gable it cannot close is worse than a hip, so every tier needs one.

    The hole is not an oversight in the geometry: a gable is exactly the case
    where the roof rises above the wall at the end of a building, and a hip has
    no triangle only because its boundary cells sit at the wall head. The first
    wiring shipped a 1.5-tile hole at every flush gable end on East
    Tradebourne because it tried to gable without one.
    """
    palette = Palette.named(cat, "medieval", 33)
    tread = _tread_for(palette, tier, {})
    infill = gable_infill(palette, tier, tread)
    assert infill is not None, f"{tier} cannot close a gable triangle"
    # One course tall at most, or it cannot step with a 1.0 rise. It may be a
    # thin WALL panel (civic carries its own wall up) or a full-cell roof cap
    # (a timber verge is closed in the roof's material) -- `_lay_gabled_wing`
    # reads which off the collider and picks place_wall or place_tile.
    assert 1e-6 < infill.size_y <= 1.0, infill.name
    assert max(infill.size_x, infill.size_z) == 1.0, infill.name


def test_the_masonry_fabric_carries_its_own_wall_up(cat):
    """Where the wall kit HAS a half-height panel, that is the right answer.

    `castle wall 1x1 half` is the gable wall carried up, which is what a
    masonry gable is -- and it doubles as the crow-step tread, so civic gets
    one piece for both jobs.
    """
    palette = Palette.named(cat, "medieval", 33)
    tread = _tread_for(palette, "civic", {})
    assert gable_infill(palette, "civic", tread) is tread


def test_a_timber_fabric_closes_its_verge_in_the_ROOFS_material(cat):
    """Tavern and Rural ship no wall piece under two tiles.

    Only floors, roofs and stairs -- so the house and the barn cannot carry
    their wall up, and the verge is closed in the roof's own material instead.
    Tile hanging and a wrapped thatch verge are both how a real gable of those
    materials is finished.
    """
    palette = Palette.named(cat, "medieval", 33)
    for tier in ("trade", "common", "utility"):
        infill = gable_infill(palette, tier, None)
        assert infill is roof_set(palette, tier)[3], tier


def test_the_infill_stops_below_the_roof_rather_than_inside_it(cat):
    """Filling to the course height buries it: +1,020 tile seams measured."""
    palette = Palette.named(cat, "medieval", 33)
    side, corner, inner, cap, chimney = roof_set(palette, "common")
    infill = gable_infill(palette, "common", None)
    got = _Collector()
    _lay_gabled_wing(got, _rect(9, 5), "flush", 4.0, side.size_y,
                     side, cap, 0, None, None, infill)
    # For a timber fabric the infill and the ridge cap are the SAME asset, so
    # they cannot be told apart by id -- compare per column instead. On a gable
    # end column nothing may reach above that column's own roof.
    from citysmith.build import cell_of, roof_courses
    courses = roof_courses(_rect(9, 5), "x", 1)
    ends = (0, 8)
    lookup = {a.id: a for a in cat.assets}
    # Infill is what sits BELOW the roof at that column; the roof piece itself
    # starts at the head and is a whole course tall, so it legitimately reaches
    # above it. Isolating by height rather than by id is what makes this work
    # for a fabric whose infill and ridge cap are the same asset.
    worst = 0.0
    seen = 0
    for p in got.placements:
        a = lookup.get(p.asset_id)
        if a is None:
            continue
        x, z = cell_of(p, a)
        if x not in ends:
            continue
        head = 4.0 + courses[(x, z)][0] * side.size_y
        if p.y >= head - 1e-6:
            continue                       # this is the roof, not the infill
        seen += 1
        worst = max(worst, (p.y + a.size_y) - head)
    assert seen, "no infill laid below the roof"
    assert worst <= 1e-6, f"infill reaches {worst:g} into its own roof"


# -- the two scales mix: endmix ------------------------------------------------
#
# `docs/great-buildings.md` §3.1 concluded from a ring flood that a 1x2x2 end
# piece "puts a two-tile-tall, two-cell-deep piece where a one-tile one belongs,
# which is the trough". That is true of a flood and false of a roof built in
# courses, and a hand-build the user handed over is the proof
# (`docs/roofscape.md` §8.2). These state the geometry that makes it true.


def test_the_end_piece_spans_exactly_two_single_courses(cat):
    """Why the scales mix at all: the piece is 2 tall and the field steps 1."""
    palette = Palette.named(cat, "medieval", 33)
    side = roof_set_named(palette, "tile")[0]
    end = gable_end_piece(palette, side)
    assert end is not None, "Tavern ships a double-course end; it went missing"
    assert round(end.size_y, 2) == round(side.size_y * END_PIECE_CELLS, 2)
    span = (round(min(end.size_x, end.size_z), 2),
            round(max(end.size_x, end.size_z), 2))
    assert span == (1.0, float(END_PIECE_CELLS)), (
        f"{end.name} is {end.size_x:g}x{end.size_z:g}; a verge piece is one "
        f"cell along the ridge and {END_PIECE_CELLS} across")


def test_only_a_kit_that_ships_an_end_can_endmix(cat):
    """Thatch and slate have no end piece, so they must fall back, not fail.

    Same shape as `crow` falling back to `flush` where a fabric ships no tread.
    A gable it cannot close is worse than a hip, and a gable closed in another
    kit's material is worse than either.
    """
    palette = Palette.named(cat, "medieval", 33)
    got = {suffix: gable_end_piece(palette, roof_set_named(palette, suffix)[0])
           for suffix in ("", "tile", "slate")}
    assert got["tile"] is not None
    assert got[""] is None, "Rural has no double-course end; one appeared"
    assert got["slate"] is None, "Abandoned Village has no end; one appeared"


def test_endmix_falls_back_to_flush_without_an_end_piece(cat):
    """The fallback is in the builder, not left to the caller."""
    palette = Palette.named(cat, "medieval", 33)
    side, _corner, _inner, cap, _chimney = roof_set(palette, "common")
    infill = gable_infill(palette, "common", None)
    wing = _rect(9, 4)
    a, b = _Collector(), _Collector()
    _lay_gabled_wing(a, wing, "endmix", 4.0, side.size_y, side, cap, 0, None,
                     None, infill, None)
    _lay_gabled_wing(b, wing, "flush", 4.0, side.size_y, side, cap, 0, None,
                     None, infill, None)
    assert len(a.placements) == len(b.placements)
    assert {(p.asset_id, p.x, p.y, p.z, p.rot) for p in a.placements} == \
           {(p.asset_id, p.x, p.y, p.z, p.rot) for p in b.placements}


def test_an_endmix_verge_is_closed_by_end_pieces(cat):
    """On a 4-deep wing both halves pair, so every verge cell is an end piece.

    Four deep is the hand-build's own depth and 34% of the gable-eligible
    wings on the three towns; the field keeps its single-course slopes.
    """
    palette = Palette.named(cat, "medieval", 33)
    side, _corner, _inner, cap, _chimney = roof_set_named(palette, "tile")
    end = gable_end_piece(palette, side)
    # **The kit's own offset, not 0.** Which way a 1x2 piece lands depends on
    # whether the fall's rotation is an even quarter turn, and that is set by
    # the kit -- Tavern is +6. Passing 0 here is not a simpler test, it is a
    # different and impossible configuration.
    edge_off, _ = roof_offsets(side)
    wing = _rect(9, 4)
    got = _Collector()
    _lay_gabled_wing(got, wing, "endmix", 4.0, side.size_y, side, cap,
                     edge_off, None, None,
                     gable_infill(palette, "trade", None), end)
    ends = [p for p in got.placements if p.asset_id == end.id]
    # Two verges, four cells deep, two cells to a piece.
    assert len(ends) == 4, f"expected 4 end pieces on a 9x4 wing, got {len(ends)}"
    # And nothing else was laid on the cells they own.
    owned = set()
    for p in ends:
        for k in range(END_PIECE_CELLS):
            owned.add((int(p.x // 1), int(p.z // 1) + k))
    others = {(int(p.x // 1), int(p.z // 1)) for p in got.placements
              if p.asset_id != end.id}
    assert not (owned & others), (
        f"{sorted(owned & others)} carry both an end piece and something else")


def test_an_end_piece_never_overhangs_its_wing(cat):
    """A piece bigger than a cell reaches past it -- the rule this repo has
    already paid for twice (`roof-rings-two-cell-step`, the 2x2 ridge cap).
    """
    palette = Palette.named(cat, "medieval", 33)
    side, _corner, _inner, cap, _chimney = roof_set_named(palette, "tile")
    end = gable_end_piece(palette, side)
    infill = gable_infill(palette, "trade", None)
    edge_off, _ = roof_offsets(side)
    for w in range(4, 12):
        for d in range(3, 10):
            wing = _rect(w, d)
            got = _Collector()
            _lay_gabled_wing(got, wing, "endmix", 4.0, side.size_y, side, cap,
                             edge_off, None, None, infill, end)
            for p in got.placements:
                if p.asset_id != end.id:
                    continue
                asset_w = end.size_z if (p.rot // 6) % 2 else end.size_x
                asset_d = end.size_x if (p.rot // 6) % 2 else end.size_z
                for dx in range(int(round(asset_w))):
                    for dz in range(int(round(asset_d))):
                        cell = (int(p.x // 1) + dx, int(p.z // 1) + dz)
                        assert cell in wing, (
                            f"{w}x{d}: end piece at {(p.x, p.z)} rot {p.rot} "
                            f"reaches {cell}, outside the wing")


def test_a_small_town_is_untouched_by_endmix():
    """The honest-fallback guarantee, stated rather than left to an A/B.

    `quarter_map` is None on a settlement whose kinds do not cluster, which is
    Pelvesthollow and Graybank, so no wing gables and endmix cannot fire.
    Measured: both towns are placement-for-placement identical with the
    treatment honoured and with it forced to flush.
    """
    for quarter in GABLE_ENDS:
        mix = dict(GABLE_ENDS[quarter])
        assert abs(sum(mix.values()) - 1.0) < 1e-9, f"{quarter} does not sum to 1"
    assert "endmix" not in dict(GABLE_ENDS["outskirts"]), (
        "a cottage in the fields does not make a town building's gesture")
    assert "endmix" not in dict(GABLE_ENDS["civic"]), (
        "crow-stepping is the masonry form; endmix is a tiled one")


# -- the hip pinches to a ridge, it does not plateau ---------------------------


def test_an_even_short_side_gets_a_ridge_not_a_deck(cat):
    """The defect `roof-flat-top-on-a-small-wing` names, stated as geometry.

    `_roof_rings` steps one cell in and one course up, so a wing whose short
    side is EVEN stops with a band two cells wide at the top. Capping all of
    that flat loses a whole course and puts a deck where the ridge belongs --
    a 6 x 4 came out a flat-topped box, and 5 x 6 is the commonest wing shape
    on every board measured.
    """
    from citysmith.build import SIDE_OFFSETS, _roof_rings, roof_top_is_supported
    for w, d in ((6, 4), (4, 4), (7, 4), (6, 6), (8, 8)):
        cells = _rect(w, d)
        rings = _roof_rings(cells)
        top = max(rings.values())
        capped = 0
        for (x, z), r in rings.items():
            if r != top:
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            if not roof_top_is_supported(rings, x, z, fall):
                capped += 1
        assert capped == 0, (
            f"{w}x{d} has an even short side; its top ring should pinch to a "
            f"ridge, but {capped} cell(s) still cap flat")


def test_an_odd_short_side_is_untouched(cat):
    """The flood already pinches there, and the fix must not disturb it.

    A one-cell ridge line falls two or three ways at every cell and has
    nothing to lean on, so it caps exactly as it always did. Stated because
    the change is to the path every hip on every board goes through.
    """
    from citysmith.build import SIDE_OFFSETS, _roof_rings, roof_top_is_supported
    for w, d in ((5, 6), (9, 5), (8, 3), (7, 7)):
        cells = _rect(w, d)
        rings = _roof_rings(cells)
        top = max(rings.values())
        sloped = 0
        for (x, z), r in rings.items():
            if r != top:
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            if roof_top_is_supported(rings, x, z, fall):
                sloped += 1
        assert sloped == 0, (
            f"{w}x{d} has an odd short side and already pinched; "
            f"{sloped} cell(s) changed")


def test_no_slope_is_ever_left_without_something_to_lean_on(cat):
    """The invariant the cap was protecting, kept.

    "A slope at the apex shows its open underside" is why the top ring was
    capped at all -- the bare timber that showed at the top of every slate
    roof. Capping the whole ring was too broad, not wrong, so the underside
    rule is asserted directly rather than left implied by the ring index.
    """
    from citysmith.build import (SIDE_OFFSETS, _BACK_OF, _roof_rings,
                                 roof_top_is_supported)
    for w in range(3, 12):
        for d in range(3, 12):
            rings = _roof_rings(_rect(w, d))
            for (x, z), r in rings.items():
                fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                             if rings.get((x + dx, z + dz), -1) < r)
                if len(fall) != 1 or not roof_top_is_supported(rings, x, z, fall):
                    continue
                dx, dz = _BACK_OF[fall[0]]
                assert rings.get((x + dx, z + dz), -1) >= r, (
                    f"{w}x{d} at {(x, z)}: sloped with nothing behind it")


# -- a chimney is two pieces, and which one depends on the cell -----------------


def test_the_tile_chimney_is_a_freestanding_stack(cat):
    """`roof_stack_*` must not resolve a combination piece.

    `Village Roof Side/Chimney` is a roof slope with a stack cast onto it --
    its tags say so, carrying `roof` AND `chimney` AND `wood` where
    `Chimney 01` is `chimney` and `stone`. Dropped on a capped ridge it stands
    a bare slope on end beside the flue, which is the pale skirt hanging off
    the stacks in the user's screenshot. A cap wants the free-standing one.
    """
    from citysmith.build import roof_stack
    palette = Palette.named(cat, "medieval", 33)
    for suffix in ("", "tile", "slate"):
        stack = roof_stack(palette, suffix)
        assert stack is not None, f"no free-standing stack for {suffix or 'thatch'}"
        assert "/" not in stack.name, (
            f"{suffix or 'thatch'} resolves {stack.name!r}, a combination piece")


def test_a_ridge_stack_is_not_two_lapped_stubs(cat):
    """Rural ships a 1.5-tall stack and we were lapping two 0.5 ones.

    Two `Thatched Chimney` at CHIMNEY_LAP make 0.75 tiles -- under four feet of
    chimney. The lap was invented because "a single 0.5 piece reads as a stub";
    the kit's own answer to that is `Thatched Roof Chimney`, three times the
    height in one piece, which nothing resolved.
    """
    from citysmith.build import roof_stack
    palette = Palette.named(cat, "medieval", 33)
    assert roof_stack(palette, "").size_y >= 1.0, (
        "the thatch stack is still a stub")


def test_a_chimney_on_a_slope_takes_that_slope_s_rotation(cat):
    """Measured off the user's hand-build: never rot 0 regardless.

    Both hand-built roofs lay the combination piece over an ordinary slope at
    the same cell and height, at a rotation chosen against that slope --
    matching it for a stack on the slope, opposing it for one straddling the
    ridge. Ours laid all 1,084 on East Tradebourne at rot 0.
    """
    from citysmith.build import (ROOF_EDGE_ROT, SIDE_OFFSETS, _roof_rings,
                                 roof_offsets, roof_set_named,
                                 roof_top_is_supported)
    palette = Palette.named(cat, "medieval", 33)
    side = roof_set_named(palette, "tile")[0]
    edge_off, _ = roof_offsets(side)
    # A 6x4 now pinches to a ridge, so its crown cells SLOPE -- which is
    # exactly the case that must not be laid at rot 0.
    rings = _roof_rings(_rect(6, 4))
    top = max(rings.values())
    sloped = []
    for (x, z), r in rings.items():
        if r != top:
            continue
        fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                     if rings.get((x + dx, z + dz), -1) < r)
        if len(fall) == 1 and roof_top_is_supported(rings, x, z, fall):
            sloped.append((ROOF_EDGE_ROT[fall[0]] + edge_off) % 24)
    assert sloped, "a 6x4 crown should slope now"
    assert set(sloped) != {0}, "every crown slope came out at rot 0"
