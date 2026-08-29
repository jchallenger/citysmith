"""Our roof, beside the two the user built by hand, on one footprint.

**This probe has ground truth**, which none of the other roof probes do. The
user hand-built two roofs over the same 6 x 4 footprint and handed the slab
over; `docs/roofscape.md` §8 is the decode and §9 is what the board said. So
the control is not "a piece we already think is wrong" -- it is a roof somebody
built in the game and kept, and the question is what ours does differently.

The three bays, west to east, counted by a bar of cells on the ground to their
north (a bar, not a stack: a vertical tally reads at an oblique and vanishes in
plan, and a roof is judged in plan):

    1 OURS     `_roof_rings` flooded from the whole boundary, exactly as
               `_lay_roofs` does it. On a 6 x 4 that is ONE course and a
               4 x 2 flat deck where the ridge should be. The control.
    2 FLUSH    the SHIPPED `_lay_gabled_wing` in its `flush` treatment, with
               the shipped `gable_infill` closing the verge -- driven through
               a real `Builder`, not reimplemented. This is what a gabled
               town building is today.
    3 END-MIX  the user's building B. Single-course slopes in the field,
               DOUBLE-COURSE end pieces closing both verges -- two scales in
               one roof, which nothing in `build.py` does.

Each bay carries the rear flat deck at the scale its source used (1x1 tiles on
1 and 2, one 2x2 tile on 3) and its chimney where its source put it.

**The first cut of this probe hand-rolled bay 2 and that was the whole trap.**
It filled the verge with stacked caps to the roof's TOP, read as a flat
parapet, and was about to be written up as a finding about the technique --
while `gable-single-course-infill` was already *done* and `build.gable_infill`
already did it correctly, stopping at the roof's UNDERSIDE. A probe that
reimplements what it is probing can only tell you about the probe. Bay 2 drives
the shipped function now, so it can only be wrong the way the town is wrong.

WHAT THE BOARD SAID (2026-08-29, `PROBE roof mix`): bay 3 is the only one of
the three that reads as a gabled house. Bay 1 is a flat-topped box. Bay 2 shows
a horizontal band at the verge with the roof set back behind it -- a parapet,
not a triangle. **The two roof scales mix**, which is the finding.

READ IT IN THIS ORDER: overhead first, because a ridge's plan is where a gable
and a flat top are unambiguous and every oblique flatters one of them; then a
square-on END elevation, which is what judges a verge; then the long flank.
`review.ps1 360` gives four 60-degree obliques, NOT four elevations -- see
`review-cardinal-faces` -- so aim the elevation by compass. And judge it at a
medium range: at distance the eaves shadow reads exactly like an open gable,
which cost one false finding here before the hand-build was pasted to check.

Usage::

    python tools/roofmix_probe.py > out/roofmix.slab.txt
    python tools/roofmix_probe.py --ridge x    # the hand-build's own axis,
                                               # which the ground-truth
                                               # comparison has to run at
"""

from __future__ import annotations

import argparse
import collections
import sys

sys.path.insert(0, ".")

from citysmith.build import (
    ROOF_EDGE_ROT, SIDE_OFFSETS, Builder, _is_reflex, _lay_gabled_wing,
    _normalized_whole_tiles, _ridge_rotations, _roof_piece, _roof_rings,
    gable_end_piece, gable_infill, place_centered, place_tile, place_wall,
    roof_offsets, roof_set_named,
)
from citysmith.catalog import load_or_build
from citysmith.palette import Palette
from citysmith.slab import Slab, encode

#: Which axis the RIDGE runs along, and therefore which way the gable verges
#: face. **`z`, so the verges face north, and that is a framing decision.**
#: The three bays have to sit in a row along the frame's LONG axis or the board
#: does not fit one shot -- 12 x 35 stacked needs 68 tiles of slant range
#: against a stop at 50, which `camera_aim --slab` says before anything is
#: pasted. Laid in a row with the ridges along x the verges face each other and
#: there is no square-on end elevation to be had, which is the one view that
#: judges a verge. Ridges across the row solves both.
RIDGE_AXIS = "z"

#: The two falls across the ridge, low side first, per ridge axis.
FALLS = {"x": ("n", "s"), "z": ("w", "e")}

#: The footprint the user hand-built on, in cells: six along the ridge, four
#: down the slope. Not arbitrary -- the commonest wing shape on every board
#: measured is 5 x 6 (253 of East Tradebourne's 1,462), so this is the size
#: the town is actually made of rather than one chosen to gable nicely.
BAY_ALONG = 6
BAY_ACROSS = 4

