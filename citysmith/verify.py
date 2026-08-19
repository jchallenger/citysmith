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
* are streets wide enough for creatures to pass abreast?
* does every building have a way in?
* does the result fit TaleSpire's board and slab limits?
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import TILE_FEET
from .raster import (
    FLOOR,
    GROUND,
    OPEN,
    PIER,
    PLAZA,
    SIDES,
    STREET,
    WATER,
    TileMap,
    components,
    open_width_at,
    reachable_from,
)

#: TaleSpire board limits, confirmed from BouncyRock's published figures.
BOARD_MAX_TILES = 1_000_000
BOARD_MAX_SPAN = 2000

#: A creature occupies one tile; two must pass abreast on a real street.
MIN_STREET_TILES = 2

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
    narrow = 0
    sampled = 0
    for z in range(tm.depth):
        for x in range(tm.width):
            if tm.surface[z][x] not in (STREET, PLAZA):
                continue
            sampled += 1
            if open_width_at(tm, x, z) < MIN_STREET_TILES:
                narrow += 1
    if sampled:
        share = 100 * narrow / sampled
        report.stats["narrow_street_tiles"] = narrow
        report.add(
            "pass" if share < 5 else "warn", "street width",
            f"{narrow} of {sampled} street tiles ({share:.1f}%) are under "
            f"{MIN_STREET_TILES} tiles ({MIN_STREET_TILES*TILE_FEET:.0f} ft) wide -- "
            "creatures cannot pass abreast there",
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
    return problems

