"""Every plausible spire assembly the tall family can make, on one board.

**The question is proportion.** The shipping spire is four `Tall 2x2x4 Corner
out` on a 4x4 -- 20 ft tall on a 20 ft base, a 45-degree cap -- and the
architectural review's standing complaint is that a broach spire (Ketton) is
nearer 3:1. The kit has pieces nobody has tried: two more 4-tall corner
variants, and `Tall 1x1x4`, a half-width steep plane that might close a 2x2
NEEDLE -- and a needle rising out of the 4x4 cap's skirt is the actual broach
form, the same lap-into-the-roof trick the chimney uses.

Six bays, west to east, each on an identical two-course 4x4 stub so the only
variable is the assembly. Numbered by bars of N cells running east on the
ground -- a hip is judged in plan and a tally stack vanishes from overhead.

    1  control   the shipping cap (hand-build rotations; the known-good bay)
    2  round     cap from `Tall 2x2x4 corner round`
    3  unique    cap from `Tall 2x2x4 corner out unique`
    4  needle    four `Tall 1x1x4` as the faces of a 2x2, EDGE rotations
    5  broach    cap + needle buried two courses into it, emerging above
    6  spike     cap + needle buried one course -- the tallest silhouette

Bay 4 is the measurement the rest depend on: `Tall 1x1x4` is a SLOPE, so the
hypothesis is the edge table plus the kit's edge offset (+6), which has never
been tried on this family -- the corner pieces took the corner table plus 0,
settled by the user's hand-build. If bay 4 shows fins from any angle, bays 5
and 6 are automatically dead and the sweep says so without a second board.

Known-good control on the board, as always: a probe with no calibration bay
cannot tell a bad assembly from a bad camera.

    python tools/spire_probe.py > out/spire/tiers.slab.txt
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from citysmith.build import (  # noqa: E402
    Builder, ROOF_CORNER_ROT, ROOF_EDGE_ROT, ROOF_ROT_OFFSET, place_tile,
)
from citysmith.catalog import load_or_build  # noqa: E402
from citysmith.palette import Palette  # noqa: E402

SIDE = 4          # the stub, matching the real tower
GAP = 3
STUB = 2          # courses of block under each assembly

EDGE_OFF, CORNER_OFF = ROOF_ROT_OFFSET["castle fortified"]


def cap(b, byname, piece_name, ox, oz, y):
    """The four-quadrant cap, at the hand-build's rotations."""
    piece = byname[piece_name]
    half = SIDE // 2
    for dx, dz, quad in ((0, 0, "nw"), (half, 0, "ne"),
                         (0, half, "sw"), (half, half, "se")):
        b.add(place_tile(piece, ox + dx, oz + dz, y,
                         (ROOF_CORNER_ROT[quad] + CORNER_OFF) % 24))
    return piece.size_y


def needle(b, byname, ox, oz, y, off):
    """Four `Tall 1x1x4` as the four faces of a 2x2, at edge table + ``off``.

    The first probe used the kit's own edge offset (+6) and produced a
    crossed X of open planes -- and the arithmetic says why before the board
    does: at +6 the north and south pieces keep a 1x2 footprint where a north
    face needs 2x1. Offset 0 yields the right FOOTPRINT on all four faces;
    whether the slopes then face outward is the mesh question, so the offset
    is swept rather than asserted. Each face piece is placed at the min
    corner of its post-rotation footprint.
    """
    piece = byname["Tall 1x1x4"]
    for side_name, x, z in (("n", ox, oz), ("s", ox, oz + 1),
                            ("w", ox, oz), ("e", ox + 1, oz)):
        b.add(place_tile(piece, x, z, y,
                         (ROOF_EDGE_ROT[side_name] + off) % 24))
    return piece.size_y


def main() -> int:
    palette = Palette.named(load_or_build(), "medieval", 33)
    byname = {a.name: a for a in palette.catalog.assets}
    block = palette.require("city_wall_core")
    ground = palette.require("ground")
    b = Builder(palette)

    bays = ("control", "needle+0", "needle+6", "needle+12", "needle+18",
            "broach", "spike")
    width = len(bays) * (SIDE + GAP) - GAP

    with b.layer("landscape"):
        for x in range(-2, width + 2):
            for z in range(-2, SIDE + 6):
                b.add(place_tile(ground, x, z, 0.0))

    with b.layer("structure"):
        for n, bay in enumerate(bays):
            ox = n * (SIDE + GAP)
            for x in range(ox, ox + SIDE):
                for z in range(SIDE):
                    for c in range(STUB):
                        b.add(place_tile(block, x, z, 0.5 + c * block.size_y))
            top = 0.5 + STUB * block.size_y

            if bay == "control":
                cap(b, byname, "Tall 2x2x4 Corner out", ox, 0, top)
            elif bay.startswith("needle"):
                needle(b, byname, ox + 1, 1, top, int(bay.split("+")[1]))
            elif bay == "broach":
                rise = cap(b, byname, "Tall 2x2x4 Corner out", ox, 0, top)
                # Buried half its height in the cap, emerging above the skirt
                # -- the chimney's lap trick, at spire scale. Offset 0 is the
                # footprint-correct hypothesis; if a different needle bay
                # closes, rebuild with that one.
                needle(b, byname, ox + 1, 1, top + rise / 2, 0)
            elif bay == "spike":
                rise = cap(b, byname, "Tall 2x2x4 Corner out", ox, 0, top)
                needle(b, byname, ox + 1, 1, top + rise - 1.0, 0)

            # The numbering bar: n+1 cells running east, south of the bay.
            for k in range(n + 1):
                b.add(place_tile(block, ox + k, SIDE + 2, 0.5))

    sys.stderr.write("bays: %s\n" % ", ".join(
        "%d=%s" % (i + 1, n) for i, n in enumerate(bays)))
    sys.stderr.write("board %dx%d, needle hypothesis: edge table %+d\n"
                     % (width, SIDE + 8, EDGE_OFF))
    print(b.to_slab().encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
