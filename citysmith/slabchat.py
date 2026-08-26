r"""A sentence becomes a small slab, without Claude ever touching geometry.

This is the module `docs/sidecar-ui.md` §5 describes, and its whole shape is
determined by one hard constraint in `CLAUDE.md`:

    Claude is a translation layer, not a generation layer. It maps natural
    language to generator parameters and writes prose. It must never produce
    coordinates, asset UUIDs, or slab bytes.

A chat that "generates a slab from a prompt" is exactly where that would get
broken, so the pipeline is split in two and the seam is a data structure::

    sentence -> [Claude, strict tool schema] -> a SlabSpec -> [Python] -> a Slab
                \_______ optional, networked _______/  \___ offline, pure ___/

The chat holds a :class:`SlabSpec`. Claude's only legal output is *edits* to
its named fields, through :data:`SLAB_TOOL` -- a strict tool schema modelled on
:data:`citysmith.ai._CITY_TOOL`, where every field is an enum, a bounded
integer or a bool. :func:`apply_edits` is the gate: anything out of range, of
the wrong type, or not in the enum is **clamped or dropped in Python**, with a
note saying so. The worst a model response can do is leave the spec exactly as
it was, which builds a boring room rather than a broken one.

:func:`build` is the other half and it is the half that must be bulletproof:
same spec in, byte-identical placements out, with no network and with
``anthropic`` uninstalled. Nothing below :func:`edit` imports it.

**Scope is one room to a handful of rooms** -- the thing a GM wants in the
middle of a session, pasted with the cheap path from §6 (clipboard, and the
user presses Ctrl+V). A request that implies a town clamps to
:data:`MAX_SPAN` rather than attempting it; a town is what ``citysmith build``
is for.

Nothing here is new geometry. A spec is turned into the
:class:`~citysmith.city.Building` that :func:`citysmith.floorplan.generate`
already takes, and the plan is handed to
:func:`citysmith.build.build_interior`, which is the same path
:mod:`citysmith.scene` walks. Every field below exists because one of those
two functions reads it.

What a spec can and cannot say
------------------------------

**It can say**: what the building is, how big, how many storeys, which wall
the door is in, what it is built of, how furnished, how finely divided into
rooms, whether it is roofed, whether the storeys stand side by side, how much
ground goes round it, what it is called, and the seed. Those are the fields of
:class:`SlabSpec`, and each one is read by a named function in
:mod:`citysmith.floorplan` or :mod:`citysmith.build`.

**It cannot say two things a user will reasonably ask for**, and the reason in
both cases is that the geometry has no parameter to carry the request -- not
that the request is unreasonable:

* **Name a room.** Room names come from ``floorplan._ROOM_MENU``, keyed on the
  building kind, and :func:`citysmith.floorplan.generate` has no ``rooms=``
  argument. So "call the back room the strongroom" changes nothing.
* **Place a fixture on a named wall.** Props are dealt by
  :func:`citysmith.build._dress` from ``_PROP_CATEGORY``, keyed on a room's
  purpose, and the only exposed lever is ``prop_density`` -- a single float for
  the whole building, which this module presents as the ``furnish`` enum. So
  "put the bar along the north wall" changes nothing.

Both would need a new argument in ``floorplan.py`` or ``build.py``. Until
there is one, **the chat must say so rather than appear to comply**: this
project has a documented history of features that were correct and invisible
(`CLAUDE.md`, "A feature can be correct and absent"), and a UI that accepts a
sentence and silently drops half of it is the same failure wearing a different
hat. :attr:`Turn.unapplied` is the channel -- the model is required to declare
what it could not express, and :meth:`Turn.summary` puts it in front of the
user.
"""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, replace
from typing import Any

from .build import build_interior, tier_of
from .city import Building, Rect
from .floorplan import Floorplan, generate as generate_floorplan
from .interior import LEVEL_GAP, MIN_INTERIOR, spread_levels, translate
from .palette import STYLES
from .slab import Slab

SLABCHAT_VERSION = 1


# -- bounds -------------------------------------------------------------------
#
# Every one of these is a *clamp*, not a validation error. A model that asks
# for a 400-tile warehouse gets a 24-tile one and a note saying so, because the
# alternative -- refusing -- gives the chat nothing to show and nothing to
# build, and the spec has to survive every turn.

#: Smallest interior worth building. :data:`citysmith.interior.MIN_INTERIOR`,
#: for the same reason: below it the BSP has nothing to split.
MIN_SPAN = MIN_INTERIOR

