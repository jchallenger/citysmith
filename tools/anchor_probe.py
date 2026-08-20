"""Settle what TaleSpire anchors a pasted slab on: stored coordinates, or volume.

Multi-chunk boards line up only because every chunk carries a registration
marker at the map's minimum corner, which is supposed to give them all one
bounding box origin. That reasoning assumes the anchor is the *stored
coordinate* min. A prop stores its collider centre, so a tree near a chunk's
low corner pushes the chunk's occupied volume out past the marker -- and if the
anchor is the volume instead, that chunk pastes offset from its neighbours.

The probe makes the game answer. Two pads of identical extent, in two different
materials, at identical stored coordinates. One of them also carries a pine
crown whose collider overhangs the pad by a tile in -x and -z. Paste both at
the *same* cursor cell, select the result, copy it back out, and decode:

  pads coincide           -> the anchor is the stored coordinate min
  gravel pad offset ~+1.05 -> the anchor is the occupied volume

    python tools/anchor_probe.py            # -> out/anchorA.slab.txt, out/anchorB.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import place_centered, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Big enough to read on screen, small enough to paste twice without thinking.
PAD = 5

#: The overhanging prop sits at this centre, so its collider -- 2.55 wide,
#: stored by its centre -- reaches x = z = -1.05 while its stored coordinate
#: stays non-negative, which is what the encoder requires.
OVERHANG_AT = 0.225

#: Clear of the pad, so the prop cannot be what lands on anything.
PROP_Y = 2.0


def pad(asset, y: float = 0.0) -> list:
    return [place_tile(asset, x, z, y)
            for z in range(PAD) for x in range(PAD)]


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    plain = palette.require("ground")        # Grass 1x1
    marked = palette.require("lane")         # gravel, unmistakable against grass
    crown = palette.require("tree_conifer_crown")

    a = Slab(pad(plain))
    b = Slab(pad(marked) + [place_centered(crown, OVERHANG_AT, OVERHANG_AT, PROP_Y, 0)])

    for stem, slab in (("anchorA", a), ("anchorB", b)):
        (mx, my, mz), _ = slab.bounds()
        with open(f"out/{stem}.slab.txt", "w") as fh:
            fh.write(encode(slab) + "\n")
        print(f"{stem}: {len(slab.placements):3d} placements  "
              f"stored min=({mx:.3f}, {my:.3f}, {mz:.3f})", file=sys.stderr)

    print(f"pine crown volume reaches x = {OVERHANG_AT - crown.size_x / 2:.3f}, "
          f"z = {OVERHANG_AT - crown.size_z / 2:.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
