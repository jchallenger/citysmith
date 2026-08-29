"""Five ways to put a chimney on a thatched ridge, side by side.

The Rural kit ships two chimney pieces and the difference is in the name, the
way `Village Roof Side/Chimney` differs from `Chimney 01` in Tavern:

    Thatched Chimney        1 x 0.5 x 1     a bare stack
    Thatched Roof Chimney   1 x 1.5 x 1     a ROOF with a chimney on it

`palette.roof_stack` is pinned to the second and reads it as "the taller of
Rural's two stacks", so `_lay_roofs` lays a flat cap and then stands the
combination on top of it -- a roof course on a roof course, with the stack
riding above both. On the board that is a chimney wearing a thatch skirt, which
is what a reviewer called "chimney + slant pieces instead of a lowered
chimney".

That reading is a guess about what the mesh looks like, and this project's rule
is that a name is not a shape -- `castle merlon 1x1` was a wooden hoarding for
eleven revisions on exactly this kind of inference. So: build all five, look,
and only then change the palette.

Round one settled which piece, on a board read of five treatments. Two
survived --

    combo   `Thatched Roof Chimney` REPLACING the cap, no cap under it
    stone   flat cap, then Tavern's `Chimney 01`, a stone stack

-- and both still read with the tile at the ridge standing proud of the thatch
around it. Round two crosses those two against a DROP applied to the ridge
piece and anything standing on it together, so the whole assembly lowers and
only its height against the surrounding slopes changes.

**The arithmetic says they are already flush, and the board says otherwise.**
A cap seats by its top at the ring height and the ring below carries a 1.0-tall
slope from one tile down, so both top out at the same y. That is exactly why
this is a probe: a collider is not a mesh, and this project has a rule about
reading one as the other.

    columns   drop 0.000 (control) | 0.125 | 0.250
    rows      combo | stone

Read it from a low oblique and from directly overhead; the difference between
1 and 2 is whether there are two roof courses at the ridge or one, which shows
in section (`ts.ps1 cutbox`) more clearly than from outside.

    python tools/chimney_probe.py > out/chimneyprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    SIDE_OFFSETS, _is_reflex, _normalized_whole_tiles, _roof_piece,
    _roof_rings, place_tile, place_wall, roof_offsets,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

BOX_W, BOX_D = 6, 5
PAD, GAP = 1, 1
#: Three columns, not a row of five. A row of five is 40 tiles wide and needs
#: 51 of slant range against a stop at 49.75 -- and PITCH DOES NOT HELP,
#: because the binding constraint is horizontal field of view at maximum
#: range, not how far the camera is leaning. Checked at 30, 35, 40, 50 and 55
#: degrees: all 51. 3x2 is 24 by 14 and fits with room to spare.
COLS = 3
STOREYS = 2

#: How far the ridge piece -- and anything standing on it -- drops below the
#: ring height. Dropping a cap alone would leave its stack hanging in the air.
DROPS = (0.0, 0.125, 0.25)

TREATMENTS = (
    [(f"combo, no cap, drop {d:g}", "combo", d) for d in DROPS]
    + [(f"cap + stone stack, drop {d:g}", "crosskit", d) for d in DROPS]
)


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = palette.require("floor")
    wall = palette.require("wall")
    tally = byname.get("md_stairblock_01") or floor

    side_a = palette.require("roof_side")
    corner_a = palette.resolve("roof_corner")
    inner_a = palette.resolve("roof_corner_inner")
    cap_a = palette.require("roof")
    combo = byname["Thatched Roof Chimney"]
    bare = byname["Thatched Chimney"]
    stone = byname.get("Chimney 01")

    out: list = []
    pitch = BOX_W + 2 * PAD + GAP
    cells = {(x, z) for x in range(BOX_W) for z in range(BOX_D)}
    storey_h = wall.size_y

    pitch_z = BOX_D + 2 * PAD + GAP
    for i, (label, mode, drop) in enumerate(TREATMENTS):
        ox = (i % COLS) * pitch
        oz = (i // COLS) * pitch_z
        for dz in range(-PAD, BOX_D + PAD):
            for dx in range(-PAD, BOX_W + PAD):
                out.append(place_tile(grass, ox + dx, oz + dz, -grass.size_y))
        for t in range(i + 1):
            out.append(place_tile(tally, ox + t, oz + BOX_D + PAD - 1, 0.0))
        for x, z in sorted(cells):
            out.append(place_tile(floor, ox + x, oz + z, 0.0))

        top = floor.size_y
        for level in range(STOREYS):
            y = top + level * storey_h
            for x, z in sorted(cells):
                for s, ex in (("n", z == 0), ("s", z == BOX_D - 1),
                              ("w", x == 0), ("e", x == BOX_W - 1)):
                    if ex:
                        out.append(place_wall(wall, ox + x, oz + z, s, y))

        roof_y = top + STOREYS * storey_h
        rings = _roof_rings(cells)
        rise = side_a.size_y
        edge_off, corner_off = roof_offsets(side_a)
        top_ring = max(rings.values())
        crown = [c for c in sorted(cells) if rings[c] == top_ring]
        at = crown[len(crown) // 2]

        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            y = roof_y + r * rise
            if (x, z) == at:
                # A piece that seats by its TOP puts that top at the ring
                # height; `drop` lowers it from there. Anything standing on it
                # takes the same drop or it is left hanging.
                ytop = y - drop
                if mode == "combo":
                    # The combination IS the roof course here, so no cap under
                    # it -- laying both is what put two roof courses at the
                    # ridge with the stack riding above them.
                    out.append(place_tile(combo, ox + x, oz + z,
                                          ytop - combo.size_y))
                    continue
                out.append(place_tile(cap_a, ox + x, oz + z,
                                      ytop - cap_a.size_y))
                if mode == "crosskit" and stone is not None:
                    out.append(place_tile(stone, ox + x, oz + z, ytop))
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, side_a, corner_a, cap_a, inner_a,
                                     _is_reflex(rings, x, z, fall),
                                     edge_off, corner_off)
            if piece is not None:
                out.append(place_tile(piece, ox + x, oz + z, y, rot))

        print(f"# {i + 1}: r{i // COLS}c{i % COLS}  {label}", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
