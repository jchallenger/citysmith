"""Four ways to end a ridge, side by side, on the run the town actually builds.

Every roof on every citysmith board is **hipped on all four sides**, because
`_roof_rings` floods inward from the block's whole boundary and nothing tells
it that two of those sides are ends rather than eaves. Barns, church naves and
great halls are gabled, and the gable end is where the cart doors, the vents,
the west window and the bellcote go -- a hip has nowhere to put any of them
(`docs/great-buildings.md` §1.2).

The pieces to build a gable with are already in the palette, which is why this
is a probe and not a shopping list:

    Village Roof Side End 01   Tavern   1 x 2 x 2   tags include `end`
    Village Roof Side End 02   Tavern   2 x 2 x 2   its 2-cell partner
    Thatched Roof Wall         Rural    2 x 2 x 1   thatch over a stone verge
    Village Roof Side Wall 01  Tavern   1 x 2 x 0.5 the panel UNDER the gable

Renders were read first (`tools/asset_shots.py`): End 01/02 are tiled slopes
closed with a solid triangular face, `Thatched Roof Wall` is thatch cast onto a
grey wall, and `Village Roof Side Wall 01` is the pale framed panel the town
currently wears as a *facade*. So the shapes are right. What a render cannot
say is which quarter turn closes the end, whether the triangle meets the slope
without a slot, and whether the infill below it reaches the wall head -- and
those are exactly the three ways this can fail invisibly in the file.

**The candidates, west to east, numbered by a bar on the ground:**

    1  HIP        what we build today. The control, and it must look wrong
                  next to the others or the probe is not testing anything.
    2  GABLE-END  ridge runs out to the end wall; `Village Roof Side End`
                  closes it; `Village Roof Side Wall` fills the triangle.
    3  GABLE-BARE ridge runs out, end left open. The failure mode: if 2 and 3
                  read the same from outside, the closing piece is not
                  closing anything and the infill is doing all the work.
    4  HALF-HIP   ridge runs out most of the way, then one hipped course --
                  the compromise a lot of real barns actually have.

**Read it in this order and no other.** Overhead first: a ridge's *plan* is
where a gable and a hip are unambiguous, and every oblique flatters one of
them. Then the two ends at eye level, which is what a player sees. Then the
long flank, which is the view that catches a slot between the triangle and the
slope. `tools/review.ps1 360` does the pass.

Both spans matter, because `walls.pack` covers a run with 2-cell panels and
falls back to 1-cell ones -- so a gable that only closes at one width closes
half the buildings in the town. **They go on separate boards, not side by
side**, and that is a framing decision rather than a tidiness one: laid as one
row of eight the board is 59 x 29 and `camera_aim` reports it needs 70 tiles
of slant range against a stop at 50, with 0 of 4 corners in shot. Four
treatments in a 2x2 grid, one span per board, fits. `probe-size-to-one-frame`
in `tasks.json` is open because three wall-probe boards turned out to be
unphotographable after the fact; this one was checked before it was pasted.

    python tools/gable_probe.py --span 1 > out/gable-1.slab.txt
    python tools/gable_probe.py --span 2 > out/gable-2.slab.txt
    python tools/gable_probe.py --kit Rural --span 1 > out/gable-rural.slab.txt
    python tools/camera_aim.py --slab out/gable-1.slab.txt --at 0,0,45,0,70
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_CORNER_ROT, ROOF_EDGE_ROT, SIDE_OFFSETS, _normalized_whole_tiles,
    _roof_rings, place_tile, place_wall, roof_offsets,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: The four end treatments, west to east. The bar on the ground counts them.
TREATMENTS = ("hip", "gable-end", "gable-bare", "half-hip")

#: One test building: long enough to have a ridge worth ending, short enough
#: across that the hip control closes. 9 x 5 gives ring depth 2, so the ridge
#: is 5 cells of crown -- and 5 is the commonest run length in a real town.
BAY_LONG = 8
BAY_ACROSS = 5
BAY_GAP = 2                 #: bare cells between test buildings
BAND_GAP = 3                #: bare cells between grid rows
MARGIN = 2                  #: cells of ground beyond the outermost bay

#: How many courses of wall under the roof. Two, not one: the gable triangle
#: sits on the wall head, and a one-course shell puts it at knee height where
#: the eye cannot judge whether it meets the slope.
WALL_COURSES = 2

#: Pieces by name, per kit. `end` is the closing piece, `infill` fills the
#: triangle under it. A kit with no `end` cannot be gabled and says so rather
#: than silently building a hip.
GABLE_SETS = {
    "Tavern": {
        "slope": "Village Roof Side 01",
        "corner": "Village Roof Corner 01",
        "inner": "Village Roof Inner Corner 01",
        "cap": "Tavern Roof flat 01",
        "end": "Village Roof Side End 01",
        "end2": "Village Roof Side End 02",
        "infill": "Village Roof Side Wall 01",
        "wall": "Tavern Wall - Small 01",
    },
    "Rural": {
        "slope": "Thatched Roof 01",
        "corner": "Thatched Roof Corner 01",
        "inner": "Thatched Roof Inner Corner 01",
        "cap": "Thatched roof flat 01",
        "end": "Thatched Roof Wall",
        "end2": None,
        "infill": None,
        "wall": "Rural Wall 01",
    },
}

CORNER_OF = {
    frozenset(("n", "w")): "nw", frozenset(("n", "e")): "ne",
    frozenset(("s", "w")): "sw", frozenset(("s", "e")): "se",
}


def gable_rings(cells: set[tuple[int, int]], gable_axis: str | None,
                half_hip: bool = False) -> dict[tuple[int, int], int]:
    """Ring depth, with the two ends of ``gable_axis`` excluded from the flood.

    This is the whole proposal in eleven lines, and it is deliberately a
    *separate* function from `build._roof_rings` rather than a flag on it: the
    probe has to be able to lay the unmodified hip beside it as a control, and
    a probe that reimplements what it is probing can only tell you about the
    probe. If the board says yes, this moves into `build.py` as an argument
    and the control disappears.

    ``gable_axis`` is the axis the RIDGE runs along -- ``"x"`` for a ridge
    running east-west, whose gables are therefore the east and west walls.
    ``None`` reproduces `_roof_rings` exactly.

    ``half_hip`` keeps one hipped course at each end: the flood starts from
    the end walls too, but one cell in, so the ridge runs out to within a cell
    of the gable and then stops.
    """
    if gable_axis is None:
        return _roof_rings(cells)

    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    lo, hi = (min(xs), max(xs)) if gable_axis == "x" else (min(zs), max(zs))
    pick = (lambda c: c[0]) if gable_axis == "x" else (lambda c: c[1])
    inset = 1 if half_hip else 0

    def is_end(c) -> bool:
        return pick(c) <= lo + inset or pick(c) >= hi - inset

    rings: dict[tuple[int, int], int] = {}
    # The frontier is the eaves ONLY -- a boundary cell on an end wall is not
    # a place the roof falls away, which is what makes the ridge run out.
    frontier = [c for c in cells
                if not is_end(c)
                and any((c[0] + dx, c[1] + dz) not in cells or
                        is_end((c[0] + dx, c[1] + dz))
                        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    if half_hip:
        frontier = [c for c in cells
                    if any((c[0] + dx, c[1] + dz) not in cells
                           for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    depth = 0
    while frontier:
        nxt = []
        for c in frontier:
            if c in rings:
                continue
            rings[c] = depth
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                if n in cells and n not in rings:
                    nxt.append(n)
        frontier, depth = nxt, depth + 1
    # End cells inherit the depth of their inboard neighbour, so the ridge
    # arrives at the gable wall at full height instead of stepping down.
    for c in sorted(cells):
        if c in rings:
            continue
        step = -1 if pick(c) <= lo + inset else 1
        inboard = ((c[0] + step, c[1]) if gable_axis == "x"
                   else (c[0], c[1] + step))
        rings[c] = rings.get(inboard, 0)
    return rings


def lay_bay(out, pieces, ground, wall, ox: int, oz: int, treatment: str,
            span: int) -> None:
    """One test building: two wall courses, then the roof under ``treatment``."""
    cells = {(x, z) for x in range(BAY_LONG) for z in range(BAY_ACROSS)}
    slope = pieces["slope"]
    rise = slope.size_y
    wall_h = wall.size_y

    # Floor, so the shell has something to stand on and the eye has a datum.
    floor = pieces["floor"]
    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))

    # Shell. Full-cell corner pieces are deliberately NOT used: the question
    # is the roof, and a corner piece would put a second variable in frame.
    #
    # **`place_wall`, not `place_tile` with a hand-rolled rotation.** The first
    # cut of this probe did the latter with `Tavern Wall 01`, which is a
    # **2-cell** panel -- so every segment overhung its neighbour and two
    # panels stacked on each cell edge. On the board that is a jumbled dark
    # glazed mass, and it sat *under the roof this probe exists to judge*.
    # `place_wall` insets the thin axis onto the cell boundary and reads which
    # axis is thin off the collider instead of assuming it.
    for level in range(WALL_COURSES):
        y = level * wall_h
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) in cells:
                    continue
                out.append(place_wall(pieces["wall"], ox + x, oz + z, side, y))

    roof_y = WALL_COURSES * wall_h
    # The ridge runs along the LONG axis, so the gables are the short ends.
    axis = None if treatment == "hip" else "x"
    rings = gable_rings(cells, axis, half_hip=(treatment == "half-hip"))
    top = max(rings.values())
    edge_off, corner_off = roof_offsets(slope)

    xs = [c[0] for c in cells]
    lo_x, hi_x = min(xs), max(xs)

    for (x, z) in sorted(cells):
        r = rings[(x, z)]
        y = roof_y + r * rise
        on_end = axis is not None and x in (lo_x, hi_x)

        if on_end and treatment == "gable-bare":
            continue                       # the deliberately open control

        if on_end and treatment == "gable-end":
            # The closing piece, facing out along the ridge. Both quarter
            # turns are laid across the two ends, so one of them is right
            # whichever way the mesh is authored -- which is the measurement.
            end = pieces["end2"] if span == 2 else pieces["end"]
            if end is not None:
                rot = 0 if x == lo_x else 12
                out.append(place_tile(end, ox + x, oz + z,
                                      y, (rot + edge_off) % 24))
            # Infill below, filling the triangle down to the wall head.
            inf = pieces["infill"]
            if inf is not None:
                for k in range(r):
                    out.append(place_tile(
                        inf, ox + x, oz + z, roof_y + k * rise,
                        18 if x == lo_x else 6))
            continue

        fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                     if rings.get((x + dx, z + dz), -1) < r)
        if not fall:
            piece, rot = pieces["cap"], 0
        elif len(fall) == 1:
            piece, rot = slope, ROOF_EDGE_ROT[fall[0]] + edge_off
        else:
            which = CORNER_OF.get(frozenset(fall))
            if which is None:
                piece, rot = slope, ROOF_EDGE_ROT[fall[0]] + edge_off
            else:
                piece, rot = pieces["corner"], ROOF_CORNER_ROT[which] + corner_off
        if piece is not None:
            out.append(place_tile(piece, ox + x, oz + z, y, rot % 24))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", default="Tavern", choices=sorted(GABLE_SETS))
    ap.add_argument("--span", type=int, choices=(1, 2), default=1,
                    help="which panel width the closing piece is laid at. "
                         "One per board: both on one board does not fit a "
                         "frame, which camera_aim says before it is pasted.")
    ap.add_argument("--cols", type=int, default=2,
                    help="treatments per row. 2 keeps the board framable; "
                         "4 lays them in a line and does not fit.")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)

    names = GABLE_SETS[args.kit]
    pieces = {k: (byname.get(v) if v else None) for k, v in names.items()}
    missing = [k for k, v in pieces.items()
               if v is None and names[k] is not None]
    if missing:
        ap.error(f"{args.kit}: not in this catalog: "
                 + ", ".join(f"{k}={names[k]!r}" for k in missing))
    if pieces["end"] is None:
        ap.error(f"{args.kit} ships no end piece; it cannot be gabled")
    pieces["floor"] = palette.require("floor")

    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or pieces["floor"]

    out: list = []
    cols = max(1, args.cols)
    rows = (len(TREATMENTS) + cols - 1) // cols

    pitch = BAY_LONG + BAY_GAP
    band = BAY_ACROSS + BAND_GAP
    width = cols * pitch
    depth = rows * band

    for dz in range(-MARGIN - 2, depth + MARGIN):
        for dx in range(-MARGIN - 1, width + MARGIN):
            out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

    for ti, treat in enumerate(TREATMENTS):
        ox = (ti % cols) * pitch
        oz = (ti // cols) * band
        lay_bay(out, pieces, ground, pieces["wall"], ox, oz, treat, args.span)
        # Numbered as a BAR ON THE GROUND running east, not a stack: a
        # vertical tally reads at an oblique and vanishes in plan, and this
        # probe is read in plan first.
        for k in range(ti + 1):
            out.append(place_tile(marker, ox + k, oz - 2, 0.0))

    # The board's own span label, once, at the north-west corner: a stack of
    # one block for the 1-cell board and two for the 2-cell. Read from the
    # side, which is where a stack is legible.
    for k in range(args.span):
        out.append(place_tile(marker, -MARGIN - 1, -MARGIN - 2, k * marker.size_y))

    print(f"# {args.kit}: {len(TREATMENTS)} end treatments, "
          f"{args.span}-cell closing piece, {cols} per row", file=sys.stderr)
    for i, t in enumerate(TREATMENTS):
        print(f"#   bar of {i + 1} to its south: {t}", file=sys.stderr)
    print(f"#   NW stack = span ({args.span} block(s))", file=sys.stderr)
    print(f"#   READ OVERHEAD FIRST, then both ends at eye level, "
          f"then the long flank", file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements, board {width + 2 * MARGIN + 1} x {depth + 2 * MARGIN + 2}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
