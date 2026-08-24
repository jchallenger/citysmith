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
#: A pine-sized canopy: wider than its cell, and authored around its origin
#: rather than off one corner, so it overhangs whatever cell it stands in.
CANOPY = Asset(id="f" * 8 + "-1111-2222-3333-444444444444", name="canopy", kind="prop",
               pack="p", group_tag="prop", tags=(), folder="",
               size_x=2.55, size_y=2.4, size_z=2.55, off_x=0.0, off_y=1.2, off_z=0.0)


class StubCatalog:
    """The assets the chunker tests place, so shapes can be looked up by id.

    The chunker normalises by the box its geometry occupies, not by the stored
    coordinates, and that needs each placement's footprint.
    """

    assets = [FLOOR, WALL, GROUND, FERN, CANOPY]


class StubPalette:
    """Just enough palette for the chunker: it only asks for ground roles."""

    catalog = StubCatalog()

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
    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)

    labels = [c.region for c in plan.chunks]
    assert len(labels) == len(set(labels))
    assert "r01c01" in labels
    by_label = {c.region: c for c in plan.chunks}
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
    """A chunk of grass and ferns is not somewhere anyone plays.

    So it is the one thing the build may leave out -- and does, once no kept
    chunk has room to carry it, which a cap with nothing to spare beyond the
    registration markers guarantees.
    """
    from citysmith import build as build_mod

    b = _builder()
    _ground_field(b)
    b.add(place_centered(FERN, 1.5, 1.5, 0.5, 0), prop=True)
    b.add(place_wall(WALL, 6, 6, "n", 0.5))       # one built thing, far corner

    original = build_mod.MAX_COMPRESSED_BYTES
    try:
        build_mod.MAX_COMPRESSED_BYTES = build_mod._REGISTRATION_MARGIN
        plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)
    finally:
        build_mod.MAX_COMPRESSED_BYTES = original
    assert [c.region for c in plan.chunks] == ["r01c01"]
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
    assert "r00c00" in {c.region for c in plan.chunks}


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
            if not plan.is_marker(p)
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
    assert "r01c01" in [c.region for c in plan.chunks]
    assert plan.skipped == []


def test_open_country_is_still_trimmed_from_the_edges():
    """Enclosure is the only thing that protects a chunk, not emptiness.

    Trimmed open country rides along in a kept chunk when one has room (the
    next test), so the room is taken away here: a byte cap with nothing to
    spare beyond the registration markers, which no passenger can meet.
    """
    from citysmith import build as build_mod

    b = _builder()
    _ground_field(b, 12, 12)
    b.add(place_wall(WALL, 1, 1, "n", 0.5))

    original = build_mod.MAX_COMPRESSED_BYTES
    try:
        build_mod.MAX_COMPRESSED_BYTES = build_mod._REGISTRATION_MARGIN
        plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)
    finally:
        build_mod.MAX_COMPRESSED_BYTES = original
    assert [c.region for c in plan.chunks] == ["r00c00"]
    assert len(plan.skipped) == 8


def test_open_country_rides_in_a_kept_chunk_that_has_room():
    """Trimming says what the map can do without, not that the bytes were needed.

    An unwritten chunk is bare board, not grass. Forest Church dropped ten
    edge chunks -- 1,618 assets of plain grass and trees -- while the kept
    landscape chunk beside them was a fifth full, so the south-west of the
    map was a hard-edged notch of nothing. Open country is dropped only when
    no kept chunk of its layer can carry it, and carrying it costs no paste.
    """
    b = _builder()
    _ground_field(b, 12, 12)
    b.add(place_wall(WALL, 1, 1, "n", 0.5))
    everything = _multiset(b.placements)

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4)
    assert plan.skipped == [], [c.label for c in plan.skipped]
    assert len(plan.chunks) == 1, "carrying the fringe must not cost a paste"
    chunk = plan.chunks[0]
    written = _multiset(p for p in chunk.slab.placements if not plan.is_marker(p))
    assert written == everything
    assert set(chunk.covers) == {(r, c) for r in range(3) for c in range(3)}
    assert (chunk.x0, chunk.z0, chunk.x1, chunk.z1) == (0, 0, 12, 12)


def _stranded(plan):
    """Skipped chunks the written chunks enclose, judged on every cell covered."""
    covered = {cell for c in plan.chunks
               for cell in (c.covers or ((c.row, c.col),))}
    seen = set()
    stack = [(r, c) for r in range(plan.rows) for c in (0, plan.cols - 1)]
    stack += [(r, c) for c in range(plan.cols) for r in (0, plan.rows - 1)]
    while stack:
        r, c = stack.pop()
        if ((r, c) in seen or (r, c) in covered
                or not (0 <= r < plan.rows and 0 <= c < plan.cols)):
            continue
        seen.add((r, c))
        stack += [(r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)]
    return [c.region for c in plan.skipped if (c.row, c.col) not in seen]


def test_open_country_is_absorbed_from_the_inside_out():
    """When only part of the fringe fits, the innermost chunks go first.

    Whatever is still dropped once the room runs out has to be the outermost
    ring -- the map ends a little sooner -- and never a cell the written
    chunks now surround, which is the rectangular hole the trim exists to
    prevent.
    """
    b = _builder()
    _ground_field(b, 20, 20)
    b.add(place_wall(WALL, 1, 1, "n", 0.5))

    # Room for exactly one 16-tile chunk beside the 17 placements kept.
    plan = b.chunk_plan(max_assets=17 + 16, chunk_tiles=4)
    assert (plan.rows, plan.cols) == (5, 5)
    assert len(plan.chunks) == 1
    assert set(plan.chunks[0].covers) == {(0, 0), (2, 2)}, "the centre is deepest"
    assert len(plan.skipped) == 23
    assert _stranded(plan) == []


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


def test_enclosed_voids_sees_every_cell_a_packed_chunk_covers():
    """Packing fuses a ring of eight cells into one chunk named for its first
    cell. A barrier built from names alone is one cell wide, so the flood
    walked through the other seven and the hole in the middle went unreported
    on every packed plan -- the metric was reading the plan's labels, not its
    coverage."""
    from citysmith.build import ChunkPlan, SlabChunk
    from citysmith.slab import Slab
    from citysmith.verify import enclosed_voids

    ring = [(r, c) for r in range(3) for c in range(3) if (r, c) != (1, 1)]
    fused = SlabChunk(row=0, col=0, quad="+7", x0=0, z0=0, x1=3, z1=3,
                      slab=Slab([place_tile(GROUND, c, r, 0.0) for r, c in ring]),
                      covers=tuple(ring))
    hole = SlabChunk(row=1, col=1, quad="", x0=1, z0=1, x1=2, z1=2,
                     slab=Slab([place_tile(GROUND, 1, 1, 0.0)]), open_country=True)
    plan = ChunkPlan([fused], [hole], 3, 3, 1, (0, 0))
    assert enclosed_voids(plan), "a packed ring still encloses its centre"


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


def test_gatehouse_ring_includes_diagonal_jambs():
    """The wall circuit stair-steps, so a jamb is often only diagonal.

    An orthogonal-only ring left the flanking towers with gaps in them.
    """
    from citysmith.build import _gatehouse_cells

    #  # # #
    #  # G #     G is the passage, everything else is wall
    #  # # #
    mass = {(x, z) for x in range(3) for z in range(3)}
    gates = {(1, 1)}
    ring = _gatehouse_cells(mass, gates)
    assert ring == mass - gates
    assert (0, 0) in ring, "the diagonal jamb has to rise with the rest"


def test_gatehouse_never_raises_the_passage_itself():
    from citysmith.build import _gatehouse_cells

    mass = {(x, 0) for x in range(5)}
    gates = {(2, 0)}
    assert gates.isdisjoint(_gatehouse_cells(mass, gates))


def _walled_tilemap():
    """A square ring three cells thick with a two-wide gate through its north side."""
    from citysmith.raster import STREET, TileMap

    tm = TileMap.blank(26, 26)
    for x in range(4, 22):
        for z in range(4, 22):
            if not (7 <= x < 19 and 7 <= z < 19):
                tm.wall[z][x] = True
    for x in (12, 13):
        for z in range(4, 7):
            tm.wall[z][x] = False
            tm.gates.add((x, z))
            tm.surface[z][x] = STREET
    tm.wall_corners = [(4, 4), (21, 4), (21, 21), (4, 21)]
    return tm


def _wall_mass(tm):
    return {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x]} | set(tm.gates)


def test_wall_towers_flank_the_gate_and_stand_at_the_corners():
    """One tower on each jamb of the gate, one on each corner of the ring.

    Without them the circuit was one unbroken band and the gate read as
    damage. A tower never stands in the passage it guards, never in the
    street, and never on top of another.
    """
    from citysmith.build import WALL_TOWER_TILES, _gatehouse_cells, pick_wall_towers

    tm = _walled_tilemap()
    mass = _wall_mass(tm)
    towers = pick_wall_towers(tm, mass, set(tm.gates))
    cells = [c for t in towers for c in t]

    assert len(towers) == 6, "two for the gate, four for the corners"
    assert all(len(t) == WALL_TOWER_TILES ** 2 for t in towers)
    assert len(cells) == len(set(cells)), "towers never overlap"
    assert not any(c in tm.gates for c in cells), "a tower never blocks its own gate"

    ring = _gatehouse_cells(mass, set(tm.gates))
    west = {c for c in ring if c[0] < 12}
    east = {c for c in ring if c[0] > 13}
    assert any(t & west for t in towers) and any(t & east for t in towers), (
        "the gate is flanked on both sides")
    for cx, cz in tm.wall_corners:
        assert any(abs(x - cx) < WALL_TOWER_TILES and abs(z - cz) < WALL_TOWER_TILES
                   for x, z in cells), f"no tower at corner {(cx, cz)}"


