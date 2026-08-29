"""A size-coded ground target, for measuring the camera rather than guessing it.

`citysmith/camera.py` models TaleSpire's builder camera. Half its constants
shipped as inferences off our own scripts -- `review.ps1` turns `-DX 320` four
times to photograph four faces, so a full circle is 1280 px, so 0.28 deg/px --
and an inference off a script we wrote is not a measurement of the game. This
is the target that replaces them.

Five squares of grass on a bare board, at coordinates we chose, **sized 6, 5,
4, 3 and 2 tiles** so which blob is which can never be misread. That is the
same trick the wall probes use and it is here for the same reason: a probe read
from one angle is a probe that lies, and the first thing an oblique view breaks
is the assumption that things are still in the order you laid them.

Why grass on bare board and not a pad with markings on it: the classifier is
`camerafit.is_grass`, and it keys on **colour rather than brightness**. A bare
board is neutral grey whatever the light does -- measured, (95,96,95) -- while
turf sits a long way off the blue axis at (160,170,94). A shadow moves both
down together and leaves the difference between them. The one time this
project classified a board feature on brightness, it read turf as a lit toolbar
icon and turned the build plane on while reporting that the toggle had failed.

    python tools/camera_probe.py > out/camtarget.slab.txt

writes the slab on stdout and `out/camtarget.json` -- the mark coordinates, in
descending size, which is the order `camerafit.match_by_size` expects. The
solver reads that file rather than repeating the numbers, so the target and the
thing measuring it cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith.build import _normalized_whole_tiles, place_tile
from citysmith.catalog import load_or_build
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

#: (size, low corner). Size is the identity; the low corner sets the spread.
#:
#: **12 tiles across, arrived at by measurement and three times too large
#: first.** CLAUDE.md records that a frame holds about 40 tiles at the top of
#: Ctrl+scroll, and a 40-tile target was built on that. Driving it found three
#: things that note does not cover:
#:
#: * That 40 is an **oblique** figure -- the far half of a tilted frame covers
#:   a lot of ground. Near plan, at the top of Ctrl+scroll, the frame holds
#:   about **24 tiles** at ~40 px/tile.
#: * Climbing out of trouble does not work at an oblique: raising the eye moves
#:   the ground point under the frame centre away by `height x cot(pitch)`, so
#:   the target slides off rather than shrinking.
#: * **What has to fit is the DIAGONAL.** A 16-tile target measures 22.6 tiles
#:   corner to corner, and as the camera turns, that diagonal swings onto the
#:   frame's short axis. It fitted at one yaw and not at another, which read as
#:   a flaky solver: nine shots in a row failed with the target simply off the
#:   bottom of the frame.
#:
#: 12 tiles is 17 on the diagonal, which leaves real margin at any yaw, and
#: still spans 500-700 px on screen -- far more baseline than the fit needs
#: against sub-pixel mark centroids.
#:
#: Marks are kept **one clear tile apart at least**: two that touch, even at a
#: corner, flood-fill into a single blob and the size ranking then has four
#: marks to give five sets of coordinates to.
#:
#: The four outermost carry the fit; the inboard one is redundancy, and
#: redundancy is what makes `Fit.residual_px` mean anything at all. Four marks
#: fit a homography exactly and would report a residual of zero however wrong
#: the correspondence was.
MARKS: list[tuple[int, tuple[int, int]]] = [
    (6, (0, 0)),
    (5, (7, 0)),
    (4, (0, 8)),
    (3, (9, 9)),
    (2, (5, 7)),
]


def centres() -> list[tuple[float, float]]:
    """Mark centres in tiles, in descending size -- the matching order."""
    return [(x + s / 2.0, z + s / 2.0) for s, (x, z) in MARKS]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--json", default="out/camtarget.json",
                    help="where to write the mark coordinates")
    args = ap.parse_args()

    pal = Palette.named(load_or_build(args.catalog), "medieval", 33)
    grass = pal.require("ground")

    out = []
    for size, (x0, z0) in MARKS:
        for dz in range(size):
            for dx in range(size):
                out.append(place_tile(grass, x0 + dx, z0 + dz, 0.0))

    byid = {a.id: a for a in pal.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))

    doc = {
        "tile": grass.name,
        "marks": [{"size": s, "x": x, "z": z} for s, (x, z) in MARKS],
        "centres_by_descending_size": [list(c) for c in centres()],
        "note": ("Mark centres in tiles, largest first. camerafit.match_by_size "
                 "pairs blobs to these by rank, so the order is load-bearing."),
    }
    path = pathlib.Path(args.json)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"# {len(MARKS)} marks, {len(out)} tiles -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