#: The rear wing under the flat deck: shorter along the ridge and shallower
#: across it, which is what makes it a lower range with a wall standing over
#: it -- the class-A rooftop-terrace geometry of `docs/roofscape.md` §4.1.
REAR_ALONG = 4
REAR_ACROSS = 2

BAY_GAP = 3                 #: bare cells between bays
MARGIN = 3                  #: cells of ground beyond the outermost bay

#: Courses of wall under the roof. The hand-build has ONE and the roof reads
#: fine on it; two is the probe default because it lifts the verge to where the
#: eye can judge whether the triangle meets the slope.
WALL_COURSES = 2

TREATMENTS = ("ours", "flush", "end-mix")

#: Set by main(); `lay_flush` drives the shipped builder and needs both.
PALETTE = None
INFILL_TIER = "trade"


def lay_shell(out, floor, wall, cells, ox, oz, courses):
    """Floor and perimeter wall for one block. Returns the wall head height."""
    for x, z in sorted(cells):
        out.append(place_tile(floor, ox + x, oz + z, -floor.size_y))
    for level in range(courses):
        y = level * wall.size_y
        for x, z in sorted(cells):
            for side, dx, dz in SIDE_OFFSETS:
                if (x + dx, z + dz) not in cells:
                    out.append(place_wall(wall, ox + x, oz + z, side, y))
    return courses * wall.size_y


def lay_flat_deck(out, cap, cells, ox, oz, top, wide=None):
    """The rear wing's flat roof, seated so its TOP lands at ``top``.

    ``wide`` is the kit's 2x2 cap. **Where a 2x2 fits it is used**, which is
    the user's own note -- building B covers the same 4 x 2 deck in two pieces
    where building A took eight. A piece bigger than a cell puts its min corner
    on the cell and reaches past it, so it is laid on the 2-cell lattice and
    only where all four of its cells are in the block. That check is the whole
    reason `Tavern Roof flat 02` once made a roof one unit too big.
    """
    left = set(cells)
    n_wide = 0
    if wide is not None:
        step = int(round(wide.size_x))
        xs = sorted({x for x, _ in cells})
        zs = sorted({z for _, z in cells})
        for x in xs[::step]:
            for z in zs[::step]:
                quad = {(x + dx, z + dz)
                        for dx in range(step) for dz in range(step)}
                if quad <= left:
                    # **rot 18, which is the hand-build's own.** The 2x2 cap
                    # is square, so the turn does not move the footprint --
                    # it turns the texture, and matching ground truth costs
                    # nothing where guessing could put the weave across the
                    # fall. It is also the free variance axis on a deck.
                    out.append(place_tile(wide, ox + x, oz + z,
                                          top - wide.size_y, 18))
                    left -= quad
                    n_wide += 1
    for x, z in sorted(left):
        out.append(place_tile(cap, ox + x, oz + z, top - cap.size_y))
    return n_wide, len(left)


def lay_ours(out, pieces, cells, ox, oz, roof_y):
    """`_lay_roofs`' own hip, reproduced through the builder's own functions."""
    side, corner, inner, cap, chimney = (
        pieces["slope"], pieces["corner"], pieces["inner"],
        pieces["cap"], pieces["chimney"])
    edge_off, corner_off = roof_offsets(side)
    rise = side.size_y
    rings = _roof_rings(cells)
    top_ring = max(rings.values())
    crown = [c for c in sorted(cells) if rings[c] == top_ring]
    chimney_at = crown[len(crown) // 2] if crown else None
    ridge_rot = _ridge_rotations(cells, rings, top_ring, chimney_at)
    for (x, z) in sorted(cells):
        r = rings[(x, z)]
        y = roof_y + r * rise
        if (x, z) == chimney_at and chimney is not None:
            # Two lapped pieces and NO roof surface under them -- the thing
            # bay 2 and bay 3 do differently.
            out.append(place_tile(chimney, ox + x, oz + z, y - 0.25))
            out.append(place_tile(chimney, ox + x, oz + z, y))
            continue
        if r == top_ring and cap is not None:
            out.append(place_tile(cap, ox + x, oz + z, y - cap.size_y,
                                  ridge_rot.get((x, z), 0)))
            continue
        fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                     if rings.get((x + dx, z + dz), -1) < r)
        piece, rot = _roof_piece(fall, side, corner, cap, inner,
                                 _is_reflex(rings, x, z, fall),
                                 edge_off, corner_off)
        if piece is not None:
            out.append(place_tile(piece, ox + x, oz + z, y, rot))


