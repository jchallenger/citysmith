"""The great building as the town builds it today, beside the design, on one board.

Two buildings on the same footprint, and **only two things differ**:

    left   ONE wall course (10 ft eaves), HIPPED, single-course roof pieces
           -- which is exactly what `_lay_roofs` builds for a warehouse now
    right  TWO wall courses (20 ft eaves), GABLED, double-course pieces with
           the measured +6 rotation and a filled gable triangle

The kit is held constant at Tavern on purpose, so the comparison is massing
and roof form and nothing else. That is not the kit a warehouse would really
wear -- utility tier is Rural boarding -- and holding it constant is the point:
a board that changed the material too could not say which change did the work.

**It also cannot be varied, and that is a finding rather than a convenience.**
Searched across the whole library, **`Tavern` is the only kit in it that ships
a roof `end` piece at all** -- Rural, Castle Fortified, Abandoned Village,
Moorgoth and Marble Palace have none, at either scale. So a gable built from a
dedicated end piece is available to exactly one fabric, and a gabled *temple*
in dressed stone cannot be built this way at all. `docs/great-buildings.md`
§3.1 records the alternative: crow-step the end out of wall panels, which is
an authentic northern form and needs no end piece. That route is now the one
that matters for every kit except this one.

The right-hand building is the geometry proved in `PROBE gable warehouse`; the
left-hand one is the town's own roof code (`_roof_rings`, `_roof_piece`) so
the control is the real thing and not a reconstruction of it.

    python tools/great_board.py > out/great.slab.txt
    python tools/camera_aim.py --slab out/great.slab.txt --at 0,0,45,0,74
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_EDGE_ROT, SIDE_OFFSETS, _normalized_whole_tiles, _roof_piece,
    _roof_rings, place_centered, place_tile, place_wall, roof_course_anchors,
    roof_course_cells, roof_courses, roof_offsets,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: One kit, both buildings. See the module docstring for why it cannot vary.
PIECES = {
    "wall": "Tavern Wall - Small 01",
    "floor": "Tavern Floor 01",
    # single-course family: what the town roofs with today
    "slope1": "Village Roof Side 01",
    "corner1": "Village Roof Corner 01",
    "inner1": "Village Roof Inner Corner 01",
    "cap1": "Tavern Roof flat 01",
    # double-course family: the only one with an end piece
    "slope2": "Village Roof Side 02",
    "end2": "Village Roof Side End 01",
    "infill": "Village Roof Side Wall 01",
    "cap2": "Tavern Roof flat 01",
}

#: The measured double-course rotation. `ROOF_ROT_OFFSET['tavern']` is the
#: same +6, which is the whole argument that the two scales share one
#: convention -- see `docs/great-buildings.md` §3.1a.
GABLE_OFFSET = 6

GAP = 2
MARGIN = 1
_INWARD = {"n": (0, 1), "s": (0, -1), "e": (-1, 0), "w": (1, 0)}


def shell(out, P, cells, ox, oz, courses):
    """Floor and ``courses`` of wall. Identical for both buildings."""
    floor, wall = P["floor"], P["wall"]
    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))
    for level in range(courses):
        y = level * wall.size_y
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) not in cells:
                    out.append(place_wall(wall, ox + x, oz + z, side, y))


def hip_today(out, P, cells, ox, oz, roof_y):
    """The town's own roof: `_roof_rings` and `_roof_piece`, unmodified.

    Called rather than reimplemented, so the control is what the town actually
    builds. A probe that rewrites the thing it is comparing against can only
    tell you about the rewrite.
    """
    slope, corner = P["slope1"], P["corner1"]
    inner, cap = P["inner1"], P["cap1"]
    edge_off, corner_off = roof_offsets(slope)
    rings = _roof_rings(cells)
    for (x, z) in sorted(cells):
        r = rings[(x, z)]
        y = roof_y + r * slope.size_y
        fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                     if rings.get((x + dx, z + dz), -1) < r)
        piece, rot = _roof_piece(fall, slope, corner, cap, inner,
                                 edge_off=edge_off, corner_off=corner_off)
        if piece is not None:
            out.append(place_tile(piece, ox + x, oz + z, y, rot % 24))


def gable_designed(out, P, cells, ox, oz, roof_y):
    """The design: `build.roof_courses` at the piece's own scale, gabled."""
    slope, end, cap = P["slope2"], P["end2"], P["cap2"]
    infill = P["infill"]
    rise = slope.size_y
    cpc = roof_course_cells(slope)

    courses = roof_courses(cells, "x", cpc)
    anchors = roof_course_anchors(courses, "x", cpc)
    xs = [c[0] for c in cells]
    lo_x, hi_x = min(xs), max(xs)

    for (x, z), (course, fall) in sorted(anchors.items()):
        ix, iz = _INWARD[fall]
        cx = ox + x + 0.5 + ix * (cpc - 1) * 0.5
        cz = oz + z + 0.5 + iz * (cpc - 1) * 0.5
        piece = end if x in (lo_x, hi_x) else slope
        out.append(place_centered(piece, cx, cz, roof_y + course * rise,
                                  (ROOF_EDGE_ROT[fall] + GABLE_OFFSET) % 24))

    # The ridge band, capped flat by its TOP. `cap1`/`cap2` are the same
    # one-cell piece: a 2x2 cap laid per cell reaches a cell past it, which is
    # how this roof came out a unit too big to the north and east.
    for (x, z), (course, fall) in sorted(courses.items()):
        if fall is None:
            out.append(place_tile(cap, ox + x, oz + z,
                                  roof_y + course * rise - cap.size_y))

    # The gable triangle, one panel per course, stepping up toward the ridge.
    for (x, z), (course, fall) in sorted(courses.items()):
        if x not in (lo_x, hi_x):
            continue
        side = "w" if x == lo_x else "e"
        for k in range(course):
            out.append(place_wall(infill, ox + x, oz + z, side,
                                  roof_y + k * infill.size_y))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--long", type=int, default=9, dest="long_",
                    help="length along the ridge, in cells")
    ap.add_argument("--across", type=int, default=9,
                    help="span across the ridge, in cells")
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
    for role in ("cap1", "cap2"):
        a = P[role]
        if (a.size_x, a.size_z) != (1.0, 1.0):
            ap.error(f"{role} {a.name!r} is {a.size_x:g}x{a.size_z:g} cells "
                     f"and is laid per cell; it would overhang")

    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or P["floor"]
    wall_h = P["wall"].size_y

    cells = {(x, z) for x in range(args.long_) for z in range(args.across)}
    pitch = args.long_ + GAP
    width, depth = 2 * pitch, args.across

    out: list = []

    # LEFT, bar of 1: as built today. One storey, hipped.
    shell(out, P, cells, 0, 0, 1)
    hip_today(out, P, cells, 0, 0, 1 * wall_h)
    out.append(place_tile(marker, 0, -2, 0.0))

    # RIGHT, bar of 2: as designed. Two courses, gabled.
    shell(out, P, cells, pitch, 0, 2)
    gable_designed(out, P, cells, pitch, 0, 2 * wall_h)
    for k in range(2):
        out.append(place_tile(marker, pitch + k, -2, 0.0))

    for dz in range(-MARGIN - 2, depth + MARGIN):
        for dx in range(-MARGIN, width + MARGIN - GAP):
            out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

    print(f"# {args.long_}x{args.across} on one kit (Tavern), two treatments",
          file=sys.stderr)
    print(f"#   bar of 1, west: as built today -- 1 course "
          f"({wall_h * 5:.0f} ft eaves), hipped", file=sys.stderr)
    print(f"#   bar of 2, east: as designed -- 2 courses "
          f"({2 * wall_h * 5:.0f} ft eaves), gabled at +{GABLE_OFFSET}",
          file=sys.stderr)
    print(f"#   ONLY the massing and the roof form differ; the kit is held",
          file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
