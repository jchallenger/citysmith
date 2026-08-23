"""Show how each candidate turns the corner of a common house.

The facade was deliberately pinned to one kit -- "Village is a flat plane, and
it is the family our window already comes from, so a facade is now one kit
instead of three meeting at each corner". The corner is the piece that never
got the memo: under seed 33 the wall deals `Village Roof Side Wall 01/02` and
every corner variant resolves to `Rural Corner`, so a Village cottage turns
each of its corners in Rural boarding.

Fixing it is not a lookup, because the catalog does not hold the piece we want:

  * the **Village** family is entirely `group='roof'` -- three flat panels
    (one of them the only 1-cell window in the medieval set) and no corner
    at all;
  * **Rural** and **Brick** each ship a wall *and* a matching 1x1 corner, and
    neither has a 1-cell window.

So it is a trade, and the trade is what this probe is for. The full-cell corner
is not decoration either: it exists because laying a panel along each exposed
side puts two wall ends in the same square, which is the doubled geometry that
showed on a third of the ground-course cells. Mitring is therefore a real
option with a real cost, and it is in here as its own candidate.

Each candidate is the shape the generator actually builds -- a closed
two-storey box with four outside corners and a window -- so the corners can be
read from outside, which is the only place they show. Read it the way
CLAUDE.md says to read a wall probe: four low passes at ninety degrees, then
overhead, then `N` for the section. A corner that is fine from the front and
open from the side is exactly the failure this exists to catch.

    python tools/corner_probe.py > out/cornerprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (
    CORNER_BY_SIDES, WALL_CORNER_ROT, _normalized_whole_tiles,
    place_tile, place_wall,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

#: name -> (wall, corner or None, window or None). A corner of ``None`` means
#: mitre it from the wall's own panels, which is what the generator falls back
#: to when no corner piece is usable.
CANDIDATES: list[tuple[str, str, str | None, str | None]] = [
    # Control: what the board has today. One kit for the wall, another for the
    # corner -- the thing being judged.
    ("village + Rural corner (today)",
     "Village Roof Side Wall 02", "Rural Corner",
     "Village Roof Side Wall With Window 01"),
    # Same facade, corners mitred from its own panels. Matching material, at
    # the cost of two wall ends per corner cell.
    ("village, mitred",
     "Village Roof Side Wall 02", None,
     "Village Roof Side Wall With Window 01"),
    # Complete kits: wall and corner from one family, no window in either.
    ("brick", "Brick wall 1x1", "Brick wall corner", None),
    ("brick, unique corner", "Brick wall 1x1", "Brick wall corner unique", None),
    ("rural", "Rural Wall 01", "Rural Corner", None),
    # Control: the civic pairing, which already matches. If this one reads
    # wrong the probe itself is wrong.
    ("castle (civic, matched)",
     "castle wall 1x1", "castle wall corner 1x1 base", "castle wall 1x1 window"),
]

BOX_W, BOX_D = 5, 4       #: footprint of the test building, in cells
STOREYS = 2
PAD = 2                   #: grass margin around each box
GAP = 3                   #: bare cells between candidates


def main() -> None:
    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = palette.require("floor")
    tally = byname.get("castle merlon 1x1 filler") or floor

    out: list = []
    pitch = BOX_W + 2 * PAD + GAP

    for i, (label, wall_name, corner_name, window_name) in enumerate(CANDIDATES):
        wall = byname.get(wall_name)
        if wall is None:
            print(f"# {label}: {wall_name!r} not in catalog, skipped", file=sys.stderr)
            continue
        corner = byname.get(corner_name) if corner_name else None
        if corner_name and corner is None:
            print(f"# {label}: corner {corner_name!r} not in catalog", file=sys.stderr)
        window = byname.get(window_name) if window_name else None

        ox = i * pitch
        storey_h = wall.size_y

        # Ground: a grass pad, with the candidate's number spelled out in
        # blocks in front of it so a screenshot cannot be misattributed.
        for dz in range(-PAD, BOX_D + PAD):
            for dx in range(-PAD, BOX_W + PAD):
                out.append(place_tile(grass, ox + dx, dz, -grass.size_y))
        for t in range(i + 1):
            out.append(place_tile(tally, ox + t, BOX_D + 1, 0.0))

        # The building's own floor, so the wall bases have something to sit on
        # the way they do on the board.
        for dz in range(BOX_D):
            for dx in range(BOX_W):
                out.append(place_tile(floor, ox + dx, dz, 0.0))

        top = floor.size_y
        for dz in range(BOX_D):
            for dx in range(BOX_W):
                exposed = set()
                if dx == 0:
                    exposed.add("w")
                if dx == BOX_W - 1:
                    exposed.add("e")
                if dz == 0:
                    exposed.add("n")
                if dz == BOX_D - 1:
                    exposed.add("s")
                if not exposed:
                    continue
                turn = CORNER_BY_SIDES.get(frozenset(exposed))
                for level in range(STOREYS):
                    y = top + level * storey_h
                    if turn is not None and corner is not None:
                        out.append(place_tile(corner, ox + dx, dz, y,
                                              WALL_CORNER_ROT[turn]))
                        continue
                    for side in sorted(exposed):
                        # One window per side, upstairs only, so the corner is
                        # never the thing a window is hiding.
                        face = wall
                        if (window is not None and level == 1
                                and len(exposed) == 1
                                and (dx + dz) % 3 == 1):
                            face = window
                        out.append(place_wall(face, ox + dx, dz, side, y))

        shape = f"{wall.size_x:.2f}x{wall.size_y:.2f}x{wall.size_z:.2f}"
        how = corner.name if corner is not None else "mitred from its own panels"
        print(f"# {i + 1}: {label}  wall={wall.name} ({shape})  corner={how}",
              file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
