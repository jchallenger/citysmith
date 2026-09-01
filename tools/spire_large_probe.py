"""The LARGE spire: can the tall family close a spire wider than 4x4?

**The arithmetic says yes, at even multiples of four.** `Tall 2x2x4` rises
4 tiles over 2 deep, so a ring that steps in 2 cells and up 4 keeps the same
plane -- a continuous ~63-degree pyramid, twice as steep as the 45-degree cap.
An 8x8 is one ring of edge and corner pieces ending on the proven 4x4 cap:
8 tiles (40 ft) of spire on a 40 ft base. A 6x6 is dead before probing, because
its top ring is the 2x2 peak the needle sweep already killed at every offset.

**The corners are known; the EDGES are the measurement.** The corner piece and
its rotations are settled by the hand-build (corner table + 0). The straight
slope `Tall 2x2x4` has never been laid at this scale, and the kit's own record
warns both ways: the ordinary Castle roof takes edge +6, its WIDE family takes
edge +6 too -- but the tall family already broke one such assumption (the
needle), so the offset is swept, one bay per quarter turn, with the 4x4 cap as
the control bay. Exactly one bay should close; the other three should show the
fins that a wrong turn always shows.

    1  control    the 4x4 cap alone (hand-build rotations)
    2  large+0    8x8, edge offset 0
    3  large+6    8x8, edge offset 6   <- the wide-family hypothesis
    4  large+12   8x8, edge offset 12
    5  large+18   8x8, edge offset 18

    python tools/spire_large_probe.py > out/spire/large.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (  # noqa: E402
    Builder, ROOF_CORNER_ROT, ROOF_EDGE_ROT, place_tile,
)
from citysmith.catalog import load_or_build  # noqa: E402
from citysmith.palette import Palette  # noqa: E402

GAP = 3
STUB = 2


def cap4(b, byname, ox, oz, y):
    """The proven 4x4 cap: four `Corner out` at the hand-build's rotations."""
    piece = byname["Tall 2x2x4 Corner out"]
    for dx, dz, quad in ((0, 0, "nw"), (2, 0, "ne"),
                         (0, 2, "sw"), (2, 2, "se")):
        b.add(place_tile(piece, ox + dx, oz + dz, y, ROOF_CORNER_ROT[quad]))


def large8(b, byname, ox, oz, y, edge_off):
    """An 8x8 spire: one ring of corners and edges, capped by the 4x4.

    Corners at the hand-build rotations (settled); edge slopes at the edge
    table plus ``edge_off`` (the sweep). Ring 1 is the cap, 4 up, so the
    plane continues to the apex at +8.
    """
    corner = byname["Tall 2x2x4 Corner out"]
    slope = byname["Tall 2x2x4"]
    for dx, dz, quad in ((0, 0, "nw"), (6, 0, "ne"),
                         (0, 6, "sw"), (6, 6, "se")):
        b.add(place_tile(corner, ox + dx, oz + dz, y, ROOF_CORNER_ROT[quad]))
    for side, cells in (("n", ((2, 0), (4, 0))), ("s", ((2, 6), (4, 6))),
                        ("w", ((0, 2), (0, 4))), ("e", ((6, 2), (6, 4)))):
        rot = (ROOF_EDGE_ROT[side] + edge_off) % 24
        for dx, dz in cells:
            b.add(place_tile(slope, ox + dx, oz + dz, y, rot))
    cap4(b, byname, ox + 2, oz + 2, y + slope.size_y)


def main() -> int:
    palette = Palette.named(load_or_build(), "medieval", 33)
    byname = {a.name: a for a in palette.catalog.assets}
    block = palette.require("city_wall_core")
    ground = palette.require("ground")
    b = Builder(palette)

    bays = (("control", 4, None), ("large+0", 8, 0), ("large+6", 8, 6),
            ("large+12", 8, 12), ("large+18", 8, 18))
    width = sum(w for _n, w, _o in bays) + GAP * (len(bays) - 1)
    depth = 8

    with b.layer("landscape"):
        for x in range(-2, width + 2):
            for z in range(-2, depth + 6):
                b.add(place_tile(ground, x, z, 0.0))

    with b.layer("structure"):
        ox = 0
        for n, (name, side, off) in enumerate(bays):
            for x in range(ox, ox + side):
                for z in range(depth - side, depth):
                    for c in range(STUB):
                        b.add(place_tile(block, x, z, 0.5 + c * block.size_y))
            top = 0.5 + STUB * block.size_y
            if off is None:
                cap4(b, byname, ox, depth - side, top)
            else:
                large8(b, byname, ox, depth - side, top, off)
            for k in range(n + 1):
                b.add(place_tile(block, ox + k, depth + 2, 0.5))
            ox += side + GAP

    sys.stderr.write("bays: %s\n" % ", ".join(
        "%d=%s" % (i + 1, n) for i, (n, _w, _o) in enumerate(bays)))
    sys.stderr.write("board %dx%d\n" % (width, depth + 6))
    print(b.to_slab().encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