#: Largest interior this module will build, per axis, in tiles -- 120 ft.
#:
#: **This is the "not a town" line, and it is set by bytes.** Measured at the
#: worst case the bounds allow -- 24 x 24, three storeys, cluttered, roofed,
#: `min_room` at its smallest so the partition count is maximal, and a 4-tile
#: apron -- that is 5,631 placements compressing to **15,067 bytes against
#: TaleSpire's 30,720-byte cap, 49%**. So no legal spec can produce a slab
#: that will not paste, and the chat never needs the chunker.
#: ``test_the_largest_legal_spec_fits_in_one_slab`` re-measures it, because
#: this is a claim about the *whole* range of the bounds and anything that
#: adds dressing moves it -- the same "re-check after any change that adds
#: dressing" rule `CLAUDE.md` states for the town.
#: A bigger footprint needs the chunker, and the chunker needs the paste
#: discipline in `CLAUDE.md`; that is `citysmith build`, not a chat.
MAX_SPAN = 24

#: `citysmith.build.UTILITY_STOREYS` is 1 and the town caps ordinary buildings
#: at 3; a scene board has no reason to go higher.
MAX_STOREYS = 3

#: Seeds are folded into this range. `_CITY_TOOL` leaves the seed an unbounded
#: integer and so does :data:`SLAB_TOOL`, because there is no meaningful enum
#: for it -- so this is the one field where the clamp is the *only* bound, and
#: it is a modulus rather than a rejection so that any integer is usable.
SEED_MODULUS = 1_000_000

#: Longest name kept. The name is cosmetic -- it reaches
#: :attr:`citysmith.floorplan.Floorplan.name` and nothing else -- but it is the
#: one free-text field a model controls, so it is bounded like the rest.
MAX_NAME = 60

#: Building kinds the geometry can actually honour. Every one of these has an
#: entry in :data:`citysmith.floorplan._ROOM_MENU`, so its rooms are named for
#: the trade; a kind outside this list falls back to a house's room names,
#: which is a shed full of bedrooms.
KINDS = (
    "apothecary", "barracks", "guildhall", "house", "manor", "shop", "smithy",
    "stable", "tavern", "temple", "warehouse",
)

#: The four fabrics, plus ``auto`` -- which is :func:`citysmith.build.tier_of`
#: on the kind, the same rule the town uses. Named explicitly because a stone
#: shop is a stone building: FTG carries `material: STONE_BRICK` per building
#: and :func:`citysmith.interior.tier_for` lets it win over the trade. A chat
#: has no export to read, so the user says it instead.
TIERS = ("auto", "civic", "common", "trade", "utility")

SIDES = ("n", "e", "s", "w")

#: How full the rooms are, as words rather than as a float.
#:
#: An enum is what a model should be choosing between; the number behind it is
#: a measurement and not a taste. ``furnished`` is
#: :data:`citysmith.build.INTERIOR_DENSITY`, which `docs/interior-slabs.md`
#: puts inside the 0.14-0.22 delivered-props-per-cell band that hand-built
#: interiors sit in. ``sparse`` is what scenes used before that was measured,
#: and on a board it reads as an empty shell with one bench in it -- kept
#: because "nearly empty" is a thing to ask for, not because it is a good
#: default.
FURNISH: dict[str, float] = {
    "bare": 0.0,
    "sparse": 0.12,
    "furnished": 0.35,
    "cluttered": 0.5,
}

#: Tiles of ground laid round the building, if any. Off by default: a slab
#: pasted mid-session usually goes down inside somewhere that already has a
#: floor, and a ring of grass around it would be a lawn in a dungeon.
MAX_APRON = 4


# -- the spec -----------------------------------------------------------------

