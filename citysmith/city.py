"""Procedural city generation.

The city is a purely geometric artifact measured in TaleSpire tiles, so the
same model drives the 2D reference render, the coarse 3D city board, and the
per-site interiors. Generation is a binary space partition:

    city rect -> [wall ring] -> blocks separated by streets -> plots -> buildings

Everything is driven by a single seeded RNG, so a seed fully determines a city.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
from dataclasses import asdict, dataclass, field
from typing import Iterator, Literal

CITY_VERSION = 1


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle in tile units. ``x``/``z`` is the min corner."""

    x: int
    z: int
    w: int
    d: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def z2(self) -> int:
        return self.z + self.d

    @property
    def area(self) -> int:
        return self.w * self.d

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.z + self.d / 2)

    def inset(self, by: int) -> "Rect":
        return Rect(self.x + by, self.z + by, max(0, self.w - 2 * by), max(0, self.d - 2 * by))

    def contains(self, px: float, pz: float) -> bool:
        return self.x <= px < self.x2 and self.z <= pz < self.z2

    def tiles(self) -> Iterator[tuple[int, int]]:
        for tz in range(self.z, self.z2):
            for tx in range(self.x, self.x2):
                yield tx, tz


DistrictKind = Literal["civic", "market", "craft", "residential", "docks", "slums", "temple"]

#: Building kinds available per district, with relative weights.
_DISTRICT_BUILDINGS: dict[str, list[tuple[str, int]]] = {
    "civic": [("guildhall", 3), ("manor", 3), ("barracks", 2), ("temple", 2), ("house", 2)],
    "market": [("shop", 6), ("tavern", 3), ("warehouse", 2), ("apothecary", 2), ("house", 2)],
    "craft": [("smithy", 4), ("shop", 3), ("warehouse", 2), ("house", 3), ("stable", 2)],
    "residential": [("house", 8), ("tavern", 2), ("shop", 2), ("apothecary", 1)],
    "docks": [("warehouse", 5), ("tavern", 3), ("shop", 2), ("house", 2)],
    "slums": [("house", 6), ("tavern", 2), ("warehouse", 1)],
    "temple": [("temple", 4), ("house", 2), ("guildhall", 1)],
}

#: Radial bands from the city centre outwards.
_RADIAL_BANDS: list[tuple[float, list[str]]] = [
    (0.22, ["civic", "temple"]),
    (0.45, ["market", "craft"]),
    (0.75, ["residential", "craft"]),
    (1.01, ["residential", "slums", "docks"]),
]


@dataclass
class Building:
    id: str
    name: str
    kind: str
    district: str
    rect: Rect
    floors: int = 1
    #: Compass side the entrance sits on: n/s/e/w (−z/+z/+x/−x).
    entrance: str = "s"
    hook: str = ""
    owner: str = ""

    @property
    def footprint(self) -> int:
        return self.rect.area


@dataclass
class Street:
    name: str
    rect: Rect
    major: bool = False


@dataclass
class District:
    name: str
    kind: str
    rect: Rect


@dataclass
class City:
    name: str
    seed: int
    style: str
    width: int
    depth: int
    walled: bool
    districts: list[District] = field(default_factory=list)
    streets: list[Street] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)
    gates: list[tuple[int, int]] = field(default_factory=list)
    wall_rect: Rect | None = None

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict:
        def rect(r: Rect | None) -> dict | None:
            return asdict(r) if r else None

        return {
            "city_version": CITY_VERSION,
            "name": self.name,
            "seed": self.seed,
            "style": self.style,
            "width": self.width,
            "depth": self.depth,
            "walled": self.walled,
            "wall_rect": rect(self.wall_rect),
            "gates": [list(g) for g in self.gates],
            "districts": [{"name": d.name, "kind": d.kind, "rect": rect(d.rect)} for d in self.districts],
            "streets": [{"name": s.name, "rect": rect(s.rect), "major": s.major} for s in self.streets],
            "buildings": [
                {
                    "id": b.id, "name": b.name, "kind": b.kind, "district": b.district,
                    "rect": rect(b.rect), "floors": b.floors, "entrance": b.entrance,
                    "hook": b.hook, "owner": b.owner,
                }
                for b in self.buildings
            ],
        }

    def save(self, path: str | os.PathLike[str]) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "City":
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "City":
        if data.get("city_version") != CITY_VERSION:
            raise ValueError(
                f"city file version {data.get('city_version')!r} is not supported "
                f"(expected {CITY_VERSION}); regenerate it."
            )

        def rect(d: dict | None) -> Rect | None:
            return Rect(**d) if d else None

        city = cls(
            name=data["name"], seed=data["seed"], style=data["style"],
            width=data["width"], depth=data["depth"], walled=data["walled"],
            wall_rect=rect(data.get("wall_rect")),
            gates=[tuple(g) for g in data.get("gates", [])],
        )
        city.districts = [District(d["name"], d["kind"], rect(d["rect"])) for d in data["districts"]]
        city.streets = [Street(s["name"], rect(s["rect"]), s.get("major", False)) for s in data["streets"]]
        city.buildings = [
            Building(
                id=b["id"], name=b["name"], kind=b["kind"], district=b["district"],
                rect=rect(b["rect"]), floors=b.get("floors", 1),
                entrance=b.get("entrance", "s"), hook=b.get("hook", ""),
                owner=b.get("owner", ""),
            )
            for b in data["buildings"]
        ]
        return city

    # -- lookups --------------------------------------------------------------

    def building(self, building_id: str) -> Building | None:
        for b in self.buildings:
            if b.id == building_id:
                return b
        return None

    def summary(self) -> str:
        kinds: dict[str, int] = {}
        for b in self.buildings:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
        return (
            f"{self.name} -- {self.width}x{self.depth} tiles, "
            f"{len(self.districts)} districts, {len(self.buildings)} buildings ({parts})"
        )


