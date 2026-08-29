"""A yard is the size of the ground the building actually has.

`YARD_REACH` used to be one number for every building in every town, and on a
board that is the wrong shape twice: at two cells a plot is an L round one
corner of the house rather than an enclosure, and a farmstead standing in open
country gets the same 10 ft skirt as a house wedged between two neighbours --
so every yard in a town is the same yard.

These pin the replacement. The variance is **measured from the site, not dealt
from a seed**: two buildings with the same room round them get the same yard,
and what differs is the ground each one has. Same argument the district work
made about wards -- an axis that does not discriminate is a knob dressed as a
feature.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from citysmith import raster as R
from citysmith.build import place_centered
from citysmith.build import (YARD_FRONT_REACH, YARD_MAX_REACH, YARD_MIN_SIDE,
                             yard_cells, yard_form, yard_reach_by_side)


def _map(width: int, depth: int) -> R.TileMap:
    """Open grass, nothing on it."""
    return R.TileMap.blank(width, depth, "Test")


def _put(tm: R.TileMap, bid: str, x0: int, z0: int, w: int, d: int) -> None:
    for z in range(z0, z0 + d):
        for x in range(x0, x0 + w):
            tm.building[z][x] = bid


def _reaches(tm: R.TileMap, bid: str) -> dict[str, int]:
    return yard_reach_by_side(tm, bid)


# ---------------------------------------------------------------- the size

def test_a_yard_reaches_further_where_there_is_more_room():
    """The whole of the feature, in one assertion.

    The same building twice: once with a neighbour two cells away, once with
    open ground. The first gets a strip; the second gets a yard.
    """
    tight = _map(40, 12)
    _put(tight, "a", 4, 4, 4, 4)
    _put(tight, "b", 10, 4, 4, 4)          # 2 cells of gap to the east

    roomy = _map(40, 12)
    _put(roomy, "a", 4, 4, 4, 4)           # nothing else on the map

    assert _reaches(tight, "a")["e"] == 2
    assert _reaches(roomy, "a")["e"] == YARD_MAX_REACH
    assert _reaches(roomy, "a")["e"] > _reaches(tight, "a")["e"]


def test_the_reach_is_capped_so_open_country_does_not_become_one_yard():
    """Without a cap a lone farmhouse claims ground to the edge of the map."""
    tm = _map(60, 60)
    _put(tm, "farm", 28, 28, 4, 4)
    assert set(_reaches(tm, "farm").values()) == {YARD_MAX_REACH}


def test_a_side_with_no_room_gets_no_yard_and_the_others_still_do():
    """A terrace house has ground behind it and none at its party walls."""
    tm = _map(40, 20)
    _put(tm, "mid", 10, 8, 4, 4)
    _put(tm, "west", 6, 8, 4, 4)           # sharing a party wall
    _put(tm, "east", 14, 8, 4, 4)

    r = _reaches(tm, "mid")
    assert r["w"] == 0 and r["e"] == 0, "a party wall has no yard on it"
    assert r["n"] > 0 and r["s"] > 0, "the ground front and back is still there"


def test_a_one_cell_gap_between_two_buildings_is_not_a_yard():
    """One cell of worked ground against a wall is a verge, not somewhere to
    stand -- and fencing it produces a panel with a building on both sides."""
    tm = _map(40, 20)
    _put(tm, "a", 8, 8, 4, 4)
    _put(tm, "b", 13, 8, 4, 4)             # exactly one cell between them
    assert YARD_MIN_SIDE == 2
    assert _reaches(tm, "a")["e"] == 0
    assert _reaches(tm, "b")["w"] == 0


def test_a_partly_blocked_face_still_has_a_yard_on_it():
    """The clearance is the MEDIAN of the runs along a face, not the least.

    Taking the minimum lets one clipped corner veto a whole side, and on a
    rasterised footprint most faces have one.
    """
    tm = _map(40, 20)
    _put(tm, "house", 10, 8, 6, 4)
    _put(tm, "shed", 10, 4, 2, 2)          # blocks two of the six north cells

    r = _reaches(tm, "house")
    assert r["n"] > 0, "four clear cells out of six is still a yard"


# ---------------------------------------------------------------- front and back

def test_the_ground_in_front_of_the_door_is_shallower_than_the_ground_behind():
    """A house fronting a street keeps a shallow strip and puts its ground
    round the back. That distinction is the whole of `front yard` against
    `back yard`, and capping the door's side is all it takes."""
    tm = _map(40, 40)
    _put(tm, "house", 18, 18, 4, 4)
    tm.doors["house"] = [(18, 18, "n")]

    r = _reaches(tm, "house")
    assert r["n"] == YARD_FRONT_REACH
    assert r["s"] == YARD_MAX_REACH
    assert r["s"] > r["n"]


