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
import re
import pathlib
import random
from dataclasses import asdict, dataclass, field

from .city import Building, Rect

FLOORPLAN_VERSION = 1

#: Room purposes offered per building kind, ground floor first.
#:
#: **Long enough for a large plan.** A hall plan on a 435-tile warehouse asks
#: for ten service rooms, and a four-name menu answered with three Vestries and
#: two Shrines -- numbering that reads as a bug rather than as a building.
#: Repeats are still allowed where they are real (`Bay 2` in a warehouse is a
#: bay), but they should be the last resort and not the third room.
_ROOM_MENU: dict[str, list[str]] = {
    "tavern": ["common room", "kitchen", "bar", "snug", "cellar stair",
               "store room", "scullery", "tap room", "pantry", "back room"],
    "shop": ["shop floor", "counter", "store room", "workshop", "back office",
             "strong room", "packing room", "yard door"],
    "smithy": ["forge", "workshop", "store room", "yard", "bellows room",
               "charcoal store", "finishing room", "tool store"],
    "warehouse": ["main store", "loading bay", "office", "store room",
                  "tally room", "cold store", "rope store", "cooperage",
                  "cart bay", "counting room"],
    "temple": ["nave", "shrine", "vestry", "store room", "sacristy",
               "chapter room", "reliquary", "crypt stair", "almonry",
               "side chapel"],
    "guildhall": ["hall", "meeting room", "office", "store room",
                  "muniment room", "clerk's room", "strong room", "kitchen",
                  "waiting room", "master's room"],
    "barracks": ["bunk room", "armoury", "mess", "office", "guard room",
                 "cell", "kit store", "kitchen", "sergeant's room"],
    "house": ["living room", "kitchen", "bedroom", "store room", "scullery",
              "pantry", "back room", "wash house"],
    "manor": ["hall", "dining room", "study", "bedroom", "kitchen", "parlour",
              "pantry", "servants' hall", "muniment room", "gallery"],
    "stable": ["stalls", "tack room", "feed store", "harness room",
               "hay loft stair", "wash stall", "farrier's corner"],
    "apothecary": ["shop floor", "workroom", "store room", "drying room",
                   "still room", "herb store", "consulting room"],
}

_UPPER_ROOMS = ["bedroom", "store room", "office", "private room", "study",
                "guest room", "linen store", "box room", "sitting room",
                "servant's room"]


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

    def rect_on(self, level: int) -> Rect:
        """The footprint of one level, which is not always ``self.rect``.

        Levels tile the same footprint when they are stacked. They do not when
        they have been laid out side by side for play
        (:func:`citysmith.interior.spread_levels`), and every pass that asks
        "is this cell on the outside wall" has to ask it of the level's own
        rect or it walls the wrong edges.
        """
        rooms = self.rooms_on(level)
        if not rooms:
            return self.rect
        x0 = min(r.rect.x for r in rooms)
        z0 = min(r.rect.z for r in rooms)
        x1 = max(r.rect.x2 for r in rooms)
        z1 = max(r.rect.z2 for r in rooms)
        return Rect(x0, z0, x1 - x0, z1 - z0)

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


#: At or above this many tiles, a plan gets a hall whatever the building is.
#: Below it, a BSP is the right shape: three to six rooms sharing walls is what
#: a cottage looks like.
#:
#: **The number comes from what the BSP does above it.** Measured over East
#: Tradebourne's 991 buildings, rooms per level by footprint: 3.3 at under 50
#: tiles, 5.0 at 50-80, 6.5 at 80-120, 10.8 at 120-200 and **23.5 above 200**.
#: A 29x15 warehouse -- 145 x 75 ft -- came out as 31 rooms of about 15x25 ft
#: each, cycling four purpose names seven times, with no room bigger than any
#: other and 52 doorways between them. That is a honeycomb, not a building.
LARGE_TILES = 90

#: Kinds whose whole point is one big volume with service rooms off it. These
#: get a hall as soon as the footprint can afford one, rather than waiting for
#: :data:`LARGE_TILES` -- a tavern whose common room is one quarter of four
#: equal rooms is not a tavern.
HALL_KINDS = frozenset({
    "temple", "guildhall", "manor", "barracks", "warehouse", "tavern", "stable",
})

#: Smallest footprint that can carry a hall and anything either side of it.
HALL_MIN = 45

#: What the principal space is called, per kind. The hall is the room the
#: building exists for, and naming it is half of what makes a plan readable.
_PRINCIPAL: dict[str, str] = {
    "tavern": "common room", "temple": "nave", "guildhall": "great hall",
    "manor": "great hall", "barracks": "muster hall", "warehouse": "loading floor",
    "stable": "stalls", "shop": "shop floor", "smithy": "forge",
    "apothecary": "shop floor", "house": "hall",
}

#: What the hall becomes on an upper floor: a corridor with rooms off it, which
#: is the shape of an inn's bedroom floor and of a guildhall's offices alike.
_UPPER_PRINCIPAL = "landing"

