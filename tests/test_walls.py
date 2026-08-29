"""Wall families, courses and run packing.

These are the invariants the wall-run work has to keep, stated so that
`tasks.json` has something that can only pass after the work is done. Every
one of them was a defect on a board first.
"""

import sys

import pytest

sys.path.insert(0, ".")

from citysmith import walls as W


# -- packing -------------------------------------------------------------------

def _cells(spans):
    """A run rendered as one character per cell: W wide, n narrow."""
    return "".join(("W" if s == 2 else "n") * s for _, s in spans)


@pytest.mark.parametrize("length", range(1, 13))
@pytest.mark.parametrize("rule", W.PACK_RULES)
def test_a_packed_run_covers_every_cell_exactly_once(length, rule):
    """Whatever the rule, the pieces tile the run -- no gap, no overlap.

    A gap is daylight through a facade and an overlap is two panels in one
    square. Both have shipped on this project before, from geometry that
    looked right in the file.
    """
    spans = W.pack(length, 0, rule)
    at = 0
    for off, span in spans:
        assert off == at, f"{rule} at {length}: piece starts at {off}, expected {at}"
        assert span in (1, 2)
        at += span
    assert at == length, f"{rule} at {length}: covered {at} cells of {length}"


@pytest.mark.parametrize("length", (5, 7, 9, 11))
def test_the_narrow_panel_does_not_stack_between_courses(length):
    """The remainder moves course to course, so it never draws a column.

    `centred` puts it in the same slot on every storey. On a Rural run of 7
    that is a dark stripe of boarding running the full height of the wall --
    the narrow piece is a different panel, so stacking it draws a line nobody
    chose. Read on the board `PROBE wall bond stagger` at three storeys.
    """
    seen = {_cells(W.pack(length, lvl, W.DEFAULT_PACK)) for lvl in range(3)}
    assert len(seen) > 1, (
        f"run of {length}: the default rule laid the identical course three "
        f"times -- {seen.pop()}")

    stacked = _cells(W.pack(length, 0, "centred"))
    assert all(_cells(W.pack(length, lvl, "centred")) == stacked
               for lvl in range(3)), "centred is the control and should stack"


@pytest.mark.parametrize("length", (4, 6, 8))
def test_only_the_full_bond_breaks_an_even_run(length):
    """An even run has no remainder, so the rules that move one cannot help it.

    Runs of 4, 6 and 8 are about a third of every wall segment in all three
    towns, which is why `bondfull` exists at all.
    """
    for rule in ("centred", "shift", "bond"):
        courses = {_cells(W.pack(length, lvl, rule)) for lvl in range(3)}
        assert len(courses) == 1, f"{rule} unexpectedly varies on an even run"

    joints = [{off for off, _ in W.pack(length, lvl, "bondfull")} - {0}
              for lvl in range(2)]
    assert not (joints[0] & joints[1]), (
        f"bondfull at {length}: courses share joints {sorted(joints[0] & joints[1])}")


def test_a_run_too_short_for_a_wide_piece_falls_back_to_single():
    assert W.pack(1, 0, "shift") == [(0, 1)]
    assert W.pack(0, 0, "shift") == []


# -- runs ----------------------------------------------------------------------

def test_runs_break_at_a_gap_and_at_a_change_of_face():
    """A door takes a cell out and splits its face into two runs.

    The caller removes the cells a corner piece or a doorway owns before
    grouping, so this has to group whatever it is handed rather than assuming
    a face is contiguous.
    """
    segs = [(x, 0, "n") for x in (1, 2, 3, 6, 7)] + [(0, z, "w") for z in (1, 2)]
    got = sorted(W.runs_of(segs))
    assert ("n", 1, 0, 3) in got
    assert ("n", 6, 0, 2) in got
    assert ("w", 0, 1, 2) in got
    assert len(got) == 3


# -- families ------------------------------------------------------------------

