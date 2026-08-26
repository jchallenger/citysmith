"""Which board holds which scene.

The point of this record is that walking into the same tavern twice does not
produce two boards. Nothing in TaleSpire can be asked -- the campaign list is
names and nothing else -- so these tests are about the record being right,
because it is the only thing that knows.
"""

from __future__ import annotations

import json

import pytest

from citysmith import boards
from citysmith.scene import Mark, Scene


def _scene(scene_id="graybank-tavern-0014", centroid=(169.0, 168.0)) -> Scene:
    return Scene(
        scene_id=scene_id, town="Graybank", building_id="tavern-0014",
        building_name="The Halfling and the Fox", kind="tavern",
        board="Interior - Graybank - The Halfling and the Fox",
        seed=33, style="medieval", levels=1, width=8, depth=7,
        centroid=centroid, entrance="n",
        party=[Mark("Party 1", 7, 4)], slabs=["graybank-tavern-0014.slab.txt"],
    )


#: A real slab, because the digest decodes what it is given -- it is a claim
#: about what is on the board, not about the bytes of a file.
def _slabs(tmp_path, scene, marker=(0.0, 0.0, 0.0)):
    from citysmith.catalog import Asset
    from citysmith.slab import Placement, Slab, encode

    asset = "a" * 8 + "-1111-2222-3333-444444444444"
    slab = Slab([Placement(asset, 1.0, 0.0, 1.0, 0),
                 Placement(asset, marker[0], marker[1], marker[2], 0)])
    for name in scene.slabs:
        (tmp_path / name).write_text(encode(slab), encoding="utf-8")
    return boards.digest_of_scene(tmp_path, scene)


# -- the four states ----------------------------------------------------------

def test_an_unvisited_building_is_new(tmp_path):
    scene = _scene()
    digest = _slabs(tmp_path, scene)
    registry = boards.Registry.load(tmp_path / "boards.json")
    assert registry.status(scene, digest) == (boards.NEW, None)


def test_a_recorded_board_is_ready_and_gets_reused(tmp_path):
    scene = _scene()
    digest = _slabs(tmp_path, scene)
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, digest)

    again = boards.Registry.load(tmp_path / "boards.json")
    status, record = again.status(scene, digest)
    assert status == boards.READY
    assert record.board == scene.board
    assert record.visits == 1


def test_rebuilding_the_scene_makes_the_board_stale(tmp_path):
    """The board holds the build that was pasted onto it, and nothing can
    change that from here -- there is no erase. Noticing is the whole job."""
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene, (0.0, 0.0, 0.0)))

    rebuilt = _slabs(tmp_path, scene, (5.0, 0.0, 5.0))
    status, record = registry.status(scene, rebuilt)
    assert status == boards.STALE
    assert record.board == scene.board


def test_a_building_that_has_moved_is_reported_not_reused(tmp_path):
    """A re-import can renumber buildings: `tavern-0014` is an index into the
    export's feature order, not an identity. If the centroid has moved, this
    id may be somebody else's tavern now."""
    scene = _scene()
    digest = _slabs(tmp_path, scene)
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, digest)

    moved = _scene(centroid=(300.0, 168.0))
    status, _record = registry.status(moved, digest)
    assert status == boards.MOVED


def test_a_small_wobble_in_the_centroid_is_not_a_move(tmp_path):
    """Re-importing with a different crop shifts every coordinate slightly.
    A tenth of a tile is not a different building."""
    scene = _scene()
    digest = _slabs(tmp_path, scene)
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, digest)

    nudged = _scene(centroid=(169.3, 168.2))
    assert registry.status(nudged, digest)[0] == boards.READY


# -- the record itself --------------------------------------------------------

def test_the_same_files_always_give_the_same_digest(tmp_path):
    """A build run twice must not read as stale."""
    scene = _scene()
    first = _slabs(tmp_path, scene)
    second = _slabs(tmp_path, scene)
    assert first == second


def test_a_manifest_naming_a_slab_that_is_not_there_is_an_error(tmp_path):
    scene = _scene()
    with pytest.raises(FileNotFoundError, match="not there"):
        boards.digest_of_scene(tmp_path, scene)


