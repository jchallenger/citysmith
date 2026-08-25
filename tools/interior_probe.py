"""The generated interior of several buildings, side by side on one board.

The furnishing rules were changed on measurements alone -- 0% of props on a
cell centre against a hand-built 0.1%, 84% on quarter turns, nothing in a
doorway -- and every one of those numbers can be right while the room still
looks wrong. `verify.feature_report` exists because a feature can be correct
and absent; this exists because a feature can be correct and *ugly*, and the
only instrument for that is a board.

Same composition trick as `tools/lot_probe.py`: build each interior from its
own plan, offset it in x, and emit one slab. One paste and one
`review.ps1 360` shows every room in the set.

Interiors are built **roofless and with the levels spread side by side**, which
is what `citysmith scene` does and for the reason `docs/board-strategy.md` §2
gives: TaleSpire cannot hide an upper floor, so a stacked building is one
visible attic and two rooms the camera has to be flown inside to use.

    python tools/interior_probe.py --catalog catalog.json --layout out/tradebourne-v2/layout.json
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import interior as I
from citysmith.build import INTERIOR_DENSITY, build_interior
from citysmith.catalog import load_or_build
from citysmith.layout import Layout
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

#: One building per trade, so each interior vocabulary is on show. Picked large
#: enough to have several rooms -- a two-room cottage says nothing about
#: furnishing.
SAMPLES = ("tavern", "temple", "warehouse", "smithy", "shop", "house")

#: Bare board between two interiors, so the eye separates them.
GAP = 6

#: Laid as a grid, not a row. Six interiors in a line is 337 tiles, and
#: zoom-out is capped well short of that -- a probe that does not fit one frame
#: gets read a piece at a time, which is the reading failure the whole 360
#: recipe exists to stop.
COLS = 3


def pick(layout: Layout, kind: str, min_cells: int = 45):
    """The largest building of ``kind``, so the plan has rooms to furnish."""
    from citysmith.ftg import oriented_extent

    best = None
    for b in layout.buildings:
        if b.kind != kind:
            continue
        long_side, short = oriented_extent(b.ring)
        area = long_side * short
        if area < min_cells:
            continue
        if best is None or area > best[0]:
            best = (area, b)
    return best[1] if best else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--layout", default="out/tradebourne-v2/layout.json")
    ap.add_argument("--out", default="out/interiors")
    ap.add_argument("--stem", default="interiors")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--density", type=float, default=INTERIOR_DENSITY,
                    help="props per room cell asked for; see build.INTERIOR_DENSITY")
    ap.add_argument("--kinds", default=",".join(SAMPLES))
    ap.add_argument("--cols", type=int, default=COLS,
                    help="grid width; fewer columns makes a squarer board")
    args = ap.parse_args()

    cols = max(1, args.cols)
    catalog = load_or_build(args.catalog)
    layout = Layout.load(args.layout)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    built: list[tuple[str, list, float, float]] = []
    print(f"{'kind':10} {'building':18} {'levels':>6} {'rooms':>6} {'doors':>6} "
          f"{'props':>6} {'assets':>7}")

    for kind in [k.strip() for k in args.kinds.split(",") if k.strip()]:
        building = pick(layout, kind)
        if building is None:
            print(f"{kind:10} -- none big enough in this town")
            continue
        fp = I.plan(layout, building, seed=args.seed)
        builder = build_interior(fp, Palette.named(catalog, "medieval", args.seed),
                                 seed=args.seed, prop_density=args.density,
                                 stack=False)
        props = sum(1 for p in builder.placements
                    for a in (catalog.by_id(p.asset_id),)
                    if a is not None and a.kind == "prop")
        print(f"{kind:10} {building.id:18} {fp.levels:6} {len(fp.rooms):6} "
              f"{len(fp.doors):6} {props:6} {len(builder.placements):7,}")

        xs = [p.x for p in builder.placements] or [0.0]
        zs = [p.z for p in builder.placements] or [0.0]
        built.append((kind, builder.placements,
                      max(xs) - min(xs), max(zs) - min(zs)))

    if not built:
        raise SystemExit("nothing to build")

    # Column widths and row depths from the samples themselves, so a wide
    # interior does not overlap its neighbour.
    col_w = [0.0] * cols
    row_d: list[float] = []
    for i, (_, _, w, d) in enumerate(built):
        col_w[i % cols] = max(col_w[i % cols], w)
        if i % cols == 0:
            row_d.append(0.0)
        row_d[-1] = max(row_d[-1], d)

    composed: list = []
    for i, (_, places, _, _) in enumerate(built):
        ox = sum(col_w[:i % cols]) + GAP * (i % cols)
        oz = sum(row_d[:i // cols]) + GAP * (i // cols)
        xs = [p.x for p in places] or [0.0]
        zs = [p.z for p in places] or [0.0]
        dx, dz = ox - min(xs), oz - min(zs)
        for p in places:
            composed.append(dataclasses.replace(p, x=p.x + dx, z=p.z + dz))
    path = out / f"{args.stem}.slab.txt"
    text = encode(Slab(composed).normalized())
    path.write_text(text, encoding="utf-8")
    span_x = sum(col_w) + GAP * (cols - 1)
    span_z = sum(row_d) + GAP * max(0, len(row_d) - 1)
    print(f"\n{len(composed):,} assets, {len(text):,} bytes, "
          f"{span_x:.0f}x{span_z:.0f} tiles in {len(row_d)} row(s) of {cols} -> {path}")
    if len(text) > 30720:
        print("  OVER THE 30,720-BYTE CAP -- drop a kind or split the board")


if __name__ == "__main__":
    main()
