"""SVG rendering of cities and floorplans.

Plain hand-written SVG so the tool stays dependency-free. These are GM-facing
reference maps, not the playable layer -- the playable layer is the slab.
"""

from __future__ import annotations

import html
import os
import pathlib

from .city import City
from .floorplan import Floorplan
from .sites import Site

_DISTRICT_FILL = {
    "civic": "#6b5b95",
    "market": "#c08a3e",
    "craft": "#7d6b57",
    "residential": "#5f7a61",
    "docks": "#3f6d7a",
    "slums": "#6b4f4f",
    "temple": "#8a7f9e",
}

_KIND_FILL = {
    "tavern": "#d08c3f",
    "temple": "#b0a3c9",
    "smithy": "#8a5a3b",
    "warehouse": "#7a6a55",
    "guildhall": "#9c7bb5",
    "manor": "#b58fa8",
    "barracks": "#7f8a99",
    "shop": "#c2a86b",
    "apothecary": "#79ab88",
    "stable": "#8f8168",
    "house": "#9a9a90",
}

_BG = "#14161a"
_STREET = "#2c3038"
_STREET_MAJOR = "#3a3f49"
_WALL = "#c9c3b6"
_TEXT = "#e6e3dc"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def city_svg(
    city: City,
    *,
    scale: int = 8,
    highlight: list[Site] | None = None,
    show_labels: bool = True,
) -> str:
    """Render the city to an SVG string.

    ``highlight`` marks ranked sites with numbered pins.
    """
    w = city.width * scale
    h = city.depth * scale
    pad = 40
    total_w, total_h = w + pad * 2, h + pad * 2 + 30

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}">',
        f'<rect width="{total_w}" height="{total_h}" fill="{_BG}"/>',
        f'<g transform="translate({pad},{pad})">',
        f'<rect width="{w}" height="{h}" fill="#1b1e24"/>',
    ]

    # Districts as translucent washes.
    for d in city.districts:
        fill = _DISTRICT_FILL.get(d.kind, "#555")
        parts.append(
            f'<rect x="{d.rect.x * scale}" y="{d.rect.z * scale}" '
            f'width="{d.rect.w * scale}" height="{d.rect.d * scale}" '
            f'fill="{fill}" fill-opacity="0.20"/>'
        )

    # Streets.
    for s in city.streets:
        fill = _STREET_MAJOR if s.major else _STREET
        parts.append(
            f'<rect x="{s.rect.x * scale}" y="{s.rect.z * scale}" '
            f'width="{s.rect.w * scale}" height="{s.rect.d * scale}" fill="{fill}"/>'
        )

    # Buildings.
    for b in city.buildings:
        fill = _KIND_FILL.get(b.kind, "#999")
        parts.append(
            f'<rect x="{b.rect.x * scale}" y="{b.rect.z * scale}" '
            f'width="{b.rect.w * scale}" height="{b.rect.d * scale}" '
            f'fill="{fill}" stroke="#0d0f12" stroke-width="0.8">'
            f"<title>{_esc(b.name)} ({_esc(b.kind)}) -- {b.rect.w}x{b.rect.d}, "
            f"{b.floors} floor(s)</title></rect>"
        )

    # City wall and gates.
    if city.walled and city.wall_rect:
        r = city.wall_rect
        parts.append(
            f'<rect x="{r.x * scale}" y="{r.z * scale}" '
            f'width="{r.w * scale}" height="{r.d * scale}" '
            f'fill="none" stroke="{_WALL}" stroke-width="{max(2, scale // 2)}"/>'
        )
        for gx, gz in city.gates:
            parts.append(
                f'<circle cx="{(gx + 0.5) * scale}" cy="{(gz + 0.5) * scale}" '
                f'r="{scale * 1.1}" fill="#e0c46a" stroke="#14161a" stroke-width="1"/>'
            )

    # Highlighted sites.
    if highlight:
        for i, site in enumerate(highlight, 1):
            b = site.building
            cx = (b.rect.x + b.rect.w / 2) * scale
            cz = (b.rect.z + b.rect.d / 2) * scale
            parts.append(
                f'<rect x="{b.rect.x * scale}" y="{b.rect.z * scale}" '
                f'width="{b.rect.w * scale}" height="{b.rect.d * scale}" '
                f'fill="none" stroke="#ff5f5f" stroke-width="2"/>'
            )
            parts.append(
                f'<circle cx="{cx}" cy="{cz}" r="9" fill="#ff5f5f"/>'
                f'<text x="{cx}" y="{cz + 4}" font-family="sans-serif" font-size="11" '
                f'font-weight="bold" fill="#14161a" text-anchor="middle">{i}</text>'
            )
            if show_labels:
                parts.append(
                    f'<text x="{cx}" y="{cz - 13}" font-family="sans-serif" font-size="11" '
                    f'fill="{_TEXT}" text-anchor="middle" '
                    f'stroke="#14161a" stroke-width="3" paint-order="stroke">'
                    f"{_esc(b.name)}</text>"
                )

    parts.append("</g>")
    parts.append(
        f'<text x="{pad}" y="{pad - 14}" font-family="sans-serif" font-size="16" '
        f'fill="{_TEXT}">{_esc(city.name)}</text>'
    )
    parts.append(
        f'<text x="{total_w - pad}" y="{pad - 14}" font-family="sans-serif" font-size="11" '
        f'fill="#8b8f98" text-anchor="end">seed {city.seed} - {city.width}x{city.depth} tiles '
        f'- {len(city.buildings)} buildings</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


_LAYOUT_DISTRICT_FILL = {
    "civic": "#6b5b95", "market": "#c08a3e", "craft": "#7d6b57",
    "residential": "#5f7a61", "docks": "#3f6d7a", "slums": "#6b4f4f",
    "temple": "#8a7f9e", "farm": "#8a8f5a",
}

_LAYOUT_BUILDING_FILL = {
    "tavern": "#d08c3f", "temple": "#b0a3c9", "smithy": "#8a5a3b",
    "warehouse": "#7a6a55", "guildhall": "#9c7bb5", "manor": "#b58fa8",
    "barracks": "#7f8a99", "shop": "#c2a86b", "apothecary": "#79ab88",
    "stable": "#8f8168", "house": "#9a9a90", "shed": "#6e6e68",
}

_ROAD_STYLE = {
    "river": ("#2f5d78", 1.0),
    "road": ("#3a3f49", 1.0),
    "plank": ("#6b5a42", 1.0),
}


def _path(points, scale: float) -> str:
    return " ".join(
        f"{'M' if i == 0 else 'L'}{x * scale:.1f},{y * scale:.1f}"
        for i, (x, y) in enumerate(points)
    )


def layout_svg(layout, *, scale: float = 4.0, labels: bool = True) -> str:
    """Render an imported :class:`~citysmith.layout.Layout`.

    This is the check that the geometry survived the trip from MFCG: if this
    looks like the map you generated, the importer is correct.
    """
    w = layout.width * scale
    h = layout.depth * scale
    pad = 44
    total_w, total_h = w + pad * 2, h + pad * 2 + 26

    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.0f}" height="{total_h:.0f}" '
        f'viewBox="0 0 {total_w:.0f} {total_h:.0f}">',
        f'<rect width="{total_w:.0f}" height="{total_h:.0f}" fill="{_BG}"/>',
        f'<g transform="translate({pad},{pad})">',
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#1b1e24"/>',
    ]

    def poly(ring, fill, opacity=1.0, stroke="none", sw=0.0, title=""):
        t = f"<title>{_esc(title)}</title>" if title else ""
        p.append(
            f'<polygon points="{" ".join(f"{x*scale:.1f},{y*scale:.1f}" for x, y in ring)}" '
            f'fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" '
            f'stroke-width="{sw}">{t}</polygon>'
        )

    # Terrain first, then wards, then streets, then structures.
    #
    # Woodland, pasture and lawn are all grass underfoot and the rasteriser
    # treats them as ground; they are drawn here because a reference map of an
    # FTG village with them missing is a hamlet floating in a void, which is not
    # what the source shows.
    for a in layout.areas_of("forest"):
        poly(a.ring, "#2f4a33", 0.9)
    for a in layout.areas_of("pasture"):
        poly(a.ring, "#5a6b45", 0.6)
    for a in layout.areas_of("lawn"):
        poly(a.ring, "#54703f", 0.6)
    # Marsh under water, matching the raster's own paint order: the fen is a
    # sheet of wet ground and the pools are the hollows in it.
    for a in layout.areas_of("marsh"):
        poly(a.ring, "#3d4f3a", 0.85)
    for a in layout.areas_of("water"):
        poly(a.ring, "#22485e")
    for a in layout.areas_of("field"):
        poly(a.ring, "#6d7040", 0.5)
    for a in layout.areas_of("park"):
        poly(a.ring, "#4a6b4a", 0.7)

    for d in layout.districts:
        poly(d.ring, _LAYOUT_DISTRICT_FILL.get(d.kind, "#555"), 0.22,
             stroke="#00000055", sw=0.6, title=f"{d.name} ({d.kind})")

    for road in layout.roads:
        colour, op = _ROAD_STYLE.get(road.kind, ("#3a3f49", 1.0))
        p.append(
            f'<path d="{_path(road.points, scale)}" fill="none" stroke="{colour}" '
            f'stroke-opacity="{op}" stroke-width="{max(1.0, road.width * scale):.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )

    for a in layout.areas_of("plaza"):
        poly(a.ring, "#b9a06a", 0.85)
    for a in layout.areas_of("bridge"):
        poly(a.ring, "#8a7a5c", 0.95)

    for line in layout.fences:
        p.append(
            f'<path d="{_path(line, scale)}" fill="none" stroke="#6b6459" '
            f'stroke-width="{max(0.6, 0.4 * scale):.1f}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )

    for b in layout.buildings:
        label = f"{b.name} -- " if b.name else ""
        poly(b.ring, _LAYOUT_BUILDING_FILL.get(b.kind, "#999"),
             1.0, stroke="#0d0f12", sw=0.4,
             title=f"{label}{b.id} ({b.kind}) -- {b.floors} floor(s), "
                   f"{b.district or 'outside the walls'}")

    for a in layout.areas_of("landmark"):
        poly(a.ring, "#e8d9a0", 1.0, stroke="#0d0f12", sw=0.6, title="landmark")

    for ring in layout.walls:
        p.append(
            f'<polygon points="{" ".join(f"{x*scale:.1f},{y*scale:.1f}" for x, y in ring)}" '
            f'fill="none" stroke="{_WALL}" '
            f'stroke-width="{max(2.0, layout.wall_thickness * scale):.1f}" '
            f'stroke-linejoin="round"/>'
        )

    for i, (gx, gy) in enumerate(layout.gates, 1):
        p.append(
            f'<circle cx="{gx*scale:.1f}" cy="{gy*scale:.1f}" r="{max(4.0, scale*1.6):.1f}" '
            f'fill="#e0c46a" stroke="#14161a" stroke-width="1.5"><title>gate {i}</title></circle>'
        )

    if labels:
        for d in layout.districts:
            cx, cy = 0.0, 0.0
            for x, y in d.ring[:-1] or d.ring:
                cx += x
                cy += y
            n = max(1, len(d.ring[:-1] or d.ring))
            p.append(
                f'<text x="{cx/n*scale:.1f}" y="{cy/n*scale:.1f}" font-family="sans-serif" '
                f'font-size="11" fill="{_TEXT}" text-anchor="middle" opacity="0.85" '
                f'stroke="#14161a" stroke-width="3" paint-order="stroke">{_esc(d.name)}</text>'
            )

    p.append("</g>")
    p.append(
        f'<text x="{pad}" y="{pad-16}" font-family="sans-serif" font-size="17" '
        f'fill="{_TEXT}">{_esc(layout.name)}</text>'
    )
    p.append(
        f'<text x="{total_w-pad:.0f}" y="{pad-16}" font-family="sans-serif" font-size="11" '
        f'fill="#8b8f98" text-anchor="end">{layout.source} - '
        f'{layout.width:.0f}x{layout.depth:.0f} tiles @ {layout.units_per_tile:.2f} u/tile - '
        f'{len(layout.buildings)} buildings</text>'
    )
    p.append("</svg>")
    return "\n".join(p)


def floorplan_svg(fp: Floorplan, *, scale: int = 28, marks=None) -> str:
    """Render every level of a floorplan side by side, with a tile grid.

    ``marks`` are the party's starting cells (``citysmith.scene.Mark``), drawn
    where the tokens go -- this sheet is what gets looked at while the board is
    loading.

    Every coordinate is taken relative to the level's **own** rect. The levels
    are already drawn in their own translated groups, so measuring from the
    building's rect put every room of a plan whose levels had been spread for
    play (`interior.spread_levels`) a panel-width outside its own panel.
    """
    pad = 30
    gap = 30
    lw = fp.rect.w * scale
    lh = fp.rect.d * scale
    total_w = pad * 2 + fp.levels * lw + (fp.levels - 1) * gap
    total_h = pad * 2 + lh + 30

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}">',
        f'<rect width="{total_w}" height="{total_h}" fill="{_BG}"/>',
        f'<text x="{pad}" y="{pad - 10}" font-family="sans-serif" font-size="15" '
        f'fill="{_TEXT}">{_esc(fp.name)} ({_esc(fp.kind)})</text>',
    ]

    for level in range(fp.levels):
        lr = fp.rect_on(level)
        ox = pad + level * (lw + gap)
        oy = pad + 8
        parts.append(f'<g transform="translate({ox},{oy})">')
        parts.append(f'<rect width="{lw}" height="{lh}" fill="#1b1e24"/>')

        for room in fp.rooms_on(level):
            r = room.rect
            rx = (r.x - lr.x) * scale
            ry = (r.z - lr.z) * scale
            parts.append(
                f'<rect x="{rx}" y="{ry}" width="{r.w * scale}" height="{r.d * scale}" '
                f'fill="#2f3742" stroke="#0d0f12" stroke-width="1"/>'
                f"<title>{_esc(room.name)}</title>"
            )
            parts.append(
                f'<text x="{rx + r.w * scale / 2}" y="{ry + r.d * scale / 2 + 4}" '
                f'font-family="sans-serif" font-size="10" fill="#cfd4dc" '
                f'text-anchor="middle">{_esc(room.name)}</text>'
            )

        # Tile grid -- this is a battle map, the squares matter.
        for gx in range(fp.rect.w + 1):
            parts.append(
                f'<line x1="{gx * scale}" y1="0" x2="{gx * scale}" y2="{lh}" '
                f'stroke="#3a4049" stroke-opacity="0.25" stroke-width="1"/>'
            )
        for gz in range(fp.rect.d + 1):
            parts.append(
                f'<line x1="0" y1="{gz * scale}" x2="{lw}" y2="{gz * scale}" '
                f'stroke="#3a4049" stroke-opacity="0.25" stroke-width="1"/>'
            )

        # Doors as gaps drawn on the relevant edge.
        for d in fp.doors:
            if d.level != level:
                continue
            dx = (d.x - lr.x) * scale
            dz = (d.z - lr.z) * scale
            colour = "#ffd166" if d.exterior else "#8fd694"
            if d.side == "n":
                x1, y1, x2, y2 = dx + 4, dz, dx + scale - 4, dz
            elif d.side == "s":
                x1, y1, x2, y2 = dx + 4, dz + scale, dx + scale - 4, dz + scale
            elif d.side == "w":
                x1, y1, x2, y2 = dx, dz + 4, dx, dz + scale - 4
            else:
                x1, y1, x2, y2 = dx + scale, dz + 4, dx + scale, dz + scale - 4
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{colour}" stroke-width="4" stroke-linecap="round"/>'
            )

        for s in fp.stairs:
            if s.from_level != level:
                continue
            sx = (s.x - lr.x) * scale
            sz = (s.z - lr.z) * scale
            parts.append(
                f'<rect x="{sx + 3}" y="{sz + 3}" width="{scale - 6}" height="{scale - 6}" '
                f'fill="none" stroke="#79c0ff" stroke-width="2"/>'
                f'<text x="{sx + scale / 2}" y="{sz + scale / 2 + 4}" font-size="11" '
                f'font-family="sans-serif" fill="#79c0ff" text-anchor="middle">S</text>'
            )

        for m in (marks or ()):
            if getattr(m, "level", 0) != level:
                continue
            mx = (m.x - lr.x) * scale
            mz = (m.z - lr.z) * scale
            parts.append(
                f'<rect x="{mx + 2}" y="{mz + 2}" width="{scale - 4}" '
                f'height="{scale - 4}" fill="#c1443c" fill-opacity="0.55" '
                f'stroke="#f0857c" stroke-width="1.5"/>'
                f'<title>{_esc(m.name)}</title>'
            )

        parts.append(
            f'<text x="0" y="{lh + 16}" font-family="sans-serif" font-size="11" '
            f'fill="#8b8f98">Level {level}</text>'
        )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


