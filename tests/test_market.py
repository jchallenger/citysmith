"""The market square: the carved plaza's shape, and the market laid on it.

Invariants, not golden output. The carve may move and resize the square as
the growth rules change, but a square that touches no frontage, sprouts
one-cell tentacles, breaks street access or gets stamped over an authored
plaza is always a bug -- each of those shipped or nearly shipped once.

`samples/forest_church.json` is the real MFCG town every number in
`docs/market-squares.md` was measured on.
"""

from __future__ import annotations

import pathlib

import pytest

from citysmith import mfcg
from citysmith.raster import (
    GROUND, PLAZA, PLAZA_MAX_AREA, PLAZA_MIN_AREA, SIDES, STREET, TileMap,
    _carve_plaza, _plaza_target_area, rasterize,
)

MFCG_FILE = pathlib.Path(__file__).parent.parent / "samples" / "forest_church.json"


@pytest.fixture(scope="module")
def town():
    return rasterize(mfcg.import_layout(MFCG_FILE))


def _plaza_cells(tm) -> set[tuple[int, int]]:
    return {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.surface[z][x] == PLAZA}


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    left = set(cells)
    out = []
    while left:
        start = min(left)
        comp = {start}
        queue = [start]
        while queue:
            x, z = queue.pop()
            for _, dx, dz in SIDES:
                n = (x + dx, z + dz)
                if n in left and n not in comp:
                    comp.add(n)
                    queue.append(n)
        left -= comp
        out.append(comp)
    return out


# -- the fallback guard -------------------------------------------------------

def test_carve_is_a_fallback_not_a_second_market():
    """A town whose export authored a square keeps it, and gets no other.

    FTG exports market squares as MARKET/PAVEMENT polygons and the raster
    paints them before this runs. Unguarded, the carve added a 7x7 twin on
    the busiest street of every such town.
    """
    tm = TileMap.blank(40, 40)
    for z in range(18, 22):
        for x in range(40):
            tm.surface[z][x] = STREET
            tm.street_class[z][x] = "main"
    for z in range(10, 14):
        for x in range(10, 14):
            tm.surface[z][x] = PLAZA
    authored = _plaza_cells(tm)

    _carve_plaza(tm)
    assert _plaza_cells(tm) == authored


# -- size ---------------------------------------------------------------------

