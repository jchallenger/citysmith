"""A sentence becomes a small slab, and the model cannot break the geometry.

Two halves, and they are tested for opposite things.

The **Python half** must be bulletproof: it has to build offline, with
`anthropic` uninstalled, and give byte-identical placements for the same spec
in a fresh process. Those are the tests that would fail loudly if the split
between translation and generation ever leaked.

The **model half** is assumed hostile. Every test that touches
:func:`~citysmith.slabchat.apply_edits` feeds it something a well-behaved model
would never send -- a string where an integer goes, a value outside the enum, a
list instead of an object -- and asserts the outcome is a *boring but valid*
spec plus a note, never an exception and never something unbuildable. No test
here makes a network call; the model is never in the loop.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys

import pytest

from citysmith import slabchat as sc
from citysmith.slab import MAX_COMPRESSED_BYTES, encode


# -- the spec itself ----------------------------------------------------------

def test_every_spec_field_is_bounded_and_documented():
    """The dataclass and the field table cannot drift apart.

    `_FIELDS` drives the JSON schema *and* the clamping, so a field added to
    the dataclass and not to the table would be a field the model can never
    set and nothing would ever validate -- which is exactly the shape of
    "correct and invisible".
    """
    from dataclasses import fields

    declared = {f.name for f in fields(sc.SlabSpec)}
    assert declared == set(sc._FIELDS), (
        "SlabSpec and _FIELDS disagree; every field needs bounds and a "
        "description"
    )
    for name, field in sc._FIELDS.items():
        assert field.doc, f"{name} has no description for the model"
        if field.type in ("enum", "int") and not field.modulus:
            assert field.values, f"{name} is unbounded"


def test_the_default_spec_is_inside_its_own_bounds():
    """A default that a clamp would have to repair is a default that is wrong."""
    spec = sc.SlabSpec()
    turn = sc.apply_edits(spec, spec.to_dict())
    assert turn.spec == spec
    assert turn.problems == ()


def test_a_spec_round_trips_through_json():
    import json

    spec = sc.SlabSpec(kind="tavern", width=14, depth=11, storeys=2,
                       entrance="w", tier="civic", furnish="cluttered",
                       name="The Fox", seed=7)
    assert sc.SlabSpec.from_dict(json.loads(json.dumps(spec.to_dict()))) == spec


def test_the_building_id_survives_a_resize_but_not_a_reseed():
    """Three passes hash this, so it must not move when the room is resized.

    `interior_fabric` takes its variant from crc32(building_id), and the window
    deal hashes it per segment -- so if the id moved with the width, asking for
    the room a tile wider would silently re-deal its walls and windows.
    """
    base = sc.SlabSpec(kind="tavern", width=9, seed=33)
    assert base.building_id() == base.__class__(kind="tavern", width=20,
                                                seed=33).building_id()
    assert base.building_id() != sc.SlabSpec(kind="tavern", seed=34).building_id()
    assert base.building_id().startswith("tavern-")


def test_auto_tier_follows_the_kind_the_way_the_town_does():
    from citysmith.build import tier_of

    for kind in sc.KINDS:
        spec = sc.SlabSpec(kind=kind)
        assert spec.tier_used() == tier_of(spec.building_id())
    assert sc.SlabSpec(kind="house", tier="civic").tier_used() == "civic"


# -- the schema ---------------------------------------------------------------

def test_the_schema_is_strict_and_closed():
    tool = sc.SLAB_TOOL
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    # Everything is required, including the two envelope fields: the schema's
    # "leave it alone" is an explicit null, not an omission.
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]) == set(sc._FIELDS) | {"note", "unsupported"}


def test_no_bound_in_the_schema_is_expressed_as_a_numeric_range():
    """The rule this module is built on, asserted rather than trusted.

    Structured outputs drop `minimum`/`maximum`/`multipleOf` (and the string
    and array constraints) before the schema reaches the model. A field
    documented as 3-24 and bounded that way is therefore *unbounded on the
    wire*. Every real bound here is an enum, and this test exists so that a
    future "simplification" back to a range fails instead of silently removing
    the constraint -- the same trap `ai._CITY_TOOL`'s `max_floors` enum was
    already avoiding without saying so.
    """
    banned = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
              "multipleOf", "minLength", "maxLength", "minItems", "maxItems"}

    def walk(node, path="input_schema"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, (
                    f"{path}.{key} is a constraint strict schemas discard; "
                    "use an enum, and clamp in Python"
                )
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(sc.SLAB_TOOL["input_schema"])


def test_every_bounded_field_offers_its_bounds_as_an_enum():
    props = sc.SLAB_TOOL["input_schema"]["properties"]
    for name, field in sc._FIELDS.items():
        if not field.values:
            continue
        inner = props[name]["anyOf"][0]
        assert inner["enum"] == list(field.values), name
        assert props[name]["anyOf"][1] == {"type": "null"}, (
            f"{name} must accept null, which is how the model leaves it alone"
        )


def test_the_seed_is_the_only_field_whose_sole_bound_is_the_python_fold():
    """Called out because it is the documented exception and should stay one."""
    unenumerated = [n for n, f in sc._FIELDS.items()
                    if not f.values and f.type in ("int",)]
    assert unenumerated == ["seed"]
    assert sc._FIELDS["seed"].modulus == sc.SEED_MODULUS


def test_the_model_cannot_name_geometry():
    """The hard constraint, as a property of the schema.

    Claude must never produce coordinates, asset UUIDs or slab bytes. Because
    the schema is closed, the way to assert that is to show the *complete* set
    of things it can say -- and that none of them is a position, an asset or a
    quantity of geometry.
    """
    props = sc.SLAB_TOOL["input_schema"]["properties"]

    # The complete set, named. Adding a field here is a deliberate act, and
    # anything that could carry a position or an asset would have to be added
    # to this list to pass -- which is the point.
    assert set(props) == {
        "kind", "width", "depth", "storeys", "entrance", "style", "tier",
        "furnish", "min_room", "roof", "spread_levels", "apron", "name",
        "seed", "note", "unsupported",
    }

    # And every one of them is a bounded scalar (or, for `unsupported`, a list
    # of prose). Nothing takes an object or a list of numbers, so there is no
    # shape in the schema that a coordinate, a UUID or a placement could
    # travel in even if the model tried.
    for name, prop in props.items():
        variants = prop.get("anyOf", [prop])
        for variant in variants:
            kind = variant["type"]
            assert kind in ("string", "integer", "boolean", "null", "array"),                 f"{name} accepts {kind}, which is wide enough to carry geometry"
            if kind == "array":
                assert name == "unsupported"
                assert variant["items"] == {"type": "string"}


# -- validation: the model is assumed hostile ---------------------------------

@pytest.mark.parametrize("garbage", [
    None, [], "make it bigger", 42, {"kind": ["tavern"]}, {"width": {}},
])
def test_a_malformed_response_leaves_a_buildable_spec(garbage, catalog_palette):
    """Never an exception, never an unbuildable spec -- a boring room."""
    before = sc.SlabSpec(kind="tavern", width=12, depth=9)
    turn = sc.apply_edits(before, garbage)
    assert turn.spec == before, "garbage must not move the spec"
    assert turn.problems, "and it must say that it did not"
    sc.build(turn.spec, catalog_palette)          # still builds


def test_an_out_of_range_number_is_clamped_not_refused():
    turn = sc.apply_edits(sc.SlabSpec(), {"width": 400, "depth": -8, "storeys": 99})
    assert turn.spec.width == sc.MAX_SPAN
    assert turn.spec.depth == sc.MIN_SPAN
    assert turn.spec.storeys == sc.MAX_STOREYS
    # Three clamps, and a fourth note because clamping depth to 3 leaves the
    # default min_room too big for the new footprint -- see `_coherent`.
    for field in ("width", "depth", "storeys"):
        assert any(p.startswith(f"{field}:") for p in turn.problems), field
    assert "400" in turn.problems[0]


def test_a_town_scale_request_clamps_rather_than_attempting_it():
    """The scope line. A chat builds a location; `citysmith build` builds towns."""
    turn = sc.apply_edits(sc.SlabSpec(), {"width": 400, "depth": 300})
    assert (turn.spec.width, turn.spec.depth) == (sc.MAX_SPAN, sc.MAX_SPAN)


def test_a_value_outside_an_enum_leaves_the_field_alone():
    before = sc.SlabSpec(kind="tavern", style="medieval")
    turn = sc.apply_edits(before, {"kind": "spaceport", "style": "art deco"})
    assert turn.spec.kind == "tavern"
    assert turn.spec.style == "medieval"
    assert len(turn.problems) == 2


def test_an_unknown_field_is_ignored_and_reported():
    turn = sc.apply_edits(sc.SlabSpec(), {"fireplace": "north wall"})
    assert turn.spec == sc.SlabSpec()
    assert "fireplace" in turn.problems[0]


def test_numbers_that_arrive_as_strings_are_taken_but_junk_is_not():
    """Generous about shape, strict about range -- the range is what protects
    the geometry, and losing a turn to `"3"` helps nobody."""
    assert sc.apply_edits(sc.SlabSpec(), {"width": "12"}).spec.width == 12
    turn = sc.apply_edits(sc.SlabSpec(), {"width": "wide"})
    assert turn.spec.width == sc.SlabSpec().width
    assert turn.problems


def test_a_bool_field_refuses_a_non_bool():
    assert sc.apply_edits(sc.SlabSpec(), {"roof": "true"}).spec.roof is True
    turn = sc.apply_edits(sc.SlabSpec(), {"roof": "sometimes"})
    assert turn.spec.roof is False and turn.problems


def test_the_seed_folds_rather_than_being_rejected():
    turn = sc.apply_edits(sc.SlabSpec(), {"seed": -1})
    assert 0 <= turn.spec.seed < sc.SEED_MODULUS


def test_a_name_is_cleaned_and_bounded():
    turn = sc.apply_edits(sc.SlabSpec(), {"name": "  The Fox\x00\x07  "})
    assert turn.spec.name == "The Fox"
    assert len(sc.apply_edits(sc.SlabSpec(), {"name": "x" * 500}).spec.name) \
        == sc.MAX_NAME


def test_null_means_leave_it_alone():
    """The schema's no-op edit, which is what an unmentioned field must be."""
    before = sc.SlabSpec(kind="temple", width=15, seed=9)
    turn = sc.apply_edits(before, {name: None for name in sc._FIELDS})
    assert turn.spec == before
    assert turn.problems == ()


