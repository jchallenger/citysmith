"""Author Sedgewater -- a hamlet of ~100 on the edge of a fen -- as an FTG export.

The brief: a small hamlet near a swamp; a small keep inside a light barricade;
a small market that the shops collate around; larger homes out in the fields.

Nothing in citysmith generates that shape, so it is authored here in the one
interchange format the pipeline already reads end to end
(``docs/ftg-geojson-import.md`` section 2 is the schema). Coordinates are
metres, which is FTG's declared unit, so every dimension below is a real
dimension: a cottage is 11 x 9 m because an FTG house measures 10.3-10.7 m
across, and the footprints were sized so ``interior.occupants`` deals ~100
people over the 24 buildings.

    python tools/sedgewater.py
    python -m citysmith --out-dir out/sedgewater import samples/sedgewater.geojson \
        --whole-canvas --margin-ft 200 --name Sedgewater

``--whole-canvas`` is not optional: ``ftg.core_cluster`` single-links building
centroids at 60 m and keeps the largest group, and the whole point of "larger
homes in the fields" is that they stand further out than that.

Use it rather than ``--no-clip``, which drops the crop window *and the margin
with it*: with no window the frame comes from ``bounds(buildings)`` exactly, so
the outermost cottage sits on the board edge and every acre of fen beyond it is
off the map. Measured -- under ``--no-clip`` four of the five fen pools had
centroids outside the board and the barricade ring overran the south edge by
ten tiles.
"""

from __future__ import annotations

import json
import math
import pathlib
import random

FEATURES: list[dict] = []
_COUNT = {"BUILDING": 0, "EDGE": 0, "BACKGROUND": 0, "WATER": 0}


def _feature(kind: str, geom: dict, props: dict) -> None:
    _COUNT[kind] += 1
    FEATURES.append({
        "type": "Feature",
        "geometry": geom,
        "properties": {"id": _COUNT[kind], "type": kind, **props},
    })


def _ring(points) -> dict:
    pts = [[round(x, 2), round(y, 2)] for x, y in points]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def quad(cx, cy, w, h, deg=0.0):
    """A rotated rectangle centred on (cx, cy). An FTG building is a rotated quad."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)
            for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                           (w / 2, h / 2), (-w / 2, h / 2))]


def blob(cx, cy, rx, ry, n, wobble, seed):
    """An irregular closed ring -- a fen pool, a stand of carr, a field."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        k = 1.0 + rng.uniform(-wobble, wobble)
        out.append((cx + rx * k * math.cos(t), cy + ry * k * math.sin(t)))
    return out


def building(cx, cy, w, h, deg, name, btype, material="WOOD") -> None:
    _feature("BUILDING", _ring(quad(cx, cy, w, h, deg)),
             {"name": name, "buildingType": btype, "material": material})


def background(points, btype) -> None:
    _feature("BACKGROUND", _ring(points), {"backgroundType": btype, "raised": False})


def water(points) -> None:
    _feature("WATER", _ring(points), {})


def line(points, edge_type) -> None:
    """FTG ships every EDGE as a 2-point segment; the importer chains them."""
    for a, b in zip(points, points[1:]):
        _feature("EDGE",
                 {"type": "LineString",
                  "coordinates": [[round(a[0], 2), round(a[1], 2)],
                                  [round(b[0], 2), round(b[1], 2)]]},
                 {"edgeType": edge_type})


def ring_line(points, edge_type) -> None:
    line(list(points) + [points[0]], edge_type)


# ---------------------------------------------------------------- the ground
# GRASS is the base sheet; the importer drops it and lets anything else win.
background([(0, 0), (460, 0), (460, 420), (0, 420)], "GRASS")

# The fen, west and south-west: MARSH sheet with WATER pools in the hollows.
#
# These were FOREST before citysmith could say "wetland", and FOREST is a
# **no-op on the board** -- `raster.rasterize` paints field, marsh, water,
# roads, plaza and park, and never looks at a forest area. So the first
# Sedgewater's fen was five ponds in a field, and the reference map was the
# only place the swamp existed at all. That is the "designed, drawn, and
# absent from the board" failure this project keeps rediscovering, and it is
# why `verify.feature_report` now carries a marsh entry.
#
# The last blob carries the fen to the SOUTH-WEST BOARD EDGE, and it is there
# for a measured reason rather than for looks. Without it the fen was a *band*
# with 790 cells of dry ground stranded behind it, and `verify` correctly
# called that a second disconnected district: a marsh is not in `raster.OPEN`,
# so -- exactly like a ploughed field, which has always behaved this way -- it
# is a barrier to the walkable network rather than part of it. A wetland
# either runs off the edge of the map or it fences off whatever is behind it.
for i, (cx, cy, rx, ry) in enumerate([
        (112, 152, 60, 56), (106, 248, 56, 60), (150, 316, 50, 42),
        (176, 100, 44, 36), (108, 104, 58, 44)]):
    background(blob(cx, cy, rx, ry, 14, 0.16, 700 + i), "MARSH")
