"""What a building is built from, inside as well as out.

The rule these all serve is one this project has relearned at three different
scales: **a building comes out of one kit**. A facade that changes material at
the corner reads as a mistake; so does a stone temple with timber partitions,
and so does dressed masonry standing on floorboards.
"""

from __future__ import annotations

import pytest

from citysmith import interior
from citysmith.build import build_interior, interior_fabric, _kit_of
from citysmith.layout import Layout, LayoutBuilding, LayoutRoad


def _square(x, z, w, d):
    return [(x, z), (x + w, z), (x + w, z + d), (x, z + d), (x, z)]


@pytest.fixture
def town() -> Layout:
    lay = Layout(name="Graybank", source="ftg", width=80, depth=80)
    lay.buildings = [
        LayoutBuilding(id="temple-0001", ring=_square(10, 10, 14, 10), kind="temple",
                       floors=2, name="Chapel of Hermes", stone=True),
        LayoutBuilding(id="tavern-0002", ring=_square(30, 10, 9, 7), kind="tavern",
                       floors=2, name="The Halfling and the Fox"),
        LayoutBuilding(id="warehouse-0003", ring=_square(45, 10, 14, 8),
                       kind="warehouse", floors=3, name="Mule Depot"),
        LayoutBuilding(id="shop-0004", ring=_square(62, 10, 8, 6), kind="shop",
                       floors=2, name="Stone Spice", stone=True),
    ]
    lay.roads = [LayoutRoad(points=[(0, 5), (80, 5)], width=2.0, kind="road")]
    return lay


def _built(town, palette, ref, **kw):
    b = interior.find(town, ref)
    fp = interior.plan(town, b, seed=33, **kw)
    return b, fp, build_interior(fp, palette, seed=33, stack=False,
                                 tier=interior.tier_for(b))


def _kits(builder, predicate=None):
    """The set of kits every placed *tile* came from."""
    kits = set()
    for p in builder.placements:
        asset = builder.byid.get(p.asset_id)
        if asset is None or asset.kind != "tile":
            continue
        if predicate and not predicate(asset):
            continue
        kits.add(_kit_of(asset))
    return kits


# -- the fabric is one kit ----------------------------------------------------

def test_a_civic_interior_is_built_of_one_kit(town, catalog_palette):
    """A temple in dressed stone was standing on Rural planking and divided by
    Village timber -- three kits in one room."""
    _b, _fp, builder = _built(town, catalog_palette, "temple-0001")
    kits = _kits(builder, lambda a: a.group_tag in ("wall", "corner", "floor"))
    # Doors are their own kit in this pack, and the apron is not built here.
    assert kits == {"castle fortified"}, kits


def test_a_common_interior_is_built_of_its_own_kit(town, catalog_palette):
    _b, _fp, builder = _built(town, catalog_palette, "tavern-0002")
    kits = _kits(builder, lambda a: a.group_tag in ("wall", "corner"))
    assert kits == {"tavern"}, kits


def test_the_partition_matches_the_wall_it_stands_between(catalog_palette):
    """Where the declared interior panel belongs to another kit, the wall
    itself is used: a plain partition of the right material beats a detailed
    one of the wrong material."""
    for tier in ("civic", "trade", "common", "utility"):
        fabric = interior_fabric(catalog_palette, tier)
        assert _kit_of(fabric.partition) == _kit_of(fabric.wall), tier
        assert _kit_of(fabric.floor) == _kit_of(fabric.wall) or tier != "civic"


def test_a_corner_from_another_kit_is_dropped_rather_than_placed(catalog_palette):
    """The rule `_usable_corner` established for the town facade, applied
    inside: a corner is only used when it comes from the wall's own kit."""
    for tier in ("civic", "trade", "common", "utility"):
        fabric = interior_fabric(catalog_palette, tier)
        if fabric.corner is not None:
            assert _kit_of(fabric.corner) == _kit_of(fabric.wall), tier
            assert fabric.corner.size_y == fabric.wall.size_y, tier


# -- stone wins over trade ----------------------------------------------------

def test_a_stone_building_is_built_in_stone_whatever_trade_it_is(town):
    """FTG says `material: STONE_BRICK` per building. A stone shop is a stone
    building; only four across the three towns carry it, which makes it a rare
    case that is free to get right."""
    assert interior.tier_for(interior.find(town, "shop-0004")) == "civic"
    assert interior.tier_for(interior.find(town, "tavern-0002")) == "trade"
    assert interior.tier_for(interior.find(town, "warehouse-0003")) == "utility"


# -- windows ------------------------------------------------------------------

def test_an_interior_has_windows_at_all(town, catalog_palette):
    """Every wall of every interior was blind, on a board whose whole point is
    being looked into."""
    _b, _fp, builder = _built(town, catalog_palette, "tavern-0002")
    glazed = [p for p in builder.placements
              if "window" in getattr(builder.byid.get(p.asset_id), "name", "").lower()]
    assert glazed, "no window anywhere in the shell"


