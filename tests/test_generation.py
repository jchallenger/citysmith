"""Tests for city, floorplan, site scoring and slab building.

These deliberately assert *invariants* rather than exact output: the generator
is free to change its aesthetics, but overlapping buildings, unreachable rooms,
and floating geometry are always bugs.
"""

from __future__ import annotations

import pytest

from citysmith import floorplan as fp_mod
from citysmith import sites
from citysmith.build import (
    ROT_E, ROT_N, ROT_S, ROT_W, place_centered, place_tile, place_wall, rotated_footprint,
)
from citysmith.catalog import Asset
from citysmith.city import CityParams, City, Rect, generate


def overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.x2 and b.x < a.x2 and a.z < b.z2 and b.z < a.z2


# -- city ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def town() -> City:
    return generate(CityParams(size="town"), seed=1234)


def test_city_is_deterministic():
    a = generate(CityParams(size="village"), seed=7)
    b = generate(CityParams(size="village"), seed=7)
    assert a.to_dict() == b.to_dict()


def test_different_seeds_differ():
    a = generate(CityParams(size="village"), seed=1)
    b = generate(CityParams(size="village"), seed=2)
    assert a.to_dict() != b.to_dict()


def test_buildings_never_overlap(town: City):
    rects = [b.rect for b in town.buildings]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not overlaps(a, b), f"{a} overlaps {b}"


def test_buildings_never_overlap_streets(town: City):
    for building in town.buildings:
        for street in town.streets:
            assert not overlaps(building.rect, street.rect)


def test_everything_stays_inside_the_city(town: City):
    bounds = Rect(0, 0, town.width, town.depth)
    for b in town.buildings:
        assert b.rect.x >= bounds.x and b.rect.x2 <= bounds.x2
        assert b.rect.z >= bounds.z and b.rect.z2 <= bounds.z2


def test_walled_city_keeps_buildings_inside_the_wall(town: City):
    assert town.walled and town.wall_rect is not None
    inner = town.wall_rect.inset(1)
    for b in town.buildings:
        assert b.rect.x >= inner.x and b.rect.x2 <= inner.x2
        assert b.rect.z >= inner.z and b.rect.z2 <= inner.z2


def test_buildings_are_playable_size(town: City):
    for b in town.buildings:
        assert b.rect.w >= 3 and b.rect.d >= 3


def test_every_building_has_a_known_district(town: City):
    names = {d.name for d in town.districts}
    assert all(b.district in names for b in town.buildings)


def test_entrances_are_cardinal(town: City):
    assert all(b.entrance in ("n", "s", "e", "w") for b in town.buildings)


def test_city_round_trips_through_json(tmp_path, town: City):
    path = tmp_path / "city.json"
    town.save(path)
    loaded = City.load(path)
    assert loaded.to_dict() == town.to_dict()


def test_tiny_city_rejected():
    with pytest.raises(ValueError, match="too small"):
        generate(CityParams(size=12), seed=0)


def test_unknown_size_rejected():
    with pytest.raises(ValueError, match="Unknown size"):
        CityParams(size="enormous").resolved_size()


@pytest.mark.parametrize("size", ["hamlet", "village", "town", "city"])
def test_all_sizes_produce_buildings(size: str):
    city = generate(CityParams(size=size), seed=3)
    assert len(city.buildings) > 0


def test_unwalled_city_has_no_gates():
    city = generate(CityParams(size="village", walled=False), seed=5)
    assert city.gates == []
    assert city.wall_rect is None


# -- sites --------------------------------------------------------------------