@dataclass(frozen=True)
class SlabSpec:
    """Everything that determines the slab, and nothing that does not.

    Frozen, because the chat holds one of these per turn and an edit produces
    a *new* spec -- :func:`apply_edits` returns one rather than mutating, so a
    turn that goes wrong can be thrown away by keeping the old object.

    Each field is named beside the function that consumes it:

    ``kind``
        :class:`citysmith.city.Building.kind`. Chooses the room menu and the
        principal room's name (:func:`citysmith.floorplan.generate`), whether
        the plan gets a hall at all (:func:`citysmith.floorplan.wants_hall`),
        which props dress it (:func:`citysmith.build._dress`) and, when
        ``tier`` is ``auto``, the fabric (:func:`citysmith.build.tier_of`).

    ``width`` / ``depth``
        :class:`citysmith.city.Building.rect`. The footprint in tiles, 5 ft
        each.

    ``storeys``
        :attr:`citysmith.city.Building.floors`, which
        :func:`citysmith.floorplan.generate` turns into levels and
        :func:`citysmith.build.build_interior` builds.

    ``entrance``
        :attr:`citysmith.city.Building.entrance`. Which wall the front door is
        in -- and therefore which way a hall runs
        (:func:`citysmith.floorplan.hall_layout`), where the stair goes, and
        which face gets the dense glazing
        (:func:`citysmith.build.glaze_rate`).

    ``style``
        :meth:`citysmith.palette.Palette.named`. Which asset pack the roles
        resolve against.

    ``tier``
        ``tier=`` on :func:`citysmith.build.build_interior`, which
        :func:`citysmith.build.interior_fabric` turns into a wall, partition,
        door, floor, window and corner **from one kit**.

    ``furnish``
        ``prop_density=`` on :func:`citysmith.build.build_interior`, via
        :data:`FURNISH`.

    ``min_room``
        ``min_room=`` on :func:`citysmith.floorplan.generate`. The smallest
        room edge, so it is the lever between "one big volume" and "several
        rooms".

    ``roof``
        ``roof=`` on :func:`citysmith.build.build_interior`. Off by default
        and it should stay off: a covered interior is nearly unusable at the
        table because the camera cannot see in.

    ``spread_levels``
        the inverse of ``stack=`` on
        :func:`citysmith.build.build_interior`, applied to the plan by
        :func:`citysmith.interior.spread_levels`. On, the storeys sit side by
        side and the whole building reads from overhead; off, they stack the
        way a building does and the camera has to be flown inside.

    ``apron``
        tiles of ground laid round the shell by
        :func:`citysmith.scene._lay_apron`.

    ``name``
        :attr:`citysmith.floorplan.Floorplan.name`. Cosmetic, and deliberately
        kept out of :meth:`building_id` so that renaming the place does not
        re-deal its windows.

    ``seed``
        every rng in the path: the floorplan's, the builder's prop draw and
        the palette's variant choice.
    """

    kind: str = "house"
    width: int = 9
    depth: int = 7
    storeys: int = 1
    entrance: str = "s"
    style: str = "medieval"
    tier: str = "auto"
    furnish: str = "furnished"
    min_room: int = 3
    roof: bool = False
    spread_levels: bool = True
    apron: int = 0
    name: str = ""
    seed: int = 33

    # -- identity ------------------------------------------------------------

    def building_id(self) -> str:
        """A stable id of the form ``tavern-0417``.

        Three passes downstream read this and none of them should move when
        the room is resized: :func:`citysmith.build.interior_fabric` takes its
        variant from ``crc32(building_id) % 3``, the window deal hashes it per
        segment, and :func:`citysmith.build.tier_of` splits it on ``-`` to
        recover the kind. So it is derived from **kind and seed only** -- ask
        for the room a tile wider and you get the same room a tile wider, not
        a different building. The seed is what re-rolls it.
        """
        digest = zlib.crc32(f"{self.kind}:{self.seed}".encode()) % 10000
        return f"{self.kind}-{digest:04d}"

    def display_name(self) -> str:
        return self.name.strip() or f"{self.kind.title()}"

    def tier_used(self) -> str:
        """The fabric this will actually build in, with ``auto`` resolved."""
        return tier_of(self.building_id()) if self.tier == "auto" else self.tier

    def prop_density(self) -> float:
        return FURNISH.get(self.furnish, FURNISH["furnished"])

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, and the UI holds this across turns."""
        d = asdict(self)
        d["slabchat_version"] = SLABCHAT_VERSION
        return d

    @classmethod
    def from_dict(cls, data: Any) -> "SlabSpec":
        """Rebuild from :meth:`to_dict`, or from anything at all.

        **It never raises**, and that is deliberate: this reads a value the
        browser has been holding, and a spec that cannot be loaded is a chat
        that cannot continue. It goes through the same clamping
        :func:`apply_edits` uses, so there is exactly one path by which a
        value gets into a :class:`SlabSpec` -- a second one would eventually
        disagree with the first.
        """
        return apply_edits(cls(), data).spec

    # -- prose ---------------------------------------------------------------

    def describe(self) -> str:
        """One line for the chat transcript."""
        levels = "1 storey" if self.storeys == 1 else f"{self.storeys} storeys"
        bits = [
            f"{self.display_name()} -- {self.kind}, {self.width}x{self.depth} "
            f"tiles ({self.width * 5}x{self.depth * 5} ft), {levels}",
            f"  door on the {_COMPASS[self.entrance]} side, {self.tier_used()} "
            f"fabric, {self.furnish}, {self.style} (seed {self.seed})",
        ]
        extras = []
        if self.roof:
            extras.append("roofed")
        if self.storeys > 1:
            extras.append("levels side by side" if self.spread_levels
                          else "levels stacked")
        if self.apron:
            extras.append(f"{self.apron}-tile apron")
        if extras:
            bits.append("  " + ", ".join(extras))
        return "\n".join(bits)


_COMPASS = {"n": "north", "e": "east", "s": "south", "w": "west"}


# -- the field table ----------------------------------------------------------
#
# One table drives three things that must not drift apart: the JSON schema the
# model is held to, the clamping applied to whatever it returns, and the
# documentation of each field. `test_every_spec_field_is_in_the_table` fails
# the build if a field is added to the dataclass and not to this.

@dataclass(frozen=True)
class _Field:
    """How one spec field is bounded, described and repaired."""

    #: ``enum`` | ``int`` | ``bool`` | ``str``
    type: str
    #: Legal values for an ``enum``, or the legal integers for an ``int``.
    #: Enumerated rather than given as a range -- see the rule stated at
    #: :data:`SLAB_TOOL`: in a strict schema an enum is the only real bound.
    values: tuple = ()
    doc: str = ""
    #: For ``int`` fields with no enum (the seed): folded into this modulus.
    modulus: int = 0


_FIELDS: dict[str, _Field] = {
    "kind": _Field(
        "enum", KINDS,
        doc=("What the building is. Decides the room names, whether it is "
             "planned around a hall, what furniture goes in it, and -- unless "
             "'tier' says otherwise -- what it is built of."),
    ),
    "width": _Field(
        "int", tuple(range(MIN_SPAN, MAX_SPAN + 1)),
        doc=(f"Footprint across, in tiles of 5 ft. {MIN_SPAN} to {MAX_SPAN}. "
             "This is a room or a small building, never a town -- anything "
             "larger is clamped."),
    ),
    "depth": _Field(
        "int", tuple(range(MIN_SPAN, MAX_SPAN + 1)),
        doc=f"Footprint front to back, in tiles of 5 ft. {MIN_SPAN} to {MAX_SPAN}.",
    ),
    "storeys": _Field(
        "int", tuple(range(1, MAX_STOREYS + 1)),
        doc="How many levels. Most buildings are one or two.",
    ),
    "entrance": _Field(
        "enum", SIDES,
        doc=("Which wall the front door is in: n, e, s or w. A hall runs in "
             "from this side and the windows are densest on it."),
    ),
    "style": _Field(
        "enum", tuple(sorted(STYLES)),
        doc="Which TaleSpire asset pack the whole thing is built from.",
    ),
    "tier": _Field(
        "enum", TIERS,
        doc=("Building fabric. 'auto' picks from the kind. 'civic' is dressed "
             "stone, 'trade' a shopfront, 'common' timber framing, 'utility' "
             "plain boarding with no windows."),
    ),
    "furnish": _Field(
        "enum", tuple(FURNISH),
        doc=("How much furniture. 'bare' is an empty shell; 'furnished' is "
             "what a hand-built interior measures at."),
    ),
    "min_room": _Field(
        "int", (2, 3, 4, 5, 6),
        doc=("Smallest room edge in tiles. Larger means fewer, bigger rooms; "
             "at 6 a small building becomes a single space."),
    ),
    "roof": _Field(
        "bool", (),
        doc=("Put a roof on. Almost always false: the camera cannot see into "
             "a covered interior, so a roofed slab is not a battle map."),
    ),
    "spread_levels": _Field(
        "bool", (),
        doc=("With several storeys, lay them side by side instead of stacking "
             "them, so the whole building reads from overhead. True unless "
             "the user wants a real stacked building."),
    ),
    "apron": _Field(
        "int", tuple(range(0, MAX_APRON + 1)),
        doc=("Tiles of ground laid around the outside, with paving in front "
             "of the door. 0 to paste the building alone into somewhere that "
             "already has a floor."),
    ),
    "name": _Field(
        "str", (),
        doc=("What the place is called. Cosmetic -- it changes no geometry, "
             "so it is safe to set freely."),
    ),
    "seed": _Field(
        "int", (), modulus=SEED_MODULUS,
        doc=("Any integer. The same spec always gives the same slab, so this "
             "is the only way to get a different arrangement of the same "
             "brief. Change it when the user asks for another version."),
    ),
}


# -- the tool schema ----------------------------------------------------------
#
# Modelled directly on `ai._CITY_TOOL`, with one difference that the whole
# design turns on.

def _schema_for(name: str, field: _Field) -> dict[str, Any]:
    """One property, as a nullable variant of its own bounded type.

    **`null` means "leave this field alone", and every field is required.**

    That combination is what makes this an *edit* schema while staying inside
    what a strict schema allows. `strict: true` needs `additionalProperties:
    false` and a `required` list, and it drops the numeric constraints
    (`minimum`/`maximum`) an unbounded integer would need -- so a bound has to
    be an `enum` to be real. Making every field required and giving it an
    explicit no-op value means the model cannot silently *omit* a field and
    cannot be tempted to restate a value it was not asked about: for anything
    the user did not mention, `null` is the obvious answer.
    """
    if field.type == "bool":
        inner: dict[str, Any] = {"type": "boolean"}
    elif field.type == "str":
        inner = {"type": "string"}
    elif field.type == "int":
        inner = {"type": "integer"}
        if field.values:
            inner["enum"] = list(field.values)
    else:
        inner = {"type": "string", "enum": list(field.values)}
    return {
        "anyOf": [inner, {"type": "null"}],
        "description": f"{field.doc} Null to leave it unchanged.",
    }


# **In a strict schema, an enum is the only real bound. A Python clamp is the
# backstop, not the constraint.**
#
# This is why `ai._CITY_TOOL` writes `max_floors` as `enum [1, 2, 3, 4]` rather
# than as an integer with `minimum`/`maximum`, and that reason was never
# recorded -- so it reads like a stylistic quirk and the obvious "simplification"
# is to put the range back. Do not: **structured outputs do not support
# numerical constraints.** `minimum`, `maximum` and `multipleOf` are dropped
# from the schema before it reaches the model, along with `minLength`/
# `maxLength` and the complex array constraints. They do not error; they simply
# stop existing, and a field documented as 3-24 is then unbounded on the wire.
#
# So every bound below that has to hold is an `enum`, including the integers --
# `width`, `depth`, `storeys`, `min_room` and `apron` all enumerate their legal
# values one by one. `_Field.values` is that list, and it drives the schema and
# the clamp from the same source so they cannot disagree.
#
# **`seed` is the one exception, and it is deliberate**: a million-member enum
# is absurd, and a seed cannot make the result unbuildable -- it only changes
# which of several equally valid arrangements you get. It is declared as a
# bare integer, and `_apply_one` folds it modulo :data:`SEED_MODULUS`. That is
# the single field where the Python fold is the *only* bound, and it is safe
# precisely because no value of it can break geometry.

#: The model's entire legal output.
#:
#: Note what is *not* in here: no coordinates, no asset names, no room list,
#: no slab. Every property is an enum, an enumerated integer or a boolean, and
#: the two that cannot be enumerated -- the seed and the name -- cannot affect
#: whether the result is buildable. There is no field through which a bad
#: model response can produce broken geometry; the worst it can do is fill the
#: whole object with nulls, which changes nothing.
SLAB_TOOL: dict[str, Any] = {
    "name": "edit_slab_spec",
    "description": (
        "Edit the specification for a small TaleSpire slab -- one room to a "
        "handful of rooms, the kind of thing a game master builds in the "
        "middle of a session. Set only the fields the user's message actually "
        "asks to change and leave every other field null; the current values "
        "are given to you and they carry over. Do not invent coordinates, "
        "asset names, room lists or slab data -- the generator produces all of "
        "that deterministically from these fields."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            **{name: _schema_for(name, f) for name, f in _FIELDS.items()},
            "note": {
                "type": "string",
                "description": (
                    "One short sentence for the chat, saying what you changed "
                    "and why. No markdown, no lists."
                ),
            },
            "unsupported": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Anything the user asked for that NO field above can "
                    "express -- most often naming a specific room, or placing "
                    "a named piece of furniture against a named wall. Neither "
                    "is a parameter this generator has. Quote the request in a "
                    "few words, one entry each. Empty array when every part of "
                    "the request landed in a field. Never put something here "
                    "that you did in fact set a field for, and never apologise "
                    "here -- it is a list, not a sentence."
                ),
            },
        },
        "required": [*_FIELDS, "note", "unsupported"],
        "additionalProperties": False,
    },
}


# -- validation ---------------------------------------------------------------

def _coerce_int(value: Any) -> int | None:
    """An int, or None if this was never a number.

    Deliberately generous about *shape* and strict about *range* -- the range
    is what protects the geometry, and refusing `"3"` where `3` was meant only
    turns a recoverable model slip into a lost turn.
    """
    if isinstance(value, bool):
        return None                 # bool is an int in Python; it is not one here
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else int(round(value))
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
    return None


def _clean_name(value: Any) -> str:
    """Printable, single-line, bounded. Anything else becomes nothing."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    return text[:MAX_NAME]


