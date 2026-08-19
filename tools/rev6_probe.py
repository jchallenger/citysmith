"""Probe for rev 6: roof corner facing, and cottage-window candidates.

Two unknowns, one paste:
  Row 0 - roof_corner at all four quarter turns, on a pad, north marker behind.
  Row 1 - candidate windows placed as WALLS on a cell's north edge, side by
          side: castle window (current), village gable-window, plain wall.
"""
from __future__ import annotations
import sys
sys.path.insert(0, ".")

from citysmith.build import ROT_E, ROT_N, ROT_S, ROT_W, place_tile, place_wall
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab

cat = load_or_build()
pal = Palette(cat, MEDIEVAL)
ground = pal.require("ground")
wall = pal.require("wall")

P = []
# Row 0: roof corner, four facings, each on its own pad.
corner = pal.resolve("roof_corner")
for col, rot in enumerate((ROT_N, ROT_E, ROT_S, ROT_W)):
    x = col * 3
    P.append(place_tile(ground, x, 0, 0.0))
    P.append(place_tile(corner, x, 0, ground.size_y, rot))
    P.append(place_wall(wall, x, -1, "n", 0.0))

# Row 1: window candidates as wall segments on the north edge of a floor cell.
cands = [
    cat.find(name="castle wall 1x1 window")[0],
    cat.find(name="Village Roof Side Wall With Window 01")[0],
    cat.find(name="Village Roof Side Wall 01")[0],
    wall,
]
for col, asset in enumerate(cands):
    x = col * 3
    P.append(place_tile(ground, x, 5, 0.0))
    P.append(place_wall(asset, x, 5, "n", ground.size_y))
    print(f"# col {col}: {asset.name}  {asset.size_x}x{asset.size_y}x{asset.size_z}",
          file=sys.stderr)

print(Slab(P).normalized().encode())