def test_a_building_with_no_door_is_not_given_a_front():
    """MFCG exports plenty of footprints the raster never doors."""
    tm = _map(40, 40)
    _put(tm, "shed", 18, 18, 4, 4)
    assert set(_reaches(tm, "shed").values()) == {YARD_MAX_REACH}


# ---------------------------------------------------------------- the names

def test_yard_form_names_the_shape_that_comes_out():
    assert yard_form({"n": 0, "e": 0, "s": 0, "w": 0}) == "none"
    assert yard_form({"n": 4, "e": 4, "s": 4, "w": 4}) == "full"
    assert yard_form({"n": 4, "e": 4, "s": 4, "w": 0}) == "wrapped"
    assert yard_form({"n": 4, "e": 4, "s": 0, "w": 0}) == "corner"
    assert yard_form({"n": 4, "e": 0, "s": 4, "w": 0}) == "through"
    assert yard_form({"n": 0, "e": 0, "s": 4, "w": 0}, front="n") == "back"
    assert yard_form({"n": 4, "e": 0, "s": 0, "w": 0}, front="n") == "front"
    assert yard_form({"n": 0, "e": 4, "s": 0, "w": 0}, front="n") == "side"


# ---------------------------------------------------------------- the apron

def test_the_apron_is_not_square():
    """`yard_cells` has to *use* the per-side reach, not just measure it.

    The bug this guards against is a real one and it is silent: the sizing can
    be measured perfectly and then thrown away by a square dilation, and the
    only symptom is that every yard on the board is the same yard again.
    """
    tm = _map(40, 40)
    _put(tm, "house", 18, 18, 4, 4)
    tm.doors["house"] = [(18, 18, "n")]

    cells = yard_cells(tm)["house"]
    north = min(z for _, z in cells)
    south = max(z for _, z in cells)
    assert 18 - north == YARD_FRONT_REACH, "the front is capped"
    assert south - 21 == YARD_MAX_REACH, "the back is not"


def test_two_buildings_with_different_room_get_different_yards():
    """The point of the whole change, asserted on the output rather than on
    the measurement: a town must stop having one yard repeated."""
    tm = _map(60, 24)
    _put(tm, "roomy", 8, 10, 4, 4)
    # Three cells of gap: enough to clear `YARD_MIN_GAP` and still qualify for
    # a yard, and not enough for the full reach on that side.
    _put(tm, "tight", 34, 10, 4, 4)
    _put(tm, "neighbour", 41, 10, 4, 4)

    yards = yard_cells(tm)
    assert len(yards["roomy"]) > len(yards["tight"])


# ---------------------------------------------------------------- the boundary
#
# These use the real catalog, because every one of them is about an asset's
# measured shape -- how long a panel is, how deep two of them meet -- and a
# stub palette cannot pin any of that. `docs/fencing.md` §10.

import math

from citysmith.build import (FENCE_MIN_RUN, FRONTAGE_MIN_RUN, YARD_BOUNDARY,
                             blocks_a_way, boundary_runs, build_from_tilemap,
                             covered_cells, edge_taper, rotated_footprint,
                             tier_of, yard_boundary, Builder, _lay_yards,
                             _run_panels, _stub)
from citysmith.catalog import load_or_build
from citysmith.layout import Layout, LayoutBuilding
from citysmith.palette import MEDIEVAL, Palette
from citysmith import verify as V

WAYS = frozenset({R.STREET, R.PLAZA, R.LANE, R.PIER})


def _real() -> Palette:
    return Palette(load_or_build(), MEDIEVAL)


def _town(*, spacing=12, count=4, width=70, depth=60, kinds=None) -> Layout:
    layout = Layout(name="probe")
    layout.width, layout.depth = float(width), float(depth)
    for i in range(count):
        x = 6 + i * spacing
        kind = (kinds or ["house"] * count)[i]
        layout.buildings.append(LayoutBuilding(
            id=f"{kind}-{i + 1:04d}",
            ring=[(x, 22), (x + 5, 22), (x + 5, 27), (x, 27)],
            kind=kind, floors=1,
        ))
    return layout


def _yard_panels(tm, palette):
    b = Builder(palette, 0)
    _lay_yards(b, tm, grade=palette.require("floor").size_y,
               taper=edge_taper(tm))
    return b


