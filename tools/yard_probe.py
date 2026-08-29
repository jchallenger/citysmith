r"""Yards and their boundaries: one street, four treatments, one board.

A design review of `build._lay_yards`, built so the alternatives stand on
**identical ground**. Every panel below is the same 34x34 crop of the same
town, built the same way, differing only in how the yard boundary is laid --
so anything that reads differently is the treatment and not the site.

Three sweeps, one board each:

    structure   how the boundary is laid    shipped / butted / gated / none
    style       what it is made of          paling / drystone / estate / hedge
    size        how far a yard reaches      YARD_REACH 1 / 2 / 3 / 4

What each is asking, and why it is worth a board:

* **structure.** `place_wall` centres a piece on a 1-tile cell edge, and
  `yard_fence` is `Wooden Fence` -- **2.0 tiles long**. Measured on
  Pelvesthollow: 507 of 599 panels have another panel lying on top of them
  lengthwise, because a 2-tile piece is being laid at 1-tile spacing.
  `verify._prop_collisions` excuses every one, because the minimum penetration
  of two collinear panels is the panel's own *thickness* -- which is exactly
  the corner-join allowance. So the doubling is invisible to the checks and
  costs twice the props. `butted` lays the same outline at the panel's own
  module; the pair says whether it is also invisible to the eye.
* **gated.** The way in is "any edge onto a street, lane, plaza or pier",
  which is two failures in one rule. A plot fronting a lane along its whole
  side has that whole side left out -- 29% of Graybank's yard perimeter is
  open, so the plot reads as a three-sided pen rather than an enclosure. And a
  yard reached across open grass has nothing to borrow an opening from, so it
  gets none: 17 of East Tradebourne's 230 yards and 5 of Forest Church's 15
  are sealed rings. `gated` fences the whole ring and cuts one gate, on the
  side facing the most paving.
* **style.** Every yard on every board is 3.4 ft of paling, whatever the
  trade. Field walls have seven styles; a yard has none.
* **size.** `YARD_REACH` is 2 cells for a cottage and for a warehouse alike.

Position is the label -- read them left to right -- and a bar of N cells at the
near edge of each panel says the same thing again for a frame that got cropped.

    python tools/yard_probe.py --sweep structure
    .\tools\review.ps1 360 -Name yard-str -Slab out\yardprobe\structure.slab.txt
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import build as B
from citysmith import raster as R
from citysmith.build import build_from_tilemap, place_centered, place_tile
from citysmith.catalog import load_or_build
from citysmith.layout import Layout
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

#: The sample. Found by sweeping every 32x32 window of all four towns for one
#: that holds several yards, a way running through, **and at least two yards
#: that touch no way at all** -- because the sealed-ring case is half of what
#: the gate treatment is for, and a crop without one cannot show it. Trimmed
#: to 24 so four panels fit one 30,720-byte slab and stay one paste.
#:
#: Four yards, 222 cells: two sealed rings and two fronting a lane, which is
#: both gate cases in one frame. **No town wall in the window, and the
#: buildings are one and two storeys**, both learned by pasting the first
#: pick: East Tradebourne's rampart ran diagonally through it and dominated
#: every frame, and its three-storey blocks put the yards in shadow at every
#: oblique. A probe that contains something it is not testing is a probe that
#: gets misread.
TOWN = "tradebourne-v2"
CROP = (444, 90, 34, 34)

#: Per-sweep site, where the default one cannot answer the question.
#:
#: **`size` needs open country and the town crop does not have it.** Run on the
#: Tradebourne window, `reach-4` covers 583 of its 1,024 cells and most of the
#: resulting boundary is off the crop edge, where no edge is emitted at all --
#: so the panel with the *biggest* yards showed the *least* fence, which is an
#: artifact of the window and reads as a finding. Two Pelvesthollow farmsteads
#: with 560 of 676 cells of open grass round them let a yard grow without
#: running out of map.
SITES = {
    "size": ("pelves-v2", (60, 12, 26, 26)),
    # A window with both cases in it: two farmsteads with open country, and
    # the lane that constrains what they can claim on one side.
    "variance": ("pelves-v2", (56, 8, 34, 34)),
}

#: Bare board between panels, so each reads on its own.
GAP = 5

WAYS = frozenset({R.STREET, R.PLAZA, R.LANE, R.PIER})


# ---------------------------------------------------------------- geometry

def boundary_edges(tm, cells, all_yard, *, skip_ways: bool = True
                   ) -> list[tuple[int, int, str]]:
    """The outside of one yard, as (cell x, cell z, side).

    An edge is on the boundary when the cell across it is neither yard nor
    building. `skip_ways` is the shipped rule: leave open every edge onto a
    street, lane, plaza or pier, because that is the way in. Turning it off
    gives the closed ring the `gated` treatment then cuts one gate in.
    """
    out = []
    for x, z in sorted(cells):
        for side, dx, dz in B.SIDE_OFFSETS:
            nx, nz = x + dx, z + dz
            if not tm.inside(nx, nz):
                continue
            if (nx, nz) in all_yard or tm.building[nz][nx]:
                continue
            if skip_ways and tm.surface[nz][nx] in WAYS:
                continue
            out.append((x, z, side))
    return out


def facing_ways(tm, run) -> int:
    """How many cells of this run look out onto a way."""
    side, fixed, a, b = run
    dx, dz = dict((s, (x, z)) for s, x, z in B.SIDE_OFFSETS)[side]
    n = 0
    for v in range(a, b + 1):
        x, z = (v, fixed) if side in ("n", "s") else (fixed, v)
        nx, nz = x + dx, z + dz
        if tm.inside(nx, nz) and tm.surface[nz][nx] in WAYS:
            n += 1
    return n


def straight_runs(edges) -> list[tuple[str, int, int, int]]:
    """Maximal straight runs, as (side, fixed coordinate, first, last).

    A boundary is not a line, it is a set of cell edges; chaining the collinear
    consecutive ones is what lets a 2-tile panel be laid at 2 tiles rather than
    at 1. For n/s a run travels along x at a fixed z, for e/w along z at a
    fixed x.
    """
    lanes: dict[tuple[str, int], list[int]] = {}
    for x, z, side in edges:
        key = (side, z) if side in ("n", "s") else (side, x)
        lanes.setdefault(key, []).append(x if side in ("n", "s") else z)

    runs = []
    for (side, fixed), vals in sorted(lanes.items()):
        vals.sort()
        start = prev = vals[0]
        for v in vals[1:]:
            if v == prev + 1:
                prev = v
                continue
            runs.append((side, fixed, start, prev))
            start = prev = v
        runs.append((side, fixed, start, prev))
    return runs


def run_line(side: str, fixed: int, a: int, b: int):
    """A run as a world segment: (t0, t1, boundary line, inward sign, along x)."""
    if side == "n":
        return a, b + 1, float(fixed), +1.0, True
    if side == "s":
        return a, b + 1, float(fixed + 1), -1.0, True
    if side == "w":
        return a, b + 1, float(fixed), +1.0, False
    return a, b + 1, float(fixed + 1), -1.0, False


def panels(asset, side: str, fixed: int, a: int, b: int
           ) -> list[tuple[float, float, int]]:
    """Where pieces go along one run, stepped at the piece's own length.

    The last panel is pulled back so the run ends flush rather than
    overhanging. On a run shorter than one panel there is a single piece,
    centred, overhanging both ends -- which is the honest picture of what a
    2-tile module does to a 1-cell jog, and worth seeing rather than hiding.
    """
    plen = max(asset.size_x, asset.size_z)
    thick = min(asset.size_x, asset.size_z)
    t0, t1, line, inward, along_x = run_line(side, fixed, a, b)
    rot = B._SIDE_ROT[side]
    if asset.size_z > asset.size_x:
        rot = (rot + B._QUARTER) % 24

    length = t1 - t0
    n = max(1, math.ceil(length / plen - 1e-9))
    off = line + inward * thick / 2.0

    out = []
    for i in range(n):
        t = t0 + plen / 2.0 + i * plen
        if i == n - 1:
            t = t1 - plen / 2.0 if length >= plen else (t0 + t1) / 2.0
        out.append((t, off, rot) if along_x else (off, t, rot))
    return out


def gate_run(tm, runs):
    """Which run to cut the gate in.

    The one that looks out onto the most paving -- a gate belongs where you
    would walk up to it. Where the yard touches no way at all (17 of East
    Tradebourne's 230), the widest stretch of boundary is the best answer
    available, and it is still an answer, which a sealed ring is not.
    """
    if not runs:
        return None
    return max(runs, key=lambda r: (facing_ways(tm, r), r[3] - r[2]))


def in_gate(run, cx: float, cz: float, side: str) -> bool:
    """Is this panel the middle of its run, and so the gate?"""
    _, _, a, b = run
    mid = (a + b + 1) / 2.0
    t = cx if side in ("n", "s") else cz
    return abs(t - mid) <= 1.0


# ---------------------------------------------------------------- the pass

def make_lay_yards(*, mode: str, piece, gate: bool):
    """A drop-in for `build._lay_yards` with one treatment bound in.

    `mode` is `shipped` (a panel per cell edge, what the build does today),
    `run` (panels stepped along each straight run at the piece's own length)
    or `none` (surface only, no boundary at all).
    """

    def lay(b, tm, grade, taper):
        yards = B.yard_cells(tm)
        if not yards:
            return 0
        all_yard = {c for cs in yards.values() for c in cs}
        enclosed = set(R.compounds(tm).values())
        laid = 0

        for bid, cells in sorted(yards.items()):
            laid += sum(1 for c in cells if taper.get(c, 0.0) is not None)
            if piece is None or bid in enclosed:
                continue

            edges = boundary_edges(tm, cells, all_yard, skip_ways=not gate)
            if mode == "shipped":
                for x, z, side in edges:
                    drop = taper.get((x, z), 0.0)
                    if drop is None:
                        continue
                    b.add(B.place_wall(piece, x, z, side, grade - drop),
                          prop=True)
                continue

            runs = straight_runs(edges)
            # **`gated` fences the whole ring and cuts ONE gate in it.** The
            # shipped rule opens every edge onto a way, which on a plot that
            # fronts a lane along its whole side is not a gate, it is a
            # missing side -- 29% of Graybank's yard perimeter. And a yard
            # that fronts nothing is left with no opening at all.
            skip = gate_run(tm, runs) if gate else None

            for run in runs:
                side, fixed, a, bnd = run
                for cx, cz, rot in panels(piece, side, fixed, a, bnd):
                    if run is skip and in_gate(run, cx, cz, side):
                        continue
                    cell = (int(math.floor(cx)), int(math.floor(cz)))
                    drop = taper.get(cell, 0.0)
                    if drop is None:
                        continue
                    b.add(place_centered(piece, cx, cz, grade - drop, rot),
                          prop=True)
        return laid

    return lay


# ---------------------------------------------------------------- sweeps

#: (label, mode, palette role or pinned asset name, gate, YARD_REACH)
SWEEPS: dict[str, list[tuple]] = {
    "structure": [
        ("shipped", "shipped", "role:yard_fence", False, 2),
        ("butted",  "run",     "role:yard_fence", False, 2),
        ("gated",   "run",     "role:yard_fence", True,  2),
        ("none",    "none",    None,              False, 2),
    ],
    # `field_wall_tall` deals `Stone Wall 01` on five seeds in eight -- the
    # ordinary wall -- so the estate panel pins `Stone Wall 02` by name and
    # compares what it names.
    # **Both of these run `butted`, not `gated`, and the gate is off.** The
    # first cut had the gate on, which meant every style panel also carried the
    # closed-ring frontage -- and that frontage is a stair-step against a
    # diagonal lane, so each panel came with its own comb of crossed pieces.
    # A sweep that varies two things answers neither.
    "style": [
        ("paling",   "run", "role:yard_fence",     False, 2),
        ("drystone", "run", "role:field_wall",     False, 2),
        ("estate",   "run", "name:Stone Wall 02",  False, 2),
        ("hedge",    "run", "role:field_hedge",    False, 2),
    ],
    "size": [
        ("reach-1", "run", "role:yard_fence", False, 1),
        ("reach-2", "run", "role:yard_fence", False, 2),
        ("reach-3", "run", "role:yard_fence", False, 3),
        ("reach-4", "run", "role:yard_fence", False, 4),
    ],
    # What `build.py` does today, untouched -- the panel to read a change
    # against, and the one that needs no monkeypatching at all.
    "now": [
        ("now", "real", None, False, None),
    ],
    # The uniform apron that shipped, against the per-side one measured from
    # the ground each building actually has. `reach` is the number forced on
    # every side of the `uniform` panel; `None` means measure it.
    "variance": [
        ("uniform-2",  "run", "role:yard_fence", False, 2),
        ("measured",   "run", "role:yard_fence", False, None),
    ],
}


def resolve_piece(spec, palette, catalog):
    """`role:<palette role>` or `name:<exact asset name>`, or None."""
    if spec is None:
        return None
    kind, _, value = spec.partition(":")
    if kind == "role":
        return palette.resolve(value)
    got = catalog.find(name=value)
    if not got:
        raise SystemExit(f"no such asset: {value!r}")
    return got[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default="structure", choices=sorted(SWEEPS))
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--layouts", default="out")
    ap.add_argument("--out", default="out/yardprobe")
    ap.add_argument("--town", default=None)
    ap.add_argument("--crop", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--storeys", type=int, default=3)
    ap.add_argument("--split", action="store_true",
                    help="one slab per panel instead of one composed row")
    args = ap.parse_args()

    catalog = load_or_build(args.catalog)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    town, crop = SITES.get(args.sweep, (TOWN, CROP))
    town = args.town or town
    x0, z0, w, d = ((int(v) for v in args.crop.split(","))
                    if args.crop else crop)

    layout = Layout.load(pathlib.Path(args.layouts) / town / "layout.json")
    whole = R.rasterize(layout)
    palette = Palette.named(catalog, "medieval", args.seed)
    mark = catalog.find(name="castle floor 1x1")[0]

    shipped, reach_was = B._lay_yards, B.YARD_REACH
    composed: list = []
    cursor = 0
    print(f"{'panel':10} {'reach':>5} {'yards':>5} {'cells':>6} {'boundary':>8} "
          f"{'assets':>7}   piece")

    for i, (label, mode, spec, gate, reach) in enumerate(SWEEPS[args.sweep], 1):
        piece = resolve_piece(spec, palette, catalog)
        tm = whole.crop(x0, z0, w, d)
        measured = B.yard_reach_by_side
        try:
            if reach is not None:
                # **Force every side, which is what the old sizing did.** The
                # constant is only a fallback now, so setting `YARD_REACH` no
                # longer reproduces the shipped behaviour -- the apron has to
                # be squared off at the measurement.
                B.yard_reach_by_side = (
                    lambda tm_, bid, cells=None, r=reach:
                    {side: r for side, _, _ in B.SIDE_OFFSETS})
            B.YARD_REACH = reach or B.YARD_REACH
            yards = B.yard_cells(tm)
            if mode != "real":
                B._lay_yards = make_lay_yards(mode=mode, piece=piece, gate=gate)
            builder = build_from_tilemap(tm, palette, storeys=args.storeys,
                                         seed=args.seed)
        finally:
            B._lay_yards, B.YARD_REACH = shipped, reach_was
            B.yard_reach_by_side = measured

        boundary = (builder.yard_pieces if mode == "real"
                    else sum(1 for p in builder.placements
                             if piece is not None and p.asset_id == piece.id))
        cells = sum(len(c) for c in yards.values())
        print(f"{label:10} {str(reach or 'site'):>5} {len(yards):5} {cells:6} {boundary:8} "
              f"{len(builder.placements):7,}   {piece.name if piece else '--'}")

        if args.split:
            # **One panel per board, because the camera cannot frame the row.**
            # Ctrl+scroll height is capped -- measured, two 1920x1080 frames 45
            # and 200 ticks apart differ by 0.59 against a 2.0 noise floor --
            # and at that cap an oblique covers about 40 tiles. A four-panel
            # row is 151. Pasted one per board at the same cursor cell after
            # the same camera moves, every panel is framed identically, which
            # is a stronger comparison than a row nobody can see at once.
            marked = list(builder.placements)
            for k in range(i):
                marked.append(place_tile(mark, 1 + k, d - 2, 0.5))
            path = out / f"{args.sweep}-{i}-{label}.slab.txt"
            text = encode(Slab(marked).normalized())
            path.write_text(text, encoding="utf-8")
            print(f"{'':10} {'':5} {'':5} {'':6} {'':8} {len(text):7,} bytes -> {path}")
        else:
            for p in builder.placements:
                composed.append(dataclasses.replace(p, x=p.x + cursor))
            for k in range(i):
                composed.append(place_tile(mark, cursor + 1 + k, d - 2, 0.5))
        cursor += w + GAP

    if args.split:
        return
    path = out / f"{args.sweep}.slab.txt"
    text = encode(Slab(composed).normalized())
    path.write_text(text, encoding="utf-8")
    print(f"\n{len(composed):,} assets, {len(text):,} bytes -> {path}")
    if len(text) > 30720:
        print("  OVER THE 30,720-BYTE CAP -- shrink the crop or split the board")


if __name__ == "__main__":
    main()