@pytest.fixture(scope="module")
def families():
    from citysmith.catalog import load_or_build
    return W.families(load_or_build())


def test_the_common_house_wall_family_is_complete(families):
    """The medieval wall resolves into a kit that ships the whole family.

    `Village Roof Side Wall 02` and `Tavern Wall 01` are both `folder='Tavern'`
    -- one kit, since the kit is the folder -- but they do not mix at panel
    granularity: a Village panel between two Tavern ones carries no timber
    frame of its own and reads as a bare plaster patch. The house is built from
    the Tavern wall proper, whose own narrow partner blends invisibly.
    """
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    wall = palette.require("wall")
    assert wall.folder == "Tavern"
    assert wall.name == "Tavern Wall - Small 01"

    fam = families[wall.folder]
    assert fam.complete, f"Tavern family is incomplete: {fam.summary()}"
    assert fam.piece("wall", 1).id == wall.id
    assert fam.wide is not None, "no 2-cell panel the common house can use"
    assert fam.piece("window", 2) is not None
    assert fam.piece("corner", 1) is not None


def test_a_wide_panel_matches_its_kits_storey_height(families):
    """A family's pieces can all share a course, or they are not a family.

    Nineteen kits ship a 2.5-tall Wall/Floor combination beside their 2.0
    pieces -- a different storey system -- and Tavern's 2.5 corner was being
    dealt for its 2.0 wall. The wide panel keeps a measured slop because
    `Tavern Wall 01` is 2.03 against its kit's 2.00; everything else is exact.
    """
    for kit, fam in families.items():
        h = fam.storey_height
        assert h is not None, f"{kit}: no storey height"
        for (role, span, course), pieces in fam.pieces.items():
            slop = W.WIDE_HEIGHT_SLOP if span == 2 else 1e-6
            for a in pieces:
                assert abs(a.size_y - h) <= slop, (
                    f"{kit}: {a.name!r} is {a.size_y} tall in a {h} family")
        if fam.wide is not None:
            assert abs(fam.wide.size_y - h) <= W.WIDE_HEIGHT_SLOP


def test_a_family_never_admits_a_roof_a_fence_or_an_archway(families):
    """Three pieces measure exactly like a wall and are not one.

    `Moorgoth Large Roof`, `Ship fence end port` and `Desert Arch top` were
    each dealt as their kit's default wall before the group filter existed. A
    collider cannot tell them apart -- all three are a thin panel about two
    tiles tall -- so the kit's own word has to.
    """
    banned = {"Moorgoth Large Roof", "Ship fence end port", "Desert Arch top"}
    for kit, fam in families.items():
        names = {a.name for v in fam.pieces.values() for a in v}
        assert not (names & banned), f"{kit} admitted {names & banned}"


def test_the_course_word_is_read_wherever_it_appears():
    """Marble Palace infixes it and numbers the piece afterwards."""
    assert W.course_of("castle wall 1x1 base") == "base"
    assert W.course_of("Palace Marble wall mid 01") == "mid"
    assert W.course_of("Palace Marble wall top 1x1") == "top"
    assert W.course_of("Rural Wall 01") == "mid"


def test_a_kit_with_no_course_system_answers_the_same_piece_every_course(families):
    """Rural names no course, so every storey is its one wall. That is right."""
    fam = families["Rural"]
    assert fam.courses == ("mid",)
    got = {fam.piece("wall", 1, c).name for c in W.COURSES}
    assert len(got) == 1, got


def test_castle_puts_its_plinth_on_the_ground_course_only(families):
    """Castle Fortified ships `base` variants and nothing read them.

    Our corner is `castle wall corner 1x1 base` -- a plinth -- and it was going
    on every floor of every civic building.
    """
    fam = families["Castle Fortified"]
    assert "base" in fam.courses
    assert fam.piece("wall", 1, "base").name == "castle wall 1x1 base"
    assert fam.piece("wall", 1, "mid").name == "castle wall 1x1"
    assert fam.piece("wall", 1, "top").name == "castle wall 1x1", (
        "no top course in this kit, so the head falls back to the plain piece")