def test_ranking_is_ordered_and_explained(town: City):
    ranked = sites.rank(town, top=10)
    assert len(ranked) == 10
    scores = [s.score for s in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(s.reasons for s in ranked)


def test_ranking_is_stable(town: City):
    assert [s.id for s in sites.rank(town, top=8)] == [s.id for s in sites.rank(town, top=8)]


def test_kind_filter(town: City):
    taverns = sites.rank(town, kind="tavern")
    assert taverns and all(s.building.kind == "tavern" for s in taverns)


def test_min_floors_filter(town: City):
    multi = sites.rank(town, min_floors=2)
    assert all(s.building.floors >= 2 for s in multi)


def test_hook_raises_score(town: City):
    with_hook = [s for s in sites.rank(town) if s.building.hook]
    assert with_hook
    assert any("hook:" in r for r in with_hook[0].reasons)


def test_best_raises_when_nothing_matches(town: City):
    with pytest.raises(ValueError, match="No building"):
        sites.best(town, kind="nonexistent-kind")


# -- floorplan ----------------------------------------------------------------

@pytest.fixture(scope="module")
def plan(town: City):
    building = max(town.buildings, key=lambda b: b.rect.area)
    return fp_mod.generate(building, seed=99)


def test_floorplan_is_deterministic(town: City):
    b = town.buildings[0]
    assert fp_mod.generate(b, seed=4).to_dict() == fp_mod.generate(b, seed=4).to_dict()


def test_rooms_tile_the_footprint_without_overlap(plan):
    for level in range(plan.levels):
        rooms = plan.rooms_on(level)
        assert rooms
        area = sum(r.rect.area for r in rooms)
        assert area == plan.rect.area, "rooms must exactly tile the footprint"
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                assert not overlaps(a.rect, b.rect)


def test_rooms_stay_inside_the_building(plan):
    for room in plan.rooms:
        assert room.rect.x >= plan.rect.x and room.rect.x2 <= plan.rect.x2
        assert room.rect.z >= plan.rect.z and room.rect.z2 <= plan.rect.z2


def test_every_level_is_fully_connected(plan):
    """A room the party cannot reach is a bug at the table."""
    for level in range(plan.levels):
        rooms = plan.rooms_on(level)
        if len(rooms) < 2:
            continue
        doors = [d for d in plan.doors if d.level == level and not d.exterior]

        def room_at(x: int, z: int):
            for r in rooms:
                if r.rect.contains(x, z):
                    return r.id
            return None

        adjacency: dict[str, set[str]] = {r.id: set() for r in rooms}
        for d in doors:
            here = room_at(d.x, d.z)
            dx, dz = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}[d.side]
            there = room_at(d.x + dx, d.z + dz)
            if here and there and here != there:
                adjacency[here].add(there)
                adjacency[there].add(here)

        seen = {rooms[0].id}
        stack = [rooms[0].id]
        while stack:
            for nxt in adjacency[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        assert len(seen) == len(rooms), f"level {level}: {len(rooms) - len(seen)} unreachable room(s)"


def test_has_exactly_one_exterior_door(plan):
    assert sum(1 for d in plan.doors if d.exterior) == 1


def test_exterior_door_sits_on_the_perimeter(plan):
    d = next(d for d in plan.doors if d.exterior)
    r = plan.rect
    assert d.x in (r.x, r.x2 - 1) or d.z in (r.z, r.z2 - 1)


def test_stairs_connect_consecutive_levels(plan):
    assert len(plan.stairs) == plan.levels - 1
    for s in plan.stairs:
        assert s.to_level == s.from_level + 1


def test_floorplan_round_trips(tmp_path, plan):
    path = tmp_path / "p.json"
    plan.save(path)
    assert fp_mod.Floorplan.load(path).to_dict() == plan.to_dict()


# -- placement math -----------------------------------------------------------

FLOOR = Asset(id="a" * 8 + "-1111-2222-3333-444444444444", name="floor", kind="tile",
              pack="p", group_tag="floor", tags=(), folder="", size_x=1.0, size_y=0.5, size_z=1.0)
WALL = Asset(id="b" * 8 + "-1111-2222-3333-444444444444", name="wall", kind="tile",
             pack="p", group_tag="wall", tags=(), folder="", size_x=1.0, size_y=2.0, size_z=0.5)


def test_unit_tile_fills_its_cell():
    p = place_tile(FLOOR, 3, 5, y=0.0)
    assert (p.x, p.y, p.z) == (3.0, 0.0, 5.0)


def test_walls_sit_on_opposite_edges_of_the_same_cell():
    n = place_wall(WALL, 0, 0, "n", y=0.5)
    s = place_wall(WALL, 0, 0, "s", y=0.5)
    assert n.rot == ROT_N and s.rot == ROT_S
    # 0.5-thick walls hug opposite edges of a 1-tile cell.
    assert n.z == pytest.approx(0.0)
    assert s.z == pytest.approx(0.5)
    assert n.x == s.x


def test_east_and_west_walls_are_rotated_and_offset():
    """Quarter-turned walls hug their tile's side, spanning its full depth.

    The stored coordinate is the min corner of the *rotated* box, so a
    1.0 x 0.5 wall turned 90 degrees occupies 0.5 x 1.0.
    """
    w = place_wall(WALL, 0, 0, "w", y=0.5)
    e = place_wall(WALL, 0, 0, "e", y=0.5)
    assert w.rot == ROT_W and e.rot == ROT_E
    assert w.x == pytest.approx(0.0)   # occupies x 0.0-0.5
    assert e.x == pytest.approx(0.5)   # occupies x 0.5-1.0
    assert w.z == pytest.approx(0.0)   # spans the tile's full depth
    assert e.z == pytest.approx(0.0)


def test_placement_matches_talespire_measurements():
    """Reproduce two walls TaleSpire placed itself.

    Ground truth copied out of the game: asset 'Wall Only With Window'
    (0.5 x 2.0 footprint) at rot=0 -> (0.50, 0.00) and rot=270 -> (0.00, 3.50).
    This is the regression guard for the rotation convention.
    """
    ts_wall = Asset(
        id="c" * 8 + "-1111-2222-3333-444444444444", name="Wall Only With Window",
        kind="tile", pack="p", group_tag="wall", tags=(), folder="",
        size_x=0.5, size_y=2.0, size_z=2.0,
    )
    unrotated = place_centered(ts_wall, 0.75, 1.0, 0.0, ROT_N)
    assert (unrotated.x, unrotated.z) == pytest.approx((0.50, 0.00))

    turned = place_centered(ts_wall, 1.0, 3.75, 0.0, ROT_W)
    assert (turned.x, turned.z) == pytest.approx((0.00, 3.50))


def test_rotated_footprint_swaps_axes_on_quarter_turns():
    assert rotated_footprint(WALL, ROT_N) == (1.0, 0.5)
    assert rotated_footprint(WALL, ROT_S) == (1.0, 0.5)
    assert rotated_footprint(WALL, ROT_E) == (0.5, 1.0)
    assert rotated_footprint(WALL, ROT_W) == (0.5, 1.0)


def test_walls_rest_on_the_floor_surface():
    top = place_tile(FLOOR, 0, 0, 0.0).y + FLOOR.size_y
    assert place_wall(WALL, 0, 0, "n", y=top).y == pytest.approx(0.5)


def test_unknown_side_rejected():
    with pytest.raises(ValueError, match="side must be"):
        place_wall(WALL, 0, 0, "up")


# -- slab chunking ------------------------------------------------------------

GROUND = Asset(id="d" * 8 + "-1111-2222-3333-444444444444", name="grass", kind="tile",
               pack="p", group_tag="floor", tags=(), folder="", size_x=1.0, size_y=0.5, size_z=1.0)
FERN = Asset(id="e" * 8 + "-1111-2222-3333-444444444444", name="fern", kind="prop",
             pack="p", group_tag="prop", tags=(), folder="", size_x=0.4, size_y=0.4, size_z=0.4)


class StubPalette:
    """Just enough palette for the chunker: it only asks for ground roles."""

    def resolve(self, role, variant=0):
        return GROUND if role == "ground" else None


def _builder():
    from citysmith.build import Builder

    return Builder(StubPalette())


def _ground_field(b, width=8, depth=8):
    for tz in range(depth):
        for tx in range(width):
            b.add(place_tile(GROUND, tx, tz, 0.0))


def _key(p):
    return (p.asset_id, p.x, p.y, p.z, p.rot)


def _multiset(placements):
    import collections

    return collections.Counter(_key(p) for p in placements)


def test_chunks_are_spatial_regions_not_bands():
    """Every placement lands in the chunk whose tile box contains it.

    The band cutter this replaced sliced the sorted placement list every N
    entries, so a chunk was a z-band spanning the whole map rather than a
    region anyone could point at.
    """
    b = _builder()
    _ground_field(b)
    for tx, tz in ((1, 1), (6, 6), (1, 6), (6, 1)):
        b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)
    assert (plan.rows, plan.cols) == (2, 2)
    for chunk in plan.chunks:
        for pl in chunk.slab.placements:
            if (pl.x, pl.y, pl.z) == (0.0, 0.0, 0.0):
                continue                      # registration marker
            assert chunk.x0 <= pl.x < chunk.x1
            assert chunk.z0 <= pl.z < chunk.z1


