"""Hand-author a small board from one sentence, through the real pipeline.

    "build a spooky forest, with only 3-4 buildings. one should have a tower,
     and on the edge of the map there should be a river beyond a high wall."

**Nothing in citysmith turns that sentence into a board today, and this is not
that either.** `slabchat` is scoped to one building and clamps anything larger;
`citysmith city` lays a BSP street grid, which a forest clearing is not; and
`build` needs a `Layout`, which normally comes from an MFCG or FTG import. So
the sentence is translated here, by hand, into the `Layout` the pipeline
already takes -- which is the same division of labour CLAUDE.md states for
Claude itself: language in, generator parameters out, and the geometry stays in
Python.

Everything the sentence asks for maps onto a field that already exists:

    spooky forest      LayoutArea("forest"), which becomes `TileMap.forest`,
                       the mask the canopy scatter reads to close a wood
    3-4 buildings      four, in a clearing the forest rectangles leave open
    one with a tower   `build.TOWER_MIN_TILES` is 60 with an aspect of 2.5,
                       so the chapel is 6x20 -- 120 tiles at 3.33. Nothing
                       else on the board qualifies, which is the point
    a high wall        `Layout.walls`, a closed circuit; a long thin ring
                       reads as a straight rampart, with one gate in it
    a river beyond it  `LayoutRoad(kind="river")`, east of the wall, running
                       off both edges of the map

Coordinates are tiles: `rasterize` takes `ceil(layout.width)` directly, so a
layout unit is a cell and a cell is 5 ft.

    python tools/spooky_forest.py --out out/spooky
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith.layout import Layout, LayoutArea, LayoutBuilding, LayoutRoad

#: The board, in tiles. 112 x 84 is 560 x 420 ft -- big enough for a wood to
#: read as one and small enough to walk round in a review.
WIDTH, DEPTH = 112, 84

#: The wall stands this far in from the east edge, and the river beyond it.
#: The gap matters: a rampart with its back to the map edge has no outside.
WALL_X = 86
RIVER_X = 100


def rect(x0: float, z0: float, x1: float, z1: float) -> list[tuple[float, float]]:
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def build_layout() -> Layout:
    lay = Layout(name="Gallowsmere", source="hand",
                 units_per_tile=1.0, feet_per_unit=5.0,
                 width=float(WIDTH), depth=float(DEPTH))
    lay.scale_basis = "authored: 1 unit = 1 tile"

    # **The wood is four rectangles, not one with a hole in it.** A LayoutArea
    # is a simple ring and carries no holes, so the clearing is the space the
    # four leave between them rather than something subtracted.
    lay.areas = [
        LayoutArea("forest", rect(1, 1, 28, DEPTH - 1)),          # west
        LayoutArea("forest", rect(28, 1, WALL_X - 2, 16)),        # north
        LayoutArea("forest", rect(28, DEPTH - 17, WALL_X - 2, DEPTH - 1)),
        LayoutArea("forest", rect(70, 16, WALL_X - 2, DEPTH - 17)),
    ]

    # The chapel earns the tower on its proportions alone: 6 x 20 is 120 tiles
    # at an aspect of 3.33, against a gate of 60 and 2.5.
    lay.buildings = [
        LayoutBuilding("temple-0001", rect(40, 26, 46, 46), kind="temple",
                       floors=2, stone=True, name="The Drowned Chapel"),
        LayoutBuilding("house-0002", rect(53, 24, 61, 32), kind="house",
                       floors=2, name="Sexton's Lodging"),
        LayoutBuilding("house-0003", rect(54, 46, 62, 54), kind="house",
                       floors=1, name="The Bier House"),
        LayoutBuilding("house-0004", rect(30, 40, 37, 48), kind="house",
                       floors=1, name="Woodcutter's Croft"),
    ]

    # A straight rampart: a long thin closed ring, thickened by
    # `wall_thickness`. High is what `build` already does with a circuit --
    # curtain, wall-walk, merlons and a stair per tower.
    lay.walls = [rect(WALL_X, 4, WALL_X + 2, DEPTH - 4)]
    lay.wall_thickness = 4.0
    lay.gates = [(float(WALL_X + 1), DEPTH / 2.0)]

    # The river runs off both edges, so it reads as passing through rather
    # than as a pond that stops.
    lay.roads = [
        LayoutRoad([(RIVER_X - 3, -6), (RIVER_X, 18), (RIVER_X - 2, 44),
                    (RIVER_X + 2, 66), (RIVER_X, DEPTH + 6)],
                   width=9.0, kind="river"),
        # The one made way on the board: chapel door to the gate. A trail, not
        # a street -- nobody lays cobble out here.
        LayoutRoad([(47, 36), (60, 38), (72, 41), (float(WALL_X + 3), DEPTH / 2.0)],
                   width=1.5, kind="trail"),
    ]
    return lay


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out/spooky", help="directory for layout.json")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    lay = build_layout()
    lay.save(out / "layout.json")
    print(f"{lay.name}: {WIDTH}x{DEPTH} tiles, {len(lay.buildings)} buildings, "
          f"{len(lay.areas)} forest blocks, {len(lay.walls)} wall, "
          f"{len(lay.gates)} gate")
    print(f"wrote {out / 'layout.json'}")


if __name__ == "__main__":
    main()