def test_marble_palace_carries_all_three_courses(families):
    """The one kit in the library with a full base/mid/top wall system."""
    fam = families["Marble Palace"]
    assert fam.courses == ("base", "mid", "top")
    got = [fam.piece("wall", 2, c).name for c in ("base", "mid", "top")]
    assert len(set(got)) == 3, got


def test_course_at_puts_a_plinth_under_a_single_storey_cottage():
    assert W.course_at(0, 1) == "base"
    assert W.course_at(0, 3) == "base"
    assert W.course_at(1, 3) == "mid"
    assert W.course_at(2, 3) == "top"


# -- glazing -------------------------------------------------------------------

@pytest.mark.parametrize("length,rate", [(6, 3), (8, 4), (12, 3), (10, 5)])
def test_glazing_is_dealt_per_panel_not_per_cell(length, rate):
    """The rate carries from cells to panels unchanged, and the area holds.

    A run of six at one-in-three is two 1-cell windows or one 2-cell window:
    the count halves and the glazed *area* is identical. That is arithmetic,
    and it is why `glaze_rate` did not have to be re-tuned when the panels got
    wider -- which was the open question this replaces.
    """
    per_cell = sum(1 for i in range(length) if i % rate == 0)
    panels = W.pack(length, 0, "centred")
    per_panel = sum(span for i, (_, span) in enumerate(panels) if i % rate == 0)
    assert per_panel == per_cell, (
        f"run of {length} at 1-in-{rate}: {per_panel} glazed cells by panel "
        f"against {per_cell} by cell")


# -- the built board -----------------------------------------------------------

def _one_building(bid, w=8, d=6):
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


def test_a_facade_is_built_from_wide_panels_where_they_fit():
    """The whole point: a run is covered by the kit's own 2-cell piece."""
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    b = build_from_tilemap(_one_building("house-0001"), palette, storeys=3)
    byid = {a.id: a for a in palette.catalog.assets}
    wide = [p for p in b.placements
            if max(byid[p.asset_id].size_x, byid[p.asset_id].size_z) > 1.5
            and 1.4 <= byid[p.asset_id].size_y <= 2.7
            and "wall" in byid[p.asset_id].name.lower()]
    assert wide, "no 2-cell wall panel was placed on an 8x6 building"


def test_a_civic_building_changes_course_above_the_ground():
    """The plinth stops at the first floor instead of climbing the building."""
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette

    palette = Palette(load_or_build(), MEDIEVAL)
    b = build_from_tilemap(_one_building("temple-0001"), palette, storeys=3)
    byid = {a.id: a for a in palette.catalog.assets}
    base_y, plain_y = set(), set()
    for p in b.placements:
        n = byid[p.asset_id].name
        # Plain wall panels only. Castle ships no 1-cell window at the base
        # course, so that slot falls back to the plain `castle wall 1x1
        # window` on the ground -- correct, and not a course this test is
        # making a claim about.
        if not n.startswith("castle wall") or "corner" in n or "window" in n:
            continue
        (base_y if "base" in n else plain_y).add(round(p.y, 3))
    assert base_y and plain_y, "civic facade did not use both courses"
    assert max(base_y) < min(plain_y), (
        f"base course reaches {max(base_y)} while plain starts at {min(plain_y)}")


# -- plain siblings ------------------------------------------------------------

def test_a_sibling_differs_only_by_a_trailing_index():
    """The rule that tells an interchangeable panel from another material."""
    assert W.stem_of("bg_wall_1x1_01") == W.stem_of("bg_wall_1x1_02")
    assert W.stem_of("Desert wall 02") == W.stem_of("Desert wall 03")
    assert W.stem_of("Lava wall 1x1 hot v1") == W.stem_of("Lava wall 1x1 hot v2")
    # ...and four materials in one folder, which must NOT collapse together.
    shogun = ["shogunWall1x2", "shogunPaperWall1x2", "shugunRockWall_1x2",
              "shogun_digWall_2x1"]
    assert len({W.stem_of(n) for n in shogun}) == 4