def test_a_stale_min_room_is_clamped_to_the_new_footprint():
    """A cross-field repair no single field could make.

    "Make it smaller" after "big rooms" would otherwise leave a min_room that
    the BSP cannot cut with, and the plan silently becomes one undivided room.
    """
    turn = sc.apply_edits(sc.SlabSpec(min_room=6), {"width": 5, "depth": 5})
    assert turn.spec.min_room == 2
    assert any("min_room" in p for p in turn.problems)


# -- the unapplied surface ----------------------------------------------------

def test_what_the_generator_cannot_do_is_surfaced_not_dropped():
    """The failure this project has shipped before, in a new place.

    A spec has no field for naming a room or placing a fixture. A chat that
    accepts those sentences and builds the same room as before is the "correct
    and absent" failure live in front of a user, so the model declares them and
    the turn carries them.
    """
    turn = sc.apply_edits(sc.SlabSpec(), {
        "kind": "tavern",
        "note": "Made it a tavern.",
        "unsupported": ["call the back room the strongroom",
                        "put the bar on the north wall"],
    })
    assert turn.spec.kind == "tavern"
    assert turn.unapplied == ("call the back room the strongroom",
                              "put the bar on the north wall")
    assert not turn.clean
    assert "strongroom" in turn.summary()
    assert "north wall" in turn.summary()


