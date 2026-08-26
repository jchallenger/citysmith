"""A walled property is one property: one yard, no inner fence, one court.

A keep and its garrison range inside a barricade came out as two cottages
that had each fenced their own garden inside somebody else's stockade --
three boundaries deep in twenty feet, with the two halves of one property
reading as two smallholdings and no paving joining their doors.

What is pinned here is the handful of decisions that fixes it, because every
one of them was got wrong first.
"""

from __future__ import annotations

from citysmith import raster as R
from citysmith.build import FENCE_STYLES, build_from_tilemap, yard_cells
from citysmith.catalog import load_or_build
from citysmith.layout import Layout, LayoutBuilding
from citysmith.palette import MEDIEVAL, Palette


def _palette():
    return Palette(load_or_build(), MEDIEVAL, 33)


def _ring(cx, cz, rx, rz, n=16):
    """A closed polygon, first point repeated -- which is what marks it closed."""
    import math
    pts = [(cx + rx * math.cos(2 * math.pi * i / n),
            cz + rz * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


def _rect(x0, z0, x1, z1):
    return [(float(x0), float(z0)), (float(x1), float(z0)),
            (float(x1), float(z1)), (float(x0), float(z1))]


def _keep(*, enclosed=True):
    """Two buildings on a road, optionally inside a closed barricade."""
    layout = Layout(name="keep")
    layout.width, layout.depth = 70.0, 70.0
    layout.buildings.append(LayoutBuilding(
        id="barracks-0001", ring=_rect(28, 40, 38, 49), kind="barracks",
        floors=2, name="The Keep"))
    layout.buildings.append(LayoutBuilding(
        id="guildhall-0002", ring=_rect(29, 22, 38, 30), kind="guildhall",
        floors=2, name="The Garrison Range"))
    from citysmith.layout import LayoutRoad
    layout.roads.append(LayoutRoad(points=[(0.0, 14.0), (70.0, 14.0)], width=4.0))
    if enclosed:
        layout.fences = [_ring(33, 35, 18, 20)]
    else:
        # The same length of boundary, left OPEN: a field wall, not a pen.
        layout.fences = [_ring(33, 35, 18, 20)[:-1]]
    return layout


# -- what makes a compound ----------------------------------------------------

def test_a_closed_run_makes_one_property_and_an_open_one_does_not():
    """The whole test is closed-versus-open, and nothing about the kind of
    building. A field boundary encloses nothing; a perimeter is somebody's."""
    shut = R.rasterize(_keep(enclosed=True))
    open_ = R.rasterize(_keep(enclosed=False))

    assert len(set(R.compounds(shut).values())) == 1
    assert len(R.compounds(shut)) == 2, "both buildings are inside the ring"
    assert R.compounds(open_) == {}


def test_the_buildings_of_a_compound_share_one_yard():
    tm = R.rasterize(_keep())
    yards = yard_cells(tm)
    keys = [k for k in yards if k.startswith("compound-")]
    assert len(keys) == 1, f"expected one pooled yard, got {sorted(yards)}"
    assert not any(k.startswith(("barracks", "guildhall")) for k in yards), \
        "a compound's buildings must not also claim yards of their own"


def test_a_compound_is_not_fenced_again_inside_its_own_enclosure():
    """The barricade IS the yard fence. A paling round each building inside it
    is the third boundary in twenty feet, and it is what the board showed."""
    p = _palette()
    tm = R.rasterize(_keep())
    builder = build_from_tilemap(tm, p, storeys=2, fence_style="palisade")
    paling = p.resolve("yard_fence")
    assert paling is not None
    assert not any(pl.asset_id == paling.id for pl in builder.placements), \
        "yard paling was built inside an enclosure that already fences it"


# -- the court ----------------------------------------------------------------

def test_the_court_joins_every_door_in_one_piece():
    """Two failures in one test. The court has to *reach* each door, and it
    has to be one connected surface rather than an island per door."""
    tm = R.rasterize(_keep())
    court = {(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.surface[z][x] == R.COURT}
    assert court, "no court was laid inside the enclosure"

    ways = frozenset({R.STREET, R.LANE, R.PLAZA, R.PIER})
    for bid in R.compounds(tm):
        for x, z, side in tm.doors.get(bid, ()):
            dx, dz = next((d, e) for s, d, e in R.SIDES if s == side)
            got = tm.surface[z + dz][x + dx]
            assert got == R.COURT or got in ways, \
                f"{bid} {side} door opens onto {got}"

    from collections import deque
    seen = {next(iter(court))}
    queue = deque(seen)
    while queue:
        x, z = queue.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in court and n not in seen:
                seen.add(n)
                queue.append(n)
    assert seen == court, "the court came out in more than one piece"


def test_the_court_goes_round_a_building_rather_than_through_it():
    """The L-corridor this replaced ran the keep's door straight through the
    garrison range: every cell of it a building cell, every one skipped, and
    the court silently stopped short of the door it was laid for."""
    tm = R.rasterize(_keep())
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] == R.COURT:
                assert not tm.building[z][x]


def test_the_court_reaches_a_way_in():
    """A paved island inside a stockade is not a forecourt."""
    tm = R.rasterize(_keep())
    ways = frozenset({R.STREET, R.LANE, R.PLAZA, R.PIER})
    court = [(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.surface[z][x] == R.COURT]
    assert any(tm.inside(x + dx, z + dz) and tm.surface[z + dz][x + dx] in ways
               for x, z in court for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))), \
        "the court is walled off from every way on the map"


def test_a_court_is_a_way_and_a_marsh_is_not():
    assert R.COURT in R.OPEN
    assert R.COURT in R.WALKABLE
    assert R.MARSH not in R.OPEN


# -- the barricade ------------------------------------------------------------

def test_the_palisade_is_a_barricade_and_not_a_garden_fence():
    """The number that started this: paling is 0.68 tall -- 3.4 ft, a fence
    you step over -- and it was the tallest timber the palette had."""
    p = _palette()
    paling = p.resolve("yard_fence")
    palisade = p.resolve("palisade_wall")
    assert paling.size_y < 0.8
    assert palisade.size_y >= 2.0
    # One cell of ground and a full cell deep, so a stair-stepped run of them
    # has no daylight in it -- which is what lets it be laid on the lattice.
    assert (palisade.size_x, palisade.size_z) == (1.0, 1.0)


def test_a_tile_boundary_stays_on_the_half_tile_grid():
    """Every other style resolves to a prop, and a prop may sit off the
    lattice. The palisade kit is tiles, and laying tiles along a surveyed
    bearing put 166 of them off the grid -- a real FAIL from the canary that
    exists because one fractional overhang breaks mini snapping."""
    p = _palette()
    tm = R.rasterize(_keep())
    builder = build_from_tilemap(tm, p, storeys=2, fence_style="palisade")
    byid = {a.id: a for a in p.catalog.assets}
    off = [pl for pl in builder.placements
           if byid.get(pl.asset_id) and byid[pl.asset_id].kind != "prop"
           and any(abs(v * 2 - round(v * 2)) > 0.01 for v in (pl.x, pl.z))]
    assert not off, f"{len(off)} tile placement(s) off the half-tile grid"


def test_every_fence_style_still_builds_something():
    p = _palette()
    tm = R.rasterize(_keep())
    for style in FENCE_STYLES:
        builder = build_from_tilemap(tm, p, storeys=2, fence_style=style)
        assert builder.fence_pieces, f"--fence-style {style} laid nothing"


def test_a_field_wall_is_not_built_as_a_barricade():
    """`--fence-style palisade` built the outlying farms' field boundaries as
    ten-foot timber stockades -- right for the keep, absurd across a wheat
    field, and plainly visible from the air as a fortification cutting
    through somebody's crop.

    The closed-versus-open test that decides what a property is also decides
    what fences it, so the DEFAULT now gets both right with no flag.
    """
    p = _palette()
    layout = _keep()
    layout.fields = None
    # One closed ring plus one open field wall across the same map.
    layout.fences = [_ring(33, 35, 18, 20), [(2.0, 60.0), (30.0, 62.0), (60.0, 61.0)]]
    tm = R.rasterize(layout)

    builder = build_from_tilemap(tm, p, storeys=2)      # default style
    byid = {a.id: a for a in p.catalog.assets}
    used = {byid[pl.asset_id].name for pl in builder.placements
            if pl.asset_id in byid}

    assert "Palisade wall tall 1x2" in used, "the enclosure lost its barricade"
    assert "Stone fence 02" in used, "the field wall was not built as a field wall"


def test_the_enclosure_material_is_taller_than_the_field_material():
    p = _palette()
    from citysmith.build import DEFAULT_ENCLOSURE_STYLE, DEFAULT_FENCE_STYLE
    ring = p.resolve(FENCE_STYLES[DEFAULT_ENCLOSURE_STYLE].panel)
    field = p.resolve(FENCE_STYLES[DEFAULT_FENCE_STYLE].panel)
    assert ring.size_y > field.size_y * 1.5


# -- the review: one run, one material ---------------------------------------

def _square_pen():
    """A pen with four REAL corners, to prove the corner piece still fires."""
    layout = _keep()
    # Clear of the road at z=14, so the pen's corners are not paved over --
    # the first version overlapped it and lost a corner to the carriageway.
    layout.fences = [[(16.0, 22.0), (50.0, 22.0), (50.0, 56.0),
                      (16.0, 56.0), (16.0, 22.0)]]
    return layout


def _palisade_pieces(tm, p):
    builder = build_from_tilemap(tm, p, storeys=2)
    byid = {a.id: a for a in p.catalog.assets}
    from collections import Counter
    c = Counter()
    for pl in builder.placements:
        a = byid.get(pl.asset_id)
        if a and "Palisade" in a.name:
            c[a.name] += 1
    return builder, c


def test_a_stair_step_is_not_a_corner():
    """**The defect this whole section exists for.** Asking whether a cell has
    both an east-west and a north-south neighbour calls every step of a
    rasterised diagonal a turn, so a smooth ring came out speckled with
    round-log corner bundles between flat stake panels -- two materials on one
    barricade. Measured on Sedgewater: a 16-gon whose turns run 1.2 to 53.9
    degrees, not one of them a real corner, and 21 of 116 cells built as
    corners anyway.
    """
    p = _palette()
    _, pieces = _palisade_pieces(R.rasterize(_keep()), p)
    assert pieces["Palisade wall tall 1x2"] > 40, pieces
    assert pieces["Palisade wall tall corner"] == 0, \
        f"a smooth ring grew {pieces['Palisade wall tall corner']} corners"


def test_a_real_corner_still_gets_a_corner_piece():
    """The other half: the fix must not simply delete the corner piece."""
    p = _palette()
    _, pieces = _palisade_pieces(R.rasterize(_square_pen()), p)
    assert pieces["Palisade wall tall corner"] == 4, \
        f"a square pen has four corners, got {pieces}"


def test_a_palisade_run_is_one_material():
    """Stated as the invariant rather than as a count: whatever the shape, a
    boundary must not mix its wall piece and its corner piece along a stretch
    that is visually straight."""
    p = _palette()
    for layout in (_keep(), _square_pen()):
        _, pieces = _palisade_pieces(R.rasterize(layout), p)
        corners = pieces["Palisade wall tall corner"]
        walls = pieces["Palisade wall tall 1x2"]
        assert corners <= 4, f"{corners} corners on one run is stair-stepping"
        assert walls > corners * 5, (walls, corners)


# -- the gate -----------------------------------------------------------------

def test_a_road_through_the_barricade_gets_a_gate():
    """A barricade with a hole in it is not enclosed. The ring skips its own
    cells where a carriageway crosses -- correctly -- and that left a
    fifteen-foot gap with nothing in it."""
    p = _palette()
    layout = _keep()
    # Run the road through the ring rather than past it.
    from citysmith.layout import LayoutRoad
    layout.roads = [LayoutRoad(points=[(33.0, 0.0), (33.0, 70.0)], width=4.0)]
    tm = R.rasterize(layout)
    builder = build_from_tilemap(tm, p, storeys=2)
    gate = p.resolve("palisade_gate")
    assert gate is not None
    assert any(pl.asset_id == gate.id for pl in builder.placements), \
        "the road cut an opening and nothing was hung in it"


def test_the_gate_stands_proud_of_the_wall_it_hangs_in():
    p = _palette()
    assert p.resolve("palisade_gate").size_y > p.resolve("palisade_wall").size_y


def _four_connected_pieces(cells):
    from collections import deque
    seen, comps = set(), 0
    for c in cells:
        if c in seen:
            continue
        comps += 1
        q = deque([c])
        seen.add(c)
        while q:
            x, z = q.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, z + dz)
                if n in cells and n not in seen:
                    seen.add(n)
                    q.append(n)
    return comps


