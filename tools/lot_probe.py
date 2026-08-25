"""Several large buildings on their own lots, side by side on one board.

The design questions about a big house on a big plot -- does the mass read as a
box, does the yard read as a yard, is the clutter doing anything -- are
comparative, and comparing needs them in one frame. Building each sample as its
own slab means one paste each and a camera move between; composing them onto
one board means one paste and one `review.ps1 360` per iteration, which is what
makes iterating cheap enough to actually do three times.

Samples are named by town and building id, chosen because they are both **large**
(>= 45 raster cells) and **standing apart** (`_standing_apart`, so they own a
yard rather than sharing a party wall). 54 buildings across the four towns
qualify; these are the interesting shapes:

    warehouse-0692   11x11 square   the pyramid-roof case
    tavern-0539      14x7 wide      a broad frontage
    temple-0002      6x19 long      a nave
    apothecary-0037  7x9            a trade with a working yard
    house-0035       9x7            the hamlet's big farmhouse

Composition is a plain x-offset on every placement, which is safe because each
sample is built from its own crop and normalised to that crop's origin.

**Every other building is erased from the crop**, and that is not tidiness. The
first two iterations were read with neighbours still standing, and the tall
three-storey block that dominated the frame -- and got written down as "the
warehouse still reads as a box" -- was a *neighbour* caught in the crop window.
The warehouse itself is utility tier, one storey, and was behaving perfectly.
A probe that includes things it is not testing is a probe that gets misread,
which is this project's oldest lesson arriving from a new direction.

    python tools/lot_probe.py --catalog catalog.json --out out/lots
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from citysmith import raster as R
from citysmith.build import build_from_tilemap, storeys_of, yard_cells
from citysmith.catalog import load_or_build
from citysmith.layout import Layout
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

#: (label, town layout stem, building id). One lot each.
SAMPLES = [
    ("square-warehouse", "tradebourne-v2", "warehouse-0692"),
    ("wide-tavern",      "tradebourne-v2", "tavern-0539"),
    ("long-temple",      "fc-v2",          "temple-0002"),
    ("trade-yard",       "fc-v2",          "apothecary-0037"),
    ("farmhouse",        "pelves-v2",      "house-0035"),
]

#: Grass either side of a lot, so each sample has its own visible edge.
MARGIN = 6

#: Grass between two samples on the composed board.
GAP = 4


def lot_window(tm, bid: str) -> tuple[int, int, int, int]:
    """The crop that holds a building and its yard, plus a margin."""
    cells = [(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.building[z][x] == bid]
    if not cells:
        raise SystemExit(f"no building {bid!r} on this map")
    yard = yard_cells(tm).get(bid, set())
    pts = cells + sorted(yard)
    x0 = min(p[0] for p in pts) - MARGIN
    z0 = min(p[1] for p in pts) - MARGIN
    x1 = max(p[0] for p in pts) + MARGIN
    z1 = max(p[1] for p in pts) + MARGIN
    x0, z0 = max(0, x0), max(0, z0)
    return x0, z0, min(tm.width - x0, x1 - x0 + 1), min(tm.depth - z0, z1 - z0 + 1)


def _isolate(tm, bid: str):
    """Erase every building except ``bid``, so only the sample is under test."""
    for z in range(tm.depth):
        for x in range(tm.width):
            other = tm.building[z][x]
            if other and other != bid:
                tm.building[z][x] = ""
                tm.surface[z][x] = R.GROUND
    tm.floors = {k: v for k, v in tm.floors.items() if k == bid}
    tm.doors = {}
    tm.perimeter = {}
    R._find_perimeters(tm, None)
    R._place_doors(tm, None)
    return tm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--layouts", default="out", help="directory holding <stem>/layout.json")
    ap.add_argument("--out", default="out/lots")
    ap.add_argument("--stem", default="lots")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--storeys", type=int, default=3)
    args = ap.parse_args()

    catalog = load_or_build(args.catalog)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    maps: dict[str, object] = {}
    composed: list = []
    cursor = 0
    print(f"{'sample':18} {'building':18} {'crop':>8} {'cells':>5} {'st':>3} "
          f"{'assets':>7} {'yard':>5}")

    for label, stem, bid in SAMPLES:
        if stem not in maps:
            layout = Layout.load(pathlib.Path(args.layouts) / stem / "layout.json")
            maps[stem] = R.rasterize(layout)
        whole = maps[stem]
        x0, z0, w, d = lot_window(whole, bid)
        tm = _isolate(whole.crop(x0, z0, w, d), bid)

        builder = build_from_tilemap(tm, Palette.named(catalog, "medieval", args.seed),
                                     storeys=args.storeys, seed=args.seed)
        yard = sum(len(c) for c in yard_cells(tm).values())
        cells = sum(1 for zz in range(tm.depth) for xx in range(tm.width)
                    if tm.building[zz][xx] == bid)
        print(f"{label:18} {bid:18} {f'{w}x{d}':>8} {cells:5} {storeys_of(tm, bid, args.storeys):3} "
              f"{len(builder.placements):7,} {yard:5}")

        for p in builder.placements:
            composed.append(dataclasses.replace(p, x=p.x + cursor))
        cursor += w + GAP

    # Normalised once over the whole composition: an edge-tapered lot dips
    # below y=0 on its own, and a slab's coordinates must all be >= 0.
    path = out / f"{args.stem}.slab.txt"
    text = encode(Slab(composed).normalized())
    path.write_text(text, encoding="utf-8")
    print(f"\n{len(composed):,} assets, {len(text):,} bytes -> {path}")
    if len(text) > 30720:
        print("  OVER THE 30,720-BYTE CAP -- drop a sample or split the board")


if __name__ == "__main__":
    main()