def _apply_one(name: str, field: _Field, value: Any,
               notes: list[str]) -> tuple[bool, Any]:
    """Validate one field. Returns ``(keep_it, cleaned)``.

    ``keep_it`` false means the existing value stands -- which is the answer
    for null, for the wrong type, and for anything outside the bounds that
    cannot be sensibly clamped.
    """
    if field.type == "bool":
        got = _coerce_bool(value)
        if got is None:
            notes.append(f"{name}: {value!r} is not true or false; left as it was")
            return False, None
        return True, got

    if field.type == "str":
        cleaned = _clean_name(value)
        if not cleaned and value not in ("", None):
            notes.append(f"{name}: {value!r} is not usable text; left as it was")
            return False, None
        return True, cleaned

    if field.type == "int":
        got = _coerce_int(value)
        if got is None:
            notes.append(f"{name}: {value!r} is not a number; left as it was")
            return False, None
        if field.modulus:
            folded = got % field.modulus
            if folded != got:
                notes.append(f"{name}: folded {got} into 0-{field.modulus - 1}")
            return True, folded
        lo, hi = field.values[0], field.values[-1]
        if got < lo or got > hi:
            clamped = min(max(got, lo), hi)
            notes.append(f"{name}: {got} is out of range {lo}-{hi}; used {clamped}")
            return True, clamped
        if got not in field.values:                     # a gap in the enum
            nearest = min(field.values, key=lambda v: (abs(v - got), v))
            notes.append(f"{name}: {got} is not offered; used {nearest}")
            return True, nearest
        return True, got

    # enum
    if isinstance(value, str) and value.strip().lower() in field.values:
        return True, value.strip().lower()
    notes.append(
        f"{name}: {value!r} is not one of {', '.join(field.values)}; "
        "left as it was"
    )
    return False, None


