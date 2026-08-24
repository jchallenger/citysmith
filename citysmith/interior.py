"""One building out of an imported town, as somewhere the party can walk in.

The town pipeline builds a whole settlement at 5 ft a tile: every building is a
shell with a doorway, and the inside is solid. This module is the other
direction -- take *one* of those buildings and produce the playable interior,
with rooms, doorways and the people who are in it when the door opens.

Three things have to be invented, and it is worth being exact about which:

- **The plan.** FTG and MFCG both export footprints, never room layouts. The
  rooms come from :mod:`citysmith.floorplan`, which is the same BSP the city
  generator uses for plots.

- **The occupants.** *The export does not contain any.* It was worth checking
  rather than assuming: across the three FTG exports on this machine the
  BUILDING features carry exactly ``id``, ``type``, ``name``, ``buildingType``
  and ``material`` and nothing else -- 1,007 of them in East Tradebourne, all
  five keys, no sixth. So a roster is *derived* here: the type says what work
  happens in the building, the authored name says who it belongs to
  ("The Halfling and the Fox" is a tavern with a keeper), and the footprint
  says how many people fit. It is deterministic from the seed, so the same
  building gives the same people every time you walk in -- which is the part
  that actually matters at the table.

  When real occupants *do* exist -- a GM's notes, a roster exported from
  somewhere else -- :func:`load_roster` reads them from a sidecar keyed on
  building id and they win. Nothing here overwrites an authored fact.

- **Which way the door faces.** The town raster works this out from what the
  building fronts onto; an interior board has no town around it, so
  :func:`entrance_side` reads the nearest road out of the layout instead.

The building's size is taken from its **oriented** extent rather than its
bounding box. A house at 40 degrees to the axis has a bounding box half again
as large as the house, and an interior built to it is a room with a house-shaped
hole in the middle.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import random
from dataclasses import dataclass

from . import names
from .city import Building, Rect
from .floorplan import Floorplan, generate as generate_floorplan
from .layout import Layout, LayoutBuilding, distance, nearest_on_polyline

#: Smallest interior we will build. Below this the BSP has nothing to split and
#: the result is a single room, which is fine -- but a 1x2 shed is not a scene.
MIN_INTERIOR = 3

#: Tiles between levels when they are laid out side by side. Two is enough to
#: read as a gap without being mistaken for a corridor.
LEVEL_GAP = 2


class InteriorError(LookupError):
    """No building matched, or too many did."""


# -- picking the building ------------------------------------------------------

def find(layout: Layout, ref: str) -> LayoutBuilding:
    """Resolve ``ref`` to one building: an id, a name, or ``kind:tavern``.

    Names are what a GM actually says ("the Halfling and the Fox"), so a
    case-insensitive substring counts -- but only when it matches once. FTG
    names six of Graybank's buildings "Farm", and silently returning the first
    is how the party ends up in the wrong barn.
    """
    ref = ref.strip()
    if not ref:
        raise InteriorError("no building given")

    for b in layout.buildings:
        if b.id == ref:
            return b

    if ref.lower().startswith("kind:"):
        kind = ref[5:].strip().lower()
        of_kind = [b for b in layout.buildings if b.kind == kind]
        if not of_kind:
            kinds = sorted({b.kind for b in layout.buildings})
            raise InteriorError(
                f"no {kind!r} in {layout.name}; it has: {', '.join(kinds)}"
            )
        # Biggest playable one: floor area is what makes a building worth
        # walking into, and the largest of a kind is usually the notable one.
        return max(of_kind, key=lambda b: b.extent[0] * b.extent[1])

    exact = [b for b in layout.buildings if b.name.lower() == ref.lower()]
    if len(exact) == 1:
        return exact[0]

    partial = [b for b in layout.buildings if ref.lower() in b.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        listed = ", ".join(f"{b.id} ({b.name})" for b in partial[:8])
        more = "" if len(partial) <= 8 else f" and {len(partial) - 8} more"
        raise InteriorError(
            f"{len(partial)} buildings match {ref!r}: {listed}{more}. "
            "Use the id."
        )
    raise InteriorError(
        f"nothing in {layout.name} matches {ref!r}. Try an id like "
        "'tavern-0014', an exact name, or 'kind:tavern'."
    )


def candidates(layout: Layout, kind: str = "", limit: int = 20) -> list[LayoutBuilding]:
    """Buildings worth walking into, biggest first -- for a `did you mean`."""
    pool = [b for b in layout.buildings if b.kind != "shed"]
    if kind:
        pool = [b for b in pool if b.kind == kind]
    pool.sort(key=lambda b: b.extent[0] * b.extent[1], reverse=True)
    return pool[:limit]


# -- the shape of it ----------------------------------------------------------

def interior_rect(building: LayoutBuilding) -> Rect:
    """The building's own dimensions in tiles, as an axis-aligned rect.

    From the *oriented* extent, not the bounding box: the town places this
    footprint at whatever angle its plot allows, and an interior board has no
    plot. What carries over is how big the building is, not how it was turned.
    """
    long_side, short_side = building.extent
    w = max(MIN_INTERIOR, int(round(long_side)))
    d = max(MIN_INTERIOR, int(round(short_side)))
    return Rect(0, 0, w, d)


def entrance_side(layout: Layout, building: LayoutBuilding) -> str:
    """Which side the front door is on: toward the nearest road.

    The town raster decides this by what the facade fronts onto, but that needs
    the whole map rasterised. For one building the nearest carriageway is the
    same answer for a fraction of the work, and where there is no road at all
    (an outlying farm) it falls back to facing the middle of the settlement.
    """
    cx, cz = building.centroid
    target: tuple[float, float] | None = None
    best = math.inf
    for road in layout.roads:
        if not road.points:
            continue
        # The nearest point on the carriageway, not the nearest vertex: a house
        # beside the middle of a straight run is nowhere near either end of it,
        # and taking the vertex faced the whole fixture town west.
        cand = nearest_on_polyline((cx, cz), road.points)
        d = distance((cx, cz), cand)
        if d < best:
            best, target = d, cand
    if target is None:
        target = (layout.width / 2, layout.depth / 2)

    dx, dz = target[0] - cx, target[1] - cz
    if abs(dx) >= abs(dz):
        return "e" if dx > 0 else "w"
    return "s" if dz > 0 else "n"


def as_building(
    layout: Layout,
    building: LayoutBuilding,
    *,
    max_levels: int = 2,
) -> Building:
    """The imported building as the :class:`~citysmith.city.Building` the
    floorplan generator takes.

    ``max_levels`` caps the storeys. It belongs here rather than at the shell
    for the same reason ``storeys_of`` owns the town's cap: three passes read
    the level count, and capping one of them leaves the others building for a
    storey that is not there.
    """
    levels = max(1, min(max_levels, building.floors))
    return Building(
        id=building.id,
        name=building.name or _fallback_name(building),
        kind=building.kind,
        district=building.district,
        rect=interior_rect(building),
        floors=levels,
        entrance=entrance_side(layout, building),
    )


def _fallback_name(building: LayoutBuilding) -> str:
    """MFCG exports geometry only, so half the towns have nameless buildings."""
    rng = random.Random(f"name:{building.id}")
    return names.building_name(rng, building.kind)


def plan(
    layout: Layout,
    building: LayoutBuilding,
    *,
    seed: int = 0,
    max_levels: int = 2,
    min_room: int = 3,
    spread: bool = True,
    gap: int = LEVEL_GAP,
) -> Floorplan:
    """The floorplan for one imported building.

    ``spread`` lays the levels **side by side** rather than stacking them, and
    it is on by default for the same reason the interior builder leaves the
    roof off: a storey you cannot see into is not a battle map. TaleSpire has
    no way to hide an upper floor, so a stacked three-storey inn is one visible
    attic and two rooms the camera has to be flown inside to use. Side by side,
    the whole building is one glance from overhead -- which is how a plan gets
    read at the table.
    """
    fp = generate_floorplan(
        as_building(layout, building, max_levels=max_levels),
        seed=seed,
        min_room=min_room,
    )
    return spread_levels(fp, gap) if spread and fp.levels > 1 else fp


def translate(fp: Floorplan, dx: int, dz: int) -> Floorplan:
    """Move a whole plan, every level of it, by ``(dx, dz)`` tiles.

    Used to hold a scene off the origin by the width of its apron, so nothing
    in it has a negative coordinate. A slab cannot store one; the encoder
    shifts the whole board to fix it, which works but means the tile numbers in
    the manifest stop matching the tile numbers on the board.
    """
    for room in fp.rooms:
        r = room.rect
        room.rect = Rect(r.x + dx, r.z + dz, r.w, r.d)
    for door in fp.doors:
        door.x += dx
        door.z += dz
    for stair in fp.stairs:
        stair.x += dx
        stair.z += dz
    fp.rect = Rect(fp.rect.x + dx, fp.rect.z + dz, fp.rect.w, fp.rect.d)
    return fp


def spread_levels(fp: Floorplan, gap: int = LEVEL_GAP) -> Floorplan:
    """Translate each level sideways so they sit in a row, not a stack.

    Every level keeps its own geometry exactly; only ``x`` moves. The builder
    reads each level's own bounding rect, so nothing downstream needs to know
    this happened.
    """
    step = fp.rect.w + gap
    for room in fp.rooms:
        if room.level:
            r = room.rect
            room.rect = Rect(r.x + room.level * step, r.z, r.w, r.d)
    for door in fp.doors:
        if door.level:
            door.x += door.level * step
    for stair in fp.stairs:
        # A stair connects two levels that are now yards apart. It stays where
        # it is on the level it rises *from*; the interior builder puts a
        # matching one on the level it arrives at, which is what makes the pair
        # readable as "this is the way up".
        stair.x += stair.from_level * step
    return fp


# -- who is in it -------------------------------------------------------------

@dataclass
class Occupant:
    """Someone in the building when the door opens."""

    name: str
    role: str
    #: What they are doing, so the GM has an opening line rather than a noun.
    doing: str = ""
    #: True when this came from an authored roster rather than being derived.
    authored: bool = False

    def describe(self) -> str:
        doing = f" -- {self.doing}" if self.doing else ""
        return f"{self.name}, {self.role}{doing}"


#: kind -> (the person in charge, the people who work there -- **each once**,
#: the people who are just here, and what anyone past both is). Counts are
#: scaled by footprint in :func:`occupants`.
#:
#: The three-way split is what stops a room reading as a bug: staff are dealt
#: once each, because a tavern has one cook, and the overflow role exists so
#: that a big house does not come out with two spouses.
_STAFF: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    "tavern": ("innkeeper", ("cook", "pot boy", "serving girl"),
               ("drover", "carter", "off-duty guard", "travelling pedlar",
                "farmhand", "local drunk"), "drinker"),
    "shop": ("shopkeeper", ("apprentice",),
             ("customer", "haggling neighbour"), "customer"),
    "smithy": ("smith", ("striker", "apprentice"),
               ("customer waiting on a repair",), "customer"),
    "warehouse": ("warehouse keeper", ("porter", "tally clerk"),
                  ("carter",), "porter"),
    "temple": ("priest", ("acolyte", "sexton"),
               ("penitent", "mourner"), "worshipper"),
    "guildhall": ("guild clerk", ("scribe", "steward"),
                  ("petitioner",), "petitioner"),
    "barracks": ("watch sergeant", ("watchman",),
                 ("prisoner in the back room",), "watchman"),
    "manor": ("householder", ("cook", "maid", "groom"), ("guest",), "servant"),
    "stable": ("ostler", ("stable hand",),
               ("traveller seeing to a horse",), "stable hand"),
    "apothecary": ("apothecary", ("assistant",), ("patient",), "patient"),
    "house": ("householder", ("spouse", "grown child", "child", "elderly parent"),
              (), "lodger"),
    "shed": ("", (), (), ""),
}

#: Roughly one person per this many tiles of floor, on top of whoever runs the
#: place. A tavern is busier than a cottage because a tavern's whole business
#: is having people in it.
_DENSITY: dict[str, int] = {
    "tavern": 8, "temple": 14, "guildhall": 14, "shop": 16, "smithy": 20,
    "warehouse": 24, "barracks": 12, "manor": 16, "stable": 24,
    "apothecary": 18, "house": 14,
}

MAX_OCCUPANTS = 12


def occupants(
    building: LayoutBuilding,
    *,
    seed: int = 0,
    roster: dict[str, list[dict]] | None = None,
    hour: str = "day",
) -> list[Occupant]:
    """Who is inside, derived from what the export *does* say.

    The export says the type and the name; those two decide the trade and who
    runs it. The footprint decides how many others fit. An authored ``roster``
    entry for this building id replaces the lot -- see :func:`load_roster`.
    """
    if roster and building.id in roster:
        return [
            Occupant(
                name=str(p.get("name", "someone")),
                role=str(p.get("role", "")),
                doing=str(p.get("doing", "")),
                authored=True,
            )
            for p in roster[building.id]
        ]

    rng = random.Random(f"{seed}:occupants:{building.id}")
    keeper_role, staff_roles, visitor_roles, overflow = _STAFF.get(
        building.kind, _STAFF["house"]
    )
    if not keeper_role:
        return []

    area = building.extent[0] * building.extent[1]
    per = _DENSITY.get(building.kind, 14)
    heads = max(1, min(MAX_OCCUPANTS, int(area // per) + 1))
    if hour == "night":
        heads = max(1, heads // 2)

    people = [Occupant(names.person_name(rng), keeper_role, _doing(rng, keeper_role))]
    for i in range(heads - 1):
        if i < len(staff_roles):
            role = staff_roles[i]          # one cook, one pot boy, one of each
        elif visitor_roles:
            role = rng.choice(visitor_roles)
        else:
            role = overflow or "resident"
        people.append(Occupant(names.person_name(rng), role, _doing(rng, role)))
    return people


_DOING = [
    "watching the door",
    "arguing about money",
    "half asleep",
    "counting something twice",
    "eating, and not sharing",
    "cleaning a thing that is already clean",
    "waiting for someone who is late",
    "pretending not to listen",
    "packing up to leave",
    "nursing an injury they will not explain",
    "writing, and covering it when anyone passes",
    "asleep in a chair",
]


def _doing(rng: random.Random, role: str) -> str:
    return rng.choice(_DOING)


def hook(building: LayoutBuilding, seed: int = 0) -> str:
    """One reason this room is worth playing in, stable for the building."""
    return names.hook(random.Random(f"{seed}:hook:{building.id}"))


# -- authored rosters ---------------------------------------------------------

ROSTER_VERSION = 1


def load_roster(path: str | os.PathLike[str]) -> dict[str, list[dict]]:
    """Read an authored occupant sidecar: ``{building id: [{name, role, ...}]}``.

    Kept separate from the layout on purpose. The layout is *imported* and gets
    overwritten on the next import; a roster is written by a person and must
    survive that. A missing file is not an error -- most towns have none.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "occupants" in data:
        data = data["occupants"]
    if not isinstance(data, dict):
        raise ValueError(
            f"{p}: expected an object keyed by building id, got {type(data).__name__}"
        )
    return {str(k): list(v) for k, v in data.items()}