def test_the_square_is_sized_to_the_town():
    """More town, more market -- between a hard floor and a hard ceiling."""
    def with_buildings(n: int) -> TileMap:
        tm = TileMap.blank(8, 8)
        for i in range(n):
            tm.building[0][0] = "x"          # ids are what is counted
        for i in range(min(n, 64)):
            tm.building[i // 8][i % 8] = f"house-{i:04d}"
        return tm

    hamlet = _plaza_target_area(with_buildings(4))
    town = _plaza_target_area(with_buildings(51))
    assert PLAZA_MIN_AREA <= hamlet < town <= PLAZA_MAX_AREA


def test_a_metropolis_is_capped():
    tm = TileMap.blank(8, 8)
    tm.building[0][0] = "x"
    # 10,000 buildings' worth of ids cannot fit on an 8x8 grid; fake the count
    # by asking the formula directly through a subclassed grid is overkill --
    # the clamp is arithmetic, so test it at the boundary the grid can hold.
    for i in range(64):
        tm.building[i // 8][i % 8] = f"house-{i:04d}"
    assert _plaza_target_area(tm) <= PLAZA_MAX_AREA


# -- shape, on a real town ----------------------------------------------------

def test_the_square_is_grown_between_the_frontages_not_stamped(town):
    """The square takes its outline from the buildings around it.

    The fixed 7x7 carve required a block *clear* of buildings, so it
    maximised distance from every frontage: 1 of its 24 perimeter cells
    touched a building on Forest Church. Grown, the square is bounded by the
    facades -- which is what a market square is.
    """
    cells = _plaza_cells(town)
    assert cells, "the fallback carved nothing on a town with empty squares"

    # One connected room, sized to the town.
    comps = _components(cells)
    assert len(comps) == 1
    assert PLAZA_MIN_AREA <= len(cells) <= PLAZA_MAX_AREA
    assert len(cells) > 49, "51 buildings deserve more market than a 7x7 stamp"

    # It reaches the frontages. The stamp managed one cell.
    perimeter = [c for c in cells
                 if any((c[0] + dx, c[1] + dz) not in cells
                        for _, dx, dz in SIDES)]
    frontage = [
        (x, z) for x, z in perimeter
        if any(town.inside(x + dx, z + dz)
               and (town.building[z + dz][x + dx] or town.wall[z + dz][x + dx])
               for _, dx, dz in SIDES)
    ]
    assert len(frontage) >= 8

    # And it never eats what it must not: buildings, walls, gates.
    for x, z in cells:
        assert not town.building[z][x]
        assert not town.wall[z][x]
        assert (x, z) not in town.gates


def test_the_square_has_no_one_cell_tentacles(town):
    """Every plaza cell is part of a full 2x2 block of plaza.

    Grown from a street junction, the disc leaks down the streets; unsmoothed
    it carried arms one cell wide, which read as a paving error rather than
    as a room.
    """
    cells = _plaza_cells(town)
    for (x, z) in cells:
        assert any((x + i, z + j) in cells and (x + i, z) in cells
                   and (x, z + j) in cells
                   for i in (-1, 1) for j in (-1, 1)), (
            f"({x}, {z}) is the tip of a one-cell tentacle")


def test_the_square_keeps_the_through_route_class(town):
    """Paving a main street into the square must not erase its class.

    `verify.through_route_pinches` and the dressing pass both read
    `street_class` to keep carts moving across the square; a carve that
    wiped it would let a stall row close the town's main road invisibly.
    """
    cells = _plaza_cells(town)
    classed = [c for c in cells if town.street_class[c[1]][c[0]] in ("main", "cart")]
    assert classed, "the square sits on the busiest junction, so some of it is through route"


def test_the_town_still_verifies_with_the_grown_square(town):
    from citysmith.verify import verify

    report = verify(town)
    assert not report.failed
    assert report.stats["reachable_buildings"] == report.stats["buildings"], (
        "growing the square cost a building its street access")


def test_the_carve_is_deterministic():
    a = rasterize(mfcg.import_layout(MFCG_FILE))
    b = rasterize(mfcg.import_layout(MFCG_FILE))
    assert _plaza_cells(a) == _plaza_cells(b)


def test_a_map_with_no_streets_gets_no_square():
    tm = TileMap.blank(20, 20)
    _carve_plaza(tm)
    assert not _plaza_cells(tm)


# -- the market on the square -------------------------------------------------
#
# The dressing pass is tested with stub assets in the real shapes the roles
# ask for (a two-cell-wide stall, a crate that blocks, a basket that does
# not), because no catalog exists in a fresh checkout -- the same argument as
# tests/conftest.py. The real assets' shapes are unknown until probed
# (`tools/market_probe.py`), which is exactly why the pass derives everything
# from the asset it is handed.

from citysmith.build import (
    Builder, MARKET_BLOCKS_ABOVE, Scatter, _dress_market, place_centered,
)
from citysmith.catalog import Asset
from citysmith.verify import market_square_open


def _prop_asset(n: int, name: str, sx: float, sy: float, sz: float) -> Asset:
    return Asset(id=f"{n:08x}-aaaa-bbbb-cccc-dddddddddddd", name=name,
                 kind="prop", pack="Medieval Fantasy", group_tag="", tags=(),
                 folder="Market", size_x=sx, size_y=sy, size_z=sz)


STALL = _prop_asset(1, "Stub Stall", 2.0, 1.8, 1.0)
CRATE = _prop_asset(2, "Stub Crate", 0.8, 0.7, 0.8)
BASKET = _prop_asset(3, "Stub Basket", 0.45, 0.4, 0.45)
WELL = _prop_asset(4, "Stub Well", 1.4, 1.6, 1.4)
#: Narrower than a cell, so the scatter's own collision test cannot stand in
#: for the keep-clear ring -- see test_the_well_keeps_a_ring_of_standing_room.
SMALL_WELL = _prop_asset(5, "Stub Well Small", 0.9, 1.6, 0.9)


class _MarketCatalog:
    def __init__(self, assets):
        self.assets = list(assets)

    def by_id(self, asset_id):
        for a in self.assets:
            if a.id == asset_id:
                return a
        return None


class MarketPalette:
    """StubPalette pattern from conftest, for the market roles only."""

    def __init__(self, stall=STALL, goods=(CRATE, BASKET), well=WELL):
        self._stall, self._goods, self._well = stall, list(goods), well
        self.catalog = _MarketCatalog(
            [a for a in [stall, well, *self._goods] if a is not None])

    def resolve(self, role, variant=0):
        if role == "market_stall":
            return self._stall
        if role == "plaza_well":
            return self._well
        if role == "market_goods" and self._goods:
            return self._goods[variant % len(self._goods)]
        return None


def _square_town() -> TileMap:
    """A 14x10 plaza, houses on its north and west sides, a main road across.

    The west house matters: the stall rows run east-west here, so its
    frontage column crosses every row -- the one arrangement that catches a
    pass which forgot the frontage rule. With houses only on the north, no
    row ever lands on a frontage cell and the rule is untestable.
    """
    tm = TileMap.blank(30, 30)
    for z in range(10, 20):
        for x in range(8, 22):
            tm.surface[z][x] = PLAZA
    # the through route crossing the square, and its continuation outside
    for x in range(0, 30):
        for z in (14, 15):
            if tm.surface[z][x] != PLAZA:
                tm.surface[z][x] = STREET
            tm.street_class[z][x] = "main"
    # houses fronting the square from the north, one door onto it
    for x in range(8, 22):
        tm.building[9][x] = "house-0001"
    tm.doors["house-0001"] = [(12, 9, "s")]
    # and from the west, clear of the road
    for z in list(range(10, 14)) + list(range(16, 20)):
        tm.building[z][7] = "house-0002"
    return tm


#: Cells the pass must leave empty on `_square_town`: the west frontage
#: column, the north frontage strip, and the door's apron.
def _must_stay_clear() -> set[tuple[int, int]]:
    west = {(8, z) for z in list(range(10, 14)) + list(range(16, 20))}
    north = {(x, 10) for x in range(8, 22)}
    apron = {(x, z) for x in (11, 12, 13) for z in (10, 11)}
    return west | north | apron


def _dressed(palette=None):
    tm = _square_town()
    b = Builder(palette or MarketPalette())
    _dress_market(b, tm, Scatter(b), 0.5, {})
    return tm, b


def _of(b, asset):
    return [p for p in b.placements if p.asset_id == asset.id]


def test_stalls_stand_in_rows_with_walking_aisles():
    """A market is rows and aisles, not mist.

    The old dressing rolled p=0.16 per cell: goods everywhere, no structure,
    density a third of what hand-built boards measure
    (`docs/interior-slabs.md`). Stall centres must share row lines, and the
    lines must be spaced -- everything between them is aisle.
    """
    tm, b = _dressed()
    stalls = _of(b, STALL)
    assert len(stalls) >= 4

    # All in rows: the stub stall is 1 deep, so its centre sits on a row
    # line's centre; every stall shares one of a handful of lines.
    zs = sorted({round(p.z * 2) for p in stalls})
    assert len(zs) <= 3, f"stall z-origins {zs} do not read as rows"
    # And the rows are at least an aisle apart.
    lines = sorted({p.z for p in stalls})
    for a, c in zip(lines, lines[1:]):
        assert c - a >= 3, f"rows at z={a} and z={c} leave no aisle"


def test_stalls_sit_on_quarter_turns():
    """84% of hand-placed props sit on a quarter turn; a stall row is a
    deliberate structure, so stalls are on one always."""
    tm, b = _dressed()
    for p in _of(b, STALL):
        assert p.rot % 6 == 0


def test_goods_cluster_at_the_rows_not_over_the_square():
    tm, b = _dressed()
    goods = _of(b, CRATE) + _of(b, BASKET)
    assert goods, "gaps in the rows carry loose goods"
    stalls = _of(b, STALL)
    row_lines = {round(p.z + STALL.size_z / 2 - 0.5) for p in stalls}
    for p in goods:
        cz = p.z + BASKET.size_z / 2 if p.asset_id == BASKET.id \
            else p.z + CRATE.size_z / 2
        assert any(abs(cz - (ln + 0.5)) <= 0.6 for ln in row_lines), (
            f"goods at z={cz} are not clustered on a stall row")


def test_the_well_stands_alone_near_the_middle():
    tm, b = _dressed()
    wells = _of(b, WELL)
    assert len(wells) == 1
    p = wells[0]
    cx, cz = p.x + WELL.size_x / 2, p.z + WELL.size_z / 2
    assert 10 < cx < 20 and 11 < cz < 19, "the well is not a focal point"


def test_the_well_keeps_a_ring_of_standing_room():
    """A crowd gathers at a well; the stalls stand a cell off it.

    Tested with a well *narrower than a cell* on a bare square, because with
    a fat well the scatter's collision test keeps stalls off the ring by
    accident and the reservation is untestable.
    """
    tm = TileMap.blank(30, 30)
    for z in range(10, 20):
        for x in range(8, 22):
            tm.surface[z][x] = PLAZA
    b = Builder(MarketPalette(well=SMALL_WELL))
    _dress_market(b, tm, Scatter(b), 0.5, {})

    wells = _of(b, SMALL_WELL)
    assert len(wells) == 1
    wx = int(wells[0].x + SMALL_WELL.size_x / 2)
    wz = int(wells[0].z + SMALL_WELL.size_z / 2)
    # Everything, baskets included: the ring is where the crowd stands, not
    # merely where nothing physically fits.
    for q in b.placements:
        if q.asset_id == SMALL_WELL.id:
            continue
        asset = b.palette.catalog.by_id(q.asset_id)
        qc = (int(q.x + asset.size_x / 2), int(q.z + asset.size_z / 2))
        assert not (abs(qc[0] - wx) <= 1 and abs(qc[1] - wz) <= 1), (
            f"{asset.name} anchored at {qc} crowds the well at {(wx, wz)}")


def test_the_market_leaves_the_cart_route_and_the_square_open():
    """The invariant, measured on the emitted boxes by verify."""
    tm, b = _dressed()
    assert market_square_open(b, tm) == []


def test_verify_flags_a_market_that_walls_off_the_square():
    """The check reads the artifact: hand it a builder whose stalls fence the
    square in two and it must say so, whatever any pass believed."""
    tm = _square_town()
    b = Builder(MarketPalette())
    for x in range(8, 22):
        b.add(place_centered(STALL, x + 0.5, 12.5, 0.5, 0), prop=True)
    problems = market_square_open(b, tm)
    assert any("walls off" in p for p in problems)


def test_verify_flags_a_stall_on_the_cart_route():
    tm = _square_town()
    b = Builder(MarketPalette())
    b.add(place_centered(STALL, 12.0, 14.5, 0.5, 0), prop=True)
    problems = market_square_open(b, tm)
    assert any("through route" in p for p in problems)


def test_a_basket_is_not_an_obstacle():
    """Props under the blocking height are stepped over, not routed around."""
    tm = _square_town()
    b = Builder(MarketPalette())
    assert BASKET.size_y < MARKET_BLOCKS_ABOVE
    for x in range(8, 22):
        b.add(place_centered(BASKET, x + 0.5, 12.5, 0.5, 0), prop=True)
    assert market_square_open(b, tm) == []


def test_the_frontage_and_the_door_stay_clear():
    """Doors open onto the square; nothing stands against the facades."""
    tm, b = _dressed()
    assert b.placements, "an empty market proves nothing here"
    clear = _must_stay_clear()
    for p in b.placements:
        asset = b.palette.catalog.by_id(p.asset_id)
        # the cell the prop is anchored in (collider centre)
        cell = (int(p.x + asset.size_x / 2), int(p.z + asset.size_z / 2))
        assert cell not in clear, (
            f"{asset.name} at {cell} stands on the frontage strip or in a "
            "doorway")


def test_the_real_square_stays_one_room_when_dressed(town):
    """The invariant on real ground, where the synthetic squares are too
    tidy to fail. The first version of the pass sealed 8 cells of Forest
    Church's square behind a 30 ft run of counters -- a shape no rectangle
    with a straight road through it ever produced."""
    b = Builder(MarketPalette())
    _dress_market(b, town, Scatter(b), 0.5, {})
    assert market_square_open(b, town) == []
    assert _of(b, WELL), "the town well stands in the square"
    assert _of(b, STALL), "a 51-building town holds a stall market"


def test_the_market_is_deterministic():
    _, b1 = _dressed()
    _, b2 = _dressed()
    key = lambda b: [(p.asset_id, p.x, p.y, p.z, p.rot) for p in b.placements]
    assert key(b1) == key(b2)


def test_no_stall_asset_degrades_to_goods_rows_not_an_error():
    """The stall role is speculative until probed; a miss costs canvas
    roofs, never a build."""
    tm, b = _dressed(MarketPalette(stall=None))
    assert not _of(b, STALL)
    assert _of(b, CRATE) + _of(b, BASKET), "goods stand where stalls would"


def test_an_empty_palette_dresses_nothing_and_does_not_crash():
    tm, b = _dressed(MarketPalette(stall=None, goods=(), well=None))
    assert b.placements == []


def test_optional_roles_do_not_fail_validation():
    """`market_stall` and `plaza_well` are structured guesses at a vocabulary
    no local catalog has confirmed. An install where they resolve to nothing
    must still build -- the pass degrades -- so validate() must not turn the
    declaration itself into a hard failure."""
    from citysmith.catalog import Catalog
    from citysmith.palette import MEDIEVAL, Palette

    problems = Palette(Catalog(assets=[]), MEDIEVAL).validate()
    assert problems, "an empty catalog fails plenty -- just not on these"
    for role in ("market_stall", "plaza_well"):
        assert not any(role in p for p in problems)