def _coherent(spec: SlabSpec, notes: list[str]) -> SlabSpec:
    """Fix the combinations no single field can catch.

    Only one so far, and it is a real one rather than a hypothetical: a
    ``min_room`` larger than the building means
    :func:`citysmith.floorplan.hall_layout` bails and the BSP cannot cut, so
    the plan comes out as one undivided room. That is a legal answer and
    sometimes the wanted one -- ask for a 6x6 shrine and you should get a
    shrine, not four cupboards -- but it should not happen because a *stale*
    ``min_room`` outlived a resize. Clamped to half the short side, which is
    the largest value that can still produce two rooms.
    """
    short = min(spec.width, spec.depth)
    cap = max(_FIELDS["min_room"].values[0], short // 2)
    if spec.min_room > cap:
        notes.append(
            f"min_room: {spec.min_room} does not fit a {spec.width}x{spec.depth} "
            f"building; used {cap}"
        )
        spec = replace(spec, min_room=cap)
    return spec


#: Keys that are part of the envelope rather than the spec, and so are not
#: reported as unknown fields.
_ENVELOPE = frozenset({"note", "unsupported", "slabchat_version"})


def apply_edits(spec: SlabSpec, edits: Any) -> Turn:
    """Apply a model's edits to ``spec``. **Never raises.**

    This is the gate between the model and the geometry, and it is written on
    the assumption that everything coming through it is wrong. A response that
    is not an object, carries fields that do not exist, gives a string where an
    integer belongs, or asks for a 300-tile warehouse produces a *valid*
    :class:`Turn` -- never an exception, and never a spec that cannot be built.

    A :attr:`Turn.clean` result means every field the model sent was taken as
    sent and it declared nothing unexpressible.
    """
    if not isinstance(edits, dict):
        return Turn(
            spec=spec,
            problems=(
                f"expected an object of field edits, got "
                f"{type(edits).__name__}; nothing changed",
            ),
        )

    notes: list[str] = []
    changes: dict[str, Any] = {}
    for name, value in edits.items():
        if name in _ENVELOPE:
            continue
        field = _FIELDS.get(name)
        if field is None:
            notes.append(f"{name}: not a field of the spec; ignored")
            continue
        if value is None:                      # the schema's "leave it alone"
            continue
        keep, cleaned = _apply_one(name, field, value, notes)
        if keep:
            changes[name] = cleaned

    return Turn(
        spec=_coherent(replace(spec, **changes), notes),
        note=note_of(edits),
        problems=tuple(notes),
        unapplied=unapplied_of(edits),
    )


#: Most entries a model may declare unexpressible in one turn. A request has a
#: handful of clauses; a list longer than this is a model narrating rather than
#: reporting, and a wall of them buries the ones that matter.
MAX_UNAPPLIED = 6


@dataclass(frozen=True)
class Turn:
    """The whole result of one edit, including what could *not* be done.

    **The last field is the reason this is a class and not a tuple.** A spec
    has no field for "call the back room the strongroom" or "put the bar on
    the north wall" -- see the module docstring -- so a chat that takes those
    sentences and quietly builds the same room as before is lying by omission.
    `CLAUDE.md` records the same failure shape twice under "A feature can be
    correct and absent": fences were built, reviewed and written up while
    being in none of the screenshots, and the conclusions drawn were
    worthless. A silently dropped clause is that, live, in front of a user.

    So the model is *required* to declare what it could not express -- the
    ``unsupported`` array is in :data:`SLAB_TOOL`'s ``required`` list, so
    "nothing was dropped" has to be said explicitly as an empty array rather
    than being the default of leaving the field out. It has to come from the
    model because **Python never sees the sentence**: by the time
    :func:`apply_edits` runs there is only a dict of fields, and nothing in it
    records what was asked for and not carried.

    Two channels, kept apart because they fail differently:

    ``problems``
        what *Python* repaired -- a clamp, a coercion, an unknown field, an
        out-of-enum value. Something was asked for and was changed.

    ``unapplied``
        what *the generator cannot do at all*. Nothing was changed and nothing
        will be until `floorplan.py` or `build.py` grows a parameter.
    """

    spec: SlabSpec
    note: str = ""
    problems: tuple[str, ...] = ()
    unapplied: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """True when everything asked for was expressible and taken as sent."""
        return not self.problems and not self.unapplied

    def summary(self) -> str:
        """What the chat shows. Never silently empty when something was lost."""
        lines = [self.spec.describe()]
        if self.note:
            lines += ["", self.note]
        if self.unapplied:
            lines += ["", "Not built -- this generator has no setting for:"]
            lines += [f"  - {u}" for u in self.unapplied]
        if self.problems:
            lines += ["", "Adjusted:"]
            lines += [f"  - {p}" for p in self.problems]
        return "\n".join(lines)


def unapplied_of(edits: Any) -> tuple[str, ...]:
    """What the model says it could not express, cleaned and bounded.

    Model-authored free text, so it is treated exactly like the ``name`` field
    -- printable, single-line, length-capped, count-capped -- rather than
    trusted onto the page.
    """
    if not isinstance(edits, dict):
        return ()
    raw = edits.get("unsupported")
    if isinstance(raw, str):          # a model that sent one string, not a list
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join("".join(c for c in item if c.isprintable()).split())[:120]
        if text and text not in out:
            out.append(text)
    return tuple(out[:MAX_UNAPPLIED])


def note_of(edits: Any) -> str:
    """The model's one-sentence summary, cleaned. Empty if it sent none."""
    if not isinstance(edits, dict):
        return ""
    text = edits.get("note")
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())[:280]


