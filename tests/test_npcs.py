"""Where the people stand, and what must never be true of it.

A town board carried no people at all until now: `interior.occupants` derived
*who* was in each building and nothing said *where*, so the roster existed only
inside a scene. These are the invariants that keep the marks usable rather than
decorative -- above all that nothing stands in a doorway, because a slab has no
physics and a token dropped in a door is a door that does not open.
"""

from __future__ import annotations

from citysmith import npcs as N
from citysmith import raster as R
from citysmith.layout import Layout, LayoutBuilding, LayoutRoad, settlement_band


def _town(*, houses=8, trades=(), width=80, depth=80, walls=(), gates=()):
    layout = Layout(name="npc probe", source="ftg")
    layout.width, layout.depth = float(width), float(depth)
    # A grid sized to the board, so a 300-building fixture does not lay half
    # its houses off the south edge and quietly become a village.
    cols = max(1, int((width - 12) // 14))
    n = 0
    for i in range(houses):
        x, z = 6 + (i % cols) * 14, 8 + (i // cols) * 16
        n += 1
        layout.buildings.append(LayoutBuilding(
            id=f"house-{n:04d}",
            ring=[(x, z), (x + 8, z), (x + 8, z + 8), (x, z + 8)],
            kind="house", floors=1,
        ))
    trade_z = 8 + (houses // cols + 1) * 16
    for j, kind in enumerate(trades):
        x, z = 6 + (j % cols) * 14, trade_z
        n += 1
        layout.buildings.append(LayoutBuilding(
            id=f"{kind}-{n:04d}",
            ring=[(x, z), (x + 9, z), (x + 9, z + 9), (x, z + 9)],
            kind=kind, floors=1,
        ))
    layout.roads = [LayoutRoad(points=[(0.0, 40.0), (float(width), 40.0)])]
    layout.walls = [list(w) for w in walls]
    layout.gates = list(gates)
    return layout


def _pop(layout, **kw):
    tm = R.rasterize(layout)
    return tm, N.posts(tm, layout, **kw)


def test_nobody_stands_in_a_doorway():
    """The rule the furnishing pass learned indoors, and it matters more out
    here: a mark in a threshold is a door the party cannot open, for the whole
    session, with nobody able to move it."""
    layout = _town(houses=10, trades=("smithy", "shop", "tavern"))
    tm, pop = _pop(layout, seed=3)
    assert pop.posts, "the fixture needs somebody on it to be meaningful"

    blocked = N._door_cells(tm)
    assert blocked, "the fixture needs doors"
    standing = [(p.name, p.x, p.z) for p in pop.posts if (p.x, p.z) in blocked]
    assert not standing, f"posts in a doorway or the cell it opens onto: {standing[:5]}"


def test_nobody_stands_inside_a_building_or_in_the_water():
    layout = _town(houses=10, trades=("smithy",))
    tm, pop = _pop(layout, seed=3)
    inside = [(p.name, p.x, p.z) for p in pop.posts if tm.building[p.z][p.x]]
    assert not inside, f"posts inside a building shell: {inside[:5]}"
    wet = [(p.name, p.x, p.z) for p in pop.posts
           if tm.surface[p.z][p.x] == R.WATER]
    assert not wet, f"posts standing in water: {wet[:5]}"


def test_two_people_never_share_a_cell():
    layout = _town(houses=12, trades=("smithy", "shop", "tavern", "stable"))
    _, pop = _pop(layout, seed=5)
    cells = [(p.x, p.z) for p in pop.posts]
    assert len(cells) == len(set(cells))


def test_a_hamlet_gets_no_street_watch():
    """At one guard per 60 tiles of main street a 35-building hamlet came out
    with 12 watchmen against 10 people at work -- a garrison. The watch is a
    function of the settlement band, and a hamlet's is nobody."""
    layout = _town(houses=8)
    assert settlement_band(len(layout.buildings)) == "hamlet"
    _, pop = _pop(layout, seed=1)
    assert pop.of(N.GUARD) == []


def test_a_town_gets_a_watch_on_its_main_street():
    layout = _town(houses=300, width=460, depth=460)
    assert settlement_band(len(layout.buildings)) == "town"
    tm, pop = _pop(layout, seed=1)
    if not any(tm.street_class[z][x] == R.MAIN_ROAD
               for z in range(tm.depth) for x in range(tm.width)):
        import pytest
        pytest.skip("fixture produced no main street to patrol")
    assert pop.of(N.GUARD), "a town with a main street should have a watch on it"


def test_a_trade_puts_somebody_outside_and_most_houses_do_not():
    """`interior.occupants` deals the whole household, which is right for a
    scene and wrong out here: marking all of them put 115 posts on a
    35-building hamlet and 3,927 on East Tradebourne -- every family in town
    standing in its own front garden at once."""
    trades = _town(houses=0, trades=("smithy", "shop", "tavern", "stable"))
    houses = _town(houses=4)
    _, with_trades = _pop(trades, seed=2)
    _, only_houses = _pop(houses, seed=2)

    per_trade = len(with_trades.of(N.WORKING)) / 4
    per_house = len(only_houses.of(N.WORKING)) / 4
    assert per_trade > per_house, (
        f"a trade should be more likely to have somebody outside than a house "
        f"({per_trade:.2f} vs {per_house:.2f})"
    )


def test_an_off_duty_person_is_not_standing_at_their_own_building():
    """The definition. Somebody has to actually leave their workshop, which is
    why the two populations are allocated together rather than each building
    filling its own frontage."""
    layout = _town(houses=14, trades=("tavern", "shop"))
    tm, pop = _pop(layout, seed=4)
    idle = pop.of(N.OFF_DUTY)
    if not idle:
        import pytest
        pytest.skip("this seed sent nobody out")
    for p in idle:
        own = {(x, z) for z in range(tm.depth) for x in range(tm.width)
               if tm.building[z][x] == p.building}
        near_own = any(abs(p.x - x) <= 1 and abs(p.z - z) <= 1 for x, z in own)
        assert not near_own, (
            f"{p.name} is 'off duty' while standing against {p.building}"
        )


def test_the_budget_keeps_guards_before_idlers():
    """A cap has to drop the least load-bearing first: an off-duty drinker is
    scenery, a gate guard is the reason the party stops."""
    layout = _town(houses=300, trades=("smithy", "tavern"), width=460, depth=460)
    _, full = _pop(layout, seed=1)
    if len(full.posts) < 10:
        import pytest
        pytest.skip("fixture too sparse to trim")
    _, capped = _pop(layout, seed=1, budget=5)
    assert len(capped.posts) == 5
    assert len(capped.of(N.GUARD)) >= min(5, len(full.of(N.GUARD)))


def test_the_manifest_carries_who_as_well_as_where():
    """A slab carries no creatures, so the board says *where* and this says
    *who*. A position with no name is not a position anyone can use."""
    layout = _town(houses=10, trades=("smithy", "tavern"))
    _, pop = _pop(layout, seed=3)
    doc = N.manifest(pop)
    assert doc["summary"]
    assert doc["posts"]
    for row in doc["posts"]:
        assert row["name"] and row["role"] and row["duty"]
        assert isinstance(row["x"], int) and isinstance(row["z"], int)


def test_outdoor_doings_are_not_the_indoor_ones():
    """`interior._DOING` deals 'asleep in a chair' and 'writing, and covering
    it when anyone passes' -- fine across a tavern table, nonsense for somebody
    standing in a lane. It shipped that way for one build."""
    from citysmith import interior as I

    layout = _town(houses=14, trades=("smithy", "shop", "tavern"))
    _, pop = _pop(layout, seed=3)
    outdoors = {p.doing for p in pop.posts if p.doing}
    assert outdoors, "the fixture needs somebody doing something"
    assert not (outdoors & set(I._DOING)), (
        f"indoor phrasing leaked outdoors: {sorted(outdoors & set(I._DOING))}"
    )
