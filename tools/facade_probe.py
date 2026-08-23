"""Six ways to dress a building, side by side, so style can be judged as a set.

Today the whole town is dealt from one hand. Measured on Forest Church (51
buildings): five are civic and get dressed stone; the other **46 get one of two
near-identical timber panels**, because ``Palette.resolve("wall", v)`` seeds a
choice inside the *first matching query*, and that query pins two names. Every
roof on the board -- cottage, barn, guildhall and temple alike -- is
``Thatched Roof 01``, because ``_lay_roofs`` resolves the roof set once for the
map rather than once per building. **The temple has a thatched roof.**

So the axes the design wants (where / size / importance) are all currently
collapsed onto one binary, and the strongest available signal -- the roof, which
is most of what a TaleSpire board shows from a normal camera -- carries none.

What the library supports, from ``tools/kit_index.py``. Three kits ship a
*complete* 1x1 roof (slope, outside corner, inner corner, flat cap) and four
ship a wall with a matching 1-cell corner:

    kit                wall + corner        window        roof (1x1, complete)
    Tavern             yes                  yes           yes  (Village, tiled)
    Castle Fortified   yes                  yes           partial (see below)
    Rural              yes                  no            yes  (Thatched)
    Abandoned Village  yes                  no            yes  (Haunted)

Only two 1-cell windows exist in the whole Medieval Fantasy pack -- the Tavern
one and the castle one -- so any tier that wants windows is built from one of
those two kits. That constraint is the reason the tiers below are what they are.

The candidates, each the same 6x5 plan with a real hip roof from the
generator's own ``_roof_rings``/``_roof_piece``:

    1  cottage, today     Village timber + thatch. The control: 46 of 51
                          buildings on the board look like this.
    2  cottage, tiled     the same walls under the Tavern kit's own tiled
                          roof. One line of difference, and it is the one
                          that reads from above.
    3  barn               Rural boarding + Rural corner, no windows, one
                          storey, thatch. What a warehouse or a stable is.
    4  merchant           Village timber, windows on the *street side only*,
                          tiled roof, two storeys. Tests the front/back idea:
                          today windows are dealt evenly on all four sides, so
                          the backs of buildings are as glazed as the fronts.
    5  civic, today       castle stone + castle window + **thatch**. The other
                          control, and the defect: this is the temple.
    6  civic, leaded      the same stone under the castle kit's own roof.
                          The uncertain one -- ``Skirt_1x1_corner out`` is a
                          guess at the hip corner and may be an eave flare, so
                          read this one's corners before believing it.

Read it the way CLAUDE.md says to read a wall probe: four low obliques at
ninety degrees, then overhead (that is where the roofs are decided), then ``N``
for the section. The controls are in so every screenshot has a known-bad to
calibrate against.

    python tools/facade_probe.py > out/facadeprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    CORNER_BY_SIDES, SIDE_OFFSETS, WALL_CORNER_ROT, _is_reflex,
    _normalized_whole_tiles, _roof_piece, _roof_rings, place_tile, place_wall,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

BOX_W, BOX_D = 6, 5
PAD, GAP = 2, 4

#: A design is a wall kit, a roof kit and a rule for where the glass goes.
#: ``glaze`` is "deal" (every third segment, which is what the board does
#: today), "front" (the south face only) or "none".
DESIGNS = [
    dict(label="cottage, today", storeys=2, glaze="deal",
         wall="Village Roof Side Wall 02", corner="Tavern no floor (1x1 a)",
         window="Village Roof Side Wall With Window 01",
         door="Door -Peasant", roof="thatch"),
    dict(label="cottage, tiled", storeys=2, glaze="deal",
         wall="Village Roof Side Wall 02", corner="Tavern no floor (1x1 a)",
         window="Village Roof Side Wall With Window 01",
         door="Door -Peasant", roof="village"),
    dict(label="barn", storeys=1, glaze="none",
         wall="Rural Wall 01", corner="Rural Corner", window=None,
         door="Door -Peasant", roof="thatch"),
    dict(label="merchant", storeys=2, glaze="front",
         wall="Village Roof Side Wall 01", corner="Tavern no floor (1x1 a)",
         window="Village Roof Side Wall With Window 01",
         door="Door - Fancy", roof="village"),
    dict(label="civic, today", storeys=2, glaze="deal",
         wall="castle wall 1x1", corner="castle wall corner 1x1 base",
         window="castle wall 1x1 window",
         door="Door - Fancy", roof="thatch"),
    dict(label="civic, leaded", storeys=2, glaze="deal",
         wall="castle wall 1x1", corner="castle wall corner 1x1 base",
         window="castle wall 1x1 window",
         door="Door - Fancy", roof="castle"),
]

#: side, outside corner, inner corner, flat cap, chimney -- the pieces
#: ``_lay_roofs`` needs. Every set here is 1x1 and stacks on a 1.0 rise except
#: the castle one, whose corner is the open question.
ROOF_KITS = {
    "thatch": ("Thatched Roof 01", "Thatched Roof Corner 01",
               "Thatched Roof Inner Corner 01", "Thatched roof flat 01",
               "Thatched Chimney"),
    "village": ("Village Roof Side 01", "Village Roof Corner 01",
                "Village Roof Inner Corner 01", "Tavern Roof flat 01",
                "Village Roof Side/Chimney"),
    "castle": ("Regular 1x1", "Skirt_1x1_corner out",
               "Skirt_1x1_corner in", "Top 1x1 flat", None),
}


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = palette.require("floor")
    tally = byname.get("md_stairblock_01") or floor

    out: list = []
    pitch = BOX_W + 2 * PAD + GAP
    cells = {(x, z) for x in range(BOX_W) for z in range(BOX_D)}

    def sides_of(x: int, z: int) -> set[str]:
        s = set()
        if x == 0:
            s.add("w")
        if x == BOX_W - 1:
            s.add("e")
        if z == 0:
            s.add("n")
        if z == BOX_D - 1:
            s.add("s")
        return s

    #: The doorway, on the south face so every candidate is entered from the
    #: same side and the fronts can be compared in one pass.
    door_cell = (BOX_W // 2, BOX_D - 1)

    for i, d in enumerate(DESIGNS):
        ox = i * pitch
        wall = byname.get(d["wall"])
        if wall is None:
            print(f"# {d['label']}: {d['wall']!r} missing, skipped", file=sys.stderr)
            continue
        corner = byname.get(d["corner"])
        window = byname.get(d["window"]) if d["window"] else None
        entry = byname.get(d["door"])
        side_a, corner_a, inner_a, cap_a, chim_a = (
            byname.get(n) if n else None for n in ROOF_KITS[d["roof"]])

        # A corner has to be a full cell at the wall's own height, or it drags
        # the storey above it out of line. Checked rather than trusted, since
        # this probe is exactly where a wrong pairing should surface.
        if corner is not None and (
                (corner.size_x, corner.size_z) != (1.0, 1.0)
                or abs(corner.size_y - wall.size_y) > 1e-6):
            print(f"# {d['label']}: corner {corner.name!r} is "
                  f"{corner.size_x:g}x{corner.size_y:g}x{corner.size_z:g}, "
                  "not a wall-height cell -- mitring instead", file=sys.stderr)
            corner = None

        storey_h = wall.size_y

        for dz in range(-PAD, BOX_D + PAD):
            for dx in range(-PAD, BOX_W + PAD):
                out.append(place_tile(grass, ox + dx, dz, -grass.size_y))
        # Numbered in a vertical stack: a row of blocks on grass vanishes at
        # the low oblique this is read from.
        for t in range(i + 1):
            out.append(place_tile(tally, ox + 1, BOX_D + 2, t * tally.size_y))
        for x, z in sorted(cells):
            out.append(place_tile(floor, ox + x, z, 0.0))

        top = floor.size_y
        for x, z in sorted(cells):
            exposed = sides_of(x, z)
            if not exposed:
                continue
            turn = CORNER_BY_SIDES.get(frozenset(exposed))
            is_door = (x, z) == door_cell
            for level in range(d["storeys"]):
                y = top + level * storey_h
                if turn is not None and corner is not None and not (level == 0 and is_door):
                    out.append(place_tile(corner, ox + x, z, y,
                                          WALL_CORNER_ROT[turn]))
                    continue
                for side in sorted(exposed):
                    if level == 0 and is_door and side == "s" and entry is not None:
                        out.append(place_wall(entry, ox + x, z, side, y))
                        continue
                    glass = False
                    if window is not None:
                        if d["glaze"] == "front":
                            glass = side == "s"
                        elif d["glaze"] == "deal":
                            glass = (x + z + level) % 3 == 1
                    face = window if glass else wall
                    out.append(place_wall(face, ox + x, z, side, y))

        # The real roof pass, so the hip is the hip the generator builds.
        roof_y = top + d["storeys"] * storey_h
        rings = _roof_rings(cells)
        rise = side_a.size_y if side_a is not None else 1.0
        crown = [c for c in sorted(cells) if rings[c] == max(rings.values())]
        chimney_at = crown[len(crown) // 2] if (crown and chim_a) else None
        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            y = roof_y + r * rise
            if (x, z) == chimney_at and chim_a is not None:
                out.append(place_tile(chim_a, ox + x, z, y))
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, side_a, corner_a, cap_a, inner_a,
                                     _is_reflex(rings, x, z, fall))
            if piece is not None:
                out.append(place_tile(piece, ox + x, z, y, rot))

        print(f"# {i + 1}: {d['label']:16s} wall={wall.name}  "
              f"corner={corner.name if corner else 'mitred'}  "
              f"roof={d['roof']}  storeys={d['storeys']}  glaze={d['glaze']}",
              file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
