"""A roof in 1x1 pieces beside the same roof using its kit's 2x2 ones.

1,661 roof pieces on Forest Church and every one is 1x1, while every kit we
roof with ships a full 2x2 set. Measured, and the relationship is exact:

    1x1 slope / corner   1.0 tall        2x2 slope / corner   2.0 tall
    1x1 flat cap         0.5 tall        2x2 flat cap         0.5 tall

So **a 2x2 slope is exactly two courses of the 1x1 field** -- two cells deep
and two rises tall. That is the same scale relationship `END_PIECE_CELLS`
already records for the gable end piece, and it is what makes the two sizes
mixable at all rather than merely both present.

Where a 2x2 goes, therefore: over a 2x2 block of cells spanning rings ``r`` and
``r+1``, seated at ring ``r``'s course. It replaces four 1x1 placements with
one, and the ring it lands on has to be even for the pairing to reach the
ridge without a leftover half-course.

**Seating comes from `build.place_roof_piece`, not from this file.** Five
review rounds went into a chimney probe that re-derived it and got it wrong
twice -- a cap by its base, a combination by its top. This probe asks a
question about SCALE; it has no business having an opinion about height.

    1  1x1 only, the shipped roof            -- the control
    2  2x2 where a full block fits, 1x1 rest
    3  2x2, on a roof one ring deeper        -- does the pairing reach the ridge
    4  2x2 corners only, 1x1 straight runs   -- corners are where a hip reads

    python tools/roofscale_probe.py > out/roofscale.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_CORNER_ROT, ROOF_EDGE_ROT, SIDE_OFFSETS, _is_reflex,
    _normalized_whole_tiles, _roof_piece, _roof_rings, place_roof_piece,
    place_tile, place_wall, roof_offsets,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

PAD, GAP = 1, 1
COLS = 2
STOREYS = 2

#: (label, box, mode). `mode` is "1x1", "2x2" or "corners".
TREATMENTS = [
    ("1x1 only, shipped", (8, 8), "1x1"),
    ("2x2 where a block fits", (8, 8), "2x2"),
    ("1x1 only, deeper roof", (10, 10), "1x1"),
    ("2x2, deeper roof", (10, 10), "2x2"),
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
        top_ring = max(rings.values())

        taken: set[tuple[int, int]] = set()
        if mode == "2x2":
            blocks, taken = two_by_two(cells, rings, top_ring)
            for (bx, bz, r) in blocks:
                quad = [(bx, bz), (bx + 1, bz), (bx, bz + 1), (bx + 1, bz + 1)]
                # Which way the block falls: the side its lower ring faces.
                low = [c for c in quad if rings[c] == r]
                fall = tuple(sd for sd, dx, dz in SIDE_OFFSETS
                             if any(rings.get((c[0] + dx, c[1] + dz), -1) < r
                                    for c in low))
                if len(fall) == 1:
                    piece = side2
                    rot = (ROOF_EDGE_ROT[fall[0]] + edge_off) % 24
                elif len(fall) == 2 and corner2 is not None:
                    piece = corner2
                    rot = (ROOF_CORNER_ROT.get("".join(sorted(fall)), 0)
                           + corner_off) % 24
                else:
                    piece, rot = side2, 0
                out.append(place_roof_piece(piece, ox + bx, oz + bz,
                                            roof_y + r * rise, rot, rise=rise))

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
              f"({bw}x{bd}, {len(taken) // 4} 2x2 block(s))", file=sys.stderr)

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
