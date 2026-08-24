"""The same plan has to build the same board, in a second process.

This is not the usual determinism test. Building twice inside one process
always agrees, because Python fixes its string-hash seed once per process --
so the defect this guards was invisible to every test in the suite and showed
up only as a board that read as rebuilt when nothing had changed.

`_interior_walls` returns a ``set`` of ``(x, z, side)``, and `side` is a
string. Across two processes that set iterates in two different orders, so the
partitions were emitted in a different order every run: identical geometry,
231 placements, same multiset -- different bytes. An undiffable build, and a
digest of the file that reports a change that did not happen.
"""

from __future__ import annotations

import os
import subprocess
import sys

#: Builds one interior and prints the placements in emission order. Kept
#: self-contained so the subprocess needs nothing but the package.
SCRIPT = """
import sys
from citysmith.build import build_interior
from citysmith.catalog import Asset
from citysmith.city import Building, Rect
from citysmith.floorplan import generate


def asset(letter, name, sx, sy, sz):
    return Asset(id=letter * 8 + "-1111-2222-3333-444444444444", name=name,
                 kind="tile", pack="p", group_tag="", tags=(), folder="f",
                 size_x=sx, size_y=sy, size_z=sz)


FLOOR = asset("a", "floor", 1.0, 0.5, 1.0)
WALL = asset("c", "wall", 1.0, 2.0, 0.5)
INNER = asset("d", "inner", 1.0, 2.0, 0.5)
DOOR = asset("e", "door", 1.0, 2.0, 0.5)


class P:
    catalog = type("C", (), {"assets": [FLOOR, WALL, INNER, DOOR]})()
    _R = {"floor": FLOOR, "floor_upper": FLOOR, "wall": WALL,
          "wall_interior": INNER, "door": DOOR}

    def resolve(self, role, variant=0):
        return self._R.get(role)

    def require(self, role, variant=0):
        return self._R[role]

    def prop(self, category, rng):
        return None


fp = generate(
    Building(id="tavern-0001", name="The Fox", kind="tavern", district="",
             rect=Rect(0, 0, 9, 7), floors=1, entrance="s"),
    seed=33,
)
b = build_interior(fp, P(), seed=33)
for p in b.placements:
    print(p.asset_id, p.x, p.y, p.z, p.rot)
"""


def _emit(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_an_interior_builds_the_same_way_under_a_different_hash_seed():
    a = _emit("1")
    b = _emit("2")
    assert a.splitlines(), "the subprocess built nothing"
    assert a == b, "the same plan emitted its placements in a different order"