for i, (cx, cy, rx, ry) in enumerate([
        (106, 170, 28, 23), (94, 256, 23, 29), (146, 320, 21, 16),
        (152, 212, 14, 19), (176, 108, 16, 12)]):
    water(blob(cx, cy, rx, ry, 12, 0.22, 900 + i))

# Carr -- wet woodland on the landward fringe, where the fen dries out enough
# for alder to hold. Drawn on the reference map only, for the reason above.
for i, (cx, cy, rx, ry) in enumerate([(168, 148, 26, 30), (162, 272, 24, 26)]):
    background(blob(cx, cy, rx, ry, 12, 0.14, 760 + i), "FOREST")

# The open ground east and south-east -- what the larger homes stand in.
background(blob(340, 232, 66, 52, 10, 0.10, 11), "WHEAT")
background(blob(352, 300, 60, 48, 10, 0.10, 12), "TILLED")
background(blob(258, 330, 62, 46, 10, 0.10, 13), "GRAIN")
background(blob(300, 128, 54, 44, 10, 0.10, 14), "SHEEP_TEXTURE_TYPE")
background(blob(196, 292, 46, 38, 10, 0.10, 15), "PIGS_TEXTURE_TYPE")
background(blob(212, 200, 44, 40, 10, 0.08, 16), "LAWN_TEXTURE_TYPE")

# ---------------------------------------------------------------- the market
# FTG ships a market square as a BUILDING with material PAVEMENT, and the
# importer diverts one into a plaza rather than roofing a box over it.
building(210, 200, 26, 26, 0, "Sedgewater Market", "MARKET", material="PAVEMENT")

# What collates around it: every trade in the hamlet fronts this one square.
building(210, 174, 14, 12, 0, "The Drowned Bell", "INN")
building(182, 194, 11, 8.5, 90, "Hobb's Forge", "ARTISAN")
building(238, 190, 10.5, 8, 90, "Marrow and Daughter, Chandlers", "SHOP")
building(238, 209, 10.5, 8, 90, "The Reedcutters' Stall", "SHOP")
building(224, 228, 10, 7, 0, "Old Tass's Physick", "SERVICE")
building(190, 230, 12, 9.5, 0, "Shrine of the Still Water", "RELIGIOUS",
         material="STONE_BRICK")
building(248, 230, 14, 10, 0, "The Tithe Barn", "WAREHOUSE")
building(250, 174, 12.5, 9, 0, "Fenwold Stables", "FARM")
building(186, 210, 10.5, 8, 90, "Sedge and Withy", "SHOP")

# Cottages on the lanes off the square.
for cx, cy, deg, name in [
        (176, 166, 12, "Kettleby Cottage"), (166, 216, -8, "Wren's Cot"),
        (180, 252, 5, "The Old Ferryman's"), (222, 256, -6, "Danner's Cot"),
        (256, 252, 10, "Hallow Cottage"), (266, 198, -4, "Pye's Cot")]:
    building(cx, cy, 11, 9, deg, name, "RESIDENCE")

# Three cots out on the fen edge, where the reedcutters live.
for cx, cy, deg, name in [
        (146, 182, -14, "Marsh Cot"), (142, 242, 9, "Bogsen's Hut"),
        (150, 268, -6, "The Eelman's")]:
    building(cx, cy, 10, 7, deg, name, "RESIDENCE")

# ------------------------------------------------------------------ the keep
# On the dry rise north-east of the village, deliberately clear of the fen.
# Stone, and the only stone here besides the shrine.
building(302, 142, 16, 13, 0, "Sedgewater Keep", "LAW_ENFORCEMENT",
         material="STONE_BRICK")
building(302, 164, 13, 11.5, 0, "The Garrison Range", "FACTION",
         material="STONE_BRICK")

# The light barricade: a closed run of STONE_FENCE, NOT a STONE_WALL. A wall
# rasterises to a 4.5 m rampart with square bastions and a carved gate, which
# is a castle. A fence run is one course thick, and `--fence-style paling`
# builds it as timber -- which is what "light barricade" means.
BARRICADE = blob(302, 152, 30, 27, 16, 0.06, 55)
ring_line(BARRICADE, "STONE_FENCE")

# ------------------------------------------ the larger homes, out in the fields
for cx, cy, deg, name in [
        (330, 232, -10, "Highfield House"), (352, 296, 7, "Wheatmoor"),
        (296, 314, -5, "Tallowbeck Grange"), (244, 316, 12, "Sedgemoor House")]:
    building(cx, cy, 15, 12.5, deg, name, "RESIDENCE")

