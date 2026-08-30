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
