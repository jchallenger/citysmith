"""Wall families: both panel widths, the course they stand in, and how a run is packed.

Three findings, probed on boards on 2026-08-28, are implemented here.

**A run is covered by the kit's own wide panel, not by N copies of its 1-cell
one.** Every kit citysmith builds from ships a 2-cell wall, a 2-cell window and
a 0.5 filler; ``palette._WALLSZ`` pinned the wall role to a 1-cell footprint,
so none of them could be resolved. Runs average 4.9 cells across all three
towns and **100% of wall segments sit in a run of 2 or more**, so this was
never an edge case. Hand-built community slabs are 29.3% wide panels against
our nil.

**The narrow remainder must not stack.** A packer that ignores the level puts
the odd cell in the same slot on every storey, which draws a full-height column
of a visibly different panel -- on a Rural run of 7 that is a dark stripe of
boarding up the whole wall. :func:`pack` takes the level for that reason and
``shift`` is the default rule.

**A course is not the same piece as the one above it.** Castle Fortified ships
``base`` variants of its wall, window, corner and filler; Marble Palace ships
the full ``base`` / ``mid`` / ``top`` set for all six roles. Nothing read them,
so a three-storey building was one course repeated three times -- and our
castle corner is ``castle wall corner 1x1 base``, a *plinth*, which we were
putting on every floor.

The kit is the ``folder``. That rule is why this module keys on it and reads
``group_tag`` only as a hint: `Village Roof Side Wall 02` is tagged
``group='roof'`` and is a wall, and taking the tag at its word is a mistake
CLAUDE.md already records twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The three courses a wall can stand in, ground upward. A kit that names none
#: of them has every piece filed under ``mid``, so a family with no course
#: system resolves to the same piece at every level and nothing special is
#: needed for it.
COURSES = ("base", "mid", "top")

#: What a family can supply. ``span`` is in cells: 1 or 2.
ROLES = ("wall", "window", "corner", "filler")

#: How far a wide panel's height may differ from its kit's 1-cell piece.
#:
#: **Not zero, and the number is measured.** `Tavern Wall 01` is 2.03 against
#: the rest of its own kit's 2.00 -- 1.8 inches at 5 ft to the tile. Rejecting
#: it costs the common house its only wide panel; accepting it means the wide
#: pieces stand 0.03 proud of the course line. The storey is always pitched at
#: the *1-cell* piece (:func:`storey_height`), so the error is an overlap
#: rather than a gap, and an overlap of 1.8 inches is invisible where a gap of
#: 1.8 inches would show as daylight. Read on a 3-storey swatch before this was
#: written: nothing visible at eye level or from overhead.
WIDE_HEIGHT_SLOP = 0.05

#: A panel is thin on one horizontal axis and about two tiles tall. Same test
#: `build.is_curtain_piece` makes, stated here so this module does not import
#: from `build` -- `build` imports from it.
_MIN_WALL_H, _MAX_WALL_H = 1.4, 2.7

#: Words that mark a *decorated* member of a role rather than its plain one.
#: A kit's default has to be the plain piece: `castle wall 2x2 base w shield`
#: is as much a "wide wall at the base course" as `castle wall 2x2 base` is,
#: and picking by chance crowns a whole town in shields.
# "ruin" rather than "ruined": MegaDungeon files `md_wall_2x1_ruin_01/02/03`,
# which tie with its plain wall on the longer word and were being dealt as if
# they were it -- ruined masonry as a building's ordinary facade.
_DECOR = ("arch", "curtain", "shield", "unique", "ruin", "ruits", "breach",
          "broken", "crumbling", "decor", "alcove", "prison", "cage",
          # A gable's extra panel is a wall and belongs in the family, but it
          # is not the kit's plain wall: `haunted roof extra wall` is shorter
          # than `abandoned_village_wall_1x1_01` and was winning the default
          # on length alone.
          "roof", "extra", "supported", "half")

#: Groups whose pieces are panel-shaped and are not walls. Each of these was
#: picked as a kit's default wall before the filter existed: `Moorgoth Large
#: Roof` for Moorgoth, `Ship fence end port` for Ship, `Desert Arch top` for
#: Desert Village's top course. A collider cannot tell them apart from a wall
#: -- all three are a thin panel about two tiles tall -- so the group has to.
_NOT_WALL_GROUPS = ("fence", "stair", "hatch", "archway", "rail", "pillar",
                    "column", "ladder", "banner", "curtain")


#: A trailing index: " 01", "_02", " v1". Everything before it is the stem.
_INDEX = re.compile(r"^(.*?)[ _\-]*v?\d+$", re.IGNORECASE)


def stem_of(name: str) -> str:
    """A piece's name with its trailing index removed.

    **This is what tells a sibling from a different material.** Two pieces can
    tie for the same rank -- same role, same width, same course, equally plain
    -- and still not be interchangeable: Shogun Palace files `shogunWall1x2`,
    `shogunPaperWall1x2`, `shugunRockWall_1x2` and `shogun_digWall_2x1` in one
    folder, and they are plaster, paper screens, rock and dug earth. Dealing
    across those per panel builds a wall out of four different things.

    A real sibling differs only by an index -- `bg_wall_1x1_01` and `_02`,
    `Desert wall 02` and `03`, `Lava wall 1x1 hot v1` and `v2`. Same stem,
    nothing to choose between them. The Shogun pieces have four different
    stems and are correctly left alone.
    """
    m = _INDEX.match(name.strip())
    return (m.group(1) if m else name).strip(" _-").lower()


def _has_word(name: str, word: str) -> bool:
    return re.search(rf"\b{word}\b", name.lower()) is not None


def course_of(name: str) -> str:
    """Which course a piece is authored for, from its name.

    The course word is not always a suffix -- Marble Palace infixes it
    (`Palace Marble wall mid 01`) and numbers the pieces afterwards -- so this
    matches the word anywhere rather than the end. A piece naming no course is
    ``mid``, which makes a kit with no course system a kit whose every piece
    is an intermediate one, and that is exactly how it should behave.
    """
    for c in ("base", "top", "mid"):
        if _has_word(name, c):
            return c
    return "mid"


def _span_of(a) -> int:
    return int(round(max(a.size_x, a.size_z)))


def _is_panel(a) -> bool:
    return (min(a.size_x, a.size_z) <= 0.75
            and _MIN_WALL_H <= a.size_y <= _MAX_WALL_H)


def _is_corner(a) -> bool:
    sx, sz = round(a.size_x, 2), round(a.size_z, 2)
    return (sx == sz and sx in (1.0, 2.0)
            and _MIN_WALL_H <= a.size_y <= _MAX_WALL_H)


def _is_filler(a) -> bool:
    return (max(a.size_x, a.size_z) <= 0.5
            and _MIN_WALL_H <= a.size_y <= _MAX_WALL_H)


def _role_of(a) -> tuple[str, int] | None:
    """``(role, span)``, or None when the piece is not part of a wall family.

    Order matters: a filler is thin on *both* axes and would otherwise pass as
    a panel, and a corner is a full cell and would otherwise pass as nothing.
    """
    n = a.name.lower()
    g = (a.group_tag or "").lower()
    if "door" in n or "door" in g:
        return None
    if any(w in g for w in _NOT_WALL_GROUPS):
        return None
    # **The kit's own word is the authority, and it is the word "wall".**
    # `Village Roof Side Wall 02` is `group='roof'` and is the panel our
    # common house is built from, so the group alone cannot decide -- but a
    # piece that says "wall" in neither its group nor its name is not one,
    # whatever its collider measures. That is the test that stopped a roof,
    # a fence end and an archway being dealt as walls.
    if not any("wall" in t or "corner" in t for t in (g, n)):
        return None
    if _is_filler(a):
        # Only if it is actually a wall part -- plenty of small props are
        # 0.5 x 2 x 0.5. The kits name theirs, without exception in this
        # library: "filler", or "inner corner".
        if "filler" in n or ("inner" in n and "corner" in n):
            return ("filler", 1)
        return None
    if _is_corner(a):
        if "corner" not in n and "corner" not in g:
            return None
        if "inner" in n:
            return None          # a reflex corner, not an outside one
        return ("corner", _span_of(a))
    if _is_panel(a):
        if "corner" in n:
            return None
        role = "window" if ("window" in n or "window" in a.tags) else "wall"
        return (role, _span_of(a))
    return None


@dataclass(frozen=True)
class WallFamily:
    """Every wall piece one kit ships, indexed by role, width and course.

    ``pieces[(role, span, course)]`` is a list, plainest first. Dealing for
    variety happens **among equally plain pieces only** -- see :meth:`all`.
    """

    kit: str
    pack: str
    pieces: dict[tuple[str, int, str], list] = field(default_factory=dict)

    def all(self, role: str, span: int, course: str = "mid",
            decorated: bool = False) -> list:
        """Every piece for this slot, falling back through the courses.

        A kit that ships no ``top`` piece uses its ``mid`` one at the head, and
        a kit that ships no ``mid`` uses whatever it has. That fallback is what
        makes Castle Fortified -- ``base`` plus plain, and no ``top`` at all --
        come out as a plinth course under plain masonry rather than as nothing.

        **Only the plainest pieces are returned**, and that is the difference
        between variety and noise. Castle Fortified files `castle wall 2x2
        base`, `... base w curtain` and `... base w shield` under one slot;
        dealing across all three by a hash put shields and hanging curtains on
        every civic building in the town, and `castle wall ARCH` on every
        upper course. Sorting them plainest-first was not enough on its own,
        because the deal indexes the whole list. Pass ``decorated`` when the
        decorated pieces are what you actually want.
        """
        for c in (course, "mid", "base", "top"):
            got = self.pieces.get((role, span, c))
            if got:
                if decorated:
                    return got
                best = _rank(got[0])
                return [a for a in got if _rank(a) == best]
        return []

    def piece(self, role: str, span: int, course: str = "mid", variant: int = 0,
              decorated: bool = False):
        """One piece for this slot, or None. ``variant`` deals among equals."""
        got = self.all(role, span, course, decorated)
        if not got:
            return None
        return got[variant % len(got)]

    def siblings(self, role: str, span: int, course: str = "mid") -> list:
        """The default piece and everything interchangeable with it.

        Interchangeable means "differs only by a trailing index" -- see
        :func:`stem_of` for why rank alone is not enough.
        """
        got = self.all(role, span, course)
        if not got:
            return []
        stem = stem_of(got[0].name)
        return [a for a in got if stem_of(a.name) == stem]

    def deal(self, role: str, span: int, course: str = "mid", key: int = 0):
        """One of the interchangeable siblings, chosen by ``key``.

        Dealt **per panel** rather than per building: nothing distinguishes
        these pieces, so a long run has no reason to repeat one of them, and a
        town has no reason to pick one and keep it. Where a kit has only one
        piece for the slot -- which is every kit the medieval palette resolves
        today -- this returns it and nothing changes.
        """
        sibs = self.siblings(role, span, course)
        if not sibs:
            return None
        return sibs[key % len(sibs)]

    @property
    def storey_height(self) -> float | None:
        """The course pitch, taken from the 1-cell wall and nothing else.

        **Never from the wide piece**, even when the wide piece is what mostly
        builds the wall. Tavern's is 2.03 against its kit's 2.00, and pitching
        a storey at it would raise every floor and the roof with it -- the
        arithmetic behind both "the roof floated" and "the floor showed from
        outside". Pitched at the narrow piece the discrepancy is an overlap
        into the course above, which nothing can see.
        """
        got = self.all("wall", 1) or self.all("wall", 2)
        return got[0].size_y if got else None

    @property
    def wide(self):
        """The kit's 2-cell wall, if it has one that can share a course."""
        got = self.all("wall", 2)
        h = self.storey_height
        if not got or h is None:
            return None
        return got[0] if abs(got[0].size_y - h) <= WIDE_HEIGHT_SLOP else None

    @property
    def courses(self) -> tuple[str, ...]:
        """Which courses this kit actually authors a wall for."""
        return tuple(c for c in COURSES
                     if self.pieces.get(("wall", 1, c))
                     or self.pieces.get(("wall", 2, c)))

    @property
    def complete(self) -> bool:
        """Can one building be clad in this kit alone, at both widths?"""
        return bool(self.all("wall", 1) and self.wide is not None
                    and (self.all("window", 1) or self.all("window", 2))
                    and self.all("corner", 1))

    def summary(self) -> str:
        bits = []
        for role in ROLES:
            for span in (1, 2):
                n = sum(len(v) for (r, s, _), v in self.pieces.items()
                        if r == role and s == span)
                if n:
                    bits.append(f"{role}{span}:{n}")
        return " ".join(bits)


