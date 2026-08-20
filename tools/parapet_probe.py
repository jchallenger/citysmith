"""Show every candidate parapet piece crowning a real stair-stepped rampart.

The mass was fixed by `wall_probe.py`; this is what stands on top of it. The
role was pinned to `castle merlon 1x1` on the strength of its group tag, and on
the board it turns out to be a *wooden hoarding* -- a boarded gallery, not a
crenellation -- laid one per outward-facing cell with `place_tile`. On a
stair-stepped circuit that leaves a row of separate wooden boxes, each hanging
over the corner of the step below it.

Two things are being asked here at once, so the probe answers both:

  * which piece reads as stone battlements
  * whether it wants the whole cell (`place_tile`) or the outward edge of it
    (`place_wall`), which is what a parapet actually is -- a thin thing on the
    lip, with room to walk behind it

Placement mode is derived, not declared: a piece thinner than a cell on one
axis is a curtain piece and goes on the edge. Each candidate crowns an
identical section of rampart -- two cells thick, two courses, stepped like the
raster circuit -- with the wall-walk paved behind.

    python tools/parapet_probe.py > out/parapetprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (_normalized_whole_tiles, is_curtain_piece,
                             place_tile, place_wall)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Candidates, by catalog name. The control is first.
CAPS = [
    "castle merlon 1x1",
    "Castle Ruins Crenellation - Small",
    "castle merlon 1x1 corner out",
    "castle merlon 1x1 stair L",
    "Palisade wall top 1",
    "castle merlon 1x1 edge",
]

COLS = 3
THICK = 2          #: cells through the wall
COURSES = 2
STEPS = 5
CELL_W, CELL_D = 9, 10
GAP = 1


def section() -> list[tuple[int, int]]:
    """A stair-stepped run, and which cells of it face out.

    Returns the mass; the outward ring is the low-x side of each step, which is
    where a parapet belongs.
    """
    cells: set[tuple[int, int]] = set()
    for step in range(STEPS):
        for t in range(THICK):
            cells.add((step + t, step))
    return sorted(cells)


def outward(cells: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """Outer cells of the run, and which side of each looks out of town.

    "Out" is west/north here: the low corner of the stair. A real circuit finds
    this by flooding the map from its border; the probe only needs a consistent
    outside so the pieces all face the same way.
    """
    mass = set(cells)
    facing: dict[tuple[int, int], str] = {}
    for (x, z) in cells:
        if (x - 1, z) not in mass:
            facing[(x, z)] = "w"
        elif (x, z - 1) not in mass:
            facing[(x, z)] = "n"
    return facing


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)
    grass = palette.require("ground")
    core = palette.require("city_wall_core")
    walk = palette.require("city_wall_walk")
    tally = byname["castle merlon 1x1 filler"]

    cells = section()
    faces = outward(cells)
    out = []

    for i, name in enumerate(CAPS):
        cap = byname.get(name)
        if cap is None:
            print(f"# {name}: not in catalog, skipped", file=sys.stderr)
            continue
        x0 = (i % COLS) * (CELL_W + GAP)
        z0 = (i // COLS) * (CELL_D + GAP)

        for dz in range(CELL_D):
            for dx in range(CELL_W):
                out.append(place_tile(grass, x0 + dx, z0 + dz, -grass.size_y))
        for t in range(i + 1):
            out.append(place_tile(tally, x0 + t, z0, 0.0))

        crown = COURSES * core.size_y
        for (cx, cz) in cells:
            x, z = x0 + cx + 1, z0 + cz + 2
            for level in range(COURSES):
                out.append(place_tile(core, x, z, level * core.size_y))
            side = faces.get((cx, cz))
            if side is None:
                out.append(place_tile(walk, x, z, crown))
                continue
            # A piece thinner than its cell is a curtain piece: it belongs on
            # the lip, with the walk paved behind it. A full-cell piece takes
            # the cell, which is what the wall does today.
            if is_curtain_piece(cap):
                out.append(place_tile(walk, x, z, crown))
                out.append(place_wall(cap, x, z, side, crown + walk.size_y))
            else:
                out.append(place_tile(cap, x, z, crown))

        print(f"# {i + 1}: {name}  "
              f"({cap.size_x:.2f}x{cap.size_y:.2f}x{cap.size_z:.2f})"
              f"{'  [edge]' if is_curtain_piece(cap) else '  [cell]'}",
              file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