def test_a_diagonal_run_follows_its_bearing_instead_of_stair_stepping():
    """**The fix that replaced diagonal connectors, and it is cheaper.**

    A 1x1 tile may be turned to any of the 24 steps and stay on the half-tile
    lattice -- `rotated_footprint` returns 1.000 x 1.000 at every step, so the
    min corner never moves. Fractional *position* breaks the off-grid canary;
    fractional *rotation* costs nothing. Conflating those two is the whole
    reason a barricade was ever stair-stepped: the piece can just be turned to
    follow the line.

    Probed one specimen per board: connectors (23 pieces) read as a staircase
    of separate frames, bearing rotation (16) as one wall, and bearing PLUS
    connectors read worse than bearing alone, because a connector sits at a
    quarter turn while its neighbours sit at 45 and stands out as a
    T-junction.
    """
    p = _palette()
    tm = R.rasterize(_keep())
    builder = build_from_tilemap(tm, p, storeys=2)
    wall = p.resolve("palisade_wall")
    rots = {pl.rot for pl in builder.placements if pl.asset_id == wall.id}
    assert rots - {0, 6, 12, 18},         f"a diagonal ring produced only quarter turns: {sorted(rots)}"


def test_turning_a_piece_off_a_quarter_turn_stays_on_the_lattice():
    """The measurement the fix rests on. If this ever stops being true, the
    off-grid canary starts firing on every angled boundary on the map."""
    from citysmith.build import place_centered, rotated_footprint

    piece = _palette().resolve("palisade_wall")
    for rot in range(24):
        sx, sz = rotated_footprint(piece, rot)
        assert (sx, sz) == (1.0, 1.0), (rot, sx, sz)
        pl = place_centered(piece, 10.5, 10.5, 0.5, rot)
        assert all(abs(v * 2 - round(v * 2)) < 1e-9 for v in (pl.x, pl.z)), (rot, pl)
