"""Probe the round-tower kit and the bridge-deck candidates, side by side.

Two things the generator is about to build for the first time, and both rest
on shape assumptions nothing in the catalog data can confirm:

* **Towers.** ``md_tower_wall_01`` measures 4x2x4. That is consistent with a
  hollow ring course of a round tower -- or with a solid drum, or with a
  three-quarter shell. Whether a floor disc and the crenellated ring can share
  the top level, and whether the kit wants a base disc, are the same kind of
  question. Four stacks, each answering one of them, plus a run of rampart
  blocks butted against a tower to see the square-meets-round junction.

* **Bridge decks.** The MFCG planks are a cobble strip at grade with a tile
  of air under it. The candidates here are laid across one channel, each as
  a one-wide strip with the water column continuous beneath it, so what
  shows is the deck's own underside and how the rail sits on it.

    python tools/tower_probe.py > out/towerprobe.slab.txt

Read it the way the wall probes are read: four faces, overhead, eye level,
and the cut box. A stack that looks right from one side has been approved
before and was wrong.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    WATER_SURFACE_DROP, _normalized_whole_tiles, place_tile, place_wall,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

GRADE = 0.5                     #: top of the grass, as on a real board
TABLE_X, TABLE_Z = 44, 24

#: Tower stacks, west to east. Each is a list of (asset name, y offset above
#: the grade) -- the courses are written out so a wrong assumption about one
#: is visible as one wrong stack rather than as a wrong rule.
TOWERS = [
    # A1: four ring courses, floor disc and crenellations sharing the top.
    [("md_tower_wall_01", 0.0), ("md_tower_wall_01", 2.0),
     ("md_tower_wall_01", 4.0), ("md_tower_wall_01", 6.0),
     ("md_tower_floor_01", 8.0), ("md_tower_crenelations_01", 8.0)],
    # A2: the same without the floor -- is the ring hollow?
    [("md_tower_wall_01", 0.0), ("md_tower_wall_01", 2.0),
     ("md_tower_wall_01", 4.0), ("md_tower_wall_01", 6.0),
     ("md_tower_crenelations_01", 8.0)],
    # A3: base disc first, the 02 wall variant, floor then crenellations
    # stacked rather than shared.
    [("md_tower_floor_base_01", 0.0),
     ("md_tower_wall_02", 0.5), ("md_tower_wall_02", 2.5),
     ("md_tower_wall_02", 4.5), ("md_tower_wall_02", 6.5),
     ("md_tower_floor_01", 8.5), ("md_tower_crenelations_01", 9.0)],
    # A4: as A1 with a window course, and a rampart butted against it.
    [("md_tower_wall_01", 0.0), ("md_tower_wall_window_01", 2.0),
     ("md_tower_wall_01", 4.0), ("md_tower_wall_window_01", 6.0),
     ("md_tower_floor_01", 8.0), ("md_tower_crenelations_01", 8.0)],
]
TOWER_Z = 2
TOWER_XS = [2, 10, 18, 26]

CHANNEL_Z = (12, 13, 14)        #: three water cells, bank either side
BRIDGE_XS = {                   #: candidate deck -> column(s)
    "Harbor Middle 06": [3],
    "Harbor Float 01": [9],
    "Harbor Extention 02": [15],
    "Tavern Floor 01": [21],            # thin deck on harbour legs
    "Castle Ruins Wood 1": [27],        # 2x1 plank, laid across
    "md_stairblock_01": [34],           # stone causeway with a parapet
}


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname = {a.name: a for a in palette.catalog.assets if a.kind == "tile"}
    out = []

    def need(name: str):
        a = byname.get(name)
        if a is None:
            print(f"# {name}: not in catalog", file=sys.stderr)
        return a

    grass = palette.require("ground_2x2")
    water = palette.require("water")
    bed = palette.require("riverbed")
    rail = palette.require("quay_rail")
    block = palette.require("city_wall_core")
    walk = palette.require("city_wall_walk")
    cap = palette.require("city_wall_cap")

    # The table, with the channel cut out of it.
    for z in range(0, TABLE_Z, 2):
        for x in range(0, TABLE_X, 2):
            if z in CHANNEL_Z or z + 1 in CHANNEL_Z:
                continue
            out.append(place_tile(grass, x, z, GRADE - grass.size_y))
    # The channel rows that the 2x2 pass skipped but are not water.
    for z in range(TABLE_Z):
        if z in CHANNEL_Z or z % 2 == 0 and z + 1 not in CHANNEL_Z:
            continue
        if z - 1 in CHANNEL_Z or z + 1 in CHANNEL_Z:
            g1 = palette.require("ground")
            for x in range(TABLE_X):
                out.append(place_tile(g1, x, z, GRADE - g1.size_y))

    # North marker: three rampart blocks in the north-west corner.
    for x in range(3):
        out.append(place_tile(block, x, 0, GRADE))

    # Towers.
    for x0, stack in zip(TOWER_XS, TOWERS):
        for name, dy in stack:
            a = need(name)
            if a is not None:
                out.append(place_tile(a, x0, TOWER_Z, GRADE + dy))
    # Rampart run butted against A4's east face: 6 long, 2 thick, 6 tall.
    x0 = TOWER_XS[-1] + 4
    for x in range(x0, x0 + 6):
        for z in (TOWER_Z + 1, TOWER_Z + 2):
            for level in range(6):
                out.append(place_tile(block, x, z, GRADE + level))
            crown = GRADE + 6
            out.append(place_tile(walk, x, z, crown))
            if z == TOWER_Z + 1:
                out.append(place_wall(cap, x, z, "n", crown + walk.size_y))

    # The channel: bed, water column, surface a full tile below the bank.
    surface_under = GRADE - WATER_SURFACE_DROP          # underside of the top tile
    for z in CHANNEL_Z:
        drop = 0.5 if z == CHANNEL_Z[1] else 0.0
        bed_top = surface_under - drop
        for x in range(TABLE_X):
            out.append(place_tile(bed, x, z, bed_top - bed.size_y))
            y = bed_top
            while y <= surface_under + 1e-6:
                out.append(place_tile(water, x, z, y))
                y += water.size_y

    # Bridges, one candidate per column, rails on both long sides.
    for name, cols in BRIDGE_XS.items():
        a = need(name)
        if a is None:
            continue
        for x in cols:
            for z in CHANNEL_Z:
                if name == "Castle Ruins Wood 1":
                    # 2x1 plank laid across the strip: x spans 2, z spans 1.
                    out.append(place_tile(a, x, z, GRADE - a.size_y))
                    sides = [(x, "w"), (x + 1, "e")]
                else:
                    out.append(place_tile(a, x, z, GRADE - a.size_y))
                    sides = [(x, "w"), (x, "e")]
                for sx, side in sides:
                    if name == "md_stairblock_01":
                        out.append(place_wall(cap, sx, z, side, GRADE))
                    else:
                        out.append(place_wall(rail, sx, z, side, GRADE))
                    if name == "Tavern Floor 01":
                        leg = need("Harbor Leg 01")
                        if leg is not None:
                            out.append(place_wall(leg, sx, z, side, GRADE - 1.0))

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
