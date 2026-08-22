"""Playtest verification for a rasterised city.

The point of this module is to answer one question before anything is pasted
into TaleSpire: *would this map actually work at the table?*

It reads the same :class:`~citysmith.raster.TileMap` the asset builder reads, so
what it checks is the geometry that will really be emitted -- not an idealised
plan. TaleSpire does not enforce collision (minis are free-placed and rest on
tile colliders), so these checks are about geometry validity and tactical
legibility rather than engine constraints:

* can a creature walk from a gate to every door?
* is the street network one connected town, or several islands?
* is each street wide enough for what has to drive down it?
* are streets wide enough for creatures to pass abreast?
* does every building have a way in?
* does the result fit TaleSpire's board and slab limits?
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field

from .layout import TILE_FEET
from .raster import (
    CART_ROAD,
    CART_TILES,
    FLOOR,
    GROUND,
    LANE_ROAD,
    LANE_TILES,
    MAIN_ROAD,
    MAIN_STREET_TILES,
    OPEN,
    PIER,
    PLAZA,
    SIDES,
    STREET,
    STREET_STANDARD,
    WATER,
    TileMap,
    components,
    open_width_at,
    reachable_from,
)

#: TaleSpire board limits, confirmed from BouncyRock's published figures.
BOARD_MAX_TILES = 1_000_000
BOARD_MAX_SPAN = 2000

#: A creature occupies one tile; two must pass abreast on any walkable way.
#: The per-class standards a *road* is held to live in :mod:`citysmith.raster`
#: alongside the rasteriser that lays them down; this is the floor below which
#: nothing is playable at all, whatever it was meant to be.
MIN_STREET_TILES = LANE_TILES

#: Class -> how it reads in a report.
_CLASS_LABEL = {MAIN_ROAD: "main street", CART_ROAD: "cart street", LANE_ROAD: "lane"}

#: Classes that carry vehicles. A cart is two tiles wide, so one of these
#: pinched under :data:`~citysmith.raster.CART_TILES` cannot let a cart past a
#: pedestrian -- the route is shut for the traffic it exists to carry.
_THROUGH_ROUTES = (MAIN_ROAD, CART_ROAD)

LEVELS = {"pass": 0, "warn": 1, "fail": 2}


@dataclass
class Finding:
    level: str  # "pass" | "warn" | "fail"
    check: str
    detail: str

    def __str__(self) -> str:
        mark = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL"}[self.level]
        return f"[{mark}] {self.check}: {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)

    def add(self, level: str, check: str, detail: str) -> None:
        self.findings.append(Finding(level, check, detail))

    @property
    def failed(self) -> bool:
        return any(f.level == "fail" for f in self.findings)

    @property
    def worst(self) -> str:
        return max((f.level for f in self.findings), key=lambda v: LEVELS[v], default="pass")

    def text(self) -> str:
        lines = [str(f) for f in sorted(self.findings, key=lambda f: -LEVELS[f.level])]
        return "\n".join(lines)


def verify(tm: TileMap, *, asset_count: int | None = None, slab_count: int | None = None,
           max_slab_bytes: int | None = None) -> Report:
    """Run every playability check against a rasterised city."""
    report = Report()
    gates = sorted(tm.gates)

    # -- connectivity ---------------------------------------------------------
    comps = components(tm)
    walkable = sum(len(c) for c in comps)
    largest = len(comps[0]) if comps else 0
    report.stats["walkable_tiles"] = walkable
    report.stats["components"] = len(comps)

    if not comps:
        report.add("fail", "connectivity", "no walkable space at all")
    else:
        share = 100 * largest / walkable
        big = [c for c in comps if len(c) >= 400]
        if len(big) > 1:
            report.add(
                "fail", "connectivity",
                f"{len(big)} large districts are not connected to each other "
                f"(largest holds {share:.0f}% of walkable space) -- the party "
                "cannot walk between them",
            )
        else:
            pockets = len(comps) - 1
            report.add(
                "pass", "connectivity",
                f"one connected town covering {share:.0f}% of walkable space"
                + (f", plus {pockets} enclosed courtyards" if pockets else ""),
            )

    # -- gates ----------------------------------------------------------------
    if gates:
        reach = reachable_from(tm, gates)
        reached = sum(1 for z in range(tm.depth) for x in range(tm.width) if reach[z][x])
        report.stats["reachable_from_gates"] = reached
        report.add(
            "pass" if reached >= largest else "warn", "gates",
            f"{len(gates)} gate tiles reach {reached} of {walkable} walkable tiles "
            f"({100*reached/max(1,walkable):.0f}%)",
        )
    else:
        reach = reachable_from(tm, [(x, z) for z in range(tm.depth)
                                    for x in (0, tm.width - 1) if tm.is_walkable(x, z)])
        report.add("warn", "gates", "no gates found; routing from the map edge instead")

    # -- building access ------------------------------------------------------
    total_buildings = len({b for row in tm.building for b in row if b})
    with_doors = len(tm.doors)
    landlocked = []
    for bid, cells in tm.doors.items():
        x, z, side = cells[0]
        dx, dz = next((d, e) for s, d, e in SIDES if s == side)
        if not (tm.inside(x + dx, z + dz) and reach[z + dz][x + dx]):
            landlocked.append(bid)

    report.stats["buildings"] = total_buildings
    report.stats["reachable_buildings"] = with_doors - len(landlocked)
    no_door = total_buildings - with_doors
    unreachable = no_door + len(landlocked)
    pct = 100 * (total_buildings - unreachable) / max(1, total_buildings)

    level = "pass" if pct >= 98 else "warn" if pct >= 90 else "fail"
    report.add(
        level, "building access",
        f"{total_buildings - unreachable} of {total_buildings} buildings "
        f"({pct:.1f}%) can be entered from the street network"
        + (f"; {no_door} have no doorway and {len(landlocked)} open only into "
           f"sealed courtyards" if unreachable else ""),
    )

    # -- street width ---------------------------------------------------------
    # Two separate questions, because a street can fail at two different jobs.
    # The floor first: a creature occupies one tile, so anywhere under two the
    # party walks single file. Then the standard the stretch was actually laid
    # to -- a cart is two tiles wide, so a through-route under three tiles
    # cannot let a cart past a pedestrian and under four cannot let two carts
    # pass at all. Reporting one flat number against every surface hides both:
    # an alley at two tiles is correct, a gate road at two tiles is a blockage.
    narrow = 0
    sampled = 0
    # class -> [tiles, tiles meeting that class's standard, tiles under a cart]
    by_class: dict[str, list[int]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            surface = tm.surface[z][x]
            if surface not in (STREET, PLAZA):
                continue
            sampled += 1
            span = open_width_at(tm, x, z)
            if span < LANE_TILES:
                narrow += 1
            # A plaza is an open square, not a road, so it is held to the floor.
            cls = tm.street_class[z][x] if surface == STREET else ""
            cls = cls or LANE_ROAD
            row = by_class.setdefault(cls, [0, 0, 0])
            row[0] += 1
            if span >= STREET_STANDARD[cls]:
                row[1] += 1
            if span < CART_TILES:
                row[2] += 1

    if sampled:
        share = 100 * narrow / sampled
        report.stats["narrow_street_tiles"] = narrow
        report.add(
            "pass" if share < 5 else "warn", "street width",
            f"{narrow} of {sampled} street tiles ({share:.1f}%) are under "
            f"{LANE_TILES:.0f} tiles ({LANE_TILES*TILE_FEET:.0f} ft) wide -- "
            "a creature fills one tile, so two cannot pass abreast there",
        )

        parts = []
        below_standard = through = pinched = 0
        for cls in (MAIN_ROAD, CART_ROAD, LANE_ROAD):
            row = by_class.get(cls)
            if not row:
                continue
            tiles, meeting, under_cart = row
            standard = STREET_STANDARD[cls]
            parts.append(
                f"{_CLASS_LABEL[cls]} {meeting}/{tiles} tiles hold "
                f"{standard:.0f} ({standard*TILE_FEET:.0f} ft)"
            )
            below_standard += tiles - meeting
            if cls in _THROUGH_ROUTES:
                through += tiles
                pinched += under_cart
        report.stats["street_tiles_by_class"] = {c: v[0] for c, v in by_class.items()}
        report.stats["pinched_vehicle_tiles"] = pinched

        pinch = 100 * pinched / max(1, through)
        short = 100 * below_standard / sampled
        level = "fail" if pinch >= 25 else "warn" if pinch >= 5 or short >= 20 else "pass"
        report.add(
            level, "vehicle width",
            ", ".join(parts)
            + f"; {pinched} of {through} through-route tiles ({pinch:.1f}%) are "
              f"under {CART_TILES:.0f} tiles ({CART_TILES*TILE_FEET:.0f} ft), "
              "where a 10 ft cart cannot get past a pedestrian",
        )

    # -- surfaces -------------------------------------------------------------
    counts = tm.counts()
    report.stats["surfaces"] = counts
    if counts.get(WATER) and tm.bridges:
        report.add("pass", "crossings",
                   f"{len(tm.bridges)} bridge(s) added to join districts split by water")
    elif counts.get(WATER):
        report.add("pass", "crossings", "water present; no bridge was needed")

    # -- TaleSpire limits -----------------------------------------------------
    if max(tm.width, tm.depth) > BOARD_MAX_SPAN:
        report.add("fail", "board size",
                   f"{tm.width}x{tm.depth} exceeds the {BOARD_MAX_SPAN} grid-unit board limit")
    else:
        report.add("pass", "board size",
                   f"{tm.width}x{tm.depth} tiles "
                   f"({tm.width_feet:.0f}x{tm.depth_feet:.0f} ft) fits the "
                   f"{BOARD_MAX_SPAN}x{BOARD_MAX_SPAN} board")

    if asset_count is not None:
        report.stats["assets"] = asset_count
        share = 100 * asset_count / BOARD_MAX_TILES
        report.add(
            "pass" if asset_count <= BOARD_MAX_TILES else "fail", "asset budget",
            f"{asset_count:,} assets = {share:.1f}% of the {BOARD_MAX_TILES:,} "
            "per-board limit",
        )
    if slab_count is not None:
        detail = f"{slab_count} slab paste(s)"
        if max_slab_bytes is not None:
            detail += f", largest {max_slab_bytes:,} compressed bytes (cap 30,720)"
        report.add(
            "pass" if (max_slab_bytes or 0) <= 30720 else "fail", "slab export", detail
        )


    pinches = through_route_pinches(tm)
    through = sum(1 for z in range(tm.depth) for x in range(tm.width)
                  if getattr(tm, "street_class", None)
                  and tm.street_class[z][x] in ("main", "cart"))
    if through:
        share = 100 * len(pinches) / through
        report.add(
            "pass" if not pinches else ("warn" if share < 5 else "fail"),
            "cart clearance",
            f"{len(pinches)} of {through} through-route tiles ({share:.1f}%) have an "
            f"open cross-section under 3 tiles (15 ft) -- a building overlapping a "
            f"widened street re-narrows it, and a 10 ft cart cannot pass there",
        )

    return report


# -- visual confirmation ------------------------------------------------------

_SURFACE_COLOUR = {
    GROUND: "#4a4f44",
    "field": "#6d7040",
    WATER: "#22485e",
    STREET: "#8a8272",
    PLAZA: "#b9a06a",
    PIER: "#7a6242",
    FLOOR: "#2e3138",
}


def tilemap_svg(tm: TileMap, *, scale: int = 3, overlay: bool = True) -> str:
    """Render the rasterised city, optionally flagging problems.

    Horizontal runs of identical cells are merged into single rectangles --
    a 250x250 map is 62,000 cells and one rect each makes an unopenable file.
    """
    w, h = tm.width * scale, tm.depth * scale
    pad = 30
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w+pad*2}" height="{h+pad*2+24}" '
        f'viewBox="0 0 {w+pad*2} {h+pad*2+24}">',
        f'<rect width="{w+pad*2}" height="{h+pad*2+24}" fill="#14161a"/>',
        f'<g transform="translate({pad},{pad})">',
    ]

    for z in range(tm.depth):
        x = 0
        while x < tm.width:
            s = tm.surface[z][x]
            run = 1
            while x + run < tm.width and tm.surface[z][x + run] == s:
                run += 1
            colour = _SURFACE_COLOUR.get(s)
            if colour:
                parts.append(
                    f'<rect x="{x*scale}" y="{z*scale}" width="{run*scale}" '
                    f'height="{scale}" fill="{colour}"/>'
                )
            x += run

    # Town wall on top of the surfaces.
    for z in range(tm.depth):
        x = 0
        while x < tm.width:
            if not tm.wall[z][x]:
                x += 1
                continue
            run = 1
            while x + run < tm.width and tm.wall[z][x + run]:
                run += 1
            parts.append(
                f'<rect x="{x*scale}" y="{z*scale}" width="{run*scale}" '
                f'height="{scale}" fill="#c9c3b6"/>'
            )
            x += run

    if overlay:
        comps = components(tm)
        for comp in comps[1:]:
            for x, z in comp:
                parts.append(
                    f'<rect x="{x*scale}" y="{z*scale}" width="{scale}" height="{scale}" '
                    f'fill="#ff5f5f" fill-opacity="0.55"/>'
                )
        for x, z in sorted(tm.gates):
            parts.append(
                f'<rect x="{x*scale}" y="{z*scale}" width="{scale}" height="{scale}" '
                f'fill="#ffd166"/>'
            )
        for x0, z0, x1, z1 in tm.bridges:
            parts.append(
                f'<line x1="{(x0+0.5)*scale}" y1="{(z0+0.5)*scale}" '
                f'x2="{(x1+0.5)*scale}" y2="{(z1+0.5)*scale}" stroke="#4fd1ff" '
                f'stroke-width="{max(2,scale)}"/>'
            )

    parts.append("</g>")
    parts.append(
        f'<text x="{pad}" y="{pad-12}" font-family="sans-serif" font-size="15" '
        f'fill="#e6e3dc">{tm.name} - rasterised {tm.width}x{tm.depth} tiles '
        f'({tm.width_feet:.0f}x{tm.depth_feet:.0f} ft)</text>'
    )
    if overlay:
        parts.append(
            f'<text x="{w+pad}" y="{pad-12}" font-family="sans-serif" font-size="10" '
            f'fill="#8b8f98" text-anchor="end">red = unreachable pockets, '
            f'yellow = gates, blue = added bridges</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def through_route_pinches(tm, minimum: float = 3.0) -> list[tuple[int, int, int]]:
    """Through-route tiles whose open cross-section is under ``minimum``.

    Streets are widened before buildings are painted, and buildings win on
    overlap -- so a footprint that laps over a widened street re-narrows it.
    Checking each tile against its class standard cannot see this: the tile is
    still *classed* main street, it just has a house on both sides of it. What
    matters to a cart is the open cross-section, measured across the direction
    of travel, so that is what this returns.
    """
    out: list[tuple[int, int, int]] = []
    for z in range(tm.depth):
        for x in range(tm.width):
            if not getattr(tm, "street_class", None) or tm.street_class[z][x] not in ("main", "cart"):
                continue
            runs = []
            for horiz in (True, False):
                n = 1
                for step in (1, -1):
                    i = 1
                    while True:
                        nx, nz = (x + step * i, z) if horiz else (x, z + step * i)
                        if not tm.inside(nx, nz) or tm.surface[nz][nx] not in OPEN:
                            break
                        n += 1; i += 1
                runs.append(n)
            # Travel runs along the longer axis; the cross-section is the other.
            cross = min(runs)
            if cross < minimum:
                out.append((x, z, cross))
    return out


def check_placements(builder, tm) -> list[str]:
    """Check the *emitted geometry*, not the tile grid.

    Everything else in this module reads the TileMap, which is the plan --
    not what was actually built. That blind spot is real: a style missing a
    civic door once laid solid wall across five doorways while this report
    still said 100% of buildings were enterable, because the tilemap still
    had a doorway recorded there. These checks look at the placements.
    """
    problems: list[str] = []
    placements = builder.placements

    off_grid = [
        p for p in placements
        if abs(p.x * 2 - round(p.x * 2)) > 0.01 or abs(p.z * 2 - round(p.z * 2)) > 0.01
    ]
    # Props are deliberately jittered off-lattice; tiles never may be.
    tiles_off = [p for p in off_grid if builder.palette.catalog.by_id(p.asset_id) is None
                 or builder.palette.catalog.by_id(p.asset_id).kind != "prop"]
    if tiles_off:
        problems.append(
            f"{len(tiles_off)} tile placements are off the half-tile grid "
            f"(first at x={tiles_off[0].x}, z={tiles_off[0].z}) -- minis with "
            "grid snap will not line up with the floors")

    planned = sum(len(v) for v in tm.doors.values())
    door_ids = {
        a.id for a in builder.palette.catalog.assets if "door" in a.name.lower()
    }
    built = sum(1 for p in placements if p.asset_id in door_ids)
    if built < planned:
        problems.append(
            f"{planned - built} of {planned} planned doorways were not built as "
            "doors -- a doorway that resolves to nothing becomes solid wall")

    problems.extend(_wall_solidity(builder, tm))
    problems.extend(_prop_collisions(builder))
    return problems


def tile_interpenetration(builder) -> list[str]:
    """Structural tiles that occupy the same space as each other.

    Two solids sharing a volume is not a paste-time failure the way an
    overlapping prop is -- TaleSpire keeps both -- it is a *visual* one: the
    buried geometry shows through as a seam, and the seam moves as the camera
    does. The case that prompted this was floors against walls. A storey was
    pitched at the wall's height, so the wall column was continuous and every
    upper floor slab drove a quarter of a cubic tile through the masonry
    around its whole perimeter.
    """
    from .build import placed_bounds

    catalog = builder.palette.catalog
    boxes: list[tuple[float, ...]] = []
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind == "prop":
            continue
        if asset.size_y < 0.4:
            continue          # ground plane and thin trim: laid flush by design
        x0, z0, x1, z1 = placed_bounds(asset, p)
        boxes.append((x0, z0, p.y, x1, z1, p.y + asset.size_y))

    at: dict[tuple[int, int], list[int]] = {}
    for i, bx in enumerate(boxes):
        for cx in range(int(bx[0]), int(bx[3]) + 1):
            for cz in range(int(bx[1]), int(bx[4]) + 1):
                at.setdefault((cx, cz), []).append(i)

    e = 1e-6
    clashes = 0
    for ids in at.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                p, q = boxes[ids[a]], boxes[ids[b]]
                ox = min(p[3], q[3]) - max(p[0], q[0])
                oz = min(p[4], q[4]) - max(p[1], q[1])
                oy = min(p[5], q[5]) - max(p[2], q[2])
                if ox > e and oz > e and oy > e and ox * oy * oz > 0.05:
                    clashes += 1
    if not clashes:
        return []
    return [
        f"{clashes} pairs of structural tiles occupy the same space -- buried "
        "geometry shows through as a seam that shifts with the camera"
    ]


def _prop_collisions(builder) -> list[str]:
    """Props whose colliders intersect another prop's.

    TaleSpire drops these on paste without saying so, which is the community
    "missing parts" bug and reads on the board as half-built scenery. Before
    the scatter took collisions into account, 1,000 of 2,137 props on the
    Forest Church map were inside another one.
    """
    from .build import placed_bounds

    catalog = builder.palette.catalog
    boxes: list[tuple[float, ...]] = []
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind != "prop":
            continue
        x0, z0, x1, z1 = placed_bounds(asset, p)
        boxes.append((x0, z0, p.y, x1, z1, p.y + asset.size_y))

    at: dict[tuple[int, int], list[int]] = {}
    for i, bx in enumerate(boxes):
        for cx in range(int(bx[0]), int(bx[3]) + 1):
            for cz in range(int(bx[1]), int(bx[4]) + 1):
                at.setdefault((cx, cz), []).append(i)

    e = 1e-6
    clashing: set[int] = set()
    for ids in at.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                p, q = boxes[ids[a]], boxes[ids[b]]
                if (p[0] < q[3] - e and q[0] < p[3] - e
                        and p[1] < q[4] - e and q[1] < p[4] - e
                        and p[2] < q[5] - e and q[2] < p[5] - e):
                    clashing.update((ids[a], ids[b]))
    if not clashing:
        return []
    return [
        f"{len(clashing)} of {len(boxes)} props overlap another prop "
        f"({100 * len(clashing) / len(boxes):.0f}%) -- TaleSpire drops these "
        "silently on paste, so they will be missing from the board"
    ]


class _Occupancy:
    """Where solid geometry actually sits, as boxes on a horizontal slice.

    Reconstructed from the placements and each asset's measured bounds, so it
    answers "is there anything at this point" for the thing that was emitted
    -- not for the thing the tile grid says was intended.
    """

    def __init__(self, builder, y: float):
        from .build import placed_bounds

        self._cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        catalog = builder.palette.catalog
        for p in builder.placements:
            asset = catalog.by_id(p.asset_id)
            if asset is None or asset.kind == "prop":
                continue           # scenery is not structure
            if not (p.y - 1e-6 <= y <= p.y + asset.size_y + 1e-6):
                continue
            box = placed_bounds(asset, p)
            for cx in range(int(box[0]) - 1, int(box[2]) + 2):
                for cz in range(int(box[1]) - 1, int(box[3]) + 2):
                    self._cells.setdefault((cx, cz), []).append(box)

    def solid_at(self, px: float, pz: float) -> bool:
        for x0, z0, x1, z1 in self._cells.get((int(px), int(pz)), ()):
            if x0 - 1e-6 <= px <= x1 + 1e-6 and z0 - 1e-6 <= pz <= z1 + 1e-6:
                return True
        return False


#: Where inside a cell to sample for solidity. Corners and centre, kept off
#: the cell boundary so a piece that merely abuts the cell does not count.
_SAMPLES = (0.17, 0.5, 0.83)


def _wall_solidity(builder, tm) -> list[str]:
    """Check the town wall is solid masonry, not a row of fins.

    The castle kit's wall pieces are 0.5 deep -- curtain wall, meant to stand
    on a cell boundary. Laying one per cell across a rampart several cells
    thick leaves a 0.5-tile slot between every pair of cells: 2.5 ft of
    daylight straight through, for the whole circuit. Nothing in the tile grid
    can show that, because in the grid every one of those cells is wall. Only
    the emitted boxes can, so this samples them.
    """
    gates = set(tm.gates)
    mass = [(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.wall[z][x] and (x, z) not in gates]
    if not mass:
        return []

    core = builder.palette.resolve("city_wall_core") or builder.palette.resolve("city_wall")
    if core is None:
        return []
    floor = builder.palette.resolve("floor")
    base = floor.size_y if floor is not None else 0.0
    # Mid-height of the second course: clear of the ground plane below and of
    # the battlements above, so anything missing here is a hole in the wall.
    occupancy = _Occupancy(builder, base + 1.5 * core.size_y)

    holed = [
        (x, z) for (x, z) in mass
        if not all(occupancy.solid_at(x + i, z + j)
                   for i in _SAMPLES for j in _SAMPLES)
    ]
    if not holed:
        return []
    return [
        f"{len(holed)} of {len(mass)} town-wall cells have gaps in the masonry "
        f"(first at x={holed[0][0]}, z={holed[0][1]}) -- a curtain-wall piece "
        "laid one per cell does not fill the cell"
    ]


def enclosed_voids(plan) -> list[str]:
    """Chunks dropped as open country that the map has built all the way round.

    An unpasted chunk is not grass, it is bare board, so dropping one the town
    encloses punches a rectangular hole into the middle of the map. Skipping
    is supposed to trim inward from the edge only; this is the check that says
    so, measured on the plan that will actually be written.

    The barrier is every grid cell a written chunk *covers*, not the one cell
    it is named for. Packing fuses a run of cells into one chunk that keeps
    the first cell's row and column, so a plan of five slabs over 64 cells
    used to present five barrier cells -- the flood walked straight through
    the other 59 and this check could never report a hole on a packed plan.
    """
    def cells(ch):
        return ch.covers or ((ch.row, ch.col),)

    kept = {cell for ch in plan.chunks for cell in cells(ch)}
    reachable: set[tuple[int, int]] = set()
    stack = [(r, c) for r in range(plan.rows) for c in (0, plan.cols - 1)]
    stack += [(r, c) for c in range(plan.cols) for r in (0, plan.rows - 1)]
    while stack:
        r, c = stack.pop()
        if (r, c) in reachable or (r, c) in kept:
            continue
        if not (0 <= r < plan.rows and 0 <= c < plan.cols):
            continue
        reachable.add((r, c))
        stack += [(r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)]

    holes = [ch for ch in plan.skipped
             if any(cell not in reachable for cell in cells(ch))]
    if not holes:
        return []
    where = ", ".join(f"r{ch.row:02d}c{ch.col:02d}" for ch in holes[:4])
    return [
        f"{len(holes)} skipped chunk(s) are enclosed by built map ({where}) -- "
        f"{sum(ch.count for ch in holes):,} assets missing from the middle of "
        "the board, which pastes as a rectangular hole in the ground"
    ]

def chunk_anchors(plan, byid) -> list[str]:
    """Chunks that would not paste at a shared origin.

    Every chunk carries a registration marker at the map's minimum corner so
    that pasting them all at one cursor cell assembles the map. That only works
    if the corner TaleSpire anchors on is the same for all of them -- and a
    slab has two candidate corners, the lowest stored coordinate and the lowest
    point its geometry reaches. They differ whenever a prop overhangs, because
    a prop stores its collider centre rather than a corner. This checks both,
    on the chunks that will actually be written.
    """
    from .build import volume_bounds

    if len(plan.chunks) < 2:
        return []
    # Tiling mode does not share an origin and is not supposed to: each chunk
    # is pasted at its own place on bare board, pinned by a marker at its own
    # region corner. `chunk_datum` is the check that fits that plan.
    if any(c.layer == "" for c in plan.chunks):
        return []

    stored: dict[tuple[float, float, float], list[str]] = {}
    volume: dict[tuple[float, float, float], list[str]] = {}
    for ch in plan.chunks:
        (sx, sy, sz), _ = ch.slab.bounds()
        (vx, vy, vz), _ = volume_bounds(ch.slab, byid)
        stored.setdefault((round(sx, 3), round(sy, 3), round(sz, 3)), []).append(ch.label)
        volume.setdefault((round(vx, 3), round(vy, 3), round(vz, 3)), []).append(ch.label)

    # The *far* corner matters as much as the near one. Chunks that share only
    # their minimum still present different boxes -- the landscape layer topped
    # out around y=7 and the structure layer around y=20 -- and pasted at one
    # cursor cell they landed at different heights, which put a layer of roofs
    # in the grass with trees growing through them.
    far: dict[tuple[float, float, float], list[str]] = {}
    for ch in plan.chunks:
        _, (fx, fy, fz) = volume_bounds(ch.slab, byid)
        far.setdefault((round(fx, 2), round(fy, 2), round(fz, 2)), []).append(ch.label)

    out = []
    for what, corners in (("stored coordinate", stored), ("occupied volume", volume),
                          ("far corner", far)):
        if len(corners) == 1:
            continue
        odd = sorted(corners.items(), key=lambda kv: len(kv[1]))
        where = "; ".join(
            f"{', '.join(labels[:3])} at {corner}" for corner, labels in odd[:3]
        )
        out.append(
            f"chunks disagree on their {what} origin ({len(corners)} different "
            f"corners: {where}) -- pasted at one cursor cell they would land "
            "offset from each other"
        )
    return out

def chunk_datum(plan, byid) -> list[str]:
    """Tiled chunks that would not come out level with each other.

    A chunk pasted onto bare board comes to rest with the lowest point of its
    geometry on the board, so two chunks whose terrain sits at different
    heights *above their own lowest point* land at different heights -- a step
    in open grass along the join, which is the one thing a reviewer notices.
    Each chunk is pinned by a marker at the map's global floor for exactly
    this reason; this is the check that the pin did its job.

    Only the vertical datum is checked. Sideways, the pieces are lined up by
    eye against the grid before the press that commits them -- and they cannot
    be pinned exactly anyway, because a pine on a map edge overhangs the map
    itself, so the outermost chunks' geometry genuinely starts before their
    own region. Height is the one that cannot be judged from a preview and
    the one that shows, as a step in open grass along the join.
    """
    from .build import volume_bounds

    tiled = [c for c in plan.chunks if c.layer == ""]
    if len(tiled) < 2:
        return []

    bad = []
    for c in tiled:
        (_, ly, _), _ = volume_bounds(c.slab, byid)
        if abs(ly) > 1e-6:
            bad.append(f"{c.label} floors at y={ly:g}")
    if not bad:
        return []
    return [
        f"{len(bad)} tiled chunk(s) do not reach the shared floor at y=0 "
        f"({'; '.join(bad[:4])}) -- each comes to rest on its own lowest "
        "point, so they would step against each other along the joins"
    ]


def shells_rest_on_their_floors(builder, tm) -> list[str]:
    """Every building's walls start exactly on the top of its own floor.

    ``floating_placements`` asks the weakest question -- is there *anything*
    under this -- because most of the map is fine and a strict test would
    argue with it. This asks the strict one, and only of buildings, because a
    building is the thing that gets pasted on its own now: its shell and its
    floor are in different layers, so "the walls sit on the floor" is a claim
    about two slabs agreeing, and that is exactly what a per-building paste is
    checking on the board. Cheaper to catch here than to find by eye.

    A wall that starts below its floor is buried; one that starts above it is
    a building standing on air with a strip of floor showing underneath.
    """
    from .build import placed_bounds

    byid = builder.byid
    groups = getattr(builder, "group_of", None)
    if not groups:
        return []

    # Floor top per cell, from the landscape layer.
    floor_top: dict[tuple[int, int], float] = {}
    for p, layer in zip(builder.placements, builder.layer_of):
        asset = byid.get(p.asset_id)
        if asset is None or layer != "landscape" or p.asset_id in builder.prop_ids:
            continue
        x0, z0, x1, z1 = placed_bounds(asset, p)
        for cx in range(int(math.floor(x0 + 1e-6)), int(math.ceil(x1 - 1e-6))):
            for cz in range(int(math.floor(z0 + 1e-6)), int(math.ceil(z1 - 1e-6))):
                top = p.y + asset.size_y
                if top > floor_top.get((cx, cz), -1e9):
                    floor_top[(cx, cz)] = top

    # Lowest wall course per building, and the cell it stands in.
    lowest: dict[str, tuple[float, tuple[int, int]]] = {}
    for p, layer, group in zip(builder.placements, builder.layer_of, groups):
        asset = byid.get(p.asset_id)
        if (asset is None or not group or layer != "structure"
                or p.asset_id in builder.prop_ids or asset.size_y < 1.0):
            continue                      # floors and roof plates are not walls
        x0, z0, x1, z1 = placed_bounds(asset, p)
        cell = (int(math.floor((x0 + x1) / 2)), int(math.floor((z0 + z1) / 2)))
        if group not in lowest or p.y < lowest[group][0]:
            lowest[group] = (p.y, cell)

    off = []
    for group, (y, cell) in sorted(lowest.items()):
        want = floor_top.get(cell)
        if want is None or abs(y - want) > 1e-6:
            off.append((group, y, want))
    if not off:
        return []
    where = ", ".join(
        f"{g} at y={y:g} over floor top {'none' if w is None else format(w, 'g')}"
        for g, y, w in off[:4]
    )
    return [
        f"{len(off)} building(s) do not stand on their own floor ({where}) -- "
        "the shell and the floor are in different slabs, so this is two "
        "pastes disagreeing about height"
    ]


def floating_placements(builder, tm) -> list[str]:
    """Anything standing over a cell that has no ground in it at all.

    Panning the map's own boundary turned up a shop sign hanging in mid-air off
    the north edge and a pier running out over the void. Both are the same
    fault: a pass placed something on a cell whose ground the edge fringe had
    taken away, or beyond where ground was ever laid. Neither shows up in the
    tile grid -- the grid still says "pier" -- so this reads the emitted
    geometry and asks the only question that matters: is there anything
    underneath it.

    The test is deliberately the weakest one that catches it: not "is it
    resting on something" but "is there ground in this cell at any height".
    Buildings stand on their own floors and walls sit on wall blocks, so a
    stricter test would spend its time arguing with the parts of the map that
    are fine.
    """
    from .build import placed_bounds

    byid = builder.byid

    def covered(p):
        asset = byid.get(p.asset_id)
        if asset is None:
            return []
        x0, z0, x1, z1 = placed_bounds(asset, p)
        return [(cx, cz)
                for cx in range(int(math.floor(x0 + 1e-6)),
                                int(math.ceil(x1 - 1e-6)))
                for cz in range(int(math.floor(z0 + 1e-6)),
                                int(math.ceil(z1 - 1e-6)))]

    def is_ground(asset):
        return (asset.size_y <= 0.75
                and asset.size_x >= 0.9 and asset.size_z >= 0.9)

    ground: set[tuple[int, int]] = set()
    for p in builder.placements:
        asset = byid.get(p.asset_id)
        if asset is None or p.asset_id in builder.prop_ids or not is_ground(asset):
            continue
        ground.update(covered(p))

    floating = []
    for p in builder.placements:
        asset = byid.get(p.asset_id)
        if asset is None or is_ground(asset):
            continue
        cells = covered(p)
        if cells and not any(c in ground for c in cells):
            floating.append((p, asset))

    if not floating:
        return []
    where = ", ".join(
        f"{a.name} at ({p.x:.1f}, {p.y:.1f}, {p.z:.1f})" for p, a in floating[:3]
    )
    return [
        f"{len(floating)} placement(s) stand over nothing ({where}) -- "
        "left hanging where the edge fringe took the ground away, or beyond "
        "where ground was ever laid"
    ]
