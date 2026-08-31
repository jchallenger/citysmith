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
    _roof_rings, place_centered, place_tile, place_wall, roof_course_anchors,
    roof_course_cells, roof_courses, roof_offsets,
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

#: The quarter-step offsets swept at ``--scale 2``. Exactly one of these
#: should close the roof; the other three are the rank of fins this project
#: has photographed before. **A sweep, not a guess** -- the double-course
#: family's convention has never been measured, and one tried offset reported
#: as a finding is the mistake `roofrot_probe.py` was written to stop.
ROT_OFFSETS = (0, 6, 12, 18)

#: Pieces of the DOUBLE-COURSE family, which is the only one with an end.
#: `Village Roof Side 02` and `Village Roof Side End 01` are 1 x 2 x 2 -- one
#: cell along the run, **two cells down the slope**, two tiles of rise -- so
#: one piece spans two of `_roof_rings`' courses and has to be placed on the
#: PAIR of cells, not on one of them.
WIDE_SETS = {
    "Tavern": {"slope": "Village Roof Side 02",
               "end": "Village Roof Side End 01",
               # **`flat 01`, the ONE-CELL cap, not `flat 02`.** The ridge
               # band is capped per cell, and `Tavern Roof flat 02` is
               # 2 x 0.5 x 2 -- laid with `place_tile` it puts its min corner
               # on the cell and reaches a cell past it, so the roof came out
               # one unit too big to the north and east. Caught on a copy-out
               # the user took off the board: the cap ran to x=15 on a
               # building whose walls stop at x=14. Same class as
               # CLAUDE.md's "place_tile needs an asset that fills the cell",
               # and the probe bypasses the palette so `CELL_ROLES` could not
               # catch it.
               "cap": "Tavern Roof flat 01",
               # The panel that fills the TRIANGLE under the gable. Dropping
               # it built a barn you could see straight through: the end
               # piece closes the roof's own edge and nothing closes the wall
               # below it, so the gable was an open triangle with the far
               # slope's underside visible through it. §3.1's table said what
               # this piece was for and the wide set left it out anyway.
               "infill": "Village Roof Side Wall 01",
               "wall": "Tavern Wall - Small 01"},
}

#: Cells a double-course piece spans down the slope.
WIDE_DEPTH = 2

#: Span across a double-course bay. See `lay_bay_wide` -- 4 is the only span
#: a 1-cell ring flood and a 2-cell piece both agree on.
WIDE_ACROSS = 4

#: Which way is "inward" from a cell whose roof falls toward ``side``.
_INWARD = {"n": (0, 1), "s": (0, -1), "e": (-1, 0), "w": (1, 0)}