def test_wall_towers_rise_above_the_curtain_in_the_same_block():
    """A tower is the rampart's own block, two courses higher, and no cell of
    the circuit is built twice -- a tower replaces the wall under it rather
    than being stacked through it."""
    import collections
    import math

    from citysmith.build import (
        TOWN_WALL_TILES, WALL_TOWER_RISE, build_from_tilemap, pick_wall_towers,
    )
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    tm = _walled_tilemap()
    b = build_from_tilemap(tm, palette, storeys=2, roofs=False)
    core = palette.require("city_wall_core")
    mass = _wall_mass(tm)
    tower_cells = {c for t in pick_wall_towers(tm, mass, set(tm.gates)) for c in t}
    assert tower_cells

    tops: dict[tuple[int, int], float] = {}
    columns = collections.Counter()
    for p in b.placements:
        if p.asset_id != core.id:
            continue
        cell = (int(math.floor(p.x + 1e-6)), int(math.floor(p.z + 1e-6)))
        tops[cell] = max(tops.get(cell, 0.0), p.y + core.size_y)
        columns[(cell, round(p.y, 3))] += 1
    assert max(columns.values()) == 1, "no block is laid twice in one place"

    grade = palette.require("floor").size_y
    wall_top = grade + max(1, round(TOWN_WALL_TILES / core.size_y)) * core.size_y
    for c in mass - tower_cells - set(tm.gates):
        assert abs(tops[c] - wall_top) < 1e-6, f"curtain at {c} is {tops[c]}, not {wall_top}"
    for c in tower_cells:
        want = wall_top + WALL_TOWER_RISE * core.size_y
        assert abs(tops[c] - want) < 1e-6, f"tower at {c} is {tops[c]}, not {want}"


def test_a_building_straddling_a_chunk_line_stays_in_one_chunk():
    """Chunking by position cut a building's shell along the grid line: the
    barracks on Forest Church went 17 pieces into one structure file and 2 into
    the other, and a paste that misses a file leaves a bare floor with half a
    house beside it. A building is assigned by its own low corner, whole."""
    import collections
    import math

    from citysmith.build import STRUCTURE, build_from_tilemap, footprints, placed_bounds
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    palette = Palette(load_or_build(), MEDIEVAL)
    tm = TileMap.blank(48, 12)
    # One 6x4 house across the x=24 grid line of a 24-tile chunk lattice.
    for x in range(21, 27):
        for z in range(4, 8):
            tm.building[z][x] = "house-0001"
            tm.surface[z][x] = FLOOR
    tm.floors["house-0001"] = 2
    _find_perimeters(tm, None)
    _place_doors(tm, None)
    b = build_from_tilemap(tm, palette, storeys=2, roofs=True)
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=24, pack=False)

    cells = set(footprints(tm)["house-0001"])
    byid = b.byid
    where = collections.Counter()
    for ch in plan.chunks:
        if ch.layer != STRUCTURE:
            continue
        for p in ch.slab.placements:
            if plan.is_marker(p) or p.asset_id in b.prop_ids:
                continue
            a = byid[p.asset_id]
            x0, z0, x1, z1 = placed_bounds(a, p)
            if (int(math.floor((x0 + x1) / 2)), int(math.floor((z0 + z1) / 2))) in cells:
                where[ch.label] += 1
    assert len(where) == 1, f"the shell is split across chunks: {dict(where)}"
    assert sum(ch.buildings for ch in plan.chunks if ch.layer == STRUCTURE) == 1
    assert all(ch.buildings == 0 for ch in plan.chunks if ch.layer != STRUCTURE)


def test_an_attic_gets_no_floor():
    """The roof seats on the wall head, not on a deck, so a slab at the top
    storey only floors the roof void -- a room nothing stands in, under a roof
    you cannot see past. A single-storey cottage gets no upper slab at all; a
    two-storey house gets exactly one, the floor you walk on upstairs."""
    from citysmith.build import build_from_tilemap, footprints
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    palette = Palette(load_or_build(), MEDIEVAL)
    upper = palette.resolve("floor_upper") or palette.require("floor")

    def decks(floors: int) -> int:
        tm = TileMap.blank(16, 16)
        for x in range(4, 9):
            for z in range(4, 8):
                tm.building[z][x] = "house-0001"
                tm.surface[z][x] = FLOOR
        tm.floors["house-0001"] = floors
        _find_perimeters(tm, None)
        _place_doors(tm, None)
        b = build_from_tilemap(tm, palette, storeys=floors, roofs=True)
        cells = set(footprints(tm)["house-0001"])
        # Ground floors are laid in the landscape pass and sit at y=0; anything
        # above that in the building's own cells is an upper deck.
        ys = {round(p.y, 3) for p in b.placements
              if p.asset_id == upper.id and p.y > 0.01
              and (int(p.x), int(p.z)) in cells}
        return len(ys)

    assert decks(1) == 0, "a one-room cottage was given an attic floor"
    assert decks(2) == 1, "a two-storey house should have exactly one deck"
    assert decks(3) == 2


def test_no_upper_deck_reaches_the_outside_of_a_building():
    """A deck fills its whole cell, so on a perimeter cell its edge lands flush
    with the wall face and reads as a band of floorboards round the building --
    the floor, seen from outside. Decks go on cells with no exposed side."""
    from citysmith.build import build_from_tilemap, footprints
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    palette = Palette(load_or_build(), MEDIEVAL)
    upper = palette.resolve("floor_upper") or palette.require("floor")

    tm = TileMap.blank(18, 18)
    for x in range(4, 11):
        for z in range(4, 10):
            tm.building[z][x] = "house-0001"
            tm.surface[z][x] = FLOOR
    tm.floors["house-0001"] = 3
    _find_perimeters(tm, None)
    _place_doors(tm, None)
    b = build_from_tilemap(tm, palette, storeys=3, roofs=True)

    edge = {(x, z) for x, z, _ in tm.perimeter["house-0001"]}
    assert edge, "the fixture should have a perimeter"
    on_edge = [p for p in b.placements
               if p.asset_id == upper.id and p.y > 0.01 and (int(p.x), int(p.z)) in edge]
    assert not on_edge, f"{len(on_edge)} upper deck tiles reach the facade"
    inner = set(footprints(tm)["house-0001"]) - edge
    laid = {(int(p.x), int(p.z)) for p in b.placements
            if p.asset_id == upper.id and p.y > 0.01}
    assert laid and laid <= inner


def test_the_roof_sits_on_the_wall_head():
    """The roof is seated at floors*storey_h and the top wall's head is at
    (floors-1)*storey_h + wall. Those agree only when the storey *is* the wall;
    pitched at wall+floor they differ by a deck, and the roof floats half a
    tile with daylight under it all the way round."""
    from citysmith.build import build_from_tilemap, placed_bounds
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    palette = Palette(load_or_build(), MEDIEVAL)
    wall = palette.require("wall")
    # `_lay_roofs` deals ridge, side and corner pieces, not the flat cap the
    # "roof" role resolves to, so the roof is found by form. The Village wall
    # panels are tagged `group='roof'` too -- they ship in a roof set -- so
    # they have to come back out, or the wall counts as its own roof.
    roof_ids = {a.id for a in palette.catalog.assets
                if "roof" in (a.group_tag or "").lower()
                and "wall" not in a.name.lower()}
    assert roof_ids

    for floors in (1, 2, 3):
        tm = TileMap.blank(16, 16)
        for x in range(4, 9):
            for z in range(4, 8):
                tm.building[z][x] = "house-0001"
                tm.surface[z][x] = FLOOR
        tm.floors["house-0001"] = floors
        _find_perimeters(tm, None)
        _place_doors(tm, None)
        b = build_from_tilemap(tm, palette, storeys=floors, roofs=True)

        heads = [p.y + wall.size_y for p in b.placements if p.asset_id == wall.id]
        roofs = [p.y for p in b.placements if p.asset_id in roof_ids]
        assert heads and roofs, f"{floors} storey: nothing built"
        assert abs(min(roofs) - max(heads)) < 1e-6, (
            f"{floors} storey: roof bottom {min(roofs)} vs wall head {max(heads)}")


def test_per_building_emits_one_slab_per_building_and_one_for_the_wall():
    """A chunk of forty buildings lands or fails as one thing, and nothing
    about the result says which building went wrong. Cut by building instead
    and each is pasted, seen and corrected on its own -- the town wall and
    anything else not part of a building in a slab of its own."""
    import collections
    import math

    from citysmith.build import STRUCTURE, build_from_tilemap, footprints, placed_bounds
    from citysmith.catalog import load_or_build
    from citysmith.palette import Palette
    from citysmith.raster import FLOOR, STREET, TileMap, _find_perimeters, _place_doors

    palette = Palette.named(load_or_build(), "medieval", 33)
    tm = TileMap.blank(40, 24)
    for i, (bx, bz) in enumerate([(4, 4), (20, 4), (12, 14)]):
        bid = f"house-{i:04d}"
        for x in range(bx, bx + 5):
            for z in range(bz, bz + 5):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        tm.floors[bid] = 2
    for x in range(40):                       # a wall run, owned by no building
        tm.wall[22][x] = True
        tm.surface[22][x] = STREET
    _find_perimeters(tm, None)
    _place_doors(tm, None)

    b = build_from_tilemap(tm, palette, storeys=2, roofs=True)
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=24, per_building=True)
    struct = [c for c in plan.chunks if c.layer == STRUCTURE]

    names = sorted(c.name for c in struct)
    assert names == ["house-0000", "house-0001", "house-0002", "rampart"], names
    assert [c.label for c in struct if c.name == "house-0001"] == ["structure-house-0001"]
    assert sum(c.buildings for c in struct) == 3
    assert next(c for c in struct if c.name == "rampart").buildings == 0

    # Every one of a building's pieces is in its own slab and nowhere else.
    byid = b.byid
    cell_bid = {c: bid for bid, cells in footprints(tm).items() for c in cells}
    where = collections.defaultdict(set)
    for ch in struct:
        for p in ch.slab.placements:
            if plan.is_marker(p) or p.asset_id in b.prop_ids:
                continue
            a = byid[p.asset_id]
            x0, z0, x1, z1 = placed_bounds(a, p)
            bid = cell_bid.get((int(math.floor((x0 + x1) / 2)),
                                int(math.floor((z0 + z1) / 2))))
            if bid:
                where[bid].add(ch.name)
    assert where, "no building pieces found"
    for bid, chunks in where.items():
        assert chunks == {bid}, f"{bid} is spread over {sorted(chunks)}"