def test_a_return_visit_is_counted_and_the_board_is_untouched(tmp_path):
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene))
    record = registry.visit(scene.scene_id)
    assert record.visits == 2
    assert record.board == scene.board


def test_a_second_board_supersedes_but_never_erases_the_first(tmp_path):
    """A rebuild goes onto a new board beside the old one. The old one still
    exists in the campaign and somebody will wonder what it was."""
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene))
    registry.record(scene, _slabs(tmp_path, scene, (5.0, 0.0, 5.0)),
                    board="Interior - Graybank - The Halfling and the Fox (0824)")

    record = registry.get(scene.scene_id)
    assert record.board.endswith("(0824)")
    assert record.superseded == ["Interior - Graybank - The Halfling and the Fox"]


def test_forgetting_a_scene_leaves_the_board_alone(tmp_path):
    """`forget` is for a board deleted by hand in game. It cannot delete
    anything itself -- the board list has no API -- and must not pretend to."""
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene))
    assert registry.forget(scene.scene_id) is True
    assert registry.forget(scene.scene_id) is False
    assert boards.Registry.load(tmp_path / "boards.json").records == {}


def test_the_registry_round_trips(tmp_path):
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene))

    again = boards.Registry.load(tmp_path / "boards.json")
    record = again.get(scene.scene_id)
    assert record.town == "Graybank"
    assert record.building_id == "tavern-0014"
    assert record.centroid == pytest.approx(scene.centroid)