def test_shogun_materials_are_never_dealt_as_siblings(families):
    """Its plaster, paper, rock and dug earth all tie for the same rank.

    Rank alone would deal a wall built from four different things. The stem
    keeps them apart, and the cost is that Shogun has no free variance --
    which is correct, because it has none.
    """
    fam = families["Shogun Palace"]
    for span in (1, 2):
        assert len(fam.all("wall", span)) > 1, "the tie is the point of this test"
        assert len(fam.siblings("wall", span)) == 1


def test_a_long_run_deals_its_kits_plain_siblings(families):
    """A kit with interchangeable panels uses more than one along a run."""
    fam = families["Desert Village"]
    assert len(fam.siblings("wall", 2)) == 2
    got = {fam.deal("wall", 2, "mid", k).name for k in range(8)}
    assert len(got) == 2, got


def test_per_panel_dealing_is_inert_for_the_kits_a_town_uses_today(families):
    """Stated rather than assumed: this changes no medieval board yet.

    Tavern, Rural and Castle Fortified each ship exactly one plain panel per
    slot, so dealing per panel returns what dealing per building returned. The
    mechanism is for the eighteen kits a town does not use yet -- and for the
    day one of them becomes a tier.
    """
    for kit in ("Tavern", "Rural", "Castle Fortified"):
        fam = families[kit]
        for span in (1, 2):
            for course in fam.courses:
                sibs = fam.siblings("wall", span, course)
                assert len(sibs) <= 1, f"{kit} {span}c {course}: {sibs}"


def test_dealing_is_stable_across_processes(families):
    """A rebuild must be byte-identical, so the key cannot be `hash()`.

    Python salts str hashing per process; `tests/test_determinism.py` exists
    because that bit this project once already. `deal` takes an int key and
    the caller supplies a crc32, so there is nothing here to salt -- this
    pins the indexing so a refactor cannot quietly reintroduce it.
    """
    fam = families["Desert Village"]
    first = [fam.deal("wall", 2, "mid", k).name for k in range(6)]
    again = [fam.deal("wall", 2, "mid", k).name for k in range(6)]
    assert first == again
    assert first == [fam.siblings("wall", 2, "mid")[k % 2].name for k in range(6)]


# -- the roster and the fabric deal --------------------------------------------

def test_every_wall_kit_has_a_declared_role(families):
    """A kit with no declared job gets rediscovered from scratch every time.

    22 kits can clad a building and three had one. `KIT_ROLE` is the record,
    and it is load-bearing rather than a list: `fabric_for` resolves through
    it, so an entry is the only route a kit has to a building.
    """
    assert not W.unmapped(families), (
        "medieval kits with no declared role: " + ", ".join(W.unmapped(families)))
    for kit, role in W.KIT_ROLE.items():
        assert role, f"{kit} has an empty role"


def test_an_unmapped_kit_is_reported_and_not_raised(families):
    """A DLC must not break the build to satisfy our bookkeeping.

    Same rule `Layout.unmapped` follows for an unknown FTG value: surface it,
    never drop it, never die on it. Scoping to the packs a style claims is the
    other half -- a sci-fi kit nobody has mapped is not a medieval problem.
    """
    assert W.unmapped(families, packs=("No Such Pack",)) == []
    fake = dict(families)
    fake["Invented Kit"] = W.WallFamily("Invented Kit", "Medieval Fantasy")
    assert "Invented Kit" in W.unmapped(fake)