def test_the_back_of_a_building_is_never_glazed(town, catalog_palette):
    """The asymmetry is the point: glass on the street, a blank wall at the
    back. Without it a building looks the same from all four sides."""
    b, fp, builder = _built(town, catalog_palette, "tavern-0002")
    front = next(d.side for d in fp.doors if d.exterior)
    back = {"n": "s", "s": "n", "e": "w", "w": "e"}[front]
    rect = fp.rect_on(0)
    edge = {"n": rect.z, "s": rect.z2 - 1, "w": rect.x, "e": rect.x2 - 1}[back]
    axis = 1 if back in ("n", "s") else 0

    for p in builder.placements:
        asset = builder.byid.get(p.asset_id)
        if asset is None or "window" not in asset.name.lower():
            continue
        here = p.z if axis else p.x
        assert abs(here - edge) > 0.6, f"a window on the back wall at {p.x},{p.z}"


def test_a_barn_has_no_glass_in_it(town, catalog_palette):
    """A warehouse with windows stops being a warehouse -- and the Rural kit
    has no 1-cell window at all, which is why it is the right kit here."""
    _b, _fp, builder = _built(town, catalog_palette, "warehouse-0003")
    for p in builder.placements:
        asset = builder.byid.get(p.asset_id)
        assert asset is None or "window" not in asset.name.lower()


# -- storeys ------------------------------------------------------------------

def test_a_barn_is_one_storey_however_many_the_layout_invented(town):
    b = interior.find(town, "warehouse-0003")
    assert b.floors == 3
    assert interior.storeys_for(b, 3) == 1


def test_the_cap_still_caps(town):
    b = interior.find(town, "tavern-0002")
    assert interior.storeys_for(b, 1) == 1
    assert interior.storeys_for(b, 3) == 2      # the layout says two


def test_a_three_storey_building_gets_three_levels():
    b = LayoutBuilding(id="guildhall-0009", ring=_square(0, 0, 12, 9),
                       kind="guildhall", floors=3, name="The Bureau")
    assert interior.storeys_for(b, 3) == 3
    assert interior.storeys_for(b, 2) == 2, "the cap is still a cap"


# -- corners ------------------------------------------------------------------

def test_a_corner_cell_gets_one_piece_not_two_panels(town, catalog_palette):
    """Two wall ends in one square is the doubled geometry the corner piece
    exists to avoid, and hand-built community slabs never contain it."""
    _b, fp, builder = _built(town, catalog_palette, "temple-0001")
    rect = fp.rect_on(0)
    corners = {(rect.x, rect.z), (rect.x2 - 1, rect.z),
               (rect.x, rect.z2 - 1), (rect.x2 - 1, rect.z2 - 1)}

    from citysmith.build import cell_of
    walls = 0
    for p in builder.placements:
        asset = builder.byid.get(p.asset_id)
        if asset is None or asset.group_tag not in ("wall", "corner"):
            continue
        if cell_of(p, asset) in corners and p.y < 3.0:
            walls += 1
    assert walls == 4, f"{walls} pieces on four ground-floor corners"


# -- prop vocabulary ----------------------------------------------------------


def _medieval_palette():
    from citysmith.catalog import load_or_build
    from citysmith.palette import Palette
    return Palette.named(load_or_build(), "medieval", 0)


def test_no_seasonal_or_grim_prop_is_reachable():
    """Every prop query in the palette is a free-text term search, which is the
    one thing CLAUDE.md's hard constraints say not to do -- and nothing enforced
    it on props, so a temple dealt `Altar (Evil)`, a hamlet's woodland dealt
    `Tree, Festive`, and a house dealt `Table Torture 01`. Found by reading a
    yard's clutter list and seeing four kinds of bed standing outdoors.
    """
    from citysmith.palette import MEDIEVAL

    palette = _medieval_palette()
    reachable = set()
    for queries in MEDIEVAL.props.values():
        for terms, kwargs in queries:
            reachable.update(a.name for a in palette.catalog.find(*terms, **kwargs))
    assert reachable, "the fixture needs a catalog with props in it"

    offenders = [
        n for n in reachable
        if any(bad in n.lower() for bad in
               ("festive", "pumpkin", "turkey", "snow flake", "(evil)",
                "torture", "aberration", "moorgoth"))
    ]
    assert not offenders, f"seasonal or grim props still reachable: {offenders[:6]}"


def test_every_prop_query_matches_something():
    """`Palette.prop` picks one query at random and returns None when it matches
    nothing, so a dead query does not fail -- it silently thins the category at
    a rate nobody would notice. Tightening the exclusions emptied
    `temple/altar` outright and only a hand count caught it."""
    palette = _medieval_palette()
    dead = [p for p in palette.validate() if "matches\nnothing" in p or "matches " in p]
    assert not dead, dead
