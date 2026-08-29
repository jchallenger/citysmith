"""One town, built end to end -- the call a user interface makes.

`cli.cmd_build` used to *be* the pipeline: it derived the chunk budget, built
the NPC population, ran the builder, planned and wrote the chunks, wrote the
manifest, ran every verify pass and formatted the report, all in one 142-line
function with no seam in it. README and CLAUDE.md both claimed `cli.py` was a
thin shell over the core so a UI could be added without touching generation
code, and that claim was false: a second front end had two options against
that function -- shell out and scrape stdout, or reimplement it and drift.

So the decisions and the side effects live here, and the command is left with
argument handling and printing. :func:`build_town` takes **typed values**, not
an argparse namespace and not a command string: the signature is the API, and
a caller that has to assemble a namespace to reach it has not been given one.
:class:`BuildResult` carries everything the CLI prints, as objects rather than
sentences, so any front end can render the same report without re-running the
build.

The stage callback is the other half of that. A build is minutes on a big town
-- East Tradebourne is 411,106 assets -- so a caller that only gets a return
value has nothing to show for those minutes. ``progress`` is called as the work
happens, with a stage name from :data:`STAGES` and structured fields; `cli.py`
turns each into the line it has always printed, and a server can turn the same
events into JSON. Every fact it reports is *also* on the result, so a caller
that does not want progress passes nothing and loses nothing.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from . import build, render
from .build import DEFAULT_CHUNK_TILES
from .layout import Layout
from .slab import encode, multislab

#: The stages :func:`build_town` reports, and the fields each carries. A caller
#: switches on the stage name, so the vocabulary is closed and small; the
#: fields are numbers and objects, never a formatted sentence, because what a
#: line looks like -- or whether there are lines at all -- is the caller's.
STAGES = {
    # The board is rasterised and cropped. `tilemap` is the TileMap.
    "rasterized": ("tilemap",),
    # Marks for the townsfolk are placed. `population` is an npcs.Population.
    "npcs": ("population",),
    # Fires only when the caller did NOT choose a budget and one was derived
    # from board size -- a number the caller passed in is not news.
    "budget": ("assets",),
    # The NPC manifest is written. `path`, and how many `posts` are in it.
    "npc_manifest": ("path", "posts"),
}


def _noop(stage: str, **fields) -> None:
    """Default progress sink, so a caller that does not care passes nothing."""


@dataclass(frozen=True)
class WrittenChunk:
    """One slab file on disk, and what went into it.

    The field names deliberately match :class:`build.SlabChunk`'s --
    ``count``, ``label``, ``region``, ``covers`` -- because the chunk table is
    rendered from a *plan* by some commands and from a finished build by this
    one, and one table is better than two.
    """

    path: pathlib.Path
    label: str          # 'landscape-r02c03' -- the filename stem
    region: str         # 'r02c03' -- the grid cell, shared across layers
    layer: str          # 'landscape' | 'structure' | '' when unlayered
    name: str           # per-building override, '' for a grid chunk
    row: int
    col: int
    x0: int
    z0: int
    x1: int
    z1: int
    assets: int
    #: How many buildings have their whole shell in this chunk. A paste that
    #: misses one structure file leaves exactly these as bare floors.
    buildings: int
    #: Compressed slab bytes, measured the way the 30,720-byte cap is stated:
    #: from what was actually written.
    size_bytes: int
    #: Base64 characters in the file.
    chars: int
    covers: tuple[tuple[int, int], ...] = ()

    @property
    def count(self) -> int:
        return self.assets


@dataclass(frozen=True)
class SkippedChunk:
    """A chunk of open country that was not written, and what was in it."""

    label: str
    region: str
    layer: str
    row: int
    col: int
    x0: int
    z0: int
    x1: int
    z1: int
    assets: int

    @property
    def count(self) -> int:
        return self.assets


@dataclass
class BuildResult:
    """Everything one :func:`build_town` produced -- files, findings, numbers.

    Enough to render the CLI's whole report without re-running anything, which
    is the test of whether the split is real.
    """

    layout: Layout
    tilemap: object
    #: An npcs.Population, or None when marks were switched off.
    population: object | None
    out_dir: pathlib.Path
    stem: str
    chunk_budget: int
    #: True when the budget came from `build.asset_budget` rather than from the
    #: caller. The CLI says so on the line it prints.
    budget_from_board_size: bool
    chunks: list[WrittenChunk] = field(default_factory=list)
    skipped: list[SkippedChunk] = field(default_factory=list)
    rows: int = 0
    cols: int = 0
    tile_size: int = 0
    #: Filenames in the order they must be pasted -- NOT the alphabetical
    #: order. See :func:`write_chunks`.
    paste_order: tuple[str, ...] = ()
    paste_order_path: pathlib.Path | None = None
    raster_svg: pathlib.Path | None = None
    npc_manifest: pathlib.Path | None = None
    multislab: pathlib.Path | None = None
    #: A verify.Report. Its ``findings`` are Finding objects, not strings.
    report: object = None
    #: True when the map was cut by region (every layer in each chunk) rather
    #: than by layer. It decides how the chunks are pasted, so it is reported.
    by_region: bool = False

    @property
    def assets_emitted(self) -> int:
        return sum(c.assets for c in self.chunks)

    @property
    def assets_skipped(self) -> int:
        return sum(c.assets for c in self.skipped)

    @property
    def largest_slab_bytes(self) -> int:
        return max((c.size_bytes for c in self.chunks), default=0)

    @property
    def failed(self) -> bool:
        return bool(self.report is not None and self.report.failed)


def write_chunks(chunks, out_dir: pathlib.Path, stem: str) -> list[pathlib.Path]:
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


def write_multislab(chunks, out_dir: pathlib.Path, stem: str) -> pathlib.Path:
    """Write the whole map as one multi-slab document.

    Chunks cut with ``register=False`` keep their true in-map coordinates, so
    every offset is zero and ``drop`` alone moves the town. That is the whole
    integration: the plugin does the aiming that the registration markers, the
    even-extent rule and the paste order were invented to do by hand.

    The file takes a ``.slab`` extension because that is what the plugins look
    for -- it is JSON inside, unlike our ``.slab.txt`` chunks, which are the
    base64 the game's own ``Ctrl+V`` reads.
    """
    path = out_dir / f"{stem}.multislab.slab"
    path.write_text(multislab([c.slab for c in chunks]), encoding="utf-8")
    return path


def build_town(
    layout,
    *,
    palette,
    out_dir="out",
    stem: str = "city",
    seed: int = 0,
    storeys: int = 3,
    roofs: bool = True,
    bridges: bool = True,
    crop: tuple[int, int, int, int] | None = None,
    quarters: bool = True,
    fence_style: str = build.DEFAULT_FENCE_STYLE,
    npcs: bool = True,
    npc_budget: int | None = None,
    hour: str = "day",
    max_assets: int | None = None,
    chunk_tiles: int = DEFAULT_CHUNK_TILES,
    keep_open_country: bool = False,
    per_building: bool = False,
    by_region: bool = False,
    multi_slab: bool = False,
    raster_scale: int = 3,
    progress=None,
) -> BuildResult:
    """Rasterise a layout, build it, write the slabs, verify what was written.

    ``layout`` is a :class:`~citysmith.layout.Layout` or a path to one.
    ``crop`` is ``(x, z, width, depth)`` in tiles, already parsed -- turning
    ``"90,90,50,50"`` into four numbers is the caller's job, because a UI has
    four fields and not a string.

    The booleans are stated positively (``roofs=True``) where the CLI states
    them negatively (``--no-roofs``); flipping a flag is argument handling.

    ``max_assets=None`` means "let the board decide", the same as the CLI's
    default, and the derived number comes back on the result.

    Raises :class:`~citysmith.slab.SlabError` if a chunk will not encode. That
    is left to the caller rather than turned into an exit: a UI has somewhere
    better to put the message than a dead process.
    """
    # Imported here rather than at module scope for the same reason `cli` does
    # it: `citysmith --help` should not pay to import the raster and every
    # verify pass. Measured at 17 ms, against 135 ms to import the CLI.
    from . import npcs as npcs_mod
    from .raster import rasterize
    from .verify import (anchor_on_a_whole_tile, check_placements, feature_report,
                         chunk_anchors, chunk_datum,
                         enclosed_voids, floating_placements,
                         shells_rest_on_their_floors, tile_interpenetration,
                         tilemap_svg, verify)

    say = progress or _noop
    if not isinstance(layout, Layout):
        layout = Layout.load(layout)
    out_dir = pathlib.Path(out_dir)

    tm = rasterize(layout, bridges=bridges)
    if crop is not None:
        tm = tm.crop(*crop)
    say("rasterized", tilemap=tm)

    population = None
    if npcs:
        population = npcs_mod.posts(tm, layout, seed=seed, hour=hour,
                                    budget=npc_budget)
        say("npcs", population=population)

    builder = build.build_from_tilemap(
        tm, palette, storeys=storeys, roofs=roofs, seed=seed,
        fence_style=fence_style, layout=layout,
        quarters=quarters, npc_population=population,
    )
    # Unset means "let the board decide" -- a small board splits into so few
    # chunks that a tight budget only costs pastes, while a large one needs the
    # headroom. See `build.asset_budget` for the measurements.
    derived = max_assets is None
    budget = build.asset_budget(tm) if derived else max_assets
    if derived:
        say("budget", assets=budget)

    plan = builder.chunk_plan(
        max_assets=budget, chunk_tiles=chunk_tiles,
        skip_open_country=not keep_open_country,
        per_building=per_building,
        by_layer=not by_region,
        # Tiling does not pack. Packing fuses a run of neighbours into one
        # chunk, which is right when every chunk is pasted at one shared
        # anchor and wrong when they are laid out side by side: the fused
        # piece is an L or a bar rather than a square, so the cursor step
        # from one paste to the next stops being one region. Uniform squares
        # are what make the step a constant.
        pack=not by_region,
        # **Registration markers exist only to serve a cursor-anchored paste.**
        # The plugin path states each slab's position, so the markers -- and
        # the even-extent box, and the written paste order they enforce -- are
        # dead weight there. Two stray tiles per chunk, at the map's corners,
        # that a reviewer can see.
        register=not multi_slab,
    )

    result = BuildResult(
        layout=layout, tilemap=tm, population=population,
        out_dir=out_dir, stem=stem,
        chunk_budget=budget, budget_from_board_size=derived,
        rows=plan.rows, cols=plan.cols, tile_size=plan.tile_size,
        by_region=by_region,
    )

    # The manifest, not the marks, is the durable half of "a position": a slab
    # carries no creatures, so the board says *where* and this says *who*.
    if population is not None and population.posts:
        out_dir.mkdir(parents=True, exist_ok=True)
        result.npc_manifest = out_dir / f"{stem}-npcs.json"
        result.npc_manifest.write_text(
            json.dumps(npcs_mod.manifest(population), indent=1) + "\n",
            encoding="utf-8")
        say("npc_manifest", path=result.npc_manifest,
            posts=len(population.posts))

    written = write_chunks(plan.chunks, out_dir, stem)
    if multi_slab:
        # Deliberately NOT in `written`. That list is the pasteable slabs, and
        # it is what the byte cap and the slab count are measured against --
        # the multi-slab document is a JSON wrapper around those same slabs, so
        # counting it double-counts the map and measures a 129 KB file against
        # the 30 KB *slab* cap.
        result.multislab = write_multislab(plan.chunks, out_dir, stem)

    for chunk, path in zip(plan.chunks, written):
        chars = len(path.read_text(encoding="utf-8"))
        result.chunks.append(WrittenChunk(
            path=path, label=chunk.label, region=chunk.region,
            layer=chunk.layer, name=chunk.name, row=chunk.row, col=chunk.col,
            x0=chunk.x0, z0=chunk.z0, x1=chunk.x1, z1=chunk.z1,
            assets=chunk.count, buildings=chunk.buildings,
            # Base64 carries three bytes in four characters, so this is the
            # compressed size the cap is stated in.
            size_bytes=chars * 3 // 4, chars=chars,
            covers=tuple(chunk.covers),
        ))
    for chunk in plan.skipped:
        result.skipped.append(SkippedChunk(
            label=chunk.label, region=chunk.region, layer=chunk.layer,
            row=chunk.row, col=chunk.col,
            x0=chunk.x0, z0=chunk.z0, x1=chunk.x1, z1=chunk.z1,
            assets=chunk.count,
        ))
    result.paste_order = tuple(p.name for p in written)
    result.paste_order_path = out_dir / f"{stem}-paste-order.txt"

    report = verify(tm, asset_count=plan.assets_emitted,
                    slab_count=len(written),
                    max_slab_bytes=result.largest_slab_bytes)

    # The report above reads the tile grid -- the plan. These read the geometry
    # we actually emitted, which is where doorways-turned-wall and off-grid
    # tiles hide.
    for problem in check_placements(builder, tm):
        report.add("fail", "placements", problem)
    for problem in enclosed_voids(plan):
        report.add("fail", "chunk coverage", problem)
    # What each designed feature had available here, and what it built from it.
    # A feature can be perfectly correct and simply absent from the region you
    # cropped -- which is not a defect, but it must not be silent either.
    for level, name, detail in feature_report(builder, tm, layout, seed):
        report.add(level, name, detail)
    # Registration, datum and anchor all exist to make a *cursor-anchored*
    # paste land right: they check that every chunk presents the same bounding
    # box, reaches the ground, and centres on a whole tile. The multi-slab path
    # states each slab's position instead, so there is no shared box to agree
    # on and nothing to aim -- running these there reports four failures for a
    # map that is correct.
    if not multi_slab:
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
    result.report = report

    result.raster_svg = render.write(
        tilemap_svg(tm, scale=raster_scale), out_dir / "city-raster.svg")
    return result