#: Distinct hues for the assets in one slab, walked in order of first
#: appearance. Not a hash of the name: a hash gives two neighbouring pieces
#: near-identical colours often enough to be useless, and the whole point of
#: this view is telling pieces APART.
_SLAB_HUES = (
    "#e0813c", "#4fa3c7", "#8bbf5a", "#c463b0", "#d8c04a", "#7d78d0",
    "#5fc2a0", "#cf5f5f", "#9a8b6b", "#4f8fd0", "#b8d05a", "#c78ad8",
)


def slab_svg(slab, catalog, *, scale: int = 22, label_every: int = 5,
             title: str = "") -> str:
    """One slab in plan, over a labelled tile grid, with the origin marked.

    **The point is probing without pasting.** A probe slab can otherwise only
    be read by making a board, pasting it and flying a camera at it -- and
    this project's history is largely screenshots read wrong: a rank of
    identical fence panels, a hip that hid its own holes, a verge whose
    bolsters were the probe rather than the kit. A plan with the grid and the
    coordinates on it answers "what is actually in this file, and where"
    before the game is opened, which is the half of the question a photograph
    is worst at.

    It is emphatically NOT a substitute for the board. It cannot show a mesh,
    what a rotation does to a face, or whether a run one cell thick reads as
    solid -- the three things every probe in `tools/` exists for. It shows
    footprints, heights and identities.

    Each placement is drawn as its **real footprint after rotation**, from
    `build.placed_bounds`, so a two-cell piece reads as two cells and an
    off-grid prop reads as off-grid. Lowest is drawn first, so what is on top
    of the picture is what is on top of the board.
    """
    from .build import placed_bounds

    byid = {str(a.id).lower(): a for a in catalog.assets}
    items = []
    for p in slab.placements:
        asset = byid.get(str(p.asset_id).lower())
        if asset is None:
            continue
        x0, z0, x1, z1 = placed_bounds(asset, p)
        items.append((p.y, asset, p, x0, z0, x1, z1))
    if not items:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="260" '
                'height="44"><text x="8" y="26" fill="#e6e3dc" '
                'font-family="monospace" font-size="12">empty slab</text></svg>')

    items.sort(key=lambda it: it[0])
    lo_x = min(it[3] for it in items)
    hi_x = max(it[5] for it in items)
    lo_z = min(it[4] for it in items)
    hi_z = max(it[6] for it in items)
    import math
    gx0, gz0 = math.floor(lo_x), math.floor(lo_z)
    gx1, gz1 = math.ceil(hi_x), math.ceil(hi_z)
    cols, rows = max(1, gx1 - gx0), max(1, gz1 - gz0)

    pad = 34
    w, h = cols * scale + pad * 2, rows * scale + pad * 2

    def sx(x):
        return pad + (x - gx0) * scale

    def sz(z):
        return pad + (z - gz0) * scale

    names = {}
    for _y, asset, _p, _a, _b, _c, _d in items:
        if asset.name not in names:
            names[asset.name] = _SLAB_HUES[len(names) % len(_SLAB_HUES)]

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">' % (w, h, w, h),
           '<rect width="%d" height="%d" fill="%s"/>' % (w, h, _BG)]

    for i in range(cols + 1):
        v = gx0 + i
        major = v % label_every == 0
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" '
                   'stroke-width="%s"/>'
                   % (sx(v), pad, sx(v), h - pad,
                      _STREET_MAJOR if major else _STREET,
                      "1.2" if major else "0.6"))
    for j in range(rows + 1):
        v = gz0 + j
        major = v % label_every == 0
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="%s"/>'
                   % (pad, sz(v), w - pad, sz(v),
                      _STREET_MAJOR if major else _STREET,
                      "1.2" if major else "0.6"))

    for i in range(cols + 1):
        v = gx0 + i
        if v % label_every:
            continue
        out.append('<text x="%.1f" y="%d" fill="%s" font-family="monospace" '
                   'font-size="10" text-anchor="middle" opacity="0.75">%d'
                   '</text>' % (sx(v), pad - 8, _TEXT, v))
    for j in range(rows + 1):
        v = gz0 + j
        if v % label_every:
            continue
        out.append('<text x="%d" y="%.1f" fill="%s" font-family="monospace" '
                   'font-size="10" text-anchor="end" opacity="0.75">%d'
                   '</text>' % (pad - 6, sz(v) + 3, _TEXT, v))

    # The origin, because a slab that does not reach (0,0) is the thing
    # `verify.chunk_anchors` fails a build over.
    if gx0 <= 0 <= gx1 and gz0 <= 0 <= gz1:
        out.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="none" '
                   'stroke="#e0813c" stroke-width="1.5"/>'
                   % (sx(0), sz(0)))

    for y, asset, p, x0, z0, x1, z1 in items:
        fill = names[asset.name]
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'fill="%s" fill-opacity="0.55" stroke="%s" '
                   'stroke-width="0.8"><title>%s  (%g, %g, %g)  rot %d'
                   '</title></rect>'
                   % (sx(x0), sz(z0), (x1 - x0) * scale, (z1 - z0) * scale,
                      fill, fill, _esc(asset.name), p.x, p.y, p.z, p.rot))

    out.append('<text x="%d" y="%d" fill="%s" font-family="monospace" '
               'font-size="11">%s %d placements, %dx%d tiles, y %g..%g</text>'
               % (pad, h - 10, _TEXT, _esc(title), len(items), cols, rows,
                  min(i[0] for i in items), max(i[0] for i in items)))
    out.append("</svg>")
    return "".join(out)


