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


def _slabs(tmp_path, scene, text="a slab"):
    for name in scene.slabs:
        (tmp_path / name).write_text(text, encoding="utf-8")
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
    registry.record(scene, _slabs(tmp_path, scene, "first build"))

    rebuilt = _slabs(tmp_path, scene, "second build, different geometry")
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
    """Over contents, not mtimes -- a build run twice must not read as stale."""
    scene = _scene()
    first = _slabs(tmp_path, scene, "identical")
    second = _slabs(tmp_path, scene, "identical")
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
    registry.record(scene, _slabs(tmp_path, scene, "rebuilt"),
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
    registry.record(a, _slabs(tmp_path, a, "a"))
    registry.record(b, _slabs(tmp_path, b, "b"))
    assert len(boards.Registry.load(tmp_path / "boards.json").records) == 2