def test_no_yard_fence_panel_laps_another_lengthwise():
    """The defect this whole pass was rewritten for.

    `place_wall` centres a piece on a 1-tile cell edge, and every boundary
    piece in the kit is 2.0 tiles long, so one panel per cell edge laid every
    fence twice: 507 of Pelvesthollow's 599 panels had another lying on top of
    them. It was invisible to `verify` because two collinear panels separate on
    their thin axis first, so the penetration is the panel's own thickness --
    exactly the corner-join allowance.
    """
    tm = R.rasterize(_town())
    b = _yard_panels(tm, _real())
    assert b.placements, "the sample must actually build a boundary"

    by_rot = {}
    for p in b.placements:
        by_rot.setdefault(p.rot, []).append(p)

    for rot, panels in by_rot.items():
        ang = math.radians(rot * 15)
        ux, uz = math.cos(ang), math.sin(ang)
        lanes = {}
        for p in panels:
            across = round(-p.x * uz + p.z * ux, 3)
            lanes.setdefault(across, []).append(p.x * ux + p.z * uz)
        for along in lanes.values():
            along.sort()
            for a, c in zip(along, along[1:]):
                assert c - a > 1.9, (
                    f"two panels at rot {rot} lie {c - a:.2f} apart along their "
                    "own axis -- a 2-tile panel lapping its neighbour")


def test_a_boundary_run_is_stepped_at_the_panel_and_not_at_the_cell():
    """`FENCE_MODULE` already stated this rule for field walls; the yard pass
    used to step by the cell, which is half the panel."""
    piece = _real().require("yard_fence")
    ts = [t for t, _, _ in _run_panels(piece, ("n", 5, 0, 7))]   # an 8-cell run
    assert len(ts) == 4, "eight cells of run take four two-tile panels"
    for a, c in zip(ts, ts[1:]):
        assert abs((c - a) - 2.0) < 1e-6


def test_an_odd_run_leaves_a_gap_rather_than_lapping_a_panel():
    """Rounding up laps one panel per odd run, and a lap is a panel the game
    may drop. Rounding down puts the remainder in the middle, where a gate
    would be, and keeps both corners flush."""
    piece = _real().require("yard_fence")
    ts = [t for t, _, _ in _run_panels(piece, ("n", 5, 0, 4))]   # a 5-cell run
    assert len(ts) == 2
    assert abs(ts[0] - 1.0) < 1e-6, "the first panel is flush with the start"
    assert abs(ts[1] - 4.0) < 1e-6, "the last is flush with the end"
    assert ts[1] - ts[0] > 2.0, "and the remainder is a gap, not a lap"


def test_a_lone_yard_cell_is_not_fenced_on_all_four_sides():
    """A single cell's four sides all meet at itself, so a test that only asked
    whether some perpendicular run shared an endpoint kept all four -- a cross
    of 2-tile panels centred on one 5 ft square, with its arms in the road."""
    tm = R.TileMap.blank(30, 30, "Test")
    runs = [("n", 5, 5, 5), ("s", 5, 5, 5), ("e", 5, 5, 5), ("w", 5, 5, 5)]
    assert all(_stub(r, runs) for r in runs)


def test_no_boundary_panel_stands_in_a_street():
    """The playability check: a wall across a way is an obstacle on the one
    thing the map is for."""
    layout = _town()
    tm = R.rasterize(layout)
    palette = _real()
    b = build_from_tilemap(tm, palette, storeys=1, seed=0, layout=layout)
    catalog = palette.catalog
    for p, asset in V._boundary_boxes(b):
        assert not blocks_a_way(tm, asset, p.x, p.z, p.rot, WAYS), (
            f"{asset.name} at ({p.x:.2f}, {p.z:.2f}) stands in a way")


def test_every_yard_has_a_way_in():
    """A yard sealed on four sides is a courtyard nobody can enter, which is
    what 17 of East Tradebourne's 230 were: the opening rule borrowed its gate
    from a street edge, and a yard reached across grass has no street edge."""
    layout = _town()
    tm = R.rasterize(layout)
    palette = _real()
    b = _yard_panels(tm, palette)

    covered = set()
    for p in b.placements:
        asset = palette.catalog.by_id(p.asset_id)
        covered |= set(covered_cells(asset, p.x, p.z, p.rot, 0.3))

    from citysmith.build import yard_cells
    yards = yard_cells(tm)
    assert yards
    for bid, cells in yards.items():
        runs = boundary_runs(tm, cells, {c for cs in yards.values() for c in cs},
                             WAYS, skip_ways=False)
        edge = {(v, r[1]) if r[0] in ("n", "s") else (r[1], v)
                for r in runs for v in range(r[2], r[3] + 1)}
        assert edge - covered, f"{bid}'s yard is fenced all the way round"