def test_a_clean_turn_says_nothing_was_lost():
    turn = sc.apply_edits(sc.SlabSpec(), {"kind": "tavern", "unsupported": []})
    assert turn.clean
    assert turn.unapplied == ()


def test_the_unapplied_list_is_cleaned_bounded_and_deduplicated():
    """Model-authored free text, treated like every other model-authored text."""
    turn = sc.apply_edits(sc.SlabSpec(), {
        "unsupported": ["a" * 500, "dup", "dup", 7, None,
                        *[f"item {i}" for i in range(20)]],
    })
    assert len(turn.unapplied) <= sc.MAX_UNAPPLIED
    assert all(len(u) <= 120 for u in turn.unapplied)
    assert len(set(turn.unapplied)) == len(turn.unapplied)


def test_a_non_list_unsupported_does_not_break_the_turn():
    assert sc.apply_edits(sc.SlabSpec(), {"unsupported": "just one"}).unapplied \
        == ("just one",)
    assert sc.apply_edits(sc.SlabSpec(), {"unsupported": 7}).unapplied == ()


def test_the_envelope_fields_are_not_reported_as_unknown_fields():
    turn = sc.apply_edits(sc.SlabSpec(),
                          {"note": "hi", "unsupported": [],
                           "slabchat_version": sc.SLABCHAT_VERSION})
    assert turn.problems == ()
    assert turn.note == "hi"