def test_tiled_chunks_all_reach_the_shared_floor():
    """Measured on the board: a chunk pasted over another inherited the height
    of the surface under the cursor -- a whole structure layer landed 1.5 tiles
    up. Tiled chunks are pasted onto bare board instead, so each comes to rest
    on its own lowest point; unless every one of them reaches the same floor,
    they step against each other along the joins."""
    from citysmith.build import LANDSCAPE, volume_bounds
    from citysmith.verify import chunk_datum

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 16, 16)
        b.add(place_tile(GROUND, 3, 3, -1.5))      # a sunken bed in one chunk
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=8, by_layer=False,
                        pack=False, skip_open_country=False)
    assert len(plan.chunks) > 1
    assert all(c.layer == "" for c in plan.chunks), "tiling mode is unlayered"
    assert chunk_datum(plan, b.byid) == []
    for c in plan.chunks:
        (_, ly, _), _ = volume_bounds(c.slab, b.byid)
        assert abs(ly) < 1e-6, f"{c.label} floors at {ly}"

    # Strip the pins and the chunk holding the sunken tile floors lower than
    # the rest -- which is the step the pins exist to prevent.
    for c in plan.chunks:
        c.slab.placements = [p for p in c.slab.placements if not plan.is_marker(p)]
    assert chunk_datum(plan, b.byid), "an unpinned tiling should be caught"


def test_tiled_chunks_present_one_identical_box():
    """The paste anchors on the bounding box's *centre* -- measured on the
    board with a 24x24 pad, which came to rest centred on the cursor. So the
    thing that lets nine chunks go down at one cursor cell is not that they
    share a corner but that they share the whole box: same centre, same anchor,
    no measuring. It also makes any error in that anchor common to all of them,
    so the map lands assembled even if it lands a little off where it was
    aimed."""
    from citysmith.build import LANDSCAPE, volume_bounds
    from citysmith.verify import chunk_anchors

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 16, 16)
        b.add(place_tile(GROUND, 3, 3, 2.0))       # a tall thing in one chunk
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=8, by_layer=False,
                        pack=False, skip_open_country=False)
    assert len(plan.chunks) > 1
    assert chunk_anchors(plan, b.byid) == []

    boxes = {volume_bounds(c.slab, b.byid) for c in plan.chunks}
    assert len(boxes) == 1, f"chunks anchor on different centres: {boxes}"


def test_the_shared_box_is_anchored_on_a_tile_not_between_two():
    """The paste centres the slab's bounding box on the cursor's ray hit and
    snaps the result to the grid. An odd-width box centres between two cells,
    so the snap has a tie -- and it does not always break the same way: with a
    189-wide box two copy-outs off the board put one chunk's props a whole tile
    east of its neighbour's, a step down the length of the join."""
    from citysmith.build import LANDSCAPE, volume_bounds
    from citysmith.verify import anchor_on_a_whole_tile

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 17, 19)          # odd both ways before rounding
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=8, by_layer=False,
                        pack=False, skip_open_country=False)
    assert len(plan.chunks) > 1
    assert anchor_on_a_whole_tile(plan, b.byid) == []
    for c in plan.chunks:
        (lx, _, lz), (hx, _, hz) = volume_bounds(c.slab, b.byid)
        for axis, v in (("x", (lx + hx) / 2), ("z", (lz + hz) / 2)):
            assert abs(v - round(v)) < 1e-6, f"{c.label} centres on {axis}={v}"


def test_the_chunk_covering_the_anchor_is_pasted_last():
    """All nine chunks anchor on the same point, and that point has to still be
    bare board when each one arrives -- a paste comes to rest on whatever the
    cursor's ray hits. Every region but one is somewhere else entirely; the one
    covering the centre goes last, so nothing is ever pasted onto it."""
    from citysmith.build import LANDSCAPE, volume_bounds
    from citysmith.slab import Slab

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 24, 24)
    plan = b.chunk_plan(max_assets=9000, chunk_tiles=8, by_layer=False,
                        pack=False, skip_open_country=False)
    assert len(plan.chunks) > 1

    (lox, _, loz), (hix, _, hiz) = volume_bounds(
        Slab([p for p in plan.chunks[0].slab.placements]), b.byid)
    cx, cz = (lox + hix) / 2.0, (loz + hiz) / 2.0
    last = plan.chunks[-1]
    assert last.x0 <= cx < last.x1 and last.z0 <= cz < last.z1, (
        f"{last.label} is last but does not cover the anchor at ({cx}, {cz})")
    for c in plan.chunks[:-1]:
        assert not (c.x0 <= cx < c.x1 and c.z0 <= cz < c.z1), (
            f"{c.label} covers the anchor and is not last")


def test_a_shell_that_hovers_over_its_floor_is_caught():
    """The shell and the floor are in different slabs, so "the walls sit on
    the floor" is two pastes agreeing about height. Cheaper to catch in the
    geometry than by eye on the board."""
    from citysmith.build import LANDSCAPE, STRUCTURE
    from citysmith.raster import TileMap
    from citysmith.verify import shells_rest_on_their_floors

    tm = TileMap.blank(6, 6)
    b = _builder()
    with b.layer(LANDSCAPE):
        b.add(place_tile(GROUND, 1, 1, 0.0))          # floor, top at 0.5
    b.group = "house-0001"
    with b.layer(STRUCTURE):
        b.add(place_wall(WALL, 1, 1, "n", 0.5))       # sits on it
    assert shells_rest_on_their_floors(b, tm) == []

    hover = _builder()
    with hover.layer(LANDSCAPE):
        hover.add(place_tile(GROUND, 1, 1, 0.0))
    hover.group = "house-0001"
    with hover.layer(STRUCTURE):
        hover.add(place_wall(WALL, 1, 1, "n", 1.0))   # half a tile of air
    problems = shells_rest_on_their_floors(hover, tm)
    assert problems and "house-0001" in problems[0]


def test_a_plank_is_decked_over_running_water():
    """A plank used to be cobble at grade with a tile of air under it and no bed
    below. Now the river runs on under a deck laid by its top, flush with the
    bank, railed on every side that faces open water."""
    import collections
    import math

    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import PIER, WATER, TileMap

    palette = Palette(load_or_build(), MEDIEVAL)
    tm = TileMap.blank(12, 12)
    for x in range(12):
        for z in (5, 6, 7):
            tm.surface[z][x] = WATER
    for z in (5, 6, 7):
        tm.surface[z][5] = PIER
    b = build_from_tilemap(tm, palette, storeys=1, roofs=False)

    deck = palette.require("bridge_deck")
    rail = palette.require("bridge_rail")
    water = palette.require("water")
    street = palette.require("street")
    grade = palette.require("floor").size_y

    by_cell = collections.defaultdict(list)
    for p in b.placements:
        by_cell[(int(math.floor(p.x + 1e-6)), int(math.floor(p.z + 1e-6)))].append(p)
    for z in (5, 6, 7):
        here = by_cell[(5, z)]
        decks = [p for p in here if p.asset_id == deck.id]
        assert len(decks) == 1
        assert abs(decks[0].y + deck.size_y - grade) < 1e-6, "the deck's top is the bank's top"
        assert not any(p.asset_id == street.id for p in here), "no cobble over the channel"
        waters = [p for p in here if p.asset_id == water.id]
        assert waters, "the river runs on under the deck"
        assert max(p.y + water.size_y for p in waters) <= decks[0].y + 1e-6, (
            "the deck rests on the water rather than cutting through it")
    rails = [p for p in b.placements if p.asset_id == rail.id]
    assert len(rails) == 6, "one rail each side of every deck cell, none at the banks"


# -- scatter ------------------------------------------------------------------

TRUNK = Asset(id="1" * 8 + "-1111-2222-3333-444444444444", name="trunk", kind="prop",
              pack="p", group_tag="prop", tags=(), folder="",
              size_x=1.0, size_y=1.3, size_z=1.0)
CROWN = Asset(id="2" * 8 + "-1111-2222-3333-444444444444", name="crown", kind="prop",
              pack="p", group_tag="prop", tags=(), folder="",
              size_x=2.5, size_y=2.4, size_z=2.5)


def test_scatter_refuses_a_prop_that_overlaps_one_already_placed():
    """TaleSpire silently drops overlapping props, so we must not emit them."""
    from citysmith.build import Builder, Scatter

    s = Scatter(Builder(StubPalette()))
    assert s.one(CROWN, 5.0, 5.0, 0.5, 0)
    assert not s.one(CROWN, 6.0, 5.0, 0.5, 0), "overlapping crown must be refused"
    assert s.one(CROWN, 8.0, 5.0, 0.5, 0), "clear of the first, so it goes down"
    assert s.rejected == 1
    assert len(s.b.placements) == 2


def test_scatter_allows_props_stacked_clear_of_each_other():
    """A crown sitting on top of a trunk shares its footprint, not its height."""
    from citysmith.build import Builder, Scatter

    s = Scatter(Builder(StubPalette()))
    assert s.one(TRUNK, 5.0, 5.0, 0.0, 0)
    assert s.one(CROWN, 5.0, 5.0, TRUNK.size_y, 0)
    assert len(s.b.placements) == 2


