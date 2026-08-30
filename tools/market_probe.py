"""Show every market-stall candidate in the shape the generator builds.

The `market_stall` role is the first role pinned by *structured queries
alone*: the machine this code was written on had no catalog, so nothing could
confirm what the Medieval Fantasy pack calls a stall -- or that it has one at
all. Whatever the queries find on this machine goes through the standard
gauntlet before it is trusted, because a probe read from one angle is a probe
that lies, and this project has paid for that three times.

**Run it with the palette the build used** (`--style`, `--seed` -- the same
flags `cli build` takes), because `Palette.resolve` seeds a choice inside the
first matching query: a broad group query can match several assets and a
different seed places a different stall. The probe shortlists *everything*
the role's queries reach, and marks which one this seed's palette will
actually place -- reading the probe under one palette and building under
another is how a board was once misread for an hour.

Per candidate, laid on its own grass pad sized from the candidate's own
measured footprint, numbered on the ground with a bar of N cobble cells
running east (a vertical tally reads wrong at an oblique and vanishes from
overhead; the bar sits ON the grass, because a marker flush with the pad is
a z-fighting smear from exactly the overhead view it exists for):

  1. **The facing rank**: the same stall at rot 0, 6, 12, 18 in a west-east
     line, each with a cobble strip laid against its south face. The one
     whose counter opens onto its strip names the mesh's front; if that is
     not the rot `_stall_rotation` picks, the fix is one constant in that
     function.
  2. **The market lane the generator actually builds**: two rows facing each
     other across a two-cell aisle, stalls stepped at their own measured
     pitch with the cross-gap `_dress_market` forces after every second
     stall, loose goods clustered in the gap -- the generator's own row
     arithmetic, so what is judged is what towns will get.

The last stall pad is the **control**: `castle merlon 1x1` laid as a stall
row. It is boarded timber that crowned a town wall in crates for eleven
revisions -- a known failure, kept in frame so every screenshot contains one
to calibrate against.

The final pad is the resolved `plaza_well` with goods around it, since the
well is the one piece of the market that stands alone.

    python tools/market_probe.py > out/marketprobe.slab.txt
    python tools/market_probe.py --seed 33 --names "Some Stall,Another Stall"

Then `review.ps1 360`: four faces at a low oblique, overhead, eye level.
Judge the aisle from eye level -- it is the walkway the party stands in.
"""

from __future__ import annotations

import argparse
import math
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    _QUARTER, _normalized_whole_tiles, _stall_rotation, place_centered,
    place_tile, rotated_footprint,
)
from citysmith.catalog import load_or_build
from citysmith.palette import Palette
from citysmith.slab import Slab, SlabError, encode

#: Known-bad control, per the wall-probe standard: keep a failure in frame.
CONTROL = "castle merlon 1x1"

GAP = 2