#: Named size presets in tiles.
SIZES: dict[str, int] = {
    "hamlet": 48,
    "village": 72,
    "town": 104,
    "city": 144,
    "metropolis": 200,
}


@dataclass
class CityParams:
    """Tunables for :func:`generate`. Defaults produce a walled market town."""

    size: str | int = "town"
    walled: bool = True
    style: str = "medieval"
    #: Width of ordinary streets and of the main arterial roads, in tiles.
    street_width: int = 2
    major_street_width: int = 3
    #: A block stops subdividing below this size.
    min_block: int = 14
    #: A plot stops subdividing below this size.
    min_plot: int = 6
    #: Gap between a building and its plot edge.
    yard: int = 1
    max_floors: int = 3
    name: str | None = None

    def resolved_size(self) -> int:
        if isinstance(self.size, int):
            return self.size
        try:
            return SIZES[self.size]
        except KeyError:
            raise ValueError(
                f"Unknown size {self.size!r}. Use one of {', '.join(SIZES)} or an integer."
            ) from None


def _split(rect: Rect, rng: random.Random, gap: int, min_size: int) -> tuple[Rect, Rect, Rect] | None:
    """Split ``rect`` in two, returning ``(a, b, gap_rect)``.

    Returns ``None`` when the rectangle cannot be split while leaving both
    halves at least ``min_size`` on the split axis.
    """
    horizontal = rect.w >= rect.d
    length = rect.w if horizontal else rect.d
    # Both halves need min_size, plus the gap between them.
    if length < 2 * min_size + gap:
        return None
    low = min_size
    high = length - min_size - gap
    if high < low:
        return None
    cut = rng.randint(low, high)

    if horizontal:
        a = Rect(rect.x, rect.z, cut, rect.d)
        gap_rect = Rect(rect.x + cut, rect.z, gap, rect.d)
        b = Rect(rect.x + cut + gap, rect.z, rect.w - cut - gap, rect.d)
    else:
        a = Rect(rect.x, rect.z, rect.w, cut)
        gap_rect = Rect(rect.x, rect.z + cut, rect.w, gap)
        b = Rect(rect.x, rect.z + cut + gap, rect.w, rect.d - cut - gap)
    return a, b, gap_rect


def _subdivide_blocks(
    rect: Rect, rng: random.Random, params: CityParams, depth: int = 0
) -> tuple[list[Rect], list[tuple[Rect, bool]]]:
    """Recursively split into blocks, collecting the streets carved between them."""
    # Wider streets near the centre of the recursion read as arterial roads.
    major = depth < 2
    gap = params.major_street_width if major else params.street_width

    too_small = rect.w < params.min_block * 2 and rect.d < params.min_block * 2
    if too_small or depth > 7:
        return [rect], []

    result = _split(rect, rng, gap, params.min_block)
    if result is None:
        return [rect], []
    a, b, street = result

    blocks_a, streets_a = _subdivide_blocks(a, rng, params, depth + 1)
    blocks_b, streets_b = _subdivide_blocks(b, rng, params, depth + 1)
    return blocks_a + blocks_b, [(street, major)] + streets_a + streets_b


def _subdivide_plots(rect: Rect, rng: random.Random, params: CityParams, depth: int = 0) -> list[Rect]:
    """Split a block into building plots. No streets -- plots share lot lines."""
    if depth > 6 or (rect.w < params.min_plot * 2 and rect.d < params.min_plot * 2):
        return [rect]
    result = _split(rect, rng, 0, params.min_plot)
    if result is None:
        return [rect]
    a, b, _ = result
    return _subdivide_plots(a, rng, params, depth + 1) + _subdivide_plots(b, rng, params, depth + 1)


