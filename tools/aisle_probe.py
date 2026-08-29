"""Aisle posts inside a barn, read through the open end -- because that is how
anyone will ever see them.

A great barn is aisled: two rows of posts down its length carry the roof, which
is what lets the span be wide without needing roof timbers the length of the
building. `docs/great-buildings.md` §2 wants them for two reasons, and only one
is structural honesty -- the other is that the aisle is a **room a party fights
in**, and it is the first interior anyone sees through an open cart door.

Renders were read first (`tools/asset_shots.py`), which ruled one candidate in
and one out before any slab existed:

    Harbor Beam 01     plain round timber post, full height   -- a barn post
    Dungeon Pillar     square stone pier, moulded cap and base -- an ARCADE
                       pier. Right for the market hall's undercroft, wrong
                       inside a timber barn.

**`Harbor Beam 01` is two different assets and the name does not say which.**
The `Harbor` one is 0.5 x 2 x 0.5; the `Misc. Exterior` one is 0.2 x 2 x 0.2.
That is the difference between a post and a stick, and it is the quantified
form of CLAUDE.md's "nothing should be keyed on a name". Both are in the sweep,
pinned by **id**, so the board says which.

The other half of the question is height, and it is the one a render cannot
touch. A barn's eaves want 4 tiles (20 ft, `docs/great-buildings.md` §1.1) and
every post candidate is 2 tiles tall, so a post has to be **stacked** -- and a
stacked post either reads as one timber or shows a joint halfway up. There is
no third outcome and no way to guess which.

**The bays, numbered by a bar on the ground:**

    1  NONE      no posts. The control: if the span reads fine empty, the
                 posts are decoration and this whole task is optional.
    2  BEAM-HARBOR   `Harbor Beam 01` (Harbor, 0.5 thick), stacked to eaves
    3  BEAM-SLIM     `Harbor Beam 01` (Misc. Exterior, 0.2 thick), stacked
    4  PILLAR-STONE  `Dungeon Pillar`, stacked -- the deliberate wrong kit,
                     kept in frame as a control the way CLAUDE.md's probe
                     standard requires, so every shot contains a failure to
                     calibrate against.

Each bay is a barn shell with **the south gable left open**, so the camera can
look straight down the length of it from outside at eye level without needing
the cut box. `ts.ps1 cutbox` is still the right tool for the section, and it is
a persistent toggle that survives a new board -- turn it off afterwards.

    python tools/aisle_probe.py > out/aisle.slab.txt
    python tools/camera_aim.py --slab out/aisle.slab.txt --at 0,0,45,0,74
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    SIDE_OFFSETS, _normalized_whole_tiles, place_tile, place_wall,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: One barn: long enough for two bays of posts, wide enough to have an aisle
#: either side of a cart way. 5 across is a 25 ft span, which is what an
#: aisled barn's centre actually is.
BAY_LONG = 9
BAY_ACROSS = 5
BAY_GAP = 2
BAND_GAP = 3
MARGIN = 1

#: Wall courses. TWO, not one, and the arithmetic is the point: a course is
#: the 2.0-tile wall piece, so two courses is 4 tiles and **20 ft** -- the
#: measured eaves height of a great barn (`docs/great-buildings.md` §1.1).
#: One course is the 10 ft the town builds today, which is the defect, and
#: posts under a 10 ft eaves answer a question nobody asked. Four courses is
#: 40 ft, which is a ridge height and not an eaves height; I wrote 4 first.
WALL_COURSES = 2

#: Post rows sit one cell in from each long wall, which is where an aisle
#: arcade goes -- against the wall is a buttress, in the middle is a spine.
POST_INSET = 1
POST_PITCH = 3          #: cells between posts along the length


def post_candidates(cat):
    """The four treatments, resolved by **id** where a name is ambiguous.

    `Harbor Beam 01` exists twice at two thicknesses. Resolving it by name
    picks whichever the catalog happened to list first, which is the failure
    this project records as "371 of 3,200 assets share a name".
    """
    byid = {a.id: a for a in cat.assets}
    beams = sorted((a for a in cat.assets if a.name == "Harbor Beam 01"),
                   key=lambda a: -a.size_x)
    pillar = next((a for a in cat.assets if a.name == "Dungeon Pillar"), None)
    out = [("none", None)]
    if len(beams) >= 1:
        out.append((f"beam-{(beams[0].folder or '?').lower()}", beams[0]))
    if len(beams) >= 2:
        out.append((f"beam-{(beams[1].folder or '?').lower()}", beams[1]))
    out.append(("pillar-stone", pillar))
    return [(n, a) for n, a in out if n == "none" or a is not None], byid


def lay_bay(out, palette, pieces, ox: int, oz: int, post) -> None:
    """A four-course barn shell, south gable open, with a post row each side."""
    cells = {(x, z) for x in range(BAY_LONG) for z in range(BAY_ACROSS)}
    wall = pieces["wall"]
    floor = pieces["floor"]
    wall_h = wall.size_y

    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))

    open_z = 0          # the south row: its south wall is the cart opening
    for level in range(WALL_COURSES):
        y = level * wall_h
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) in cells:
                    continue
                if side == "n" and z == open_z:
                    continue            # the open gable end
                # place_wall, not place_tile: it insets the thin axis onto the
                # cell boundary and takes the quarter turn from which axis is
                # thin. Hand-rolling that put the gable probe's whole shell a
                # half tile off and doubled every segment.
                out.append(place_wall(wall, ox + x, oz + z, side, y))

    if post is None:
        return
    eaves = WALL_COURSES * wall_h
    for x in range(POST_PITCH, BAY_LONG - 1, POST_PITCH):
        for z in (POST_INSET, BAY_ACROSS - 1 - POST_INSET):
            y = 0.0
            # Stacked to the eaves. The joint is the measurement: a post that
            # reads as one timber and a post that shows a band at 10 ft are
            # the two outcomes, and only the board can say which.
            while y + post.size_y <= eaves + 1e-6:
                out.append(place_tile(post, ox + x, oz + z, y))
                y += post.size_y


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cols", type=int, default=2,
                    help="bays per row. 2 keeps the board inside one frame.")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    cat = palette.catalog
    cands, byid = post_candidates(cat)

    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)
    pieces = {
        "wall": byname.get("Rural Wall 01") or palette.require("wall"),
        "floor": palette.require("floor"),
    }
    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or pieces["floor"]

    out: list = []
    cols = max(1, args.cols)
    rows = (len(cands) + cols - 1) // cols
    pitch = BAY_LONG + BAY_GAP
    band = BAY_ACROSS + BAND_GAP
    width, depth = cols * pitch, rows * band

    for dz in range(-MARGIN - 1, depth + MARGIN - 1):
        for dx in range(-MARGIN - 1, width + MARGIN - 1):
            out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

    for i, (name, post) in enumerate(cands):
        ox = (i % cols) * pitch
        oz = (i // cols) * band
        lay_bay(out, palette, pieces, ox, oz, post)
        for k in range(i + 1):
            out.append(place_tile(marker, ox + k, oz - 2, 0.0))

    print(f"# {len(cands)} post treatments, {WALL_COURSES} wall courses "
          f"({WALL_COURSES * pieces['wall'].size_y * 5:.0f} ft eaves)",
          file=sys.stderr)
    for i, (n, a) in enumerate(cands):
        if a is None:
            print(f"#   bar of {i + 1} to its south: {n}", file=sys.stderr)
        else:
            print(f"#   bar of {i + 1} to its south: {n}  "
                  f"{a.name} [{a.folder}] "
                  f"{a.size_x:g}x{a.size_y:g}x{a.size_z:g} id={a.id}",
                  file=sys.stderr)
    print("#   every bay's SOUTH gable is open -- look down the length from "
          "the south at eye level", file=sys.stderr)

    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements, board "
          f"{width + 2 * MARGIN} x {depth + 2 * MARGIN}", file=sys.stderr)


if __name__ == "__main__":
    main()
