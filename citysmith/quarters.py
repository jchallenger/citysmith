"""Which quarter of town a cell is in -- derived, and only where it is real.

`docs/district-surfaces.md` is the design. The short version:

**Authored districts are unusable.** FTG carries none at all -- 0 of 35, 0 of
150, 0 of 991 buildings across the three exports here -- and MFCG's wards put
47 of Forest Church's 51 buildings in one ward that contains the temple, the
barracks and both stables. A district-keyed style read off the export is a
no-op dressed as a feature.

**Derived quarters are real, and only on a town big enough to have them.**
Taking each building's eight nearest neighbours and asking how often they share
its kind, against the rate the same mix shuffled would give:

    Forest Church      1.06x all kinds,  0.97x excluding houses
    Graybank           1.01x             0.86x
    East Tradebourne   1.36x             1.27x

and on East Tradebourne, per kind: smithies 3.37x in clumps of 55 and 47,
shops 4.34x in clumps of 21 and 17. Those are a craft quarter and a market
street, authored in the export. Graybank's biggest shop clump is *two*.

So this module measures before it fires. :func:`quarter_map` returns nothing at
all on a town whose kinds do not cluster, and the caller falls back to one
honest palette rather than painting six quarters three cells wide.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

#: Building kind -> the quarter it argues for.
QUARTER_OF_KIND: dict[str, str] = {
    "smithy": "craft",
    "stable": "craft",
    "shed": "craft",
    "shop": "market",
    "apothecary": "market",
    "tavern": "market",
    "warehouse": "docks",
    "guildhall": "civic",
    "temple": "civic",
    "manor": "civic",
    "barracks": "civic",
    "house": "residential",
}

#: What a cell is called when no building is near enough to claim it.
OUTSKIRTS = "outskirts"

#: The quarter a building of an unmapped kind argues for.
DEFAULT_QUARTER = "residential"

#: How far a building's influence reaches, in tiles. 14 is a little under three
#: house frontages -- far enough that a terrace speaks for the lane behind it,
#: short enough that a smithy does not colour the next street.
INFLUENCE_TILES = 14.0

#: Neighbours sampled when measuring whether kinds cluster at all.
NEIGHBOURS = 8

#: Clustering lift below which quarters are not used. Graybank measures 0.86x
#: excluding houses and East Tradebourne 1.27x, so this sits between two real
#: towns rather than being chosen from taste. A town under it gets tier 1
#: surfaces and no quarters -- which is correct, not a degradation: a village
#: does not have a craft quarter to find.
MIN_LIFT = 1.20

#: A quarter covering less of the paved board than this is not worth painting;
#: it reads as dirt rather than as design. Reported, not enforced.
MIN_SHARE = 0.03


def centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    n = max(1, len(ring))
    return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)


def buildings_of(tm) -> list[tuple[tuple[float, float], str]]:
    """Every building on the tilemap as (centroid in CELLS, kind).

    **Read off the TileMap and not the Layout, and the reason is coordinates.**
    A `Layout` is in whole-map tiles; a cropped `TileMap` is in its own. Keying
    a quarter off the layout and then indexing it by a cropped cell reads the
    quarter from wherever that cell number happens to land on the full map --
    300 tiles away, on the block this was caught with. The tilemap carries the
    building id in every cell it occupies, and the id leads with the kind, so
    the whole measurement can be made in the coordinate space it is used in.

    This is the same rule as `verify.check_placements`: measure the artifact.
    """
    cells: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                cells[bid].append((x, z))
    out = []
    for bid, pts in cells.items():
        n = len(pts)
        out.append((
            (sum(p[0] for p in pts) / n + 0.5, sum(p[1] for p in pts) / n + 0.5),
            bid.split("-")[0],
        ))
    return out


def clustering_lift(pts, *, exclude_houses: bool = True) -> float:
    """How much more often a building's neighbours share its kind than chance.

    1.0 is "no structure at all". Houses are excluded by default because they
    are 70-85% of every town and their own mass drags the figure up without
    saying anything about quarters -- Graybank measures 1.01x with them and
    0.86x without, and the second number is the honest one.
    """
    if exclude_houses:
        pts = [(c, k) for c, k in pts if k != "house"]
    n = len(pts)
    if n <= NEIGHBOURS + 1:
        return 0.0

    agree = total = 0
    for i, (p, kind) in enumerate(pts):
        near = sorted(
            (math.dist(p, q), k) for j, (q, k) in enumerate(pts) if j != i
        )[:NEIGHBOURS]
        agree += sum(1 for _, k in near if k == kind)
        total += len(near)

    base = Counter(k for _, k in pts)
    expected = sum((c / n) * ((c - 1) / (n - 1)) for c in base.values())
    if expected <= 0.0 or total == 0:
        return 0.0
    return (agree / total) / expected


def quarter_map(tm, *, min_lift: float = MIN_LIFT
                ) -> dict[tuple[int, int], str] | None:
    """Cell -> quarter, or ``None`` when this town has no quarters to speak of.

    ``None`` is the important return value and it is not a failure: on a
    village the honest answer is that there are no quarters, and a caller that
    treats it as one keys nothing on it.
    """
    pts = buildings_of(tm)
    if not pts:
        return None
    if clustering_lift(pts) < min_lift:
        return None

    # Bucketed so a 991-building town is not an O(cells x buildings) sweep.
    cell = int(INFLUENCE_TILES) + 2
    grid: dict[tuple[int, int], list[tuple[tuple[float, float], str]]] = defaultdict(list)
    for c, kind in pts:
        grid[(int(c[0] // cell), int(c[1] // cell))].append(
            (c, QUARTER_OF_KIND.get(kind, DEFAULT_QUARTER))
        )

    out: dict[tuple[int, int], str] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            gx, gz = int(x // cell), int(z // cell)
            weight: Counter[str] = Counter()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for c, quarter in grid.get((gx + dx, gz + dz), ()):
                        d = math.dist((x + 0.5, z + 0.5), c)
                        if d < INFLUENCE_TILES:
                            # Linear falloff, so a cell between two quarters
                            # goes to the nearer one rather than to whichever
                            # has more buildings somewhere off in the distance.
                            weight[quarter] += (INFLUENCE_TILES - d) / INFLUENCE_TILES
            out[(x, z)] = weight.most_common(1)[0][0] if weight else OUTSKIRTS
    return out


def shares(quarters: dict[tuple[int, int], str], tm) -> dict[str, float]:
    """Each quarter's share of the *paved* board, for reporting.

    Paved rather than total, because a quarter is a thing you walk through and
    open country is not where it shows.
    """
    from . import raster as R

    paved = [(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.surface[z][x] in (R.STREET, R.PLAZA, R.LANE)]
    if not paved:
        return {}
    count = Counter(quarters.get(c, OUTSKIRTS) for c in paved)
    return {k: v / len(paved) for k, v in count.most_common()}
