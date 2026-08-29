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

from . import build
from . import floorplan as floorplan_mod
from . import render, sites
from .build import DEFAULT_CHUNK_TILES, build_city_board, build_interior
from .catalog import Catalog, CatalogError, load_or_build
from .city import CityParams, City, SIZES
from .city import generate as generate_city
from .palette import STYLES, Palette
from .pipeline import build_town, write_chunks
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


def _chunk_table(plan, stem: str) -> str:
    """A map and a table of which chunk covers which tiles.

    Printed so the reader can paste a quarter of a town instead of all of it:
    match a region on ``city-raster.svg`` to a row/column here, then paste only
    those files.

    ``plan`` is a :class:`build.ChunkPlan` from a command that has just planned
    one, or a finished :class:`pipeline.BuildResult`. The two agree on the
    field names this reads, which is why there is one table and not two.
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
MULTISLAB_HELP = """PASTE THE WHOLE MAP IN ONE ACTION, WITH A PLUGIN.

{name} is a multi-slab document: JSON holding every chunk of this map, each
with the position it belongs at. It needs one of LordAshes' paste plugins --
MultiPasteSlabsPlugin or SlabPlugin_CCM, both on Thunderstore, both needing
BepInEx.

  1. Copy the whole contents of the file to the clipboard.
  2. In build mode, press LCTRL+B to place every slab at the stated position,
     or RCTRL+B to be prompted for an offset first.

Because the plugin does the aiming, this build carries NO registration markers
and needs NO paste order, NO shared cursor cell, and NO camera discipline. The
two marker tiles that normally sit outside the map's corners are not there.

