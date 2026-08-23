"""Every 1x1 roof piece in a kit, at every quarter turn, in a labelled grid.

`_lay_roofs` stacks a hip using rotations read out of one community-built
cottage::

    edges    N=6   E=0   S=18  W=12
    corners  NW=12 NE=6  SW=18 SE=0

Those are **the Thatched kit's**. Dropped onto the Tavern/Village pieces they
produce a rank of fins, and I read that as "the hip pieces do not exist at
1x1" -- which was wrong, and wrong in the way this file keeps warning about:
I tried one guessed corner per kit and reported the guess as a finding. The
catalog says otherwise. Tavern ships the same five-piece vocabulary as Rural,
one for one::

    role          Rural (Thatched)                Tavern (Village)
    slope         Thatched Roof 01                Village Roof Side 01
    corner        Thatched Roof Corner 01         Village Roof Corner 01
    inner corner  Thatched Roof Inner Corner 01   Village Roof Inner Corner 01
    flat cap      Thatched roof flat 01           Tavern Roof flat 01
    chimney       Thatched Chimney                Village Roof Side/Chimney

and Castle Fortified has two 1x1 corner pieces nothing has tried
(`Regular 2x2 corner in half bottom`, `Regular 2x2 corner out half top`) plus
a flat cap and three `flare` pieces that look like a *border* course -- a flat
roof with a moulded edge rather than a hip.

So the question is which piece is which shape and which way each one faces,
and that is a measurement, not an argument. This lays **every 1x1-footprint
roof piece in one kit** on its own pedestal, once per quarter turn:

    columns  the pieces, left to right, numbered by a stack at the south end
    rows     rotation 0 / 6 / 12 / 18, numbered by a stack at the west end
    marker   one block off the NORTH side of every pedestal, so "which way
             does it fall" has an answer that does not depend on the camera

Band B repeats the run that actually matters: **three of the piece in a line**,
per rotation. A single slope can look right and a run of them still gap at
every join, which is the failure the town shows and a lone piece cannot.

Read it from **directly overhead first** -- a slope's fall direction is
unambiguous in plan and ambiguous at every oblique -- then drop to a low
oblique for the runs.

    python tools/roofrot_probe.py --kit Tavern > out/roofrot-tavern.slab.txt
    python tools/roofrot_probe.py --kit "Castle Fortified" > out/roofrot-castle.slab.txt
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_CORNER_ROT, ROOF_EDGE_ROT, SIDE_OFFSETS, _is_reflex,
    _normalized_whole_tiles, _roof_rings, place_tile,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

ROTS = (0, 6, 12, 18)
COL_PITCH = 3          #: cells between piece columns
ROW_PITCH = 3          #: cells between rotation rows
RUN = 3                #: pieces in the straight-run band


def roof_pieces(catalog, kit: str) -> list:
    """Every 1x1-footprint roof tile in one kit, by name.

    Chimneys are included deliberately: the point is to see what each piece
    *is*, and a probe that pre-filters by name is the same guess that got the
    corner wrong in the first place.
    """
    out = []
    for a in catalog.assets:
        if a.kind != "tile" or (a.folder or "") != kit:
            continue
        if (a.size_x, a.size_z) != (1.0, 1.0):
            continue
        if "roof" not in ((a.group_tag or "") + a.name).lower():
            continue
        out.append(a)
    return sorted(out, key=lambda a: a.name)


#: The five roles a hip needs, per kit, by name. Only kits whose vocabulary is
#: complete can be offset-tested -- and the point of the table is that Tavern's
#: is complete, one piece for one piece with Rural's.
HIP_SETS = {
    "Rural": ("Thatched Roof 01", "Thatched Roof Corner 01",
              "Thatched Roof Inner Corner 01", "Thatched roof flat 01"),
    "Tavern": ("Village Roof Side 01", "Village Roof Corner 01",
               "Village Roof Inner Corner 01", "Tavern Roof flat 01"),
    "Abandoned Village": ("Haunted roof 1x1", "haunted roof corner out tip",
                          "haunted roof corner inner tip",
                          "haunted roof 1x1 flat"),
    "Castle Fortified": ("Regular 1x1", "Regular 2x2 corner out half top",
                         "Regular 2x2 corner in half bottom", "Top 1x1 flat"),
}

HIP = 6          #: edge of each test hip, in cells
HIP_GAP = 3      #: bare cells between hips


def lay_hip(out, byname, pedestal, kit: str, ox: int, oz: int,
            edge_off: int, corner_off: int) -> None:
    """One hip of ``kit``'s pieces with the Thatched convention turned by
    ``edge_off`` / ``corner_off`` quarter-steps.

    This is the whole hypothesis in one artifact: if a kit's pieces are the
    right shapes and only the *convention* is wrong, then exactly one of these
    closes into a roof and the rest are the rank of fins the town shows.
    """
    slope, corner, inner, cap = (byname.get(n) for n in HIP_SETS[kit])
    cells = {(x, z) for x in range(HIP) for z in range(HIP)}
    for x, z in sorted(cells):
        out.append(place_tile(pedestal, ox + x, oz + z, 0.0))
    rings = _roof_rings(cells)
    rise = slope.size_y if slope is not None else 1.0
    for (x, z) in sorted(cells):
        r = rings[(x, z)]
        y = pedestal.size_y + r * rise
        fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                     if rings.get((x + dx, z + dz), -1) < r)
        # _roof_piece inlined, because the offsets are the thing being tested
        # and threading them through the shared helper would change the town.
        if len(fall) == 1:
            piece, rot = slope, ROOF_EDGE_ROT[fall[0]] + edge_off
        elif len(fall) == 2:
            which = CORNER_OF.get(frozenset(fall))
            if which is None:
                piece, rot = slope, ROOF_EDGE_ROT[fall[0]] + edge_off
            elif _is_reflex(rings, x, z, fall) and inner is not None:
                piece = inner
                rot = ROOF_CORNER_ROT[OPPOSITE_CORNER[which]] + corner_off
            else:
                piece, rot = (corner or slope), ROOF_CORNER_ROT[which] + corner_off
        else:
            piece, rot = cap, 0
        if piece is not None:
            out.append(place_tile(piece, ox + x, oz + z, y, rot % 24))


CORNER_OF = {
    frozenset(("n", "w")): "nw", frozenset(("n", "e")): "ne",
    frozenset(("s", "w")): "sw", frozenset(("s", "e")): "se",
}
OPPOSITE_CORNER = {"nw": "se", "ne": "sw", "sw": "ne", "se": "nw"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", default="Tavern",
                    help="catalog folder, e.g. Tavern, 'Castle Fortified', Rural")
    ap.add_argument("--hips", action="store_true",
                    help="lay four hips, one per rotation offset, instead of "
                         "the piece matrix -- the decisive test, and small "
                         "enough to read at the zoom cap")
    # The edge and the corner do not have to share a convention, and on the
    # Tavern kit they do while on Castle and Haunted they do not: at a shared
    # +6 the slopes run continuously and the corners are still wrong. Pin the
    # edge at the offset that closes the runs and sweep the corner alone.
    ap.add_argument("--edge-off", type=int, default=None,
                    help="fix the edge offset (0/6/12/18) and sweep the "
                         "corner offset across the four hips instead")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    pieces = roof_pieces(cat, args.kit)
    if not pieces:
        ap.error(f"no 1x1 roof pieces in folder {args.kit!r}")

    byname = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)
    grass = palette.require("ground")
    pedestal = palette.require("floor")
    marker = byname.get("md_stairblock_01") or pedestal

    if args.hips:
        if args.kit not in HIP_SETS:
            ap.error(f"no hip vocabulary recorded for {args.kit!r}; "
                     f"have {', '.join(HIP_SETS)}")
        out: list = []
        pitch = HIP + HIP_GAP
        span = len(ROTS) * pitch
        for dz in range(-3, HIP + 5):
            for dx in range(-3, span + 2):
                out.append(place_tile(grass, dx, dz, -grass.size_y))
        for i, off in enumerate(ROTS):
            ox = i * pitch
            # Numbered as a **bar on the ground**, not a stack. A vertical
            # tally reads at an oblique and vanishes in plan -- and a hip is
            # judged in plan, because that is where a gap between courses
            # shows. A bar of i+1 cells running east is unmistakable from
            # directly overhead, which is the one view this probe needs.
            for t in range(i + 1):
                out.append(place_tile(marker, ox + t, HIP + 2, 0.0))
            edge = args.edge_off if args.edge_off is not None else off
            lay_hip(out, byname, pedestal, args.kit, ox, 0, edge, off)
            print(f"# {i + 1}: {args.kit} edge +{edge} corner +{off}",
                  file=sys.stderr)
        byid = {a.id: a for a in cat.assets}
        print(encode(_normalized_whole_tiles(Slab(out), byid)))
        print(f"# {len(out)} placements", file=sys.stderr)
        return

    out: list = []
    width = len(pieces) * COL_PITCH + 4
    depth = len(ROTS) * ROW_PITCH + RUN * 0 + 12

    for dz in range(-4, depth):
        for dx in range(-4, width):
            out.append(place_tile(grass, dx, dz, -grass.size_y))

    # Row labels: rotation index, in a stack west of each row.
    for r, rot in enumerate(ROTS):
        for t in range(r + 1):
            out.append(place_tile(marker, -3, r * ROW_PITCH, t * marker.size_y))
    # Column labels: piece index, in a stack south of each column.
    south = len(ROTS) * ROW_PITCH + 2
    for c in range(len(pieces)):
        for t in range(c + 1):
            out.append(place_tile(marker, c * COL_PITCH, south, t * marker.size_y))

    # Band A: the matrix. One pedestal per (piece, rotation), with a marker
    # block off its north side.
    for c, piece in enumerate(pieces):
        for r, rot in enumerate(ROTS):
            x, z = c * COL_PITCH, r * ROW_PITCH
            out.append(place_tile(pedestal, x, z, 0.0))
            out.append(place_tile(marker, x, z - 1, 0.0))
            out.append(place_tile(piece, x, z, pedestal.size_y, rot))

    # Band B: three in a line, per rotation. A lone slope can look right and a
    # run of them still gap at every join -- which is the failure the town
    # shows and the matrix above cannot.
    runz = south + 4
    for c, piece in enumerate(pieces):
        for r, rot in enumerate(ROTS):
            z = runz + r * (RUN + 1)
            for i in range(RUN):
                x = c * COL_PITCH
                out.append(place_tile(pedestal, x, z + i, 0.0))
                out.append(place_tile(piece, x, z + i, pedestal.size_y, rot))

    print(f"# kit {args.kit}: {len(pieces)} pieces x {len(ROTS)} rotations",
          file=sys.stderr)
    for c, p in enumerate(pieces):
        print(f"#   col {c + 1}: {p.name}  (h={p.size_y:g})", file=sys.stderr)
    print("#   rows, west stack 1..4: rot 0, 6, 12, 18", file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
