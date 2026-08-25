"""Every candidate paving and ground material, set into grass, side by side.

A town's whole outdoor surface vocabulary is currently three materials --
cobble for every street and square, gravel for every lane *and* every field
leftover, grass for everything else -- while the raster distinguishes STREET,
PLAZA, LANE, PIER, FIELD and GROUND and classes each road main/cart/lane. Six
distinctions arriving on three materials, one of which does three jobs.

Before any of that is rearranged, the materials have to be *seen*. Nothing in
the catalog says what a tile looks like, and this project has three separate
findings that end "and nothing in the catalog data says either of these
things". So: one pad per candidate, set into a grass field, each labelled by a
bar of N cells running east of it.

Read it with `review.ps1 360`, and the questions are:

  * **Do two of these read as different materials at play distance?** Gravel
    and dry earth may well not, and if they do not then a lane and a field
    edge cannot be told apart however they are keyed.
  * **Do they meet flush?** Every pad is laid with `Builder.surface`, which
    aligns tops rather than bottoms -- cobble is 0.25 thick and grass is 0.5,
    and laying them from a common bottom put a 15-inch kerb along both sides of
    every road on an early board. The plan and cut views are where a step shows.
  * **Which of them read as *made* rather than as terrain?** A civic square
    wants dressed stone; a craft yard wants something that looks worked but not
    laid. That is a judgement and it needs the eye-level shot.

    python tools/surface_probe.py --catalog catalog.json > out/surfaceprobe.slab.txt
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Candidates, by catalog name, in the order they are laid west to east.
#: The three in use today come first so everything else is read against them.
CANDIDATES = [
    ("CobbleStone Floor Small",      "street today"),
    ("gravel_1x1_01",                "lane AND field today"),
    ("Grass 1x1",                    "ground today"),
    ("Desert Ground Dry 01",         "trodden earth"),
    ("Desert Ground 01",             "dust"),
    ("Swamp floor 1x1",              "mud"),
    ("castle floor 1x1",             "dressed flagstone"),
    ("Castle Ruins floor stone 1x1", "weathered flag"),
    ("Castle Ruins Stone Floor 2",   "weathered flag 2"),
    ("Desert stone floor 01",        "pale stone"),
    ("Moorgoth Floor 01",            "dark stone"),
    ("md_floor_1x1_01",              "dungeon stone"),
    ("Rural Floor 01",               "timber deck"),
    ("Ship floor 1x1",               "planking"),
]

PAD = 6          #: pad edge in tiles
GAP = 2          #: grass between pads
MARGIN = 4
LABEL_GAP = 1    #: grass between a pad and its tally bar
#: Laid as a grid, not a row. Zoom-out is capped well short of a long strip,
#: and a probe that does not fit one frame gets read a piece at a time -- which
#: is the reading failure this whole recipe exists to stop.
COLS = 5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    args = ap.parse_args()

    catalog = load_or_build(args.catalog)
    palette = Palette(catalog, MEDIEVAL)
    byname = {a.name: a for a in catalog.assets if not a.deprecated}

    grass = palette.require("ground")
    tally = byname.get("CobbleStone Floor Small") or grass
    top = grass.size_y

    missing = [n for n, _ in CANDIDATES if n not in byname]
    if missing:
        print(f"not in this catalog: {missing}", file=sys.stderr)

    found = [(byname[n], n, why) for n, why in CANDIDATES if n in byname]
    rows = (len(found) + COLS - 1) // COLS
    cell_w = PAD + GAP
    cell_d = PAD + LABEL_GAP + 2 + GAP
    width = MARGIN * 2 + COLS * cell_w
    depth = MARGIN * 2 + rows * cell_d

    # Which cells a pad or a tally owns, so grass is NOT laid under them.
    #
    # **Two tiles in one cell z-fight, and it reads as a broken pad.** The first
    # run of this probe laid grass across the whole board and then set each pad
    # on top at the same top height, so pad and sod occupied the identical
    # volume. On the board half the pads came back as a dither of both textures
    # -- scattered chips of stone in grass -- which looks exactly like a pad
    # that failed to lay. Tiles are not props and nothing is dropped; they
    # simply fight. Lay one tile per cell.
    taken: set[tuple[int, int]] = set()
    pads: list[tuple[object, int, int]] = []
    for i, (asset, name, why) in enumerate(found):
        x0 = MARGIN + (i % COLS) * cell_w
        z0 = MARGIN + (i // COLS) * cell_d
        for dz in range(PAD):
            for dx in range(PAD):
                taken.add((x0 + dx, z0 + dz))
                pads.append((asset, x0 + dx, z0 + dz))
        for k in range(i + 1):
            taken.add((x0 + (k % PAD), z0 + PAD + LABEL_GAP + k // PAD))

    placements = []
    for z in range(depth):
        for x in range(width):
            if (x, z) in taken:
                continue
            placements.append(place_tile(grass, x, z, top - grass.size_y, 0))

    # Laid by the TOP, the same rule every ground pass follows, so a thin
    # cobble and a thick sod finish level with each other.
    for asset, x, z in pads:
        placements.append(place_tile(asset, x, z, top - asset.size_y, 0))

    print(f"{'#':>3} {'material':32} {'thick':>6}  note", file=sys.stderr)
    for i, (asset, name, why) in enumerate(found):
        x0 = MARGIN + (i % COLS) * cell_w
        z0 = MARGIN + (i // COLS) * cell_d
        # Tally: i+1 cells running EAST, wrapping in rows of PAD so it never
        # runs into the next pad. It reads in plan; a vertical stack vanishes
        # from overhead, which is how a four-kit roof probe got counted wrong.
        for k in range(i + 1):
            placements.append(
                place_tile(tally, x0 + (k % PAD),
                           z0 + PAD + LABEL_GAP + k // PAD,
                           top - tally.size_y, 0))
        print(f"{i+1:>3} {name:32} {asset.size_y:6.2f}  {why}", file=sys.stderr)

    print(f"\n{len(found)} pads of {PAD}x{PAD} in {rows} row(s) of {COLS}, "
          f"board {width}x{depth} tiles", file=sys.stderr)
    print("Count the tally bar EAST of each pad to identify it.", file=sys.stderr)
    print(encode(Slab(placements)))


if __name__ == "__main__":
    main()
