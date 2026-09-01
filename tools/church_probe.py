"""Every church size the towns actually contain, in a row, on one board.

**The question this answers is "is a big church big".** `CHURCH_BANDS` deals a
nave course count and a tower stage count from the footprint, and a table of
numbers cannot say whether a 102-cell town church reads as a different
building from a 30-cell chapel. Two things about it are only answerable on a
board: whether the tower still stands clear of a ridge that grew with it, and
whether the biggest rung looks like a landmark or like a silo.

The footprints are the REAL ones, measured off the five layouts on disk, not
invented rectangles:

    102  6x19   Forest Church     temple-0002    great
     88  8x12   East Tradebourne  temple-0027    town
     81  10x9   East Tradebourne  temple-0004    town
     65  15x5   Graybank          temple-0123    parish
     52  8x7    Sedgewater        temple-0006    parish
     30  4x9    East Tradebourne  temple-0991    chapel

Built through `build_from_tilemap` itself rather than through a copy of the
church code -- a probe that reimplements what it is probing can only tell you
about the probe, which is the rule `wallkit_board.py` states and this follows.

**Each bay is numbered by a bar of N cells running east**, not by a tally
stack: a vertical stack reads at an oblique and vanishes from overhead, and a
hip is judged in plan. That is the same finding `roofkit_probe.py` records.

    python tools/church_probe.py > out/church/sizes.slab.txt
    python tools/camera_aim.py --slab out/church/sizes.slab.txt --at 0,0,45,0,55
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import build_from_tilemap, church_band  # noqa: E402
from citysmith.catalog import load_or_build  # noqa: E402
from citysmith.palette import MEDIEVAL, Palette  # noqa: E402
from citysmith.raster import (  # noqa: E402
    FLOOR, GROUND, TileMap, _find_perimeters, _place_doors,
)

#: The measured footprints, largest first so the row reads as a ladder.
#: `(cells, width, depth, town, id)` -- cells is what the layout actually had,
#: which is not always `w * d` because a real footprint is not a rectangle.
SIZES: tuple[tuple[int, int, int, str, str], ...] = (
    (102, 6, 19, "Forest Church", "temple-0002"),
    (88, 8, 12, "East Tradebourne", "temple-0027"),
    (81, 10, 9, "East Tradebourne", "temple-0004"),
    (65, 15, 5, "Graybank", "temple-0123"),
    (52, 8, 7, "Sedgewater", "temple-0006"),
    (30, 4, 9, "East Tradebourne", "temple-0991"),
)

GAP = 4       # cells of open ground between bays
MARGIN = 3    # apron round the whole row
BAR_GAP = 2   # cells between a bay and its numbering bar


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--style", default="medieval")
    ap.add_argument("--seed", type=int, default=33)
    ap.add_argument("--storeys", type=int, default=3,
                    help="the HOUSE ceiling. A church is exempt from it by "
                         "design, so this is here to prove that rather than "
                         "to set the church's height.")
    ap.add_argument("--only", default="",
                    help="comma-separated cell counts, to build a subset")
    args = ap.parse_args(argv)

    want = {int(s) for s in args.only.split(",") if s.strip()}
    sizes = [s for s in SIZES if not want or s[0] in want]

    # Lay the bays out west to east, each on its own strip of ground. The
    # numbering bar sits SOUTH of its bay, which is where a plan view can read
    # it and where no roof overhangs it.
    depth = max(d for _c, _w, d, _t, _i in sizes) + BAR_GAP + 2
    width = sum(w for _c, w, _d, _t, _i in sizes) + GAP * (len(sizes) - 1)
    tm = TileMap.blank(width + MARGIN * 2, depth + MARGIN * 2)
    for z in range(tm.depth):
        for x in range(tm.width):
            tm.surface[z][x] = GROUND

    x0 = MARGIN
    for n, (cells, w, d, town, ident) in enumerate(sizes, start=1):
        bid = f"temple-{n:04d}"
        for x in range(x0, x0 + w):
            for z in range(MARGIN, MARGIN + d):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        # The layout's storey count, which the church path is meant to ignore
        # in favour of its band. Set to 1 on purpose: if a bay comes out one
        # course tall, the band is not being consulted.
        tm.floors[bid] = 1
        # The numbering bar: n cells running east, two clear of the bay.
        for i in range(n):
            tm.surface[MARGIN + d + BAR_GAP][x0 + i] = FLOOR
        x0 += w + GAP

    _find_perimeters(tm, None)
    _place_doors(tm, None)

    palette = Palette.named(load_or_build(), args.style, args.seed)
    b = build_from_tilemap(tm, palette, storeys=args.storeys, seed=args.seed,
                           quarters=False)

    print(b.to_slab().encode())

    x0 = MARGIN
    sys.stderr.write("bay  cells  plan    band                courses  stages  "
                     "eaves  tower\n")
    for n, (cells, w, d, town, ident) in enumerate(sizes, start=1):
        courses, stages = church_band(w * d)
        sys.stderr.write(
            "%3d  %5d  %2dx%-3d %-19s %7d  %6d  %4d ft %5d ft   %s %s\n"
            % (n, w * d, w, d,
               ("great" if w * d >= 100 else "town" if w * d >= 70
                else "parish" if w * d >= 40 else "chapel"),
               courses, stages, courses * 10, (courses + stages) * 10,
               town, ident))
        x0 += w + GAP
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