def test_a_tier_deals_more_than_one_fabric_across_a_town(families):
    """The whole point: two houses on a street are not the same building.

    Measured on Forest Church before this: 46 of 51 buildings were one
    material, because a tier resolved exactly one kit.
    """
    import zlib

    got = {W.fabric_for("common", zlib.crc32(f"house-{i:04d}".encode()),
                        families).kit for i in range(51)}
    assert len(got) > 1, got
    assert "Tavern" in got, "the common house should still mostly be Tavern"


def test_the_fabric_deal_is_stable_and_weighted(families):
    """Same key, same fabric -- and the poor kit stays the minority.

    A fifty-fifty deal reads as a town that burned down rather than as a town
    with a poor edge, so the weighting is the design and not a detail.
    """
    import collections
    import zlib

    keys = [zlib.crc32(f"house-{i:04d}".encode()) for i in range(200)]
    first = [W.fabric_for("common", k, families).kit for k in keys]
    assert first == [W.fabric_for("common", k, families).kit for k in keys]

    share = collections.Counter(first)
    assert share["Tavern"] > share["Abandoned Village"] * 3, share


def test_a_tier_that_declares_nothing_falls_back_to_the_palette(families):
    """`fabric_for` returns None rather than inventing a kit.

    The caller keeps whatever the palette resolved, which is what makes this
    safe to wire into a style that has no roster entries at all.
    """
    assert W.fabric_for("no-such-tier", 0, families) is None


def test_the_family_cache_cannot_serve_a_dead_catalogs_families():
    """Keyed on `id(catalog)`, a cache is wrong rather than merely stale.

    CPython reuses an address once the object at it is collected. The first
    cut of `build.wall_families` kept `id(catalog) -> families` and held no
    reference to the catalog, so a catalog built, used and dropped could hand
    its id to the next one and the second would silently be served the first
    one's families -- correct in isolation, wrong only when something else ran
    first and freed an address, which is exactly the shape of the
    order-dependent uiserver failure seen twice on 2026-08-28.
    """
    import gc
    import sys

    sys.path.insert(0, ".")
    from citysmith import build as B
    from citysmith.catalog import load_or_build

    before = len(B._WALL_FAMILIES)
    cat = load_or_build()
    fams = B.wall_families(cat)
    assert "Tavern" in fams
    assert len(B._WALL_FAMILIES) == before + 1

    del cat, fams
    gc.collect()
    assert len(B._WALL_FAMILIES) == before, (
        "the entry outlived the catalog it was filed under, so its address is "
        "free for another catalog to be allocated at")


def test_a_town_builds_its_common_houses_from_more_than_one_fabric():
    """Two houses on a street should not be the same building.

    Measured on Forest Church before this: 46 of 51 buildings were `Tavern
    Wall 01` + `Tavern Wall - Small 01`, one material, because a tier resolved
    exactly one kit. A/B with `TIER_FABRICS` emptied: Abandoned Village walls
    0 -> 72, and 6 of 51 buildings changed fabric.
    """
    import sys

    sys.path.insert(0, ".")
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    tm = TileMap.blank(70, 20)
    for i in range(12):
        bid = f"house-{i:04d}"
        bx = 3 + i * 5
        for x in range(bx, bx + 4):
            for z in range(6, 12):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        tm.floors[bid] = 2
    _find_perimeters(tm, None)
    _place_doors(tm, None)

    palette = Palette(load_or_build(), MEDIEVAL)
    b = build_from_tilemap(tm, palette, storeys=2)

    fams = W.families(palette.catalog)
    kit_of = {a.id: k for k, f in fams.items()
              for v in f.pieces.values() for a in v}
    placed = {kit_of[p.asset_id] for p in b.placements if p.asset_id in kit_of}
    assert len(placed) > 1, (
        f"twelve houses were all built from {placed} -- a tier is still "
        "resolving exactly one kit")
    assert "Tavern" in placed, "the common house should still mostly be Tavern"