#: Isometric basis. cos30 / sin30, so a tile is a true 2:1 diamond and a
#: cube of side one reads as a cube. The vertical is one screen unit per tile
#: of HEIGHT, which is what makes a course countable off the picture.
_ISO_KX = 0.8660254037844387
_ISO_KY = 0.5

#: The four azimuths, named for the compass direction the CAMERA is at. Four
#: rather than free rotation on purpose: these are the faces `review.ps1 360`
#: walks in game, so a slab reviewed here and a slab reviewed on a board are
#: compared from the same places.
AXON_AZIMUTHS = (0, 90, 180, 270)


def _axon_rot(x: float, z: float, azimuth: int) -> tuple[float, float]:
    """Turn the world's (x, z) into the view's (u, v).

    A quarter turn keeps an axis-aligned box axis-aligned, which is the whole
    reason the azimuth is quantised: every face stays a rectangle and the
    projection stays four polygons per placement.
    """
    if azimuth == 90:
        return z, -x
    if azimuth == 180:
        return -x, -z
    if azimuth == 270:
        return -z, x
    return x, z


def slab_axon(slab, catalog, *, scale: int = 16, azimuth: int = 0,
              title: str = "", label_every: int = 5) -> str:
    """One slab as solid boxes in axonometric, from one of four azimuths.

    **This is the half of a slab a plan cannot show.** `slab_svg` answers what
    is in the file and where; it is silent about height, and height is what
    every roofscape defect in this project has been about -- a roof floating
    half a tile over the wall head, a chimney a course too tall, eaves at 10 ft
    on a building whose footprint says tithe barn. Those are all one glance in
    elevation and invisible in plan.

    Each placement is drawn as its true box: the footprint after rotation from
    `build.placed_bounds`, extruded from `p.y` to `p.y + size_y`. Three faces
    are drawn -- top, and the two the camera can see -- shaded from the piece's
    own legend colour, so a stack of one material reads as one mass and a piece
    from another kit stands out of it.

    **It orbits, and that is not a convenience.** This project's most expensive
    rule is that a probe read from one angle is a probe that lies: a rank of
    blades hides its own gaps from the front, a ruined wall covers its own
    holes from overhead. A single fixed axonometric would reproduce that
    failure in a viewer built to prevent it. `AXON_AZIMUTHS` is the same four
    faces `review.ps1 360` walks.

    What it still cannot do, and the reason no probe is retired for it: it
    draws colliders, not meshes. A piece whose collider fills the cell and
    whose mesh is a diagonal blade -- `md_wall_1x1_diag_01`, which cost this
    project a whole rampart -- looks perfectly solid here. Massing is
    answerable from this view; material and surface are not.

    Depth is painter's algorithm on the box's far corner, which is exact for
    boxes that do not interpenetrate and approximate for those that do. Slabs
    here interpenetrate deliberately (a chimney is lapped into its own roof),
    so read a seam as a drawing-order artifact rather than as geometry.
    """
    from .build import placed_bounds

    azimuth = azimuth % 360
    if azimuth not in AXON_AZIMUTHS:
        raise ValueError(f"azimuth must be one of {AXON_AZIMUTHS}")

    byid = {str(a.id).lower(): a for a in catalog.assets}
    boxes = []
    for p in slab.placements:
        asset = byid.get(str(p.asset_id).lower())
        if asset is None:
            continue
        x0, z0, x1, z1 = placed_bounds(asset, p)
        corners = [_axon_rot(x, z, azimuth)
                   for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]
        u0 = min(c[0] for c in corners)
        u1 = max(c[0] for c in corners)
        v0 = min(c[1] for c in corners)
        v1 = max(c[1] for c in corners)
        boxes.append({"asset": asset, "p": p, "u0": u0, "u1": u1,
                      "v0": v0, "v1": v1,
                      "y0": p.y, "y1": p.y + asset.size_y})
    if not boxes:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="260" '
                'height="44"><text x="8" y="26" fill="#e6e3dc" '
                'font-family="monospace" font-size="12">empty slab</text></svg>')

    # Colours are assigned in the SAME order as `slab_svg` and `slab_legend`,
    # so one legend serves all three views. Walk the placements, not the
    # boxes, in case an asset went missing from the catalog above.
    names: dict[str, str] = {}
    for p in slab.placements:
        asset = byid.get(str(p.asset_id).lower())
        if asset is not None and asset.name not in names:
            names[asset.name] = _SLAB_HUES[len(names) % len(_SLAB_HUES)]

    lo_u = min(b["u0"] for b in boxes)
    hi_u = max(b["u1"] for b in boxes)
    lo_v = min(b["v0"] for b in boxes)
    hi_v = max(b["v1"] for b in boxes)
    lo_y = min(b["y0"] for b in boxes)
    hi_y = max(b["y1"] for b in boxes)

    def proj(u, v, y):
        return ((u - lo_u - (v - lo_v)) * _ISO_KX * scale,
                ((u - lo_u) + (v - lo_v)) * _ISO_KY * scale - (y - lo_y) * scale)

    xs, ys = [], []
    for u in (lo_u, hi_u):
        for v in (lo_v, hi_v):
            for y in (lo_y, hi_y):
                sx, sy = proj(u, v, y)
                xs.append(sx)
                ys.append(sy)
    pad = 40
    off_x = pad - min(xs)
    off_y = pad - min(ys)
    w = int(max(xs) - min(xs)) + pad * 2
    h = int(max(ys) - min(ys)) + pad * 2 + 18

    def pt(u, v, y):
        sx, sy = proj(u, v, y)
        return sx + off_x, sy + off_y

    def poly(points, fill, opacity, tip=""):
        d = " ".join("%.1f,%.1f" % q for q in points)
        return ('<polygon points="%s" fill="%s" fill-opacity="%s" '
                'stroke="%s" stroke-width="0.5" stroke-opacity="0.55">%s'
                '</polygon>' % (d, fill, opacity, fill, tip))

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d">' % (w, h, w, h),
           '<rect width="%d" height="%d" fill="%s"/>' % (w, h, _BG)]

    # -- the ground plane, so a piece that does not touch it is visible as
    #    floating rather than merely high.
    import math
    gu0, gu1 = math.floor(lo_u), math.ceil(hi_u)
    gv0, gv1 = math.floor(lo_v), math.ceil(hi_v)
    for i in range(gu0, gu1 + 1):
        major = i % label_every == 0
        a, bq = pt(i, gv0, lo_y), pt(i, gv1, lo_y)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                   'stroke-width="%s"/>'
                   % (a[0], a[1], bq[0], bq[1],
                      _STREET_MAJOR if major else _STREET,
                      "1.1" if major else "0.5"))
    for j in range(gv0, gv1 + 1):
        major = j % label_every == 0
        a, bq = pt(gu0, j, lo_y), pt(gu1, j, lo_y)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                   'stroke-width="%s"/>'
                   % (a[0], a[1], bq[0], bq[1],
                      _STREET_MAJOR if major else _STREET,
                      "1.1" if major else "0.5"))

    # -- the height ruler. The point of the whole view is that a course is
    #    countable, so put the numbers on it rather than leaving it to the eye.
    rule_u, rule_v = gu0, gv1
    top = int(math.ceil(hi_y - lo_y))
    a, bq = pt(rule_u, rule_v, lo_y), pt(rule_u, rule_v, lo_y + max(1, top))
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
               'stroke-width="1" stroke-opacity="0.6"/>'
               % (a[0], a[1], bq[0], bq[1], _STREET_MAJOR))
    for k in range(0, top + 1):
        q = pt(rule_u, rule_v, lo_y + k)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                   'stroke-width="1" stroke-opacity="0.6"/>'
                   % (q[0] - 4, q[1], q[0], q[1], _STREET_MAJOR))
        if k % label_every == 0 or k == top:
            out.append('<text x="%.1f" y="%.1f" fill="%s" '
                       'font-family="monospace" font-size="9" '
                       'text-anchor="end" opacity="0.75">%g</text>'
                       % (q[0] - 6, q[1] + 3, _TEXT, lo_y + k))

    # -- the boxes. Far corner first; see the docstring on interpenetration.
    boxes.sort(key=lambda b: (b["u0"] + b["v0"] + b["y0"],
                              b["u0"] + b["v0"], b["y0"]))
    for b in boxes:
        asset, p = b["asset"], b["p"]
        fill = names.get(asset.name, _SLAB_HUES[0])
        u0, u1, v0, v1 = b["u0"], b["u1"], b["v0"], b["v1"]
        y0, y1 = b["y0"], b["y1"]
        tip = ('<title>%s  (%g, %g, %g)  rot %d  h %g</title>'
               % (_esc(asset.name), p.x, p.y, p.z, p.rot, asset.size_y))
        # top, then the two faces the camera is on the near side of
        out.append(poly([pt(u0, v0, y1), pt(u1, v0, y1),
                         pt(u1, v1, y1), pt(u0, v1, y1)], fill, "0.95", tip))
        out.append(poly([pt(u1, v0, y1), pt(u1, v1, y1),
                         pt(u1, v1, y0), pt(u1, v0, y0)], fill, "0.62"))
        out.append(poly([pt(u0, v1, y1), pt(u1, v1, y1),
                         pt(u1, v1, y0), pt(u0, v1, y0)], fill, "0.40"))

    out.append('<text x="%d" y="%d" fill="%s" font-family="monospace" '
               'font-size="11">%s %d placements, azimuth %d, '
               'y %g..%g (%g tiles tall)</text>'
               % (pad, h - 10, _TEXT, _esc(title), len(boxes), azimuth,
                  lo_y, hi_y, round(hi_y - lo_y, 2)))
    out.append("</svg>")
    return "".join(out)


def slab_legend(slab, catalog):
    """``[{name, count, size, kind, folder, colour}]``, coloured as `slab_svg`.

    Returned as data rather than drawn into the picture: the legend is the
    part a page wants to make interactive, and a caller that only wants the
    SVG should not have to parse it back out of one.
    """
    byid = {str(a.id).lower(): a for a in catalog.assets}
    order = []
    seen = {}
    for p in slab.placements:
        asset = byid.get(str(p.asset_id).lower())
        if asset is None:
            continue
        row = seen.get(asset.name)
        if row is None:
            row = {"name": asset.name,
                   "size": [asset.size_x, asset.size_y, asset.size_z],
                   "kind": asset.kind, "folder": asset.folder, "count": 0,
                   "colour": _SLAB_HUES[len(order) % len(_SLAB_HUES)]}
            seen[asset.name] = row
            order.append(asset.name)
        row["count"] += 1
    return [seen[n] for n in order]


def write(svg: str, path: str | os.PathLike[str]) -> pathlib.Path:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(svg, encoding="utf-8")
    return p
