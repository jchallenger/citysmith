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

Four rounds in, and two of the four were measuring this probe's own bugs. Both
are fixed and both are worth stating, because they are the same mistake about
the same thing -- WHICH END OF A PIECE SEATS AT THE COURSE.

  * A **cap** seats by its TOP, so `y - cap.size_y` puts that top at the ring
    height. The probe placed it at `y` and the ridge stood half a tile proud in
    every box. "The roof patch looks high" was that.
  * A **combination** -- a roof piece with a chimney on it -- seats by its
    BASE, like a slope, so its roof half sits at the course and the stack rises
    above. The probe seated it like a cap, which put all 1.5 tiles of it below
    the course with the chimney buried. "Still sunken" was that.

The shipped `_lay_roofs` has always had both right. This is what
`wallkit_board.py`'s docstring means by a probe that can only tell you about
the probe, and it has now cost four rounds.

Round five sweeps the combination's base around the course height:

    sink 0     base flush with the roof course, the whole stack proud
    sink 0.25  a quarter tile into the roof
    sink 0.5   half a tile in

    r0  flat, centre       -- replacing the ridge cap
    r1  slant, off-centre  -- replacing a slope one ring down, at its rotation

Read it from a low oblique and from directly overhead; the difference between
1 and 2 is whether there are two roof courses at the ridge or one, which shows
in section (`ts.ps1 cutbox`) more clearly than from outside.

    python tools/chimney_probe.py > out/chimneyprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_EDGE_ROT, SIDE_OFFSETS, _is_reflex, _normalized_whole_tiles,
    _roof_piece, _roof_rings, place_tile, place_wall, roof_offsets,
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

#: (cell, position, drop). `cell` is "slant" or "flat", `position` is "centre"
#: or "off". The drop lowers the piece below the ring height it would otherwise
#: seat at by its top.
TREATMENTS = [
    ("flat, centre, sink 0", "flat", "centre", 0.0),
    ("flat, centre, sink 0.25", "flat", "centre", 0.25),
    ("flat, centre, sink 0.5", "flat", "centre", 0.5),
    ("slant, off-centre, sink 0", "slant", "off", 0.0),
    ("slant, off-centre, sink 0.25", "slant", "off", 0.25),
    ("slant, off-centre, sink 0.5", "slant", "off", 0.5),
]


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
    for i, (label, cell_kind, position, sink) in enumerate(TREATMENTS):
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

        def falls(c):
            return tuple(sd for sd, dx, dz in SIDE_OFFSETS
                         if rings.get((c[0] + dx, c[1] + dz), -1) < rings[c])

        if cell_kind == "flat":
            run = crown
        else:
            # A genuine slope one ring below the ridge: exactly one fall, so
            # there is a single rotation for the piece to take. A corner has
            # two and no roof-and-chimney piece describes one.
            run = [c for c in sorted(cells)
                   if rings[c] == top_ring - 1 and len(falls(c)) == 1]
        if not run:
            run = crown
        at = run[len(run) // 2] if position == "centre" else run[0]

        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            y = roof_y + r * rise
            if (x, z) == at:
                # The combination REPLACES whatever would have been here -- the
                # cap on a ridge cell, the slope on a sloped one. Laying both is
                # what put two roof courses at the ridge with the stack riding
                # above them. It seats by its top, and `drop` lowers it from the
                # ring height.
                rot = 0
                if cell_kind == "slant":
                    f = falls((x, z))
                    if f:
                        rot = (ROOF_EDGE_ROT[f[0]] + edge_off) % 24
                # **A combination seats by its BASE, like a slope -- NOT by its
                # top, like a cap.** It is a roof piece with a chimney standing
                # on it, so the roof half belongs at the course and the stack
                # rises above. Seated by its top the whole 1.5 tiles sat below
                # the course and the chimney was buried in the roof, which is
                # what "still sunken" was for three rounds. The shipped gabled
                # path has always done this correctly:
                # `place_tile(chimney, x, z, y, rot)`.
                out.append(place_tile(combo, ox + x, oz + z, y - sink, rot))
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, side_a, corner_a, cap_a, inner_a,
                                     _is_reflex(rings, x, z, fall),
                                     edge_off, corner_off)
            if piece is not None:
                # **A CAP SEATS BY ITS TOP.** `build._lay_roofs` places it at
                # `roof_y + course * rise - cap.size_y`; this probe placed it
                # at the ring height with no subtraction, so the ridge stood
                # half a tile proud of its own slopes in every box -- and
                # three rounds of chimney judgements were made against it.
                # `Builder.surface` follows the same rule for anything laid
                # flat, and CLAUDE.md states it: "surface tiles align at the
                # top, not the bottom".
                seat = y - piece.size_y if piece is cap_a else y
                out.append(place_tile(piece, ox + x, oz + z, seat, rot))

        print(f"# {i + 1}: r{i // COLS}c{i % COLS}  {label}", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
