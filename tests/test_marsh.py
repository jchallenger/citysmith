"""Wetland: a surface class, its fabric, and the traps found building it.

A fen was the one landscape citysmith could not say. `swamp` sits on
`palette._WRONG_SETTING` -- correctly, because a free-text query for a floor
has no business wandering into another biome -- so every swamp asset in the
Nature kit was unreachable and a marsh built as ordinary ground with a pond in
it.

The pass that fixed it is small. What is worth pinning is the handful of
decisions inside it that are easy to get backwards, and each test below is one
of them.
"""

from __future__ import annotations

from citysmith import raster as R
from citysmith.build import build_from_tilemap
from citysmith.catalog import load_or_build
from citysmith.layout import Layout, LayoutArea, LayoutBuilding, LayoutRoad
from citysmith.palette import MEDIEVAL, Palette
from citysmith.verify import feature_report


def _palette():
    return Palette(load_or_build(), MEDIEVAL)


def _rect(x0, z0, x1, z1):
    return [(float(x0), float(z0)), (float(x1), float(z0)),
            (float(x1), float(z1)), (float(x0), float(z1))]


def _fen(*, pool=True, road=False, width=60, depth=60):
    """A hamlet with a fen down its west side, and a pool in the fen."""
    layout = Layout(name="fen")
    layout.width, layout.depth = float(width), float(depth)
    for i in range(3):
        x = 34 + i * 8
        layout.buildings.append(LayoutBuilding(
            id=f"house-{i + 1:04d}",
            ring=_rect(x, 26, x + 5, 31), kind="house", floors=1,
        ))
    layout.areas.append(LayoutArea("marsh", _rect(2, 6, 26, 52)))
    if pool:
        layout.areas.append(LayoutArea("water", _rect(8, 16, 18, 30)))
    if road:
        # A causeway running west out of the village and straight into the fen.
        layout.roads.append(LayoutRoad(points=[(58.0, 40.0), (4.0, 40.0)],
                                       width=3.0))
    return layout


# -- the raster ---------------------------------------------------------------

def test_a_marsh_area_becomes_marsh_cells():
    tm = R.rasterize(_fen(pool=False))
    wet = sum(1 for row in tm.surface for v in row if v == R.MARSH)
    assert wet > 400, f"the fen should cover most of its polygon, got {wet}"


def test_water_is_painted_over_marsh_and_not_the_other_way_round():
    """Paint order, and it is the difference between a fen with pools in it
    and a fen with no pools at all. Marsh is laid first *because* the pools
    sit in the hollows of it."""
    tm = R.rasterize(_fen(pool=True))
    # The pool's own middle must be open water, not wet ground.
    assert tm.surface[23][13] == R.WATER
    # And it must still be surrounded by fen.
    assert tm.surface[10][13] == R.MARSH


def test_a_track_can_cross_the_fen():
    """`MARSH` has to be in the street pass's `over` set. Left out, every way
    into a wetland stops dead at its edge -- a silently dropped feature, which
    is the failure this project records more often than any other."""
    tm = R.rasterize(_fen(pool=False, road=True))
    crossing = [x for x in range(2, 26) if tm.surface[40][x] == R.STREET]
    assert len(crossing) > 15, (
        f"the causeway only reached {len(crossing)} cells into the fen")


# -- what a marsh is, and is not ----------------------------------------------

def test_a_marsh_is_wadeable_but_is_not_a_way():
    """Two sets, two questions. A fen is wadeable, so a party can cross it;
    it is not public open space, so no front door opens onto it and the
    street network is never routed through it.

    `WALKABLE` is the descriptive set and nothing in the package reads it;
    `is_walkable` gates on `OPEN`. Both are asserted because the pair is
    genuinely confusing -- it produced a wrong assertion in this very file.
    """
    assert R.MARSH in R.WALKABLE
    assert R.MARSH not in R.OPEN

    tm = R.rasterize(_fen(pool=False))
    marsh = next((x, z) for z in range(tm.depth) for x in range(tm.width)
                 if tm.surface[z][x] == R.MARSH)
    assert not tm.is_walkable(*marsh)


def test_nobody_is_left_standing_in_the_bog():
    from citysmith import npcs

    tm = R.rasterize(_fen(pool=False))
    marsh = next((x, z) for z in range(tm.depth) for x in range(tm.width)
                 if tm.surface[z][x] == R.MARSH)
    assert not npcs._standable(tm, *marsh)


def test_a_market_square_is_never_sited_in_a_fen():
    """`_place_plaza` scores open blocks; a wetland is not one."""
    tm = R.rasterize(_fen(pool=False))
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] == R.PLAZA:
                assert tm.surface[z][x] != R.MARSH