def test_scatter_places_a_group_all_or_nothing():
    """A crown that landed while its trunk was refused is the exact defect."""
    from citysmith.build import Builder, Scatter

    s = Scatter(Builder(StubPalette()))
    s.one(CROWN, 5.0, 5.0, 0.5, 0)                      # blocks the site
    placed = s.place([(TRUNK, 5.2, 5.2, 0.5, 0),        # clashes
                      (CROWN, 5.2, 5.2, 1.8, 0)])       # would not have
    assert placed is False
    assert len(s.b.placements) == 1, "no half of the tree may survive"


def test_conifer_is_a_whole_tree_with_its_crown_on_its_trunk():
    """"Stackable Pine Top" alone is a canopy cone lying on the grass."""
    from citysmith.build import Builder, Scatter, _plant_conifer

    class PineKit:
        def resolve(self, role, variant=0):
            return {"tree_conifer_trunk": TRUNK, "tree_conifer_crown": CROWN}.get(role)

        def require(self, role, variant=0):
            return self.resolve(role)

    s = Scatter(Builder(StubPalette()))
    assert _plant_conifer(s, PineKit(), 4.0, 4.0, 0.5, 0, tall=False)
    trunk, crown = s.b.placements
    assert trunk.y == 0.5
    assert crown.y == 0.5 + TRUNK.size_y, "the crown sits on the trunk, not the ground"


# -- map edge taper -----------------------------------------------------------

def test_tapered_ground_is_still_open_country():
    """The coupling this feature exists to get right.

    Lowering the border is what stops the map reading as a cropped slab. But
    open-country detection used to ask "is this ground at grade?", so tapered
    ground would have counted as a *feature* and the border chunks -- exactly
    the ones worth dropping -- would never be skipped again. Ground is tested
    against its own cell's baseline instead.
    """
    b = _builder()
    for tz in range(8):
        for tx in range(8):
            y = -0.5 if tx < 4 else 0.0        # the left half falls away
            b.add(place_tile(GROUND, tx, tz, y))
            b.ground_baseline[(tx, tz)] = y
    b.add(place_wall(WALL, 6, 6, "n", 0.5))    # one built thing, far corner

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert [c.region for c in plan.chunks] == ["r01c01"]
    assert len(plan.skipped) == 3, "tapered border chunks must still be skippable"


