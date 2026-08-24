"""citysmith command line.

The pipeline, in order::

    citysmith catalog build          # index your installed TaleSpire assets
    citysmith city --seed 42         # generate a city -> city.json + city.svg
    citysmith sites city.json        # rank places worth playing in
    citysmith plan city.json --site tavern-038   # floorplan -> plan.json + svg
    citysmith design plan.json       # -> slab.txt, paste into TaleSpire
    citysmith board city.json        # coarse 3D city, split into pasteable slabs

Every step writes a file the next step reads, so any stage can be inspected,
hand-edited, or regenerated on its own.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from . import floorplan as floorplan_mod
from . import render, sites
from .build import DEFAULT_CHUNK_TILES, build_city_board, build_interior
from .catalog import Catalog, CatalogError, load_or_build
from .city import CityParams, City, SIZES
from .city import generate as generate_city
from .palette import STYLES, Palette
from .slab import SlabError, encode

DEFAULT_OUT = pathlib.Path("out")


def _catalog(args) -> Catalog:
    return load_or_build(args.catalog, getattr(args, "talespire_path", None))


def _palette(args, catalog: Catalog, style: str, seed: int) -> Palette:
    palette = Palette.named(catalog, style, seed)
    problems = palette.validate()
    if problems:
        raise SystemExit(
            f"Style {style!r} cannot be used with your installed packs:\n  "
            + "\n  ".join(problems)
        )
    return palette


def _write_chunks(chunks, out_dir: pathlib.Path, stem: str) -> list[pathlib.Path]:
    """Write one file per chunk, named for the region the chunk covers.

    ``forest-r02c03.slab.txt`` says which piece of the map is in the file;
    the old ``forest-07.slab.txt`` only said which piece came seventh.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Chunk names encode grid position, and packing boundaries move between
    # builds -- so a rebuild can leave last run's files sitting beside this
    # run's. They paste without complaint and silently mix two revisions of
    # the map, so clear the stem's previous output first.
    for stale in out_dir.glob(f"{stem}-r*.slab.txt"):
        stale.unlink()
    for stale in out_dir.glob(f"{stem}-landscape-*.slab.txt"):
        stale.unlink()
    for stale in out_dir.glob(f"{stem}-structure-*.slab.txt"):
        stale.unlink()
    for stale in out_dir.glob(f"{stem}.slab.txt"):
        stale.unlink()
    written: list[pathlib.Path] = []
    for chunk in chunks:
        name = (f"{stem}.slab.txt" if len(chunks) == 1
                else f"{stem}-{chunk.label}.slab.txt")
        path = out_dir / name
        path.write_text(encode(chunk.slab), encoding="utf-8")
        written.append(path)

    # The paste order is not the filename order, and getting it wrong is the
    # difference between a flat map and a stepped one: the chunk covering the
    # anchor cell is written *last* so that the anchor is still bare board for
    # every paste before it. A glob sorts that chunk into the middle. So the
    # order is written down beside the slabs, for anything driving the paste.
    (out_dir / f"{stem}-paste-order.txt").write_text(
        "\n".join(p.name for p in written) + "\n", encoding="utf-8"
    )
    return written


def _chunk_table(plan, stem: str) -> str:
    """A map and a table of which chunk covers which tiles.

    Printed so the reader can paste a quarter of a town instead of all of it:
    match a region on ``city-raster.svg`` to a row/column here, then paste only
    those files.
    """
    from .layout import TILE_FEET

    if not plan.chunks and not plan.skipped:
        return ""
    ft = int(plan.tile_size * TILE_FEET)
    lines = [
        f"Chunk grid: {plan.rows} row(s) x {plan.cols} col(s) of "
        f"{plan.tile_size}x{plan.tile_size} tiles ({ft}x{ft} ft)",
        "",
    ]

    # A packed chunk covers many cells; marking only its start cell would
    # show most of a written map as skipped.
    emitted_cells = {
        cell for c in plan.chunks
        for cell in (c.covers or ((c.row, c.col),))
    }
    lines.append("      " + " ".join(f"c{c:02d}" for c in range(plan.cols)))
    for r in range(plan.rows):
        marks = [
            " # " if (r, c) in emitted_cells else " . "
            for c in range(plan.cols)
        ]
        lines.append(f" r{r:02d}  " + " ".join(marks))
    lines.append("        # = written    . = open country, skipped")
    lines.append("")

    single = len(plan.chunks) == 1
    last_layer = None
    for chunk in plan.chunks:
        # Grouped by layer, and in paste order: the landscape goes down first,
        # onto bare board, and everything else stands on it.
        if chunk.layer != last_layer:
            last_layer = chunk.layer
            if chunk.layer:
                lines.append(f"  [{chunk.layer}]")
        name = (f"{stem}.slab.txt" if single
                else f"{stem}-{chunk.label}.slab.txt")
        span = f" ({len(chunk.covers)} cells)" if len(chunk.covers) > 1 else ""
        # A building's whole shell is in exactly one structure file, so the
        # count says what a paste that skips this file will leave as bare
        # floors -- which is how a missing fifth paste was first noticed.
        held = f", {chunk.buildings} buildings" if chunk.buildings else ""
        lines.append(
            f"  {(chunk.name or chunk.region):>16}  x {chunk.x0:>4}-{chunk.x1 - 1:<4} "
            f"z {chunk.z0:>4}-{chunk.z1 - 1:<4} {chunk.count:>6} assets"
            f"{span}{held}  {name}"
        )
    for chunk in plan.skipped:
        lines.append(
            f"  {chunk.label:>10}  x {chunk.x0:>4}-{chunk.x1 - 1:<4} "
            f"z {chunk.z0:>4}-{chunk.z1 - 1:<4} {chunk.count:>6} assets  "
            f"(open country -- not written)"
        )
    return "\n".join(lines)


