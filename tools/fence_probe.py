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

import math
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


def bearing_rot(cs, x, z) -> int:
    """The run's local bearing here, as one of the 24 rotation steps.

    **A 1x1 tile may be turned to any of the 24 steps and stay on the
    half-tile lattice** -- `rotated_footprint` returns 1.000 x 1.000 at every
    step, so the min corner never moves. Fractional POSITION is what breaks
    the off-grid canary (the 166 failures that sent the palisade onto cells in
    the first place); fractional ROTATION costs nothing. Those two were
    conflated, and the conflation is why this was never tried.

    rot=0 lays the stake plane east-west, so the step is just the bearing in
    15 degree units. On a 45 degree run every piece turns to step 3 and their
    planes are collinear -- a continuous diagonal wall with no stair-step to
    patch.
    """
    nb = [(nx, nz) for nx in (x - 1, x, x + 1) for nz in (z - 1, z, z + 1)
          if (nx, nz) in cs and (nx, nz) != (x, z)]
    if not nb:
        return 0
    # **The direction is a LINE through the neighbours, not a vector sum.**
    # Summing offsets makes the two neighbours of any straight or diagonal run
    # cancel exactly, which sent every cell to the axis fallback and produced
    # a probe with no 45 degree rotation in it at all -- caught by counting
    # the rotations before pasting, not by looking at the board.
    if len(nb) >= 2:
        far = max(((a, b) for i, a in enumerate(nb) for b in nb[i + 1:]),
                  key=lambda ab: (ab[0][0] - ab[1][0]) ** 2 + (ab[0][1] - ab[1][1]) ** 2)
        dx, dz = far[0][0] - far[1][0], far[0][1] - far[1][1]
    else:
        dx, dz = nb[0][0] - x, nb[0][1] - z
    # A wall plane is undirected, so fold onto a half turn; the facing is a
    # separate question the ring's centroid answers.
    return int(round(math.degrees(math.atan2(dz, dx)) / 15.0)) % 12


def lay_bearing(ox, oz, cells, piece) -> None:
    """One piece per cell, each turned to the run's local bearing."""
    cs = set(cells)
    for x, z in sorted(cs):
        placements.append(place_tile(piece, ox + x, oz + z, GRADE,
                                     bearing_rot(cs, x, z)))


def lay_diag_piece(ox, oz, cells, straight, diag) -> None:
    """A purpose-built diagonal blade on the stepped cells, straight elsewhere.

    `md_wall_1x1_diag_01` is a blade cutting its cell corner to corner --
    which `CLAUDE.md` records as the reason it was thrown out as a rampart
    MASS, and which is exactly what a diagonal boundary is. Stone, not timber,
    so this tests the idea and not the material.
    """
    cs = set(cells)
    for x, z in sorted(cs):
        rot = bearing_rot(cs, x, z)
        if rot % 6 == 0:
            placements.append(place_tile(straight, ox + x, oz + z, GRADE, rot))
        else:
            placements.append(place_tile(diag, ox + x, oz + z, GRADE,
                                         (rot // 6) * 6))


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
    ("1 blocks + connectors (SHIPPED)",
     lambda ox, oz: lay_blocks(ox, oz, _close_diagonals(set(cells_of(1.0))), PALISADE)),
    ("2 curtain on every outward face",
     lambda ox, oz: lay_curtain(ox, oz, _close_diagonals(set(cells_of(1.0))), PALISADE)),
    ("3 one per cell, turned to the run bearing",
     lambda ox, oz: lay_bearing(ox, oz, cells_of(1.0), PALISADE)),
    ("4 bearing + diagonal connectors",
     lambda ox, oz: lay_bearing(ox, oz, _close_diagonals(set(cells_of(1.0))), PALISADE)),
    ("5 md diagonal blade on the steps (STONE)",
     lambda ox, oz: lay_diag_piece(ox, oz, cells_of(1.0),
                                   asset("md_wall_1x1_01"), asset("md_wall_1x1_diag_01"))),
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