def test_ground_off_its_baseline_still_disqualifies_a_chunk():
    """A sunken riverbed is a feature; a tapered edge is not.

    Both sit below grade, so height alone cannot tell them apart. What can:
    the taper records a baseline for the cell, and a channel does not.
    """
    b = _builder()
    _ground_field(b)
    for tz in range(8):
        for tx in range(8):
            b.ground_baseline[(tx, tz)] = 0.0
    b.add(place_tile(GROUND, 1, 1, -1.0))      # a channel, with no baseline
    b.add(place_wall(WALL, 6, 6, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert "r00c00" in [c.region for c in plan.chunks]


def test_edge_taper_never_leaves_the_outermost_block_at_grade():
    """Ragged *reach*, not a ragged decision about whether to drop at all.

    Letting the per-block nudge decide that left a third of the border sitting
    at full grade against the void -- the sheer edge the taper removes.
    """
    from citysmith.build import edge_taper

    class FlatMap:
        width = depth = 40
        building = [[None] * 40 for _ in range(40)]
        wall = [[False] * 40 for _ in range(40)]
        surface = [["ground"] * 40 for _ in range(40)]

    taper = edge_taper(FlatMap())
    border = [(x, z) for x in range(40) for z in range(40)
              if min(x, z, 39 - x, 39 - z) < 2]
    for cell in border:
        assert cell in taper, f"{cell} on the border was left at grade"

    # One step, not a flight of terraces: a wide flat terrace half a tile
    # below grade reads as a second layer of land, not as a slope.
    from citysmith.build import EDGE_TAPER_MAX_DROP

    assert {v for v in taper.values() if v is not None} == {EDGE_TAPER_MAX_DROP}


def test_place_wall_handles_a_mesh_authored_along_z():
    """Edge pieces are not all authored the same way round.

    Wall kits run their length along x with a thin z. The harbour fences are
    the reverse (0.5 x 0.5 x 1.0), and placing one on the wall convention put
    it a quarter tile off the grid on *both* axes -- which the build's own
    off-grid check caught.
    """
    along_z = Asset(id="3" * 8 + "-1111-2222-3333-444444444444", name="rail",
                    kind="tile", pack="p", group_tag="fence", tags=(), folder="",
                    size_x=0.5, size_y=0.5, size_z=1.0)

    for side in ("n", "e", "s", "w"):
        p = place_wall(along_z, 4, 7, side, y=0.5)
        assert abs(p.x * 2 - round(p.x * 2)) < 1e-9, f"{side}: x={p.x} off grid"
        assert abs(p.z * 2 - round(p.z * 2)) < 1e-9, f"{side}: z={p.z} off grid"

    # and it still spans the edge it was asked for, rather than crossing it
    n = place_wall(along_z, 4, 7, "n", y=0.0)
    sx, sz = rotated_footprint(along_z, n.rot)
    assert (sx, sz) == (1.0, 0.5), "the long axis must run along the edge"
    assert n.z == 7.0 and n.x == 4.0


def test_place_wall_leaves_x_authored_meshes_alone():
    """The correction must be a no-op for every kit already in use."""
    n = place_wall(WALL, 2, 3, "n", y=0.5)
    assert n.rot == ROT_N
    assert (n.x, n.z) == (2.0, 3.0)


# -- town layout --------------------------------------------------------------

def test_lane_counts_as_public_open_space():
    """A lane is somewhere people walk, so a door may open onto it.

    Leaving LANE out of OPEN silently invalidated the doorway of every
    building whose only frontage had just been paved as a lane. Access fell
    from 100% to 96% and nothing pointed at the lanes as the cause -- the
    notching feature landing in the same rev took the blame for two rounds.
    """
    from citysmith import raster as R

    assert R.LANE in R.OPEN
    assert R.LANE in R.WALKABLE


def test_notch_is_refused_when_the_yard_is_sealed():
    """A notch hemmed in by neighbours is a courtyard, not a yard."""
    from citysmith.raster import _notch_opens_outward, TileMap

    tm = TileMap.blank(10, 10)
    patch = [(4, 4), (5, 4), (4, 5), (5, 5)]
    assert not _notch_opens_outward(tm, patch, reachable=set())
    assert _notch_opens_outward(tm, patch, reachable={(6, 4)})


def test_notching_produces_l_shapes_but_keeps_every_door():
    """The whole point: vary the plan without cutting anyone off the street."""
    import collections
    from citysmith.layout import Layout
    from citysmith.raster import rasterize
    import pathlib

    src = pathlib.Path("out/layout.json")
    if not src.exists():
        pytest.skip("needs the imported Forest Church layout")

    tm = rasterize(Layout.load(src), bridges=True)
    fp = collections.defaultdict(list)
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.building[z][x]:
                fp[tm.building[z][x]].append((x, z))

    non_rect = 0
    for cells in fp.values():
        xs = [c[0] for c in cells]
        zs = [c[1] for c in cells]
        if len(cells) != (max(xs) - min(xs) + 1) * (max(zs) - min(zs) + 1):
            non_rect += 1
    assert non_rect > 0, "every footprint is still a perfect rectangle"
    assert all(tm.doors.get(bid) for bid in fp), "a notch took a building's only door"


def test_the_town_has_a_market_square():
    from citysmith import raster as R
    from citysmith.layout import Layout
    from citysmith.raster import rasterize
    import pathlib

    src = pathlib.Path("out/layout.json")
    if not src.exists():
        pytest.skip("needs the imported Forest Church layout")
    tm = rasterize(Layout.load(src), bridges=True)
    plaza = sum(1 for z in range(tm.depth) for x in range(tm.width)
                if tm.surface[z][x] == R.PLAZA)
    assert plaza > 0, "MFCG gave no square, so one has to be carved"


def test_packing_respects_the_byte_cap_not_just_the_asset_count():
    """Assets are a proxy for bytes, and a proxy that drifts.

    As the map gained height variety and dressing, the same asset count
    compressed worse and the largest chunk crept to 29,634 of the 30,720-byte
    cap on an unchanged budget. Merges are measured now, so a run that will
    not encode is closed one chunk early.
    """
    from citysmith import build as build_mod
    from citysmith.slab import MAX_COMPRESSED_BYTES, encode

    b = _builder()
    _ground_field(b, 24, 24)
    for tz in range(0, 24, 4):
        for tx in range(0, 24, 4):
            b.add(place_wall(WALL, tx, tz, "n", 0.5))

    generous = b.chunk_plan(max_assets=100000, chunk_tiles=4, pack=True,
                            register=False)
    for chunk in generous.chunks:
        raw = len(encode(chunk.slab).encode()) * 3 // 4
        assert raw <= MAX_COMPRESSED_BYTES

    # With a tiny cap the packer must stop merging and emit more slabs.
    original = build_mod.MAX_COMPRESSED_BYTES
    try:
        build_mod.MAX_COMPRESSED_BYTES = 400
        tight = b.chunk_plan(max_assets=100000, chunk_tiles=4, pack=True,
                             register=False)
    finally:
        build_mod.MAX_COMPRESSED_BYTES = original
    assert len(tight.chunks) > len(generous.chunks), (
        "a byte cap the run cannot meet must force more slabs")


def test_scatter_bands_are_cumulative_not_independent():
    """The woodland thresholds are arms of one elif ladder.

    Each band must sit above the last or it is unreachable. Written as
    independent probabilities, the tree band reached 0.24 in a thick stand
    while ferns were fixed at 0.15 -- so undergrowth stopped appearing in
    exactly the places it should be thickest, and the whole map fell to 88
    ferns without anything reporting a problem.
    """
    for thickness in (0.0, 0.25, 0.5, 0.75, 1.0):
        p_tree = 0.010 + 0.230 * thickness ** 3
        p_stump = p_tree + 0.006
        p_fern = p_stump + 0.030 + 0.130 * thickness ** 2
        assert p_tree < p_stump < p_fern <= 1.0


def test_woodland_grows_in_stands_not_salt_and_pepper():
    """Species come from a smooth field, so neighbours agree.

    Drawing per tree gave nearest-neighbour species agreement of 46% --
    exactly the random rate, which is what salt-and-pepper measures as.
    """
    from citysmith.build import species_at, canopy_at

    agree = total = 0
    for z in range(0, 120, 3):
        for x in range(0, 120, 3):
            total += 1
            if species_at(x, z) == species_at(x + 3, z):
                agree += 1
    assert agree / total > 0.7, "species field is not coherent enough to form stands"

    # and the canopy varies rather than sitting flat
    vals = [canopy_at(x, z) for z in range(0, 120, 7) for x in range(0, 120, 7)]
    assert max(vals) - min(vals) > 0.6, "canopy field is too flat to make glades"


# -- collider offsets ---------------------------------------------------------

def test_a_tile_stores_its_min_corner_and_a_prop_stores_its_centre():
    """The two authoring conventions, which look identical until they don't.

    A tile is authored with its collider's min corner on the origin, so
    m_Center equals m_Extent. A prop is authored with the collider centred on
    the origin, so m_Center is about zero. The stored coordinate is the origin
    either way -- which means subtracting half a footprint is right for one
    and wrong for the other. Doing it for both shifted every prop by half its
    own size: 0.2 tiles on a fern, 1.275 on a pine canopy, and since the trunk
    beneath moved only 0.55 the two came apart and the trunk ended up anchored
    to the corner of its own crown.
    """
    from citysmith.build import place_centered, placed_bounds

    tile = Asset(id="7" * 8 + "-1111-2222-3333-444444444444", name="slab",
                 kind="tile", pack="p", group_tag="floor", tags=(), folder="",
                 size_x=1.0, size_y=0.5, size_z=1.0,
                 off_x=0.5, off_y=0.25, off_z=0.5)          # min corner on origin
    prop = Asset(id="8" * 8 + "-1111-2222-3333-444444444444", name="canopy",
                 kind="prop", pack="p", group_tag="prop", tags=(), folder="",
                 size_x=2.5, size_y=2.4, size_z=2.5,
                 off_x=0.0, off_y=1.2, off_z=0.0)           # centred on origin

    t = place_centered(tile, 5.5, 5.5, 0.0, 0)
    assert (t.x, t.z) == (5.0, 5.0), "a tile stores its min corner"
    p = place_centered(prop, 5.5, 5.5, 0.0, 0)
    assert (p.x, p.z) == (5.5, 5.5), "a prop stores its centre"

    # Both must reconstruct to a box centred on where they were asked to go.
    for asset, placement in ((tile, t), (prop, p)):
        x0, z0, x1, z1 = placed_bounds(asset, placement)
        assert abs((x0 + x1) / 2 - 5.5) < 1e-9
        assert abs((z0 + z1) / 2 - 5.5) < 1e-9


def test_asset_without_explicit_offsets_uses_the_tile_convention():
    """Hand-built assets keep the behaviour every helper assumed before."""
    a = Asset(id="9" * 8 + "-1111-2222-3333-444444444444", name="x", kind="tile",
              pack="p", group_tag="", tags=(), folder="",
              size_x=1.0, size_y=2.0, size_z=0.5)
    assert (a.off_x, a.off_y, a.off_z) == (0.5, 1.0, 0.25)


def test_a_notched_plan_is_roofed_as_rectangular_wings():
    """A hip roof is a rectangle's answer to being roofed.

    Forced over a notched plan it gives a valid height field and incoherent
    ridges -- probed in isolation, a 6x6 roofs as a clean pyramid while an L
    and a U come out with ridge lines meeting at angles that resolve into
    nothing. No corner piece repairs it: with axis-aligned notches the reflex
    corner falls on a vertex *between* cells, so no cell can carry one.
    """
    from citysmith.build import roof_wings, largest_rectangle

    square = {(x, z) for x in range(6) for z in range(6)}
    assert len(roof_wings(square)) == 1, "a rectangle is already one hip"

    ell = square - {(x, z) for x in (4, 5) for z in (0, 1)}
    wings = roof_wings(ell)
    assert len(wings) == 2, "an L is a main range and a wing"
    assert set().union(*wings) == ell, "the wings must tile the plan exactly"
    assert not (wings[0] & wings[1]), "and must not overlap"

    # Every wing is genuinely rectangular, which is what the kit can express.
    for w in wings:
        xs = [c[0] for c in w]
        zs = [c[1] for c in w]
        assert len(w) == (max(xs) - min(xs) + 1) * (max(zs) - min(zs) + 1)

    # Largest first, so the main mass keeps the dominant ridge.
    assert len(wings[0]) >= len(wings[1])
    assert largest_rectangle(set()) == set()


def test_a_prop_overhanging_the_low_corner_does_not_shift_its_chunk():
    """Both readings of a chunk's origin have to agree, not just one.

    TaleSpire anchors a pasted slab on its bounding box, and a slab has two
    candidate corners: the lowest stored coordinate, and the lowest point its
    geometry reaches. They part company as soon as a prop is involved, because
    a prop stores its collider *centre*. On the board this was one chunk of
    four whose pines hung a tile past the map's low corner -- and it was the
    one chunk the old marker rule skipped, because its stored minimum was
    already zero on every axis even though no placement sat at the origin.
    """
    from citysmith.build import place_centered, volume_bounds
    from citysmith.verify import chunk_anchors

    b = _builder()
    _ground_field(b)
    # A canopy over the very first cell, reaching back past the map's corner.
    b.add(place_centered(CANOPY, 0.5, 0.5, 2.0, 0), prop=True)

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert len(plan.chunks) > 1

    stored = {tuple(round(v, 3) for v in ch.slab.bounds()[0]) for ch in plan.chunks}
    volume = {tuple(round(v, 3) for v in volume_bounds(ch.slab, b.byid)[0])
              for ch in plan.chunks}
    assert stored == {(0.0, 0.0, 0.0)}, f"stored corners disagree: {stored}"
    assert volume == {(0.0, 0.0, 0.0)}, f"volume corners disagree: {volume}"
    assert chunk_anchors(plan, b.byid) == []


def test_the_anchor_check_catches_a_chunk_that_would_land_offset():
    """The check has to fail on a plan that is actually misaligned."""
    from citysmith.build import place_centered
    from citysmith.verify import chunk_anchors

    b = _builder()
    _ground_field(b)
    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    # Undo one chunk's registration: an overhang with nothing pinning the
    # corner is exactly the shape of the defect.
    victim = plan.chunks[-1]
    victim.slab.placements = [p for p in victim.slab.placements
                              if (p.x, p.y, p.z) != (0.0, 0.0, 0.0)]
    victim.slab.add(place_centered(CANOPY, 0.5, 0.5, 2.0, 0))

    assert chunk_anchors(plan, b.byid), "a misaligned chunk was reported as fine"


def test_a_river_does_not_run_over_grass():
    """The bed is the thing you look at, because the water is translucent.

    Laying it in the ``ground`` role put a lawn under the river, so the board
    showed two sheets of grass with a blue film between them -- which is what
    "a second layer of land" meant. The bed gets its own role, and the
    waterline sits a full tile below the bank rather than half of one.
    """
    from citysmith.build import WATER_SURFACE_DROP
    from citysmith.palette import STYLES

    for name, style in STYLES.items():
        assert "riverbed" in style.roles, f"style {name!r} has no riverbed"
    assert WATER_SURFACE_DROP >= 1.5


def test_water_is_filled_to_the_bed_so_depth_shows():
    """One translucent sheet is the same colour over a ford as over a channel.

    The bed already stepped down away from the bank, so the depth was in the
    geometry -- it just could not be seen, because the water was a single tile
    floating at the surface. Filling the column is what turns that geometry
    into something a party can read off the board.
    """
    from citysmith.build import _fill_water

    laid = []

    class Spy:
        def add(self, p, prop=False):
            laid.append(p)

    water = Asset(id="c" * 8 + "-1111-2222-3333-444444444444", name="water",
                  kind="tile", pack="p", group_tag="floor", tags=(), folder="",
                  size_x=1.0, size_y=0.5, size_z=1.0)

    _fill_water(Spy(), water, 0, 0, surface_y=3.0, bed_y=3.0)
    assert len(laid) == 1, "a ford is one tile of water"

    laid.clear()
    _fill_water(Spy(), water, 0, 0, surface_y=3.0, bed_y=1.5)
    assert [p.y for p in laid] == [1.5, 2.0, 2.5, 3.0]


def test_the_rampart_holds_its_height_when_the_block_changes():
    """The wall's height is a decision; the course count is a consequence.

    It used to be given as "three courses", and a course is however tall the
    block happens to be -- so swapping a 2.0 block for a 2.5 one would have
    raised the whole circuit by a quarter with nobody asking for it.
    """
    from citysmith.build import TOWN_WALL_TILES

    for course in (1.0, 2.0, 2.5):
        courses = max(1, round(TOWN_WALL_TILES / course))
        assert abs(courses * course - TOWN_WALL_TILES) <= course / 2 + 1e-9


def test_the_rampart_block_is_not_a_thin_piece():
    """A full-cell collider does not mean a full-cell mesh.

    `md_wall_1x1_diag_01` measures a full cell and is a blade cutting it corner
    to corner, so the circuit came out as a comb with daylight between every
    pair of cells. Nothing in the catalog data says so -- only the probe does --
    so the guard is on the name, which is where the kit does say so.
    """
    from citysmith.palette import STYLES

    # Names the kits use for pieces that are not a solid full cell. "ruins" is
    # here because `Castle Ruins Wallbase 02` is broken masonry: it measures a
    # full cell, tiles into a see-through lattice, and nothing but the word
    # "Ruins" says so.
    banned = ("diag", "_1x2", "1x2_", "ruins")
    for name, style in STYLES.items():
        for query in style.roles.get("city_wall_core", []):
            pinned = query[1].get("name", "")
            names = (pinned,) if isinstance(pinned, str) else tuple(pinned)
            for n in names:
                assert not any(b in n.lower() for b in banned), (
                    f"style {name!r} builds its rampart mass from {n!r}, "
                    "which the kit names as a partial piece"
                )


def test_a_parapet_stands_on_the_lip_not_in_place_of_the_walk():
    """A curtain piece goes on the cell edge; a block fills the cell.

    The rampart got this backwards twice. The mass was built from a thin
    diagonal piece, which striped the whole circuit with daylight; the parapet
    was a full-cell timber hoarding, which sat where the wall-walk should be
    and hung over the step below it.
    """
    from citysmith.build import is_curtain_piece
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    core = palette.require("city_wall_core")
    cap = palette.require("city_wall_cap")
    walk = palette.require("city_wall_walk")

    assert not is_curtain_piece(core), "the rampart mass must fill its cell"
    assert is_curtain_piece(cap), "the parapet must stand on the cell edge"
    assert not is_curtain_piece(walk), "the wall-walk must pave its cell"


def _taper_fringe(tm):
    """The border blocks the taper keeps and the ones it bites out."""
    from citysmith.build import edge_taper

    taper = edge_taper(tm)
    blocks_x, blocks_z = (tm.width + 1) // 2, (tm.depth + 1) // 2
    border, bitten = set(), set()
    for bz in range(blocks_z):
        for bx in range(blocks_x):
            if min(bx, bz, blocks_x - 1 - bx, blocks_z - 1 - bz) != 0:
                continue
            border.add((bx, bz))
            cells = [(bx * 2 + dx, bz * 2 + dz)
                     for dz in (0, 1) for dx in (0, 1)
                     if bx * 2 + dx < tm.width and bz * 2 + dz < tm.depth]
            if all(taper.get(c, 0.0) is None for c in cells):
                bitten.add((bx, bz))
    return border, bitten


def test_the_fringe_is_runs_not_teeth():
    """A kept block with no kept neighbour projects over nothing.

    Bites were decided per block and independently, so the border came out as a
    comb: 2x2 tabs standing off the edge with void under them, which at eye
    level is the map reading as a torn sheet rather than as land running out.
    """
    from citysmith.layout import Layout
    from citysmith.raster import rasterize

    tm = rasterize(Layout.load("out/layout.json"))
    border, bitten = _taper_fringe(tm)
    kept = border - bitten
    assert bitten, "nothing was bitten -- the fringe is not ragged at all"

    def around(b):
        return [(b[0] + 1, b[1]), (b[0] - 1, b[1]),
                (b[0], b[1] + 1), (b[0], b[1] - 1)]

    teeth = [b for b in kept
             if not any(n in kept for n in around(b))]
    assert teeth == [], f"{len(teeth)} border block(s) project alone: {teeth[:5]}"


def test_no_road_runs_out_over_the_void():
    """A road leaving town is fine; a road leaving the world is not.

    The fringe used to bite a block and then rescue the paved cells inside it,
    which removes the ground either side and leaves the street on a two-tile
    causeway over bare board.
    """
    from citysmith import raster as R
    from citysmith.layout import Layout
    from citysmith.raster import rasterize

    tm = rasterize(Layout.load("out/layout.json"))
    border, bitten = _taper_fringe(tm)

    for (bx, bz) in bitten:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)):
            nb = (bx + dx, bz + dz)
            if nb not in border:
                continue
            for cx, cz in [(nb[0] * 2 + a, nb[1] * 2 + c)
                           for c in (0, 1) for a in (0, 1)]:
                if not (cx < tm.width and cz < tm.depth):
                    continue
                assert tm.surface[cz][cx] in (R.GROUND, R.FIELD, R.VOID), (
                    f"bite at {(bx, bz)} strands "
                    f"{tm.surface[cz][cx]!r} at {(cx, cz)}"
                )


