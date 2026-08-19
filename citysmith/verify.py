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
    return problems


class _Occupancy:
    """Where solid geometry actually sits, as boxes on a horizontal slice.

    Reconstructed from the placements and each asset's measured bounds, so it
    answers "is there anything at this point" for the thing that was emitted
    -- not for the thing the tile grid says was intended.
    """

    def __init__(self, builder, y: float):
        from .build import rotated_footprint

        self._cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        catalog = builder.palette.catalog
        for p in builder.placements:
            asset = catalog.by_id(p.asset_id)
            if asset is None or asset.kind == "prop":
                continue           # scenery is not structure
            if not (p.y - 1e-6 <= y <= p.y + asset.size_y + 1e-6):
                continue
            sx, sz = rotated_footprint(asset, p.rot)
            box = (p.x, p.z, p.x + sx, p.z + sz)
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
    """
    kept = {(ch.row, ch.col) for ch in plan.chunks}
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

    holes = [ch for ch in plan.skipped if (ch.row, ch.col) not in reachable]
    if not holes:
        return []
    where = ", ".join(f"r{ch.row:02d}c{ch.col:02d}" for ch in holes[:4])
    return [
        f"{len(holes)} skipped chunk(s) are enclosed by built map ({where}) -- "
        f"{sum(ch.count for ch in holes):,} assets missing from the middle of "
        "the board, which pastes as a rectangular hole in the ground"
    ]