#: How to paste a chunked map. Every chunk carries a registration marker at the
#: whole map's corner, so they share one bounding box and one anchor point.
#: How to paste a map cut by region, with every layer in each chunk. Nothing
#: is ever pasted over anything, which is the whole point.
TILE_HELP = """TILE THE CHUNKS ONTO BLANK BOARD. Each file holds one region of the map with
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

A subset is fine -- each file is a complete piece of town, ground included."""

PASTE_HELP = """Every chunk shares one origin (a registration marker at the map's corner),
so paste each file at the SAME anchor point. Do not move the camera between
pastes.

PASTE THE LANDSCAPE LAYER FIRST, then the structures. A paste comes to rest on
whatever is under the cursor, so the first slab onto bare board sets the height
everything else is measured from. Within a layer the order does not matter and
a subset is fine -- each chunk lands in its own region.

Re-pasting one layer over a board that already has the other is the fast way to
review a change: a new roof does not need 20,000 grass tiles laid again."""


# -- commands -----------------------------------------------------------------

def cmd_catalog(args) -> int:
    if args.action == "build":
        catalog = Catalog.from_install(args.talespire_path)
        catalog.save(args.catalog)
        counts = catalog.counts()
        print(f"Wrote {args.catalog}")
        print(f"  packs: {', '.join(catalog.packs)}")
        print(f"  assets: {len(catalog)} ({', '.join(f'{v} {k}s' for k, v in sorted(counts.items()))})")
        return 0

    catalog = _catalog(args)
    results = catalog.find(
        *args.terms,
        kind=args.kind,
        group=args.group,
        tags=args.tag or (),
    )
    print(f"{len(results)} match(es)")
    for a in results[: args.limit]:
        print(
            f"  {a.id}  {a.name[:38]:38} {a.kind:8} "
            f"{a.size_x}x{a.size_y}x{a.size_z}  group={a.group_tag!r} [{a.pack}]"
        )
    if len(results) > args.limit:
        print(f"  ... {len(results) - args.limit} more (use --limit)")
    return 0


def cmd_city(args) -> int:
    params = CityParams(
        size=args.size, walled=not args.no_walls, style=args.style,
        min_block=args.min_block, min_plot=args.min_plot,
        max_floors=args.max_floors, name=args.name,
    )
    city = generate_city(params, seed=args.seed)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    city_path = out_dir / "city.json"
    city.save(city_path)
    print(city.summary())
    print(f"  wrote {city_path}")

    if not args.no_svg:
        top = sites.rank(city, top=args.highlight) if args.highlight else None
        svg_path = render.write(render.city_svg(city, scale=args.scale, highlight=top), out_dir / "city.svg")
        print(f"  wrote {svg_path}")
    return 0


def cmd_sites(args) -> int:
    city = City.load(args.city)
    ranked = sites.rank(
        city, top=args.top, kind=args.kind,
        district=args.district, min_floors=args.min_floors,
    )
    if not ranked:
        print("No buildings matched those filters.")
        return 1
    print(f"Top {len(ranked)} site(s) in {city.name}:\n")
    for i, site in enumerate(ranked, 1):
        print(f"{i:2}. {site.describe()}")
        print(f"     id: {site.id}\n")
    return 0


def cmd_plan(args) -> int:
    city = City.load(args.city)
    if args.site:
        building = city.building(args.site)
        if building is None:
            raise SystemExit(f"No building with id {args.site!r}. Run `citysmith sites` to list them.")
    else:
        building = sites.best(city, kind=args.kind).building
        print(f"Auto-selected best site: {building.id} ({building.name})")

    fp = floorplan_mod.generate(building, seed=args.seed or city.seed, levels=args.levels)
    out_dir = pathlib.Path(args.out_dir)
    plan_path = out_dir / f"{building.id}.plan.json"
    fp.save(plan_path)
    print(fp.summary())
    print(f"  wrote {plan_path}")

    if not args.no_svg:
        svg_path = render.write(render.floorplan_svg(fp), out_dir / f"{building.id}.plan.svg")
        print(f"  wrote {svg_path}")
    return 0