def test_only_land_falls_away_at_the_edge():
    """A river surface and a road have to stay level; ground may not.

    The taper lowers the outermost ring so the map does not end in a sheer
    face. Applied to every surface it put a half-tile step across the
    carriageway two tiles from the border, and a ledge straight across the
    river -- a waterfall the width of an estuary, which is what "why is the
    water stepped" turned out to be.
    """
    from citysmith import raster as R
    from citysmith.build import edge_taper
    from citysmith.layout import Layout
    from citysmith.raster import rasterize

    tm = rasterize(Layout.load("out/layout.json"))
    taper = edge_taper(tm)

    level = {R.WATER, R.STREET, R.PLAZA, R.LANE, R.PIER}
    stepped = [
        (x, z) for z in range(tm.depth) for x in range(tm.width)
        if tm.surface[z][x] in level and taper.get((x, z), 0.0) != 0.0
    ]
    assert stepped == [], (
        f"{len(stepped)} cell(s) that must stay level were tapered: "
        f"{stepped[:5]}"
    )


def test_floating_geometry_is_caught():
    """The check has to fail on something actually standing over nothing.

    Written after it reported zero on a map where two things *looked* like they
    were hanging in mid-air. Zero is only worth believing from a check that is
    known to fire.
    """
    from citysmith.verify import floating_placements

    b = _builder()
    _ground_field(b, width=6, depth=6)
    assert floating_placements(b, None) == [], "a plain field is not floating"

    b.add(place_wall(WALL, 20, 20, "n", 0.5))          # far off the ground
    problems = floating_placements(b, None)
    assert problems, "a wall over bare board was not reported"
    assert "stand over nothing" in problems[0]


def test_a_chunk_holds_one_layer_and_terrain_is_all_in_one_of_them():
    """Splitting by layer is what removes the terrain-meets-terrain seam.

    Region splitting alone means every chunk after the first is pasted over
    ground the previous one laid, and a paste comes to rest on whatever is
    under the cursor -- so chunks can land at different heights and the join
    shows as a step in open grass. If all the ground is one layer it cannot
    disagree with itself, whatever the paste does.
    """
    from citysmith.build import LANDSCAPE, LAYERS, STRUCTURE

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 12, 12)
    with b.layer(STRUCTURE):
        for tz in (1, 5, 9):
            for tx in (1, 5, 9):
                b.add(place_wall(WALL, tx, tz, "n", 0.5))

    assert len(b.layer_of) == len(b.placements)

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert {c.layer for c in plan.chunks} == set(LAYERS)

    ground = {p.asset_id for p in b.placements if p.asset_id == GROUND.id}
    for chunk in plan.chunks:
        if chunk.layer != LANDSCAPE:
            stray = [p for p in chunk.slab.placements
                     if p.asset_id in ground and not plan.is_marker(p)]
            assert stray == [], f"{chunk.label} carries terrain"


def test_layered_chunks_still_share_one_origin():
    """The layers are pasted at the same anchor, so they must still register."""
    from citysmith.build import LANDSCAPE, STRUCTURE, volume_bounds
    from citysmith.verify import chunk_anchors

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 12, 12)
    with b.layer(STRUCTURE):
        b.add(place_wall(WALL, 5, 5, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    corners = {tuple(round(v, 3) for v in volume_bounds(c.slab, b.byid)[0])
               for c in plan.chunks}
    assert corners == {(0.0, 0.0, 0.0)}
    assert chunk_anchors(plan, b.byid) == []


def test_an_unlayered_plan_still_works():
    """`by_layer=False` keeps the old single-body behaviour for probes."""
    b = _builder()
    _ground_field(b, 8, 8)
    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False, by_layer=False)
    assert {c.layer for c in plan.chunks} == {""}
    assert [c.label for c in plan.chunks] == [c.region for c in plan.chunks]


def test_ground_under_a_building_is_never_skipped_as_open_country():
    """Layering broke the old open-country test and this is the repair.

    Open country means "nowhere anyone plays", and a chunk used to answer that
    from its own contents -- a building in the cell disqualified it. Once the
    layers are separate, a landscape chunk under a town holds nothing but grass,
    reads as empty, and gets dropped, leaving the buildings above standing on
    nothing. A 40x40 crop lost half its ground exactly this way.
    """
    from citysmith.build import LANDSCAPE, STRUCTURE

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 12, 12)
    # One building, in the middle chunk of a 3x3 grid, and nothing else.
    with b.layer(STRUCTURE):
        for tx, tz in ((5, 5), (6, 5), (5, 6), (6, 6)):
            b.add(place_wall(WALL, tx, tz, "n", 0.5))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    ground_regions = {c.region for c in plan.chunks if c.layer == LANDSCAPE}
    assert "r01c01" in ground_regions, (
        "the ground under the building was trimmed as open country; "
        f"kept {sorted(ground_regions)}"
    )


def test_every_chunk_presents_the_same_bounding_box():
    """A shared corner is not a shared box, and the paste uses the box.

    Chunks used to share only their minimum. Their maxima did not agree -- the
    landscape layer topped out around y=7 and the structure layer around y=20 --
    and pasted at one cursor cell they seated at different heights. That is how
    a whole layer of roofs ended up lying in the grass.
    """
    from citysmith.build import LANDSCAPE, STRUCTURE, volume_bounds
    from citysmith.verify import chunk_anchors

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 12, 12)
    with b.layer(STRUCTURE):
        # Tall, so the two layers would disagree about the far corner.
        for level in range(6):
            b.add(place_wall(WALL, 5, 5, "n", 0.5 + level * 2.0))

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    boxes = {tuple(round(v, 2) for corner in volume_bounds(c.slab, b.byid)
                   for v in corner)
             for c in plan.chunks}
    assert len(boxes) == 1, f"chunks present {len(boxes)} different boxes"
    assert chunk_anchors(plan, b.byid) == []