def test_a_barn_gets_no_glass_however_glazed_its_fabric_is():
    """`glazes` is a flag now, and this is why it had to be.

    Utility used to be kept blind by setting `glass = None`. A fabric can
    supply a window of its own -- Rural has `Rural Wall Window` -- so once the
    tier deals fabrics that would have put glass in every barn.
    """
    import sys

    sys.path.insert(0, ".")
    from citysmith.build import build_from_tilemap
    from citysmith.catalog import load_or_build
    from citysmith.palette import MEDIEVAL, Palette
    from citysmith.raster import FLOOR, TileMap, _find_perimeters, _place_doors

    tm = TileMap.blank(40, 20)
    for i in range(6):
        bid = f"stable-{i:04d}"
        bx = 3 + i * 6
        for x in range(bx, bx + 5):
            for z in range(6, 12):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        tm.floors[bid] = 1
    _find_perimeters(tm, None)
    _place_doors(tm, None)

    palette = Palette(load_or_build(), MEDIEVAL)
    b = build_from_tilemap(tm, palette, storeys=1)
    byid = {a.id: a for a in palette.catalog.assets}
    glazed = [byid[p.asset_id].name for p in b.placements
              if "window" in byid[p.asset_id].name.lower()]
    assert not glazed, f"a barn was glazed: {sorted(set(glazed))}"


# -- the Village fabric --------------------------------------------------------

def test_one_folder_can_hold_two_fabrics(families):
    """`Tavern` splits, and the split is the point.

    `Village Roof Side Wall 01/02` and `Tavern Wall 01` are one folder and must
    not mix panel by panel -- a Village panel between two Tavern ones carries
    no timber frame of its own and reads as a bare plaster patch. But as a
    WHOLE BUILDING's fabric the Village panels are coherent; that is what every
    common house looked like before the wide-panel work.
    """
    assert "Tavern" in families and "Tavern/Village" in families
    tav, vil = families["Tavern"], families["Tavern/Village"]

    assert [a.name for a in vil.all("wall", 1)] == [
        "Village Roof Side Wall 01", "Village Roof Side Wall 02"]
    assert not vil.all("wall", 2), "Village ships no 2-cell piece"
    assert tav.wide is not None and tav.wide.name == "Tavern Wall 01"

    # No piece belongs to both, or the split has not actually split anything.
    a = {x.id for v in tav.pieces.values() for x in v}
    b = {x.id for v in vil.pieces.values() for x in v}
    assert not (a & b)


def test_the_village_fabric_mitres_its_corners(families):
    """It has no corner piece, and that is correct rather than missing.

    `Tavern no floor (1x1 a)` is `group='corner'` so it stays with the parent.
    CLAUDE.md's corner probe settled that the Village panel mitres cleanly --
    its own edge timber meets its neighbour as a corner post -- which is why
    `_usable_corner` drops a mismatched corner rather than swapping one in.
    """
    assert not families["Tavern/Village"].all("corner", 1)


def test_a_street_gets_both_house_fabrics_with_tavern_in_front(families):
    """Two fabrics, and the secondary must not become the primary by arriving.

    Weighted by ROLE alone both kits took the role's whole weight and the deal
    came out 29 Village against 19 Tavern. Named as kits it is 6:3:1.
    """
    import collections
    import zlib

    got = collections.Counter()
    for i in range(200):
        f = W.fabric_for("common", zlib.crc32(f"house-{i:04d}".encode()),
                         families)
        got[f.kit] += 1
    assert set(got) == {"Tavern", "Tavern/Village", "Abandoned Village"}
    assert got["Tavern"] > got["Tavern/Village"] > got["Abandoned Village"]


def test_a_named_kit_beats_a_role_of_the_same_name(families):
    """`fabric_for` resolves a kit name first, then falls back to a role."""
    assert W.fabric_for("common", 0, families) is not None
    assert W.kits_for_role("common", families) == ["Tavern", "Tavern/Village"]