def test_the_summary_shows_the_spec_even_when_nothing_went_wrong():
    turn = sc.apply_edits(sc.SlabSpec(), {"kind": "temple"})
    assert "temple" in turn.summary()


# -- building it --------------------------------------------------------------

def test_a_spec_builds_a_slab(catalog_palette):
    slab = sc.build(sc.SlabSpec(kind="tavern", width=12, depth=9), catalog_palette)
    assert slab.placements
    (mx, my, mz), _ = slab.bounds()
    assert (mx, my, mz) == (0.0, 0.0, 0.0), "a slab cannot store a negative"
    encode(slab)


def test_the_plan_needs_no_catalog():
    """The UI can draw the floorplan on a machine with no TaleSpire install."""
    fp = sc.plan_for(sc.SlabSpec(kind="tavern", width=14, depth=11))
    assert fp.rooms and fp.doors
    assert any(d.exterior for d in fp.doors)


def test_levels_are_laid_side_by_side_when_asked_and_stacked_when_not():
    spread = sc.plan_for(sc.SlabSpec(kind="tavern", width=10, depth=8,
                                     storeys=2, spread_levels=True))
    assert spread.rect_on(0).x != spread.rect_on(1).x
    stacked = sc.plan_for(sc.SlabSpec(kind="tavern", width=10, depth=8,
                                      storeys=2, spread_levels=False))
    assert stacked.rect_on(0).x == stacked.rect_on(1).x


def test_an_apron_holds_the_plan_off_the_origin(catalog_palette):
    """Otherwise the apron is at negative coordinates and normalising it shifts
    the tile numbers out from under the plan."""
    spec = sc.SlabSpec(kind="house", apron=3)
    fp = sc.plan_for(spec)
    assert fp.rect.x >= 3 and fp.rect.z >= 3
    assert len(sc.build(spec, catalog_palette).placements) > len(
        sc.build(sc.SlabSpec(kind="house", apron=0), catalog_palette).placements
    )


def test_an_apron_is_skipped_rather_than_raising_when_the_style_has_no_ground():
    """`Builder.surface` *requires* its role, so an unguarded apron on a style
    with no ground would turn a chat request into a traceback."""
    class NoGround:
        def __init__(self, inner):
            self._inner = inner
            self.catalog = inner.catalog

        def resolve(self, role, variant=0):
            return None if role in ("ground", "street") else \
                self._inner.resolve(role, variant)

        def require(self, role, variant=0):
            return self._inner.require(role, variant)

        def prop(self, category, rng):
            return self._inner.prop(category, rng)

    palette = NoGround(_stub())
    assert sc.build(sc.SlabSpec(kind="house", apron=3), palette).placements


def test_the_largest_legal_spec_fits_in_one_slab():
    """The bound that makes MAX_SPAN "not a town".

    Worst case the bounds allow: biggest footprint, most storeys, most
    furniture, roofed, the smallest room size so partitions are maximal, and
    the widest apron. If this ever approaches the cap, the bounds move -- a
    chat must never emit something TaleSpire refuses to paste.
    """
    palette = _stub(roof=True)
    spec = sc.SlabSpec(kind="tavern", width=sc.MAX_SPAN, depth=sc.MAX_SPAN,
                       storeys=sc.MAX_STOREYS, furnish="cluttered", roof=True,
                       min_room=2, apron=sc.MAX_APRON)
    size = len(base64.b64decode(encode(sc.build(spec, palette))))
    assert size < MAX_COMPRESSED_BYTES * 0.75, (
        f"the largest legal spec compresses to {size} bytes against the "
        f"{MAX_COMPRESSED_BYTES} cap; reduce MAX_SPAN or MAX_STOREYS"
    )


def test_every_kind_and_tier_builds():
    """No combination the schema offers may fail to build."""
    palette = _stub()
    for kind in sc.KINDS:
        for tier in sc.TIERS:
            spec = sc.SlabSpec(kind=kind, tier=tier, width=11, depth=9,
                               storeys=2)
            assert sc.build(spec, palette).placements, f"{kind}/{tier}"


