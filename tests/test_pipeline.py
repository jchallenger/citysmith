"""`cli build` is a shell over `pipeline.build_town`, and this pins that.

The pipeline was lifted out of `cmd_build` so a UI could call it. The whole
value of that move depends on it being a *move*: if the report changes shape,
every driver reading it -- `review.ps1`, `scene.ps1`, a person -- is reading
something else. So one small build is run through the real command and matched
against the text below, character for character.

**The palette here is a stub, and deliberately so.** A golden built with the
real catalog would pin whichever TaleSpire packs one machine owns: the asset
counts and the `surfaces:` line come straight out of them, so a colleague's
checkout would fail this on hardware grounds. That is the same reason
`docs/asset-index.md` is regenerated rather than committed. What a stub cannot
pin -- that the numbers a real catalog produces are unchanged -- was verified
by hand instead, by running the pre-refactor CLI and this one over the same
layout and diffing: seven invocations, byte-identical stdout and byte-identical
output files.

The stub answers every role, because `build_from_tilemap` asks for far more of
them than an interior does and an unanswered one is a KeyError rather than a
finding. The shapes are the real ones for the roles that matter -- a 0.5-thick
floor, a 2.0-tall wall 0.5 deep -- for the reason `conftest` gives: every
placement rule is derived from `ColliderBoundsBound`.
"""

from __future__ import annotations

import pathlib
import random

from citysmith import cli
from citysmith.catalog import Asset
from citysmith.layout import Layout, LayoutBuilding, LayoutRoad


def _asset(n: int, name: str, kind: str, sx: float, sy: float, sz: float,
           group: str = "", folder: str = "Tavern") -> Asset:
    # The id has to be real hex -- the slab codec parses it as a UUID.
    return Asset(
        id=f"{n:08x}-1111-2222-3333-444444444444", name=name, kind=kind,
        pack="Medieval Fantasy", group_tag=group, tags=(), folder=folder,
        size_x=sx, size_y=sy, size_z=sz,
    )


FLOOR = _asset(1, "Tavern Floor 01", "tile", 1.0, 0.5, 1.0, "floor")
WALL = _asset(2, "Village Roof Side Wall 01", "tile", 1.0, 2.0, 0.5, "wall")
DOOR = _asset(3, "Door -Peasant", "tile", 1.0, 2.0, 0.5, "door", "Doors")
GROUND = _asset(4, "Grass 1x1", "tile", 1.0, 0.5, 1.0, "grassland", "Nature")
STREET = _asset(5, "CobbleStone Floor Small", "tile", 1.0, 0.25, 1.0, "floor",
                "CobbleStones")
BLOCK = _asset(6, "md_stairblock_01", "tile", 1.0, 1.0, 1.0, "wall",
               "MegaDungeon")
STOOL = _asset(7, "stool wood 01", "prop", 0.5, 0.3, 0.3, "chair", "Furniture")

_ROLES = {
    "floor": FLOOR, "floor_upper": FLOOR, "wall": WALL, "wall_interior": WALL,
    "door": DOOR, "ground": GROUND, "street": STREET,
}


class _Catalog:
    assets = [FLOOR, WALL, DOOR, GROUND, STREET, BLOCK, STOOL]

    def find(self, *a, **k):
        # The build looks a few props up by name directly. Nothing here has
        # those names, and an empty result is the supported answer -- the
        # dressing passes skip what they cannot find.
        return []

    def by_id(self, asset_id: str):
        # `verify.check_placements` reads an off-grid placement back to see
        # whether its asset is one that is allowed off the lattice.
        return next((a for a in self.assets if a.id == asset_id), None)


class _Palette:
    """Answers every role, so the golden pins formatting and not the catalog."""

    def __init__(self) -> None:
        self.catalog = _Catalog()

    def resolve(self, role: str, variant: int = 0):
        # A full-cell block for anything unnamed: `place_tile` needs an asset
        # that fills the cell, and a curtain piece in a mass is the defect
        # CLAUDE.md opens its asset-geometry section with.
        return _ROLES.get(role, BLOCK)

    def require(self, role: str, variant: int = 0):
        return self.resolve(role, variant)

    def prop(self, category: str, rng: random.Random):
        return STOOL