def test_chunk_labels_name_the_row_and_column():
    b = _builder()
    _ground_field(b)
    b.add(place_wall(WALL, 6, 6, "n", 0.5))
    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)

    labels = [c.label for c in plan.chunks]
    assert len(labels) == len(set(labels))
    assert "r01c01" in labels
    by_label = {c.label: c for c in plan.chunks}
    assert (by_label["r01c01"].x0, by_label["r01c01"].z0) == (4, 4)


def test_every_chunk_shares_one_bounding_box_origin():
    """The registration invariant: multi-chunk maps only line up because of it.

    TaleSpire anchors a pasted slab by its own bounding box, so chunks pasted
    at one anchor point must all report the same minimum corner.
    """
    b = _builder()
    _ground_field(b)
    for tx, tz in ((1, 1), (6, 6)):
        b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert len(plan.chunks) > 1
    corners = {chunk.slab.bounds()[0] for chunk in plan.chunks}
    assert corners == {(0.0, 0.0, 0.0)}


def test_chunking_loses_and_duplicates_nothing():
    b = _builder()
    _ground_field(b)
    for tx, tz in ((1, 1), (6, 6)):
        b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, register=False,
                        skip_open_country=False)
    union = _multiset(pl for c in plan.chunks for pl in c.slab.placements)
    assert union == _multiset(b.placements)