def _decor_score(a) -> int:
    """How decorated a piece is: 0 is the kit's plain member of its slot."""
    n = a.name.lower()
    return sum(1 for w in _DECOR if w in n)


def _rank(a) -> tuple[int, int]:
    """How readily a piece stands in for its slot. Lower is better.

    **The group is the first term, and that is the Village panel finding.**
    `Village Roof Side Wall 01/02` and `Tavern Wall 01` are one kit -- same
    folder -- and they are still not interchangeable: the Village panels are a
    roof set's gable, `group='roof'`, and carry no timber frame of their own,
    so one dropped between two `Tavern Wall 01`s reads as a bare plaster patch.
    Probed on `PROBE wall mix village tavern`.

    Ranking `group='wall'` above everything else picks `Tavern Wall - Small
    01` -- the wide panel's own partner, which blends invisibly -- while
    leaving the Village panel in the family for the one slot where it is the
    only candidate there is: the single 1-cell window in the pack.
    """
    g = (a.group_tag or "").lower()
    return (0 if "wall" in g else 1, _decor_score(a))


def _plainness(a) -> tuple:
    """Sort key that puts a kit's plain piece before its decorated ones."""
    return _rank(a) + (len(a.name), a.name)


def families(catalog, *, kits: set[str] | None = None) -> dict[str, WallFamily]:
    """Every kit in the catalog that can clad a wall, keyed on ``folder``."""
    out: dict[str, WallFamily] = {}
    for a in catalog.assets:
        if getattr(a, "kind", "tile") != "tile":
            continue
        if getattr(a, "deprecated", False):
            continue
        kit = a.folder or "(none)"
        if kits is not None and kit not in kits:
            continue
        slot = _role_of(a)
        if slot is None:
            continue
        role, span = slot
        if span not in (1, 2):
            continue
        fam = out.setdefault(kit, WallFamily(kit, a.pack))
        fam.pieces.setdefault((role, span, course_of(a.name)), []).append(a)
    for fam in out.values():
        for v in fam.pieces.values():
            v.sort(key=_plainness)
    out = {k: v for k, v in out.items() if v.all("wall", 1) or v.all("wall", 2)}
    for fam in out.values():
        _prune_to_one_course(fam)
    return out


