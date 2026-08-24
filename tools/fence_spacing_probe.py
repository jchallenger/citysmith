"""Does TaleSpire drop a prop on its bounding box, or on its real collider?

This gates the whole fence design and cannot be answered from the files.

`Scatter` and `verify._prop_collisions` both test **axis-aligned bounding
boxes**, because that is what the catalog gives. A 1.98 x 0.43 fence panel
turned off-axis has an AABB much larger than the panel -- 1.70 x 1.70 at 45
degrees -- so two panels butted end to end along a diagonal overlap as boxes
(+0.29 on both axes) while their meshes are disjoint. Since 97-100% of surveyed
fence lines run off-axis, the two possible answers are very different maps:

  * **oriented collider** -- butt-jointed runs arrive whole, the checks are
    merely pessimistic about fences, and nothing needs to change.
  * **bounding box** -- every second panel of every diagonal fence is dropped
    on paste, silently, and a run has to be spaced ~2.41 tiles instead of 2.00,
    which leaves a visible gap between 1.98-long pieces.

So: five blocks on one pad, each a run of eight panels, each labelled on the
ground by a bar of N cells running east of it. Count the panels standing in
each block against the count printed below.

    row 1   45 deg, butt-jointed at 2.00   <- the question
    row 2   45 deg, spaced 2.20
    row 3   45 deg, spaced 2.41            <- boxes are disjoint here
    row 4   15 deg, butt-jointed at 2.00
    row 5    0 deg, butt-jointed at 2.00   <- control, must be whole

If row 1 is whole, the collider is oriented and the design in `docs/fencing.md`
stands as written. If row 1 is gappy and row 3 is whole, the test is on the box
and the run has to be spaced.

**Read it from overhead.** A gap in a diagonal run is invisible end-on -- the
panel behind covers it -- which is the same trap that cost this project three
wall picks. The label bars run east so they read in plan.

    python tools/fence_spacing_probe.py > out/fencespacing.slab.txt
"""

from __future__ import annotations

import argparse
import math
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    FENCE_MODULE,
    bearing_rot,
    place_centered,
    place_tile,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: (label, bearing in degrees, centre-to-centre spacing in tiles)
ROWS = [
    (1, 45.0, 2.00),
    (2, 45.0, 2.20),
    (3, 45.0, 2.41),
    (4, 15.0, 2.00),
    (5, 0.0, 2.00),
]

PANELS = 8          #: panels per run -- enough that a dropped every-other shows
BLOCK = 18          #: tiles per block along x
PAD_MARGIN = 3


def aabb(sx: float, sz: float, deg: float) -> tuple[float, float]:
    t = math.radians(deg)
    return (abs(sx * math.cos(t)) + abs(sz * math.sin(t)),
            abs(sx * math.sin(t)) + abs(sz * math.cos(t)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--panel", default="Stone Wall 01")
    args = ap.parse_args()

    catalog = load_or_build(args.catalog)
    palette = Palette(catalog, MEDIEVAL)
    panel = next((a for a in catalog.assets if a.name == args.panel), None)
    if panel is None:
        raise SystemExit(f"no asset named {args.panel!r}")
    ground = palette.require("ground")
    mark = palette.require("street")

    width = PAD_MARGIN * 2 + BLOCK * len(ROWS)
    depth = PAD_MARGIN * 2 + BLOCK
    placements = []
    for z in range(depth):
        for x in range(width):
            placements.append(place_tile(ground, x, z, 0.0, 0))
    top = ground.size_y

    print(f"panel {panel.name}  {panel.size_x:.2f} x {panel.size_y:.2f} "
          f"x {panel.size_z:.2f}", file=sys.stderr)
    print(f"{'row':>4} {'bearing':>8} {'spacing':>8} {'AABB':>13} "
          f"{'boxes overlap':>14} {'panels':>7}", file=sys.stderr)

    for i, (label, deg, spacing) in enumerate(ROWS):
        ox = PAD_MARGIN + i * BLOCK + 1.0
        oz = PAD_MARGIN + 1.0
        rot = bearing_rot(math.cos(math.radians(deg)), math.sin(math.radians(deg)))
        ux = math.cos(math.radians(deg))
        uz = math.sin(math.radians(deg))
        for n in range(PANELS):
            along = n * spacing
            placements.append(place_centered(
                panel, ox + ux * along + 1.0, oz + uz * along + 1.0, top, rot))
        # Label: a bar of `label` cells running east, at the block's near edge.
        for k in range(label):
            placements.append(place_tile(mark, int(ox) + k, depth - PAD_MARGIN,
                                         0.0, 0))

        ax, az = aabb(panel.size_x, panel.size_z, deg)
        dx, dz = abs(ux * spacing), abs(uz * spacing)
        overlaps = (ax - dx > 1e-9) and (az - dz > 1e-9)
        print(f"{label:>4} {deg:>7.0f}d {spacing:>8.2f} {ax:>6.2f}x{az:<6.2f} "
              f"{'YES' if overlaps else 'no':>14} {PANELS:>7}", file=sys.stderr)

    print(f"\nExpect {PANELS} panels in every block. Count them from OVERHEAD.",
          file=sys.stderr)
    print("A row short of 8 means the drop test is on the bounding box.",
          file=sys.stderr)
    print(encode(Slab(placements)))


if __name__ == "__main__":
    main()
