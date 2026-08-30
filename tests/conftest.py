"""Shared fixtures.

The palette here is a **stub**, not the real one, for the same reason the
chunker tests stub theirs: `catalog.json` is built from a local TaleSpire
install, is gitignored, and does not exist in a fresh checkout or a worktree.
A test that needs the real catalog is a test that only runs on one machine.

The shapes are the real ones though -- a 0.5-thick floor, a 2.0-tall wall
0.5 deep -- because every placement rule in `build.py` is derived from
`ColliderBoundsBound`, and stubbing a wall as a cube would make the tests agree
with geometry the game will not produce.
"""

from __future__ import annotations

import random

import pytest

from citysmith.catalog import Asset


_IDS: dict[str, int] = {}


def _asset(letter: str, name: str, kind: str, sx: float, sy: float, sz: float,
           group: str = "", folder: str = "") -> Asset:
    # The folder is the kit, and `build._kit_of` reads it -- so a stub whose
    # pieces all sat in one folder would let a mismatched fabric pass every
    # test. Inferred from the name here, the way the real catalog has it.
    if not folder:
        low = name.lower()
        folder = ("Castle Fortified" if low.startswith("castle") else
                  "Rural" if low.startswith("rural") else
                  "Doors" if low.startswith("door") else
                  "Moorgoth" if low.startswith("moorgoth") else
                  "Nature" if "grass" in low else
                  "CobbleStones" if "cobble" in low else "Tavern")
    # The id has to be real hex -- the slab codec parses it as a UUID -- so the
    # `letter` is only a label and the id is numbered from it.
    n = _IDS.setdefault(letter, len(_IDS) + 1)
    return Asset(
        id=f"{n:08x}-1111-2222-3333-444444444444", name=name, kind=kind,
        pack="Medieval Fantasy", group_tag=group, tags=(), folder=folder,
        size_x=sx, size_y=sy, size_z=sz,
    )


FLOOR = _asset("a", "Tavern Floor 01", "tile", 1.0, 0.5, 1.0, "floor")
#: The civic kit, so a test can tell one fabric from another by `folder`.
CIVIC_WALL = _asset("g", "castle wall 1x1", "tile", 1.0, 2.0, 0.5, "wall")
CIVIC_WINDOW = _asset("h", "castle wall 1x1 window", "tile", 1.0, 2.0, 0.5, "wall")
CIVIC_DOOR = _asset("i", "Door - Fancy", "tile", 1.0, 2.0, 0.5, "door")
CIVIC_CORNER = _asset("j", "castle wall corner 1x1 base", "tile", 1.0, 2.0, 1.0, "corner")
CIVIC_FLOOR = _asset("k", "castle floor 1x1", "tile", 1.0, 0.5, 1.0, "floor")
#: Rural boarding: the utility kit, and it has no window on purpose.
UTIL_WALL = _asset("l", "Rural Wall 01", "tile", 1.0, 2.0, 0.5, "wall")
UTIL_CORNER = _asset("m", "Rural Corner", "tile", 1.0, 2.0, 1.0, "corner")
CORNER = _asset("n", "Tavern no floor (1x1 a)", "tile", 1.0, 2.0, 1.0, "corner")
WINDOW = _asset("p", "Village Roof Side Wall With Window 01", "tile", 1.0, 2.0, 0.5, "wall")
UPPER = _asset("b", "Rural Floor 02", "tile", 1.0, 0.5, 1.0, "floor")
WALL = _asset("c", "Village Roof Side Wall 01", "tile", 1.0, 2.0, 0.5, "wall")
INNER = _asset("d", "Wall (Plain, Small)", "tile", 1.0, 2.0, 0.5, "wall")
DOOR = _asset("e", "Door -Peasant", "tile", 1.0, 2.0, 0.5, "door")
STAIRS = _asset("f", "Stairs Wood 01", "tile", 1.0, 1.0, 1.0, "stairs")
STOOL = _asset("0", "stool wood 01", "prop", 0.5, 0.3, 0.3, "chair")
MARK = _asset("1", "Moorgoth Floor - Carpet Centre", "tile", 1.0, 0.5, 1.0, "floor")
GROUND = _asset("2", "Grass 1x1", "tile", 1.0, 0.5, 1.0, "grassland")
#: Cobble really is 0.25 thick where grass is 0.5, and that difference is the
#: whole reason `Builder.surface` places by top height -- laid from a common
#: bottom it is a 15 inch kerb. Stubbing them the same thickness would make
#: the tests agree with a board the game will not produce.
STREET = _asset("3", "CobbleStone Floor Small", "tile", 1.0, 0.25, 1.0, "floor")
#: The town-wall kit, shaped like the real pins because `_lay_town_wall`'s
#: whole logic is block-vs-curtain (`build.is_curtain_piece`, a dims test):
#: the mass is a full 1x1x1 cube (really `md_stairblock_01`; named castle-*
#: here so `_asset`'s folder inference keeps the rampart in one family), the
#: parapet is a 0.5-deep curtain piece that stands on the lip, and the walk
#: is a half-tile paver. Stubbing the cap as a cube would flip `crown_cell`
#: into its cap-as-paving branch and no merlon would ever stand on a lip.
WALL_CORE = _asset("q", "castle wall block 1x1", "tile", 1.0, 1.0, 1.0, "block")
WALL_CAP = _asset("r", "Castle Ruins Crenellation - Small", "tile", 1.0, 1.0, 0.5, "merlon")
WALL_WALK = _asset("s", "Castle Ruins floor stone 1x1", "tile", 1.0, 0.5, 1.0, "floor")
WALL_STAIR = _asset("t", "Castle Ruins Stair", "tile", 1.0, 1.0, 1.0, "stairs")
#: 4.0 wide exactly, because `_hang_portcullises` measures the grille against
#: the passage mouth and skips any that misses by more than half a tile -- a
#: stub of a different width would make the hang silently not happen, which
#: is precisely what the test using it exists to catch.
GATE = _asset("u", "Door - Portcullis double", "tile", 4.0, 3.75, 0.5, "door")
#: Trodden earth for the lane role. Thinner than the grass, like the real
#: gravel, so laying it exercises the same top-alignment rule as the street.
LANE_TILE = _asset("4", "gravel_1x1_01", "tile", 1.0, 0.25, 1.0, "floor")


