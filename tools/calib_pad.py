"""A 24x24 pad with its NW corner blocked, for calibrating the paste step.

Tiling a map means pasting chunks side by side, and the step from one paste
to the next is measured in *screen pixels*: the cursor moves, the slab moves
with it, one for one. So the one number the procedure needs is pixels per
tile, and the honest way to get it is to paste a pad of known size and
measure it, not to derive it from the camera.

Two things this pad is shaped to show. The corner block says which corner is
which, so a pad is never read a tile out. And pasting two of them a measured
step apart says immediately whether the step is right: they abut with no seam
when it is, and show a gap or an overlap when it is not.

    python tools/calib_pad.py > out/calib24.slab.txt

Measured with it: the step is exact and linear along one screen row, and
*not* between rows -- the camera keeps some perspective even pitched straight
down. Paste a row at one screen Y before moving. See CLAUDE.md, "Tiling".
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

SIDE = 24


def main() -> None:
    pal = Palette.named(load_or_build(), "medieval", 33)
    cobble = pal.require("street")
    block = pal.require("city_wall_core")
    out = []
    for z in range(SIDE):
        for x in range(SIDE):
            out.append(place_tile(cobble, x, z, 0.0))
    for x, z in ((0, 0), (1, 0), (0, 1)):
        out.append(place_tile(block, x, z, cobble.size_y))
    byid = {a.id: a for a in pal.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {SIDE}x{SIDE} pad, NW corner blocked", file=sys.stderr)


if __name__ == "__main__":
    main()