def test_open_country_chunks_are_skipped():
    """A chunk of grass and ferns is not somewhere anyone plays."""
    b = _builder()
    _ground_field(b)
    b.add(place_centered(FERN, 1.5, 1.5, 0.5, 0), prop=True)
    b.add(place_wall(WALL, 6, 6, "n", 0.5))       # one built thing, far corner

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)
    assert [c.label for c in plan.chunks] == ["r01c01"]
    assert len(plan.skipped) == 3
    assert plan.assets_skipped == 48 + 1          # three grass quarters + fern
    assert b.stats.chunks_skipped == 3


def test_a_map_that_is_all_open_country_is_still_written():
    """Skipping every chunk would emit nothing at all, which is worse."""
    b = _builder()
    _ground_field(b)
    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert len(plan.chunks) == 4
    assert plan.skipped == []


def test_sunken_ground_is_not_open_country():
    """Ground a tile low is a watercourse, not empty grass."""
    b = _builder()
    _ground_field(b)
    b.add(place_tile(GROUND, 1, 1, -1.0))
    b.add(place_wall(WALL, 6, 6, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert "r00c00" in {c.label for c in plan.chunks}


def test_oversized_chunks_subdivide_until_they_fit():
    """``max_assets`` stays the hard cap; a dense cell splits quadtree-style."""
    b = _builder()
    for tz in range(8):
        for tx in range(8):
            b.add(place_tile(GROUND, tx, tz, 0.0))
            b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=40, chunk_tiles=8, register=False)
    assert len(plan.chunks) > 1
    for chunk in plan.chunks:
        assert chunk.count <= 40
        assert chunk.quad, "a subdivided piece carries a quadrant tag"
    union = _multiset(pl for c in plan.chunks for pl in c.slab.placements)
    assert union == _multiset(b.placements)


def test_to_slabs_still_returns_plain_slabs():
    b = _builder()
    _ground_field(b)
    b.add(place_wall(WALL, 6, 6, "n", 0.5))
    slabs = b.to_slabs(max_assets=1000, chunk_tiles=4)
    assert all(hasattr(s, "placements") for s in slabs)
    assert len(slabs) == b.stats.slabs


# -- palette integrity --------------------------------------------------------

def test_every_style_resolves_cleanly():
    """No style may ship an unresolved or wrong-footprint role.

    Undeclared roles fall through to a bare name search with no pack filter,
    which is how the cyberpunk style silently resolved "water" to a rowing
    boat and "park" to a balloon cart. ``validate()`` cannot see a role a
    style never declared, so the guard is: every style, every role, clean.
    """
    from citysmith.catalog import load_or_build
    from citysmith.palette import STYLES, Palette

    catalog = load_or_build()
    for name, style in STYLES.items():
        problems = Palette(catalog, style).validate()
        assert problems == [], f"style {name!r}: " + "; ".join(problems)


def test_styles_declare_the_roles_the_builder_requires():
    """Roles the tilemap builder calls ``require()`` on must be declared.

    ``require()`` raises rather than degrading, so a missing one is a hard
    build failure -- cyberpunk aborted on every map with a field in it.
    """
    from citysmith.palette import STYLES

    needed = {"ground", "field_1x1", "street", "floor", "wall", "city_wall"}
    for name, style in STYLES.items():
        missing = sorted(needed - set(style.roles))
        assert not missing, f"style {name!r} does not declare: {missing}"


def test_packing_preserves_every_placement_and_one_origin():
    """Packing merges chunks for fewer pastes; it must not change the map.

    Detection wants small chunks (it can only skip a region it can see);
    pasting wants few. Packing reconciles them, so the thing to guard is that
    it is purely a regrouping: same placements, and still one shared origin,
    without which multi-chunk pastes do not line up.
    """
    b = _builder()
    _ground_field(b)
    for x in (1, 6):
        for z in (1, 6):
            b.add(place_wall(WALL, x, z, "n", 0.5))

    loose = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    packed = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=True)

    def bag(plan):
        # Registration markers are synthetic and only exist when there is more
        # than one chunk to line up, so they are not part of the map.
        return sorted(
            (p.asset_id, round(p.x, 2), round(p.y, 2), round(p.z, 2), p.rot)
            for c in plan.chunks for p in c.slab.placements
            if (p.x, p.y, p.z) != (0.0, 0.0, 0.0)
        )

    assert bag(packed) == bag(loose), "packing changed the placements"
    assert len(packed.chunks) <= len(loose.chunks)
    corners = {c.slab.bounds()[0] for c in packed.chunks}
    assert len(corners) == 1, f"chunks must share one origin, got {corners}"