class StubCatalog:
    assets = [FLOOR, UPPER, WALL, INNER, DOOR, STAIRS, STOOL, MARK, GROUND, STREET,
              CIVIC_WALL, CIVIC_WINDOW, CIVIC_DOOR, CIVIC_CORNER, CIVIC_FLOOR,
              UTIL_WALL, UTIL_CORNER, CORNER, WINDOW,
              WALL_CORE, WALL_CAP, WALL_WALK, WALL_STAIR, GATE, LANE_TILE]


class StubPalette:
    """Enough palette to build an interior, with the real shapes."""

    _ROLES = {
        "floor": FLOOR, "floor_upper": UPPER, "wall": WALL,
        "wall_interior": INNER, "door": DOOR, "stairs": STAIRS,
        "party_mark": MARK, "ground": GROUND, "street": STREET,
        "wall_window": WINDOW, "wall_corner": CORNER, "lane": LANE_TILE,
        "wall_civic": CIVIC_WALL, "wall_window_civic": CIVIC_WINDOW,
        "door_civic": CIVIC_DOOR, "wall_corner_civic": CIVIC_CORNER,
        "floor_civic": CIVIC_FLOOR,
        "wall_utility": UTIL_WALL, "wall_corner_utility": UTIL_CORNER,
        # The town wall: the curtain facing is the civic wall panel, exactly
        # as the real palette pins `castle wall 1x1` for both roles.
        "city_wall": CIVIC_WALL, "city_wall_core": WALL_CORE,
        "city_wall_cap": WALL_CAP, "city_wall_walk": WALL_WALK,
        "city_wall_stair": WALL_STAIR, "city_gate": GATE,
    }

    def __init__(self) -> None:
        self.catalog = StubCatalog()

    def resolve(self, role: str, variant: int = 0):
        return self._ROLES.get(role)

    def require(self, role: str, variant: int = 0) -> Asset:
        asset = self._ROLES.get(role)
        if asset is None:
            raise KeyError(role)
        return asset

    def prop(self, category: str, rng: random.Random):
        return STOOL


@pytest.fixture
def catalog_palette() -> StubPalette:
    return StubPalette()
