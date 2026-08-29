"""How to cover a RUN of wall cells, probed as a set.

Every facade this project builds is one 1-cell panel per cell per side. That
is not a style decision, it is a *query*: ``palette._WALLSZ`` pins the ``wall``
role to ``size=(1.0, 0.5)``, so a wider piece cannot be resolved even when the
kit ships one. It ships one in every kit we build from.

Measured before writing this, and each number is the reason for a sheet below:

- **A run is 4.9 cells long on average and nothing is shorter than 2.**
  Rasterised from the real layouts: Pelvesthollow 190 runs mean 4.78,
  Graybank 726 runs mean 4.89, Forest Church 244 runs mean 4.90, and in all
  three **100% of wall segments sit in a run of 2 or more**, 87-93% in a run
  of 4 or more. So this is not an edge case to be handled -- it is every
  facade on the board.
- **Hand-builders use the wide pieces and we do not.** Decoding the community
  slabs in ``library/`` for panels in the medieval kits: 256 one-cell, **106
  two-cell (29.3%)** and 21 half-cell fillers. Two of the five slabs are
  majority wide (``modular-viking-workshop`` 53%, ``small-cabin...`` 62%).
  citysmith's share of both is zero, and cannot be anything else.
- **Every kit ships the whole family, and all but one at 2.00 tall.**
  1-cell, 2-cell, a 2-cell window, a 0.5 filler and a corner::

    kit                1-cell                  2-cell                  2-cell window
    Rural              Rural Wall 01           Rural Wall 02           Rural Wall Window
    Castle Fortified   castle wall 1x1         castle wall 2x2         castle wall 2x2 window
    Abandoned Village  ..._wall_1x1_01         ..._wall_2x1_01/02      ..._wall_window_2x1_01
    Tavern             Tavern Wall - Small 01  Tavern Wall 01 (2.03!)  Wall Only With Window

  **Tavern's wide piece is 2.03 tall, not 2.00**, so it cannot share a course
  with anything else in its own kit -- and the Tavern folder is where the
  common house's panels live. That is sheet 2's job to show or to clear.

The sheets, because several different things are being asked. Building sheets
(``packing``, ``kits``, ``glazing``) put a whole 6x5 box on the ground, which
is the only shape that shows what a rule does at a corner and a doorway. Swatch
sheets (``swatch-*``, ``design-*``) are bare runs on grass: at the height where
a box fits the frame a panel seam is two or three pixels, and the Ctrl+scroll
cap will not let the camera closer, so the joint can only be read on a run.

**What the design pass settled (probed 2026-08-28, boards named below).**

``design-mix`` -- *the Village and Tavern panels are one kit and still do not
mix.* `Village Roof Side Wall 01/02`, `Tavern Wall 01`, `Wall Only With
Window` and `Tavern no floor (1x1 a)` are all ``folder='Tavern'``, so by this
project's own rule they were never separate families -- ``group`` names a
*form*, and the Village panels are tagged ``roof`` only because they ship in a
roof set. But a Village panel dropped between two Tavern ones reads as a bare
plaster patch: it carries no timber frame of its own, so nothing meets the
Tavern frame at the joint, and stacked by ``centred`` it is a pale column the
full height of the wall. **The Tavern kit does not need it.** Its own narrow
partner `Tavern Wall - Small 01` blends invisibly against `Tavern Wall 01`, and
with the plinth band those pieces carry, the run reads as a storeyed building
rather than as a fence. The Village panel keeps exactly one job: it is the only
1-cell window in the Medieval Fantasy pack.

``design-bond`` -- *a packer that ignores the level stacks the narrow panel
into a column.* `pack_centred` puts the remainder in the same slot on every
storey; on a Rural run of 7 that is a dark stripe of boarding running the full
height. Three fixes, all of which remove it: `bond` swaps the remainder between
the two ends, `shift` walks it along the interior slots so both corners stay
flush, and `bond_full` offsets every course by a cell. **Only `bond_full`
touches an EVEN run**, which has no remainder to move and therefore comes out
identical on every course under the other two -- runs of 4, 6 and 8 are about a
third of every segment in all three towns.

Whether broken joints are even wanted is a per-material question and is
deliberately left on the board rather than decided here: a real timber frame
carries its posts straight up, so a bond reads as masonry and a stack reads as
framing. What is not in question is the narrow panel, which is a *different
piece* and should not stack whatever the material.

Every box is the same 6x5 plan, two storeys, one door on the south face, and
**the same thatch roof whatever the walls are** -- the roof is held constant
so it cannot carry a comparison the walls are supposed to be making. Read it
the way CLAUDE.md says to read a wall probe: four low obliques at ninety
degrees, then overhead, then ``N`` for the section. Each box is numbered by a
bar of blocks on the ground running east, because a run is judged in plan and
a vertical tally vanishes at an oblique.

    python tools/wallrun_probe.py --sheet design-mix  > out/wallrun-design-mix.slab.txt
    python tools/wallrun_probe.py --sheet design-bond > out/wallrun-design-bond.slab.txt
    python tools/wallrun_probe.py --sheet packing > out/wallrun-packing.slab.txt
    python tools/wallrun_probe.py --sheet kits    > out/wallrun-kits.slab.txt
    python tools/wallrun_probe.py --sheet glazing > out/wallrun-glazing.slab.txt
"""