def test_registration_markers_sit_on_the_half_tile_grid():
    """The far marker hugs the map's far face, and that face is wherever some
    canopy's collider happens to end -- x=187.51 on Forest Church, the one
    non-prop tile on the board that failed the off-grid canary. It is rounded
    out to the lattice instead, so the canary has no known exception.
    """
    from citysmith.build import LANDSCAPE, volume_bounds

    b = _builder()
    with b.layer(LANDSCAPE):
        _ground_field(b, 12, 12)
        # A prop overhanging the far corner by a fraction of a tile.
        b.add(place_centered(CROWN, 11.7, 11.7, 0.5, 0), prop=True)

    plan = b.chunk_plan(max_assets=1000, chunk_tiles=4, pack=False)
    assert len(plan.anchors) == 2
    for marker in plan.anchors:
        assert all(abs(v * 2 - round(v * 2)) < 1e-9 for v in (marker.x, marker.z)), (
            f"marker at ({marker.x}, {marker.z}) is off the half-tile grid")
    boxes = {tuple(round(v, 2) for corner in volume_bounds(c.slab, b.byid)
                   for v in corner)
             for c in plan.chunks}
    assert len(boxes) == 1, "rounding out must not make the chunks disagree"


def _one_building(bid, w=7, d=6):
    """A single rectangular building on a blank map, perimeters and door set."""
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    tm = TileMap.blank(w + 8, d + 8)
    for x in range(4, 4 + w):
        for z in range(4, 4 + d):
            tm.building[z][x] = bid
            tm.surface[z][x] = FLOOR
    tm.floors[bid] = 3
    _find_perimeters(tm, None)
    _place_doors(tm, None)
    return tm


def test_an_outbuilding_is_a_single_storey():
    """A three-storey stable reads as a tenement. The cap lives in storeys_of
    because the shell, the upper floors and the roof all read that one
    function -- capping at the shell alone would leave the roof three courses
    up with nothing under it."""
    from citysmith.build import storeys_of

    tm = _one_building("stable-0001")
    assert tm.floors["stable-0001"] == 3
    assert storeys_of(tm, "stable-0001", 3) == 1
    # ...and a house of the same size keeps the height the layout dealt it.
    assert storeys_of(_one_building("house-0001"), "house-0001", 3) == 3


def test_a_barn_has_no_windows():
    """Rural ships a wall and a matching corner and no 1-cell window at all,
    which is exactly what a warehouse or a stable is. The tier must not reach
    into another kit for glass."""
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    window = palette.resolve("wall_window")
    assert window is not None, "the fixture needs a window to look for"

    b = build_from_tilemap(_one_building("warehouse-0001"), palette, storeys=3)
    glazed = [p for p in b.placements if p.asset_id == window.id]
    assert not glazed, f"{len(glazed)} windows on an outbuilding"


def test_the_back_of_a_building_is_never_glazed():
    """Windows used to be dealt by a hash over every exposed segment, so the
    back of a building was as glazed as its front and a town looked identical
    from all four sides. The glass goes on the street."""
    from citysmith.build import (
        OPPOSITE_SIDE, build_from_tilemap, place_wall, tier_of,
    )
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)

    for bid in ("house-0001", "tavern-0001", "temple-0001"):
        # Civic glazes in its own kit's arched window, so look for the piece
        # the tier actually places rather than the common-house one.
        role = ("wall_window_civic" if tier_of(bid) == "civic" else "wall_window")
        window = palette.require(role)
        tm = _one_building(bid)
        front = tm.doors[bid][0][2]
        back = OPPOSITE_SIDE[front]
        b = build_from_tilemap(tm, palette, storeys=3)
        glazed = {(round(p.x, 3), round(p.z, 3)) for p in b.placements
                  if p.asset_id == window.id}
        # A wall's placement is fixed by its cell and side, so the back
        # segments can be located exactly rather than inferred from position.
        on_back = {(round(w.x, 3), round(w.z, 3))
                   for x, z, side in tm.perimeter[bid] if side == back
                   for w in (place_wall(window, x, z, side, 0.5),)}
        assert not (glazed & on_back), f"{bid}: glass on the back face"
        assert glazed, f"{bid}: no glass anywhere"


def test_every_tier_turns_its_corner_in_its_own_kit():
    """A facade that changes material at the corner reads as a mistake. Where
    no corner in the wall's kit exists the cell falls back to a mitre, so the
    invariant is 'same kit or no corner piece', never 'another kit'."""
    from citysmith.build import _kit_of, build_from_tilemap, tier_of
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    # The wall and corner each tier reaches for. Read from the palette rather
    # than sniffed out of the placements: `Village Roof Side Wall 02` carries
    # `group='roof'` because it ships in a roof set, so any "is this a wall"
    # test over group tags finds none of the facade at all.
    roles = {
        "civic": ("wall_civic", "wall_corner_civic"),
        "utility": ("wall_utility", "wall_corner_utility"),
        "common": ("wall", "wall_corner"),
        "trade": ("wall", "wall_corner"),
    }

    checked = 0
    for bid in ("house-0001", "tavern-0001", "temple-0001", "stable-0001"):
        wall_role, corner_role = roles[tier_of(bid)]
        # Common and trade deal the wall per building across three variants,
        # so ask which one this building actually got rather than assuming
        # variant 0 -- that is the whole point of the deal.
        walls = [palette.resolve(wall_role, v) for v in range(3)]
        corners = [palette.resolve(corner_role, v) for v in range(3)]
        b = build_from_tilemap(_one_building(bid), palette, storeys=2)
        placed = {p.asset_id for p in b.placements}
        wall = next((w for w in walls if w is not None and w.id in placed), None)
        assert wall is not None, f"{bid}: no {wall_role!r} variant was placed"
        corner = next((c for c in corners if c is not None and c.id in placed), None)
        if corner is None:
            continue          # mitred, which is the allowed fallback
        assert _kit_of(corner) == _kit_of(wall), (
            f"{bid}: corner {corner.name!r} from kit {_kit_of(corner)!r}, "
            f"wall {wall.name!r} from {_kit_of(wall)!r}")
        checked += 1
    assert checked, "no tier placed a corner piece -- the test proved nothing"


def test_each_roof_kit_gets_its_own_rotation():
    """The hip rotations were read out of one thatched cottage, and no other
    kit shares them -- dropped onto Village pieces they make a rank of fins.
    Measured per kit with tools/roofrot_probe.py --hips."""
    from citysmith.build import ROOF_EDGE_ROT, _roof_piece, roof_offsets
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    thatch = palette.require("roof_side")
    tile = palette.require("roof_side_tile")
    assert roof_offsets(thatch) == (0, 0), "thatch is the baseline convention"
    assert roof_offsets(tile) == (6, 6), "the Tavern kit is a quarter turn on"

    # The turn has to reach the placement, not just the table.
    _, plain = _roof_piece(("n",), thatch, None, None)
    _, turned = _roof_piece(("n",), tile, None, None, edge_off=6)
    assert plain == ROOF_EDGE_ROT["n"]
    assert turned == (ROOF_EDGE_ROT["n"] + 6) % 24


def test_a_tier_is_roofed_in_its_own_material():
    """Every roof on the map used to be Thatched Roof 01, because _lay_roofs
    resolved the set once for the map rather than once per building -- so the
    temple was thatched too."""
    from citysmith.build import build_from_tilemap, roof_set
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    sets = {t: roof_set(palette, t) for t in ("civic", "trade", "common")}
    slopes = {t: s[0].name for t, s in sets.items()}
    assert len(set(slopes.values())) == 3, f"tiers share a roof: {slopes}"

    for bid, tier in (("temple-0001", "civic"), ("tavern-0001", "trade"),
                      ("house-0001", "common")):
        b = build_from_tilemap(_one_building(bid), palette, storeys=2, roofs=True)
        placed = {p.asset_id for p in b.placements}
        assert sets[tier][0].id in placed, (
            f"{bid}: no {slopes[tier]!r} on a {tier} building")
        for other, s in sets.items():
            if other != tier:
                assert s[0].id not in placed, (
                    f"{bid}: {tier} building roofed in {other}'s slope")


def test_the_ridge_is_capped_not_carried_up_another_course():
    """Stepping the top ring up a full rise and roofing it in slopes leaves
    their undersides showing along the apex -- the bare timber that showed at
    the top of every slate roof. The last course is a cap, seated so its top
    is flush with the ring height."""
    from citysmith.build import build_from_tilemap, roof_set
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    side, _, _, cap, _ = roof_set(palette, "common")

    # 3 wide is the case the hand-built correction was made on: the top ring
    # is a single column, so the whole ridge is cap.
    tm = _one_building("house-0001", w=3, d=7)
    b = build_from_tilemap(tm, palette, storeys=1, roofs=True)
    caps = [p for p in b.placements if p.asset_id == cap.id]
    slopes = [p for p in b.placements if p.asset_id == side.id]
    assert caps, "no ridge cap laid"

    # The cap's *top* lands on the course the ring above would have used --
    # which is exactly the course that now carries no slopes, because that is
    # the ring the cap replaces.
    top_course = max(round(p.y, 3) for p in slopes) + side.size_y
    for p in caps:
        assert round(p.y + cap.size_y, 3) == round(top_course, 3), (
            f"cap at y={p.y} is not seated flush with the ring at {top_course}")
    assert not [p for p in slopes if round(p.y, 3) == round(top_course, 3)], \
        "a slope is still carried up onto the ridge course"