def stall_candidates(palette: Palette, extra: list[str]) -> list:
    """Everything the role's own queries match, in query order -- the probe
    shortlists, the palette's resolver only ever picks one (and which one
    depends on the seed, which is why the pick is marked in the listing)."""
    seen: set[str] = set()
    out = []
    for terms, kwargs in palette.style.roles.get("market_stall", ()):
        for a in palette.catalog.find(*terms, **kwargs):
            if a.id not in seen:
                seen.add(a.id)
                out.append(a)
    for name in extra:
        for a in palette.catalog.find(name=name):
            if a.id not in seen:
                seen.add(a.id)
                out.append(a)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="medieval",
                    help="palette style, as passed to `cli build`")
    ap.add_argument("--seed", type=int, default=0,
                    help="palette seed, as passed to `cli build` -- the "
                         "resolver's pick depends on it")
    ap.add_argument("--names", default="",
                    help="comma-separated extra candidates to include by name")
    args = ap.parse_args()

    palette = Palette.named(load_or_build(), args.style, args.seed)
    grass = palette.require("ground")
    cobble = palette.require("street")
    goods = [g for g in (palette.resolve("market_goods", v) for v in range(4))
             if g is not None]
    well = palette.resolve("plaza_well")
    pick = palette.resolve("market_stall")

    extra = [n.strip() for n in args.names.split(",") if n.strip()]
    stalls = stall_candidates(palette, extra)
    if not stalls:
        print("# market_stall resolved to NOTHING on this catalog -- the "
              "degraded goods-cluster market is what towns will get. "
              "Try --names with candidates from the asset library.",
              file=sys.stderr)
    control = palette.catalog.find(name=CONTROL)
    pads = stalls + (control[:1] if control else [])

    # Pad geometry is derived from the candidates' own measured footprints,
    # not hardcoded: a candidate wider or deeper than a guess would overhang
    # its pad, and a lane row for a deep stall would collide with the facing
    # rank above it. Depth per pad: facing rank at RANK_Z (which spans the
    # candidate's LONG dimension in z at the odd quarter turns), its front
    # strip against its face, then the lane -- the top row's box reaches
    # back from the aisle by up to the same long dimension, a two-cell
    # aisle, the bottom row, and a margin.
    RANK_Z = 2
    d = max(1, math.ceil(max((max(s.size_x, s.size_z) for s in pads),
                             default=1.0)))
    aisle0 = RANK_Z + 2 * d + 2       # first aisle line of the lane
    pad_d = aisle0 + 2 + d + 2
    step = d + 2.0
    pad_w = max(22, 2 + math.ceil(4 * step) + 2)

    out = []

    def pad_floor(x0: int) -> None:
        for dz in range(pad_d):
            for dx in range(pad_w):
                out.append(place_tile(grass, x0 + dx, dz, -grass.size_y))

    def bar(x0: int, n: int) -> None:
        # ON the grass (top of pad is y=0), never flush with it: a bar whose
        # top face is coplanar with the pad's is unreadable from overhead,
        # which is the one view the bar exists for.
        for t in range(n):
            out.append(place_tile(cobble, x0 + t, 0, 0.0))

    for i, stall in enumerate(pads):
        x0 = i * (pad_w + GAP)
        pad_floor(x0)
        bar(x0, i + 1)

        # 1. the facing rank: which quarter turn opens to the south strip.
        # The strip touches the stall's face (ceil of its measured depth,
        # never a guessed offset) and runs its full width, so "opens onto
        # its strip" is judged against a walkway rather than a lone cobble.
        for q in range(4):
            rot = q * _QUARTER
            sx, sz = rotated_footprint(stall, rot)
            cx = x0 + 2 + q * step + sx / 2
            out.append(place_centered(stall, cx, RANK_Z + sz / 2, 0.0, rot))
            strip_z = RANK_Z + math.ceil(sz)
            for t in range(max(1, math.ceil(sx))):
                out.append(place_tile(cobble, int(cx - sx / 2) + t,
                                      strip_z, 0.0))

        # 2. the lane the generator builds: two rows over a two-cell aisle,
        # the generator's own front/pitch arithmetic (`_dress_market`).
        ln_top, ln_bot = aisle0 - 1, aisle0 + 2
        for face, ln in ((1, ln_top), (-1, ln_bot)):
            rot = _stall_rotation(stall, "x", face)
            sx, sz = rotated_footprint(stall, rot)
            pitch = max(1, math.ceil(sx))
            front = ln + 1 - sz / 2.0 if face > 0 else ln + sz / 2.0
            x = x0 + 2
            placed = 0
            while x + pitch <= x0 + pad_w - 2 and placed < 3:
                out.append(place_centered(stall, x + pitch / 2.0, front,
                                          0.0, rot))
                x += pitch
                placed += 1
                if placed == 2 and goods:      # the cross-gap, with goods
                    g = goods[(i + placed) % len(goods)]
                    out.append(place_centered(g, x + 0.5, ln + 0.5, 0.0,
                                              _QUARTER * ((i + x) % 4)))
                    x += 1

        name = stall.name
        if control and stall is control[0]:
            name = "CONTROL " + name
        if pick is not None and stall.id == pick.id:
            name += f"  <- the build's pick under --seed {args.seed}"
        print(f"# {i + 1}: {name}  "
              f"({stall.size_x:.2f}x{stall.size_y:.2f}x{stall.size_z:.2f})",
              file=sys.stderr)

    if well is not None:
        x0 = len(pads) * (pad_w + GAP)
        pad_floor(x0)
        bar(x0, len(pads) + 1)
        out.append(place_centered(well, x0 + pad_w / 2, pad_d / 2, 0.0, 0))
        for j, g in enumerate(goods[:4]):
            out.append(place_centered(g, x0 + pad_w / 2 - 3 + 2 * j,
                                      pad_d / 2 + 3, 0.0, _QUARTER * j))
        print(f"# well pad: {well.name}", file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    try:
        print(encode(_normalized_whole_tiles(Slab(out), byid)))
    except SlabError as e:
        # `encode` enforces TaleSpire's 30,720-byte cap itself; a probe with
        # many candidates can genuinely bust it, and the failure belongs here
        # at generation, not at paste.
        raise SystemExit(f"probe too big for one slab ({e}); run it in "
                         "batches with --names")
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
