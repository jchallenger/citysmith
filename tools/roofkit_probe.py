"""Read each roof kit's own convention, instead of assuming the Thatched one.

`_lay_roofs` stacks a hip as concentric rings -- each course one cell in and one
piece-height up, corners taking the corner piece -- with rotations read out of a
real community-built cottage (`library/cabin/small-forest-cottage`)::

    edges    N=6   E=0   S=18  W=12
    corners  NW=12 NE=6  SW=18 SE=0

Those rotations are **the Thatched kit's**, and nothing has ever checked that
another kit shares them. `tools/facade_probe.py` shows what happens when you
assume: the Village tiled roof and the Castle roof both come out as a rank of
fins with daylight between them, while the Thatched hip beside them is clean.
All three slope pieces measure exactly 1.0 x 1.0 x 1.0, so the catalog cannot
tell them apart -- the same trap as `md_wall_1x1_diag_01`, which measures like a
block and is a blade.

Rotation is about Y, so it cannot tip a slope upright: if a piece reads as a fin
at every rotation then it is not a hip course at all and the kit needs a
different piece (or has none). This probe separates those two cases, which is
the thing guesswork cannot do.

Three bands per kit, north at the top of the board:

    A  the slope piece alone, at rot 0 / 6 / 12 / 18, each on its own pedestal
       with a marker block off its NORTH side. Read which way it falls.
    B  the corner piece, same four rotations, same marker.
    C  a 6x6 hip built with the Thatched convention -- the control, and for
       Thatch itself the known-good.

Kits are numbered in a stack of blocks at the west end of each band:
1 Thatched (control), 2 Village, 3 Castle, 4 Haunted.

Read A and B from directly overhead first -- a slope's fall direction is
unambiguous in plan and ambiguous at every oblique -- then drop to a low
oblique for C, which is where a gap between courses shows.

    python tools/roofkit_probe.py > out/roofkitprobe.slab.txt
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    SIDE_OFFSETS, _is_reflex, _normalized_whole_tiles, _roof_piece,
    _roof_rings, place_tile,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: kit -> (slope, outside corner, inner corner, flat cap). Every piece here is
#: 1x1 by the catalog; whether any of them is a *hip course* is the question.
KITS = {
    "thatched": ("Thatched Roof 01", "Thatched Roof Corner 01",
                 "Thatched Roof Inner Corner 01", "Thatched roof flat 01"),
    "village": ("Village Roof Side 01", "Village Roof Corner 01",
                "Village Roof Inner Corner 01", "Tavern Roof flat 01"),
    "castle": ("Regular 1x1", "Skirt_1x1_corner out",
               "Skirt_1x1_corner in", "Top 1x1 flat"),
    "haunted": ("Haunted roof 1x1", "haunted roof corner out tip",
                "haunted roof corner inner tip", "haunted roof 1x1 flat"),
}

ROTS = (0, 6, 12, 18)
HIP_W = HIP_D = 6

#: Bands run north to south; each kit gets its own strip of board.
BAND_GAP = 4
KIT_PITCH = 22


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    # **Castle and Haunted are both dark weathered timber**, and told apart on
    # one board only by counting a stack of blocks at a grazing angle -- which
    # is the reading error this project has made twice. Run one dark kit at a
    # time against the tan Thatched control and identification is a colour, not
    # a count.
    ap.add_argument("--kits", default=",".join(KITS),
                    help="comma-separated subset of " + ", ".join(KITS))
    args = ap.parse_args()
    chosen = [k.strip() for k in args.kits.split(",") if k.strip()]
    unknown = [k for k in chosen if k not in KITS]
    if unknown:
        ap.error(f"unknown kit(s) {', '.join(unknown)}; have {', '.join(KITS)}")

    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    marker = byname.get("md_stairblock_01") or palette.require("floor")
    pedestal = palette.require("floor")

    out: list = []

    def pad(x0: int, z0: int, w: int, d: int) -> None:
        for dz in range(d):
            for dx in range(w):
                out.append(place_tile(grass, x0 + dx, z0 + dz, -grass.size_y))

    for k, kit in enumerate(chosen):
        names = KITS[kit]
        ox = k * KIT_PITCH
        slope, corner, inner, cap = (byname.get(n) for n in names)
        missing = [n for n, a in zip(names, (slope, corner, inner, cap)) if a is None]
        if missing:
            print(f"# {kit}: missing {', '.join(missing)}", file=sys.stderr)

        pad(ox - 2, -2, KIT_PITCH - 2, HIP_D + BAND_GAP * 2 + 8)

        # The kit's number, in a stack at its west end -- a row of blocks on
        # grass vanishes at the oblique band C has to be read from.
        for t in range(k + 1):
            out.append(place_tile(marker, ox - 2, -2, t * marker.size_y))

        # Band A: the slope alone. Each piece sits on a one-tile pedestal with
        # a marker block to its NORTH, so "which way does it fall" has an
        # answer that does not depend on where the camera is.
        for i, rot in enumerate(ROTS):
            x = ox + i * 3
            out.append(place_tile(pedestal, x, 0, 0.0))
            out.append(place_tile(marker, x, -1, 0.0))
            if slope is not None:
                out.append(place_tile(slope, x, 0, pedestal.size_y, rot))

        # Band B: the outside corner, same four rotations.
        bz = BAND_GAP
        for i, rot in enumerate(ROTS):
            x = ox + i * 3
            out.append(place_tile(pedestal, x, bz, 0.0))
            out.append(place_tile(marker, x, bz - 1, 0.0))
            if corner is not None:
                out.append(place_tile(corner, x, bz, pedestal.size_y, rot))

        # Band C: a hip built the way the generator builds one, from this
        # kit's pieces on the Thatched convention. For Thatch this is the
        # known-good; for the others it is the failure, in the exact shape
        # the town shows it.
        cz = BAND_GAP * 2 + 2
        cells = {(x, z) for x in range(HIP_W) for z in range(HIP_D)}
        for x, z in sorted(cells):
            out.append(place_tile(pedestal, ox + x, cz + z, 0.0))
        rings = _roof_rings(cells)
        rise = slope.size_y if slope is not None else 1.0
        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, slope, corner, cap, inner,
                                     _is_reflex(rings, x, z, fall))
            if piece is not None:
                out.append(place_tile(piece, ox + x, cz + z,
                                      pedestal.size_y + r * rise, rot))

        shapes = "  ".join(
            f"{n}={a.size_x:g}x{a.size_y:g}x{a.size_z:g}"
            for n, a in zip(("slope", "corner"), (slope, corner)) if a is not None)
        print(f"# {k + 1}: {kit:9s} {shapes}", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