def _prune_to_one_course(fam: "WallFamily") -> None:
    """Drop pieces that cannot share a course with the family's 1-cell wall.

    **A folder is not automatically one storey system.** Nineteen kits ship a
    2.5-tall Wall/Floor combination alongside their 2.0 pieces -- wall and
    deck in one casting, the pack's own answer to the storey problem -- and
    Tavern's `Tavern Wall/Floor Corner 01` was being dealt as the corner for a
    2.0 wall, which would put a corner half a tile proud of the panel beside it
    at every floor. They are a different system and belong to a different
    fabric, not to this one.

    The wide panel keeps :data:`WIDE_HEIGHT_SLOP`; everything else is exact.
    """
    h = fam.storey_height
    if h is None:
        return
    for key in list(fam.pieces):
        role, span, _ = key
        slop = WIDE_HEIGHT_SLOP if span == 2 else 1e-6
        kept = [a for a in fam.pieces[key] if abs(a.size_y - h) <= slop]
        if kept:
            fam.pieces[key] = kept
        else:
            del fam.pieces[key]


def course_at(level: int, floors: int) -> str:
    """Which course a storey stands in.

    A single-storey building takes ``base``: given one course to spend, the
    plinth is the half worth having -- it is what puts a building on the
    ground rather than on the grass.
    """
    if level == 0:
        return "base"
    return "top" if level == floors - 1 else "mid"


