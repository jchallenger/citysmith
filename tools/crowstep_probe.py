"""Four ways to end a ridge when the kit ships no end piece -- which is every
kit but one.

`docs/great-buildings.md` §3.1a measured the double-course gable and it works.
Then §3.4b's follow-up found the constraint that matters more: **`Tavern` is
the only kit in the whole library with a roof `end` piece.** Rural, Castle
Fortified, Abandoned Village, Moorgoth and Marble Palace have none at either
scale. So a gable built from a dedicated terminator is available to the house
fabric and nothing else -- and the two great buildings that most want one, the
barn in boarding and the temple in dressed stone, cannot have it.

A **crow-stepped gable** needs no end piece. The gable wall simply carries on
up past the roof in steps that follow its pitch, and it is a real northern
British and Scottish form rather than a workaround. It is also, at a one-cell
ring scale, the only gable the geometry can express without a terminator: a
step is exactly one cell in and one course up, which is what `roof_courses`
already computes.

Probed in the **civic** fabric on purpose -- Castle Fortified walls with the
Abandoned Village slate roof, which is what `roof_set` actually resolves for
that tier. Crow-stepping is a masonry form; a timber barn wants a plain
boarded gable, not a stone staircase, so Rural is a separate question.

**The pieces, and two of the four candidates were settled by render before any
slab existed** (`tools/asset_shots.py`):

    castle wall 1x1 half   1 x 1.0 x 0.5  coursed pale stone, HALF height --
                           exactly one 45-degree step. The find.
    Top 1x1 flare out      1 x 0.5 x 1    dark leaded coping with a moulded
                           flared edge. A roof-top border course.
    castle merlon 1x1 stair L/R          REJECTED. The name says stair and it
                           is one: a WOODEN staircase for climbing. CLAUDE.md
                           already records that this kit's whole merlon group
                           is boarded timber -- "the circuit was crowned with
                           wooden crates for eleven revisions" -- and the
                           render confirmed it in a second.

**The bays, numbered by a bar on the ground:**

    1  HIP          what the town builds now. The control.
    2  GABLE-FLUSH  ridge runs out; the end column's wall is filled up to the
                    roof line and stops there. No parapet, no end piece.
    3  CROW-BARE    the end wall carries ONE COURSE PROUD of the roof at every
                    step, so the outline is a staircase. No coping.
    4  CROW-COPED   as 3, with `Top 1x1 flare out` capping each tread.

3 and 4 are the real question and 2 is what separates them from it: if FLUSH
and CROW read the same from the ground, the parapet is doing nothing and the
cheaper one wins.

**A CROW-STEPPED end is not roofed, and getting that wrong is what the first
run of this probe did.** It laid the roof over the end column and stood the
parapet on its outer face, reasoning that a parapet is a wall beside a roofed
cell. On the board that reads as a row of **detached lumps with slate between
them**: the parapet is one course proud of the roof at each z, and the roof at
z+1 is one course higher again, so it rises between every pair of steps and the
staircase never reads as one wall. A real crow-step is the building's end --
the roof stops *against* it. So the end column carries the parapet and no roof
at all, which is also what makes the steps continuous.

The FLUSH bay still roofs its end column, because a flush gable genuinely is a
roofed cell with the wall stopping at the roof line. That difference is now the
main thing separating bays 2 and 3.

    python tools/crowstep_probe.py > out/crowstep.slab.txt
    python tools/camera_aim.py --slab out/crowstep.slab.txt --at 0,0,45,0,74
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    GABLE_ENDS, ROOF_EDGE_ROT, SIDE_OFFSETS, _normalized_whole_tiles,
    _roof_piece, _roof_rings, gable_end_for, place_tile, place_wall,
    roof_course_anchors, roof_course_cells, roof_courses, roof_offsets,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

TREATMENTS = ("hip", "gable-flush", "crow-bare", "crow-coped")

#: `build.gable_end_for` deals one of three names per quarter; these are the
#: bay treatments they map to. `crow` takes the BARE parapet, because the coped
#: one reads as a row of little hats perched on the steps -- measured on
#: `PROBE crow-step`, and `crowstep-coping` is open against finding a piece
#: that sits flush.
BY_DEAL = {"hip": "hip", "flush": "gable-flush", "crow": "crow-bare"}

BAY_LONG = 9
BAY_ACROSS = 7
BAY_GAP = 2
BAND_GAP = 3
MARGIN = 1
WALL_COURSES = 2

#: Civic fabric, by name. The roof is Abandoned Village because that is what
#: `roof_set(palette, "civic")` resolves -- Castle Fortified ships **no roof
#: pieces at all**, which is its own small finding.
PIECES = {
    "wall": "castle wall 1x1",
    "step": "castle wall 1x1 half",
    "floor": "castle floor 1x1",
    "slope": "Haunted roof 1x1",
    "corner": "haunted roof corner out tip",
    "inner": "haunted roof corner inner tip",
    "cap": "haunted roof 1x1 flat",
    "coping": "Top 1x1 flare out",
}


def lay_bay(out, P, ox: int, oz: int, treatment: str,
            long_: int = BAY_LONG, across: int = BAY_ACROSS) -> None:
    cells = {(x, z) for x in range(long_) for z in range(across)}
    floor, wall = P["floor"], P["wall"]
    wall_h = wall.size_y

    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))
    for level in range(WALL_COURSES):
        y = level * wall_h
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) not in cells:
                    out.append(place_wall(wall, ox + x, oz + z, side, y))

    roof_y = WALL_COURSES * wall_h
    slope, corner, inner, cap = (P["slope"], P["corner"], P["inner"], P["cap"])
    rise = slope.size_y
    edge_off, corner_off = roof_offsets(slope)

    if treatment == "hip":
        rings = _roof_rings(cells)
        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, slope, corner, cap, inner,
                                     edge_off=edge_off, corner_off=corner_off)
            if piece is not None:
                out.append(place_tile(piece, ox + x, oz + z,
                                      roof_y + r * rise, rot % 24))
        return

    # -- gabled ---------------------------------------------------------------
    cpc = roof_course_cells(slope)
    courses = roof_courses(cells, "x", cpc)
    anchors = roof_course_anchors(courses, "x", cpc)
    xs = [c[0] for c in cells]
    lo_x, hi_x = min(xs), max(xs)
    crow = treatment.startswith("crow")

    for (x, z), (course, fall) in sorted(anchors.items()):
        if crow and x in (lo_x, hi_x):
            continue                # the parapet IS the end; see the docstring
        out.append(place_tile(slope, ox + x, oz + z, roof_y + course * rise,
                              (ROOF_EDGE_ROT[fall] + edge_off) % 24))
    for (x, z), (course, fall) in sorted(courses.items()):
        if fall is not None:
            continue
        if crow and x in (lo_x, hi_x):
            continue
        out.append(place_tile(cap, ox + x, oz + z,
                              roof_y + course * rise - cap.size_y))

    # -- the gable end --------------------------------------------------------
    step, coping = P["step"], P["coping"]
    for (x, z), (course, _fall) in sorted(courses.items()):
        if x not in (lo_x, hi_x):
            continue
        side = "w" if x == lo_x else "e"
        # FLUSH stops at the roof line. CROW carries one course past it AND
        # owns the cell, so the stack is continuous and nothing rises between
        # the steps.
        proud = 1 if crow else 0
        n = int(round((course + proud) * rise / step.size_y))
        for k in range(n):
            out.append(place_wall(step, ox + x, oz + z, side,
                                  roof_y + k * step.size_y))
            if crow:
                # The inner face too, so the parapet is a full cell thick and
                # the end is closed rather than a 0.5 fin with daylight behind.
                out.append(place_wall(step, ox + x, oz + z,
                                      "e" if side == "w" else "w",
                                      roof_y + k * step.size_y))
        if treatment == "crow-coped" and coping is not None:
            out.append(place_tile(coping, ox + x, oz + z,
                                  roof_y + n * step.size_y - coping.size_y))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--districts", type=int, default=None, metavar="SEED",
                    help="lay one bay per QUARTER instead of one per "
                         "treatment, dealt by `build.gable_end_for` at this "
                         "seed. All the gable ends face west, so one "
                         "square-on elevation holds every district at once "
                         "-- which is the view a crow-step is judged in and "
                         "the one every earlier reading of it lacked.")
    ap.add_argument("--long", type=int, default=BAY_LONG, dest="long_",
                    help="length along the ridge. Small for an end "
                         "elevation -- it is depth into the screen there.")
    ap.add_argument("--across", type=int, default=BAY_ACROSS,
                    help="width of the gable end. This is what the elevation "
                         "actually shows, and what the frame has to hold.")
    ap.add_argument("--only", type=str, default=None,
                    help="with --districts, a comma-separated subset of "
                         "quarters. Six across does not frame; three does.")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)

    P = {}
    for role, name in PIECES.items():
        a = byname.get(name)
        if a is None:
            ap.error(f"not in this catalog: {name!r} (for {role})")
        P[role] = a
    for role in ("slope", "corner", "inner", "cap", "coping"):
        a = P[role]
        if (a.size_x, a.size_z) != (1.0, 1.0):
            ap.error(f"{role} {a.name!r} is {a.size_x:g}x{a.size_z:g} cells "
                     f"and is laid per cell; it would overhang")

    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or P["floor"]

    out: list = []

    if args.districts is not None:
        # One bay per quarter, laid in a ROW ACROSS. Every ridge runs along x
        # and every gable end faces west, so a single camera square-on to the
        # west wall holds all of them -- no orbiting, no per-bay framing, and
        # nothing judged from an oblique.
        quarters = sorted(GABLE_ENDS)
        if args.only:
            want = {q.strip() for q in args.only.split(",")}
            unknown = want - set(quarters)
            if unknown:
                ap.error(f"unknown quarter(s): {', '.join(sorted(unknown))}")
            quarters = [q for q in quarters if q in want]
        band = args.across + 1
        width, depth = args.long_, len(quarters) * band - 1

        for qi, q in enumerate(quarters):
            deal = gable_end_for(q, args.districts)
            lay_bay(out, P, 0, qi * band, BY_DEAL[deal],
                    args.long_, args.across)
            # Numbered on the ground WEST of each bay, in front of its own
            # gable, so the elevation shot carries its own labels.
            for k in range(qi + 1):
                out.append(place_tile(marker, -2, qi * band + k, 0.0))

        for dz in range(-MARGIN, depth + MARGIN - 1):
            for dx in range(-MARGIN - 2, width + MARGIN):
                out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

        print(f"# one bay per quarter at seed {args.districts}, "
              f"gable ends all facing WEST", file=sys.stderr)
        for qi, q in enumerate(quarters):
            print(f"#   west bar of {qi + 1}: {q:12} -> "
                  f"{gable_end_for(q, args.districts)}", file=sys.stderr)
        print(f"#   END ELEVATION frame: --frame "
              f"{-MARGIN - 2},{-MARGIN},{width + MARGIN},{depth + MARGIN - 1}",
              file=sys.stderr)
        print("#   Point the camera SQUARE-ON to the west wall. A crow-step "
              "is an outline and every oblique flatters it.", file=sys.stderr)
    else:
        cols = max(1, args.cols)
        rows = (len(TREATMENTS) + cols - 1) // cols
        pitch, band = BAY_LONG + BAY_GAP, BAY_ACROSS + BAND_GAP
        width, depth = cols * pitch, rows * band

        for ti, treat in enumerate(TREATMENTS):
            ox, oz = (ti % cols) * pitch, (ti // cols) * band
            lay_bay(out, P, ox, oz, treat)
            for k in range(ti + 1):
                out.append(place_tile(marker, ox + k, oz - 2, 0.0))

        for dz in range(-MARGIN - 2, depth + MARGIN - 1):
            for dx in range(-MARGIN, width + MARGIN - BAY_GAP):
                out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

        print(f"# civic fabric: {len(TREATMENTS)} gable-end treatments, "
              f"{BAY_LONG}x{BAY_ACROSS}", file=sys.stderr)
        for i, t in enumerate(TREATMENTS):
            print(f"#   bar of {i + 1} to its south: {t}", file=sys.stderr)
        print("#   READ FROM THE END at eye level -- a crow-step is an "
              "OUTLINE, and plan view shows an outline as a line",
              file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
