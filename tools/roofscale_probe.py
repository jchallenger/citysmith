"""A roof in 1x1 pieces beside the same roof using its kit's 2x2 ones.

1,661 roof pieces on Forest Church and every one is 1x1, while every kit we
roof with ships a full 2x2 set. Measured, and the relationship is exact:

    1x1 slope / corner   1.0 tall        2x2 slope / corner   2.0 tall
    1x1 flat cap         0.5 tall        2x2 flat cap         0.5 tall

So a 2x2 piece is exactly two courses of the 1x1 field -- two cells deep and
two rises tall.

**Two things were refuted before this got anywhere, and both are worth keeping.**

First, the wide family does not share its kit's 1x1 rotation. Rural and Tavern
both close at (12, 12) while their small conventions are (0, 0) and (6, 6), so
no rule takes one table to the other -- `ROOF_ROT_OFFSET_WIDE`, swept with
`roofrot_probe.py --hips --footprint wide`.

Second, and this is the shape of the answer: **mixing the scales inside one
roof does not work even with the right turn.** Dropping 2x2 blocks into a 1x1
ring layout leaves holes wherever the coarse piece and the fine ring structure
disagree, which is most places. But the rotation sweep also showed a hip built
ENTIRELY at 2x2 on a coarse grid closing perfectly. So the unit of choice is
the ROOF, not the cell: a wing whose footprint is even in both dimensions can
be built wholly at the double scale, and one that is not stays wholly at the
single one.

    1  1x1, 8x8      the shipped roof, control
    2  2x2, 8x8      the same roof built wholly on a 4x4 coarse grid
    3  1x1, 10x10    control
    4  2x2, 10x10    coarse, 5x5 -- an odd coarse grid, so its ridge is a cell
                     rather than a line and the comparison is the harder one

    python tools/roofscale_probe.py > out/roofscale.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    CORNER_BY_SIDES, ROOF_CORNER_ROT, ROOF_EDGE_ROT, SIDE_OFFSETS, _is_reflex,
    _normalized_whole_tiles, _roof_piece, _roof_rings, place_roof_piece,
    place_tile, place_wall, roof_offsets, roof_offsets_wide,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

PAD, GAP = 1, 1
COLS = 2
STOREYS = 2

#: (label, box, mode). `mode` is "1x1", "2x2" or "corners".
TREATMENTS = [
    ("1x1, 8x8 (control)", (8, 8), "1x1"),
    ("2x2 coarse, 8x8", (8, 8), "coarse"),
    ("1x1, 10x10 (control)", (10, 10), "1x1"),
    ("2x2 coarse, 10x10", (10, 10), "coarse"),
]


def two_by_two(cells, rings, top_ring):
    """Blocks of four cells spanning two rings that a 2x2 piece can cover.

    Greedy and deliberately simple: walk the cells in order and take a block
    wherever all four are free and the pair of rings is ``(r, r+1)``. The
    point of the probe is whether the SCALES mix on a board, not whether this
    is the best packing -- a better one is worth writing only once the answer
    to that is yes.
    """
    taken: set[tuple[int, int]] = set()
    blocks: list[tuple[int, int, int]] = []
    for (x, z) in sorted(cells):
        quad = [(x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)]
        if any(c in taken or c not in rings for c in quad):
            continue
        rs = {rings[c] for c in quad}
        if len(rs) != 2 or max(rs) - min(rs) != 1:
            continue
        if max(rs) > top_ring:
            continue
        taken.update(quad)
        blocks.append((x, z, min(rs)))
    return blocks, taken


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = palette.require("floor")
    wall = palette.require("wall")
    tally = byname.get("md_stairblock_01") or floor

    side1 = palette.require("roof_side")
    corner1 = palette.resolve("roof_corner")
    inner1 = palette.resolve("roof_corner_inner")
    cap1 = palette.require("roof")

    # The 2x2 partners, matched by SIZE rather than by name: the kits number
    # them inconsistently (`Thatched Roof 03` beside `Thatched Roof Corner 02`).
    def partner(one, want_h=2.0):
        return next((a for a in palette.catalog.assets
                     if a.folder == one.folder
                     and "roof" in (a.group_tag or "").lower()
                     and round(a.size_x, 2) == 2.0 and round(a.size_z, 2) == 2.0
                     and abs(a.size_y - want_h) < 1e-6
                     and _family(a.name) == _family(one.name)), None)

    side2 = partner(side1)
    corner2 = partner(corner1) if corner1 else None
    cap2 = partner(cap1, want_h=cap1.size_y) if cap1 else None
    for nm, a in (("1x1 slope", side1), ("2x2 slope", side2),
                  ("1x1 corner", corner1), ("2x2 corner", corner2)):
        print(f"  {nm:12s} {a.name if a else '(none)'}", file=sys.stderr)
    if side2 is None:
        raise SystemExit("no 2x2 slope in this kit -- nothing to probe")

    out: list = []
    widest = max(b[0] for _, b, _ in TREATMENTS) + 2 * PAD + GAP
    deepest = max(b[1] for _, b, _ in TREATMENTS) + 2 * PAD + GAP
    storey_h = wall.size_y
    rise = side1.size_y

    for i, (label, (bw, bd), mode) in enumerate(TREATMENTS):
        ox = (i % COLS) * widest
        oz = (i // COLS) * deepest
        cells = {(x, z) for x in range(bw) for z in range(bd)}

        for dz in range(-PAD, bd + PAD):
            for dx in range(-PAD, bw + PAD):
                out.append(place_tile(grass, ox + dx, oz + dz, -grass.size_y))
        for t in range(i + 1):
            out.append(place_tile(tally, ox + t, oz + bd + PAD - 1, 0.0))
        for x, z in sorted(cells):
            out.append(place_tile(floor, ox + x, oz + z, 0.0))

        top = floor.size_y
        for level in range(STOREYS):
            y = top + level * storey_h
            for x, z in sorted(cells):
                for sd, ex in (("n", z == 0), ("s", z == bd - 1),
                               ("w", x == 0), ("e", x == bw - 1)):
                    if ex:
                        out.append(place_wall(wall, ox + x, oz + z, sd, y))

        roof_y = top + STOREYS * storey_h
        rings = _roof_rings(cells)
        edge_off, corner_off = roof_offsets(side1)
        # **The wide family has its own turn.** Measured with
        # `roofrot_probe.py --hips --footprint wide`: Rural and Tavern both
        # close at (12, 12) while their 1x1 conventions are (0, 0) and (6, 6).
        # The first run of this probe used the 1x1 offsets and produced
        # misaligned planes; that was the bug, not the packing.
        wide_off = roof_offsets_wide(side1)
        top_ring = max(rings.values())

        taken: set[tuple[int, int]] = set()
        if mode == "coarse" and wide_off is None:
            print(f"#    {side1.folder}: no wide turn measured -- 1x1 only",
                  file=sys.stderr)
        elif mode == "coarse":
            # **The whole roof at the double scale, on its own grid.** Rings
            # are computed over super-cells, a piece covers a 2x2 block, and a
            # course rises by the piece's own 2.0. This is `lay_hip_wide`'s
            # algorithm, which the rotation sweep showed closes cleanly -- and
            # it is the thing that mixing scales cell by cell could not do.
            wedge, wcorner = wide_off
            gw, gd = bw // 2, bd // 2
            coarse = {(gx, gz) for gx in range(gw) for gz in range(gd)}
            grings = _roof_rings(coarse)
            wrise = side2.size_y
            for (gx, gz) in sorted(coarse):
                r = grings[(gx, gz)]
                y = roof_y + r * wrise
                fall = tuple(sd for sd, dx, dz in SIDE_OFFSETS
                             if grings.get((gx + dx, gz + dz), -1) < r)
                if len(fall) == 1:
                    piece = side2
                    rot = (ROOF_EDGE_ROT[fall[0]] + wedge) % 24
                elif len(fall) == 2 and corner2 is not None:
                    piece = corner2
                    # **Key the corner on a frozenset, not a sorted join.**
                    # sorted(("n","e")) is ["e","n"] -> "en", which is not a
                    # key, so `.get(..., 0)` silently returned rotation 0 on
                    # the north-east and south-east corners -- half of them --
                    # and the roof came out holed. `CORNER_BY_SIDES` exists
                    # for this.
                    rot = (ROOF_CORNER_ROT[CORNER_BY_SIDES[frozenset(fall)]]
                           + wcorner) % 24
                elif not fall:
                    piece, rot = cap2 or cap1, 0
                else:
                    piece, rot = side2, 0
                if piece is not None:
                    out.append(place_roof_piece(piece, ox + 2 * gx,
                                                oz + 2 * gz, y, rot,
                                                rise=wrise))
            # Any odd row or column the coarse grid could not reach keeps the
            # single scale, which is what an odd footprint gets everywhere.
            taken = {(2 * gx + dx, 2 * gz + dz) for (gx, gz) in coarse
                     for dx in (0, 1) for dz in (0, 1)}

        for (x, z) in sorted(cells):
            if (x, z) in taken:
                continue
            r = rings[(x, z)]
            fall = tuple(sd for sd, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, side1, corner1, cap1, inner1,
                                     _is_reflex(rings, x, z, fall),
                                     edge_off, corner_off)
            if piece is not None:
                out.append(place_roof_piece(piece, ox + x, oz + z,
                                            roof_y + r * rise, rot, rise=rise))

        print(f"# {i + 1}: r{i // COLS}c{i % COLS}  {label}  "
              f"({bw}x{bd}, {len(taken) // 4} wide cell(s))", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


def _family(name: str) -> str:
    """A piece's family: its name with trailing digits and scale words gone."""
    n = name.lower()
    for w in ("01", "02", "03", "04", "05"):
        n = n.replace(w, "")
    return " ".join(n.split())


if __name__ == "__main__":
    main()