def test_enclosed_open_country_is_kept():
    """A chunk the town has built all the way round is not trimmable.

    An unpasted chunk is bare board, not grass, so dropping an enclosed one
    punches a rectangular hole into the middle of the map. This is the
    "half generated chunks" defect: two enclosed cells on the Forest Church
    map left a 24x48 tile void with hard straight edges.
    """
    b = _builder()
    _ground_field(b, 12, 12)
    # Build on every chunk of a 3x3 grid except the middle one.
    for tz in (1, 5, 9):
        for tx in (1, 5, 9):
            if (tx, tz) != (5, 5):
                b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert (plan.rows, plan.cols) == (3, 3)
    assert "r01c01" in [c.label for c in plan.chunks]
    assert plan.skipped == []


def test_open_country_is_still_trimmed_from_the_edges():
    """Enclosure is the only thing that protects a chunk, not emptiness."""
    b = _builder()
    _ground_field(b, 12, 12)
    b.add(place_wall(WALL, 1, 1, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert [c.label for c in plan.chunks] == ["r00c00"]
    assert len(plan.skipped) == 8


def test_enclosed_voids_reports_a_hole_in_the_middle():
    from citysmith.build import SlabChunk, ChunkPlan
    from citysmith.slab import Slab
    from citysmith.verify import enclosed_voids

    def chunk(r, c, empty):
        return SlabChunk(row=r, col=c, quad="", x0=c, z0=r, x1=c + 1, z1=r + 1,
                         slab=Slab([place_tile(GROUND, c, r, 0.0)]),
                         open_country=empty)

    made = [chunk(r, c, (r, c) == (1, 1)) for r in range(3) for c in range(3)]
    plan = ChunkPlan([m for m in made if not m.open_country],
                     [m for m in made if m.open_country], 3, 3, 1, (0, 0))
    assert enclosed_voids(plan)

    edge = [chunk(r, c, (r, c) == (0, 0)) for r in range(3) for c in range(3)]
    plan = ChunkPlan([m for m in edge if not m.open_country],
                     [m for m in edge if m.open_country], 3, 3, 1, (0, 0))
    assert enclosed_voids(plan) == []


def test_roof_rings_follow_the_real_shape_not_the_bounding_box():
    """An L-shaped terrace slopes to its own edges.

    Ring depth used to be distance to the block's bounding box. On an L that
    counts cells on a real edge as interior, so they float a course too high.
    """
    from citysmith.build import _roof_rings

    # An L: a 4x4 block with its whole north-east quarter bitten out.
    cells = ({(x, z) for x in range(4) for z in range(4)}
             - {(x, z) for x in (2, 3) for z in (0, 1)})
    rings = _roof_rings(cells)

    # (1, 1) sits one cell in from every side of the *bounding box*, so the
    # old measure called it ring 1 and floated it a course above the eaves.
    # Its east neighbour is part of the bite, so it is really on an edge.
    assert rings[(1, 1)] == 0
    assert (2, 1) not in rings, "the bite is not roofed"

    # The one genuinely interior cell of the L is where the arms are widest.
    assert rings[(1, 2)] == 1

    solid = {(x, z) for x in range(4) for z in range(4)}
    assert _roof_rings(solid)[(1, 1)] == 1


def test_roof_piece_caps_the_tip_of_a_narrow_arm():
    """Three or four falls is a point; no hip piece describes one."""
    from citysmith.build import _roof_piece

    side, corner, cap = "SIDE", "CORNER", "CAP"
    assert _roof_piece(("n",), side, corner, cap)[0] == side
    assert _roof_piece(("n", "w"), side, corner, cap)[0] == corner
    assert _roof_piece(("e", "w"), side, corner, cap)[0] == side   # a ridge run
    assert _roof_piece(("n", "e", "w"), side, corner, cap)[0] == cap
    assert _roof_piece((), side, corner, cap)[0] == cap


def test_surface_tiles_align_at_the_top_not_the_bottom():
    """Cobble is 0.25 thick and grass is 0.5; what must line up is the walk.

    Laying both from a common bottom sank every street a quarter tile below
    the grass beside it -- a 15 inch kerb along both sides of every road.
    """
    from citysmith.build import Builder

    thin = Asset(id="f" * 8 + "-1111-2222-3333-444444444444", name="cobble",
                 kind="tile", pack="p", group_tag="floor", tags=(), folder="",
                 size_x=1.0, size_y=0.25, size_z=1.0)

    class TwoThicknesses:
        def resolve(self, role, variant=0):
            return thin if role == "street" else GROUND

        def require(self, role, variant=0):
            return self.resolve(role, variant)

    b = Builder(TwoThicknesses())
    b.surface("ground", 0, 0, 0.5)
    b.surface("street", 1, 0, 0.5)

    grass, cobble = b.placements
    assert grass.y == 0.0 and cobble.y == 0.25
    assert grass.y + GROUND.size_y == cobble.y + thin.size_y == 0.5