# -- the fabric ---------------------------------------------------------------

def test_every_marsh_role_resolves_and_is_the_right_shape():
    p = _palette()
    assert p.validate() == []
    assert p.resolve("marsh").size_x == 1.0
    assert p.resolve("marsh").size_z == 1.0
    block = p.resolve("marsh_2x2")
    assert (block.size_x, block.size_z) == (2.0, 2.0)
    # Flush with grass, which is why a marsh needs no special casing in the
    # terrain pass. If this ever changes, `_lay_terrain` does too.
    assert p.resolve("marsh").size_y == p.resolve("ground").size_y


def test_the_marsh_roles_actually_vary():
    """`resolve(role, v)` seeds its choice *inside the first matching query*
    and stops there, so a list of one-name queries pins every variant to the
    first name. That is how the wall deal came out "5 civic, 46 identical",
    and it was reproduced here before being spotted: the whole fen laid in a
    single tile and every reed the same silhouette."""
    p = _palette()
    for role, least in (("marsh_2x2", 2), ("marsh_reed", 2), ("marsh_lily", 2)):
        names = {p.resolve(role, v).name for v in range(8)}
        assert len(names) >= least, f"{role} resolved to {names}"


def _built(tm, p):
    builder = build_from_tilemap(tm, p, storeys=1)
    return builder, {pl.asset_id for pl in builder.placements}


def test_the_ground_of_a_fen_is_laid_in_wetland_tiles():
    """The terrain half, asserted on `marsh_2x2` **specifically**.

    Not on the `marsh` role: that resolves to `Swamp floor 1x1`, the same
    asset `lane_earth` uses, so any board with one trodden lane on it would
    satisfy the check while the fen was laid in grass. `FEATURE_ROLES` carries
    a comment about that trap; the first version of this test walked straight
    into it and passed with the terrain pass torn out.
    """
    p = _palette()
    _, used = _built(R.rasterize(_fen()), p)
    block = p.resolve("marsh_2x2")
    assert block is not None and block.id in used, \
        "the fen's ground was not laid in wetland tiles"


def test_a_fen_grows_reeds():
    """The dressing half, and it fails for its own reason."""
    p = _palette()
    _, used = _built(R.rasterize(_fen()), p)
    reeds = {a.id for v in range(6)
             for a in (p.resolve("marsh_reed", v),) if a is not None}
    assert used & reeds, "nothing was growing in the fen"


# -- the feature report -------------------------------------------------------

def test_a_fen_that_built_as_ordinary_ground_fails_loudly():
    """The branch that matters. Wetland cells on the map and no wetland
    fabric in the build means the marsh pass is broken or switched off, and
    it must not read as a pass."""
    p = _palette()
    tm = R.rasterize(_fen())
    builder = build_from_tilemap(tm, p, storeys=1)

    wet = {a.id for r in ("marsh_2x2", "marsh_reed", "marsh_lily")
           for a in (p.resolve(r),) if a is not None}
    builder.placements = [pl for pl in builder.placements
                          if pl.asset_id not in wet]

    level, _, detail = next(
        r for r in feature_report(builder, tm, _fen()) if r[1] == "marsh")
    assert level == "fail", detail
    assert "ordinary ground" in detail


def test_a_crop_with_no_fen_in_it_says_so_rather_than_failing():
    layout = _fen()
    whole = R.rasterize(layout)
    tm = whole.crop(30, 20, 28, 20)
    assert not any(v == R.MARSH for row in tm.surface for v in row)

    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "marsh")
    assert level == "pass"
    assert "outside this crop" in detail, detail


def test_a_dry_town_reports_no_wetland_in_the_source():
    layout = Layout(name="dry")
    layout.width, layout.depth = 40.0, 40.0
    layout.buildings.append(LayoutBuilding(
        id="house-0001", ring=_rect(10, 10, 16, 16), kind="house", floors=1))
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, _palette(), storeys=1)
    level, _, detail = next(
        r for r in feature_report(builder, tm, layout) if r[1] == "marsh")
    assert level == "pass"
    assert "none in the source" in detail


# -- the importer -------------------------------------------------------------

def test_an_unmapped_wetland_is_reported_rather_than_dropped():
    """The rule for a growing vocabulary: map what is known, default the rest,
    and *say so*. A dropped background is invisible on the board."""
    from citysmith.ftg import BACKGROUND_AREAS, DEFAULT_BACKGROUND_AREA

    assert BACKGROUND_AREAS["MARSH"] == "marsh"
    assert BACKGROUND_AREAS["SWAMP"] == "marsh"
    assert "FEN" not in BACKGROUND_AREAS
    assert DEFAULT_BACKGROUND_AREA == "park"