def test_a_registry_from_the_future_is_refused(tmp_path):
    path = tmp_path / "boards.json"
    path.write_text(json.dumps({"registry_version": 99, "boards": {}}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="registry_version"):
        boards.Registry.load(path)


def test_two_scenes_keep_separate_records(tmp_path):
    a, b = _scene(), _scene("graybank-tavern-0016", centroid=(50.0, 50.0))
    b.slabs = ["graybank-tavern-0016.slab.txt"]
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(a, _slabs(tmp_path, a))
    registry.record(b, _slabs(tmp_path, b, (2.0, 0.0, 2.0)))
    assert len(boards.Registry.load(tmp_path / "boards.json").records) == 2


def test_renaming_a_record_does_not_claim_a_fresh_paste(tmp_path):
    """A naming scheme can change under a campaign that already has boards in
    it. `record` would recompute the digest from the files on disk and quietly
    relabel a board holding an older build as holding the current one -- which
    is the exact thing the digest exists to notice."""
    scene = _scene()
    registry = boards.Registry.load(tmp_path / "boards.json")
    registry.record(scene, _slabs(tmp_path, scene, (0.0, 0.0, 0.0)))
    rebuilt = _slabs(tmp_path, scene, (5.0, 0.0, 5.0))
    assert registry.status(scene, rebuilt)[0] == boards.STALE

    registry.rename(scene.scene_id, "GRB/T14 The Halfling and the Fox Interior")
    record = boards.Registry.load(tmp_path / "boards.json").get(scene.scene_id)
    assert record.board == "GRB/T14 The Halfling and the Fox Interior"
    assert record.superseded == [scene.board]
    assert record.visits == 1, "a rename is not a visit"
    assert registry.status(scene, rebuilt)[0] == boards.STALE, "still stale"


def test_renaming_something_unrecorded_says_so(tmp_path):
    registry = boards.Registry.load(tmp_path / "boards.json")
    assert registry.rename("nothing-here", "X") is None


# -- pruning ------------------------------------------------------------------
#
# TaleSpire tells you nothing about a board -- no size, no date, no contents,
# no API, and a list that clips at sixteen capitals -- so they accumulate and
# nothing can say which of twenty-two `Unknown Realm N` rows is last week's
# probe. Deleting is manual and has no undo, so the bar for listing something
# here is that it is *provably* disposable.


def _registry(tmp_path, records):
    from citysmith import boards
    reg = boards.Registry(tmp_path / "boards.json", {})
    for r in records:
        reg.records[r.scene_id] = r
    return reg


def test_a_superseded_board_is_prunable(tmp_path):
    """A rebuild does not erase anything -- there is no erase -- so it makes a
    second board and leaves the first under the old name."""
    from citysmith import boards

    reg = _registry(tmp_path, [boards.BoardRecord(
        scene_id="gb-tavern-0014", board="GRB/T14 The Fox Interior",
        superseded=["The Fox - Graybank interior"])])
    names = [p.board for p in boards.prunable(reg)]
    assert names == ["The Fox - Graybank interior"]


def test_an_unnamed_board_is_never_prunable_on_its_name_alone(tmp_path):
    """**Two bugs, and the second one was caught with live work in the frame.**

    The first: the registry only ever tracked *scene* boards. The town boards
    -- East Tradebourne, Graybank, Pelvesthollow -- are named by hand and
    recorded nowhere, so a rule of "prune whatever the registry does not
    claim" listed all three for deletion.

    The second: the fix for that treated `Unknown Realm N` as *provably*
    disposable, on the grounds that the default name is what `newboard` hands
    out to a paste nobody came back to. On 2026-08-26 the board in front of us
    was `Unknown Realm 22`, holding the newest build of a town its owner
    wanted -- so the rule offered up live work, in the one operation with no
    undo. **A default name is the absence of evidence, not evidence of
    absence.** Unnamed boards are a work list, not a delete list; deciding
    means switching to each and looking, which is a thing only a person does.
    """
    from citysmith import boards

    reg = _registry(tmp_path, [])
    seen = ["East Tradebourne", "Pelvesthollow", "Probe - old paste",
            "Unknown Realm 7", "Unknown Realm 22"]

    # Nothing is prunable on a default name: prunable means *provable*, and
    # the only proof available is a supersession the registry recorded.
    assert boards.prunable(reg, seen) == []
    assert boards.unnamed(reg, seen) == ["Unknown Realm 22", "Unknown Realm 7"]

    # The named ones are reported, but as untracked rather than as rubbish.
    assert boards.unclaimed(reg, seen) == [
        "East Tradebourne", "Pelvesthollow", "Probe - old paste"]


def test_a_board_a_scene_still_points_at_is_never_prunable(tmp_path):
    from citysmith import boards

    reg = _registry(tmp_path, [boards.BoardRecord(
        scene_id="gb-tavern-0014", board="Unknown Realm 9")])
    assert boards.prunable(reg, ["Unknown Realm 9"]) == []
    assert [r.board for r in boards.keepers(reg)] == ["Unknown Realm 9"]


def test_a_superseded_board_already_deleted_is_not_recommended_again(tmp_path):
    """Remembering a name is not the same as the board still existing.

    `superseded` is append-only -- it is the record of every name a scene has
    had -- so once the old board is actually deleted the entry stays behind and
    `prune` goes on naming it. Measured on the real campaign 2026-08-26: both
    superseded interior boards had already gone and both were still being
    recommended for deletion.

    That is not merely a stale line. The campaign list is the only way to act
    on a recommendation, the rows move on every rename, and deleting sits one
    click from the play arrow with no undo -- so naming a row that is not there
    is pointing somebody at its neighbour. When a campaign list is supplied it
    is the authority on what exists; without one, nothing can be checked and
    every remembered name is still reported.
    """
    from citysmith import boards

    reg = _registry(tmp_path, [boards.BoardRecord(
        scene_id="gb-tavern-0014", board="GRB/T14 The Fox",
        superseded=["The Fox - Graybank interior"])])

    # Still in the campaign: worth deleting, and named.
    seen = ["GRB/T14 The Fox", "The Fox - Graybank interior"]
    assert [p.board for p in boards.prunable(reg, seen)] == [
        "The Fox - Graybank interior"]

    # Already gone: silence, not a second recommendation.
    assert boards.prunable(reg, ["GRB/T14 The Fox"]) == []

    # No list given, nothing to check against, so the memory still speaks up.
    assert [p.board for p in boards.prunable(reg)] == [
        "The Fox - Graybank interior"]