def test_the_smallest_spec_still_builds():
    spec = sc.SlabSpec(kind="shop", width=sc.MIN_SPAN, depth=sc.MIN_SPAN,
                       min_room=2, furnish="bare")
    assert sc.build(spec, _stub()).placements


def _stub(roof: bool = False):
    from tests.conftest import FLOOR, StubPalette

    palette = StubPalette()
    if roof:
        palette._ROLES = dict(StubPalette._ROLES) | {"roof": FLOOR}
    return palette


# -- determinism --------------------------------------------------------------

def test_the_same_spec_builds_the_same_slab(catalog_palette):
    spec = sc.SlabSpec(kind="tavern", width=13, depth=10, storeys=2,
                       furnish="cluttered", seed=5)
    first = sc.build(spec, catalog_palette)
    second = sc.build(spec, _stub())
    assert first.placements == second.placements


def test_a_different_seed_gives_a_different_slab(catalog_palette):
    """Otherwise the seed is decoration and "give me another version" does
    nothing -- which is the one field a user has for asking again."""
    a = sc.build(sc.SlabSpec(kind="tavern", width=13, depth=10, seed=1),
                 catalog_palette)
    b = sc.build(sc.SlabSpec(kind="tavern", width=13, depth=10, seed=2),
                 _stub())
    assert a.placements != b.placements


#: Built offline in a subprocess: no network, and `anthropic` made
#: unimportable. Kept self-contained so the subprocess needs nothing but the
#: package -- the same shape as `tests/test_determinism.py`.
SCRIPT = """
import socket, sys

# `import anthropic` now raises ImportError, whatever is installed.
sys.modules["anthropic"] = None

def _blocked(*a, **k):
    raise AssertionError("the offline half tried to open a socket")

socket.socket = _blocked
socket.create_connection = _blocked

from citysmith import slabchat as sc
from citysmith.catalog import Asset


def asset(letter, name, sx, sy, sz):
    return Asset(id=letter * 8 + "-1111-2222-3333-444444444444", name=name,
                 kind="tile", pack="p", group_tag="", tags=(), folder="f",
                 size_x=sx, size_y=sy, size_z=sz)


FLOOR = asset("a", "floor", 1.0, 0.5, 1.0)
WALL = asset("c", "wall", 1.0, 2.0, 0.5)
INNER = asset("d", "inner", 1.0, 2.0, 0.5)
DOOR = asset("e", "door", 1.0, 2.0, 0.5)
STOOL = asset("b", "stool", 0.5, 0.3, 0.3)


class P:
    catalog = type("C", (), {"assets": [FLOOR, WALL, INNER, DOOR, STOOL]})()
    _R = {"floor": FLOOR, "floor_upper": FLOOR, "wall": WALL,
          "wall_interior": INNER, "door": DOOR}

    def resolve(self, role, variant=0):
        return self._R.get(role)

    def require(self, role, variant=0):
        return self._R[role]

    def prop(self, category, rng):
        return STOOL


spec = sc.SlabSpec(kind="tavern", width=13, depth=10, storeys=2,
                   furnish="cluttered", seed=33)
for p in sc.build(spec, P()).placements:
    print(p.asset_id, p.x, p.y, p.z, p.rot)

assert "anthropic" not in [m for m in sys.modules if sys.modules[m] is not None]
"""


def _emit(hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_spec_builds_the_same_slab_under_a_different_hash_seed():
    """The determinism guarantee, across processes -- which is the only place
    it can be tested.

    A process fixes its string-hash seed once, so building twice in one
    process always agrees. That is exactly why the whole suite was blind to
    `_interior_walls` emitting its partitions in a different order every run:
    identical geometry, different bytes, and every scene reading STALE after a
    rebuild that changed nothing. Same trap, same shape of test.
    """
    first, second = _emit("1"), _emit("2")
    assert first.splitlines(), "the subprocess built nothing"
    assert first == second, (
        "the same spec emitted its placements in a different order"
    )


def test_the_build_half_works_with_anthropic_uninstalled_and_no_network():
    """`SCRIPT` blocks both. If this fails, the split has leaked."""
    assert len(_emit("0").splitlines()) > 100


def test_importing_the_module_does_not_import_anthropic():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import citysmith.slabchat; "
         "print('anthropic' in sys.modules)"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", (
        "slabchat imports anthropic at module scope; the offline half must not"
    )
