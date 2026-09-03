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

import collections
import math

from dataclasses import dataclass, field

from .layout import TILE_FEET
from .raster import (
    CART_ROAD,
    CART_TILES,
    FLOOR,
    GROUND,
    LANE,
    LANE_ROAD,
    LANE_TILES,
    MAIN_ROAD,
    MARSH,
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
    # **A church complex is ONE building to this check.** A chancel has no
    # street door on purpose -- you enter a church through its nave, and
    # `_find_perimeters` drops the wall between them so the chancel is inside
    # the nave's shell. Counting it separately reported a building that could
    # not be entered, which is exactly the "verify counts four enterable
    # buildings where there is one church" failure the multi-volume design was
    # warned about. Its nave carries the access for both.
    subordinate = {b for b, (_n, r) in tm.church_parts.items() if r != "nave"}
    total_buildings = len({b for row in tm.building for b in row
                           if b and b not in subordinate})
    with_doors = len({b for b in tm.doors if b not in subordinate})
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
    MARSH: "#3d4f3a",
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

    problems.extend(_ground_sheet(builder, tm))
    problems.extend(_wall_solidity(builder, tm))
    problems.extend(_prop_collisions(builder))
    problems.extend(_boundaries_stay_on_the_map(builder, tm))
    problems.extend(_boundaries_do_not_block_a_way(builder, tm))
    problems.extend(market_square_open(builder, tm))
    return problems


def market_square_open(builder, tm) -> list[str]:
    """The market must leave the square one walkable room, off the cart route.

    Measured on the *emitted boxes*, not on the dressing pass's intentions,
    per this project's metrics rule: the pass can believe its keep-clear set
    and still be wrong -- a stall footprint wider than its pitch, or a goods
    cluster jittered across a cell line, blocks a cell no plan recorded.
    A prop's box blocks a cell when it covers the cell's centre and stands
    taller than :data:`_MARKET_BLOCKS_ABOVE`.

    Two claims, per plaza region: the unblocked cells stay one connected
    room (a stall row must never wall off a corner of the square), and no
    cell of the through route crossing the square (`street_class` main/cart)
    is blocked at all -- that is the lane the carts have. A prop shorter
    than `build.MARKET_BLOCKS_ABOVE` blocks nothing: a basket is stepped
    over, a stall or a crate is stood behind.
    """
    from .build import MARKET_BLOCKS_ABOVE, placed_bounds

    plaza = {(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.surface[z][x] == PLAZA}
    if not plaza:
        return []

    catalog = builder.palette.catalog
    blocked: set[tuple[int, int]] = set()
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind != "prop":
            continue
        if asset.size_y < MARKET_BLOCKS_ABOVE:
            continue
        x0, z0, x1, z1 = placed_bounds(asset, p)
        for cx in range(int(math.floor(x0)), int(math.ceil(x1))):
            for cz in range(int(math.floor(z0)), int(math.ceil(z1))):
                if (cx, cz) in plaza and x0 <= cx + 0.5 <= x1 and z0 <= cz + 0.5 <= z1:
                    blocked.add((cx, cz))

    problems: list[str] = []

    routed = sorted(c for c in blocked
                    if tm.street_class[c[1]][c[0]] in ("main", "cart"))
    if routed:
        problems.append(
            f"{len(routed)} market placements stand on the through route "
            f"crossing the square (first at x={routed[0][0]}, z={routed[0][1]}) "
            "-- that is the lane the carts have")

    # Each plaza region separately: an FTG town can author several squares.
    left = set(plaza)
    while left:
        start = min(left)
        comp = {start}
        queue = [start]
        while queue:
            x, z = queue.pop()
            for _, dx, dz in SIDES:
                n = (x + dx, z + dz)
                if n in left and n not in comp:
                    comp.add(n)
                    queue.append(n)
        left -= comp

        open_cells = comp - blocked
        if not open_cells:
            problems.append(
                f"the {len(comp)}-cell square at {min(comp)} is completely "
                "covered by market dressing -- there is no room to stand")
            continue
        seen = {min(open_cells)}
        queue = [min(open_cells)]
        while queue:
            x, z = queue.pop()
            for _, dx, dz in SIDES:
                n = (x + dx, z + dz)
                if n in open_cells and n not in seen:
                    seen.add(n)
                    queue.append(n)
        if seen != open_cells:
            cut = min(open_cells - seen)
            problems.append(
                f"market dressing walls off {len(open_cells) - len(seen)} of "
                f"{len(open_cells)} open cells in the square at {min(comp)} "
                f"(first stranded cell x={cut[0]}, z={cut[1]}) -- the square "
                "must stay one walkable room")
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
    from .build import is_chimney, placed_bounds

    catalog = builder.palette.catalog
    boxes: list[tuple[float, ...]] = []
    flue: list[bool] = []
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind == "prop":
            continue
        if asset.size_y < 0.4:
            continue          # ground plane and thin trim: laid flush by design
        x0, z0, x1, z1 = placed_bounds(asset, p)
        boxes.append((x0, z0, p.y, x1, z1, p.y + asset.size_y))
        flue.append(is_chimney(asset))

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
                    # **Every overlap a chimney is in is deliberate**, and
                    # both of its two kinds are measured rather than assumed.
                    # A flue laps its own courses by `build.CHIMNEY_LAP`, each
                    # buried to its middle in the one below, so the shaft
                    # reads as continuous masonry where stacked courses show a
                    # joint at every lift; and its foot is sunk into the roof
                    # it comes through by `build.CHIMNEY_BASE`, because a
                    # chimney emerges from a roof rather than standing on one.
                    # Both come off the chimney the user laid by hand, and
                    # `test_the_flue_is_the_one_the_user_built` holds the
                    # shipped code against that slab.
                    #
                    # Counted on Forest Church: 764 chimney-against-roof pairs
                    # and 416 chimney-against-chimney, 1,180 of 1,737 -- 68%
                    # of the whole metric, from geometry that is correct. A
                    # number inflated by deliberate overlap is a number that
                    # hides a real one, which is the argument this project
                    # already makes about two corner panels meeting at right
                    # angles. What a volume test cannot do here is tell a
                    # measured burial from an accidental one; the chimney's
                    # relationship with its roof is checked by tests that
                    # know what it should be --
                    # `test_every_chimney_sits_on_its_ridge_the_same_way` for
                    # both ends of the flue and
                    # `test_a_chimney_never_replaces_the_roof_surface` for the
                    # roof still being under it.
                    if flue[ids[a]] or flue[ids[b]]:
                        continue
                    clashes += 1
    if not clashes:
        return []
    return [
        f"{clashes} pairs of structural tiles occupy the same space -- buried "
        "geometry shows through as a seam that shifts with the camera"
    ]


#: Below this a tile is trim or dressing rather than the ground somebody walks
#: on. Cobble is 0.25 and grass is 0.5, so the sheet itself is never thinner.
_GROUND_MIN_THICK = 0.2

#: Two adjacent ground tops closer than this are flush. A quarter tile is 15
#: inches, which is the kerb the top-align rule exists to stop; a hundredth of
#: a tile is under an inch and is float noise.
_FLUSH_TOLERANCE = 0.02


def _ground_sheet(builder, tm) -> list[str]:
    """The outdoor ground: one tile per cell, and all of it flush.

    Two checks over one pass, both designed in `docs/district-surfaces.md` 6
    and neither built until now -- which is the failure `tasks.json` exists to
    stop, so they are worth stating plainly.

    **One tile per cell.** This is the surface probe's own bug promoted to a
    check: it laid grass over the whole board and then set each material pad on
    top at the same top height, and TaleSpire does not drop a co-located tile
    the way it drops a colliding prop -- the two simply z-fight, dithering as
    the camera moves. With six materials now keyed on overlapping conditions
    (surface class, road class, quarter, yard) it is easy to lay two by
    accident and never see it in the file.

    **Flush.** Surface tiles align at the *top*, not the bottom, because cobble
    is 0.25 thick and grass is 0.5: laid from a common bottom, every street sat
    a quarter tile under the grass beside it -- a 15 inch kerb along both sides
    of every road, on 1,234 tiles. That rule has never been checked, and it now
    carries nine materials rather than two.
    """
    catalog = builder.palette.catalog

    # **A watercourse is neither of these things and both checks trip on it.**
    # A channel is a bed with a translucent column standing on it, so the cell
    # legitimately holds two tiles; and water sits *below* grade on purpose, so
    # it is legitimately not flush with the bank -- that is what makes it read
    # as a channel a creature can be pulled into rather than a blue floor.
    # First run flagged 941 cells and 125 steps, every one of them the river.
    wet = {
        a.id for role in ("water", "water_2x2", "riverbed", "riverbed_2x2")
        for a in (builder.palette.resolve(role),) if a is not None
    }

    tops: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind != "tile" or p.asset_id in wet:
            continue
        if asset.size_y < _GROUND_MIN_THICK or asset.size_y > 0.6:
            continue                      # trim below, walls and blocks above
        if (asset.size_x, asset.size_z) != (1.0, 1.0):
            continue                      # the 2x2 pass has its own lattice
        top = p.y + asset.size_y
        # The ground sheet only. Anything standing on a storey is a floor, and
        # a floor is allowed to sit above the ground it shares a cell with.
        if top > 1.0:
            continue
        tops.setdefault((int(p.x), int(p.z)), []).append((top, asset.name))

    problems: list[str] = []

    doubled = sorted(c for c, v in tops.items() if len(v) > 1)
    if doubled:
        x, z = doubled[0]
        names = " + ".join(sorted(n for _, n in tops[(x, z)])[:2])
        problems.append(
            f"{len(doubled)} cell(s) hold more than one ground tile (first at "
            f"x={x}, z={z}: {names}) -- co-located tiles are not dropped, they "
            "z-fight, and the dithering moves with the camera")

    # **The border taper steps down on purpose**, so two cells either side of
    # a falloff ring legitimately differ by exactly one step -- and the check
    # reported those two pairs on every build until it was told. A check that
    # always says 2 is a check people stop reading.
    from .build import edge_taper
    taper = edge_taper(tm)

    rough = 0
    first: tuple[int, int, float, float] | None = None
    for (x, z), here in sorted(tops.items()):
        for dx, dz in ((1, 0), (0, 1)):
            there = tops.get((x + dx, z + dz))
            if not there:
                continue
            if taper.get((x, z), 0.0) != taper.get((x + dx, z + dz), 0.0):
                continue
            a, b = max(t for t, _ in here), max(t for t, _ in there)
            if abs(a - b) > _FLUSH_TOLERANCE:
                rough += 1
                if first is None:
                    first = (x, z, a, b)
    if first is not None:
        x, z, a, b = first
        problems.append(
            f"{rough} pair(s) of adjacent ground tiles differ in top height "
            f"(first at x={x}, z={z}: {a:.2f} against {b:.2f}) -- a step of "
            f"{abs(a - b) * TILE_FEET:.1f} ft where a creature walks")
    return problems


def _obb(asset, placement) -> tuple[float, ...]:
    """A placement as an oriented box, in the same form `Scatter` uses.

    The collider centre is where the stored coordinate plus
    :func:`build.collider_offset` puts it, which is right at any rotation.
    The box itself comes from `build.oriented_box` rather than a second copy
    here: this check and the scatter that is supposed to prevent what it finds
    have to measure the same thing, or one of them is always wrong.
    """
    from .build import collider_offset, oriented_box

    ox, oz = collider_offset(asset, placement.rot)
    return oriented_box(asset, placement.x + ox, placement.z + oz, placement.rot)


def _boundary_ids(builder) -> set[str]:
    """Every asset a boundary pass can lay: field walls, yard boundaries, posts.

    Derived from the tables rather than listed, for the reason
    `_fences_built` records at length -- a hardcoded list reported
    `--fence-style paling` as unbuilt over 782 standing panels. `YARD_BOUNDARY`
    is here too now that the yard boundary is dealt per tier and can be any of
    four pieces.
    """
    from .build import FENCE_STYLES, YARD_BOUNDARY

    roles = {r for spec in FENCE_STYLES.values()
             for r in (spec.panel, spec.post) if r}
    roles |= set(YARD_BOUNDARY.values())
    roles |= {"yard_fence", "field_wall", "field_wall_post", "field_wall_tall",
              "field_hedge"}
    return {a.id for r in roles
            for a in (builder.palette.resolve(r),) if a is not None}


def _boundary_boxes(builder):
    """``(placement, asset, cx, cz)`` for every boundary piece on the board.

    **The centre is handed out here because a stored coordinate is not one.**
    A placement holds the asset's *origin*, and where that sits inside the
    collider depends on how the piece was authored: a prop is centred on its
    origin, a tile stands with its collider's min corner there
    (:func:`build.place_centered` is the long version). Both boundary checks
    below want a centre, and both used to read ``p.x`` as one -- right for the
    props, and half a footprint out for every tile.

    On Sedgewater that was the whole of a standing ``[FAIL]``. The palisade
    pieces are the only boundary assets in the medieval palette that are
    ``kind="tile"`` (``off=(0.5, 0.5)``), and the enclosure ring is built from
    them whatever ``--fence-style`` asks for -- so a box measured half a tile
    low on both axes straddled the four cells meeting at the tile's own min
    corner instead of the one cell it fills, and caught a street two of them
    away. Nine styles, the same two pieces, the same coordinate.

    Same rule as :func:`build.placed_bounds`, which every other placement check
    in this module already goes through.
    """
    from .build import collider_offset

    catalog = builder.palette.catalog
    ids = _boundary_ids(builder)
    for p in builder.placements:
        if p.asset_id not in ids:
            continue
        asset = catalog.by_id(p.asset_id)
        if asset is not None:
            ox, oz = collider_offset(asset, p.rot)
            yield p, asset, p.x + ox, p.z + oz


def _boundaries_stay_on_the_map(builder, tm) -> list[str]:
    """No boundary piece may lie outside the map rectangle.

    `docs/fencing.md` §2.4: a fence segment reaches 258 tiles at its longest
    and a quarter of every line lies outside the crop window, so a run laid
    without clipping puts props up to 188 tiles off the map. Every other
    consumer clips by construction -- `_fill_polygon` writes into a bounded
    grid and discards the rest -- and a prop run gets no such protection.

    This **fails** rather than warns, and not for tidiness: the build's
    bounding box is what every registration marker, chunk anchor and
    `anchor_on_a_whole_tile` check is measured against, so one prop off the map
    moves all of them.
    """
    from .build import placed_bounds

    out = []
    for p, asset, cx, cz in _boundary_boxes(builder):
        x0, z0, x1, z1 = placed_bounds(asset, p)
        if (x0 < -0.5 or x1 > tm.width + 0.5
                or z0 < -0.5 or z1 > tm.depth + 0.5):
            out.append((cx, cz))
    if not out:
        return []
    fx, fz = out[0]
    return [f"{len(out)} boundary piece(s) lie outside the {tm.width}x{tm.depth} "
            f"map (first at x={fx:.2f}, z={fz:.2f}) -- an off-map prop drags the "
            "bounding box every registration check is measured against"]


def _boundaries_do_not_block_a_way(builder, tm) -> list[str]:
    """No boundary piece may stand in a street, lane, plaza or pier.

    The playability check of the three, and the one that matters: a drystone
    wall laid across a road is an impassable line through the one thing the map
    exists to let people walk down. Both boundary passes avoid it by
    construction -- `_lay_fences` opens the run where the paving is,
    `_lay_yards` refuses a panel whose overhang would land in one -- so this is
    the artifact-side proof that they did.

    **It measures the piece where the piece actually is**, which is what
    :func:`_boundary_boxes` hands it and what it did not do for the first nine
    fence styles it was run against; the correction is written up there.
    """
    from .build import blocks_a_way

    ways = frozenset({STREET, PLAZA, LANE, PIER})
    out = [(cx, cz) for p, asset, cx, cz in _boundary_boxes(builder)
           if blocks_a_way(tm, asset, cx, cz, p.rot, ways)]
    if not out:
        return []
    fx, fz = out[0]
    return [f"{len(out)} boundary piece(s) stand in a street or lane "
            f"(first at x={fx:.2f}, z={fz:.2f}) -- a wall across a "
            "way is an obstacle on the one thing the map is for"]


def _prop_collisions(builder) -> list[str]:
    """Props whose colliders intersect another prop's.

    TaleSpire drops these on paste without saying so, which is the community
    "missing parts" bug and reads on the board as half-built scenery. Before
    the scatter took collisions into account, 1,000 of 2,137 props on the
    Forest Church map were inside another one.

    **This tests the ORIENTED box, and that is a correction rather than a
    refinement.** It used to test the axis-aligned one, and on a fenced map
    that is not an approximation, it is wrong: two 2-tile fence panels butted
    end to end on an off-axis bearing overlap as boxes by +0.29 on both axes at
    45 degrees while their meshes are disjoint, and 97-100% of surveyed fence
    lines are off-axis. Every fenced build therefore printed ``[FAIL]`` --
    5,672 pairs of them on East Tradebourne -- for scenery that was standing on
    the board perfectly well.

    That was defensible only while the question was open. It is not: the
    2026-08-25 build settled that **TaleSpire's own drop test is on the
    oriented collider** (`docs/fencing.md` 4.1) -- 78 flagged pairs, and the
    walls came out continuous. So the check now measures what the game
    measures, for every prop rather than by exempting fences, and a real
    overlap between two fence panels still fails.
    """
    from .build import oriented_aabb, oriented_depth

    catalog = builder.palette.catalog
    boundary_ids = _boundary_ids(builder)

    boxes: list[tuple[float, ...]] = []
    spans: list[tuple[float, float]] = []
    joinable: list[float] = []
    rots: list[int] = []
    for p in builder.placements:
        asset = catalog.by_id(p.asset_id)
        if asset is None or asset.kind != "prop":
            continue
        boxes.append(_obb(asset, p))
        spans.append((p.y, p.y + asset.size_y))
        # How deep two of these may meet and still be a *join* rather than a
        # burial. Zero for anything that is not a boundary panel.
        joinable.append(min(asset.size_x, asset.size_z)
                        if p.asset_id in boundary_ids else 0.0)
        rots.append(p.rot)

    at: dict[tuple[int, int], list[int]] = {}
    for i, box in enumerate(boxes):
        x0, z0, x1, z1 = oriented_aabb(box)
        for cx in range(math.floor(x0), math.floor(x1) + 1):
            for cz in range(math.floor(z0), math.floor(z1) + 1):
                at.setdefault((cx, cz), []).append(i)

    e = 1e-6
    clashing: set[int] = set()
    joins = 0
    tested: set[tuple[int, int]] = set()
    for ids in at.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = (ids[a], ids[b]) if ids[a] < ids[b] else (ids[b], ids[a])
                if (i, j) in tested:
                    continue
                tested.add((i, j))
                # Height first: one comparison, and it rejects a prop standing
                # on a shelf above another without any trigonometry.
                if not (spans[i][0] < spans[j][1] - e
                        and spans[j][0] < spans[i][1] - e):
                    continue
                depth = oriented_depth(boxes[i], boxes[j])
                if depth <= e:
                    continue
                # **A boundary turns corners, and a corner is an overlap by
                # design.** Measured on Pelvesthollow: 577 of the flagged pairs
                # were `Wooden Fence` against `Wooden Fence` at a penetration of
                # exactly 0.180, which is that panel's own thickness -- one
                # panel running east meeting the next running north.
                #
                # **A corner is not the only thing that penetrates by a
                # thickness, and that is what this allowance used to miss.**
                # Two panels lying along the SAME line, lapped by half their
                # length, separate on their thin axis first, so the minimum
                # penetration is also exactly the thickness -- and the
                # allowance waved through every one. That is how the yard
                # boundary came to be laid twice over on every board this
                # project has built, invisibly to the checks:
                # `docs/fencing.md` §10.1. The 577 pairs above were not all
                # corners; 507 of them were laps.
                #
                # So a join has to be a TURN. Rotation is a step index and 12
                # steps is a half turn, so two pieces are parallel exactly when
                # their steps agree modulo 12 -- whatever bearing the run is
                # on, which matters because a field wall follows a surveyed
                # line and uses all 24.
                limit = min(joinable[i], joinable[j])
                if (limit > 0.0 and depth <= limit + e
                        and (rots[i] - rots[j]) % 12 != 0):
                    joins += 1
                    continue
                clashing.update((i, j))

    if not clashing:
        return []
    out = [
        f"{len(clashing)} of {len(boxes)} props overlap another prop "
        f"({100 * len(clashing) / len(boxes):.0f}%) -- TaleSpire drops these "
        "silently on paste, so they will be missing from the board"
    ]
    if joins:
        out.append(f"    ({joins} boundary corner join(s) not counted)")
    return out


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

def anchor_on_a_whole_tile(plan, byid) -> list[str]:
    """Chunks whose shared box is anchored on a cell boundary.

    A paste comes to rest with the slab's bounding box *centred* on the
    cursor's ray hit, and the result is snapped to the global grid. If the box
    has an odd extent its centre falls exactly between two cells, and the snap
    has a tie to break -- which it does not always break the same way. Measured
    on the board: with a 189-wide box (centre x=94.5) two copy-outs put one
    chunk's props at one offset and its neighbour's a tile further east, a
    one-tile step down the whole join. An even extent has nothing to round.
    """
    from .build import volume_bounds

    if len(plan.chunks) < 2:
        return []
    bad = []
    for c in plan.chunks:
        (lx, _, lz), (hx, _, hz) = volume_bounds(c.slab, byid)
        cx, cz = (lx + hx) / 2.0, (lz + hz) / 2.0
        for axis, v in (("x", cx), ("z", cz)):
            if abs(v - round(v)) > 1e-6:
                bad.append(f"{c.label} centres on {axis}={v:g}")
    if not bad:
        return []
    return [
        "registration box is anchored between cells, so the paste has a tie to "
        "break and neighbouring chunks can land a tile apart: "
        + "; ".join(sorted(set(bad))[:4])
    ]


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
            lowest[group] = (p.y, cell, asset)

    off = []
    for group, (y, cell, asset) in sorted(lowest.items()):
        want = floor_top.get(cell)
        if want is None:
            off.append((group, y, want))
            continue
        # **A wall taller than its course is SUNK, and that is correct.**
        # `Tavern Wall 01` is 2.03 tall where every other panel in the
        # medieval set is 2.00, so seating its head on the course line puts
        # its base 0.03 low -- 27 buildings on Forest Church, 614 on East
        # Tradebourne, every one of them that piece. The head is the end that
        # has to be right, because the roof seats at `floors * storey_h`; the
        # excess is absorbed at the base where the floor hides it rather than
        # left standing proud of the eaves, which is the end that shows.
        #
        # So the allowance is the panel's own excess over a whole course and
        # nothing more, and it is one-sided: a shell may be buried by it and
        # may NEVER float. This check exists to catch two pastes disagreeing
        # about height, which is a whole course, and it was firing on 1.8
        # inches.
        excess = asset.size_y - round(asset.size_y) if asset is not None else 0.0
        sunk = want - y
        if sunk < -1e-6 or sunk > max(0.0, excess) + 1e-6:
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


#: Roles a feature is built from, for :func:`feature_report`. A feature is
#: "present" when at least one placement uses one of its roles.
FEATURE_ROLES = {
    # Superseded by `_fence_roles()`, which derives the same list from
    # `build.FENCE_STYLES`. Kept as the fallback for a caller with no build
    # module to hand, and as the record of what the hardcoded version missed.
    "field walls": ("field_wall", "field_wall_post", "field_wall_tall",
                    "field_hedge"),
    "yards": ("yard_fence", "yard_gravel"),
    "plazas": ("plaza",),
    "cart streets": ("street_cart",),
    "lanes": ("lane_earth",),
    # `marsh` is deliberately NOT in this tuple. It resolves to the same
    # `Swamp floor 1x1` as `lane_earth`, so a board with one trodden lane and
    # no fen at all would report the wetland as built. The 2x2 puddled tiles
    # and the reeds are unique to a marsh, so they are what proves one.
    "marsh": ("marsh_2x2", "marsh_reed", "marsh_lily"),
}


def _fences_built(builder) -> bool:
    """Did the boundary pass actually lay anything?

    **Ask the pass, do not infer from asset ids.** Two attempts failed first,
    and both are worth keeping written down:

    1. `FEATURE_ROLES["field walls"]` listed the drystone and hedge roles by
       hand, so `--fence-style paling` reported "nothing was built from them"
       while 782 `Wooden Fence` panels stood on the board -- the barricade
       that map was made for. A false FAIL inside the check whose entire
       purpose is catching absent features.
    2. Deriving the role list from `FENCE_STYLES` fixed that and broke the
       opposite case: paling builds from `yard_fence`, and so does
       `_lay_yards`. A town with garden fences and no field walls would have
       reported its boundaries as built.

    An asset id cannot distinguish the pass that placed it -- the same lesson
    as `layer_of`, and as `marsh` sharing `Swamp floor 1x1` with `lane_earth`.
    `Builder.fence_pieces` is the count `_lay_fences` returned. The role
    fallback stays for a builder that predates it.
    """
    count = getattr(builder, "fence_pieces", None)
    if count is not None:
        return count > 0
    ids = set()
    for role in FEATURE_ROLES["field walls"]:
        asset = builder.palette.resolve(role)
        if asset is not None:
            ids.add(asset.id)
    return bool(ids & {p.asset_id for p in builder.placements})


def _yards_built(builder) -> bool:
    """Did the yard pass lay a boundary? See the note at the call site."""
    count = getattr(builder, "yard_pieces", None)
    if count is not None:
        return count > 0
    ids = {a.id for r in FEATURE_ROLES["yards"]
           for a in (builder.palette.resolve(r),) if a is not None}
    return bool(ids & {p.asset_id for p in builder.placements})


def _yard_forms(tm, yards) -> str:
    """The mix of yard shapes, so a report line says what a town looks like.

    A count of cells cannot tell a town of back yards from a town of full ones,
    and the whole point of sizing a yard from its site is that they differ.
    """
    from collections import Counter
    from .build import yard_form, yard_reach_by_side

    forms = Counter()
    for bid in yards:
        doors = tm.doors.get(bid) or []
        forms[yard_form(yard_reach_by_side(tm, bid),
                        doors[0][2] if doors else None)] += 1
    return ", ".join(f"{k} {v}" for k, v in forms.most_common())


def _gables_built(builder, tm, seed: int) -> tuple[int, int]:
    """``(wings that should be gabled, those with no hip corner in them)``.

    **Read off the artifact, not off the deal.** A hipped wing lays a roof
    *corner* piece at each of its ends and a gabled one lays none, so the
    question "did this build actually gable" is answered by looking for corners
    inside the wings the deal picked out. Counting the deal instead would
    report the feature as built because we asked for it, which is the
    plan-versus-artifact trap this module exists to close -- and which it has
    already closed seven times for other checks.
    """
    from .build import (SIDE_OFFSETS, _wing_gable, cell_of, footprints,
                        roof_set, roof_wings)
    from .quarters import quarter_map

    quarters = quarter_map(tm)
    if not quarters:
        return (0, 0)

    corner_ids = set()
    for tier in ("civic", "trade", "common", "utility"):
        pieces = roof_set(builder.palette, tier)
        for piece in (pieces[1], pieces[2]):
            if piece is not None:
                corner_ids.add(piece.id)
    lookup = builder.palette.catalog.by_id
    corner_cells = set()
    for p in builder.placements:
        if p.asset_id in corner_ids:
            a = lookup(p.asset_id)
            if a is not None:
                corner_cells.add(cell_of(p, a))

    want = clean = 0
    for bid, cells in footprints(tm).items():
        for wing in roof_wings(cells):
            if _wing_gable(wing, quarters, seed) == "hip":
                continue
            want += 1
            if not (wing & corner_cells):
                clean += 1
    return (want, clean)


def feature_report(builder, tm, layout=None, seed: int = 0
                   ) -> list[tuple[str, str, str]]:
    """What each designed feature had available, and what it actually built.

    **This check exists because a feature was built, shipped, reviewed and
    reported on while being entirely absent from every board looked at.**
    Fences work; both crops chosen to review them were dense town centre, where
    a field boundary does not go. Twenty-two fence runs on the map, zero in the
    frame, and nothing said so.

    So the question this answers is not "is the code right" -- the other checks
    do that -- but "did this build contain the thing you think you are looking
    at". It compares what the *input* offered against what the *output* used,
    which is the only way to tell an absent feature from an inapplicable one:

    * offered and built      -> ok
    * offered and not built  -> **fail**; something is broken or switched off
    * not offered            -> ok, and say so, because "no fences here" is a
      fact about the map and not a fact about the code

    Returns ``(level, name, detail)`` triples.
    """
    from . import raster as R
    from .build import pick_towers

    out: list[tuple[str, str, str]] = []
    by_id = builder.palette.catalog.by_id
    used: set[str] = {p.asset_id for p in builder.placements}

    def built(roles) -> int:
        # Every variant of each role, not the one `resolve` settled on. The
        # terrain pass deals between a role's interchangeable tiles, so a
        # role's output is a *set* of ids -- `marsh_2x2` is three swamp
        # blocks. Asking about one of them would report a fen that is on the
        # board as absent, which is the exact false FAIL this whole report
        # exists to avoid.
        from .palette import role_variants

        ids = {a.id for role in roles
               for a in role_variants(builder.palette, role)}
        return len(ids & used)

    # -- gabled ridge ends ---------------------------------------------------
    # **Cropping a town destroys its quarters, and the gable is keyed on
    # them.** `quarter_map` measures clustering before it fires and returns
    # None below a lift of 1.2, which is the correct answer on a small map --
    # so a 44x40 crop of East Tradebourne has 14 buildings, a lift of 1.04, no
    # quarters, and every ridge hipped. That crop was pasted and read as "the
    # gable did not fire" when the truth was that the region could not contain
    # it. Measured: the same town needs a **160x160** crop, 193 buildings, for
    # its quarters to survive. Exactly the failure this whole function exists
    # for, arriving on a feature added after it was written.
    from .quarters import quarter_map
    from .build import gable_end_for
    quarters = quarter_map(tm)
    if quarters:
        dealt = {gable_end_for(q, seed) for q in sorted(set(quarters.values()))}
        gabled = dealt - {"hip"}
        if not gabled:
            out.append(("pass", "gabled ends",
                        "none; every quarter here dealt a hipped end at this "
                        "seed, which is the deal working rather than a miss"))
        else:
            want, clean = _gables_built(builder, tm, seed)
            # **A PRESENCE check, not a per-wing audit.** This function's own
            # contract is "did this build contain the thing you think you are
            # looking at", and `check_placements` is where correctness lives.
            # The distinction is not pedantry here: `_lay_roofs` roofs
            # *blocks* -- connected cells sharing a storey count, which a
            # terrace makes span two buildings -- while this counts per
            # building footprint, so a straggler or two is the two definitions
            # disagreeing rather than a defect. Failing on that would be a
            # check that cries wolf on every town with a terrace in it.
            if want and clean:
                stragglers = ("" if clean == want else
                              f"; {want - clean} of them still carry a hip "
                              f"corner, which is a terrace roofed as one block")
                out.append(("pass", "gabled ends",
                            f"{clean} of {want} wing(s) gabled, dealing "
                            f"{', '.join(sorted(gabled))}{stragglers}"))
            elif want:
                out.append(("fail", "gabled ends",
                            f"{want} wing(s) should be gabled and every one of "
                            f"them still carries a hip corner"))
            else:
                out.append(("pass", "gabled ends",
                            f"none; the quarters here deal "
                            f"{', '.join(sorted(gabled))} but no wing is big "
                            f"enough to have a ridge to end"))
    else:
        out.append(("pass", "gabled ends",
                    "none here; no quarters to key on, so every ridge is "
                    "hipped -- a crop rarely clusters enough to have any"))

    # -- chimneys ------------------------------------------------------------
    # **This line exists because of a measurement, not a hypothesis.** The
    # four-course flue was built, tested and written up, and reached 26 of
    # 1,274 stacks across the three towns -- 2% -- because it was wired into
    # one branch of `_lay_roofs` and nothing counted the others. East
    # Tradebourne's 1,084 chimneys were every one of them a single piece. That
    # is this function's own failure mode, arriving from a direction it did not
    # cover: not a feature absent from the crop, but a feature present in the
    # code and absent from the output.
    #
    # So it reports the SHAPE of what was laid, not just that something was.
    # "1,084 stacks" reads as a success; "1,084 stacks of 1 course" does not.
    from .build import chimney_form_for, is_chimney

    flues: dict[tuple[float, float], int] = {}
    pieces: set[str] = set()
    for p in builder.placements:
        asset = by_id(p.asset_id)
        if asset is None or not is_chimney(asset):
            continue
        flues[(p.x, p.z)] = flues.get((p.x, p.z), 0) + 1
        pieces.add(asset.name)
    roofed = any((a := by_id(p.asset_id)) is not None
                 and "roof" in (a.group_tag or "").lower()
                 and not is_chimney(a) for p in builder.placements)
    if flues:
        shape = ", ".join(f"{n} of {c} course(s)" for c, n in sorted(
            collections.Counter(flues.values()).items()))
        forms = (sorted({chimney_form_for(q, seed)
                         for q in set(quarters.values())})
                 if quarters else [])
        where = (f"; placed {'/'.join(forms)} by quarter" if forms else
                 "; every stack on the ridge, this map having no quarters")
        out.append(("pass", "chimneys",
                    f"{len(flues)} stack(s) -- {shape} -- from "
                    f"{len(pieces)} piece(s){where}"))
    elif roofed:
        out.append(("fail", "chimneys",
                    "the town has roofs and not one chimney on them"))
    else:
        out.append(("pass", "chimneys", "none; nothing here is roofed"))

    # -- field walls ---------------------------------------------------------
    runs = len(getattr(tm, "fences", ()) or ())
    on_map = len(getattr(layout, "fences", ()) or ()) if layout is not None else None
    if runs:
        level = "pass" if _fences_built(builder) else "fail"
        out.append((level, "field walls",
                    f"{runs} boundary run(s) on this map"
                    + ("" if level == "pass" else
                       " but nothing was built from them")))
    elif on_map:
        out.append(("pass", "field walls",
                    f"none here; the layout has {on_map}, all outside this crop"))
    else:
        out.append(("pass", "field walls", "none in the source"))

    # -- yards ---------------------------------------------------------------
    from .build import yard_cells

    yards = yard_cells(tm)
    total = len({v for row in tm.building for v in row if v})
    if yards:
        # **Ask the pass, do not infer from asset ids** -- the same rule
        # `_fences_built` states, and the yard boundary walked into it the
        # moment it was dealt per tier: `FEATURE_ROLES["yards"]` named
        # `yard_fence`, which only the utility tier builds from now, so a town
        # of hedged cottages reported its yards as unbuilt. And the reverse
        # fails too, since `field_wall` and `field_hedge` are shared with
        # `_lay_fences`. `Builder.yard_pieces` is what the pass laid.
        level = "pass" if _yards_built(builder) else "fail"
        cells = sum(len(c) for c in yards.values())
        forms = _yard_forms(tm, yards)
        out.append((level, "yards",
                    f"{len(yards)} of {total} buildings stand apart enough for a "
                    f"yard ({cells} cells; {forms})"
                    + ("" if level == "pass" else " but none was surfaced")))
    else:
        out.append(("pass", "yards",
                    f"none; no building of {total} stands clear of its neighbours"))

    # -- churches ------------------------------------------------------------
    #
    # **A COUNT IS NOT A SHAPE, and this branch exists because of that.** The
    # chimney line once read "1,084 stacks" and every one of them was a single
    # course; it now reads "1,084 of 1 course" and that reads as the bug it is.
    # The same trap was walked into twice more on churches in one session:
    # `SUBORDINATE_STEP` and `lay_spire` were both written, tested and written
    # up as landed while being reachable only from a probe tool, so they
    # touched **zero** real buildings. Nothing said a word, because this
    # function did not know churches existed.
    #
    # So the line reports the SPLIT and the SPIRE, not "there are churches".
    # And a temple offered and not split is a FAIL rather than an "ok, none
    # here": a town does not decline to have churches in it the way a map
    # declines to have field boundaries.
    temples = {b for b, (_n, r) in tm.church_parts.items() if r == "nave"}
    unsplit = {b for row in tm.building for b in row
               if b and b.split("-")[0] == "temple"
               and b not in tm.church_parts}
    plan = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.building[z][x]:
                plan.setdefault(tm.building[z][x], 0)
                plan[tm.building[z][x]] += 1
    small = {b for b in unsplit if plan.get(b, 0) < R.CHURCH_MIN_SPLIT_CELLS}
    big = unsplit - small
    spires = sum(1 for p in builder.placements
                 if (by_id(p.asset_id) is not None
                     and by_id(p.asset_id).name.startswith("Tall 2x2x4")))
    towers = len({b for b in pick_towers(tm, 3).values()
                  if b in tm.church_parts or b in unsplit})

    if temples or unsplit:
        bits = [f"{len(temples) + len(unsplit)} church(es)"]
        if temples:
            bits.append(f"{len(temples)} split into nave and chancel")
        if small:
            bits.append(f"{len(small)} under the "
                        f"{R.CHURCH_MIN_SPLIT_CELLS}-cell split threshold")
        if big:
            bits.append(f"{len(big)} BIG ENOUGH TO SPLIT AND NOT SPLIT")
        bits.append(f"{towers} with a tower, {spires // 4} spire(s)")
        level = "pass"
        detail = "; ".join(bits)
        if big:
            level = "fail"
            detail += " -- a church over the threshold that came out one box "
            detail += "means the split did not run"
        elif towers and spires < 4:
            level = "fail"
            detail += " -- a church tower with no spire on it means `lay_spire`"
            detail += " is not reachable from the build"
        out.append((level, "churches", detail))
    else:
        out.append(("pass", "churches", "none in the source"))

    # -- marsh ---------------------------------------------------------------
    wet = sum(1 for row in tm.surface for v in row if v == MARSH)
    on_map = (sum(1 for a in layout.areas if a.kind == "marsh")
              if layout is not None else None)
    if wet:
        level = "pass" if built(FEATURE_ROLES["marsh"]) else "fail"
        out.append((level, "marsh",
                    f"{wet} wetland cell(s) on this map"
                    + ("" if level == "pass" else
                       " but nothing marsh-specific was built on them -- "
                       "the fen is being laid as ordinary ground")))
    elif on_map:
        out.append(("pass", "marsh",
                    f"none here; the layout has {on_map} wetland area(s), "
                    "all outside this crop"))
    else:
        out.append(("pass", "marsh", "none in the source"))

    # -- quarters ------------------------------------------------------------
    from .quarters import buildings_of, clustering_lift, quarter_map, shares

    lift = clustering_lift(buildings_of(tm))
    quarters = quarter_map(tm)
    if quarters is None:
        out.append(("pass", "quarters",
                    f"none; trades cluster at {lift:.2f}x, under the threshold "
                    "-- this settlement has no quarters to find"))
    else:
        share = shares(quarters, tm)
        thin = [k for k, v in share.items() if v < 0.03 and k != "outskirts"]
        detail = (f"{lift:.2f}x clustering -> "
                  + ", ".join(f"{k} {v:.0%}" for k, v in share.items()))
        out.append(("warn" if thin else "pass", "quarters",
                    detail + (f"; {', '.join(thin)} too thin to read"
                              if thin else "")))

    # -- surfaces ------------------------------------------------------------
    ground = {a.name for a in (by_id(i) for i in used)
              if a is not None and a.kind == "tile" and a.size_y <= 0.5
              and a.size_x <= 2.0 and a.size_z <= 2.0}
    surfaces = sorted(n for n in ground if any(
        k in n.lower() for k in ("cobble", "grass", "gravel", "stone floor",
                                 "castle floor", "swamp", "desert ground",
                                 "floor stone", "tilled")))
    level = "pass" if len(surfaces) >= 3 else "warn"
    out.append((level, "surfaces",
                f"{len(surfaces)} distinct outdoor material(s): "
                + ", ".join(surfaces[:8])))

    # -- storeys -------------------------------------------------------------
    from .build import storeys_of

    heights = {}
    for bid in {v for row in tm.building for v in row if v}:
        heights[bid] = storeys_of(tm, bid, 3)
    if heights:
        counts = {n: sum(1 for v in heights.values() if v == n)
                  for n in sorted(set(heights.values()))}
        mean = sum(heights.values()) / len(heights)
        out.append(("warn" if len(counts) == 1 else "pass", "storeys",
                    " ".join(f"{n}:{c}" for n, c in counts.items())
                    + f", mean {mean:.2f}"
                    + ("  -- every building the same height"
                       if len(counts) == 1 else "")))
    return out