def test_the_yard_boundary_is_dealt_per_tier():
    """The facade has dealt a kit per tier for a long time and the yard dealt
    3.4 ft of paling round a temple, a smithy and a cottage alike."""
    palette = _real()
    got = {tier: yard_boundary(palette, f"{kind}-0001").name
           for tier, kind in (("civic", "temple"), ("trade", "smithy"),
                              ("common", "house"), ("utility", "shed"))}
    assert len(set(got.values())) == 4, f"four tiers, four boundaries: {got}"
    for kind, tier in (("temple", "civic"), ("smithy", "trade"),
                       ("house", "common"), ("shed", "utility")):
        assert tier_of(f"{kind}-0001") == tier
        assert yard_boundary(palette, f"{kind}-0001") is palette.resolve(
            YARD_BOUNDARY[tier])


def test_the_tall_field_wall_is_always_the_tall_one():
    """`resolve` seeds its choice INSIDE the first matching query, so one query
    listing two names is a coin flip: `--fence-style drystone-tall` dealt the
    ordinary wall on five seeds in eight."""
    catalog = load_or_build()
    plain = Palette(catalog, MEDIEVAL).require("field_wall")
    for seed in range(12):
        palette = Palette.named(catalog, "medieval", seed)
        tall = palette.require("field_wall_tall")
        assert tall.size_y > plain.size_y, (
            f"seed {seed}: field_wall_tall resolved to {tall.name!r}, "
            f"which is no taller than the ordinary wall")


def test_the_field_wall_post_is_always_a_post():
    """Same shape of bug, and worse: the fallback in that query was a
    1.98-long wall panel, so a seed that picked it put a full panel across
    every vertex instead of a post."""
    catalog = load_or_build()
    for seed in range(12):
        post = Palette.named(catalog, "medieval", seed).require("field_wall_post")
        assert max(post.size_x, post.size_z) < 1.0, (
            f"seed {seed}: field_wall_post resolved to {post.name!r}, "
            "which is a panel and not a joint")


def test_a_collinear_lap_is_not_excused_as_a_corner():
    """`_prop_collisions` allowed any boundary overlap no deeper than a panel's
    own thickness, meaning to allow corners. Two panels lying along the same
    line separate on their thin axis first, so a half-length lap measures
    exactly the same -- and the check waved through every doubled fence on
    every board this project has built."""
    palette = _real()
    piece = palette.require("yard_fence")
    plen = max(piece.size_x, piece.size_z)

    b = Builder(palette, 0)
    b.add(place_centered(piece, 10.0, 10.0, 0.5, 0), prop=True)
    b.add(place_centered(piece, 10.0 + plen / 2, 10.0, 0.5, 0), prop=True)
    assert V._prop_collisions(b), "a collinear lap must be reported"

    turn = Builder(palette, 0)
    turn.add(place_centered(piece, 10.0, 10.0, 0.5, 0), prop=True)
    turn.add(place_centered(piece, 10.0 + plen / 2, 10.0, 0.5, 6), prop=True)
    assert not V._prop_collisions(turn), "a corner is a join, not a clash"


def test_a_yard_quad_is_not_sheeted_in_grass():
    """`_lay_terrain` keys its 2x2 block on the surface CLASS after checking
    the four cells agree on their ROLE -- so a quad of four cells that all
    agree on `lane_earth` passed the check and was laid in grass anyway.
    Between 41% and 60% of every yard on all four towns came out as lawn."""
    layout = _town()
    tm = R.rasterize(layout)
    palette = _real()
    b = build_from_tilemap(tm, palette, storeys=1, seed=0, layout=layout)

    from citysmith.build import yard_cells
    cells = {c for cs in yard_cells(tm).values() for c in cs}
    assert cells

    lawn = {palette.require("ground").id, palette.require("ground_2x2").id}
    for p in b.placements:
        asset = palette.catalog.by_id(p.asset_id)
        if asset is None or asset.kind != "tile" or p.y > 0.01:
            continue
        if p.asset_id not in lawn:
            continue
        w = max(1, int(round(asset.size_x)))
        d = max(1, int(round(asset.size_z)))
        covered = {(int(round(p.x)) + i, int(round(p.z)) + j)
                   for i in range(w) for j in range(d)}
        assert not (covered & cells), (
            f"{asset.name} at ({p.x}, {p.z}) sheets a yard cell in lawn")
