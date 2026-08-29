"""A street of common houses, built through the shipped town path.

Not a swatch and not a kit board: this asks the question a swatch cannot,
which is whether a STREET reads as varied. `walls.TIER_FABRICS` deals a whole
fabric per building, so the thing to look at is several houses beside each
other -- one Tavern with its 2-cell bays and plinth, one Village with 1-cell
bays and no plinth, one Abandoned Village at the poor end, and whatever the
deal actually gives.

It goes through `build_from_tilemap`, so what is on the board is what a town
gets. A probe that reimplements the thing it is probing can only tell you
about the probe.

**Sized to be photographable.** The camera stops pulling back at 49.75 tiles
of slant range, which is about 50x27 tiles in one shot -- so five houses at a
pitch of 8 is 40 wide and fits, where the wall-kit boards built earlier in the
same week needed 95 tiles of range and could never be seen whole. Check with
`camera_aim.py --slab` before pasting; `panel_review.ps1` now does.

    python tools/fabric_probe.py > out/fabricprobe.slab.txt
    .\\tools\\panel_review.ps1 -Slab out\\fabricprobe.slab.txt -Name fabric `
                              -Board "PROBE fabric street" -Height 130 -Oblique 210
"""

from __future__ import annotations

import argparse
import sys
import zlib

sys.path.insert(0, ".")

from citysmith import walls as W
from citysmith.build import build_from_tilemap, tier_of
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.raster import FLOOR, STREET, TileMap, _find_perimeters, _place_doors
from citysmith.slab import encode

HOUSES = 4
W_, D_ = 6, 5
PITCH = 8
STOREYS = 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--houses", type=int, default=HOUSES)
    ap.add_argument("--storeys", type=int, default=STOREYS)
    ap.add_argument("--kind", default="house")
    # **Which houses, not a seed.** The fabric is dealt from a hash of the
    # building id, so the way to show all three on one small board is to pick
    # a run of ids the deal happens to spread -- 13..16 is the first. Forcing
    # a fabric would make the probe a picture of the probe.
    ap.add_argument("--first", type=int, default=13)
    args = ap.parse_args()

    width = args.houses * PITCH + 4
    tm = TileMap.blank(width, D_ + 10)
    # A street along the south, so every house has a front and the glazing
    # rule has something to be dense about.
    for x in range(width):
        for z in range(D_ + 3, D_ + 6):
            tm.surface[z][x] = STREET
            tm.street_class[z][x] = "main"

    for i in range(args.houses):
        bid = f"{args.kind}-{args.first + i:04d}"
        bx = 2 + i * PITCH
        for x in range(bx, bx + W_):
            for z in range(3, 3 + D_):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        tm.floors[bid] = args.storeys
    _find_perimeters(tm, None)
    _place_doors(tm, None)

    palette = Palette(load_or_build(), MEDIEVAL)
    b = build_from_tilemap(tm, palette, storeys=args.storeys)

    fams = W.families(palette.catalog)
    for i in range(args.houses):
        bid = f"{args.kind}-{args.first + i:04d}"
        fab = W.fabric_for(tier_of(bid), zlib.crc32(bid.encode()), fams)
        print(f"  house {i}: {bid:16s} fabric={fab.kit if fab else '(palette)'}",
              file=sys.stderr)

    # One slab, because a probe you have to paste twice is a probe whose two
    # halves can disagree about where they landed.
    from citysmith.build import _normalized_whole_tiles
    from citysmith.slab import Slab
    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(b.placements), byid)))
    print(f"# {len(b.placements)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