# -- packing a run -------------------------------------------------------------

def _slots(length: int, wide: int, at: int) -> list[tuple[int, int]]:
    """``length`` cells as wide pieces with the single remainder at slot ``at``.

    ``at`` counts pieces, not cells. Every rule below is a choice of ``at``,
    which is the whole design space once the two widths are fixed.
    """
    n, rem = divmod(length, wide)
    at = max(0, min(n, at))
    out, cell = [], 0
    for i in range(n + (1 if rem else 0)):
        span = 1 if (rem and i == at) else wide
        out.append((cell, span))
        cell += span
    return out


def pack(length: int, level: int = 0, rule: str = "shift",
         wide: int = 2) -> list[tuple[int, int]]:
    """Which pieces cover a run of ``length`` cells, as ``(offset, span)``.

    ``single``
        one piece per cell -- what the board did before this module.
    ``centred``
        wide panels butting outward from both ends, remainder mid-run. Keeps
        both corners flush, and **stacks the remainder into a column**, which
        is the defect the other two exist to fix.
    ``shift`` (default)
        the remainder walks the interior slots, one per course, so it never
        stacks and never reaches a corner. A run of 5 is three pieces with one
        interior slot and nothing to walk to, so it falls back to using every
        slot -- a narrow panel beside a corner piece beats a column, and runs
        of 5 are the commonest length there is.
    ``bond``
        remainder at alternating ends, course by course.
    ``bondfull``
        every course offset by a cell, so no vertical joint is ever shared.
        The only rule that also breaks an **even** run, which has no remainder
        for the others to move -- at the cost of two narrow pieces per odd
        course.
    """
    if length <= 0:
        return []
    if rule == "single" or wide < 2 or length < wide:
        return [(i, 1) for i in range(length)]
    n = length // wide
    if rule == "centred":
        return _slots(length, wide, (n + 1) // 2)
    if rule == "greedy":
        return _slots(length, wide, n)
    if rule == "bond":
        return _slots(length, wide, 0 if level % 2 else n)
    if rule == "bondfull":
        if level % 2 == 0:
            return _slots(length, wide, n)
        out, cell = [(0, 1)], 1
        while length - cell >= wide:
            out.append((cell, wide))
            cell += wide
        while cell < length:
            out.append((cell, 1))
            cell += 1
        return out
    interior = list(range(1, n))
    if len(interior) < 2:
        interior = list(range(n + 1))
    return _slots(length, wide, interior[level % len(interior)])


#: Every rule :func:`pack` understands, for probes and for the CLI.
PACK_RULES = ("single", "centred", "shift", "bond", "bondfull", "greedy")

#: The rule the town build uses. See :func:`pack`.
DEFAULT_PACK = "shift"


def runs_of(segments) -> list[tuple[str, int, int, int]]:
    """Group ``(x, z, side)`` wall segments into maximal straight runs.

    Returns ``(side, x, z, length)`` for the run's first cell, advancing along
    x on the north and south faces and along z on the east and west ones.

    Runs are computed from whatever is handed in, which is deliberate: the
    caller has already taken out the cells a corner piece or a doorway owns, so
    a door splits its face into two runs and each is packed on its own. That is
    the case a packer has to get right, and building it into this function
    instead would hide it.
    """
    lines: dict[tuple[str, int], list[int]] = {}
    for x, z, side in segments:
        if side in ("n", "s"):
            lines.setdefault((side, z), []).append(x)
        else:
            lines.setdefault((side, x), []).append(z)
    out: list[tuple[str, int, int, int]] = []
    for (side, fixed), vals in sorted(lines.items()):
        vals = sorted(set(vals))
        start = prev = vals[0]
        for v in vals[1:] + [None]:
            if v != prev + 1:
                length = prev - start + 1
                out.append((side, start, fixed, length) if side in ("n", "s")
                           else (side, fixed, start, length))
                if v is None:
                    break
                start = v
            prev = v if v is not None else prev
    return out


# -- the roster: what every kit is FOR -----------------------------------------

#: Folder -> the job that kit is declared to do. **Load-bearing, not a list:**
#: :data:`TIER_FABRICS` resolves through it, so an entry here is the only way a
#: kit reaches a building. A roster nothing reads is documentation pretending
#: to be code, which is why this was nearly not built at all.
#:
#: 22 kits in the installed packs can clad a building and three had a job. The
#: rest were not "unused" in any recorded sense -- nothing said what they were
#: for, so each was rediscovered from scratch every time somebody looked.
#:
#: Roles beginning ``style:`` name a whole :class:`~citysmith.palette.Style`
#: that does not exist yet; ``interior`` is a fabric for a scene rather than a
#: town, since those kits ship no window and a windowless house is a cellar.
KIT_ROLE: dict[str, str] = {
    # -- medieval, in a town ---------------------------------------------
    "Tavern": "common",
    "Rural": "utility",
    "Castle Fortified": "civic",
    "Abandoned Village": "poor",
    "CastleRuins": "ruin",
    "Marble Palace": "civic-large",
    "Moorgoth": "civic-dark",
    "Ship": "docks",
    # -- medieval, inside one building -----------------------------------
    "MegaDungeon": "interior",
    "Dungeon Cellar": "interior",
    "BellowGloom": "interior",
    "Aberration": "interior",
    "Cavern Lava": "interior",
    # -- medieval, a style of its own ------------------------------------
    "Desert Village": "style:desert",
    "Shogun Palace": "style:shogun",
    # -- the sci-fi packs ------------------------------------------------
    "Concrete Building": "style:cyberpunk",
    "Facility": "style:cyberpunk",
    "Interstellar": "style:cyberpunk",
    "Industrial": "style:cyberpunk",
    "Brick Building": "style:cyberpunk",
    "Chamber": "style:cyberpunk",
    "Outpost": "style:cyberpunk",
}

#: Which roles a tier may be built from, and how often. **The weighting is the
#: design, not a detail.** Abandoned Village beside Tavern reads as a poor
#: house next to a sound one, which is what a town edge looks like; dealt
#: fifty-fifty it reads as a town that burned down. The numbers here are a
#: conservative first cut and have NOT been read on a board -- see the
#: `facade-fabric-variety` task, which does not close until they have.
#:
#: The problem this solves, measured on Forest Church: 46 of 51 buildings were
#: `Tavern Wall 01` + `Tavern Wall - Small 01`, one material, because a tier
#: resolved exactly one kit. Before the 2026-08-28 wall work the common house
#: dealt two panels per building, so across-building variety had gone 2 -> 1
#: while within-wall variety went up.
TIER_FABRICS: dict[str, tuple[tuple[str, int], ...]] = {
    "common": (("common", 6), ("poor", 1)),
    "utility": (("utility", 4), ("poor", 1)),
    # Trade shares the house's fabric and is told apart by its door and its
    # glazing -- the reason CLAUDE.md already gives, unchanged.
    "trade": (("common", 1),),
    "civic": (("civic", 1),),
}


def kits_for_role(role: str, families: "dict[str, WallFamily] | None" = None,
                  catalog=None) -> list[str]:
    """Every kit declared for ``role``, in a stable order."""
    named = sorted(k for k, r in KIT_ROLE.items() if r == role)
    if families is None and catalog is None:
        return named
    have = families if families is not None else globals()["families"](catalog)
    return [k for k in named if k in have]


def unmapped(families: "dict[str, WallFamily]", *,
             packs: "tuple[str, ...]" = ("Medieval Fantasy",)) -> list[str]:
    """Kits with no declared role, in the packs a style claims.

    **Reported, never raised, and scoped.** A test that fails on any unmapped
    family is a cost paid by whoever installs a DLC for our bookkeeping; this
    is the same rule `Layout.unmapped` already follows for an FTG value we do
    not know -- surface it, do not drop it and do not die on it.
    """
    return sorted(k for k, f in families.items()
                  if k not in KIT_ROLE and f.pack in packs)


def fabric_for(tier: str, key: int, families: "dict[str, WallFamily]"):
    """The wall family one building of ``tier`` is clad in.

    Dealt per BUILDING from ``key`` -- a stable hash of the building id, the
    way the wall variant used to be -- so a street is a mix and a rebuild is
    identical. Returns ``None`` when the tier declares nothing, which lets a
    caller keep whatever it resolved from the palette.

    **A fabric is a whole kit, not a panel.** That is the difference between
    this and `WallFamily.deal`: the sibling deal varies a panel inside one
    wall, this varies the wall. Both are needed and neither substitutes for
    the other -- the sibling deal is inert on every kit a town uses today,
    and this one is what makes two houses differ.
    """
    weighted = TIER_FABRICS.get(tier)
    if not weighted:
        return None
    pool: list[str] = []
    for role, weight in weighted:
        for kit in kits_for_role(role, families):
            pool.extend([kit] * max(1, int(weight)))
    if not pool:
        return None
    return families[pool[key % len(pool)]]