# -- building it (no AI, no network) ------------------------------------------

def as_building(spec: SlabSpec) -> Building:
    """The spec as the :class:`~citysmith.city.Building` the planner takes.

    :func:`citysmith.floorplan.generate` is written against the city
    generator's building record, so this is the adapter -- the same job
    :func:`citysmith.interior.as_building` does for an imported town, from a
    sentence instead of a footprint.
    """
    return Building(
        id=spec.building_id(),
        name=spec.display_name(),
        kind=spec.kind,
        district="",
        rect=Rect(0, 0, spec.width, spec.depth),
        floors=spec.storeys,
        entrance=spec.entrance,
    )


def plan_for(spec: SlabSpec) -> Floorplan:
    """The floorplan, before any assets are chosen.

    Separate from :func:`build` because it needs no catalog: the UI can draw
    the plan (:func:`citysmith.render.floorplan_svg`) and list the rooms on a
    machine with no TaleSpire install, and only the slab itself needs assets.
    """
    fp = generate_floorplan(as_building(spec), seed=spec.seed,
                            min_room=spec.min_room)
    if spec.spread_levels and fp.levels > 1:
        fp = spread_levels(fp, LEVEL_GAP)
    if spec.apron:
        # Held off the origin by the apron so nothing has a negative
        # coordinate. `scene.build` does the same, for the same reason.
        translate(fp, spec.apron, spec.apron)
    return fp