def cmd_design(args) -> int:
    fp = floorplan_mod.Floorplan.load(args.plan)
    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style, args.seed)

    builder = build_interior(
        fp, palette, seed=args.seed, roof=args.roof, prop_density=args.prop_density
    )
    plan = builder.chunk_plan(max_assets=args.max_assets)
    try:
        written = _write_chunks(plan.chunks, pathlib.Path(args.out_dir), f"{fp.building_id}")
    except SlabError as exc:
        raise SystemExit(f"Could not encode slab: {exc}") from exc

    print(f"{fp.name}: {builder.stats.tiles} tiles + {builder.stats.props} props")
    for p in written:
        print(f"  wrote {p}  ({len(p.read_text(encoding='utf-8'))} chars)")
    print("\nPaste a slab file's contents into TaleSpire with Ctrl+V while in build mode.")
    return 0


def cmd_board(args) -> int:
    city = City.load(args.city)
    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style or city.style, args.seed or city.seed)

    builder = build_city_board(
        city, palette,
        include_ground=not args.no_ground,
        include_streets=not args.no_streets,
        building_height=args.building_height,
        seed=args.seed or city.seed,
    )
    plan = builder.chunk_plan(
        max_assets=args.max_assets, chunk_tiles=args.chunk_tiles,
        skip_open_country=not args.keep_open_country,
    )
    try:
        written = _write_chunks(plan.chunks, pathlib.Path(args.out_dir), "city-board")
    except SlabError as exc:
        raise SystemExit(
            f"Could not encode slab: {exc}\nTry a smaller --max-assets."
        ) from exc

    print(f"{city.name} board: {plan.assets_emitted} assets in {len(written)} chunk(s)")
    if plan.skipped:
        print(f"  skipped {len(plan.skipped)} open-country chunk(s), "
              f"{plan.assets_skipped} assets")
    print("\n" + _chunk_table(plan, "city-board"))
    print("\n" + PASTE_HELP)
    return 0


def cmd_calibrate(args) -> int:
    """Emit a slab that makes the rotation-pivot convention visible in-game."""
    from .build import place_wall
    from .slab import Slab

    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style, 0)
    floor = palette.require("floor")
    wall = palette.require("wall")

    slab = Slab()
    from .build import place_tile

    # Four walls boxing a single tile look identical whether the pivot is the
    # asset centre or its origin -- a closed box either way. So each wall gets
    # its own tile, spaced two apart, and the question becomes readable: does
    # each wall hug one named edge of its own tile, or straddle the boundary
    # between two tiles?
    width, depth = 9, 3
    for tx in range(width):
        for tz in range(depth):
            slab.add(place_tile(floor, tx, tz, 0.0))

    probes = [(1, "n"), (3, "e"), (5, "s"), (7, "w")]
    for tx, side in probes:
        slab.add(place_wall(wall, tx, 1, side, floor.size_y))

    out = pathlib.Path(args.out_dir) / "calibrate.slab.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(encode(slab.normalized()), encoding="utf-8")
    print(f"wrote {out}  ({len(slab)} assets)")
    print(
        "\nPaste into TaleSpire, then LOOK STRAIGHT DOWN and zoom in.\n"
        "You should see a 9x3 floor pad with four separate walls in the middle row,\n"
        "each hugging one edge of its own tile:\n"
        "    tile 2 of 9 -> wall on its NORTH edge (away from you, top of screen)\n"
        "    tile 4 of 9 -> wall on its EAST edge  (right)\n"
        "    tile 6 of 9 -> wall on its SOUTH edge (bottom)\n"
        "    tile 8 of 9 -> wall on its WEST edge  (left)\n"
        "\nIf instead each wall sits centred on its tile, or straddles the line\n"
        "between two tiles, the pivot assumption is wrong: set\n"
        "ROTATE_ABOUT_CENTER = False in citysmith/build.py and regenerate."
    )
    return 0


