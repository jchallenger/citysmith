"""Four church plans, each as its own slab, for review before any of them lands.

**A church is not one box, and the height ladder cannot fix that.**
`CHURCH_BANDS` made the temple taller; it is still a single rectangle with a
tower on one end, and every real church of any size is a *group* of volumes --
a nave, a lower chancel, a porch or narthex you come in through, a side chapel,
a vestry. The step between those volumes is what says church from outside, and
it is the thing `docs/great-buildings.md` §2 asks for and §4.2 never got to.

The trick here is that citysmith already builds this. A building id gets its
own footprint, its own band and its own roof, so **a church complex is several
abutting footprints**, not one footprint with rooms drawn in it. The nave is a
big id and lands in the `great` band; a chancel of 30 cells lands in `chapel`
and comes out three courses lower on its own. Nothing new is needed for the
massing -- only the plan.

Each plan is stated as rectangles because that is what a reviewer can check.
`cells` and the band each part falls in are printed, so a plan that reads well
on paper and deals a flat set of heights is caught before it is built.

    python tools/church_plans.py --plan aisled --out out/plans
    python tools/church_plans.py --all --out out/plans

`--spire N` caps the tower with the `Castle Fortified` tall-roof family at
quarter turn N. **That rotation is a HYPOTHESIS, not a measurement**:
`ROOF_ROT_OFFSET` is keyed on folder and this is a different *group* inside a
folder we build from, so the kit's ordinary +6 is not evidence about this set.
`--spire-sweep` builds the same plan four times, once per turn, which is the
form that settles it -- exactly one closes.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith.build import (  # noqa: E402
    SIDE_OFFSETS, _is_reflex, _roof_piece, _roof_rings, build_from_tilemap,
    church_band, place_tile,
)
from citysmith.catalog import load_or_build  # noqa: E402
from citysmith.palette import MEDIEVAL, Palette  # noqa: E402
from citysmith.raster import (  # noqa: E402
    FLOOR, GROUND, TileMap, _find_perimeters, _place_doors,
)

#: `(part name, x, z, w, d)`. The FIRST part is the principal volume -- it is
#: the one sized to earn a tower, and the one everything else steps down from.
#: Origins are relative; the tool pads a margin round the lot.
PLANS: dict[str, tuple[str, tuple[tuple[str, int, int, int, int], ...]]] = {
    "aisled": (
        "Aisled parish church: one long nave with a west tower, a lower "
        "chancel at the east end, a north chapel and a south porch.",
        (
            ("nave", 3, 0, 6, 20),      # 120 cells -> great, aspect 3.3 -> tower
            ("chancel", 4, 20, 4, 6),   # 24 -> chapel, three courses lower
            ("chapel", 9, 6, 4, 5),     # 20 -> chapel
            ("vestry", 0, 8, 3, 4),     # 12 -> chapel
        ),
    ),
    "cruciform": (
        "Cruciform: nave and chancel on one axis, transepts north and south, "
        "the crossing carrying the tower. The most church-shaped thing here.",
        (
            ("nave", 5, 0, 6, 18),      # 108 -> great, aspect 3.0 -> tower
            ("n_transept", 0, 7, 5, 5),
            ("s_transept", 11, 7, 5, 5),
            ("chancel", 6, 18, 4, 5),
        ),
    ),
    "narthex": (
        "Hall church entered through a narthex: a wide aisled body, a lobby "
        "across the whole west end, a lady chapel off the south and a vestry.",
        (
            ("nave", 2, 4, 9, 16),      # 144 -> great, aspect 1.8 -> NO tower
            ("narthex", 2, 0, 9, 4),    # 36 -> chapel: the lobby
            ("lady_chapel", 11, 8, 5, 6),
            ("vestry", 0, 14, 2, 5),
        ),
    ),
    "minster": (
        "Minster with a cloister: a long nave, a chapter house on the east "
        "range, and two low ranges round an open court on the south side.",
        (
            ("nave", 0, 0, 7, 21),      # 147 -> great, aspect 3.0 -> tower
            ("chapter", 7, 2, 6, 6),
            ("south_range", 7, 15, 10, 4),
            ("east_range", 13, 8, 4, 11),
        ),
    ),
}

MARGIN = 4
BAR_GAP = 2


def _tilemap(parts):
    w = max(x + pw for _n, x, _z, pw, _d in parts) + MARGIN * 2
    d = max(z + pd for _n, _x, z, _w, pd in parts) + MARGIN * 2 + BAR_GAP + 2
    tm = TileMap.blank(w, d)
    for z in range(tm.depth):
        for x in range(tm.width):
            tm.surface[z][x] = GROUND
    for n, (name, px, pz, pw, pd) in enumerate(parts, start=1):
        bid = f"temple-{n:04d}"
        for x in range(px + MARGIN, px + MARGIN + pw):
            for z in range(pz + MARGIN, pz + MARGIN + pd):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        # 1 on purpose: if a part comes out one course tall, the band is not
        # being consulted and the plan is telling you so.
        tm.floors[bid] = 1
    _find_perimeters(tm, None)
    _place_doors(tm, None)
    return tm


def _spire(b, tm, palette, turn: int) -> int:
    """Cap the tallest part with the tall-roof family. Returns pieces laid.

    Built out of `_roof_rings` and `_roof_piece`, the same two functions
    `_lay_towers` uses, so this is the shape the generator would make rather
    than a reconstruction of it.
    """
    byname = {a.name: a for a in palette.catalog.assets}
    slope = byname.get("Tall 2x2x4")
    corner = byname.get("Tall 2x2x4 Corner out")
    inner = byname.get("Tall 2x2x4 Corner in")
    if slope is None or corner is None:
        return 0
    # The principal part, and the top of everything standing on it.
    cells = {(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.building[z][x] == "temple-0001"}
    if not cells:
        return 0
    top = max((p.y + palette.catalog.by_id(p.asset_id).size_y)
              if hasattr(palette.catalog, "by_id") else 0 for p in b.placements) \
        if False else max(p.y for p in b.placements)
    # The spire sits on the square block at one end -- the same 6x6 the tower
    # pass crowns. Take the deepest square that fits inside the part.
    xs = sorted({c[0] for c in cells})
    zs = sorted({c[1] for c in cells})
    side = min(len(xs), 6)
    block = {(x, z) for x in xs[:side] for z in zs[:side]}
    rings = _roof_rings(block)
    laid = 0
    with b.layer("structure"):
        for (x, z) in sorted(block):
            r = rings[(x, z)]
            fall = tuple(sd for sd, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, slope, corner, None, inner,
                                     _is_reflex(rings, x, z, fall), turn, turn)
            if piece is not None:
                b.add(place_tile(piece, x, z, top + (r - 1) * slope.size_y, rot))
                laid += 1
    return laid


def build(name: str, *, seed: int, storeys: int, spire: int | None):
    blurb, parts = PLANS[name]
    tm = _tilemap(parts)
    palette = Palette.named(load_or_build(), "medieval", seed)
    b = build_from_tilemap(tm, palette, storeys=storeys, seed=seed,
                           quarters=False)
    n = _spire(b, tm, palette, spire) if spire is not None else 0
    return tm, b, blurb, parts, n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="out/plans")
    ap.add_argument("--seed", type=int, default=33)
    ap.add_argument("--storeys", type=int, default=3)
    ap.add_argument("--spire", type=int, default=None,
                    help="quarter turn for the tall-roof cap (0/6/12/18)")
    ap.add_argument("--spire-sweep", action="store_true",
                    help="build one slab per quarter turn -- the form that "
                         "settles the rotation, since exactly one closes")
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = list(PLANS) if args.all or not args.plan else [args.plan]

    for name in names:
        turns = (0, 6, 12, 18) if args.spire_sweep else (args.spire,)
        for turn in turns:
            tm, b, blurb, parts, laid = build(
                name, seed=args.seed, storeys=args.storeys, spire=turn)
            stem = name if turn is None or not args.spire_sweep \
                else f"{name}-spire{turn}"
            path = out / f"{stem}.slab.txt"
            path.write_text(b.to_slab().encode(), encoding="utf-8")
            print(f"{stem:22s} {len(b.placements):6d} placements"
                  f"  {tm.width}x{tm.depth} tiles"
                  f"  spire {laid} piece(s) -> {path}")
        print(f"    {blurb}")
        for n, (pname, _x, _z, w, d) in enumerate(parts, start=1):
            courses, stages = church_band(w * d)
            band = ("great" if w * d >= 100 else "town" if w * d >= 70
                    else "parish" if w * d >= 40 else "chapel")
            print("      %-13s %2dx%-2d = %3d cells  %-7s %d courses (%d ft)"
                  % (pname, w, d, w * d, band, courses, courses * 10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