def build(spec: SlabSpec, palette) -> Slab:
    """The slab. Same spec in, byte-identical placements out.

    **This half has no AI dependency and no network call**, and that is the
    point of the split: ``anthropic`` can be uninstalled, the machine can be
    offline, and this still works. :func:`edit` is a convenience on top of it,
    not a step in it.

    Determinism is inherited rather than asserted. Every rng below is seeded
    from ``spec.seed`` and the building id, and the two places where set
    iteration used to leak the process hash seed into the output -- the
    interior partitions, and the digest that noticed -- were fixed at the
    source (see `CLAUDE.md`, "The same plan did not build the same board
    twice"). ``tests/test_slabchat.py`` re-checks it across two processes with
    different ``PYTHONHASHSEED`` anyway, because a determinism claim that only
    holds inside one process is the exact bug that was shipped.
    """
    fp = plan_for(spec)
    b = build_interior(
        fp, palette,
        seed=spec.seed,
        roof=spec.roof,
        prop_density=spec.prop_density(),
        stack=not spec.spread_levels,
        tier=spec.tier_used(),
    )
    if spec.apron:
        _lay_apron(spec, b, fp, palette)
    # Normalised because the apron and the wall pieces on the west and north
    # faces can sit fractionally outside the plan's own rect, and a slab
    # cannot store a negative coordinate.
    return Slab(list(b.placements)).normalized()


