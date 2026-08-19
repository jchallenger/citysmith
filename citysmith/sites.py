"""Site selection -- the "find somewhere worth playing in" step.

Ranks a city's buildings by how good an encounter location each one would make,
and explains why. The score is intentionally transparent: every contribution is
a named reason, so a GM can disagree with the ranking and see exactly which
signal to override rather than being handed an opaque number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .city import Building, City, Rect

#: Building kinds that make interesting encounter sites, and why.
_KIND_SCORE: dict[str, tuple[float, str]] = {
    "tavern": (3.0, "taverns put strangers in one room"),
    "warehouse": (3.0, "open floor and stacked cover"),
    "temple": (2.5, "sightlines, alcoves and a reason to be quiet"),
    "guildhall": (2.5, "a place with something worth taking"),
    "smithy": (2.0, "hazards and improvised weapons"),
    "manor": (2.0, "rooms worth searching"),
    "barracks": (2.0, "armed opposition on site"),
    "apothecary": (1.5, "valuables in small packages"),
    "shop": (1.0, "counters and shelves for cover"),
    "stable": (1.0, "animals complicate a fight"),
    "house": (0.4, "domestic, low stakes"),
}

#: Districts that raise the stakes of whatever sits in them.
_DISTRICT_SCORE: dict[str, tuple[float, str]] = {
    "slums": (1.5, "in the slums, where the watch is slow"),
    "docks": (1.5, "on the docks, with routes in and out by water"),
    "market": (1.0, "in the market, busy enough to lose a tail"),
    "craft": (0.5, "in the craft quarter"),
    "civic": (1.0, "in the civic quarter, where being caught matters"),
    "temple": (0.5, "in the temple quarter"),
    "residential": (0.0, "in a residential street"),
}

#: A battle map wants roughly this many tiles per side to be playable.
IDEAL_MIN, IDEAL_MAX = 6, 14


@dataclass
class Site:
    """A ranked candidate location."""

    building: Building
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.building.id

    @property
    def name(self) -> str:
        return self.building.name

    def describe(self) -> str:
        b = self.building
        head = (
            f"{b.name} ({b.kind}, {b.rect.w}x{b.rect.d}, "
            f"{b.floors} floor{'s' if b.floors > 1 else ''}) -- score {self.score:.1f}"
        )
        body = "\n".join(f"    - {r}" for r in self.reasons)
        return f"{head}\n{body}" if body else head


def _size_score(rect: Rect) -> tuple[float, str]:
    """Reward footprints that fit a battle grid; penalise cramped or sprawling."""
    short, long_ = min(rect.w, rect.d), max(rect.w, rect.d)
    if short < 4:
        return -2.0, f"cramped at {rect.w}x{rect.d} -- little room to manoeuvre"
    if short >= IDEAL_MIN and long_ <= IDEAL_MAX:
        return 2.0, f"well sized at {rect.w}x{rect.d} for a battle grid"
    if long_ > IDEAL_MAX + 6:
        return -0.5, f"sprawling at {rect.w}x{rect.d} -- may need splitting"
    return 1.0, f"usable at {rect.w}x{rect.d}"


def _district_kind(city: City, name: str) -> str:
    for d in city.districts:
        if d.name == name:
            return d.kind
    return "residential"


def score_building(city: City, building: Building) -> Site:
    """Score one building, accumulating human-readable reasons."""
    score = 0.0
    reasons: list[str] = []

    kind_score, kind_reason = _KIND_SCORE.get(building.kind, (0.5, "an ordinary building"))
    score += kind_score
    reasons.append(kind_reason)

    dkind = _district_kind(city, building.district)
    d_score, d_reason = _DISTRICT_SCORE.get(dkind, (0.0, ""))
    if d_reason:
        score += d_score
        reasons.append(d_reason)

    s_score, s_reason = _size_score(building.rect)
    score += s_score
    reasons.append(s_reason)

    if building.floors > 1:
        bonus = 1.0 + 0.5 * (building.floors - 2)
        score += bonus
        reasons.append(f"{building.floors} floors give vertical play and a chase route")

    if building.hook:
        score += 2.0
        reasons.append(f"hook: {building.hook}")

    if building.owner:
        score += 0.25
        reasons.append(f"owned by {building.owner}")

    return Site(building=building, score=round(score, 2), reasons=reasons)


def rank(
    city: City,
    *,
    top: int | None = None,
    kind: str | None = None,
    district: str | None = None,
    min_floors: int = 1,
) -> list[Site]:
    """Rank the city's buildings, best first.

    Ties break on building id so the ordering is stable across runs.
    """
    sites: list[Site] = []
    for b in city.buildings:
        if kind and b.kind != kind:
            continue
        if district and district.lower() not in b.district.lower():
            continue
        if b.floors < min_floors:
            continue
        sites.append(score_building(city, b))

    sites.sort(key=lambda s: (-s.score, s.building.id))
    return sites[:top] if top else sites


def best(city: City, **kwargs) -> Site:
    """Return the single best site, raising if nothing matches the filters."""
    ranked = rank(city, **kwargs)
    if not ranked:
        raise ValueError("No building matched those filters.")
    return ranked[0]