def lay_flush(out, pieces, cells, ox, oz, roof_y):
    """The BUILDER's own flush gable -- `_lay_gabled_wing`, not a copy of it.

    **The first cut of this probe hand-rolled the verge fill and that was the
    whole trap.** `gable-single-course-infill` is *done*: `build.gable_infill`
    already closes a timber verge with the roof kit's own flat cap, stacked,
    and `_lay_gabled_wing` already lays it -- stopping at the roof's UNDERSIDE,
    which is half a tile lower on a capped ridge cell than a sloped one and is
    the correction that took the seam count from +1,020 to 136. The copy here
    filled to the roof's TOP and read as a flat parapet with the roof set back
    behind it. That is a finding about the copy, not about the technique, which
    is exactly what CLAUDE.md says happens to a probe that reimplements what it
    is probing. So this bay drives the shipped function through a real
    `Builder` and the probe can only be wrong the way the town is wrong.
    """
    b = Builder(PALETTE)
    b.group = ""
    # **The tier is chosen so its DEFAULT roof matches this probe's material.**
    # `gable_infill` resolves the cap from `roof_set(palette, tier)` -- the
    # tier's default -- while `_lay_roofs` deals the material per BUILDING
    # through `roof_suffix_for`. The two disagree on 227 of East Tradebourne's
    # 989 non-civic buildings (a tiled common house gets a thatch verge, a
    # thatched trade building gets a tile one), which is `gable-infill-follows-
    # the-tier-not-the-roof`. Passing the matching tier here keeps this bay a
    # test of the GABLE rather than a second display of that bug.
    infill = gable_infill(PALETTE, INFILL_TIER, None)
    _lay_gabled_wing(b, {(ox + x, oz + z) for x, z in cells}, "flush",
                     roof_y, pieces["slope"].size_y, pieces["slope"],
                     pieces["cap"], roof_offsets(pieces["slope"])[0],
                     None, pieces["chimney"], infill)
    out.extend(b.placements)


def lay_end_mix(out, pieces, cells, ox, oz, roof_y):
    """The SHIPPED `endmix` treatment -- `_lay_gabled_wing`, not a copy.

    This bay was hand-rolled from the decoded slab while the treatment was
    being designed, and reproduced the user's building B at 18 of 20
    placements. It is wired to the builder now that `build.gable_end_piece`
    and the `endmix` branch exist, for the same reason bay 2 is: a probe that
    reimplements what it is probing can only tell you about the probe. What it
    now checks is that the SHIPPED path puts the same pieces in the same places
    the hand-build did.
    """
    b = Builder(PALETTE)
    b.group = ""
    side = pieces["slope"]
    b_end = gable_end_piece(PALETTE, side)
    _lay_gabled_wing(b, {(ox + x, oz + z) for x, z in cells}, "endmix",
                     roof_y, side.size_y, side, pieces["cap"],
                     roof_offsets(side)[0], None, pieces["chimney"],
                     gable_infill(PALETTE, INFILL_TIER, None), b_end)
    out.extend(b.placements)


LAYERS = {"ours": lay_ours, "flush": lay_flush, "end-mix": lay_end_mix}

#: Which flat-deck scale each bay uses, mirroring its source.
DECK_WIDE = {"ours": False, "flush": False, "end-mix": True}

#: How far the rear deck sits above the wall head. The hand-build seats A's
#: flush (0.0) and B's a quarter PROUD, which gives the deck a visible lip
#: instead of a flush join -- measured, not inferred, and worth carrying
#: because a flat roof with no edge reads as a hole.
DECK_RAISE = {"ours": 0.0, "flush": 0.0, "end-mix": 0.25}


