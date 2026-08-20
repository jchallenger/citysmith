"""Show every candidate rampart block in the two shapes a circuit actually makes.

The mass of the town wall is laid one full-cell block per cell, and the block
was chosen by pasting six candidates as flat 3x3x2 masses. A flat mass is the
one shape a circuit never is. `md_wall_1x1_diag_01` -- the piece that won that
probe -- is a *diagonal*: its mesh cuts the cell corner to corner. Tiled across
a flat face the cut edges meet and it reads as coursed stone; run along a wall
it leaves a vertical slot of daylight between every pair of cells.

So this probe builds what the generator builds. Per candidate, two shapes:

    a straight run, and the stair-stepped diagonal a raster circuit is made of

Two cells thick and two courses tall, on a grass pad, so anything that fails to
close shows as a bright slot rather than as a void. The whole grid is laid in
one screenful deliberately: the camera in TaleSpire pans by dragging and does
not do it reliably under synthetic input, so a probe that needs scrolling is a
probe that does not get read from more than one angle.

    python tools/wall_probe.py > out/wallprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Candidates, by catalog name. The first is what the wall is built from today,
#: and it stays first so every screenshot has the control in the corner.
BLOCKS = [
    "md_wall_1x1_diag_01",
    "md_pref_wall_1x1_01",
    "md_stairblock_01",
    "Tall 1x1x2",
    "Castle Ruins Wallbase 02",
    "castle wall corner 1x1 base",
]

COLS = 3           #: candidates across, so six fit one screen
THICK = 2          #: cells through the wall
COURSES = 2        #: blocks stacked
RUN = 5            #: cells along a straight section
CELL_W, CELL_D = 8, 11
GAP = 1

#: A marker course of merlons sits at the north-west corner of each candidate's
#: pad, one per index, so a screenshot says which block it is without counting.
TALLY = "city_wall_cap"


def straight_cells() -> list[tuple[int, int]]:
    return [(x, z) for x in range(RUN) for z in range(THICK)]


def stair_cells() -> list[tuple[int, int]]:
    """A diagonal run as the rasteriser makes it: one cell across per step.

    A straight line on a diagonal is a staircase on a square grid, so this is
    the shape most of the circuit is -- and the shape the flat-mass probe that
    picked the current block never tested.
    """
    cells: set[tuple[int, int]] = set()
    for step in range(RUN):
        for t in range(THICK):
            cells.add((step, step + t))
    return sorted(cells)


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)     # first match is what resolve() picks
    grass = palette.require("ground")
    tally = palette.require(TALLY)

    out = []
    for i, name in enumerate(BLOCKS):
        block = byname.get(name)
        if block is None:
            print(f"# {name}: not in catalog, skipped", file=sys.stderr)
            continue
        x0 = (i % COLS) * (CELL_W + GAP)
        z0 = (i // COLS) * (CELL_D + GAP)

        for dz in range(CELL_D):
            for dx in range(CELL_W):
                out.append(place_tile(grass, x0 + dx, z0 + dz, -grass.size_y))

        # Index tally, so the screenshot is self-labelling.
        for t in range(i + 1):
            out.append(place_tile(tally, x0 + t, z0, 0.0))

        for cx, cz in straight_cells():
            for course in range(COURSES):
                out.append(place_tile(block, x0 + cx + 1, z0 + cz + 2,
                                      course * block.size_y))
        for cx, cz in stair_cells():
            for course in range(COURSES):
                out.append(place_tile(block, x0 + cx + 1, z0 + cz + 5,
                                      course * block.size_y))

        print(f"# {i + 1}: {name}  "
              f"({block.size_x:.2f}x{block.size_y:.2f}x{block.size_z:.2f})",
              file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