from __future__ import annotations

import argparse
import sys
import zlib

sys.path.insert(0, ".")

from citysmith.build import (
    CORNER_BY_SIDES, SIDE_OFFSETS, WALL_CORNER_ROT, _QUARTER, _SIDE_ROT,
    _is_reflex, _normalized_whole_tiles, _roof_piece, _roof_rings,
    place_centered, place_tile, place_wall, rotated_footprint,
)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette
from citysmith.slab import Slab, encode

BOX_W, BOX_D = 6, 5
PAD, GAP = 2, 3

#: Boxes go in a GRID, not a row, and that is the frame cap rather than taste.
#: Ctrl+scroll camera height is capped at about 40 tiles (CLAUDE.md, measured),
#: and five boxes at a pitch of 13 is 65 tiles -- readable only by flying along
#: it, which puts the candidates in different frames and defeats the point of a
#: side-by-side. Three columns is 39 tiles wide and two rows is 24 deep, so a
#: whole sheet lands in one oblique.
COLS = 3


# -- placing a piece that spans more than one cell -----------------------------

def place_wall_span(asset, tx: int, tz: int, side: str, span: int,
                    y: float = 0.0):
    """Place a wall piece covering ``span`` cells from ``(tx, tz)`` along ``side``.

    The generalisation of :func:`citysmith.build.place_wall`, which is this
    with ``span=1``. Only the centre along the run changes; the rotation and
    the inset onto the cell boundary are the same rules and deliberately the
    same code -- **which axis the mesh is authored along is read off the
    collider, never assumed**, because ``Rural Wall 02`` is 0.5 x 2 x 2 (long
    on z) while ``castle wall 2x2`` is 2 x 2 x 0.5 (long on x) and both have
    to end up lying along the run.
    """
    rot = _SIDE_ROT[side]
    if asset.size_z > asset.size_x:
        rot = (rot + _QUARTER) % 24
    sx, sz = rotated_footprint(asset, rot)
    thickness = min(sx, sz)
    if side in ("n", "s"):
        cx = tx + span / 2.0
        cz = tz + (thickness / 2 if side == "n" else 1 - thickness / 2)
    else:
        cz = tz + span / 2.0
        cx = tx + (thickness / 2 if side == "w" else 1 - thickness / 2)
    return place_centered(asset, cx, cz, y, rot)


# -- how a run of N cells is covered ------------------------------------------

def _slots(length: int, wide: int, at: int) -> list[tuple[int, int]]:
    """``length`` cells as wide pieces with the single remainder at slot ``at``.

    ``at`` counts pieces, not cells: 0 puts the narrow panel first, ``n`` puts
    it last, anything between is mid-run. Every packer below is a choice of
    ``at``, which is the whole design space once the piece widths are fixed.
    """
    n, rem = divmod(length, wide)
    at = max(0, min(n, at))
    out, cell = [], 0
    for i in range(n + (1 if rem else 0)):
        span = 1 if (rem and i == at) else wide
        out.append((cell, span))
        cell += span
    return out


def pack_single(length: int, wide: int = 2, level: int = 0):
    """One piece per cell. What the board does today."""
    return [(i, 1) for i in range(length)]