def lay_bay(out, pieces, ground, wall, ox: int, oz: int, treatment: str,
            span: int = 1, long_: int = BAY_LONG, across: int = BAY_ACROSS,
            wall_courses: int = WALL_COURSES) -> None:
    """One SINGLE-COURSE bay, ended four ways. The `--scale 1` half.

    This did not exist: `main` has always called it and only `lay_bay_wide`
    was ever written, so `--kit Rural` -- the thatched cottage every complaint
    about verges has been about -- died on a `NameError` and the one material
    that mattered was the one material this probe could not build.

    **The gable arithmetic is `build.roof_courses`, not a copy of it**, the
    same rule `lay_bay_wide` states: a probe that reimplements what it is
    probing can only tell you about the probe. `roof_courses` already takes
    the ridge axis and answers `(course, fall)` per cell with `None` for the
    ridge band, which is exactly the three gable treatments.

    **The hip control is the exception, and it is declared rather than
    hidden.** The shipped hip is `_lay_roofs`, which needs a Builder and a
    TileMap and cannot be called from here; so the control is built from
    `_roof_rings` -- the real flood, not a copy -- plus the one rule the flood
    does not carry, which side a ring cell falls toward. If the hip bay ever
    looks wrong, suspect this before suspecting the kit.

        hip        every side an eave; no gable, no end piece, no triangle
        gable-end  ridge runs out; `end` closes it; `infill` fills the
                   triangle, or the cap is stacked where the kit has no
                   infill -- which is the boarding `gable-verge-look` is about
        gable-bare ridge runs out and the end is left open. If this reads the
                   same as `gable-end`, the end piece is closing nothing
        half-hip   ridge runs out, then the last course hips
    """
    cells = {(x, z) for x in range(long_) for z in range(across)}
    floor = pieces["floor"]
    wall_h = wall.size_y
    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))
    for level in range(wall_courses):
        y = level * wall_h
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) not in cells:
                    out.append(place_wall(wall, ox + x, oz + z, side, y))

    roof_y = wall_courses * wall_h
    slope = pieces["slope"]
    corner = pieces.get("corner")
    cap = pieces.get("cap")
    end = pieces.get("end")
    infill = pieces.get("infill")
    rise = slope.size_y
    edge_off, corner_off = roof_offsets(slope)

    xs = sorted({c[0] for c in cells})
    lo_x, hi_x = xs[0], xs[-1]

    if treatment == "hip":
        rings = _roof_rings(cells)
        peak = max(rings.values())
        for (x, z), ring in sorted(rings.items()):
            y = roof_y + ring * rise
            # Which sides this cell is `ring` steps from -- the flood does not
            # record it, and a cell equidistant from two is a corner.
            near = [s for s, dx, dz in SIDE_OFFSETS
                    if (x + dx * (ring + 1), z + dz * (ring + 1)) not in cells]
            if ring == peak and cap is not None and len(near) != 1:
                out.append(place_tile(cap, ox + x, oz + z, y - cap.size_y))
                continue
            if len(near) >= 2 and corner is not None:
                key = frozenset(near[:2])
                which = CORNER_OF.get(key)
                if which is not None:
                    rot = (ROOF_CORNER_ROT[which] + corner_off) % 24
                    out.append(place_tile(corner, ox + x, oz + z, y, rot))
                    continue
            fall = near[0] if near else None
            if fall is None:
                if cap is not None:
                    out.append(place_tile(cap, ox + x, oz + z, y - cap.size_y))
                continue
            rot = (ROOF_EDGE_ROT[fall] + edge_off) % 24
            out.append(place_tile(slope, ox + x, oz + z, y, rot))
        return

    courses = roof_courses(cells, "x", roof_course_cells(slope))
    hip_last = treatment == "half-hip"
    for (x, z), (course, fall) in sorted(courses.items()):
        y = roof_y + course * rise
        if fall is None:                       # the ridge band
            if cap is not None:
                out.append(place_tile(cap, ox + x, oz + z, y - cap.size_y))
            continue
        # `half-hip` hips only the outermost column of each end, which is the
        # compromise a real barn has: a short hipped face above a gable.
        if hip_last and x in (lo_x, hi_x):
            side = "w" if x == lo_x else "e"
            rot = (ROOF_EDGE_ROT[side] + edge_off) % 24
            out.append(place_tile(slope, ox + x, oz + z, y, rot))
            continue
        out.append(place_tile(slope, ox + x, oz + z, y,
                              (ROOF_EDGE_ROT[fall] + edge_off) % 24))

    if treatment == "gable-bare":
        return                                  # the control: nothing closes it
    if hip_last:
        return                 # a half-hip has no gable wall to close at all

    # **The triangle, and this is the whole of `gable-verge-look`.**
    #
    # First cut of this stood an `end` piece at every cell of the end column
    # with `place_wall`, ON TOP of the slope already there rather than in
    # place of it, and the board showed thatch bolsters proud of the ridge
    # like horns. `lay_bay_wide` does not do that: at the end column it
    # SUBSTITUTES the end piece for the slope. The horns were the probe.
    #
    # A wall-shaped verge piece is not a cap and does not stack like one.
    # `Thatched Roof Wall` is 2.0 x 2.0 x 1.0 -- two cells along its own x,
    # two tiles tall, one deep -- so laid in a plane of constant x it takes a
    # quarter turn to put its long axis across the ridge, spans TWO cells of
    # z, and covers TWO courses of a 1.0 rise. One per cell, unturned, is the
    # column of stacked panels the first paste showed.
    verge = infill if infill is not None else end
    if verge is None:
        verge = cap
    if verge is None:
        return
    wide = verge.size_x >= 2.0 - 1e-6
    zs = sorted({c[1] for c in cells})
    step = 2 if wide else 1
    lift = verge.size_y

    for x in (lo_x, hi_x):
        for i in range(0, len(zs), step):
            group = zs[i:i + step]
            if len(group) < step:
                continue        # an odd cell at the end has no partner piece
            course = max(courses[(x, zz)][0] for zz in group)
            if course <= 0:
                continue
            cz = oz + (group[0] + group[-1]) / 2.0 + 0.5
            cx = ox + x + 0.5
            # A quarter turn where the piece is wide, so its length runs
            # across the ridge and not along it.
            rot = 6 if wide else 0
            # **Only a panel that FITS under the roof line.** A 2.0-tall
            # panel started at the last course rises a course past the slope
            # it is closing, and on the board that is a thatch bolster
            # standing proud of the ridge like a horn -- which is what the
            # first two pastes of this showed and what was read as a kit
            # fault before it was read as arithmetic.
            tall = max(1, int(round(lift / rise)))
            k = 0
            while k + tall <= course:
                out.append(place_centered(verge, cx, cz, roof_y + k * rise, rot))
                k += tall
            # The remainder, shorter than one panel, takes the flat cap: it is
            # the one place the boarding is unavoidable, and it is one course
            # under the ridge rather than the whole rake.
            if k < course and cap is not None:
                for j in range(k, course):
                    out.append(place_centered(cap, cx, cz,
                                              roof_y + j * rise, rot))


