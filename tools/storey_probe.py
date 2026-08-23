"""Three ways to stack a storey, side by side, so the roof line and the floor
edge can be judged instead of argued about.

Two complaints, one stack:

  * **roofs float.** Wall courses are pitched at `wall + floor` (2.5) so a deck
    can drop into the gap between them, but the roof is seated at
    `floors * storey_h` -- which is a deck-thickness above the head of the top
    wall. That gap used to be filled by the attic deck. Take the attic deck
    away and the gap is what is left.
  * **the floor shows from outside.** The deck sits in that same gap and fills
    its whole cell, so its edge is a band of floorboards running right round
    the building between storeys, level with the wall face.

Both are about where the deck goes, which is why they are one probe.

    1  today            storey pitched at wall+floor (2.5) so the deck drops
                        into the gap between wall courses; a deck at the top
                        too; roof seated at floors*2.5, half a tile proud
    2  roof seated      same stack, attic deck gone, roof dropped onto the
                        wall head. Fixes the float; the band remains.
    3  continuous wall  storey pitched at the wall alone (2.0) so the courses
                        touch and there is no gap to see through, with the
                        deck laid on interior cells only so it never reaches
                        the outside face. No band, no float.

Design 3 costs an upper floor that stops one cell short of the wall, which
shows only through a window. The kit's own 2.5-tall Wall/Floor combination
pieces are the other way to do it -- wall and floor in one casting -- but they
are full-cell pieces with the wall on one face and nothing in the catalog says
which face, so driving them needs the orientation settled first.

Each is the same 5x4 two-storey house with a flat roof cap, numbered in blocks
in front of it. Read it from a low oblique on all four sides -- the band and
the roof gap are edge-on features and both vanish from directly overhead.

    python tools/storey_probe.py > out/storeyprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    CORNER_BY_SIDES, WALL_CORNER_ROT, _normalized_whole_tiles,
    place_tile, place_wall,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

BOX_W, BOX_D = 5, 4
STOREYS = 2
PAD, GAP = 2, 4

#: The Tavern kit, which is the facade's own kit -- `Village Roof Side Wall 02`
#: lives in folder `Tavern`. See docs/asset-index.md.
WALL = "Village Roof Side Wall 02"
WINDOW = "Village Roof Side Wall With Window 01"
CORNER = "Tavern no floor (1x1 a)"          # 1x2x1, group 'corner'
COMBO = "Tavern Wall/Floor 01"              # 1x2.5x1, wall and floor in one
COMBO_CORNER = "Tavern Wall/Floor Corner 01"
DECK = "Tavern Floor 01"
ROOF = "Thatched roof flat 01"

DESIGNS = ("today", "roof seated", "continuous wall")


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = byname[DECK]
    wall = byname[WALL]
    window = byname[WINDOW]
    corner = byname[CORNER]
    combo, combo_corner = byname.get(COMBO), byname.get(COMBO_CORNER)
    roof = byname[ROOF]
    tally = byname.get("castle merlon 1x1 filler") or floor

    deck_h = floor.size_y
    wall_h = wall.size_y
    storey_h = wall_h + deck_h
    out: list = []
    pitch = BOX_W + 2 * PAD + GAP

    def perimeter():
        for dz in range(BOX_D):
            for dx in range(BOX_W):
                sides = set()
                if dx == 0:
                    sides.add("w")
                if dx == BOX_W - 1:
                    sides.add("e")
                if dz == 0:
                    sides.add("n")
                if dz == BOX_D - 1:
                    sides.add("s")
                if sides:
                    yield dx, dz, sides

    for i, design in enumerate(DESIGNS):
        ox = i * pitch
        for dz in range(-PAD, BOX_D + PAD):
            for dx in range(-PAD, BOX_W + PAD):
                out.append(place_tile(grass, ox + dx, dz, -grass.size_y))
        # Numbered in a stack rather than a row: a row of blocks on grass
        # disappears at a low oblique, which is the angle this is read from,
        # and a probe whose candidates cannot be told apart is worse than no
        # probe. i+1 blocks high, in front of the door side.
        for t in range(i + 1):
            out.append(place_tile(tally, ox + 1, BOX_D + 1, t * tally.size_y))
        for dz in range(BOX_D):
            for dx in range(BOX_W):
                out.append(place_tile(floor, ox + dx, dz, 0.0))

        top = floor.size_y

        def shell(level: int, y: float) -> None:
            """One course of panel wall, with the kit's own corner piece."""
            for dx, dz, sides in perimeter():
                turn = CORNER_BY_SIDES.get(frozenset(sides))
                if turn is not None:
                    out.append(place_tile(corner, ox + dx, dz, y,
                                          WALL_CORNER_ROT[turn]))
                    continue
                for side in sorted(sides):
                    face = window if (level == 1 and (dx + dz) % 3 == 1) else wall
                    out.append(place_wall(face, ox + dx, dz, side, y))

        if design == "continuous wall":
            # Courses touch, so there is no slot between storeys to see
            # through and no band to see. The deck goes at the course
            # boundary, on interior cells only, where it never meets the wall.
            pitch_h = wall_h
            for level in range(STOREYS):
                shell(level, top + level * pitch_h)
            edge = {(dx, dz) for dx, dz, _ in perimeter()}
            inner = [(dx, dz) for dx in range(BOX_W) for dz in range(BOX_D)
                     if (dx, dz) not in edge]
            for level in range(1, STOREYS):
                for dx, dz in inner:
                    out.append(place_tile(floor, ox + dx, dz,
                                          top + level * pitch_h))
            roof_y = top + STOREYS * pitch_h
            head = roof_y
        else:
            for level in range(STOREYS):
                shell(level, top + level * storey_h)
            levels = (range(1, STOREYS + 1) if design == "today"
                      else range(1, STOREYS))
            for level in levels:
                for dx in range(BOX_W):
                    for dz in range(BOX_D):
                        out.append(place_tile(floor, ox + dx, dz,
                                              top + level * storey_h - deck_h))
            head = top + (STOREYS - 1) * storey_h + wall_h
            roof_y = top + STOREYS * storey_h if design == "today" else head

        for dz in range(BOX_D):
            for dx in range(BOX_W):
                out.append(place_tile(roof, ox + dx, dz, roof_y))

        print(f"# {i + 1}: {design}  roof bottom y={roof_y:g}, wall head y={head:g}"
              + ("   <-- FLOATS" if roof_y > head + 1e-6 else ""), file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