def _layout(path: pathlib.Path) -> pathlib.Path:
    """A four-house hamlet on a 30x30 board, written where the CLI reads it."""
    layout = Layout(name="Pinfold")
    layout.width, layout.depth = 30.0, 30.0
    for i in range(4):
        x = 4 + (i % 2) * 12
        z = 5 + (i // 2) * 12
        layout.buildings.append(LayoutBuilding(
            id=f"house-{i + 1:04d}",
            ring=[(x, z), (x + 6, z), (x + 6, z + 6), (x, z + 6)],
            kind="house", floors=1,
        ))
    layout.roads = [LayoutRoad(points=[(0.0, 14.0), (30.0, 14.0)], width=3.0)]
    out = path / "layout.json"
    layout.save(out)
    return out


def _run(tmp_path, monkeypatch, capsys, *flags) -> str:
    """Drive the real command, stub palette in place, and return its stdout."""
    palette = _Palette()
    monkeypatch.setattr(cli, "_catalog", lambda args: palette.catalog)
    monkeypatch.setattr(cli, "_palette", lambda args, c, s, seed: palette)

    layout = _layout(tmp_path)
    out_dir = tmp_path / "out"
    code = cli.main(["--out-dir", str(out_dir), "build", str(layout),
                     "--stem", "pin", *flags])
    assert code in (0, 2), f"the command failed outright: {code}"
    return capsys.readouterr().out


def _expected(template: str, out_dir: pathlib.Path) -> str:
    """The golden with this run's paths in it.

    The paths are the only thing left as a placeholder: they are the machine's
    and the separator is the platform's, so pinning them would pin neither the
    build nor the report.
    """
    return template.format(
        npcs=out_dir / "pin-npcs.json",
        svg=out_dir / "city-raster.svg",
        out=out_dir,
    )


#: What ``citysmith build`` prints for the hamlet above, to the character.
#:
#: THREE THINGS BEFORE ANYONE "FIXES" THE FAILURES IN HERE. They are the stub
#: talking, not the map: every role the stub does not name resolves to one
#: full-cell block, so the scatter plants blocks at fractional coordinates
#: (off the grid) and the edge fringe lays them under ground that is not there
#: (floating). The third is the same collapse read from the other end --
#: `verify._boundary_ids` gathers boundary pieces by asset id, and with every
#: unnamed role answering with the *same* block that set is most of the board,
#: so a plaza tile is counted as a wall standing on a plaza. A real palette
#: answers those roles with props and soil and reports none of the three.
#: **The two yard roles widen it.** `yard_smithy` and `yard_trade` are named
#: pins the stub does not carry, so they answer with that same block and the
#: off-grid and seam counts here rose with them -- 60 to 113 and 192 to 265.
#: Measured on the REAL palette over the same town, tile seams are 2355 either
#: way and neither the off-grid nor the floating finding appears at all. The
#: movement is the stub, as everything above it in this note is.
#:
#: They are kept because a golden that only ever shows ``[ok  ]`` never proves
#: the FAIL branch renders, and because they are stable: identical under
#: PYTHONHASHSEED 1, 7 and 12345.
#:
#: **The boundary counts moved when the checks learned to read a stored
#: coordinate.** A placement holds the asset's origin, and these checks used to
#: read it as the collider centre -- right for a prop, half a footprint out for
#: a tile, and the stub's block is a tile. So the boxes were measured half a
#: cell low on both axes: a different 72 pieces were named, and two pieces
#: whose min corner is inside the map read as hanging off it. Both counts here
#: are now the piece where the piece is.
#:
#: The grid row below ends in a space. Do not strip trailing whitespace here.
DEFAULT_REPORT = """Pinfold: 30x30 tiles (150x150 ft)
  surfaces: 638 ground, 128 floor, 96 street, 38 plaza
  0 wall cells, 0 gate cells, 4 buildings with doors
  npcs: 0 guard(s), 1 working, 3 off duty
  chunk budget: 9,000 assets (from board size)
  wrote {npcs}  (4 post(s))

936 assets in 2 chunk(s)

[FAIL] placements: 113 tile placements are off the half-tile grid (first at x=3.34, z=9.32) -- minis with grid snap will not line up with the floors
[FAIL] placements: 51 boundary piece(s) stand in a street or lane (first at x=2.50, z=10.50) -- a wall across a way is an obstacle on the one thing the map is for
[FAIL] floating geometry: 445 placement(s) stand over nothing (md_stairblock_01 at (0.0, -1.0, 0.0), md_stairblock_01 at (2.0, -1.0, 0.0), md_stairblock_01 at (14.0, -1.0, 0.0)) -- left hanging where the edge fringe took the ground away, or beyond where ground was ever laid
[WARN] gates: no gates found; routing from the map edge instead
[WARN] surfaces: 2 distinct outdoor material(s): CobbleStone Floor Small, Grass 1x1
[WARN] storeys: 1:4, mean 1.00  -- every building the same height
[WARN] tile seams: 265 pairs of structural tiles occupy the same space -- buried geometry shows through as a seam that shifts with the camera
[ok  ] connectivity: one connected town covering 100% of walkable space
[ok  ] building access: 4 of 4 buildings (100.0%) can be entered from the street network
[ok  ] street width: 0 of 134 street tiles (0.0%) are under 2 tiles (10 ft) wide -- a creature fills one tile, so two cannot pass abreast there
[ok  ] vehicle width: main street 96/96 tiles hold 4 (20 ft), lane 38/38 tiles hold 2 (10 ft); 0 of 96 through-route tiles (0.0%) are under 3 tiles (15 ft), where a 10 ft cart cannot get past a pedestrian
[ok  ] board size: 30x30 tiles (150x150 ft) fits the 2000x2000 board
[ok  ] asset budget: 936 assets = 0.1% of the 1,000,000 per-board limit
[ok  ] slab export: 2 slab paste(s), largest 2,391 compressed bytes (cap 30,720)
[ok  ] cart clearance: 0 of 120 through-route tiles (0.0%) have an open cross-section under 3 tiles (15 ft) -- a building overlapping a widened street re-narrows it, and a 10 ft cart cannot pass there
[ok  ] gabled ends: none here; no quarters to key on, so every ridge is hipped -- a crop rarely clusters enough to have any
[ok  ] chimneys: none; nothing here is roofed
[ok  ] field walls: none in the source
[ok  ] yards: 4 of 4 buildings stand apart enough for a yard (332 cells; wrapped 4)
[ok  ] marsh: none in the source
[ok  ] quarters: none; trades cluster at 0.00x, under the threshold -- this settlement has no quarters to find

  wrote {svg}  (tile numbers here match the chunk table below)
  wrote 2 slab file(s) in {out}

Chunk grid: 2 row(s) x 2 col(s) of 24x24 tiles (120x120 ft)

      c00 c01
 r00   #   # 
 r01   #   # 
        # = written    . = open country, skipped

  [landscape]
          r00c00+3  x    0-25   z    0-27      698 assets (4 cells)  pin-landscape-r00c00+3.slab.txt
  [structure]
            r00c00  x    0-23   z    0-23      238 assets, 4 buildings  pin-structure-r00c00.slab.txt

Every chunk shares one origin (a registration marker at the map's corner),
so paste each file at the SAME anchor point. Do not move the camera between
pastes.

PASTE THE LANDSCAPE LAYER FIRST, then the structures. A paste comes to rest on
whatever is under the cursor, so the first slab onto bare board sets the height
everything else is measured from. Within a layer the order does not matter and
a subset is fine -- each chunk lands in its own region.

Re-pasting one layer over a board that already has the other is the fast way to
review a change: a new roof does not need 20,000 grass tiles laid again.
"""

#: The same hamlet cut by region for tiling, with the plugin document written
#: and NPC marks off. A different help footer, a chunk table with no layer
#: headings, and -- the reason this case is here -- the three registration
#: checks skipped, because a multi-slab build has no shared box to agree on.
TILED_REPORT = """Pinfold: 30x30 tiles (150x150 ft)
  surfaces: 638 ground, 128 floor, 96 street, 38 plaza
  0 wall cells, 0 gate cells, 4 buildings with doors

929 assets in 7 chunk(s)

[FAIL] placements: 113 tile placements are off the half-tile grid (first at x=3.34, z=9.32) -- minis with grid snap will not line up with the floors
[FAIL] placements: 48 boundary piece(s) stand in a street or lane (first at x=2.50, z=10.50) -- a wall across a way is an obstacle on the one thing the map is for
[FAIL] floating geometry: 442 placement(s) stand over nothing (md_stairblock_01 at (0.0, -1.0, 0.0), md_stairblock_01 at (2.0, -1.0, 0.0), md_stairblock_01 at (14.0, -1.0, 0.0)) -- left hanging where the edge fringe took the ground away, or beyond where ground was ever laid
[WARN] gates: no gates found; routing from the map edge instead
[WARN] surfaces: 2 distinct outdoor material(s): CobbleStone Floor Small, Grass 1x1
[WARN] storeys: 1:4, mean 1.00  -- every building the same height
[WARN] tile seams: 265 pairs of structural tiles occupy the same space -- buried geometry shows through as a seam that shifts with the camera
[ok  ] connectivity: one connected town covering 100% of walkable space
[ok  ] building access: 4 of 4 buildings (100.0%) can be entered from the street network
[ok  ] street width: 0 of 134 street tiles (0.0%) are under 2 tiles (10 ft) wide -- a creature fills one tile, so two cannot pass abreast there
[ok  ] vehicle width: main street 96/96 tiles hold 4 (20 ft), lane 38/38 tiles hold 2 (10 ft); 0 of 96 through-route tiles (0.0%) are under 3 tiles (15 ft), where a 10 ft cart cannot get past a pedestrian
[ok  ] board size: 30x30 tiles (150x150 ft) fits the 2000x2000 board
[ok  ] asset budget: 929 assets = 0.1% of the 1,000,000 per-board limit
[ok  ] slab export: 7 slab paste(s), largest 693 compressed bytes (cap 30,720)
[ok  ] cart clearance: 0 of 120 through-route tiles (0.0%) have an open cross-section under 3 tiles (15 ft) -- a building overlapping a widened street re-narrows it, and a 10 ft cart cannot pass there
[ok  ] gabled ends: none here; no quarters to key on, so every ridge is hipped -- a crop rarely clusters enough to have any
[ok  ] chimneys: none; nothing here is roofed
[ok  ] field walls: none in the source
[ok  ] yards: 4 of 4 buildings stand apart enough for a yard (332 cells; wrapped 4)
[ok  ] marsh: none in the source
[ok  ] quarters: none; trades cluster at 0.00x, under the threshold -- this settlement has no quarters to find

  wrote {svg}  (tile numbers here match the chunk table below)
  wrote 7 slab file(s) in {out}

Chunk grid: 2 row(s) x 2 col(s) of 24x24 tiles (120x120 ft)

      c00 c01
 r00   #   # 
 r01   #   # 
        # = written    . = open country, skipped

          r00c00ne  x   12-23   z    0-11      191 assets, 1 buildings  pin-r00c00ne.slab.txt
          r00c00nw  x    0-11   z    0-11      191 assets, 1 buildings  pin-r00c00nw.slab.txt
          r00c00se  x   12-23   z   12-23      153 assets, 1 buildings  pin-r00c00se.slab.txt
          r00c00sw  x    0-11   z   12-23      175 assets, 1 buildings  pin-r00c00sw.slab.txt
            r00c01  x   24-25   z    0-23       89 assets  pin-r00c01.slab.txt
            r01c00  x    0-23   z   24-27      115 assets  pin-r01c00.slab.txt
            r01c01  x   24-25   z   24-27       15 assets  pin-r01c01.slab.txt

TILE THE CHUNKS ONTO BLANK BOARD. Each file holds one region of the map with
its terrain, buildings and walls together, and the regions do not overlap --
so every chunk is pasted onto ground nothing has been laid on yet.

A paste comes to rest on whatever the cursor's ray hits. That is what put a
whole structure layer 1.5 tiles up and a tile sideways when it was pasted over
terrain: it inherited the height of the surface under the anchor. Regions
cannot do that to each other, because no two of them occupy the same ground.

PASTE EVERY FILE AT THE SAME CURSOR CELL, IN THE ORDER LISTED, WITHOUT MOVING
THE CAMERA. Every chunk carries the map's two registration markers, so they
all present the identical bounding box and all anchor on the same point --
which means they need no measuring and no lining up by eye. It also means an
error in that one anchor is shared by every chunk, so the map can land a
little off where you aimed it and still be perfectly assembled with itself.

  1. Pitch the camera straight down first, and leave it there. The anchor is
     the cursor's ray hit, and with the camera vertical nothing under the
     cursor can slide it sideways.
  2. Pick a cell with room for the whole map around it, and paste every file
     there. Look at the preview before each commit: a held slab that
     intersects placed geometry renders as a pale translucent mesh.
  3. Do not reorder the list. The anchor sits at the centre of the map, and
     the chunk whose region covers it is listed last so that the anchor is
     still bare board for all the pastes before it.

A subset is fine -- each file is a complete piece of town, ground included.
"""


def test_the_build_command_prints_exactly_this(tmp_path, monkeypatch, capsys):
    """The default build, pinned line for line.

    It is long on purpose. For anyone who cannot open TaleSpire the report *is*
    the product, and the failure this guards against -- a refactor quietly
    dropping a line -- reads as a clean run.
    """
    out = _run(tmp_path, monkeypatch, capsys)
    assert out == _expected(DEFAULT_REPORT, tmp_path / "out")


def test_tiling_and_the_plugin_document_print_exactly_this(
        tmp_path, monkeypatch, capsys):
    """The other three branches of the command's output, in one invocation."""
    out = _run(tmp_path, monkeypatch, capsys, "--by-region", "--multi-slab",
               "--no-npcs", "--max-assets", "400")
    assert out == _expected(TILED_REPORT, tmp_path / "out")


def test_the_result_carries_the_whole_report_without_rerunning(tmp_path):
    """The point of the split: a UI must not have to scrape stdout.

    Everything the command prints after the build has to be reachable from the
    result -- as objects, not as sentences. A finding that arrived as a string
    would render fine here and be useless to anything that wanted to colour a
    failure red or count them.
    """
    from citysmith import pipeline
    from citysmith.verify import Finding

    result = pipeline.build_town(
        _layout(tmp_path), palette=_Palette(), out_dir=tmp_path / "out",
        stem="pin")

    assert result.report.findings, "the report should never come back empty"
    assert all(isinstance(f, Finding) for f in result.report.findings)
    assert result.failed is result.report.failed

    # The chunk table is rendered off the result by the CLI, so the result has
    # to satisfy it without a plan in sight.
    table = cli._chunk_table(result, "pin")
    for chunk in result.chunks:
        assert chunk.path.exists()
        assert chunk.path.name in table
        assert chunk.size_bytes == len(chunk.path.read_text(encoding="utf-8")) * 3 // 4
    assert result.assets_emitted == sum(c.assets for c in result.chunks)
    assert result.largest_slab_bytes == max(c.size_bytes for c in result.chunks)

    # The paste order is the one thing a driver must not re-derive by globbing.
    assert result.paste_order == tuple(
        result.paste_order_path.read_text(encoding="utf-8").split())
    assert result.raster_svg.exists()
    assert result.npc_manifest.exists()
    assert result.chunk_budget > 0 and result.budget_from_board_size


def test_progress_only_reports_stages_it_documents(tmp_path):
    """`STAGES` is the contract a UI switches on, so it has to be the truth.

    A stage that fires undocumented is one a server has no case for; a field
    renamed under a caller is the same failure one level down.
    """
    from citysmith import pipeline

    seen = []
    pipeline.build_town(
        _layout(tmp_path), palette=_Palette(), out_dir=tmp_path / "out",
        stem="pin", progress=lambda stage, **f: seen.append((stage, set(f))))

    assert [s for s, _ in seen] == ["rasterized", "npcs", "budget",
                                    "npc_manifest"]
    for stage, fields in seen:
        assert fields == set(pipeline.STAGES[stage])


def test_a_budget_the_caller_chose_is_not_reported_as_news(tmp_path):
    """The budget line says "from board size", so it must only fire then."""
    from citysmith import pipeline

    seen = []
    result = pipeline.build_town(
        _layout(tmp_path), palette=_Palette(), out_dir=tmp_path / "out",
        stem="pin", max_assets=400,
        progress=lambda stage, **f: seen.append(stage))

    assert "budget" not in seen
    assert result.chunk_budget == 400 and not result.budget_from_board_size