The plugin is third-party and does break on TaleSpire updates from time to
time. The ordinary chunk files are written alongside and still paste with
Ctrl+V on a vanilla install -- but note they carry no registration markers in
this mode, so if you fall back to them, rebuild without --multi-slab.
"""

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
        written = write_chunks(plan.chunks, pathlib.Path(args.out_dir), f"{fp.building_id}")
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
        written = write_chunks(plan.chunks, pathlib.Path(args.out_dir), "city-board")
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
    """Rasterise an imported layout, verify it, and emit pasteable slabs.

    The build itself is :func:`pipeline.build_town`. What is left here is
    turning flags into values and turning the result into lines -- which is
    the claim this module has always made about itself and did not keep.
    """
    # Parsing "90,90,50,50" into four numbers is the command line's job: a UI
    # has four fields, and `build_town` takes the numbers.
    crop = None
    if args.crop:
        try:
            cx, cz, cw, cd = (int(v) for v in args.crop.split(","))
        except ValueError:
            raise SystemExit("--crop expects x,z,width,depth (e.g. 90,90,50,50)")
        crop = (cx, cz, cw, cd)

    # Resolved before the build rather than in the middle of it. A style the
    # installed packs cannot supply now stops the command before it rasterises
    # a town instead of after -- the one place this command's output moved.
    catalog = _catalog(args)
    palette = _palette(args, catalog, args.style, args.seed)

    def show(stage: str, **f) -> None:
        """The lines the pipeline used to print itself.

        Streamed rather than collected and printed at the end, because the
        alternative on a big town is minutes of silence: East Tradebourne is
        411,106 assets. Every one of these facts is on the result as well, so
        a caller that wants the report in one piece can have it either way.
        """
        if stage == "rasterized":
            print(f["tilemap"].summary())
        elif stage == "npcs":
            print(f"  npcs: {f['population'].summary()}")
        elif stage == "budget":
            print(f"  chunk budget: {f['assets']:,} assets (from board size)")
        elif stage == "npc_manifest":
            print(f"  wrote {f['path']}  ({f['posts']} post(s))")

    try:
        result = build_town(
            args.layout, palette=palette,
            out_dir=args.out_dir, stem=args.stem, seed=args.seed,
            storeys=args.storeys, roofs=not args.no_roofs,
            bridges=not args.no_bridges, crop=crop,
            quarters=not args.no_quarters, fence_style=args.fence_style,
            npcs=not args.no_npcs, npc_budget=args.npc_budget, hour=args.hour,
            max_assets=args.max_assets, chunk_tiles=args.chunk_tiles,
            keep_open_country=args.keep_open_country,
            per_building=args.per_building, by_region=args.by_region,
            multi_slab=args.multi_slab, raster_scale=args.scale,
            progress=show,
        )
    except SlabError as exc:
        raise SystemExit(
            f"Could not encode slab: {exc}\nTry a smaller --max-assets "
            "or --chunk-tiles."
        ) from exc

    print(f"\n{result.assets_emitted:,} assets in {len(result.chunks)} chunk(s)"
          + (f"; {len(result.skipped)} open-country chunk(s) skipped "
             f"({result.assets_skipped:,} assets)" if result.skipped else ""))
    print("\n" + result.report.text())

    print(f"\n  wrote {result.raster_svg}  "
          "(tile numbers here match the chunk table below)")
    print(f"  wrote {len(result.chunks)} slab file(s) in {result.out_dir}")
    print("\n" + _chunk_table(result, args.stem))
    print("\n" + (TILE_HELP if args.by_region else PASTE_HELP))
    return 2 if result.failed else 0


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


#: Mirrors `boards.HOLDS`. Repeated rather than imported because `cli` builds
#: its parser at import time and every other module here is imported inside the
#: command that needs it -- `test_cli_holds_matches_the_registry` is what keeps
#: the two honest.
_HOLDS = ("town", "scene", "probe", "other")


def cmd_boards(args) -> int:
    """The scene -> board record. Read by tools/scene.ps1 before every paste.

    Exit codes are the interface, because that is what a PowerShell driver can
    branch on without parsing anything: 0 READY, 3 STALE, 4 NEW, 5 MOVED.
    """
    from . import boards
    from .config import Config
    from .scene import Scene

    cfg = Config.load(args.config)
    registry = boards.Registry.load(cfg.get("registry", "campaign/boards.json"))

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

    def _listing():
        """The campaign listing, from --seen and --seen-file together.

        Indentation carries the folder, so the raw lines go to `parse_seen`
        with their leading whitespace intact -- stripping first was a bug
        waiting to happen, since the parser's whole input is the indentation.
        """
        raw = [n for n in (args.seen or "").splitlines() if n.strip()]
        if args.seen_file:
            raw += [n for n in pathlib.Path(args.seen_file)
                    .read_text(encoding="utf-8").splitlines() if n.strip()]
        return boards.parse_seen(raw)

    if args.action == "index":
        filed = _listing()
        if not registry.index:
            print(f"No boards indexed in {registry.path}.\n")
            print("The index is written at PASTE time, because that is the only "
                  "moment anything knows what is going on a board -- TaleSpire "
                  "exposes no contents, no size and no date afterwards. Record "
                  "one with:\n")
            print('  citysmith boards note --board "East Tradebourne" '
                  '--holds town --source out/tradebourne-v2/layout.json')
            if not filed:
                return 0

        if registry.index:
            print(f"{len(registry.index)} board(s) indexed in {registry.path}:\n")
            by_folder: dict[str, list] = {}
            for entry in sorted(registry.index.values(), key=lambda e: e.board):
                by_folder.setdefault(entry.folder, []).append(entry)
            for folder in sorted(by_folder):
                if folder:
                    print(f"  {folder}:")
                for entry in by_folder[folder]:
                    lead = "    " if folder else "  "
                    flag = "  [disposable]" if entry.disposable else ""
                    print(f"{lead}{entry.board}{flag}")
                    print(f"{lead}  {entry.summary}")
                    if entry.recorded:
                        print(f"{lead}  recorded {entry.recorded}")
                    if entry.note:
                        print(f"{lead}  {entry.note}")

        if not filed:
            print("\n(No campaign list given, so nothing here is checked "
                  "against the game. Run `tools\\ts.ps1 boards`, transcribe the "
                  "rows and pass them with --seen-file to find the boards "
                  "nobody wrote down.)")
            return 0

        r = boards.reconcile(registry, filed)
        print(f"\nAGAINST THE CAMPAIGN LIST -- {len(r.matched)} of "
              f"{len(r.matched) + len(r.unrecorded)} board(s) on screen are "
              f"indexed ({r.coverage:.0%}).")
        if r.missing:
            print(f"\nGONE -- {len(r.missing)} indexed board(s) are not in the "
                  "list. Deleted or renamed by hand; nothing here is told "
                  "either way. `boards rename` follows a rename, `boards drop` "
                  "forgets a deletion:")
            for entry in r.missing:
                print(f"  {entry.board}  ({entry.summary})")
        if r.unrecorded:
            print(f"\nUNRECORDED -- {len(r.unrecorded)} board(s) in the list "
                  "with nothing written down. This is the bucket that makes "
                  "deleting dangerous: the list gives a name and nothing else, "
                  "so a finished town and last week's throwaway read the same:")
            for board in r.unrecorded:
                where = f"  [{board.folder}]" if board.folder else ""
                print(f"  {board.name}{where}")
        return 1 if r.unrecorded else 0

    if args.action == "note":
        if not args.board:
            raise SystemExit("error: boards note needs --board <name>")
        disposable = None
        if args.disposable:
            disposable = True
        if args.keep:
            disposable = False
        entry = registry.note(
            args.board, holds=args.holds, source=args.source,
            folder=args.folder, stem=args.stem, chunks=args.chunks,
            assets=args.assets, note=args.note, disposable=disposable)
        registry.save()
        flag = " (disposable)" if entry.disposable else ""
        print(f"indexed {entry.board!r}: {entry.summary}{flag}")
        return 0

    if args.action == "drop":
        if not args.board:
            raise SystemExit("error: boards drop needs --board <name>")
        if registry.drop(args.board):
            registry.save()
            print(f"dropped {args.board!r} from the index; "
                  "the board itself is untouched")
            return 0
        print(f"no index entry for {args.board!r}")
        return 4

    if args.action == "prune":
        filed = _listing()
        seen = [b.name for b in filed]
        gone = boards.prunable(registry, seen)
        keep = boards.keepers(registry)
        other = boards.unclaimed(registry, seen)
        blank = boards.unnamed(registry, seen)
        spare = boards.disposable(registry, seen or None)
        indexed = {e.board for e in registry.index.values()}
        # Anything the index already accounts for is not a mystery, so it does
        # not belong in the two "go and look" buckets. Those exist because
        # nothing was written down; this is what writing it down is worth.
        other = [n for n in other if n not in indexed]
        blank = [n for n in blank if n not in indexed]
        print(f"KEEP -- {len(keep)} board(s) a scene points at:")
        for record in keep:
            print(f"  {record.board}")
        if other:
            print(f"\nNOT TRACKED -- {len(other)} board(s) somebody named by "
                  "hand. The registry only ever held scenes, so this is where "
                  "the town boards are. Listed, not recommended:")
            for name in other:
                print(f"  {name}")
        if blank:
            print(f"\nLOOK FIRST -- {len(blank)} board(s) still called "
                  f"'{boards.UNNAMED_PREFIX} N'. Usually a probe or a rebuild, "
                  "and NOT safe to delete on the name alone: one of these was "
                  "the newest build of a town its owner wanted. Switch to each "
                  "and look, then name the ones worth keeping:")
            for name in blank:
                print(f"  {name}")
        if spare:
            print(f"\nDISPOSABLE -- {len(spare)} board(s) the index records as "
                  "safe to delete. This is the only bucket here that is a "
                  "record rather than the absence of one: somebody said so when "
                  "they pasted it:")
            for entry in spare:
                print(f"  {entry.board}  ({entry.summary})")
        print(f"\nPRUNE -- {len(gone)} board(s) nothing points at:")
        for item in gone:
            print(f"  {item.describe()}")
        if not seen:
            print("\n(No campaign list given, so only superseded names are "
                  "listed. Run `tools\\ts.ps1 boards`, read the rows off the "
                  "screenshot and pass them with --seen-file to catch the "
                  "unnamed boards too.)")
        print("\nNothing here deletes anything. Delete board sits behind the "
              "per-board triangle in the campaign list, right beside the play "
              "arrow, and the rows move on every rename -- so a click that "
              "misses by one deletes the wrong board, and there is no undo.")

        # The one hard error here. Loose in the campaign, an unnamed board is
        # only a LOOK FIRST; filed under the published folder it is a
        # contradiction, because filing it there is a claim and `Unknown Realm
        # N` is the name the game invents when nobody made one.
        unfit = boards.unfit_to_publish(filed)
        if unfit:
            print(f"\nFAIL -- {len(unfit)} board(s) in "
                  f"'{boards.PUBLISHED_FOLDER}' that nobody ever named:")
            for name in unfit:
                print(f"  {name}")
            print("Filing a board there is a claim about it, and that name is "
                  "the one the game invents when nobody made one. Either it is "
                  "the wrong row -- they move on every rename -- or it needs a "
                  "name before anyone else sees it.")
            return 1
        return 0

    if not args.scene:
        raise SystemExit(f"error: boards {args.action} needs a scene id")

    if args.action == "rename":
        if not args.board:
            raise SystemExit("error: boards rename needs --board <new name>")
        # A rename is the one edit that silently orphans an index entry, since
        # the index is keyed on the name and TaleSpire has no board id. Follow
        # it here, where the old name is still known.
        was = registry.get(args.scene)
        record = registry.rename(args.scene, args.board)
        if record is None:
            print(f"no record of {args.scene}")
            return 4
        if was is not None and was.board in registry.index:
            registry.index[args.board] = registry.index.pop(was.board)
            registry.index[args.board].board = args.board
            registry.save()
        print(f"{record.scene_id} now points at {record.board!r}; "
              "the digest is untouched, so a stale board still reads STALE")
        return 0

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
        # A scene board is a board, so it goes in the index too -- otherwise
        # `index --seen-file` reports every interior as unrecorded and the
        # coverage number is a lie about the one thing that was tracked all
        # along.
        registry.note(record.board, holds="scene", source=record.scene_id,
                      folder=args.folder or boards.PUBLISHED_FOLDER,
                      digest=digest, disposable=False)
        registry.save()
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


def cmd_tasks(args) -> None:
    from . import tasks as T

    items = T.load()
    changed = False

    if args.add:
        task = T.add(items, args.add, doc=args.doc, evidence=args.evidence,
                     note=args.note, tags=list(args.tags))
        print(f"added {task.id}")
        changed = True
    if args.done:
        changed |= _restate(items, args.done, "done")
    if args.state:
        if args.to not in T.STATES:
            raise SystemExit(f"--to must be one of {', '.join(T.STATES)}")
        changed |= _restate(items, args.state, args.to)
    if changed:
        print(f"wrote {T.save(items)}")

    print()
    print(T.report(items, check=args.check), end="")


def _restate(items, task_id: str, state: str) -> bool:
    for t in items:
        if t.id == task_id:
            t.state = state
            print(f"{t.id} -> {state}")
            return True
    raise SystemExit(f"no task with id {task_id!r}")


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
    written = write_chunks(builder.chunk_plan().chunks, out_dir, chosen.id)
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
    written = write_chunks(builder.chunk_plan().chunks, out_dir, chosen.id)
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
    c.add_argument("--max-assets", type=int, default=None,
                   help="assets per chunk; the quadtree splits a cell that "
                        "exceeds it. Default follows board size (see "
                        "build.asset_budget): a small board splits into so "
                        "few chunks that a tight budget only costs pastes, "
                        "while a large one needs the headroom -- East "
                        "Tradebourne reached 99.4%% of the 30,720-byte slab "
                        "cap at 9000. Lowering --chunk-tiles is NOT the "
                        "same lever and makes it worse.")
    c.add_argument("--chunk-tiles", type=int, default=DEFAULT_CHUNK_TILES,
                   help="chunk edge in tiles; smaller chunks skip more empty "
                        f"country and cost more pastes (default {DEFAULT_CHUNK_TILES})")
    c.add_argument("--keep-open-country", action="store_true",
                   help="also write chunks that hold only grass and scenery")
    c.add_argument("--per-building", action="store_true",
                   help="emit one slab per building instead of a few large "
                        "structure chunks, so each is pasted and checked on "
                        "its own; the town wall gets its own slab too")
    c.add_argument("--multi-slab", action="store_true",
                   help="also write <stem>.multislab.slab, a JSON document for "
                        "LordAshes' MultiPasteSlabs / SlabPlugin_CCM. Those "
                        "place each slab at a stated position, so the map "
                        "needs no cursor aiming, no shared bounding box and no "
                        "paste order -- at the cost of a BepInEx plugin. "
                        "Requires the plugin; the chunk files still work "
                        "without it.")
    c.add_argument("--by-region", action="store_true",
                   help="one slab per map region with every layer in it, to "
                        "be tiled onto blank board: nothing is ever pasted "
                        "over anything, which is what stops a chunk inheriting "
                        "the height of what is under the cursor")
    c.add_argument("--scale", type=int, default=3, help="raster SVG pixels per tile")
    c.add_argument("--no-quarters", action="store_true",
                   help="do not vary lane and yard surfaces by derived "
                        "quarter; the measurement already switches this off "
                        "on a town whose trades do not cluster")
    c.add_argument("--fence-style", default=build.DEFAULT_FENCE_STYLE,
                   choices=sorted(build.FENCE_STYLES),
                   help="how field boundaries are built; see docs/fencing.md "
                        f"(default {build.DEFAULT_FENCE_STYLE})")
    c.add_argument("--crop", default=None, metavar="X,Z,W,D",
                   help="build only this tile region, for a staged in-game test")
    c.add_argument("--no-npcs", action="store_true",
                   help="do not mark where the townsfolk and the watch are "
                        "standing. A v2 slab carries no creatures, so each is "
                        "a contrasting floor tile plus a row in "
                        "<stem>-npcs.json for the GM to read while placing "
                        "minis -- the same device a scene uses for the party.")
    c.add_argument("--npc-budget", type=int, default=None, metavar="N",
                   help="cap the number of NPC marks; guards are kept first, "
                        "then people at work, then the off-duty")
    c.add_argument("--hour", default="day", choices=["day", "night"],
                   help="who is about: at night a household is half its "
                        "daytime size (default day)")
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
                   choices=["status", "record", "rename", "visit", "forget",
                            "list", "prune", "index", "note", "drop"],
                   help="status: what to do about this scene (exit 0 READY, "
                        "3 STALE, 4 NEW, 5 MOVED). record: note that it has "
                        "been pasted. visit: count a return trip. forget: drop "
                        "the record for a board deleted by hand. rename: "
                        "point the record at a new board name without "
                        "claiming a fresh paste. prune: list the boards "
                        "nothing points at, so a person can delete them. "
                        "index: what every board holds, and what the campaign "
                        "list shows that nothing has written down. note: "
                        "record what a board holds. drop: forget a board.")
    c.add_argument("--seen", default="",
                   help="board names from the campaign list, one per line; "
                        "anything no scene claims is reported as prunable")
    c.add_argument("--seen-file", default=None, metavar="PATH",
                   help="the same list, read from a file")
    c.add_argument("scene", nargs="?", default="",
                   help="scene id, its directory, or a path to scene.json")
    c.add_argument("--board", default=None,
                   help="with record, the board name actually used; with note "
                        "and drop, the board to index or forget")
    c.add_argument("--holds", default="other", choices=list(_HOLDS),
                   help="with note, what is on the board (default: other)")
    c.add_argument("--source", default="",
                   help="with note, what it was built from -- a layout path, a "
                        "scene id, a slab, a sentence")
    c.add_argument("--folder", default="",
                   help="with note, the campaign folder it is filed under")
    c.add_argument("--stem", default="",
                   help="with note, the build stem its slab files carry")
    c.add_argument("--chunks", type=int, default=0,
                   help="with note, how many slabs were pasted")
    c.add_argument("--assets", type=int, default=0,
                   help="with note, how many assets those slabs held")
    c.add_argument("--note", default="",
                   help="with note, anything worth remembering about it")
    c.add_argument("--disposable", action="store_true",
                   help="with note, mark the board safe to delete without "
                        "looking. `probe` is disposable by default")
    c.add_argument("--keep", action="store_true",
                   help="with note, the opposite: never list it as disposable")
    c.add_argument("--config", default=None)
    c.set_defaults(func=cmd_boards)

    c = sub.add_parser(
        "tasks",
        help="what was designed, what was built, and the difference",
        description=(
            "A design that is never built looks exactly like one that was, "
            "because both are paragraphs of prose. Every task carries the "
            "dotted path of the symbol that exists when it is done, and "
            "--check imports each one: a task marked done whose evidence is "
            "missing is a lie, and one marked open whose evidence is already "
            "there is stale bookkeeping."),
    )
    c.add_argument("--check", action="store_true",
                   help="verify every claim against the code")
    c.add_argument("--add", metavar="TEXT", help="record a new task")
    c.add_argument("--doc", default="", help="design document it came from")
    c.add_argument("--evidence", default="",
                   help="dotted symbol path, or test:<name>, proving it is built")
    c.add_argument("--note", default="")
    c.add_argument("--tag", action="append", default=[], dest="tags")
    c.add_argument("--done", metavar="ID", help="mark a task done")
    c.add_argument("--state", metavar="ID", help="task to restate with --to")
    c.add_argument("--to", default="", help="new state for --state")
    c.set_defaults(func=cmd_tasks)

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
