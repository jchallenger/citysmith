"""Interior floorplans for a single building.

A floorplan is the playable layer: rooms sized for a battle grid, doorways the
party can be blocked at, and stairs between levels. Rooms are produced by the
same BSP used for city plots, then doors are cut between rooms that share a
wall so the plan is always fully connected -- an interior with an unreachable
room is a bug at the table, not a feature.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
from dataclasses import asdict, dataclass, field

from .city import Building, Rect

FLOORPLAN_VERSION = 1

#: Room purposes offered per building kind, ground floor first.
_ROOM_MENU: dict[str, list[str]] = {
    "tavern": ["common room", "kitchen", "cellar stair", "store room", "snug", "bar"],
    "shop": ["shop floor", "counter", "store room", "workshop"],
    "smithy": ["forge", "workshop", "store room", "yard"],
    "warehouse": ["main store", "loading bay", "office", "store room"],
    "temple": ["nave", "shrine", "vestry", "store room"],
    "guildhall": ["hall", "meeting room", "office", "store room"],
    "barracks": ["bunk room", "armoury", "mess", "office"],
    "house": ["living room", "kitchen", "bedroom", "store room"],
    "manor": ["hall", "dining room", "study", "bedroom", "kitchen"],
    "stable": ["stalls", "tack room", "feed store"],
    "apothecary": ["shop floor", "workroom", "store room", "drying room"],
}

_UPPER_ROOMS = ["bedroom", "store room", "office", "landing", "private room"]


@dataclass
class Room:
    id: str
    name: str
    purpose: str
    rect: Rect
    level: int = 0


@dataclass
class Door:
    """A doorway on a cell edge.

    ``(x, z)`` is the cell the door belongs to and ``side`` the edge it cuts.
    """

    x: int
    z: int
    side: str
    level: int = 0
    exterior: bool = False


@dataclass
class Stair:
    x: int
    z: int
    from_level: int
    to_level: int


@dataclass
class Floorplan:
    building_id: str
    name: str
    kind: str
    rect: Rect
    levels: int
    rooms: list[Room] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)
    stairs: list[Stair] = field(default_factory=list)

    def rooms_on(self, level: int) -> list[Room]:
        return [r for r in self.rooms if r.level == level]

    def to_dict(self) -> dict:
        return {
            "floorplan_version": FLOORPLAN_VERSION,
            "building_id": self.building_id,
            "name": self.name,
            "kind": self.kind,
            "rect": asdict(self.rect),
            "levels": self.levels,
            "rooms": [
                {"id": r.id, "name": r.name, "purpose": r.purpose,
                 "rect": asdict(r.rect), "level": r.level}
                for r in self.rooms
            ],
            "doors": [asdict(d) for d in self.doors],
            "stairs": [asdict(s) for s in self.stairs],
        }

    def save(self, path: str | os.PathLike[str]) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Floorplan":
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if d.get("floorplan_version") != FLOORPLAN_VERSION:
            raise ValueError("floorplan file version mismatch; regenerate it")
        fp = cls(
            building_id=d["building_id"], name=d["name"], kind=d["kind"],
            rect=Rect(**d["rect"]), levels=d["levels"],
        )
        fp.rooms = [
            Room(r["id"], r["name"], r["purpose"], Rect(**r["rect"]), r["level"])
            for r in d["rooms"]
        ]
        fp.doors = [Door(**x) for x in d["doors"]]
        fp.stairs = [Stair(**x) for x in d["stairs"]]
        return fp

    def summary(self) -> str:
        per_level = [len(self.rooms_on(i)) for i in range(self.levels)]
        return (
            f"{self.name} ({self.kind}) -- {self.rect.w}x{self.rect.d} tiles, "
            f"{self.levels} level(s), rooms per level {per_level}, "
            f"{len(self.doors)} doors, {len(self.stairs)} stairs"
        )


def _split_rooms(rect: Rect, rng: random.Random, min_room: int, depth: int = 0) -> list[Rect]:
    """BSP a level into rooms sharing walls."""
    if depth > 5:
        return [rect]
    horizontal = rect.w >= rect.d
    length = rect.w if horizontal else rect.d
    if length < min_room * 2:
        return [rect]
    cut = rng.randint(min_room, length - min_room)
    if horizontal:
        a = Rect(rect.x, rect.z, cut, rect.d)
        b = Rect(rect.x + cut, rect.z, rect.w - cut, rect.d)
    else:
        a = Rect(rect.x, rect.z, rect.w, cut)
        b = Rect(rect.x, rect.z + cut, rect.w, rect.d - cut)
    return _split_rooms(a, rng, min_room, depth + 1) + _split_rooms(b, rng, min_room, depth + 1)


def _shared_edge(a: Rect, b: Rect) -> tuple[int, int, str] | None:
    """Find a cell on ``a`` whose edge touches ``b``; returns ``(x, z, side)``."""
    # b directly east of a
    if a.x2 == b.x:
        lo, hi = max(a.z, b.z), min(a.z2, b.z2)
        if hi > lo:
            return a.x2 - 1, (lo + hi) // 2, "e"
    if b.x2 == a.x:
        lo, hi = max(a.z, b.z), min(a.z2, b.z2)
        if hi > lo:
            return a.x, (lo + hi) // 2, "w"
    if a.z2 == b.z:
        lo, hi = max(a.x, b.x), min(a.x2, b.x2)
        if hi > lo:
            return (lo + hi) // 2, a.z2 - 1, "s"
    if b.z2 == a.z:
        lo, hi = max(a.x, b.x), min(a.x2, b.x2)
        if hi > lo:
            return (lo + hi) // 2, a.z, "n"
    return None


def _connect(rooms: list[Room], level: int, rng: random.Random) -> list[Door]:
    """Door the rooms into one connected component, then add a few loops.

    Runs a spanning-tree pass first so connectivity is guaranteed, then adds
    optional extra doors for tactical alternatives.
    """
    if len(rooms) < 2:
        return []

    doors: list[Door] = []
    connected = {rooms[0].id}
    remaining = {r.id: r for r in rooms[1:]}

    while remaining:
        best: tuple[Room, Room, tuple[int, int, str]] | None = None
        for r in rooms:
            if r.id not in connected:
                continue
            for other in list(remaining.values()):
                edge = _shared_edge(r.rect, other.rect)
                if edge is not None:
                    best = (r, other, edge)
                    break
            if best:
                break
        if best is None:
            # No remaining room touches the connected set. Cannot happen with a
            # BSP layout, but bail rather than loop forever if it ever does.
            break
        _r, other, (dx, dz, side) = best
        doors.append(Door(dx, dz, side, level))
        connected.add(other.id)
        remaining.pop(other.id, None)

    # Extra doorways for flanking routes.
    for a in rooms:
        for b in rooms:
            if a.id >= b.id:
                continue
            if rng.random() > 0.18:
                continue
            edge = _shared_edge(a.rect, b.rect)
            if edge is None:
                continue
            dx, dz, side = edge
            if any(d.x == dx and d.z == dz and d.side == side for d in doors):
                continue
            doors.append(Door(dx, dz, side, level))
    return doors


def generate(
    building: Building,
    seed: int = 0,
    *,
    min_room: int = 3,
    levels: int | None = None,
) -> Floorplan:
    """Generate a floorplan for ``building``."""
    rng = random.Random(f"{seed}:{building.id}")
    rect = building.rect
    n_levels = levels if levels is not None else max(1, building.floors)

    fp = Floorplan(
        building_id=building.id, name=building.name, kind=building.kind,
        rect=rect, levels=n_levels,
    )

    menu = _ROOM_MENU.get(building.kind, _ROOM_MENU["house"])
    counter = 0
    for level in range(n_levels):
        # Interior space is inset by the wall thickness in the builder, not here;
        # rooms tile the full footprint so shared walls line up on cell edges.
        rects = _split_rooms(rect, rng, min_room)
        purposes = list(menu if level == 0 else _UPPER_ROOMS)
        rng.shuffle(purposes)
        for i, r in enumerate(rects):
            counter += 1
            purpose = purposes[i % len(purposes)]
            fp.rooms.append(
                Room(
                    id=f"r{counter:03d}", name=purpose.title(), purpose=purpose,
                    rect=r, level=level,
                )
            )
        fp.doors.extend(_connect(fp.rooms_on(level), level, rng))

    # Exterior entrance on the side the city generator faced the building.
    side = building.entrance
    if side == "n":
        ex, ez = rect.x + rect.w // 2, rect.z
    elif side == "s":
        ex, ez = rect.x + rect.w // 2, rect.z2 - 1
    elif side == "w":
        ex, ez = rect.x, rect.z + rect.d // 2
    else:
        ex, ez = rect.x2 - 1, rect.z + rect.d // 2
    fp.doors.append(Door(ex, ez, side, 0, exterior=True))

    # Stairs: put them in the largest room on each level below the top.
    for level in range(n_levels - 1):
        rooms = fp.rooms_on(level)
        if not rooms:
            continue
        room = max(rooms, key=lambda r: r.rect.area)
        sx = room.rect.x + room.rect.w // 2
        sz = room.rect.z + room.rect.d // 2
        fp.stairs.append(Stair(sx, sz, level, level + 1))

    return fp
