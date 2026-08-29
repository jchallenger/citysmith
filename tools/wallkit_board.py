"""Every wall kit in the library, built the way the generator now builds one.

Twenty-two kits in the installed packs can clad a building and citysmith
resolves three of them. This lays all of them out on one board, each built by
`citysmith.walls` itself rather than by a parallel copy of it -- which is the
point. A probe that reimplements the thing it is probing can only tell you
about the probe. Wide packing, the `shift` remainder, the base/mid/top course
and the glazing here are the *shipped* code paths, so what the board shows is
what a town will look like.

Each panel is one kit's wall: a run of eight cells with the kit's own corner
piece returning at each end, three storeys tall so all three variances show at
once --

  * **wide packing** -- the run is covered by the kit's 2-cell panel wherever
    one fits, with a 1-cell remainder;
  * **the remainder walks** between courses (`walls.DEFAULT_PACK`), so it never
    stacks into a full-height column of a visibly different panel;
  * **courses** -- ground, middle and head come from the kit's `base` / `mid` /
    `top` pieces where it ships them. Castle Fortified has a plinth; Marble
    Palace has all three; most kits name none and answer the same piece
    everywhere, which is correct rather than a gap.

Kits are laid in reading order and the mapping is printed to stderr; the bar of
blocks in front of each panel is its **column**, so a kit can be identified from
any angle without counting to twenty-two.

    python tools/wallkit_board.py > out/wallkits.slab.txt
    python tools/wallkit_board.py --medieval > out/wallkits-med.slab.txt
    .\\tools\\panel_review.ps1 -Slab out\\wallkits.slab.txt -Name wallkits `
                              -Board "PROBE all wall kits" -Height 200 -Oblique 190
"""

from __future__ import annotations

import argparse
import random
import sys
import zlib

sys.path.insert(0, ".")