def _lay_apron(spec: SlabSpec, b, fp: Floorplan, palette) -> None:
    """Ground round the shell, if the style has any.

    Guarded rather than assumed: :func:`citysmith.scene._lay_apron` lays
    through :meth:`citysmith.build.Builder.surface`, which *requires* its role,
    so a style with no ``ground`` would turn an apron request into an
    exception. A chat cannot afford that -- the fallback is a building with no
    ground round it, plus a note, which is what the caller asked for minus the
    part this asset pack cannot do.
    """
    from .scene import _lay_apron as lay

    if palette.resolve("ground") is None or palette.resolve("street") is None:
        return
    lay(b, fp, spec.apron, palette.require("floor").size_y)


def palette_for(spec: SlabSpec, catalog):
    """The palette this spec builds against.

    Seeded from the spec, because :meth:`citysmith.palette.Palette.resolve`
    picks between equally-good candidates with an rng -- so the seed reaches
    the *materials* as well as the plan, and two specs differing only in seed
    give two different-looking rooms rather than the same room rearranged.
    """
    from .palette import Palette

    return Palette.named(catalog, spec.style, spec.seed)


# -- the natural language half (optional, networked) --------------------------

_SYSTEM = (
    "You are helping a game master build one small location for a tabletop "
    "session in TaleSpire: a room, or a small building of a few rooms. The "
    "user describes what they want and you edit a specification for it.\n\n"
    "You do not draw anything. A Python generator turns the specification "
    "into geometry deterministically, so your entire job is choosing the "
    "right values for the named fields. Never describe coordinates, tile "
    "positions, asset names or furniture placement -- you cannot control any "
    "of them, and claiming to is worse than saying nothing.\n\n"
    "Change only what the user asked about. Every other field must be null; "
    "the current value carries over. If the request implies something much "
    "bigger than a building -- a street, a district, a town -- set the size "
    "to the largest offered and say in the note that this tool builds one "
    "location and the town generator is a different command.\n\n"
    "Some things the user will ask for have no field at all, and you must "
    "say so rather than let them pass. There is no way to name an "
    "individual room, and no way to place a particular piece of furniture "
    "or put anything against a particular wall: room names come from the "
    "building type, and furniture is controlled only by the overall "
    "'furnish' level. When part of a request needs one of those, put it "
    "in the 'unsupported' array. Do not pretend in the note that you did "
    "it, and do not set an unrelated field as a consolation.\n\n"
    "Always call the edit_slab_spec tool."
)


def edit(spec: SlabSpec, message: str, *, effort: str | None = None,
         history: list[dict] | None = None) -> Turn:
    """One conversational turn: ``spec`` plus a sentence gives a :class:`Turn`.

    The turn carries the new spec, the model's own sentence for the
    transcript, whatever :func:`apply_edits` had to repair, and whatever the
    model declared it could not express. The UI should show all four -- a
    clamp the user cannot see is a clamp they will argue with, and a dropped
    clause they cannot see is worse.

    Raises :class:`citysmith.ai.AIError` when the model layer is unavailable,
    and nothing else: a *response* that is unusable is not an error here, it
    is a spec that did not change.

    ``tool_choice`` forces the call, so this is structured extraction rather
    than a conversation with a tool available -- there is no agentic loop and
    the model gets exactly one move.
    """
    from . import ai                     # imports `anthropic` only when called

    client = ai._client()
    turns: list[dict] = list(history or [])
    turns.append({
        "role": "user",
        "content": (
            "Current specification:\n"
            f"{_as_prompt(spec)}\n\n"
            f"The game master says: {message}"
        ),
    })

    try:
        response = client.messages.create(
            model=ai.MODEL,
            max_tokens=2000,
            system=_SYSTEM,
            output_config={"effort": effort or ai.DEFAULT_EFFORT},
            tools=[SLAB_TOOL],
            tool_choice={"type": "tool", "name": SLAB_TOOL["name"]},
            messages=turns,
        )
    except Exception as exc:  # pragma: no cover - network dependent
        raise ai.AIError(f"Claude request failed: {exc}") from exc

    if response.stop_reason == "refusal":
        raise ai.AIError("Claude declined this request.")

    for block in response.content:
        if block.type == "tool_use":
            return apply_edits(spec, dict(block.input))

    # Not an exception. A model that answered without calling the tool has
    # said nothing about the spec, which is the same outcome as an edit of all
    # nulls -- and the boring room is the designed failure mode.
    return Turn(
        spec=spec,
        problems=("Claude returned no edits; the specification is unchanged.",),
    )


def _as_prompt(spec: SlabSpec) -> str:
    """The spec as the model sees it: field names and current values only."""
    return "\n".join(
        f"  {name}: {getattr(spec, name)!r}" for name in _FIELDS
    )