#: Rooms are cut to about this deep along the hall. Six tiles is 30 ft -- a
#: room a party can fight in, rather than the 15x25 ft cells the BSP produced.
ROOM_TARGET = 6

#: A corridor is three tiles: two creatures can pass, and it still reads as
#: circulation rather than as another room.
CORRIDOR = 3


def wants_hall(kind: str, rect: Rect) -> bool:
    """True when this building should be planned around a principal space."""
    if rect.area >= LARGE_TILES:
        return True
    return kind in HALL_KINDS and rect.area >= HALL_MIN


def _slice_run(start: int, length: int, min_room: int) -> list[tuple[int, int]]:
    """Cut ``length`` into rooms of about :data:`ROOM_TARGET`, none too small.

    Returns ``(offset, size)`` pairs that tile the run exactly -- the builder
    walls shared edges, so a gap between two rooms is a wall with nothing
    either side of it.
    """
    n = max(1, round(length / ROOM_TARGET))
    while n > 1 and length // n < min_room:
        n -= 1
    out = []
    at = start
    for i in range(n):
        size = length // n + (1 if i < length % n else 0)
        out.append((at, size))
        at += size
    return out


def hall_layout(rect: Rect, entrance: str, level: int,
                min_room: int) -> tuple[list[Rect], int]:
    """A principal space with rooms off it. Returns the rects and the hall's index.

    The hall always **touches the wall the door is in**, so the door opens onto
    it and everything else opens off it -- which is what makes a big plan
    legible from the threshold instead of being a maze of equal cells. Which
    shape it takes depends on the building's proportions, and getting that
    backwards is worth a paragraph:

    * **A nave**, running from the door into the depth of the building, with
      flanking strips either side and a band across the far end. This is the
      right form when the building is deeper than it is wide *from the door*.

    * **A broad hall**, spanning the full width against the entrance wall, with
      the back of the building cut into rooms behind it. This is the right form
      for a wide, shallow building -- and it is the one the first version got
      wrong: a 29x15 warehouse entered from its long side was given a 10x10
      nave, 23% of the floor, with 335 tiles of service rooms around it. A
      warehouse is a loading floor with bays at the back.

    On an upper level the hall narrows to a :data:`CORRIDOR`, which turns the
    same construction into a landing with rooms off it.
    """
    # `along` runs into the building from the entrance; `across` is the other.
    vertical = entrance in ("n", "s")
    along = rect.d if vertical else rect.w
    across = rect.w if vertical else rect.d
    if along < min_room * 2 or across < min_room:
        return [], -1

    def place(a_off: int, a_len: int, c_off: int, c_len: int) -> Rect:
        """Build a rect from (along, across) offsets in the building's frame."""
        if vertical:
            x, z, w, d = rect.x + c_off, rect.z + a_off, c_len, a_len
        else:
            x, z, w, d = rect.x + a_off, rect.z + c_off, a_len, c_len
        # `along` always counts from the door, so the far side mirrors.
        if entrance == "s":
            z = rect.z2 - (a_off + a_len)
        elif entrance == "e":
            x = rect.x2 - (a_off + a_len)
        return Rect(x, z, w, d)

    def broad() -> list[Rect]:
        """The hall as a band along the entrance wall, rooms behind it."""
        depth = CORRIDOR if level else max(min_room, round(along * 0.55))
        back = along - depth
        if back and back < min_room:          # no room for a back band
            return []
        out = [place(0, depth, 0, across)]
        if back:
            for c_off, c_len in _slice_run(0, across, min_room):
                out.append(place(depth, back, c_off, c_len))
        return out

    # -- broad: the hall is a band along the entrance wall -------------------
    if along < across * 0.8:
        rects = broad()
        if len(rects) > 1:
            return rects, 0
        return [], -1

    # -- nave: the hall runs in from the door -------------------------------
    band = CORRIDOR if level else max(min_room, round(across * 0.45))
    if across < band + min_room:
        return [], -1

    off = (across - band) // 2
    # A flank thinner than a room is absorbed into the hall rather than left as
    # a slot nothing fits in.
    if off < min_room:
        band, off = band + off, 0
    tail = across - off - band
    if 0 < tail < min_room:
        band, tail = band + tail, 0

    # A deep building keeps a band across the far end. Without it the flank
    # rooms run the whole depth and every one of them is a corridor.
    back = 0
    if along >= 14:
        back = max(min_room, ROOM_TARGET - 1)
    hall_run = along - back

    rects = [place(0, hall_run, off, band)]
    for c_off, c_len in ((0, off), (off + band, tail)):
        if c_len <= 0:
            continue
        for a_off, a_len in _slice_run(0, hall_run, min_room):
            rects.append(place(a_off, a_len, c_off, c_len))
    if back:
        for c_off, c_len in _slice_run(0, across, min_room):
            rects.append(place(hall_run, back, c_off, c_len))

    # **A hall that ate the whole building is not a plan.** On a small
    # footprint both flanks can be thinner than a room, so both are absorbed
    # and the nave becomes the entire rect: an 8x7 tavern came out as one
    # undivided room, a common room with no kitchen and no bar, and the GM
    # brief listed all eight occupants in the same place. Try the other form
    # before giving up -- across the entrance wall, an 8x7 is a common room
    # with a back room behind it, which is what a small tavern is.
    if len(rects) > 1:
        return rects, 0
    fallback = broad()
    return (fallback, 0) if len(fallback) > 1 else ([], -1)


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