def cmd_import(args) -> int:
    """Import a GeoJSON town export as a polygonal layout.

    The format is sniffed rather than taken from the extension -- both MFCG and
    FTG exports arrive as ``.json`` and as ``.geojson``. See
    `docs/ftg-geojson-import.md`.
    """
    from . import importers

    try:
        fmt = args.format or importers.detect_format(args.geojson)
        layout = importers.import_layout(
            args.geojson,
            fmt=fmt,
            house_frontage_ft=args.house_ft,
            feet_per_unit=args.feet_per_unit,
            margin_feet=args.margin_ft,
            clip=not args.no_clip,
            core_only=not args.whole_canvas,
            cluster_gap_ft=args.cluster_gap_ft,
            fences=not args.no_fences,
            name=args.name,
            seed=args.seed,
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc

    out_dir = pathlib.Path(args.out_dir)
    print(f"  format: {fmt}")
    print(layout.summary())
    if fmt == importers.FTG:
        from .ftg import check_playability
        problems = check_playability(layout)
    else:
        from .mfcg import check_playability
        problems = check_playability(layout, {})
    for problem in problems:
        print(f"  WARNING: {problem}")
    for prop, values in layout.unmapped.items():
        print(f"  WARNING: unmapped {prop}: {', '.join(values)} -- imported as a default")
    path = out_dir / "layout.json"
    layout.save(path)
    print(f"  wrote {path}")

    if not args.no_svg:
        svg = render.write(
            render.layout_svg(layout, scale=args.scale), out_dir / "layout.svg"
        )
        print(f"  wrote {svg}")
    return 0


def cmd_build(args) -> int:
    """Rasterise an imported layout, verify it, and emit pasteable slabs."""
    from .layout import Layout
    from .raster import rasterize
    from .build import build_from_tilemap
    from .verify import (anchor_on_a_whole_tile, check_placements,
                     chunk_anchors, chunk_datum,
                     enclosed_voids, floating_placements,
                     shells_rest_on_their_floors, tile_interpenetration,
                     tilemap_svg, verify)

    layout = Layout.load(args.layout)
    tm = rasterize(layout, bridges=not args.no_bridges)
    if args.crop:
        try:
            cx, cz, cw, cd = (int(v) for v in args.crop.split(","))
        except ValueError:
            raise SystemExit("--crop expects x,z,width,depth (e.g. 90,90,50,50)")
        tm = tm.crop(cx, cz, cw, cd)
    print(tm.summary())

    out_dir = pathlib.Path(args.out_dir)
    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style, args.seed)
    builder = build_from_tilemap(
        tm, palette, storeys=args.storeys, roofs=not args.no_roofs, seed=args.seed
    )
    plan = builder.chunk_plan(
        max_assets=args.max_assets, chunk_tiles=args.chunk_tiles,
        skip_open_country=not args.keep_open_country,
        per_building=args.per_building,
        by_layer=not args.by_region,
        # Tiling does not pack. Packing fuses a run of neighbours into one
        # chunk, which is right when every chunk is pasted at one shared
        # anchor and wrong when they are laid out side by side: the fused
        # piece is an L or a bar rather than a square, so the cursor step
        # from one paste to the next stops being one region. Uniform squares
        # are what make the step a constant.
        pack=not args.by_region,
    )
    try:
        written = _write_chunks(plan.chunks, out_dir, args.stem)
    except SlabError as exc:
        raise SystemExit(
            f"Could not encode slab: {exc}\nTry a smaller --max-assets "
            "or --chunk-tiles."
        ) from exc

    biggest = max((len(p.read_text(encoding="utf-8")) * 3 // 4 for p in written), default=0)
    report = verify(tm, asset_count=plan.assets_emitted,
                    slab_count=len(written), max_slab_bytes=biggest)

    # The report above reads the tile grid -- the plan. These read the geometry
    # we actually emitted, which is where doorways-turned-wall and off-grid
    # tiles hide.
    for problem in check_placements(builder, tm):
        report.add("fail", "placements", problem)
    for problem in enclosed_voids(plan):
        report.add("fail", "chunk coverage", problem)
    for problem in chunk_anchors(plan, builder.byid):
        report.add("fail", "chunk registration", problem)
    for problem in chunk_datum(plan, builder.byid):
        report.add("fail", "chunk datum", problem)
    for problem in anchor_on_a_whole_tile(plan, builder.byid):
        report.add("fail", "paste anchor", problem)
    # Buried geometry is a finish problem, not a broken map: it shows as a
    # seam rather than stopping anything working, so it warns rather than
    # failing the build.
    for problem in tile_interpenetration(builder):
        report.add("warn", "tile seams", problem)
    for problem in floating_placements(builder, tm):
        report.add("fail", "floating geometry", problem)
    for problem in shells_rest_on_their_floors(builder, tm):
        report.add("fail", "shell footing", problem)

    print(f"\n{plan.assets_emitted:,} assets in {len(written)} chunk(s)"
          + (f"; {len(plan.skipped)} open-country chunk(s) skipped "
             f"({plan.assets_skipped:,} assets)" if plan.skipped else ""))
    print("\n" + report.text())

    svg = render.write(tilemap_svg(tm, scale=args.scale), out_dir / "city-raster.svg")
    print(f"\n  wrote {svg}  (tile numbers here match the chunk table below)")
    print(f"  wrote {len(written)} slab file(s) in {out_dir}")
    print("\n" + _chunk_table(plan, args.stem))
    print("\n" + (TILE_HELP if args.by_region else PASTE_HELP))
    return 2 if report.failed else 0


def cmd_scene(args) -> int:
    """Prepare one building as the board a party walks onto."""
    from . import interior, scene as scene_mod
    from .config import Config
    from .layout import Layout

    try:
        cfg = Config.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    for key in cfg.unknown:
        print(f"  WARNING: {cfg.path}: unknown setting {key!r} -- it does nothing")

    # Command line beats config file beats default, for the few that overlap.
    for flag, key in (("seed", "seed"), ("style", "style"),
                      ("party_size", "party.size"), ("hour", "occupants.hour"),
                      ("roster", "occupants.roster")):
        value = getattr(args, flag, None)
        if value is not None:
            node, _, leaf = key.rpartition(".")
            (cfg.section(node) if node else cfg.data)[leaf] = value

    layout = Layout.load(args.layout)

    if args.list:
        print(f"{layout.name}: {len(layout.buildings)} buildings\n")
        for b in interior.candidates(layout, kind=args.kind or "", limit=args.top):
            long_side, short_side = b.extent
            print(f"  {b.id:16} {b.kind:10} {long_side:4.0f}x{short_side:<4.0f} tiles"
                  f"  {b.name}")
        return 0

    try:
        building = interior.find(layout, args.building)
    except interior.InteriorError as exc:
        raise SystemExit(f"error: {exc}") from exc

    catalog = _catalog(args)
    palette = _palette(args, catalog, cfg.get("style"), int(cfg.get("seed")))

    sc, builder, fp = scene_mod.build(layout, building, palette, cfg)
    out_dir = pathlib.Path(cfg.get("out_dir", "out/scenes")) / sc.scene_id
    try:
        written = scene_mod.write(sc, builder, fp, out_dir, cfg)
    except SlabError as exc:
        raise SystemExit(f"Could not encode slab: {exc}") from exc

    print(sc.summary())
    print(f"\n  hook: {sc.hook}")
    for p in sc.occupants:
        print(f"    {p['name']}, {p['role']} -- {p.get('doing', '')}"
              f" ({p.get('room', '')})")
    print()
    for p in written:
        print(f"  wrote {p}")
    print(f"  wrote {out_dir / 'scene.json'}")
    print(f"  wrote {out_dir / 'brief.md'}")
    print(f"  wrote {out_dir / 'plan.svg'}")
    print("\n" + SCENE_HELP.format(scene_id=sc.scene_id, board=sc.board))
    return 0


def _scene_dir(cfg, ref: str) -> pathlib.Path:
    """A scene id, a scene directory, or a path to a scene.json -- all accepted.

    The driver passes an id; a person types whatever is on their screen.
    """
    p = pathlib.Path(ref)
    if p.is_file():
        return p.parent
    if p.is_dir() and (p / "scene.json").exists():
        return p
    return pathlib.Path(cfg.get("out_dir", "out/scenes")) / ref


def cmd_boards(args) -> int:
    """The scene -> board record. Read by tools/scene.ps1 before every paste.

    Exit codes are the interface, because that is what a PowerShell driver can
    branch on without parsing anything: 0 READY, 3 STALE, 4 NEW, 5 MOVED.
    """
    from . import boards
    from .config import Config
    from .scene import Scene

    cfg = Config.load(args.config)
    registry = boards.Registry.load(cfg.get("registry", "out/scenes/boards.json"))

    if args.action == "list":
        if not registry.records:
            print(f"No boards recorded in {registry.path}.")
            return 0
        print(f"{len(registry.records)} board(s) in {registry.path}:\n")
        for r in sorted(registry.records.values(), key=lambda r: r.board):
            visits = f"{r.visits} visit(s)"
            print(f"  {r.board}")
            print(f"    {r.scene_id}  {visits}, last {r.last_entered or 'never'}")
            for old in r.superseded:
                print(f"    superseded: {old}")
        return 0

    if not args.scene:
        raise SystemExit(f"error: boards {args.action} needs a scene id")

    if args.action == "forget":
        if registry.forget(args.scene):
            print(f"forgot {args.scene}; the board itself is untouched")
            return 0
        print(f"no record of {args.scene}")
        return 4

    directory = _scene_dir(cfg, args.scene)
    manifest = directory / "scene.json"
    if not manifest.exists():
        raise SystemExit(
            f"error: no scene at {manifest}. Build it with `citysmith scene`."
        )
    scene = Scene.load(manifest)
    digest = boards.digest_of_scene(directory, scene)

    if args.action == "record":
        record = registry.record(scene, digest, board=args.board or scene.board)
        print(f"recorded {record.scene_id} on board {record.board!r} "
              f"({record.visits} visit(s))")
        return 0

    if args.action == "visit":
        record = registry.visit(scene.scene_id)
        if record is None:
            print(f"NEW {scene.board}")
            return 4
        print(f"visited {record.board!r} ({record.visits} visit(s))")
        return 0

    status, record = registry.status(scene, digest)
    board = record.board if record else scene.board
    print(f"{status} {board}")
    if status == boards.STALE:
        print(f"  the board holds the build of {record.last_entered}; the files "
              f"on disk have changed since.")
        print("  Reuse it as it is, or -Rebuild onto a second board. Nothing "
              "here deletes the first.")
    if status == boards.MOVED:
        print(f"  recorded at {record.centroid}, this scene is at "
              f"{scene.centroid} -- the town was re-imported and "
              f"{scene.building_id!r} may not be the same building.")
    return {boards.READY: 0, boards.STALE: 3, boards.NEW: 4, boards.MOVED: 5}[status]


SCENE_HELP = """PUT IT ON A BOARD:

    .\\tools\\scene.ps1 enter -Scene {scene_id}

which reuses the board named "{board}" if this building has been visited
before, and makes one if it has not. Nothing is ever deleted.

By hand: new board, camera straight down, paste the slab(s) in the order in
the paste-order file at one cursor cell, then rename the board. The marks in
the floor by the door are where the four tokens go -- a slab cannot carry
creatures, so the minis are dropped on by hand."""


def cmd_verify(args) -> int:
    """Check a rasterised city for playability without building assets."""
    from .layout import Layout
    from .raster import rasterize
    from .verify import tilemap_svg, verify

    layout = Layout.load(args.layout)
    tm = rasterize(layout, bridges=not args.no_bridges)
    print(tm.summary())
    report = verify(tm)
    print("\n" + report.text())
    svg = render.write(tilemap_svg(tm, scale=args.scale),
                       pathlib.Path(args.out_dir) / "city-raster.svg")
    print(f"\n  wrote {svg}")
    return 2 if report.failed else 0


def cmd_brief(args) -> int:
    """Turn a natural language brief into a city (and optionally a slab)."""
    from .ai import AIError, interpret

    try:
        brief = interpret(args.prompt, effort=args.effort)
    except AIError as exc:
        raise SystemExit(f"error: {exc}") from exc

    print(brief.describe())
    print()

    city = generate_city(brief.params, seed=brief.seed)
    out_dir = pathlib.Path(args.out_dir)
    city.save(out_dir / "city.json")
    print(city.summary())

    ranked = sites.rank(city, top=5)
    render.write(render.city_svg(city, scale=args.scale, highlight=ranked), out_dir / "city.svg")
    print("\nTop sites:")
    for i, s in enumerate(ranked, 1):
        print(f"  {i}. {s.name} ({s.building.kind}) score {s.score} [{s.id}]")

    if args.no_design:
        print(f"\nOutput in {out_dir.resolve()}")
        return 0

    chosen = ranked[0]
    fp = floorplan_mod.generate(chosen.building, seed=brief.seed)
    fp.save(out_dir / f"{chosen.id}.plan.json")
    render.write(render.floorplan_svg(fp), out_dir / f"{chosen.id}.plan.svg")

    catalog = _catalog(args)
    palette = _palette(args, catalog, brief.params.style, brief.seed)
    builder = build_interior(fp, palette, seed=brief.seed)
    written = _write_chunks(builder.chunk_plan().chunks, out_dir, chosen.id)
    print(f"\nDesigned: {fp.summary()}")
    for p in written:
        print(f"  wrote {p}")

    if args.describe:
        from .ai import describe_site

        try:
            prose = describe_site(chosen, city, effort=args.effort)
        except AIError as exc:
            print(f"\n(could not generate description: {exc})")
        else:
            notes = out_dir / f"{chosen.id}.notes.md"
            notes.write_text(f"# {chosen.name}\n\n{prose}\n", encoding="utf-8")
            print(f"\n{prose}\n\n  wrote {notes}")

    print(f"\nOutput in {out_dir.resolve()}")
    return 0


def cmd_pipeline(args) -> int:
    """Run city -> sites -> plan -> design in one go."""
    out_dir = pathlib.Path(args.out_dir)
    params = CityParams(size=args.size, style=args.style, name=args.name)
    city = generate_city(params, seed=args.seed)
    city.save(out_dir / "city.json")
    print(city.summary())

    ranked = sites.rank(city, top=5)
    render.write(render.city_svg(city, scale=args.scale, highlight=ranked), out_dir / "city.svg")
    print(f"\nTop sites:")
    for i, s in enumerate(ranked, 1):
        print(f"  {i}. {s.name} ({s.building.kind}) score {s.score} [{s.id}]")

    chosen = ranked[0]
    fp = floorplan_mod.generate(chosen.building, seed=args.seed)
    fp.save(out_dir / f"{chosen.id}.plan.json")
    render.write(render.floorplan_svg(fp), out_dir / f"{chosen.id}.plan.svg")
    print(f"\nDesigned: {fp.summary()}")

    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style, args.seed)
    builder = build_interior(fp, palette, seed=args.seed)
    written = _write_chunks(builder.chunk_plan().chunks, out_dir, chosen.id)
    print(f"  {builder.stats.tiles} tiles + {builder.stats.props} props")
    for p in written:
        print(f"  wrote {p}")
    print(f"\nAll output in {out_dir.resolve()}")
    return 0


# -- argument parsing ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="citysmith", description=__doc__.split("\n")[0])
    p.add_argument("--catalog", default="catalog.json", help="asset catalog path")
    p.add_argument("--talespire-path", default=None, help="TaleSpire install dir")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT), help="output directory")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("catalog", help="build or search the asset catalog")
    c.add_argument("action", choices=["build", "search"])
    c.add_argument("terms", nargs="*", help="free-text search terms")
    c.add_argument("--kind", choices=["tile", "prop", "creature"])
    c.add_argument("--group", help="exact GroupTag, e.g. floor / wall / roof")
    c.add_argument("--tag", action="append", help="require this exact tag (repeatable)")
    c.add_argument("--limit", type=int, default=25)
    c.set_defaults(func=cmd_catalog)

    c = sub.add_parser("city", help="generate a city")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--size", default="town", help=f"{', '.join(SIZES)} or a tile count")
    c.add_argument("--style", default="medieval", choices=sorted(STYLES))
    c.add_argument("--name", default=None)
    c.add_argument("--no-walls", action="store_true")
    c.add_argument("--no-svg", action="store_true")
    c.add_argument("--scale", type=int, default=8, help="SVG pixels per tile")
    c.add_argument("--highlight", type=int, default=5, help="mark N top sites (0 to disable)")
    c.add_argument("--min-block", type=int, default=14)
    c.add_argument("--min-plot", type=int, default=6)
    c.add_argument("--max-floors", type=int, default=3)
    c.set_defaults(func=cmd_city)

    c = sub.add_parser("sites", help="rank buildings by encounter potential")
    c.add_argument("city", help="path to city.json")
    c.add_argument("--top", type=int, default=10)
    c.add_argument("--kind", help="only this building kind")
    c.add_argument("--district", help="only districts matching this text")
    c.add_argument("--min-floors", type=int, default=1)
    c.set_defaults(func=cmd_sites)

    c = sub.add_parser("plan", help="generate an interior floorplan for one site")
    c.add_argument("city")
    c.add_argument("--site", help="building id (default: highest ranked)")
    c.add_argument("--kind", help="when auto-selecting, restrict to this kind")
    c.add_argument("--levels", type=int, default=None)
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--no-svg", action="store_true")
    c.set_defaults(func=cmd_plan)

    c = sub.add_parser("design", help="turn a floorplan into a TaleSpire slab")
    c.add_argument("plan", help="path to a .plan.json")
    c.add_argument("--style", default="medieval", choices=sorted(STYLES))
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--roof", action="store_true", help="include a roof (hides the interior)")
    c.add_argument("--prop-density", type=float, default=0.12)
    c.add_argument("--max-assets", type=int, default=4000)
    c.set_defaults(func=cmd_design)

    c = sub.add_parser("board", help="build the coarse 3D city board")
    c.add_argument("city")
    c.add_argument("--style", default=None, choices=sorted(STYLES))
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--no-ground", action="store_true")
    c.add_argument("--no-streets", action="store_true")
    c.add_argument("--building-height", type=int, default=2)
    c.add_argument("--max-assets", type=int, default=4000)
    c.add_argument("--chunk-tiles", type=int, default=DEFAULT_CHUNK_TILES,
                   help="chunk edge in tiles; smaller chunks skip more empty "
                        f"country and cost more pastes (default {DEFAULT_CHUNK_TILES})")
    c.add_argument("--keep-open-country", action="store_true",
                   help="also write chunks that hold only grass and scenery")
    c.set_defaults(func=cmd_board)

    c = sub.add_parser("calibrate", help="emit a slab to verify placement in-game")
    c.add_argument("--style", default="medieval", choices=sorted(STYLES))
    c.set_defaults(func=cmd_calibrate)

    c = sub.add_parser("import", help="import an MFCG or FTG GeoJSON town export")
    c.add_argument("geojson", help="path to a Medieval Fantasy City Generator or "
                                   "Fantasy Town Generator export")
    c.add_argument("--format", choices=["mfcg", "ftg"], default=None,
                   help="override the format sniff. By default the file is read "
                        "to decide, because the extension does not tell them "
                        "apart -- both formats ship as .json and as .geojson.")
    c.add_argument("--house-ft", type=float, default=None,
                   help="scale anchor: real width in feet of a median building "
                        "footprint. Sets tiles-per-source-unit, so it scales the "
                        "whole town. MFCG has no real scale of its own and uses "
                        "35 ft, chosen for play rather than strict history: "
                        "below ~30 most buildings are too small to stand a party "
                        "in (at 20 only 31%% clear a 3x3 interior; at 35, 94%% "
                        "do), and above 35 buys no further playability. FTG "
                        "declares 1 unit = 1 m and uses that instead; passing "
                        "this overrides it.")
    c.add_argument("--feet-per-unit", type=float, default=None,
                   help="override: feet per source world unit")
    c.add_argument("--margin-ft", type=float, default=60.0,
                   help="feet of suburb kept around the settlement (default 60)")
    c.add_argument("--no-clip", action="store_true", help="keep the entire export")
    c.add_argument("--whole-canvas", action="store_true",
                   help="FTG only: crop to every building instead of to the "
                        "settled core. An FTG canvas is mostly farmland and a "
                        "few outlying farms stretch the window across the whole "
                        "map -- on Graybank that is 853x1013 tiles instead of "
                        "400x272.")
    c.add_argument("--cluster-gap-ft", type=float, default=None,
                   help="FTG only: how far apart two buildings can be and still "
                        "count as one settlement (default 60 m)")
    c.add_argument("--no-fences", action="store_true",
                   help="FTG only: drop field boundaries. They are the format's "
                        "biggest single asset cost out in open country.")
    c.add_argument("--name", default=None)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--scale", type=float, default=4.0, help="SVG pixels per tile")
    c.add_argument("--no-svg", action="store_true")
    c.set_defaults(func=cmd_import)

    c = sub.add_parser("build", help="rasterise a layout, verify it, and emit slabs")
    c.add_argument("layout", help="path to layout.json from `citysmith import`")
    c.add_argument("--style", default="medieval", choices=sorted(STYLES))
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--storeys", type=int, default=3,
                   help="ceiling on storeys; each building uses its own floor "
                        "count from the layout, clipped to this (default 3)")
    c.add_argument("--no-roofs", action="store_true")
    c.add_argument("--no-bridges", action="store_true",
                   help="do not auto-connect districts split by water")
    c.add_argument("--max-assets", type=int, default=9000)
    c.add_argument("--chunk-tiles", type=int, default=DEFAULT_CHUNK_TILES,
                   help="chunk edge in tiles; smaller chunks skip more empty "
                        f"country and cost more pastes (default {DEFAULT_CHUNK_TILES})")
    c.add_argument("--keep-open-country", action="store_true",
                   help="also write chunks that hold only grass and scenery")
    c.add_argument("--per-building", action="store_true",
                   help="emit one slab per building instead of a few large "
                        "structure chunks, so each is pasted and checked on "
                        "its own; the town wall gets its own slab too")
    c.add_argument("--by-region", action="store_true",
                   help="one slab per map region with every layer in it, to "
                        "be tiled onto blank board: nothing is ever pasted "
                        "over anything, which is what stops a chunk inheriting "
                        "the height of what is under the cursor")
    c.add_argument("--scale", type=int, default=3, help="raster SVG pixels per tile")
    c.add_argument("--crop", default=None, metavar="X,Z,W,D",
                   help="build only this tile region, for a staged in-game test")
    c.add_argument("--stem", default="city", help="output filename stem")
    c.set_defaults(func=cmd_build)

    c = sub.add_parser("scene", help="prepare one building as a board to play in")
    c.add_argument("layout", help="path to layout.json from `citysmith import`")
    c.add_argument("building", nargs="?", default="",
                   help="building id ('tavern-0014'), an unambiguous piece of "
                        "its name ('halfling'), or 'kind:tavern' for the "
                        "biggest of a kind")
    c.add_argument("--list", action="store_true",
                   help="list the buildings worth walking into and stop")
    c.add_argument("--kind", default=None, help="with --list, only this kind")
    c.add_argument("--top", type=int, default=20, help="with --list, how many")
    c.add_argument("--config", default=None,
                   help="settings file (default config/scene.json, and every "
                        "key has a working default if it is missing)")
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--style", default=None, choices=sorted(STYLES))
    c.add_argument("--party-size", type=int, default=None,
                   help="how many marks to put on the floor")
    c.add_argument("--hour", default=None, choices=["day", "night"],
                   help="night thins the room")
    c.add_argument("--roster", default=None,
                   help="a JSON sidecar of real occupants keyed by building "
                        "id; it wins over the derived ones")
    c.set_defaults(func=cmd_scene)

    c = sub.add_parser("boards", help="which board holds which scene")
    c.add_argument("action",
                   choices=["status", "record", "visit", "forget", "list"],
                   help="status: what to do about this scene (exit 0 READY, "
                        "3 STALE, 4 NEW, 5 MOVED). record: note that it has "
                        "been pasted. visit: count a return trip. forget: drop "
                        "the record for a board deleted by hand.")
    c.add_argument("scene", nargs="?", default="",
                   help="scene id, its directory, or a path to scene.json")
    c.add_argument("--board", default=None,
                   help="with record, the board name actually used")
    c.add_argument("--config", default=None)
    c.set_defaults(func=cmd_boards)

    c = sub.add_parser("verify", help="check a layout plays well, without building it")
    c.add_argument("layout")
    c.add_argument("--no-bridges", action="store_true")
    c.add_argument("--scale", type=int, default=3)
    c.set_defaults(func=cmd_verify)

    c = sub.add_parser("brief", help="describe a city in plain English (uses Claude)")
    c.add_argument("prompt", help="e.g. 'a rainy harbour town run by smuggling families'")
    c.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    c.add_argument("--scale", type=int, default=8)
    c.add_argument("--no-design", action="store_true", help="stop after the city")
    c.add_argument("--describe", action="store_true", help="also write GM notes for the site")
    c.set_defaults(func=cmd_brief)

    c = sub.add_parser("pipeline", help="city -> sites -> plan -> slab in one command")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--size", default="town")
    c.add_argument("--style", default="medieval", choices=sorted(STYLES))
    c.add_argument("--name", default=None)
    c.add_argument("--scale", type=int, default=8)
    c.set_defaults(func=cmd_pipeline)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CatalogError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
