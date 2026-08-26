"""A design pass on fencing, gates and materials -- built to be walked around.

The palisade barricade has now been wrong on a board three times, each time in
a way its collider said was impossible:

  * corner bundles between wall panels, because a rasterised diagonal step was
    read as a corner
  * a see-through circuit, because two full-cell pieces stepping corner to
    corner touch at a *point*
  * and, in the close-up that prompted this file, a diagonal that reads as a
    STAIRCASE OF SEPARATE PANELS with their ends showing -- after both of
    those were fixed

The common thread is the rule `CLAUDE.md` already states for
`md_wall_1x1_diag_01`: **a full-cell collider does not mean a full-cell
mesh.** `Palisade wall tall 1x2` measures 1.0 x 2.0 x 1.0, so
`build.is_curtain_piece` calls it a block and it is laid one per cell -- but
the visible stake wall is a thin plane with a bracing frame behind it, which
is why a stepped run shows its own ends.

**Five specimens, in ONE ROW, west to east.** The first cut of this file laid
twelve in a grid, each named by a bar of N cells on the ground, and at the
framing `review.ps1 360` gives, the bars could not be counted -- so no
specimen could be attributed to a candidate, which makes the probe worthless
(this project's own rule, learned on `roofrot_probe`). Position is the label
now: read them left to right.

    1  blocks + diagonal connectors     THE SHIPPED ONE, the control
    2  the run laid two cells thick
    3  laid as a curtain on the outward cell face
    4  the same, in `Palisade wall tall 2x2`
    5  along the SURVEYED BEARING, the way every prop fence is laid

Specimen 5 is the one to look hardest at. Drystone walls laid that way are
recorded in `docs/fencing.md` as "continuous ribbons ... no stair-step, no
comb, no fins", and the only reason the palisade is not laid that way is that
its pieces are tiles and the off-grid canary refuses a tile off the half-tile
lattice. If 5 is the one that reads, the question stops being "how do we
stair-step better" and becomes "is that canary right to refuse a boundary
piece, given it already exempts every prop for the same reason".

    python tools/fence_probe.py > out/fenceprobe.slab.txt
    .\tools\review.ps1 360 -Name fence -Slab out\fenceprobe.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith import raster as R
from citysmith.build import (_close_diagonals, place_centered, place_tile,
                             place_wall, run_along_polyline)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

CELL = 20          #: pad edge, in tiles
GAP = 4            #: bare board between pads, so each reads on its own
GRADE = 0.5        #: top of the ground course

#: The line every specimen builds, in pad-local tiles: a straight tail, a 45
#: degree diagonal, another straight tail. The diagonal is what has failed
#: three times; the tails are there so each frame contains a stretch that is
#: known to work, as a control inside the specimen itself.
LINE = [(2.5, 3.5), (6.5, 3.5), (13.5, 10.5), (17.5, 10.5)]

cat = load_or_build()
pal = Palette(cat, MEDIEVAL, 33)


def asset(name: str):
    got = [a for a in cat.assets if a.name == name]
    if not got:
        raise SystemExit(f"no such asset: {name!r}")
    return got[0]


GROUND = pal.require("ground")
MARK = asset("castle floor 1x1")
PALISADE = asset("Palisade wall tall 1x2")
WIDE = asset("Palisade wall tall 2x2")
GATE = asset("Doors - Palisade")

placements: list = []


def pad(ox: int, oz: int, n: int) -> None:
    """Ground under a specimen, and a bar of ``n`` cells as a backup label.

    Position is the real label -- see the module docstring -- but the bar
    costs nothing and settles it if a frame ever gets cropped.
    """
    for z in range(CELL):
        for x in range(CELL):
            placements.append(place_tile(GROUND, ox + x, oz + z, 0.0))
    for i in range(n):
        placements.append(place_tile(MARK, ox + 1 + i, oz + CELL - 2, GRADE))


def cells_of(width: float = 1.0):
    return sorted(set(R._stroke_line(LINE, width, CELL, CELL)))


def dominant(cells: set, x: int, z: int) -> bool:
    sx = sum(1 for d in (-2, -1, 1, 2) if (x + d, z) in cells)
    sz = sum(1 for d in (-2, -1, 1, 2) if (x, z + d) in cells)
    return sx >= sz


def lay_blocks(ox, oz, cells, piece) -> None:
    """One piece per cell, as a block -- what ships today."""
    cs = set(cells)
    for x, z in sorted(cs):
        placements.append(place_tile(piece, ox + x, oz + z, GRADE,
                                     0 if dominant(cs, x, z) else 6))


def lay_curtain(ox, oz, cells, piece) -> None:
    """On every cell EDGE the run does not continue through.

    The hypothesis: if the mesh is a thin plane rather than a filled cell then
    it belongs on a boundary, and a stepped run closes because each piece
    sits on the face its neighbour is missing. This is the same correction
    that took the rampart from a curtain piece per cell to a solid core with
    panels hung on the faces that show.
    """
    cs = set(cells)
    for x, z in sorted(cs):
        for side, dx, dz in (("n", 0, -1), ("s", 0, 1), ("w", -1, 0), ("e", 1, 0)):
            if (x + dx, z + dz) not in cs:
                placements.append(place_wall(piece, ox + x, oz + z, side, GRADE))


def lay_surveyed(ox, oz, piece) -> None:
    """Along the true bearing, the way every prop fence on the map is laid."""
    panels, _ = run_along_polyline([(ox + x, oz + z) for x, z in LINE])
    for cx, cz, rot in panels:
        placements.append(place_centered(piece, cx, cz, GRADE, rot))


SPECIMENS = [
    # **Two, side by side, and nothing else.** Five in a row did not fit the
    # frame `review.ps1 360` gives and twelve in a grid could not be
    # attributed; both times the probe answered nothing. The decisive question
    # is one comparison, so the board carries one comparison and left-versus-
    # right is the label.
    #
    # And it has to be judged by EYE. `tools/fence_seal.py` scores every
    # cell-laid strategy here at 100% sealed, including the one with no
    # connectors at all, because `_Occupancy` reads collider bounds and the
    # defect is in the mesh. That is the same blind spot `md_wall_1x1_diag_01`
    # exploited, and it is why this file exists rather than a unit test.
    ("WEST: blocks + connectors (SHIPPED)",
     lambda ox, oz: lay_blocks(ox, oz, _close_diagonals(set(cells_of(1.0))), PALISADE)),
    ("EAST: curtain on the outward face",
     lambda ox, oz: lay_curtain(ox, oz, _close_diagonals(set(cells_of(1.0))), PALISADE)),
]

for i, (label, fn) in enumerate(SPECIMENS):
    ox = i * (CELL + GAP)
    pad(ox, 0, i + 1)
    fn(ox, 0)

# A sixth pad for the gates, set apart to the south so it is never confused
# with the diagonal sweep: a straight run with the kit's gate in it, beside
# the same run with the opening left bare.
gx = 0
gz = CELL + GAP
pad(gx, gz, 6)
straight = [(x, 3) for x in range(2, 18)]
gap_at = {(9, 3), (10, 3)}
lay_blocks(gx, gz, [c for c in straight if c not in gap_at], PALISADE)
placements.append(place_centered(GATE, gx + 10.0, gz + 3.25, GRADE, 0))
bare = [(x, 12) for x in range(2, 18)]
lay_blocks(gx, gz, [c for c in bare if c not in {(9, 12), (10, 12)}], PALISADE)

print(encode(Slab(placements=placements).normalized()))
print(f"# {len(SPECIMENS) + 1} pads, {len(placements)} placements", file=sys.stderr)
print("# west to east:", file=sys.stderr)
for i, (label, _) in enumerate(SPECIMENS):
    print(f"#   {i + 1}  {label}", file=sys.stderr)
print("#   6  (north) gates: with a gate above, opening left bare below",
      file=sys.stderr)