# Their field boundaries.
line([(300, 262), (352, 256), (392, 268)], "STONE_FENCE")
line([(268, 288), (306, 276), (318, 246)], "STONE_FENCE")
line([(226, 292), (252, 282), (284, 288)], "STONE_FENCE")
line([(370, 210), (386, 244), (376, 282)], "STONE_FENCE")

# --------------------------------------------------------------------- roads
line([(6, 236), (68, 230), (128, 226), (188, 221), (240, 216),
      (292, 208), (352, 202), (452, 196)], "MAIN_ROAD")        # the fen road
line([(292, 208), (298, 186), (302, 172)], "ROAD")             # up to the keep
ring_line(quad(210, 200, 30, 30), "SMALL_ROAD")                # round the square
line([(210, 185), (210, 170)], "SMALL_ROAD")                   # square to the inn
line([(195, 215), (176, 224), (168, 244), (180, 258)], "SMALL_ROAD")
line([(225, 215), (232, 240), (250, 252)], "SMALL_ROAD")
line([(195, 185), (172, 174), (156, 178)], "SMALL_ROAD")
line([(225, 185), (248, 180), (262, 192)], "SMALL_ROAD")
# The fen lane runs a clear 12 m EAST of the reedcutters' cots. Routed over
# them it classified as a cart route, and `raster` keeps cart routes open
# against a footprint -- Marsh Cot lost 18 of its 30 cells and vanished.
line([(162, 176), (160, 196), (158, 216), (160, 238), (168, 262)], "DIRT_ROAD")

# **Droves out to the three cots, and they are not decoration.** A marsh is
# walkable but it is not in `raster.OPEN` -- no front door opens onto a bog --
# so the moment the fen became real marsh instead of ordinary ground, these
# three cots were standing in it with no way to them: `verify` dropped to
# 87.5% building access with three doorways opening into sealed courtyards,
# and called the far bank a second disconnected district. That is the check
# being right. A reedcutter's cot has a drove out to it in life, too.
line([(160, 190), (152, 184)], "DIRT_ROAD")     # to Marsh Cot
line([(159, 240), (148, 242)], "DIRT_ROAD")     # to Bogsen's Hut
line([(164, 264), (156, 268)], "DIRT_ROAD")     # to The Eelman's
line([(320, 204), (328, 226), (334, 250)], "DIRT_ROAD")        # to Highfield
line([(334, 250), (350, 282), (352, 300)], "DIRT_ROAD")        # to Wheatmoor
line([(250, 252), (272, 286), (294, 308)], "DIRT_ROAD")        # to Tallowbeck
line([(272, 286), (250, 302), (244, 316)], "DIRT_ROAD")        # to Sedgemoor
line([(158, 220), (124, 244), (108, 270)], "TRAIL")            # into the fen
line([(160, 196), (132, 180), (112, 164)], "TRAIL")

out = pathlib.Path("samples/sedgewater.geojson")
out.write_text(json.dumps({"type": "FeatureCollection", "features": FEATURES}), "utf-8")
print(f"{out}: {len(FEATURES)} features  " +
      "  ".join(f"{k}={v}" for k, v in _COUNT.items()))


def audit() -> int:
    """Every authored building must survive the raster. Run it, do not assume.

    A footprint laid over a through route loses the overlap -- `raster` keeps
    cart and main routes open against a building -- and a footprint that loses
    enough of itself is then swept by `_absorb_fragments`. Both are silent:
    the import report still says 24 buildings, and the *verify* line drops to
    "23 of 23", which reads like a pass. Two cots were lost that way while
    this file was being written, so the check lives here rather than in a
    session's memory.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from citysmith import importers, raster
    from citysmith.layout import centroid

    # `core_only=False` is what the CLI's `--whole-canvas` sets. Pass the CLI
    # spelling here and `importers._filter` drops it silently -- it keeps only
    # the names in `_FTG_OPTIONS` -- and the core crop runs anyway.
    lay = importers.import_layout(str(out), core_only=False, margin_feet=200.0,
                                  name="Sedgewater")
    tm = raster.rasterize(lay)
    seen = {b for row in tm.building for b in row if b}
    lost = [b for b in lay.buildings if b.id not in seen]
    for b in lost:
        cx, cz = centroid(b.ring)
        print(f"  LOST {b.id} {b.name!r} at ({cx:.0f},{cz:.0f}) -- "
              "it is under a through route; move it clear")
    print(f"  audit: {len(lay.buildings) - len(lost)} of {len(lay.buildings)} "
          "buildings survive the raster")
    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(audit())
