"""Emit a slab that shows every roof-kit piece at every quarter turn.

The Village Roof kit has to be assembled by hand -- eaves, ridge, gable ends --
and nothing in the asset index says which way a piece faces at rotation 0. The
wall convention was settled by making the game its own oracle; this does the
same for roofs, in one paste instead of a rebuild per guess.

Layout: one row per kit piece, one column per quarter turn, each piece sitting
on its own floor pad. A stub wall runs along the north edge of every pad, so
north is unambiguous in a screenshot no matter how the camera is turned.

    python tools/roof_probe.py > out/roofprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import ROT_E, ROT_N, ROT_S, ROT_W, place_tile, place_wall
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab

#: The kit, in the order a roof is assembled from it.
ROLES = [
    "roof",
    "roof_side",
    "roof_corner",
    "roof_inner_corner",
    "roof_gable",
    "roof_gable_window",
    "roof_chimney",
]

ROTS = [("N", ROT_N), ("E", ROT_E), ("S", ROT_S), ("W", ROT_W)]

#: Pads are 3 apart so neighbouring pieces cannot be mistaken for each other.
PITCH = 3


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    floor = palette.require("ground")
    marker = palette.require("wall")

    placements = []
    for row, role in enumerate(ROLES):
        asset = palette.resolve(role)
        if asset is None:
            print(f"# {role}: unresolved, skipped", file=sys.stderr)
            continue
        for col, (label, rot) in enumerate(ROTS):
            x, z = col * PITCH, row * PITCH
            # Floor pad, then the piece on top of it, then a north marker.
            placements.append(place_tile(floor, x, z, 0.0))
            placements.append(place_tile(asset, x, z, floor.size_y, rot))
            placements.append(place_wall(marker, x, z - 1, "n", 0.0))
        print(f"# row {row}: {role:18s} -> {asset.name}", file=sys.stderr)

    slab = Slab(placements).normalized()
    print(f"# {len(placements)} placements, {len(ROLES)} roles x {len(ROTS)} rotations",
          file=sys.stderr)
    print(slab.encode())


if __name__ == "__main__":
    main()
