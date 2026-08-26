"""Where the people are standing when the party walks in.

A town board has always been empty of people. `interior.occupants` derives
*who* is in each building -- the keeper, the staff, the visitors, each with a
name and something they are doing -- but nothing ever said *where*, so the
roster only existed inside a scene and the town itself was a stage set.

**A v2 slab carries no creatures.** `creatureCount` is always 0 in what we
emit, which is the same constraint that makes a scene paste four party marks
and leave the minis to the GM. So a "position" here is two things: a row in a
manifest, which is the durable artifact, and a contrasting floor tile on the
board, which is where the GM drops the mini.

Three populations, and they are different problems:

``GUARD``
    Posted, not resident. A guard belongs where the town is entered or
    watched: flanking a gate passage, and at intervals along the main street.
    All of that depends on `ftg.gates_from_roads`, which is why that landed
    first -- before it, `layout.gates` was empty on every FTG town and there
    was nowhere to post anyone.

``WORKING``
    At their own trade. `interior.occupants` already deals a keeper and staff
    per building from the type, the authored name and the footprint, so the
    people exist; what was missing is that a smith at work is *at the forge*,
    which on the board means their own doorway or their own yard.

``OFF_DUTY``
    Defined by *not* being at their day job -- and that is why the two cannot
    be allocated building by building. Somebody has to leave their workshop
    and turn up on the green, so the roster is dealt first and then a share of
    it is moved to a destination: the plaza, or the frontage of a tavern or
    shop. A town with nowhere to go has no off-duty population, which is
    correct rather than a failure.

Placement obeys the rule the furnishing pass learned indoors: **nothing stands
in a doorway**. A slab has no physics, so a token dropped in a door is a door
that does not open, and here it would also be a guard standing inside the gate
he is supposed to be watching.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import interior as I
from . import layout as L
from . import raster as R

#: The three duties. `duty` is what the manifest reports and what picks the
#: mark, so these strings are part of the output format.
GUARD = "guard"
WORKING = "working"
OFF_DUTY = "off_duty"

#: Guards posted either side of a gate passage.
GUARDS_PER_GATE = 2

#: One guard per this many tiles of main street, **by settlement band**.
#:
#: A single rate is wrong here in a way that shows immediately: at one guard
#: per 60 tiles, Pelvesthollow's 728 main-street tiles came out with 12
#: watchmen against 10 people at work -- a 35-building hamlet with a garrison.
#: A hamlet has no watch at all; somebody's uncle looks out of a window. A
#: village has a constable. A walled town has a patrol, which is what its gates
#: and wall-walk are for.
MAIN_STREET_PER_GUARD: dict[str, int | None] = {
    "hamlet": None,
    "village": 220,
    "town": 70,
}

#: Share of a building's roster that is out rather than at work. Measured
#: against nothing -- there is no source for it -- so it is a stated choice, and
#: it is the one number here most worth arguing about on a board.
OFF_DUTY_SHARE = 0.25

#: Trades whose work is visibly *outside* the building, so their people belong
#: on the board rather than in the interior.
#:
#: **This gate is the whole difference between a populated town and confetti.**
#: `interior.occupants` deals the entire household -- keeper, staff, visitors --
#: which is right for a scene, where the party is standing in the room. Marking
#: all of them outdoors put 115 posts on a 35-building hamlet and 3,927 on East
#: Tradebourne: every family in town standing in its own front garden at once.
#: A smith works at the forge and a stablehand in the yard; a householder is
#: indoors, and the scene pipeline is what opens that door.
OUTDOOR_TRADES = frozenset({
    "smithy", "stable", "warehouse", "market", "bakery", "shop", "tavern",
})

#: Chance an ordinary house puts somebody outside anyway -- hanging washing,
#: splitting wood, watching the street. Low, because the point of the gate
#: above is that most of them are indoors.
HOUSE_OUTDOOR_CHANCE = 0.15

#: A second person joins the first at a trade this many tiles or larger. One
#: figure outside a big warehouse reads as an empty warehouse.
BUSY_TRADE_TILES = 90

#: Chance any given household has somebody out about the town rather than at
#: home or at work. Drawn from *every* household, including the ones already
#: represented outdoors -- otherwise the only people on the green would be the
#: ones who were at work anyway, which is the opposite of what off-duty means.
OUT_AND_ABOUT_CHANCE = 0.12

#: Two posts closer than this are one crowd. Keeps a market square from
#: becoming a solid block of marks.
MIN_SPACING = 2

#: Buildings whose frontage is somewhere to stand around.
SOCIAL_KINDS = frozenset({"tavern", "inn", "shop", "market", "temple", "bakery"})


@dataclass
class Post:
    """One person, and where they are standing."""

    x: int
    z: int
    duty: str
    name: str
    role: str
    doing: str = ""
    #: The building they belong to. A guard belongs to none.
    building: str = ""
    #: Plain English for the manifest: "at the north gate", "in their own yard".
    where: str = ""

    def describe(self) -> str:
        doing = f", {self.doing}" if self.doing else ""
        return f"{self.name} ({self.role}) {self.where}{doing}"


@dataclass
class Population:
    posts: list[Post] = field(default_factory=list)

    def of(self, duty: str) -> list[Post]:
        return [p for p in self.posts if p.duty == duty]

    def summary(self) -> str:
        return (f"{len(self.of(GUARD))} guard(s), "
                f"{len(self.of(WORKING))} working, "
                f"{len(self.of(OFF_DUTY))} off duty")


def _door_cells(tm) -> set[tuple[int, int]]:
    """Every doorway and the cell it opens onto.

    Both, because a mark in the cell a door swings into blocks it just as
    surely as one in the threshold -- the lesson `build._door_keepout` learned
    indoors, which applies to a street door too.
    """
    out: set[tuple[int, int]] = set()
    for cells in tm.doors.values():
        for x, z, side in cells:
            out.add((x, z))
            dx, dz = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}.get(side, (0, 0))
            out.add((x + dx, z + dz))
    return out


def _standable(tm, x: int, z: int) -> bool:
    """Somewhere a person could actually be: open, walkable, not water.

    The marsh clause is belt-and-braces and is unreachable today: a fen is
    not in `R.OPEN`, and `is_walkable` gates on `OPEN`, so the guard above
    has already refused every wetland cell before this line runs. It is kept
    because the two facts are declared in different modules -- the day a fen
    becomes public open space for some routing reason, the villagers should
    not silently move into it knee-deep in the reeds.
    """
    if not tm.inside(x, z) or not tm.is_walkable(x, z):
        return False
    return (tm.surface[z][x] not in (R.WATER, R.MARSH)
            and not tm.building[z][x])


def posts(tm, layout, *, seed: int = 0, hour: str = "day",
          roster: dict | None = None, budget: int | None = None) -> Population:
    """Everyone on the board, and where they stand.

    ``budget`` caps the total. A thousand marks on East Tradebourne is a
    quarter of a percent of the asset limit and costs nothing, but it reads as
    confetti from above; the caller passes a number it has looked at.
    """
    rng = random.Random(f"{seed}:npcs")
    taken: set[tuple[int, int]] = set()
    blocked = _door_cells(tm)
    out: list[Post] = []

    def claim(x: int, z: int) -> bool:
        """Take a cell if it is free, standable and clear of doors and others."""
        if (x, z) in blocked or not _standable(tm, x, z):
            return False
        for dz in range(-MIN_SPACING, MIN_SPACING + 1):
            for dx in range(-MIN_SPACING, MIN_SPACING + 1):
                if (x + dx, z + dz) in taken:
                    return False
        taken.add((x, z))
        return True

    band = L.settlement_band(len(layout.buildings))
    out += _guards(tm, rng, claim, band)
    working, idle = _townsfolk(tm, layout, rng, seed, hour, roster)
    out += _place_working(tm, working, rng, claim)
    out += _place_off_duty(tm, idle, rng, claim)

    if budget is not None and len(out) > budget:
        # Trim the least load-bearing first: an off-duty drinker is scenery, a
        # gate guard is the reason the party stops.
        order = {GUARD: 0, WORKING: 1, OFF_DUTY: 2}
        out.sort(key=lambda p: (order[p.duty], p.z, p.x))
        out = out[:budget]
    return Population(sorted(out, key=lambda p: (p.z, p.x)))


# -- guards -------------------------------------------------------------------

def _guards(tm, rng, claim, band: str) -> list[Post]:
    """Gate posts first, then a patrol along the main street.

    An unwalled settlement has no gates -- Pelvesthollow and Graybank export
    zero wall rings -- so it gets the street patrol and nothing else, and a
    hamlet does not even get that.
    """
    from . import names

    found: list[Post] = []

    for gx, gz in sorted(tm.gates):
        posted = 0
        # Work outward from the passage so a guard stands beside it, never in
        # it: a mark in the carriageway is a guard standing in the gateway.
        for r in (2, 3, 4):
            for dx, dz in ((r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, -r), (r, -r), (-r, r)):
                if posted >= GUARDS_PER_GATE:
                    break
                x, z = gx + dx, gz + dz
                if (x, z) in tm.gates or not claim(x, z):
                    continue
                found.append(Post(
                    x, z, GUARD, names.person_name(rng), "gate guard",
                    doing=rng.choice(_GUARD_DOING), where="at the gate",
                ))
                posted += 1
            if posted >= GUARDS_PER_GATE:
                break

    # A patrol along the through road. Sampled at a stride rather than dealt at
    # random, so the spacing reads as a beat rather than as a crowd.
    stride = MAIN_STREET_PER_GUARD.get(band)
    if stride is None:
        return found
    main = [(x, z) for z in range(tm.depth) for x in range(tm.width)
            if tm.street_class[z][x] == R.MAIN_ROAD and _standable(tm, x, z)]
    for i in range(0, len(main), stride):
        x, z = main[i]
        if claim(x, z):
            found.append(Post(
                x, z, GUARD, names.person_name(rng), "watchman",
                doing=rng.choice(_GUARD_DOING), where="on the main street",
            ))
    return found


_GUARD_DOING = [
    "bored, and looking for a reason not to be",
    "checking a cart that has already been checked",
    "warming their hands",
    "watching the road, properly",
    "arguing with the other one about the rota",
    "asleep standing up, and will deny it",
]

#: **`interior._DOING` is written for a room and cannot be reused out here.**
#: It deals "asleep in a chair" and "writing, and covering it when anyone
#: passes", which read fine across a tavern table and read as nonsense for
#: somebody standing in a lane. Outdoors needs its own list, and it splits by
#: duty because the whole point of the two populations is that they are doing
#: different things.
_WORK_DOING = [
    "carrying something heavy and not enjoying it",
    "haggling, badly",
    "stacking, unstacking, and stacking again",
    "shouting at someone out of sight",
    "mending a thing that will break again",
    "counting stock and losing count",
    "scrubbing something down",
    "loading a cart that is already full",
    "watching the road for a delivery",
    "taking a break they have not earned",
]

_IDLE_DOING = [
    "watching the world go past",
    "waiting for somebody who is late",
    "eating, and in no hurry",
    "telling a story that is getting longer",
    "avoiding somebody",
    "drunk, cheerfully",
    "sitting where they were told not to",
    "listening to an argument that is not theirs",
    "looking for work, quietly",
    "up to something",
]


# -- everyone else ------------------------------------------------------------

def _townsfolk(tm, layout, rng, seed, hour, roster):
    """Deal the roster, then split it into who is at work and who is not.

    The split happens here rather than per building because an off-duty person
    has to *go* somewhere -- they are defined by being away from their own
    trade, so the two lists cannot be built independently.
    """
    on_board = {b for row in tm.building for b in row if b}
    working: list[tuple[str, I.Occupant]] = []
    idle: list[tuple[str, I.Occupant]] = []

    for building in layout.buildings:
        if building.id not in on_board:
            continue
        people = I.occupants(building, seed=seed, roster=roster, hour=hour)
        if not people:
            continue

        # How many of this household are OUTSIDE. See `OUTDOOR_TRADES`: the
        # rest are indoors, which is what the scene pipeline is for.
        area = building.extent[0] * building.extent[1]
        if building.kind in OUTDOOR_TRADES:
            heads = 2 if area >= BUSY_TRADE_TILES else 1
        elif rng.random() < HOUSE_OUTDOOR_CHANCE:
            heads = 1
        else:
            continue
        for i, person in enumerate(people[:min(heads, len(people))]):
            # The keeper is never off duty. A tavern whose landlord is out is a
            # shut tavern, and that is a story rather than a default.
            if i > 0 and rng.random() < OFF_DUTY_SHARE:
                idle.append((building.id, person))
            else:
                working.append((building.id, person))

    # The off-duty population is drawn from EVERY household, not only from the
    # ones already standing outside -- otherwise the only people on the green
    # are the ones who were at work anyway, which is the opposite of the idea.
    # Drawn last so it cannot compete with a trade for its own frontage.
    for building in layout.buildings:
        if building.id not in on_board or rng.random() >= OUT_AND_ABOUT_CHANCE:
            continue
        people = I.occupants(building, seed=seed, roster=roster, hour=hour)
        if not people:
            continue
        # Anyone but the keeper of an open trade -- a shop whose shopkeeper is
        # on the green is a shut shop. A household of one CAN go out, though,
        # and requiring two is what left a hamlet of small cottages with
        # nobody on its own square.
        pool = people[1:] if building.kind in OUTDOOR_TRADES else people
        if pool:
            idle.append((building.id, pool[rng.randrange(len(pool))]))
    return working, idle


def _building_cells(tm) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for z in range(tm.depth):
        for x in range(tm.width):
            bid = tm.building[z][x]
            if bid:
                out.setdefault(bid, []).append((x, z))
    return out


def _place_working(tm, working, rng, claim) -> list[Post]:
    """At their own trade: their yard if they have one, else their frontage."""
    from .build import yard_cells

    yards = yard_cells(tm)
    cells = _building_cells(tm)
    found: list[Post] = []

    for bid, person in working:
        spots: list[tuple[int, int]] = []
        # A yard is worked ground and the best place to be seen working.
        spots += sorted(yards.get(bid, ()))
        # Otherwise the ground just outside the shell -- their own frontage.
        own = cells.get(bid, [])
        if own:
            x0 = min(c[0] for c in own) - 1
            x1 = max(c[0] for c in own) + 1
            z0 = min(c[1] for c in own) - 1
            z1 = max(c[1] for c in own) + 1
            spots += [(x, z) for x in range(x0, x1 + 1) for z in (z0, z1)]
            spots += [(x, z) for z in range(z0, z1 + 1) for x in (x0, x1)]

        rng.shuffle(spots)
        for x, z in spots:
            if claim(x, z):
                found.append(Post(
                    x, z, WORKING, person.name, person.role,
                    doing=rng.choice(_WORK_DOING), building=bid,
                    where="in their own yard" if (x, z) in yards.get(bid, ())
                          else "outside their own door",
                ))
                break
    return found


def _place_off_duty(tm, idle, rng, claim) -> list[Post]:
    """Somewhere that is *not* their own trade.

    Destinations, in order of how much a person would rather be there: the
    plaza, then the frontage of a tavern, shop or temple, then any lane. A town
    with none of those has no off-duty population -- which is a fact about the
    town, not a bug, and the manifest says so.
    """
    cells = _building_cells(tm)
    social: list[tuple[int, int]] = []

    plaza = [(x, z) for z in range(tm.depth) for x in range(tm.width)
             if tm.surface[z][x] == R.PLAZA and _standable(tm, x, z)]
    social += plaza

    for bid, own in sorted(cells.items()):
        if bid.split("-")[0] not in SOCIAL_KINDS:
            continue
        x0 = min(c[0] for c in own) - 1
        x1 = max(c[0] for c in own) + 1
        z0 = min(c[1] for c in own) - 1
        z1 = max(c[1] for c in own) + 1
        social += [(x, z) for x in range(x0, x1 + 1) for z in (z0, z1)]
        social += [(x, z) for z in range(z0, z1 + 1) for x in (x0, x1)]

    social = [c for c in social if _standable(tm, *c)]
    if not social:
        return []

    rng.shuffle(social)
    found: list[Post] = []
    i = 0
    for bid, person in idle:
        while i < len(social):
            x, z = social[i]
            i += 1
            if claim(x, z):
                found.append(Post(
                    x, z, OFF_DUTY, person.name, person.role,
                    doing=rng.choice(_IDLE_DOING), building=bid,
                    where="on the square" if (x, z) in plaza else "out, not at work",
                ))
                break
        else:
            break                     # nowhere left to stand
    return found


def manifest(pop: Population) -> dict:
    """The durable artifact. A slab carries no creatures; this is the roster a
    GM reads while dropping minis on the marks."""
    return {
        "summary": pop.summary(),
        "posts": [
            {
                "x": p.x, "z": p.z, "duty": p.duty, "name": p.name,
                "role": p.role, "doing": p.doing,
                "building": p.building, "where": p.where,
            }
            for p in pop.posts
        ],
    }