def pack_greedy(length: int, wide: int = 2, level: int = 0):
    """Wide pieces from one end, the remainder at the other.

    The obvious rule, and the one to beat. Its cost is that the odd cell
    always lands at the same end of every run, so on a box the west face's
    short panel and the east face's are both at the north -- a diagonal of
    narrow panels across the building, which is a pattern nobody chose.
    """
    return _slots(length, wide, length // wide)


def pack_centred(length: int, wide: int = 2, level: int = 0):
    """Wide pieces butting outward from BOTH ends, the remainder in the middle.

    The same rule :func:`citysmith.build._run_panels` follows for a field
    wall, and for the same reason: **the corner is the part that reads**. A
    run is an odd number of cells long about half the time, and putting the
    short piece against a corner puts the one visible discontinuity exactly
    where the eye goes. Mid-run it lands where a door or a window would be.

    **Its cost is a column, and that is what this rule got wrong.** The
    remainder lands at the same slot on every course, so the narrow panel
    stacks -- on a Rural run of 7 that is a dark stripe of boarding running
    the full height of the wall, in a facade of wide panels. A packer that
    ignores the level cannot avoid it; every rule below takes one.
    """
    n = length // wide
    return _slots(length, wide, (n + 1) // 2)


def pack_bond(length: int, wide: int = 2, level: int = 0):
    """Remainder at alternating ENDS, course by course -- a running bond.

    The masonry answer, and the one a mason would recognise: every course is
    the previous one phase-shifted, so no vertical joint runs more than two
    courses. It breaks the column outright.

    What it costs is the thing `pack_centred` was protecting: the narrow panel
    now stands against a corner on every course. That is less bad here than it
    is on a field wall, because a building's run ends at a full-cell corner
    PIECE rather than in mid-air -- the narrow panel has a post beside it
    either way. Judge it on the board against `shift`.
    """
    n = length // wide
    return _slots(length, wide, 0 if level % 2 else n)


def pack_shift(length: int, wide: int = 2, level: int = 0):
    """Remainder walks along the INTERIOR slots, one per course.

    The rule that tries to have both: the column is broken because the narrow
    panel moves, and both corners stay flush because it never reaches an end.

    **A short run has nowhere to walk to, and that is the real limit.** A run
    of 5 is three pieces, so its only interior slot is the middle one and
    there is nothing to alternate with -- the fallback is to let it reach an
    end on odd courses, which is `bond` for that one length. A run of 7 has
    two interior slots and works properly. Runs of 5 are the single commonest
    length on Graybank (213 of 726), so this fallback is not a corner case and
    the sheet builds a 5 as well as a 7.
    """
    n = length // wide
    interior = list(range(1, n))
    if len(interior) < 2:
        # Nowhere to walk inside: a run of 5 is three pieces and its only
        # interior slot is the middle one. Walk every slot instead, ends
        # included -- a narrow panel beside a corner piece beats the column.
        interior = list(range(n + 1))
    return _slots(length, wide, interior[level % len(interior)])


def pack_bond_full(length: int, wide: int = 2, level: int = 0):
    """Every course offset by one cell, so NO vertical joint is ever shared.

    `bond` and `shift` both only move the *remainder*, so an even run -- which
    has none -- comes out identical on every course: a run of 6 is three wide
    panels with joints at 2 and 4, on every storey, for ever. Runs of 4, 6 and
    8 are about a third of every segment in all three towns, so that is not a
    corner the other rules can be forgiven for cutting.

    This one shifts the whole course instead: odd courses open with a narrow
    panel and take the wide pieces after it, so a run of 6 goes
    ``WW WW WW`` / ``n WW WW n`` and the joints land at 2,4 against 1,3,5.
    Two narrow pieces per odd course is the price, and on a timber-framed kit
    it may well be the wrong trade -- a real frame *does* carry its posts
    straight up, so broken joints read as masonry and stacked ones read as
    framing. That is a per-material choice and it is on the board to be made,
    not assumed.
    """
    if level % 2 == 0:
        return _slots(length, wide, length // wide)
    out, cell = [(0, 1)], 1
    while length - cell >= wide:
        out.append((cell, wide))
        cell += wide
    while cell < length:
        out.append((cell, 1))
        cell += 1
    return out


PACKERS = {"single": pack_single, "greedy": pack_greedy,
           "centred": pack_centred, "bond": pack_bond, "shift": pack_shift,
           "bondfull": pack_bond_full}


# -- the kits ------------------------------------------------------------------

#: One entry per kit: the whole wall family, keyed on ``folder`` because the
#: kit is the folder. Every name here is checked against its collider at build
#: time rather than trusted -- this file exists because a query trusted a size.
KITS = {
    "rural": dict(
        label="Rural", one="Rural Wall 01", wide="Rural Wall 02",
        window1=None, window2="Rural Wall Window",
        corner="Rural Corner", filler="Rural Inner Corner"),
    "castle": dict(
        label="Castle Fortified", one="castle wall 1x1", wide="castle wall 2x2",
        window1="castle wall 1x1 window", window2="castle wall 2x2 window",
        corner="castle wall corner 1x1 base", filler="castle wall filler"),
    "abandoned": dict(
        label="Abandoned Village", one="abandoned_village_wall_1x1_01",
        wide="abandoned_village_wall_2x1_01",
        window1=None, window2="abandoned_village_wall_window_2x1_01",
        corner="abandoned_village_wall_1x1_corner_01",
        filler="abandoned_village_corner_filler_01"),
    "tavern": dict(
        label="Tavern", one="Tavern Wall - Small 01", wide="Tavern Wall 01",
        window1=None, window2="Wall Only With Window",
        corner="Tavern no floor (1x1 a)", filler="Tavern Inner Corner 2"),
    # **The mix, and it is one kit rather than two.** `Village Roof Side
    # Wall 01/02`, `Tavern Wall 01`, `Wall Only With Window` and
    # `Tavern no floor (1x1 a)` are all `folder='Tavern'` -- so by this
    # project's own rule (the kit is the folder) they were never separate
    # families. What made them look separate is `group`, which names a *form*:
    # the Village panels are tagged `roof` because they ship in a roof set,
    # and CLAUDE.md already records that exact trap for the same three pieces.
    #
    # Pairing them fixes both halves of the problem at once. The Village
    # family has no wide piece; the Tavern wall has no 1-cell partner in use.
    # Together they are a complete family, and the Village panel is the only
    # 1-cell window in the whole Medieval Fantasy pack, which is the reason
    # the common house is built from it in the first place.
    #
    # `Wall Only With Window` is 2.00 tall, so the kit's wide *window* matches
    # the narrow pieces exactly -- only the plain `Tavern Wall 01` is 2.03.
    "mix": dict(
        label="Village+Tavern", one="Village Roof Side Wall 02",
        wide="Tavern Wall 01",
        window1="Village Roof Side Wall With Window 01",
        window2="Wall Only With Window",
        corner="Tavern no floor (1x1 a)", filler="Tavern Inner Corner 2"),
    # The common house as shipped. Its panels are ``group='roof'`` in the
    # Tavern folder -- the gable side, not the tavern's own wall -- and there
    # is no wide piece in that family at all. This is the control everything
    # else is judged against, and it is what 28 of Forest Church's 51
    # buildings look like today.
    "village": dict(
        label="Village (shipped)", one="Village Roof Side Wall 02", wide=None,
        window1="Village Roof Side Wall With Window 01", window2=None,
        corner="Tavern no floor (1x1 a)", filler="Tavern Inner Corner 2"),
}

#: Held constant across every box on every sheet, so the roof cannot carry a
#: comparison the walls are supposed to be making.
ROOF = ("Thatched Roof 01", "Thatched Roof Corner 01",
        "Thatched Roof Inner Corner 01", "Thatched roof flat 01",
        "Thatched Chimney")


#: Each candidate is a kit, a packing rule, a glazing rule and a corner rule.
#: ``glaze`` is "none", "deal:N" (one panel in N by a stable hash -- the shape
#: the board uses today) or "face" (exactly one window per wall face, centred
#: on the run).
SHEETS: dict[str, list[dict]] = {
    # Bare runs, read close. `swatch-pack` varies the rule in one material,
    # `swatch-kit` varies the material under one rule.
    "swatch-pack": [
        dict(label="1-cell (shipped)", kit="village", pack="single", glaze="none"),
        dict(label="1-cell, castle", kit="castle", pack="single", glaze="none"),
        dict(label="wide, castle", kit="castle", pack="centred", glaze="none"),
        dict(label="wide, castle, glazed", kit="castle", pack="centred", glaze="deal:2"),
    ],
    # **Where the narrow panel goes, course by course.** `centred` puts it in
    # the same slot on every storey, so it stacks into a column -- a dark
    # stripe of boarding running the full height of a Rural wall. Three storeys
    # each, all in one material, so only the rule varies.
    "design-bond": [
        dict(label="centred, 7 (stacks)", kit="rural", pack="centred",
             glaze="none", len=7, storeys=3),
        dict(label="bond, 7", kit="rural", pack="bond", glaze="none",
             len=7, storeys=3),
        dict(label="shift, 7", kit="rural", pack="shift", glaze="none",
             len=7, storeys=3),
        dict(label="bondfull, 7", kit="rural", pack="bondfull", glaze="none",
             len=7, storeys=3),
        # The even case none of the first three can touch: no remainder, so
        # every joint is shared on every course unless the whole run shifts.
        dict(label="bondfull, 6 (even)", kit="rural", pack="bondfull",
             glaze="none", len=6, storeys=3),
    ],
    # **The mix.** Village narrow with the Tavern wide panel -- one folder, so
    # one kit. Against the shipped Village-only control and against the
    # all-Tavern pairing that was probed first.
    "design-mix": [
        dict(label="Village 1-cell (shipped)", kit="village", pack="single",
             glaze="deal:3", len=7, storeys=3),
        dict(label="Tavern narrow + wide", kit="tavern", pack="centred",
             glaze="deal:3", len=7, storeys=3),
        dict(label="Village + Tavern, centred", kit="mix", pack="centred",
             glaze="deal:3", len=7, storeys=3),
        dict(label="Village + Tavern, shift", kit="mix", pack="shift",
             glaze="deal:3", len=7, storeys=3),
        dict(label="Village + Tavern, bondfull", kit="mix", pack="bondfull",
             glaze="deal:3", len=7, storeys=3),
    ],
    # Odd runs, where a 1-cell piece has to sit next to a 2-cell one. This is
    # where a kit whose wide piece is a different height gives itself away,
    # and where the two packers stop agreeing.
    "swatch-odd": [
        dict(label="Village 1-cell, 5 (shipped)", kit="village", pack="single",
             glaze="none", len=5),
        dict(label="Castle wide greedy, 5", kit="castle", pack="greedy",
             glaze="none", len=5),
        dict(label="Castle wide centred, 5", kit="castle", pack="centred",
             glaze="none", len=5),
        dict(label="Tavern wide centred, 5", kit="tavern", pack="centred",
             glaze="none", len=5),
        dict(label="Rural wide centred, 7", kit="rural", pack="centred",
             glaze="none", len=7),
    ],
    "swatch-kit": [
        dict(label="Village 1-cell (shipped)", kit="village", pack="single", glaze="deal:3"),
        dict(label="Tavern wide", kit="tavern", pack="centred", glaze="deal:3"),
        dict(label="Rural wide", kit="rural", pack="centred", glaze="deal:3"),
        dict(label="Castle wide", kit="castle", pack="centred", glaze="deal:3"),
        dict(label="Abandoned wide", kit="abandoned", pack="centred", glaze="deal:3"),
    ],
    "packing": [
        dict(label="1-cell (shipped)", kit="rural", pack="single", glaze="deal:3"),
        dict(label="wide, greedy", kit="rural", pack="greedy", glaze="deal:3"),
        dict(label="wide, centred", kit="rural", pack="centred", glaze="deal:3"),
        dict(label="wide, filler corner", kit="rural", pack="centred",
             glaze="deal:3", corner="filler"),
    ],
    "kits": [
        dict(label="Village (shipped)", kit="village", pack="single", glaze="deal:3"),
        dict(label="Tavern wide", kit="tavern", pack="centred", glaze="deal:3"),
        dict(label="Rural wide", kit="rural", pack="centred", glaze="deal:3"),
        dict(label="Castle wide", kit="castle", pack="centred", glaze="deal:3"),
        dict(label="Abandoned wide", kit="abandoned", pack="centred", glaze="deal:3"),
    ],
    "glazing": [
        dict(label="1-cell, 1-in-3 (shipped)", kit="castle", pack="single", glaze="deal:3"),
        dict(label="wide, 1-in-3 panels", kit="castle", pack="centred", glaze="deal:3"),
        dict(label="wide, 1-in-2 panels", kit="castle", pack="centred", glaze="deal:2"),
        dict(label="wide, one per face", kit="castle", pack="centred", glaze="face"),
        dict(label="wide, none", kit="castle", pack="centred", glaze="none"),
    ],
}


#: A swatch sheet is straight runs on bare ground -- no floor, no corners, no
#: roof. It exists because the building sheets could not answer the question
#: they were built for: at the height where a whole 6x5 box fits the frame, a
#: panel seam is two or three pixels wide, and the Ctrl+scroll height cap
#: (CLAUDE.md, measured) means the camera cannot come closer *and* keep the
#: comparison in one shot. A bare run 6 cells long is 6 tiles instead of 39,
#: so four of them sit side by side inside the cap with the joint legible.
#:
#: This does not replace the building sheets and neither replaces the other.
#: A swatch says whether the seam shows; only a box says what the rule does at
#: a corner, at a doorway and where two faces meet -- which is where every
#: previous wall finding on this project actually went wrong.
SWATCH_LEN = 6
SWATCH_PITCH = 9


def swatch_sheet(designs, byname, grass, tally, out: list) -> None:
    """One straight run per candidate, side by side, facing south.

    ``len`` on a design overrides :data:`SWATCH_LEN`. **An odd run is the case
    that matters and the even one is the easy one**: 6 cells packs into three
    wide panels with nothing left over, so it can never show what happens
    where a 1-cell piece meets a 2-cell one. Runs of 5 are the single commonest
    length on Graybank (213 of 726) and 5 and 7 together are a third of every
    run in all three towns.
    """
    for i, d in enumerate(designs):
        kit = KITS[d["kit"]]
        one = byname.get(kit["one"])
        wide = byname.get(kit["wide"]) if kit["wide"] else None
        win1 = byname.get(kit["window1"]) if kit["window1"] else None
        win2 = byname.get(kit["window2"]) if kit["window2"] else None
        if one is None:
            continue
        packer = PACKERS[d["pack"] if wide is not None else "single"]
        storey_h = one.size_y
        length = d.get("len", SWATCH_LEN)
        ox = i * SWATCH_PITCH

        for dz in range(-1, 3):
            for dx in range(-1, length + 1):
                out.append(place_tile(grass, ox + dx, dz, -grass.size_y))
        # The bar runs east in front of the swatch, in the one row of ground
        # the wall does not stand on.
        for t in range(i + 1):
            out.append(place_tile(tally, ox + t, 2, 0.0))

        for level in range(d.get("storeys", 2)):
            y = level * storey_h
            for k, (off, span) in enumerate(packer(length, 2, level)):
                piece = None
                if glazed(d["glaze"], d["kit"], "s", level, k, off, span,
                          length):
                    piece = win2 if span == 2 else win1
                if piece is None:
                    piece = wide if span == 2 else one
                out.append(place_wall_span(piece, ox + off, 0, "s", span, y))


#: Which sheets are bare runs rather than whole buildings. An explicit set,
#: not a name prefix -- the prefix test silently built `design-bond` as five
#: 6x5 boxes with corners and a roof, ignoring its `len` and answering a
#: question nobody asked. A sheet's shape is a property of the sheet.
SWATCH_SHEETS = frozenset({
    "swatch-pack", "swatch-kit", "swatch-odd", "design-bond", "design-mix",
})


def sides_of(x: int, z: int) -> set[str]:
    s = set()
    if x == 0:
        s.add("w")
    if x == BOX_W - 1:
        s.add("e")
    if z == 0:
        s.add("n")
    if z == BOX_D - 1:
        s.add("s")
    return s


def run_on(side: str, corners: bool) -> tuple[int, int, int]:
    """The straight stretch of one face, as a ``(x, z, length)`` start cell.

    With ``corners`` on, the two end cells of the north and south faces are
    spoken for by a corner piece and the run is what is left between them.
    The east and west faces always stop short of the corners, because the
    corner cell belongs to whichever face claims it and claiming it twice is
    the doubled geometry the corner piece was introduced to remove.
    """
    if side == "n":
        return (1, 0, BOX_W - 2) if corners else (0, 0, BOX_W)
    if side == "s":
        return (1, BOX_D - 1, BOX_W - 2) if corners else (0, BOX_D - 1, BOX_W)
    if side == "w":
        return (0, 1, BOX_D - 2)
    return (BOX_W - 1, 1, BOX_D - 2)


def glazed(rule: str, kit: str, side: str, level: int, k: int,
           off: int, span: int, length: int) -> bool:
    """Whether this panel carries glass.

    ``deal:N`` is the board's own rule stated over *panels* rather than
    segments, which is the substantive change a wide piece forces: at
    one-in-three a run of six is two windows when it is six 1-cell panels and
    one window when it is three 2-cell ones. **Same rate, half the glass** --
    so the shipped number is not transferable, and the glazing sheet exists to
    pick a new one rather than to assume it.

    The north face stays blind on every candidate, which is what
    ``build.GLAZE_RATE`` already does for the face opposite the door: a town
    looked identical from all four sides before that, and this probe should
    not quietly undo it.
    """
    if rule == "none" or side == "n":
        return False
    if rule == "face":
        return off <= (length - span) / 2 < off + span
    n = int(rule.split(":")[1])
    # Ground floors keep one fewer window than the storeys above -- privacy,
    # and the doorway already breaks those runs. Same rule as `glaze_rate`.
    rate = n + (1 if level == 0 else 0)
    key = zlib.crc32(f"{kit}:{side}:{level}:{k}:{off}".encode())
    return key % rate == 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Probe how a run of wall cells is covered.")
    ap.add_argument("--sheet", choices=sorted(SHEETS), default="packing")
    args = ap.parse_args()

    palette = Palette(load_or_build(), MEDIEVAL)
    byname: dict[str, object] = {}
    for a in palette.catalog.assets:
        byname.setdefault(a.name, a)

    grass = palette.require("ground")
    floor = palette.require("floor")
    tally = byname.get("md_stairblock_01") or floor
    entry = byname.get("Door -Peasant")
    side_a, corner_a, inner_a, cap_a, chim_a = (byname.get(n) for n in ROOF)

    designs = SHEETS[args.sheet]
    out: list = []
    if args.sheet in SWATCH_SHEETS:
        swatch_sheet(designs, byname, grass, tally, out)
        for i, d in enumerate(designs):
            kit = KITS[d["kit"]]
            print(f"# {i + 1}: {d['label']:26s} kit={kit['label']:18s} "
                  f"pack={d['pack']:7s} glaze={d['glaze']}", file=sys.stderr)
        byid = {a.id: a for a in palette.catalog.assets}
        print(encode(_normalized_whole_tiles(Slab(out), byid)))
        print(f"# {len(out)} placements", file=sys.stderr)
        return
    pitch_x = BOX_W + 2 * PAD + GAP
    pitch_z = BOX_D + 2 * PAD + GAP
    cells = {(x, z) for x in range(BOX_W) for z in range(BOX_D)}
    door_cell = (BOX_W // 2, BOX_D - 1)

    for i, d in enumerate(designs):
        kit = KITS[d["kit"]]
        one = byname.get(kit["one"])
        wide = byname.get(kit["wide"]) if kit["wide"] else None
        win1 = byname.get(kit["window1"]) if kit["window1"] else None
        win2 = byname.get(kit["window2"]) if kit["window2"] else None
        nook = byname.get(kit["corner"])
        filler = byname.get(kit["filler"]) if kit["filler"] else None
        if one is None:
            print(f"# {d['label']}: {kit['one']!r} missing, skipped",
                  file=sys.stderr)
            continue

        # **The height is checked, not trusted.** Every course on a map is
        # pitched at the 1-cell piece's own height, so a wide piece that
        # disagrees raises its own storey and puts the roof out of line.
        # Tavern's does, by 0.03. It is reported and still built, because
        # seeing what 0.03 looks like on a board is the point of the sheet.
        storey_h = one.size_y
        note = ""
        if wide is not None and abs(wide.size_y - storey_h) > 1e-6:
            note = (f"  !! wide is {wide.size_y:g} tall vs 1-cell {storey_h:g}"
                    f" ({wide.size_y - storey_h:+.2f})")
        if wide is None and d["pack"] != "single":
            note = "  !! no wide piece in this kit -- built 1-cell"

        packer = PACKERS[d["pack"] if wide is not None else "single"]
        use_corner = d.get("corner", "cell") == "cell" and nook is not None
        ox = (i % COLS) * pitch_x
        oz = (i // COLS) * pitch_z

        for dz in range(-PAD, BOX_D + PAD):
            for dx in range(-PAD, BOX_W + PAD):
                out.append(place_tile(grass, ox + dx, oz + dz, -grass.size_y))
        # Numbered by a BAR running east, not a stack: a tally read in plan is
        # the only one that survives both the oblique and the overhead.
        for t in range(i + 1):
            out.append(place_tile(tally, ox + t, oz + BOX_D + 1, 0.0))
        for x, z in sorted(cells):
            out.append(place_tile(floor, ox + x, oz + z, 0.0))
        top = floor.size_y

        for level in range(2):
            y = top + level * storey_h

            if use_corner:
                for (x, z) in sorted(cells):
                    turn = CORNER_BY_SIDES.get(frozenset(sides_of(x, z)))
                    if turn is None:
                        continue
                    if level == 0 and (x, z) == door_cell:
                        continue
                    out.append(place_tile(nook, ox + x, oz + z, y,
                                          WALL_CORNER_ROT[turn]))

            for side in ("n", "e", "s", "w"):
                rx, rz, length = run_on(side, use_corner)
                # The door takes a cell out of the ground course and splits
                # the face into two shorter runs. That is the case a packer
                # has to get right, and the reason the doorway is mid-face
                # rather than at a corner.
                door_at = None
                if level == 0 and side == "s" and entry is not None:
                    off0 = door_cell[0] - rx
                    if 0 <= off0 < length:
                        door_at = off0
                if door_at is None:
                    spans = packer(length)
                else:
                    spans = (packer(door_at) + [(door_at, 0)]
                             + [(door_at + 1 + o, n)
                                for o, n in packer(length - door_at - 1)])

                for k, (off, span) in enumerate(spans):
                    if side in ("n", "s"):
                        cx, cz = rx + off, rz
                    else:
                        cx, cz = rx, rz + off
                    if span == 0:
                        out.append(place_wall(entry, ox + cx, oz + cz, side, y))
                        continue
                    piece = None
                    if glazed(d["glaze"], d["kit"], side, level, k, off, span,
                              length):
                        piece = win2 if span == 2 else win1
                    if piece is None:
                        piece = wide if span == 2 else one
                    out.append(place_wall_span(piece, ox + cx, oz + cz, side, span, y))

            # With no full-cell corner piece the north and south faces run the
            # full width and the east and west ones stop short, so what is
            # left at each outside corner is a 0.5 x 0.5 notch -- and the kit
            # ships a piece exactly that size called ``corner_filler``. That
            # is the treatment our full-cell corner replaced without ever
            # being compared against it.
            #
            # **The filler goes in the notch, not on the corner cell.**
            # `place_wall` would centre it on the cell, where the long face's
            # panel already is; it belongs one panel-thickness along the run
            # from there. Measured rather than eyeballed: laid on the cell it
            # overlapped the north panel 0.5 x 0.25 at every corner, eight
            # pairs on this sheet.
            if not use_corner and filler is not None:
                thick = min(filler.size_x, filler.size_z)
                for (x, z) in sorted(cells):
                    exposed = sides_of(x, z)
                    if CORNER_BY_SIDES.get(frozenset(exposed)) is None:
                        continue
                    side = "w" if "w" in exposed else "e"
                    cx = x + (thick / 2 if side == "w" else 1 - thick / 2)
                    cz = z + (1 - thick / 2 if "n" in exposed else thick / 2)
                    out.append(place_centered(filler, ox + cx, oz + cz, y,
                                              _SIDE_ROT[side]))

        roof_y = top + 2 * storey_h
        rings = _roof_rings(cells)
        rise = side_a.size_y if side_a is not None else 1.0
        crown = [c for c in sorted(cells) if rings[c] == max(rings.values())]
        chimney_at = crown[len(crown) // 2] if (crown and chim_a) else None
        for (x, z) in sorted(cells):
            r = rings[(x, z)]
            y = roof_y + r * rise
            if (x, z) == chimney_at and chim_a is not None:
                out.append(place_tile(chim_a, ox + x, oz + z, y))
                continue
            fall = tuple(s for s, dx, dz in SIDE_OFFSETS
                         if rings.get((x + dx, z + dz), -1) < r)
            piece, rot = _roof_piece(fall, side_a, corner_a, cap_a, inner_a,
                                     _is_reflex(rings, x, z, fall))
            if piece is not None:
                out.append(place_tile(piece, ox + x, oz + z, y, rot))

        print(f"# {i + 1}: {d['label']:24s} kit={kit['label']:18s} "
              f"pack={d['pack']:7s} glaze={d['glaze']:7s} "
              f"corner={'cell' if use_corner else 'filler'}{note}",
              file=sys.stderr)

    byid = {a.id: a for a in palette.catalog.assets}
    print(encode(_normalized_whole_tiles(Slab(out), byid)))
    print(f"# {len(out)} placements", file=sys.stderr)


if __name__ == "__main__":
    main()