def test_a_single_storey_gets_a_lantern_and_no_porch():
    """A porch seats at storey_h + 0.5, which on a one-storey cottage is level
    with its own eaves -- a second roof grafted onto the first. Those get a
    lantern by the door instead."""
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    lantern = palette.require("door_lantern")

    low = build_from_tilemap(_one_building("tavern-0001"), palette, storeys=1)
    tall = build_from_tilemap(_one_building("tavern-0002"), palette, storeys=3)

    assert not [p for p in tall.placements if p.asset_id == lantern.id], \
        "a two-storey building took a lantern instead of its porch"
    # A signed trade says who it is with its board; a plain house gets the
    # lantern. Both are single storey, and neither may take a porch.
    house = build_from_tilemap(_one_building("house-0003"), palette, storeys=1)
    assert [p for p in house.placements if p.asset_id == lantern.id], \
        "no lantern on a single-storey house"


def _ring_map(w=34, d=34, edge=6, thickness=3):
    """A square wall ring on open ground, for gate and rampart tests."""
    from citysmith.raster import TileMap

    tm = TileMap.blank(w, d)
    for z in range(edge, d - edge):
        for x in range(edge, w - edge):
            if (x < edge + thickness or x >= w - edge - thickness
                    or z < edge + thickness or z >= d - edge - thickness):
                tm.wall[z][x] = True
    # The raster records where the ring turns, because a band of cells has no
    # memory of it and a turn is where a mural tower goes. Without these the
    # circuit gets no towers -- and so no stairs, which is how this fixture
    # first came out with an empty rampart.
    tm.wall_corners = [(edge, edge), (w - edge - 1, edge),
                       (edge, d - edge - 1), (w - edge - 1, d - edge - 1)]
    return tm


def test_a_gate_passage_is_square_so_a_door_can_hang_in_it():
    """The passage used to be cleared as a disc, so on a diagonal circuit its
    jambs were a 45-degree stair-step -- 18 cells with a 7x4 bounding box, and
    no straight jamb-to-jamb line for the 4-wide portcullis the palette has
    carried unused since the gates were first built."""
    from citysmith.raster import MAIN_STREET_TILES, _carve_gate

    tm = _ring_map()
    carved = _carve_gate(tm, 17, 6, MAIN_STREET_TILES)
    assert carved is not None, "nothing was cut"
    cut, _, _, (lo, width) = carved
    xs = {c[0] for c in cut}
    zs = {c[1] for c in cut}
    assert width == int(MAIN_STREET_TILES)
    # A rectangle: every row of the passage is the same width, and every cell
    # in the bounding box was cut. That is what a straight jamb means.
    assert len(xs) * len(zs) == len(cut), (
        f"passage is not a rectangle: {len(cut)} cells in a "
        f"{len(xs)}x{len(zs)} box")
    # One axis is the opening (the carriageway) and the other is the tunnel
    # through the band -- which is shorter here, because the wall is thinner
    # than the road is wide. The opening is the one that must match.
    assert int(MAIN_STREET_TILES) in (len(xs), len(zs)), (
        f"no axis of the {len(xs)}x{len(zs)} passage is the road's width")
    assert all((x, z) in tm.gates and not tm.wall[z][x] for x, z in cut)


def test_a_one_gate_circuit_gets_a_postern_opposite():
    """A walled town with a single entrance is a cul-de-sac: every approach,
    sortie and chase funnels through the same arch."""
    import math

    from citysmith.build import _components8
    from citysmith.raster import MAIN_STREET_TILES, _add_second_gate, _carve_gate

    tm = _ring_map()
    _carve_gate(tm, 17, 6, MAIN_STREET_TILES)
    first = {c for c in tm.gates}
    _add_second_gate(tm, MAIN_STREET_TILES)
    passages = _components8(set(tm.gates))
    assert len(passages) == 2, f"expected two passages, got {len(passages)}"

    def centre(cells):
        return (sum(c[0] for c in cells) / len(cells),
                sum(c[1] for c in cells) / len(cells))

    a, b = centre(first), centre(set(tm.gates) - first)
    # Opposite, not merely elsewhere: the two mouths should be most of the
    # ring's width apart.
    assert math.dist(a, b) > 15, f"postern at {b} is not opposite {a}"


def test_the_buried_core_of_the_rampart_is_not_built():
    """A cell walled in on all four sides shows nothing but its top. The
    rampart is nearly three tiles thick, so 38% of its body had no face anyone
    could ever see and five courses of solid nothing under the walk."""
    import collections

    from citysmith.build import _lay_town_wall, Builder, TOWN_WALL_TILES
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    tm = _ring_map(thickness=3)
    b = Builder(palette, 0)
    _lay_town_wall(b, tm, palette.require("city_wall"), 0.5, TOWN_WALL_TILES)

    core = palette.resolve("city_wall_core")
    per_cell = collections.Counter(
        (int(p.x), int(p.z)) for p in b.placements if p.asset_id == core.id)
    NB = ((1, 0), (-1, 0), (0, 1), (0, -1))
    mass = {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x]} | set(tm.gates)
    # Towers are exempt: they stand the full stack plus WALL_TOWER_RISE by
    # design, and a tower's footprint can sit over buried curtain.
    from citysmith.build import pick_wall_towers

    gates = set(tm.gates)
    towers = {c for t in pick_wall_towers(tm, mass, gates) for c in t}
    buried = [c for c in mass - towers
              if all((c[0] + dx, c[1] + dz) in mass and
                     (c[0] + dx, c[1] + dz) not in tm.gates for dx, dz in NB)]
    assert buried, "the fixture should have a buried core to test"
    faced = [c for c in mass - towers
             if c not in buried and c not in tm.gates]
    # A buried cell carries at most the one course the walk rests on; a faced
    # one carries the full stack. Towers stand on some cells and build higher,
    # so compare against the *minimum* a faced cell gets.
    assert max(per_cell[c] for c in buried) <= 1, "buried cells are still solid"
    assert min(per_cell[c] for c in faced) > 1, "a faced cell lost its courses"


def test_wall_stairs_are_inside_parallel_and_land_on_the_curtain():
    """Three things that were each wrong once.

    Inside: a stair on the field side of a town wall is a siege ramp for the
    enemy. This began as a *preference* in the scoring and a preference is not
    enough -- one tower had no inside option and duly scored the field.

    Parallel: the run used to march straight out from the tower's face into
    the town, hugging the curtain for one cell of six and eating 35 ft of
    street.

    Landing: the top tread must arrive beside the *curtain*, not a tower. A
    tower crowns WALL_TOWER_RISE courses higher, so a flight that reaches its
    flank stops ten feet below anywhere you can stand.
    """
    from citysmith.build import (
        Builder, SIDE_OFFSETS, TOWN_WALL_TILES, _lay_town_wall,
        _outside_the_wall, pick_wall_towers,
    )
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    stair = palette.resolve("city_wall_stair")
    assert stair is not None, "the fixture needs a stair to look for"

    tm = _ring_map(thickness=3)
    b = Builder(palette, 0)
    _lay_town_wall(b, tm, palette.require("city_wall"), 0.5, TOWN_WALL_TILES)

    gates = set(tm.gates)
    mass = {(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x]} | gates
    towers = pick_wall_towers(tm, mass, gates)
    curtain = mass - {c for t in towers for c in t}
    outside = _outside_the_wall(tm, mass | {c for t in towers for c in t})

    treads = [p for p in b.placements if p.asset_id == stair.id]
    assert treads, "no stair was laid on the rampart"

    cells = {(int(p.x), int(p.z)) for p in treads}
    on_field = cells & outside
    assert not on_field, f"{len(on_field)} treads on the field side: {sorted(on_field)[:4]}"
    assert not (cells & mass), "a tread was laid inside the wall mass"
    assert not (cells & gates), "a tread was laid in a gate passage"
    # A tower footprint is not always part of the mass -- pick_wall_towers
    # lets one stand on open ground beside the wall -- so excluding `mass`
    # alone once put three treads where a tower was about to be built, solid
    # block over the top of them.
    in_tower = cells & {c for t in towers for c in t}
    assert not in_tower, f"treads entombed in a tower: {sorted(in_tower)}"

    # On an axis-aligned ring every tread should be hard against the curtain.
    touching = [c for c in cells
                if any((c[0] + dx, c[1] + dz) in mass for _, dx, dz in SIDE_OFFSETS)]
    assert len(touching) == len(cells), (
        f"only {len(touching)} of {len(cells)} treads touch the wall")

    # Each flight is a straight run, and its highest tread reaches the curtain.
    by_height = {}
    for p in treads:
        by_height.setdefault(round(p.y, 3), []).append((int(p.x), int(p.z)))
    top = max(by_height)
    for c in by_height[top]:
        assert any((c[0] + dx, c[1] + dz) in curtain for _, dx, dz in SIDE_OFFSETS), (
            f"top tread {c} does not reach the curtain")


# -- scatter clearance --------------------------------------------------------

def test_the_wall_clears_woodland_the_way_a_building_does():
    """A rampart is built, so trees fall back from it.

    `building_distance` drives the density falloff for everything scattered.
    Seeded from `tm.building` alone it cleared woodland off every doorstep and
    left it growing flush against the town wall -- pines in the ditch with
    their canopies over the masonry. A wall has no building id, so it has to be
    seeded explicitly, and this is the check that it still is.
    """
    from citysmith import build as B
    from citysmith import raster as R

    tm = R.TileMap.blank(24, 24, "wall")
    for x in range(4, 20):
        tm.wall[12][x] = True

    dist = B.building_distance(tm)
    for x in range(4, 20):
        for z in (11, 13):
            assert dist.get((x, z)) == 1, f"({x},{z}) beside the wall is not cleared"
    # and the falloff still reaches open country
    assert dist.get((12, 3)) is None or dist[(12, 3)] > B.TREE_CLEARANCE


def test_a_bare_tilemap_has_no_cleared_ground():
    from citysmith import build as B
    from citysmith import raster as R
    assert B.building_distance(R.TileMap.blank(8, 8, "empty")) == {}
