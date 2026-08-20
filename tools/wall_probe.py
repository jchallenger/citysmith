"""Show every candidate rampart block from every side, in the harshest shape.

Twice now a block has been chosen from too few views and put daylight through
the whole circuit.

  * `md_wall_1x1_diag_01` measures a full cell and is a blade cutting it corner
    to corner. It won a probe of flat 3x3x2 masses read from the front, which is
    the one view where a rank of blades hides its own gaps.
  * `Castle Ruins Wallbase 02` replaced it and is *ruined* masonry -- the kit is
    broken wall by design. It read solid from overhead and from one oblique,
    because at those angles its own front face covers its holes. Tiled into a
    town it produced a lattice of piers and lintels you can see straight
    through, houses included.

So this probe is built to be walked around. Three shapes per candidate, worst
first:

    a single block  |  a run one cell thick  |  a mass two cells thick

A one-cell run is the harshest test there is: nothing stands behind the block to
plug what it leaves open, so any hole shows sky. Candidates are laid in a single
row so that one orbit of the camera reads all of them, and the review is four
low passes at ninety degrees plus one from overhead -- not one screenshot.

    python tools/wall_probe.py > out/wallprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Candidates, by catalog name, with the two known-bad ones kept as controls so
#: every screenshot contains a failure to calibrate against.
BLOCKS = [
    "md_stairblock_01",
    "md_stairblock_02",
    "bg_stairblock_01",
    "Dungeon Stair Block",
    "shugunRockBlock_1x2",
    "Castle Ruins Wallbase 02",     # control: ruined, gaps
    "md_wall_1x1_diag_01",          # control: a blade
]

RUN = 4            #: cells along a run
WALL_TILES = 5.0   #: how tall to build, in tiles, so candidates match in height
CELL_W, CELL_D = 6, 12
GAP = 2


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)
    grass = palette.require("ground")
    tally = byname["castle merlon 1x1 filler"]

    out = []
    for i, name in enumerate(BLOCKS):
        block = byname.get(name)
        if block is None:
            print(f"# {name}: not in catalog, skipped", file=sys.stderr)
            continue
        x0 = i * (CELL_W + GAP)
        courses = max(1, round(WALL_TILES / block.size_y))

        for dz in range(CELL_D):
            for dx in range(CELL_W):
                out.append(place_tile(grass, x0 + dx, dz, -grass.size_y))
        for t in range(i + 1):
            out.append(place_tile(tally, x0 + t, 0, 0.0))

        def stack(cx: int, cz: int) -> None:
            for level in range(courses):
                out.append(place_tile(block, x0 + cx, cz, level * block.size_y))

        stack(2, 2)                                  # one block, alone
        for cx in range(1, 1 + RUN):
            stack(cx, 5)                             # a run one cell thick
        for cx in range(1, 1 + RUN):
            for cz in (8, 9):
                stack(cx, cz)                        # a mass two cells thick

        print(f"# {i + 1}: {name}  "
              f"({block.size_x:.2f}x{block.size_y:.2f}x{block.size_z:.2f}), "
              f"{courses} courses", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
