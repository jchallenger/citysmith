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


def _asset(letter: str, name: str, kind: str, sx: float, sy: float, sz: float,
           group: str = "") -> Asset:
    return Asset(
        id=f"{letter * 8}-1111-2222-3333-444444444444", name=name, kind=kind,
        pack="Medieval Fantasy", group_tag=group, tags=(), folder="Tavern",
        size_x=sx, size_y=sy, size_z=sz,
    )


FLOOR = _asset("a", "Tavern Floor 01", "tile", 1.0, 0.5, 1.0, "floor")
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


class StubCatalog:
    assets = [FLOOR, UPPER, WALL, INNER, DOOR, STAIRS, STOOL, MARK, GROUND, STREET]


class StubPalette:
    """Enough palette to build an interior, with the real shapes."""

    _ROLES = {
        "floor": FLOOR, "floor_upper": UPPER, "wall": WALL,
        "wall_interior": INNER, "door": DOOR, "stairs": STAIRS,
        "party_mark": MARK, "ground": GROUND, "street": STREET,
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