def _district_for(rect: Rect, city_rect: Rect, rng: random.Random) -> str:
    """Assign a district kind by normalised distance from the city centre."""
    cx, cz = city_rect.center
    bx, bz = rect.center
    half_w = max(city_rect.w / 2, 1)
    half_d = max(city_rect.d / 2, 1)
    # Chebyshev-style normalised radius keeps bands rectangular, matching the
    # rectangular street grid better than a circular falloff would.
    radius = max(abs(bx - cx) / half_w, abs(bz - cz) / half_d)
    for limit, kinds in _RADIAL_BANDS:
        if radius <= limit:
            return rng.choice(kinds)
    return "residential"


def _weighted_choice(options: list[tuple[str, int]], rng: random.Random) -> str:
    total = sum(w for _, w in options)
    roll = rng.randrange(total)
    upto = 0
    for name, weight in options:
        upto += weight
        if roll < upto:
            return name
    return options[-1][0]


def _entrance_side(plot: Rect, streets: list[Street]) -> str:
    """Face the entrance toward the nearest street."""
    cx, cz = plot.center
    best, best_dist = "s", float("inf")
    for s in streets:
        sx, sz = s.rect.center
        dx, dz = sx - cx, sz - cz
        dist = abs(dx) + abs(dz)
        if dist < best_dist:
            best_dist = dist
            if abs(dx) > abs(dz):
                best = "e" if dx > 0 else "w"
            else:
                best = "s" if dz > 0 else "n"
    return best


def generate(params: CityParams | None = None, seed: int = 0) -> City:
    """Generate a city. The same ``seed`` and ``params`` always give the same city."""
    params = params or CityParams()
    size = params.resolved_size()
    if size < 24:
        raise ValueError(f"city size {size} is too small to subdivide; use at least 24 tiles")

    rng = random.Random(seed)
    name = params.name or __import__("citysmith.names", fromlist=["names"]).city_name(rng)

    city = City(
        name=name, seed=seed, style=params.style,
        width=size, depth=size, walled=params.walled,
    )

    full = Rect(0, 0, size, size)
    if params.walled:
        # Reserve a ring for the wall plus a clear street just inside it.
        city.wall_rect = full
        interior = full.inset(4)
    else:
        interior = full.inset(2)

    blocks, streets = _subdivide_blocks(interior, rng, params)

    from . import names as _names

    used_street_names: set[str] = set()
    for street_rect, major in streets:
        for _ in range(12):
            sname = _names.street_name(rng)
            if sname not in used_street_names:
                break
        used_street_names.add(sname)
        city.streets.append(Street(sname, street_rect, major))

    # Districts, one per block, then buildings within each block's plots.
    counter = 0
    district_counts: dict[str, int] = {}
    for block in blocks:
        kind = _district_for(block, full, rng)
        district_counts[kind] = district_counts.get(kind, 0) + 1
        dname = f"{kind.title()} District {district_counts[kind]}"
        city.districts.append(District(dname, kind, block))

        for plot in _subdivide_plots(block, rng, params):
            build_rect = plot.inset(params.yard)
            if build_rect.w < 3 or build_rect.d < 3:
                continue
            bkind = _weighted_choice(_DISTRICT_BUILDINGS[kind], rng)
            counter += 1
            floors = 1
            if build_rect.area >= 30:
                floors = rng.randint(1, params.max_floors)
            elif build_rect.area >= 16:
                floors = rng.randint(1, max(1, params.max_floors - 1))
            city.buildings.append(
                Building(
                    id=f"{bkind}-{counter:03d}",
                    name=_names.building_name(rng, bkind),
                    kind=bkind,
                    district=dname,
                    rect=build_rect,
                    floors=floors,
                    entrance=_entrance_side(plot, city.streets),
                    hook=_names.hook(rng) if rng.random() < 0.25 else "",
                    owner=_names.person_name(rng) if rng.random() < 0.5 else "",
                )
            )

    if params.walled:
        city.gates = _place_gates(city, full, rng)
    return city


def _place_gates(city: City, full: Rect, rng: random.Random) -> list[tuple[int, int]]:
    """Put a gate on each wall side, aligned with the nearest major street."""
    gates: list[tuple[int, int]] = []
    majors = [s for s in city.streets if s.major] or city.streets
    if not majors:
        return gates

    mid_x, mid_z = full.center
    # North and south gates align with a vertical street; east/west with a horizontal one.
    vertical = [s for s in majors if s.rect.d >= s.rect.w]
    horizontal = [s for s in majors if s.rect.w > s.rect.d]

    if vertical:
        s = min(vertical, key=lambda s: abs(s.rect.center[0] - mid_x))
        gx = int(s.rect.center[0])
        gates.append((gx, full.z))
        gates.append((gx, full.z2 - 1))
    if horizontal:
        s = min(horizontal, key=lambda s: abs(s.rect.center[1] - mid_z))
        gz = int(s.rect.center[1])
        gates.append((full.x, gz))
        gates.append((full.x2 - 1, gz))
    return gates