from citysmith import walls as W
from citysmith.build import (
    WALL_CORNER_ROT, _normalized_whole_tiles, place_tile, place_wall,
    place_wall_span,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

RUN = 8              # cells of straight facade (--run overrides)
STOREYS = 3
COLS = 4
PAD, GAP = 1, 2

#: The three kits the medieval palette resolves today, flagged on the board so
#: the eighteen unused ones are read against something familiar rather than
#: against each other.
IN_USE = ("Tavern", "Rural", "Castle Fortified")

#: One in this many panels carries glass. The rate is stated over *panels* and
#: is the same number the town build uses over cells, which is arithmetic: a
#: run of six at one-in-three is two 1-cell windows or one 2-cell window, so
#: the count halves and the glazed area is identical.
GLAZE = 3


def variants(fam, role, span, course):
    """``(plain siblings, feature pieces)`` for one slot.

    Two different things get called variance and they want different rules:

    **Plain siblings** tie for the same rank -- `bg_wall_1x1_01` and `_02`,
    `abandoned_village_wall_2x1_01` and `_02`. Nothing distinguishes them, so
    dealing at random is free: a long run stops being one repeated texture and
    nothing about the building has changed.

    **Feature pieces** are the same slot with something *on* it -- an arch, an
    alcove, a shield, a breach, a porthole. These carry meaning, which is why
    `WallFamily.all` keeps them out of the default deal: dealt at random they
    put a shield on every house. They need a rate, and some of them need more
    than a rate -- see the caution in the module docstring about arches.
    """
    plain = fam.all(role, span, course)
    every = fam.all(role, span, course, decorated=True)
    ids = {a.id for a in plain}
    return plain, [a for a in every if a.id not in ids]


def build_panel(fam, ox: int, oz: int, out: list, grass, tally, col: int,
                mark, run: int = RUN, variance: int = 0) -> None:
    """One kit's wall: a run with a corner and a return at each end."""
    h = fam.storey_height
    if h is None:
        return
    rng = random.Random(f"{fam.kit}:{run}:{variance}")

    for dz in range(-PAD, 4 + PAD):
        for dx in range(-PAD, run + 2 + PAD):
            out.append(place_tile(grass, ox + dx, oz + dz, -grass.size_y))
    # The bar is the COLUMN, not the kit -- counting to four from any angle is
    # possible and counting to twenty-two is not.
    for t in range(col + 1):
        out.append(place_tile(tally, ox + t, oz + 3, 0.0))
    if mark is not None:
        out.append(place_tile(mark, ox + run + 1, oz + 3, 0.0))

    for level in range(STOREYS):
        y = level * h
        course = W.course_at(level, STOREYS)
        narrow = fam.piece("wall", 1, course)
        wide = fam.piece("wall", 2, course)
        if wide is not None and abs(wide.size_y - h) > W.WIDE_HEIGHT_SLOP:
            wide = None
        if narrow is None:
            narrow = wide
        if narrow is None:
            return
        nook = fam.piece("corner", 1, course)

        # The corner, and one cell of return, so the piece is seen doing its
        # job rather than sitting at the end of a line. Where a kit ships no
        # 1-cell corner the ends are mitred from panels, which is the same
        # fallback `build_from_tilemap` makes.
        #
        # **The returns go NORTH, away from the camera, and the corners are the
        # SOUTH pair.** Both were wrong when this was first written and the
        # board showed it: a corner piece fills its whole cell while a panel is
        # half a cell deep, so on a *free-standing* wall the corner stands half
        # a tile proud of the run and the ends read as a step. In a building
        # that half tile is the inside -- measured on a 10x7 house, no ground
        # course wall geometry reaches outside the footprint at all -- so the
        # panel has to be built as the south wall of something, not as a wall
        # with nothing behind it. `sw`/`se` rather than `nw`/`ne` for the same
        # reason: the exposed sides here are south plus the return's, and
        # `CORNER_BY_SIDES` is the table that says so.
        for cx, turn, ret in ((ox, "sw", "w"), (ox + run + 1, "se", "e")):
            if nook is not None:
                out.append(place_tile(nook, cx, oz, y, WALL_CORNER_ROT[turn]))
            else:
                out.append(place_wall(narrow, cx, oz, "s", y))
                out.append(place_wall(narrow, cx, oz, ret, y))
            out.append(place_wall(narrow, cx, oz - 1, ret, y))

        rule = W.DEFAULT_PACK if wide is not None else "single"
        for k, (off, span) in enumerate(W.pack(run, level, rule)):
            lit = zlib.crc32(f"{fam.kit}:{level}:{off}".encode()) % GLAZE == 0
            piece = None
            if lit:
                piece = fam.piece("window", span, course)
                if piece is not None and span == 2 \
                        and abs(piece.size_y - h) > W.WIDE_HEIGHT_SLOP:
                    piece = None
            if piece is None and variance:
                # Feature pieces are probe-only: they are not wired into the
                # build, because most of them are openings rather than
                # decoration and a rate is the wrong control for an opening.
                _, feats = variants(fam, "wall", span, course)
                if feats and rng.randrange(variance) == 0:
                    piece = rng.choice(feats)
            if piece is None:
                # **The shipped rule**, called the way `build_from_tilemap`
                # calls it: interchangeable siblings dealt per PANEL, keyed on
                # the cell so a rebuild is identical. Kits with one piece per
                # slot -- which is all three the medieval palette uses -- get
                # that piece and nothing changes.
                piece = fam.deal("wall", span, course,
                                 zlib.crc32(f"{fam.kit}:{level}:{off}".encode()))
            if piece is not None and abs(piece.size_y - h) > (
                    W.WIDE_HEIGHT_SLOP if span == 2 else 1e-6):
                piece = None
            if piece is None:
                piece = wide if span == 2 else narrow
            out.append(place_wall_span(piece, ox + 1 + off, oz, "s", span, y))


def main() -> None:
    ap = argparse.ArgumentParser(description="Every wall kit, built as one.")
    ap.add_argument("--medieval", action="store_true",
                    help="only the Medieval Fantasy pack")
    ap.add_argument("--complete", action="store_true",
                    help="only kits that ship the whole family")
    ap.add_argument("--kits", help="comma-separated folder names, in this order")
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--run", type=int, default=RUN,
                    help="cells of straight facade per panel")
    ap.add_argument("--variance", type=int, default=0, metavar="N",
                    help="one panel in N is a FEATURE piece (arch, alcove, "
                         "breach, shield...); plain siblings are dealt at "
                         "random whatever this is set to. 0 turns both off, "
                         "which is the plain board.")
    args = ap.parse_args()

    catalog = load_or_build()
    palette = Palette(catalog, MEDIEVAL)
    grass = palette.require("ground")
    byname = {}
    for a in catalog.assets:
        byname.setdefault(a.name, a)
    tally = byname.get("md_stairblock_01") or palette.require("floor")
    # A second, contrasting block marks the kits the generator uses today.
    mark = byname.get("castle merlon 1x1 filler") or byname.get("Gold Chest")

    fams = W.families(catalog)
    if args.kits:
        want = [k.strip() for k in args.kits.split(",")]
        missing = [k for k in want if k not in fams]
        if missing:
            raise SystemExit(f"no wall family for {missing}; "
                             f"try one of {sorted(fams)}")
        fams = {k: fams[k] for k in want}
    if args.medieval:
        fams = {k: v for k, v in fams.items()
                if v.pack.startswith("Medieval")}
    if args.complete:
        fams = {k: v for k, v in fams.items() if v.complete}

    # In use first, then the complete families, then the rest -- so the board
    # reads as "what we build from, and what we could".
    def order(item):
        kit, fam = item
        return (0 if kit in IN_USE else (1 if fam.complete else 2), kit)

    rows = list(fams.items()) if args.kits else sorted(fams.items(), key=order)
    cols = max(1, args.cols)
    out: list = []
    pitch_x = args.run + 2 + 2 * PAD + GAP
    pitch_z = 4 + 2 * PAD + GAP

    for i, (kit, fam) in enumerate(rows):
        col, row = i % cols, i // cols
        build_panel(fam, col * pitch_x, row * pitch_z, out, grass, tally, col,
                    mark if kit in IN_USE else None,
                    run=args.run, variance=args.variance)
        flag = "USED" if kit in IN_USE else ("full" if fam.complete else "  - ")
        extra = ""
        if args.variance:
            names = []
            for span in (1, 2):
                sibs, feats = variants(fam, "wall", span, "mid")
                if len(sibs) > 1:
                    names.append(f"{len(sibs)}x{span}c siblings")
                if feats:
                    names.append(", ".join(a.name for a in feats))
            extra = "  || " + ("; ".join(names) if names else "no variance")
        print(f"  r{row}c{col}  {flag}  {kit:20s} {fam.pack[:20]:20s} "
              f"courses={','.join(fam.courses):14s} {fam.summary()}{extra}",
              file=sys.stderr)

    byid = {a.id: a for a in catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(rows)} kits, {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