def _title(text: str) -> str:
    """Title case that does not capitalise after an apostrophe.

    `str.title()` gives "Clerk'S Room" and "Servants' Hall" reads as a typo on
    the GM's page.
    """
    return re.sub(r"(?<![A-Za-z'])[a-z]", lambda m: m.group().upper(), text)


def _name_rooms(kind: str, menu: list[str], count: int, hall: int, level: int,
                rng: random.Random) -> list[str]:
    """One name per room, and **no repeats**.

    The old dealer took ``purposes[i % len(purposes)]``, so a 31-room warehouse
    floor had seven rooms called Office and seven called Loading Bay. A name
    that appears seven times names nothing; where the menu runs out, the rooms
    are numbered instead.
    """
    principal = ""
    if hall >= 0:
        principal = _UPPER_PRINCIPAL if level else _PRINCIPAL.get(kind, "hall")

    # **The principal name is spent on the hall and never again.** Cycling the
    # whole pool once the menu runs out put a second and third "Loading Floor"
    # among the service rooms of a warehouse that has exactly one.
    services = [p for p in (_UPPER_ROOMS if level else menu) if p != principal]
    rng.shuffle(services)
    if not services:
        services = ["room"]

    names: list[str] = []
    used: dict[str, int] = {}
    nxt = 0
    for i in range(count):
        if i == hall:
            names.append(principal)
            continue
        base = services[nxt % len(services)]
        nxt += 1
        used[base] = used.get(base, 0) + 1
        names.append(base if used[base] == 1 else f"{base} {used[base]}")
    return names


def _door_onto(rooms: list[Room], hall: Room, level: int) -> list[Door]:
    """A doorway from every room that touches the hall, into the hall.

    Anything that does not touch it -- a back-band room behind another -- is
    joined to its neighbour instead, so the plan stays fully connected.
    """
    doors: list[Door] = []
    reached = {hall.id}
    for room in rooms:
        if room.id == hall.id:
            continue
        edge = _shared_edge(room.rect, hall.rect)
        if edge is None:
            continue
        dx, dz, side = edge
        doors.append(Door(dx, dz, side, level))
        reached.add(room.id)

    # Whatever the hall could not reach, hang off something that it did.
    stranded = [r for r in rooms if r.id not in reached]
    for room in stranded:
        for other in rooms:
            if other.id == room.id or other.id not in reached:
                continue
            edge = _shared_edge(room.rect, other.rect)
            if edge is None:
                continue
            dx, dz, side = edge
            doors.append(Door(dx, dz, side, level))
            reached.add(room.id)
            break
    return doors


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
        hall = -1
        rects: list[Rect] = []
        if wants_hall(building.kind, rect):
            rects, hall = hall_layout(rect, building.entrance, level, min_room)
        if not rects:
            rects, hall = _split_rooms(rect, rng, min_room), -1

        purposes = _name_rooms(building.kind, menu, len(rects), hall, level, rng)
        for i, r in enumerate(rects):
            counter += 1
            purpose = purposes[i]
            fp.rooms.append(
                Room(
                    id=f"r{counter:03d}", name=_title(purpose), purpose=purpose,
                    rect=r, level=level,
                )
            )
        rooms = fp.rooms_on(level)
        if hall >= 0:
            # **Every room opens onto the hall**, which is what a hall is for.
            # The spanning tree would connect them in any order that happens to
            # share an edge, so a temple came out with 52 doorways and rooms
            # you reached through three other rooms.
            fp.doors.extend(_door_onto(rooms, rooms[hall], level))
        else:
            fp.doors.extend(_connect(rooms, level, rng))

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

    # Stairs go in the largest room -- the hall, where there is one -- at the
    # end away from the entrance, so a flight does not land in the doorway.
    for level in range(n_levels - 1):
        rooms = fp.rooms_on(level)
        if not rooms:
            continue
        room = max(rooms, key=lambda r: r.rect.area)
        sx = room.rect.x + room.rect.w // 2
        sz = room.rect.z + room.rect.d // 2
        if building.entrance == "n":
            sz = room.rect.z2 - 1
        elif building.entrance == "s":
            sz = room.rect.z
        elif building.entrance == "w":
            sx = room.rect.x2 - 1
        else:
            sx = room.rect.x
        fp.stairs.append(Stair(sx, sz, level, level + 1))

    return fp