def lay_bay_wide(out, pieces, ox: int, oz: int, offset: int,
                 wall, wall_courses: int, across: int,
                 long_: int = BAY_LONG) -> None:
    """A gabled double-course roof over one bay, at one rotation offset.

    **The course arithmetic is `build.roof_courses`, not a copy of it.** The
    first version of this probe carried its own `gable_rings`, and a probe that
    reimplements what it is probing can only tell you about the probe -- which
    is exactly what happened: three placement bugs in that copy were read as
    findings about the kit before they were read as bugs. The builder owns the
    flood now and this calls it, the same way `wallkit_board.py` builds through
    `citysmith.walls` rather than beside it.
    """
    cells = {(x, z) for x in range(long_) for z in range(across)}
    floor = pieces["floor"]
    wall_h = wall.size_y
    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))
    for level in range(wall_courses):
        y = level * wall_h
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) not in cells:
                    out.append(place_wall(wall, ox + x, oz + z, side, y))

    roof_y = wall_courses * wall_h
    slope, end, cap = pieces["slope"], pieces["end"], pieces["cap"]
    rise = slope.size_y
    cpc = roof_course_cells(slope)

    courses = roof_courses(cells, "x", cpc)
    anchors = roof_course_anchors(courses, "x", cpc)
    xs = [c[0] for c in cells]
    lo_x, hi_x = min(xs), max(xs)

    for (x, z), (course, fall) in sorted(anchors.items()):
        y = roof_y + course * rise
        ix, iz = _INWARD[fall]
        # Centre of the band this piece covers: cpc cells running inward.
        cx = ox + x + 0.5 + ix * (cpc - 1) * 0.5
        cz = oz + z + 0.5 + iz * (cpc - 1) * 0.5
        piece = end if x in (lo_x, hi_x) else slope
        out.append(place_centered(piece, cx, cz, y,
                                  (ROOF_EDGE_ROT[fall] + offset) % 24))

    # The ridge band, capped flat. Laid by its TOP, the rule `Builder.surface`
    # follows for anything horizontal -- a cap seated by its base sits a
    # cap-thickness proud of the slopes it closes.
    if cap is not None:
        for (x, z), (course, fall) in sorted(courses.items()):
            if fall is not None:
                continue
            y = roof_y + course * rise
            out.append(place_tile(cap, ox + x, oz + z, y - cap.size_y))

    # The gable TRIANGLE, filled column by column. One panel per course, from
    # the wall head up to the roof line at that across-position -- so the
    # infill steps up toward the ridge and follows the slope, which is what
    # makes it a triangle rather than a rectangle.
    infill = pieces.get("infill")
    if infill is not None:
        for (x, z), (course, fall) in sorted(courses.items()):
            if x not in (lo_x, hi_x):
                continue
            side = "w" if x == lo_x else "e"
            for k in range(course):
                out.append(place_wall(infill, ox + x, oz + z, side,
                                      roof_y + k * infill.size_y))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", default="Tavern", choices=sorted(GABLE_SETS))
    ap.add_argument("--span", type=int, choices=(1, 2), default=1,
                    help="which panel width the closing piece is laid at. "
                         "One per board: both on one board does not fit a "
                         "frame, which camera_aim says before it is pasted.")
    ap.add_argument("--scale", type=int, choices=(1, 2), default=1,
                    help="1 sweeps the four END TREATMENTS at the "
                         "single-course scale -- which is what the town "
                         "builds and which has no end piece. 2 sweeps the "
                         "four ROTATION OFFSETS of the double-course family, "
                         "which is the only one that has one.")
    ap.add_argument("--long", type=int, default=BAY_LONG, dest="long_",
                    help="length ALONG the ridge, in cells. Must be at least "
                         "--across or the gable ends up on the long face and "
                         "the building reads squat -- which is a fact about "
                         "the probe's composition, not about the roof.")
    ap.add_argument("--across", type=int, default=WIDE_ACROSS,
                    help="span across the ridge, in cells. 4 was the only "
                         "span the two scales agreed on before "
                         "`build.roof_courses`; every span tiles now, and "
                         "11 is what a real East Tradebourne warehouse is.")
    ap.add_argument("--offset", type=int, default=None, choices=(0, 6, 12, 18),
                    help="lay ONE bay at this offset instead of sweeping all "
                         "four. The rotation is measured (+6); this is for "
                         "showing a wide span, where four bays do not frame.")
    ap.add_argument("--cols", type=int, default=2,
                    help="treatments per row. 2 keeps the board framable; "
                         "4 lays them in a line and does not fit.")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)

    if args.scale == 2:
        if args.kit not in WIDE_SETS:
            ap.error(f"no double-course vocabulary recorded for {args.kit!r}; "
                     f"have {', '.join(WIDE_SETS)}")
        names = dict(WIDE_SETS[args.kit])
        names.setdefault("end2", None)
        names.setdefault("infill", None)
        names.setdefault("corner", None)
        names.setdefault("inner", None)
    else:
        names = GABLE_SETS[args.kit]
    pieces = {k: (byname.get(v) if v else None) for k, v in names.items()}
    missing = [k for k, v in pieces.items()
               if v is None and names[k] is not None]
    if missing:
        ap.error(f"{args.kit}: not in this catalog: "
                 + ", ".join(f"{k}={names[k]!r}" for k in missing))
    if pieces["end"] is None:
        ap.error(f"{args.kit} ships no end piece; it cannot be gabled")
    # The ridge cap is laid one per cell, so it has to BE one cell. This is
    # the check that would have caught the north-east overhang before it went
    # on a board.
    cap = pieces.get("cap")
    if cap is not None and (cap.size_x, cap.size_z) != (1.0, 1.0):
        ap.error(f"cap {cap.name!r} is {cap.size_x:g}x{cap.size_z:g} cells; "
                 f"it is laid per cell and would overhang")
    pieces["floor"] = palette.require("floor")

    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or pieces["floor"]

    out: list = []
    # A wide span needs the bay spacing to follow it, or the bays overlap.
    across = args.across if args.scale == 2 else BAY_ACROSS
    sweep = ([args.offset] if args.offset is not None
             else list(ROT_OFFSETS) if args.scale == 2 else list(TREATMENTS))
    cols = max(1, min(args.cols, len(sweep)))
    rows = (len(sweep) + cols - 1) // cols

    pitch = (args.long_ if args.scale == 2 else BAY_LONG) + BAY_GAP
    band = across + BAND_GAP
    width = cols * pitch
    depth = rows * band

    for dz in range(-MARGIN - 2, depth + MARGIN):
        for dx in range(-MARGIN - 1, width + MARGIN):
            out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

    for ti, item in enumerate(sweep):
        ox = (ti % cols) * pitch
        oz = (ti // cols) * band
        if args.scale == 2:
            lay_bay_wide(out, pieces, ox, oz, item, pieces["wall"],
                         WALL_COURSES, across, args.long_)
        else:
            lay_bay(out, pieces, ground, pieces["wall"], ox, oz, item,
                    args.span)
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

    if args.scale == 2:
        print(f"# {args.kit}: double-course gable, span {across} across, "
              f"{len(sweep)} bay(s)", file=sys.stderr)
        for i, o in enumerate(sweep):
            print(f"#   bar of {i + 1} to its south: offset +{o}",
                  file=sys.stderr)
    else:
        print(f"# {args.kit}: {len(TREATMENTS)} end treatments, "
              f"{args.span}-cell closing piece, {cols} per row",
              file=sys.stderr)
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
