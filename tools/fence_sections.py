"""Build one section of a town once per fence style, for a side-by-side look.

The fence styles in `build.FENCE_STYLES` differ only in which pieces go along
the line and what happens at the joints -- the geometry is identical -- so they
are only judged against each other, on the same ground, in the same light.
This writes one slab per (section, style) so a row of them can be pasted and
walked past.

Sections are named rather than passed as numbers, because the point of each one
is a *question*:

    A  288,120  four boundary runs, ten vertices, one turning 135 degrees, and
                no buildings at all -- the joint policy, with nothing to
                distract from it.
    B  648,480  nine buildings, a waterfront and 212 paved cells -- whether a
                boundary still reads at town scale and beside a quay.
    C    0,514  a main road straight through a single 48-tile run -- the gate
                gap, which is the whole of the road rule.

Why this and not `cli build` in a loop: the CLI rasterises the whole town for
every invocation, and East Tradebourne is 739x598. Rasterising once and cropping
per section takes the matrix from minutes to seconds.

    python tools/fence_sections.py --layout out/tradebourne/layout.json

`docs/fencing.md` is the design pass these are cut to answer.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import raster as R
from citysmith import verify as V
from citysmith.build import FENCE_STYLES, build_from_tilemap
from citysmith.catalog import load_or_build
from citysmith.slab import encode
from citysmith.layout import Layout
from citysmith.palette import Palette

#: name -> (crop, what it is for)
SECTIONS: dict[str, tuple[tuple[int, int, int, int], str]] = {
    "A": ((288, 120, 48, 48), "boundary geometry, no buildings"),
    "B": ((648, 480, 48, 48), "town and waterfront context"),
    "C": ((0, 514, 48, 48), "a main road through the run"),
}

#: Which styles each section is built in. A carries the whole comparison; the
#: other two only need enough to check the design in context, because every
#: extra slab is another hand-driven paste.
PLAN: dict[str, list[str]] = {
    "A": ["drystone", "drystone-plain", "drystone-corner", "drystone-tall",
          "paling", "hedge", "hedgerow"],
    "B": ["drystone", "paling", "hedgerow"],
    "C": ["drystone"],
}


def fence_length(tm) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for run in tm.fences for a, b in zip(run, run[1:]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", default="out/tradebourne/layout.json")
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--out-dir", default="out/fence")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--storeys", type=int, default=3)
    ap.add_argument("--sections", default=",".join(SECTIONS),
                    help="comma-separated section names to build")
    args = ap.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    catalog = load_or_build(args.catalog)
    layout = Layout.load(args.layout)
    print(f"{layout.name}: rasterising {layout.width:.0f}x{layout.depth:.0f} tiles once")
    whole = R.rasterize(layout)

    rows = []
    for name in [s.strip() for s in args.sections.split(",") if s.strip()]:
        crop, why = SECTIONS[name]
        tm = whole.crop(*crop)
        print(f"\n== section {name}  crop {crop}  -- {why}")
        print(f"   {len(tm.fences)} fence run(s), {fence_length(tm):.0f} tiles of boundary")
        for style in PLAN[name]:
            palette = Palette.named(catalog, "medieval", args.seed)
            builder = build_from_tilemap(tm, palette, storeys=args.storeys,
                                         seed=args.seed, fence_style=style)
            plan = builder.chunk_plan(chunk_tiles=48, by_layer=False, pack=False,
                                      skip_open_country=False)
            stem = f"{name}-{style}"
            biggest = 0
            for i, chunk in enumerate(plan.chunks):
                suffix = "" if len(plan.chunks) == 1 else f"-{chunk.label}"
                text = encode(chunk.slab)
                (out / f"{stem}{suffix}.slab.txt").write_text(text, encoding="utf-8")
                biggest = max(biggest, len(text))
            fences = _fence_pieces(builder, palette)
            dropped = [f for f in V.check_placements(builder, tm) if "overlap" in f]
            rows.append((stem, builder.stats.total, fences, biggest,
                         len(plan.chunks), dropped))
            print(f"   {stem:22} {builder.stats.total:6,} assets  "
                  f"{fences:4} fence pieces  {biggest:6,} b"
                  + ("  " + dropped[0][:60] if dropped else ""))

    print(f"\n  {'slab':22} {'assets':>7} {'fence':>6} {'bytes':>7} {'files':>6}"
          f"  overlapping-box pairs")
    for stem, total, fences, size, files, dropped in rows:
        note = dropped[0].split(" props")[0] if dropped else "none"
        print(f"  {stem:22} {total:7,} {fences:6} {size:7,} {files:6}  {note}")
    print(f"\nwrote slabs for {len(rows)} (section, style) pair(s) to {out}")
    print("Paste each on bare board, at one cursor cell, camera straight down.")


def _fence_pieces(builder, palette) -> int:
    ids = {a.id for spec in FENCE_STYLES.values()
           for role in (spec.panel, spec.post) if role
           for a in (palette.resolve(role),) if a is not None}
    return sum(1 for p in builder.placements if p.asset_id in ids)


if __name__ == "__main__":
    main()
