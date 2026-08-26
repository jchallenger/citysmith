"""Measure whether a boundary strategy actually SEALS, instead of looking at it.

Three rounds of judging the palisade from screenshots produced three wrong
answers -- corner bundles read as a material choice, a see-through circuit
read as solid, and a staircase read as fixed. `CLAUDE.md` states the rule this
file applies instead: decode the geometry and measure it.

The question a barricade has to answer is exactly one thing: **standing
outside it, can you see through to the inside?** That is a ray test, and it
does not care how the pieces are arranged.

For each strategy this walks the design line, and at every sample throws a
short ray straight across it at mid-wall height. A sample is SEALED when the
ray meets solid geometry somewhere in the band. The score is the share of
samples sealed; a wall that reads solid scores 100%.

    python tools/fence_seal.py
"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")

from citysmith import raster as R
from citysmith.build import (place_centered, place_tile, place_wall,
                             run_along_polyline)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.verify import _Occupancy

CELL = 20
GRADE = 0.5
LINE = [(2.5, 3.5), (6.5, 3.5), (13.5, 10.5), (17.5, 10.5)]
#: Where across the wall the ray is thrown, in tiles either side of the line.
REACH = 1.6
#: How far along the line between samples. Fine enough to fall in a slit.
STEP = 0.05

cat = load_or_build()
pal = Palette(cat, MEDIEVAL, 33)
by_name = {a.name: a for a in cat.assets}
PALISADE = by_name["Palisade wall tall 1x2"]
WIDE = by_name["Palisade wall tall 2x2"]


class Bag:
    """Just enough of a Builder for `_Occupancy` to read."""

    def __init__(self):
        self.placements = []
        self.palette = pal


def cells_of(width=1.0):
    return sorted(set(R._stroke_line(LINE, width, CELL, CELL)))


def dominant(cs, x, z):
    sx = sum(1 for d in (-2, -1, 1, 2) if (x + d, z) in cs)
    sz = sum(1 for d in (-2, -1, 1, 2) if (x, z + d) in cs)
    return sx >= sz


def blocks(bag, cells, piece):
    cs = set(cells)
    for x, z in sorted(cs):
        bag.placements.append(place_tile(piece, x, z, GRADE,
                                         0 if dominant(cs, x, z) else 6))


def curtain(bag, cells, piece):
    cs = set(cells)
    for x, z in sorted(cs):
        for side, dx, dz in (("n", 0, -1), ("s", 0, 1), ("w", -1, 0), ("e", 1, 0)):
            if (x + dx, z + dz) not in cs:
                bag.placements.append(place_wall(piece, x, z, side, GRADE))


def surveyed(bag, piece):
    panels, _ = run_along_polyline(list(LINE))
    for cx, cz, rot in panels:
        bag.placements.append(place_centered(piece, cx, cz, GRADE, rot))


def samples():
    """Points along the design line, with the unit normal at each."""
    out = []
    for a, b in zip(LINE, LINE[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        ux, uz = dx / length, dz / length
        nx, nz = -uz, ux
        n = int(length / STEP)
        for i in range(n):
            t = i * STEP
            out.append((a[0] + ux * t, a[1] + uz * t, nx, nz))
    return out


def seal(bag) -> float:
    occ = _Occupancy(bag, GRADE + 1.0)          # mid-wall on a 2.0 piece
    pts = samples()
    hit = 0
    for px, pz, nx, nz in pts:
        d = -REACH
        while d <= REACH:
            if occ.solid_at(px + nx * d, pz + nz * d):
                hit += 1
                break
            d += 0.05
    return 100.0 * hit / len(pts)


thin = set(cells_of(1.0))
closed = thin       # `_close_diagonals` is retired; see build._bearing_rot

CASES = [
    ("1  blocks, no connectors", lambda g: blocks(g, thin, PALISADE)),
    ("2  blocks + connectors  (SHIPPED)", lambda g: blocks(g, closed, PALISADE)),
    ("3  two cells thick", lambda g: blocks(g, cells_of(2.0), PALISADE)),
    ("4  curtain on the outward face", lambda g: curtain(g, closed, PALISADE)),
    ("5  blocks + connectors, tall 2x2", lambda g: blocks(g, closed, WIDE)),
    ("6  along the surveyed bearing", lambda g: surveyed(g, PALISADE)),
]

print(f"sealed %, sampled every {STEP} tiles along the line, "
      f"ray +-{REACH} tiles across, at mid-wall height")
print()
for label, fn in CASES:
    bag = Bag()
    fn(bag)
    print(f"  {seal(bag):6.1f}%   {len(bag.placements):4} pieces   {label}")
