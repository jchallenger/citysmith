"""Show what a river bed looks like through TaleSpire's water, per material.

The water tile is translucent, so the bed is not hidden -- it is the thing you
look at. A grass bed made the river read as two sheets of turf with a blue film
between them; sand fixed that and then read as a dry wash, because a bright bed
gives the water nothing to tint. Which bed reads as water is a question about
rendering, so the game answers it.

Layout: one column per candidate bed, one row per water depth (1 to 4 tiles of
column). A dry strip of the same bed runs along the front of each column, so
"with water" and "without water" sit side by side.

    python tools/water_probe.py > out/waterprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Candidate beds, by catalog name. All are 1x1 and 0.5 thick.
BEDS = ["Desert Ground 01", "gravel_1x1_01", "Cave Floor - Rock 2",
        "Cave Floor - Rock 03", "Desert stone floor 01", "Grass 1x1"]

DEPTHS = [0, 1, 2, 3, 4]       #: water tiles stacked above the bed
PAD = 3                        #: cells per swatch, so a tile is not read alone
GAP = 1

#: The bank the swatches are cut into, so each reads as a channel rather than
#: as a tile sitting on nothing.
BANK_TOP = 4.0


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname = {a.name: a for a in palette.catalog.assets}
    water = palette.require("water")
    grass = palette.require("ground")

    out = []
    span_x = len(BEDS) * (PAD + GAP)
    span_z = len(DEPTHS) * (PAD + GAP)

    # A grass table with the swatches *cut out* of it. Laying the table across
    # the whole area and dropping the swatches underneath buries them: the
    # first run of this probe showed one unbroken lawn.
    cut = {(col * (PAD + GAP) + dx, row * (PAD + GAP) + dz)
           for col in range(len(BEDS)) for row in range(len(DEPTHS))
           for dx in range(PAD) for dz in range(PAD)}
    for z in range(-2, span_z + 2):
        for x in range(-2, span_x + 2):
            if (x, z) not in cut:
                out.append(place_tile(grass, x, z, BANK_TOP - grass.size_y))

    for col, name in enumerate(BEDS):
        bed = byname.get(name)
        if bed is None:
            print(f"# {name}: not in catalog, skipped", file=sys.stderr)
            continue
        for row, depth in enumerate(DEPTHS):
            x0 = col * (PAD + GAP)
            z0 = row * (PAD + GAP)
            # The bed drops with the depth, so the waterline stays level with
            # the bank on every swatch -- what varies is only the column.
            bed_top = BANK_TOP - 0.5 - depth * water.size_y
            for dz in range(PAD):
                for dx in range(PAD):
                    x, z = x0 + dx, z0 + dz
                    out.append(place_tile(bed, x, z, bed_top - bed.size_y))
                    for layer in range(depth):
                        out.append(place_tile(
                            water, x, z, bed_top + layer * water.size_y))
        print(f"# col {col}: {name}", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements, {len(BEDS)} beds x {len(DEPTHS)} depths",
          file=sys.stderr)


if __name__ == "__main__":
    main()
