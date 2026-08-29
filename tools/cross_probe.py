"""A market cross, swept: every shaft stack against every head, on its steps.

`docs/great-buildings.md` §3.4 said "no market cross exists and no candidate has
been probed". The first half is still true -- nothing in the catalog is a
market cross -- and the second half was answered by reading renders rather than
by arguing: a cross is a **stepped base, a shaft, and a head**, and the library
has all three as separate pieces. So the question is not "which asset is a
market cross" but "which stack reads as one", and that is a sweep.

What the renders settled before any slab was built (`tools/asset_shots.py`):

    Castle Ruins Pillar - Base   1 x 1.5 x 1   round drum on a moulded foot
    Castle Ruins Pillar - Mid    1 x 0.5 x 1   a drum course
    Castle Ruins Pillar - Top    1 x 0.5 x 1   a flat round capital
    Moorgoth Buttress Spire      1 x 2.0 x 1   stepped, tapering stone pinnacle
    Moorgoth Fancy Pillar        1 x 2.18 x 1  moulded gothic shaft
    Knight Statue                0.59 x 1.98   carved figure on a square plinth
    Fountain_01                  2.02 x 2.37   carved basin, fills a 2x2

The two on the right are **monolithic alternatives**, not parts: a town square
with a statue or a fountain in it is as period as one with a cross, and both
are one placement instead of four. They are in the sweep as whole candidates so
the stacks have something to lose to.

**Columns are the shaft, rows are the head**, and both are numbered on the
ground. Column 0 and row 0 are the null cases -- no shaft, no head -- which is
what makes the grid readable: the leftmost column shows each head sitting on
the steps alone, and the bottom row shows each shaft with nothing on it. A
candidate that only works because of its neighbour shows up there.

**The steps are the part nobody will think to check.** A real market cross
stands on three or four square steps, and it is the steps that make it read as
a monument rather than as a bollard someone left in the square. They are built
here from the plaza's own paving at `--steps` courses, so the probe also
answers whether our paving tile can BE a step -- it is 0.25 thick, so four
courses is one foot, which may be far too shallow to see. That is a
measurement, not an assumption, and it is why the null column exists.

Read it from **eye level first**, which is the opposite of the gable probe and
for the opposite reason: a cross is a silhouette against the sky, and plan view
shows a monument as a dot. Then walk round it -- a stepped pinnacle is
four-sided and a statue is not, and the statue's back is a thing a party will
stand behind.

    python tools/cross_probe.py > out/cross.slab.txt
    python tools/camera_aim.py --slab out/cross.slab.txt --at 0,0,45,0,74
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: Shafts, west to east. ``None`` is the null column: the head on the steps
#: with nothing under it, which is how you find out the shaft is doing nothing.
#: Each entry is a list of (name, repeat) stacked bottom to top.
SHAFTS: tuple[tuple[str, tuple], ...] = (
    ("none", ()),
    ("ruins-pillar", (("Castle Ruins Pillar - Base", 1),
                      ("Castle Ruins Pillar - Mid", 2),
                      ("Castle Ruins Pillar - Top", 1))),
    ("fancy-pillar", (("Moorgoth Fancy Pillar", 1),)),
    ("buttress", (("Moorgoth Buttress Base", 1),)),
)

#: Heads, south to north. ``None`` is the null row.
HEADS: tuple[tuple[str, str | None], ...] = (
    ("none", None),
    ("spire", "Moorgoth Buttress Spire"),
    ("knight", "Knight Statue"),
    ("lantern", "Lantern -Small"),
)

#: Whole-piece alternatives, laid in their own row north of the grid. These
#: are not shaft-and-head at all; they are one placement that either reads as
#: a square's centrepiece or does not.
MONOLITHS = ("Fountain_01", "statue_lg_hooded_01", "Well 01")

CELL = 5              #: cells between grid positions -- room to walk round one
STEP_COURSES = 3      #: square steps under each candidate
MARGIN = 1


def lay_steps(out, paving, ox: int, oz: int, courses: int) -> float:
    """A stepped plinth, widest at the bottom, and the height of its top.

    Built out of the plaza's own paving so the probe reports whether that tile
    can be a step at all: it is a quarter-tile thick, so three courses is nine
    inches, and nine inches of relief may simply not read from eye level. If
    it does not, the finding is that a cross needs a built base rather than a
    stepped one -- which is a different task, and better found here than on a
    town board.
    """
    y = 0.0
    for c in range(courses):
        half = courses - c            # 3 -> a 5x5 pad, 2 -> 3x3, 1 -> 1x1
        for dx in range(-half + 1, half):
            for dz in range(-half + 1, half):
                out.append(place_tile(paving, ox + dx, oz + dz, y))
        y += paving.size_y
    return y


def lay_stack(out, byname, ox: int, oz: int, top: float, shaft, head) -> None:
    """The shaft's pieces bottom to top, then the head on the head of it."""
    y = top
    for name, repeat in shaft:
        a = byname.get(name)
        if a is None:
            continue
        for _ in range(repeat):
            out.append(place_tile(a, ox, oz, y))
            y += a.size_y
    if head is not None:
        a = byname.get(head)
        if a is not None:
            out.append(place_tile(a, ox, oz, y))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=STEP_COURSES,
                    help="courses in the stepped plinth (0 for none)")
    ap.add_argument("--monoliths", action="store_true",
                    help="lay the whole-piece alternatives INSTEAD of the "
                         "grid. Their own board, because the grid plus a "
                         "fifth row is 26x31 and camera_aim reports that "
                         "needs 64 tiles of slant range against a stop at 50.")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)

    # Report what is missing rather than laying a hole. A missing candidate is
    # invisible in the file and reads on the board as "that one was no good".
    wanted = [n for _, sh in SHAFTS for n, _ in sh]
    wanted += [h for _, h in HEADS if h] + list(MONOLITHS)
    missing = sorted({n for n in wanted if n not in byname})
    for n in missing:
        print(f"# MISSING from this catalog, laid as a gap: {n}", file=sys.stderr)

    paving = palette.resolve("plaza") or palette.require("street")
    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or palette.require("floor")

    out: list = []
    if args.monoliths:
        width, depth = len(MONOLITHS) * CELL, CELL
    else:
        width, depth = len(SHAFTS) * CELL, len(HEADS) * CELL

    for dz in range(-MARGIN - 1, depth + MARGIN - 1):
        for dx in range(-MARGIN - 1, width + MARGIN - 1):
            out.append(place_tile(ground, dx, dz, -ground.size_y))

    if args.monoliths:
        for mi, name in enumerate(MONOLITHS):
            a = byname.get(name)
            if a is None:
                continue
            ox = mi * CELL + 2
            top = lay_steps(out, paving, ox, 2, args.steps)
            out.append(place_tile(a, ox, 2, top))
            for k in range(mi + 1):
                out.append(place_tile(marker, ox + k, -2, 0.0))
    else:
        for ci, (sname, shaft) in enumerate(SHAFTS):
            for ri, (hname, head) in enumerate(HEADS):
                ox, oz = ci * CELL + 2, ri * CELL + 2
                top = lay_steps(out, paving, ox, oz, args.steps)
                lay_stack(out, byname, ox, oz, top, shaft, head)

        # Labels: a bar running EAST counts the shaft, a bar running NORTH
        # counts the head. Two axes, two directions, so a shot from any side
        # can be read -- which the gable probe's single bar cannot do.
        for ci in range(len(SHAFTS)):
            for k in range(ci + 1):
                out.append(place_tile(marker, ci * CELL + 2 + k, -2, 0.0))
        for ri in range(len(HEADS)):
            for k in range(ri + 1):
                out.append(place_tile(marker, -2, ri * CELL + 2 + k, 0.0))

    if args.monoliths:
        print(f"# {len(MONOLITHS)} whole-piece alternatives on "
              f"{args.steps} steps", file=sys.stderr)
        for i, n in enumerate(MONOLITHS):
            print(f"#   east bar of {i + 1}: {n}", file=sys.stderr)
    else:
        print(f"# {len(SHAFTS)} shafts x {len(HEADS)} heads on "
              f"{args.steps} steps", file=sys.stderr)
        for i, (n, _) in enumerate(SHAFTS):
            print(f"#   east bar of {i + 1}: shaft {n}", file=sys.stderr)
        for i, (n, _) in enumerate(HEADS):
            print(f"#   north bar of {i + 1}: head {n}", file=sys.stderr)
    print(f"#   READ AT EYE LEVEL FIRST -- a monument is a silhouette, and "
          f"plan view shows one as a dot", file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements, board "
          f"{width + 2 * MARGIN} x {depth + 2 * MARGIN}", file=sys.stderr)


if __name__ == "__main__":
    main()
