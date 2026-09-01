"""Four church plans, revised after review, each as its own slab.

**A church is not one box, and the height ladder could not fix that.**
`CHURCH_BANDS` made the temple taller; it was still a single rectangle with a
tower on one end. Every real church is a *group* of volumes, and the step
between them is what says church from outside.

citysmith already builds that: a building id gets its own footprint, its own
band and its own roof, so a church complex is several ABUTTING FOOTPRINTS
rather than one footprint with rooms drawn in it. What it did NOT have, and
what the first round of these plans exposed, is a sane rule for how tall each
volume stands -- `build.SUBORDINATE_STEP` is that rule now.

## What the first round got wrong, and what changed

Three independent reviews read the plans (architecture, tabletop play,
generator design). Every one of the four failed, and the corrections are:

- **The naves were corridors.** 6 cells is 30 ft: a bowling alley with total
  sightlines, where a shortbow covers the whole thing from turn one and a
  20 ft fireball spans it wall to wall. Naves are 8-10 wide now and carry an
  ARCADE -- piers one cell in from each long wall, every third cell -- which
  is what makes a nave aisled, breaks the sightline, and gives cover every
  15 ft. That single change was the top recommendation of two of the three.
- **The plan called "aisled" had no aisles.** Checked, not argued. A probe
  whose caption promises what its geometry does not contain is the
  `1,084 stacks` failure in a different costume, so `--check` now fails a
  plan whose parts do not match its name.
- **Parts were in the wrong places.** Chapel and vestry hung off the middle
  of the nave where only a porch belongs; they flank the CHANCEL now. The
  cruciform's transepts sat mid-nave with the chancel 35 ft further on, so
  there was no crossing at all -- they abut the chancel now and the tower
  stands over them.
- **Two plans had no chancel.** A hall church still terminates in a
  presbytery; a minster's church arm without one is just the longest range.
  Both have one.
- **Heights came from area.** A 24-cell chancel landed in the `chapel` band
  and drew a 30 ft step where 10 is right, because a chancel is small in plan
  and tall in section. `SUBORDINATE_STEP` bands each part against the nave by
  ROLE: transept equal, chancel -1, everything minor -2.
- **The tower was the full width of the nave**, which reads as a westwork or
  a keep rather than a tower rising out of a roofline -- the biggest
  silhouette defect, and on three of four plans. `TOWER_INSET` holds it a
  cell off each flank so nave wall and roof show past it.
- **The spire.** Settled by a slab the user built by hand after a rotation
  sweep of mine failed on all four turns: four `Tall 2x2x4 Corner out` on a
  4x4, no slopes at all, at the kit's own corner rotations. `build.lay_spire`.

Still open and deliberately not fixed here: these are hand-authored
rectangles at 176-267 cells against a real imported range of 30-102. Fitting
a plan to an imported polygon is `church-subdivide-the-polygon`, and it is a
different piece of work; these exist to settle FORM.

    python tools/church_plans.py --all --out out/plans
    python tools/church_plans.py --check
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith.build import (  # noqa: E402
    build_from_tilemap, church_band, lay_spire, place_tile,
    subordinate_courses,
)
from citysmith.catalog import load_or_build  # noqa: E402
from citysmith.palette import Palette  # noqa: E402
from citysmith.raster import (  # noqa: E402
    FLOOR, GROUND, TileMap, _find_perimeters, _place_doors,
)

#: `(role, x, z, w, d)`. The part whose role is `nave` is the principal
#: volume: it is banded on its own area and everything else is banded against
#: it. Origins are relative; a margin is padded round the lot.
PLANS: dict[str, tuple[str, tuple[tuple[str, int, int, int, int], ...]]] = {
    "aisled": (
        "Aisled parish church. A nave nine wide with an arcade down it, a west "
        "tower set back off both flanks, a lower chancel, and the vestry and "
        "chapel flanking the chancel where they belong rather than the nave.",
        (
            ("nave", 0, 0, 9, 18),
            ("chancel", 2, 18, 5, 7),
            ("chapel", 7, 18, 4, 5),     # flanks the chancel, south
            ("vestry", 0, 18, 2, 5),     # flanks the chancel, north
        ),
    ),
    "cruciform": (
        "Cruciform. The transepts abut the chancel so the crossing is a real "
        "crossing, and the tower stands over it. Three equal gables meet "
        "there, which is what makes the plan read as a cross from any bearing.",
        (
            ("nave", 5, 0, 8, 15),
            ("crossing", 5, 15, 8, 8),   # the tower goes here
            ("n_transept", 0, 15, 5, 8),
            ("s_transept", 13, 15, 5, 8),
            ("chancel", 6, 23, 6, 8),
        ),
    ),
    "narthex": (
        "Hall church. The widest body here at ten cells, entered through a "
        "full-width narthex, and it now terminates in a presbytery -- a hall "
        "church without one is a box with a lobby.",
        (
            ("nave", 0, 4, 10, 16),
            ("narthex", 0, 0, 10, 4),
            ("chancel", 2, 20, 6, 6),
            ("chapel", 10, 8, 5, 6),
        ),
    ),
    "minster": (
        "Minster. A long aisled nave with a chancel, and a cloister whose "
        "ranges are inset a cell so the walk round the garth reads as an "
        "arcade rather than the court being a light well.",
        (
            ("nave", 0, 0, 9, 18),
            ("chancel", 2, 18, 5, 7),
            ("chapter", 9, 18, 6, 6),    # off the east range, by the chancel
            ("range", 9, 2, 5, 15),      # east range along the nave
        ),
    ),
}

#: Piers one cell in from each long wall, every Nth cell along it. Three is
#: 15 ft, which is a real bay and puts cover within one move.
PIER_SPACING = 3
MARGIN = 4


def _tilemap(parts):
    w = max(x + pw for _r, x, _z, pw, _d in parts) + MARGIN * 2
    d = max(z + pd for _r, _x, z, _w, pd in parts) + MARGIN * 2
    tm = TileMap.blank(w, d)
    for z in range(tm.depth):
        for x in range(tm.width):
            tm.surface[z][x] = GROUND
    for n, (role, px, pz, pw, pd) in enumerate(parts, start=1):
        bid = f"temple-{n:04d}"
        tm.church_parts[bid] = "nave" if role == "nave" else role.split("_")[-1]
        for x in range(px + MARGIN, px + MARGIN + pw):
            for z in range(pz + MARGIN, pz + MARGIN + pd):
                tm.building[z][x] = bid
                tm.surface[z][x] = FLOOR
        tm.floors[bid] = 1
    _find_perimeters(tm, None)
    _place_doors(tm, None)
    return tm


def _arcade(b, tm, palette, parts):
    """Piers down the nave: what makes it aisled, and what breaks the sightline.

    One cell in from each long wall so the aisle is a real 1-cell walkway, and
    every `PIER_SPACING` cells along, stopping clear of both ends. Built from
    the civic corner piece, which is the one full-cell square in the fabric.
    """
    pier = palette.resolve("wall_corner_civic") or palette.resolve("city_wall_core")
    if pier is None:
        return 0
    role, px, pz, pw, pd = next(p for p in parts if p[0] == "nave")
    x0, z0 = px + MARGIN, pz + MARGIN
    laid = 0
    courses = church_band(pw * pd)[0]
    with b.layer("structure"):
        for col in (x0 + 1, x0 + pw - 2):
            for z in range(z0 + PIER_SPACING, z0 + pd - 1, PIER_SPACING):
                for c in range(courses):
                    b.add(place_tile(pier, col, z, 0.5 + c * pier.size_y))
                laid += 1
    return laid


def build(name, *, seed, storeys, spire=True):
    blurb, parts = PLANS[name]
    tm = _tilemap(parts)
    palette = Palette.named(load_or_build(), "medieval", seed)
    b = build_from_tilemap(tm, palette, storeys=storeys, seed=seed,
                           quarters=False)
    piers = _arcade(b, tm, palette, parts)
    spired = 0
    if spire:
        # On the tallest part, above everything already standing on it.
        tall = max(p.y for p in b.placements) if b.placements else 0.0
        first = next(bid for bid, r in tm.church_parts.items() if r == "nave")
        cells = {(x, z) for z in range(tm.depth) for x in range(tm.width)
                 if tm.building[z][x] == first}
        with b.layer("structure"):
            spired = lay_spire(b, cells, tall, "civic", first)
    return tm, b, blurb, parts, piers, spired


def check() -> int:
    """A plan's parts must match what its caption says it is.

    The first round shipped a plan captioned "Aisled parish church" with no
    aisles in it. This is the cheap guard against the next one.
    """
    bad = []
    for name, (blurb, parts) in PLANS.items():
        roles = {r for r, *_ in parts}
        text = (name + " " + blurb).lower()
        for word, role in (("chancel", "chancel"), ("transept", "transept"),
                           ("narthex", "narthex"), ("cloister", "range"),
                           ("chapter", "chapter")):
            if word in text and not any(role in r for r in roles):
                bad.append(f"{name}: caption says {word!r}, no such part")
        if "aisle" in text and PIER_SPACING <= 0:
            bad.append(f"{name}: caption says aisled, no arcade")
    for line in bad:
        print("  FAIL", line)
    print("  %d plan(s) checked, %d problem(s)" % (len(PLANS), len(bad)))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default="out/plans")
    ap.add_argument("--seed", type=int, default=33)
    ap.add_argument("--storeys", type=int, default=3)
    ap.add_argument("--no-spire", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in (list(PLANS) if args.all or not args.plan else [args.plan]):
        tm, b, blurb, parts, piers, spired = build(
            name, seed=args.seed, storeys=args.storeys, spire=not args.no_spire)
        path = out / f"{name}.slab.txt"
        path.write_text(b.to_slab().encode(), encoding="utf-8")
        nave = next(p for p in parts if p[0] == "nave")
        courses = church_band(nave[3] * nave[4])[0]
        print("%-11s %5d placements  %2dx%-2d tiles  %2d pier(s)  "
              "%d spire piece(s) -> %s"
              % (name, len(b.placements), tm.width, tm.depth, piers, spired,
                 path))
        print("    " + blurb)
        for role, _x, _z, w, d in parts:
            key = "nave" if role == "nave" else role.split("_")[-1]
            c = courses if key == "nave" else subordinate_courses(key, courses)
            print("      %-12s %2dx%-2d = %3d cells   %d courses (%d ft)"
                  % (role, w, d, w * d, c, c * 10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