def main() -> None:
    global RIDGE_AXIS, PALETTE, INFILL_TIER
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=33,
                    help="palette seed. 33 is what the towns are built at, "
                         "and the roles resolve differently under another -- "
                         "probing with an unseeded palette cost an hour once.")
    ap.add_argument("--suffix", default="tile",
                    help="roof material: tile, slate, or empty for thatch.")
    ap.add_argument("--ridge", choices=("x", "z"), default=RIDGE_AXIS,
                    help="which axis the ridge runs along. `z` is the "
                         "default and is a FRAMING choice (see RIDGE_AXIS); "
                         "`x` is what the hand-build used and is what the "
                         "ground-truth comparison has to run at.")
    ap.add_argument("--wall-courses", type=int, default=WALL_COURSES,
                    dest="wall_courses",
                    help="1 is the hand-build's own proportion; 2 lifts the "
                         "verge to where the eye can judge it.")
    args = ap.parse_args()
    RIDGE_AXIS = args.ridge

    palette = Palette.named(load_or_build(), "medieval", args.seed)
    PALETTE = palette
    INFILL_TIER = {"tile": "trade", "slate": "civic"}.get(args.suffix, "common")
    cat = palette.catalog
    byname: dict[str, object] = {}
    for a in cat.assets:
        byname.setdefault(a.name, a)

    slope, corner, inner, cap, chimney = roof_set_named(palette, args.suffix)
    pieces = {
        "slope": slope, "corner": corner, "inner": inner,
        "cap": cap, "chimney": chimney,
        "end": byname.get("Village Roof Side End 01"),
        "end2": byname.get("Village Roof Side End 02"),
        "wide_cap": byname.get("Tavern Roof flat 02"),
    }
    missing = [k for k in ("slope", "cap", "end", "end2") if pieces[k] is None]
    if missing:
        ap.error("not in this catalog: " + ", ".join(missing))
    if (cap.size_x, cap.size_z) != (1.0, 1.0):
        ap.error(f"cap {cap.name!r} is not one cell; it is laid per cell")

    wall = palette.require("wall")
    floor = palette.require("floor")
    ground = palette.require("ground")
    marker = byname.get("md_stairblock_01") or floor
    pieces["floor"] = floor

    out: list = []
    # Bays in a row along x (the frame's long axis); ridges along z, so all
    # three verges face north and one elevation holds the lot. See RIDGE_AXIS.
    span_across = BAY_ACROSS + REAR_ACROSS
    pitch = (BAY_ALONG if RIDGE_AXIS == "x" else span_across) + BAY_GAP
    width = len(TREATMENTS) * pitch
    depth = span_across if RIDGE_AXIS == "x" else BAY_ALONG

    for dz in range(-MARGIN - 2, depth + MARGIN):
        for dx in range(-MARGIN, width + MARGIN):
            out.append(place_tile(ground, dx, dz, -ground.size_y - 0.5))

    # The block follows the ridge axis: ``along`` runs with the ridge,
    # ``across`` down the slope, and the rear wing hangs off the far eaves.
    def block(a0, a1, b0, b1):
        return {((a, b) if RIDGE_AXIS == "x" else (b, a))
                for a in range(a0, a1) for b in range(b0, b1)}

    main_cells = block(0, BAY_ALONG, 0, BAY_ACROSS)
    rear_cells = block(0, REAR_ALONG, BAY_ACROSS, BAY_ACROSS + REAR_ACROSS)

    for ti, t in enumerate(TREATMENTS):
        ox, oz = ti * pitch, 0
        head = lay_shell(out, floor, wall, main_cells | rear_cells,
                         ox, oz, args.wall_courses)
        LAYERS[t](out, pieces, main_cells, ox, oz, head)
        nw, n1 = lay_flat_deck(out, cap, rear_cells, ox, oz,
                               head + DECK_RAISE[t],
                               pieces["wide_cap"] if DECK_WIDE[t] else None)
        # A bar running EAST, on the ground north of the bay -- a bar rather
        # than a stack, because a vertical tally reads at an oblique and
        # vanishes in plan, and this probe is read in plan first.
        for k in range(ti + 1):
            out.append(place_tile(marker, ox + k, oz - 2, 0.0))
        print(f"#   bar of {ti + 1}: {t:<10} rear deck {nw} x (2x2) "
              f"+ {n1} x (1x1), raised {DECK_RAISE[t]}", file=sys.stderr)

    byid = {a.id: a for a in cat.assets}
    slab = _normalized_whole_tiles(Slab(out), byid)
    print(encode(slab))

    counts = collections.Counter(
        byid[p.asset_id].name for p in slab.placements if p.asset_id in byid)
    board_w = width + 2 * MARGIN
    board_d = depth + 2 * MARGIN + 2
    print(f"# {len(out)} placements, board {board_w} x {board_d}",
          file=sys.stderr)
    print(f"# roof material: {args.suffix or 'thatch'}  "
          f"slope={slope.name}  cap={cap.name}  chimney={chimney.name}",
          file=sys.stderr)
    for nm, n in counts.most_common(12):
        print(f"#   {n:5d}  {nm}", file=sys.stderr)
    print("# READ: overhead, then a square-on END elevation, then the flank.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
