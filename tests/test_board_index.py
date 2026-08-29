"""What every board in the campaign holds, not just the ones a scene owns.

`test_boards.py` covers the scene registry. This covers the index beside it,
and the reason it exists is worth stating once: **TaleSpire's campaign list is
a column of names and nothing else.** No contents, no asset count, no size, no
date. A board holding a finished town and one holding last week's throwaway are
the same row, which is why deleting is dangerous and why `prunable` could only
ever name the boards a scene had explicitly superseded -- every other bucket in
`prune` is the absence of a record dressed up as advice.

So the index is written at paste time and read back later. These tests are
about it staying honest about what it does not know.
"""

from __future__ import annotations

import json

import pytest

from citysmith import boards


def _registry(tmp_path, **entries) -> boards.Registry:
    r = boards.Registry(tmp_path / "boards.json", {})
    for name, kw in entries.items():
        r.note(name, **kw)
    return r


# ---------------------------------------------------------------- the file

def test_a_version_1_registry_loads_with_an_empty_index(tmp_path):
    """The index arrived after the scene records, and a v1 file is a true
    record of those. Refusing it would throw away the one thing in this project
    that cannot be regenerated from anything on disk."""
    p = tmp_path / "boards.json"
    p.write_text(json.dumps({
        "registry_version": 1,
        "campaign": "",
        "boards": {"s": {"scene_id": "s", "board": "B", "visits": 1}},
    }), encoding="utf-8")

    r = boards.Registry.load(p)
    assert r.records["s"].board == "B"
    assert r.index == {}


def test_a_version_from_the_future_is_still_refused(tmp_path):
    p = tmp_path / "boards.json"
    p.write_text(json.dumps({"registry_version": 99, "boards": {}}),
                 encoding="utf-8")
    with pytest.raises(ValueError):
        boards.Registry.load(p)


def test_the_index_survives_a_round_trip(tmp_path):
    r = _registry(tmp_path, **{})
    r.note("East Tradebourne", holds="town",
           source="out/tradebourne-v2/layout.json",
           folder="Ready to publish", stem="et", chunks=114, assets=408853,
           note="the big one")
    r.save()

    back = boards.Registry.load(r.path)
    entry = back.index["East Tradebourne"]
    assert entry.holds == "town"
    assert entry.chunks == 114 and entry.assets == 408853
    assert entry.folder == "Ready to publish"
    assert entry.note == "the big one"
    assert entry.recorded, "an entry has to say when it was made"


# ---------------------------------------------------------------- entries

def test_noting_a_board_twice_replaces_rather_than_appends(tmp_path):
    """A board is one thing at a time. Pasting a second town onto it does not
    make it two boards, and an index that grew a row per paste would be a log
    pretending to be an index."""
    r = _registry(tmp_path)
    r.note("Scratch", holds="probe", source="first")
    r.note("Scratch", holds="town", source="second")
    assert len(r.index) == 1
    assert r.index["Scratch"].holds == "town"
    assert r.index["Scratch"].source == "second"


def test_a_note_is_carried_across_a_re_paste(tmp_path):
    """Whatever somebody wrote down about a board is the part a rebuild is
    least likely to know, so it survives one."""
    r = _registry(tmp_path)
    r.note("Scratch", holds="town", note="the seed 33 build, do not lose")
    r.note("Scratch", holds="town", source="rebuilt")
    assert r.index["Scratch"].note == "the seed 33 build, do not lose"


def test_a_probe_is_disposable_and_a_town_is_not(tmp_path):
    r = _registry(tmp_path)
    assert r.note("P", holds="probe").disposable
    assert not r.note("T", holds="town").disposable
    assert not r.note("O", holds="other").disposable


def test_disposable_can_be_overridden_both_ways(tmp_path):
    """It is a claim by whoever recorded it, not a deduction -- a probe worth
    keeping and a town worth throwing away both exist."""
    r = _registry(tmp_path)
    assert not r.note("P", holds="probe", disposable=False).disposable
    assert r.note("T", holds="town", disposable=True).disposable


def test_an_unknown_holds_value_is_refused(tmp_path):
    r = _registry(tmp_path)
    with pytest.raises(ValueError):
        r.note("X", holds="dungeon")


# ---------------------------------------------------------------- reconcile

def _seen(*rows) -> list[boards.SeenBoard]:
    return boards.parse_seen(rows)


def test_reconcile_reports_the_three_states(tmp_path):
    """Matched, gone and unrecorded are all ordinary states, and saying which
    is more use than a pass or a fail. The index is a record of pastes; the
    listing is a transcription of a screen; they part company for real
    reasons."""
    r = _registry(tmp_path)
    r.note("Graybank", holds="town")
    r.note("Old probe", holds="probe")

    got = boards.reconcile(r, _seen("Graybank", "Sedgewater"))
    assert [e.board for e in got.matched] == ["Graybank"]
    assert [e.board for e in got.missing] == ["Old probe"]
    assert [b.name for b in got.unrecorded] == ["Sedgewater"]
    assert got.coverage == pytest.approx(0.5)


def test_reconcile_keeps_the_folder_a_board_was_seen_in(tmp_path):
    """The unrecorded bucket is a work list, and where a board sits is half of
    deciding what to do about it -- an unnamed board loose in the campaign is a
    shrug, and the same board under `Ready to publish` is a contradiction."""
    r = _registry(tmp_path)
    got = boards.reconcile(r, _seen("Ready to publish:", "  Sedgewater"))
    assert got.unrecorded[0].folder == "Ready to publish"


def test_coverage_of_an_empty_campaign_is_not_a_division_by_zero(tmp_path):
    assert boards.reconcile(_registry(tmp_path), []).coverage == 0.0


# ---------------------------------------------------------------- disposable

def test_disposable_is_the_only_bucket_that_is_a_record(tmp_path):
    r = _registry(tmp_path)
    r.note("Probe - wall", holds="probe")
    r.note("Graybank", holds="town")
    assert [e.board for e in boards.disposable(r)] == ["Probe - wall"]


def test_a_board_a_scene_claims_is_never_listed_as_disposable(tmp_path):
    """The scene registry is the older claim, and an index entry that
    contradicts it loses. Otherwise a mistyped `--disposable` on an interior
    would put somebody's session on a delete list."""
    r = _registry(tmp_path)
    r.records["s"] = boards.BoardRecord(scene_id="s", board="GRB/T14 Tavern")
    r.note("GRB/T14 Tavern", holds="probe", disposable=True)
    assert boards.disposable(r) == []


def test_disposable_is_filtered_by_what_is_actually_on_screen(tmp_path):
    """A board already deleted by hand is not a recommendation to delete it
    again; `reconcile` reports that separately as gone."""
    r = _registry(tmp_path)
    r.note("Probe - gone", holds="probe")
    r.note("Probe - here", holds="probe")
    assert [e.board for e in boards.disposable(r, ["Probe - here"])] == \
        ["Probe - here"]


# ---------------------------------------------------------------- renames

def test_renaming_a_board_moves_its_index_entry(tmp_path):
    """The index is keyed on the name and TaleSpire has no board id, so a
    rename is the one edit that silently orphans an entry. Every other
    difference shows up in `reconcile`; this one has to be told."""
    r = _registry(tmp_path)
    r.records["s"] = boards.BoardRecord(scene_id="s", board="Unknown Realm 4")
    r.note("Unknown Realm 4", holds="scene", source="s")

    entry = r.rename_board("Unknown Realm 4", "GRB/T14 Tavern")
    assert entry is not None
    assert "Unknown Realm 4" not in r.index
    assert r.index["GRB/T14 Tavern"].board == "GRB/T14 Tavern"
    assert r.records["s"].board == "GRB/T14 Tavern"
    assert "Unknown Realm 4" in r.records["s"].superseded


def test_renaming_a_board_nothing_knows_about_is_not_an_error(tmp_path):
    assert _registry(tmp_path).rename_board("nope", "still nope") is None


def test_dropping_a_board_forgets_it_and_says_whether_it_did(tmp_path):
    r = _registry(tmp_path)
    r.note("Scratch", holds="probe")
    assert r.drop("Scratch") is True
    assert r.drop("Scratch") is False


# ---------------------------------------------------------------- the CLI

def test_cli_holds_matches_the_registry():
    """`cli` builds its parser at import time and imports `boards` inside the
    command, so the choice list is repeated rather than shared. This is what
    keeps the two copies honest."""
    from citysmith import cli

    assert tuple(cli._HOLDS) == boards.HOLDS


# ---------------------------------------------------------------- the drivers
#
# The PowerShell half of this project cannot be imported, driven or unit
# tested -- it talks to a game over synthetic input. What it *can* be held to
# is that the lines which make the index self-filling, and the guard that keeps
# a delete loop from clicking blind, are still in the file. `uiserver.py`
# already carries a test of this shape (the word `subprocess` must not appear
# in it), and the reason is the same: a one-line deletion in a script nothing
# runs in CI is otherwise invisible until it costs something.

import pathlib as _pathlib

_TOOLS = _pathlib.Path(__file__).resolve().parents[1] / "tools"


def test_the_paste_drivers_record_the_board_they_pasted():
    """An index nobody fills is another thing to remember, and this project's
    own record of a nineteen-board prune done off screenshots is what that
    costs. Both drivers write an entry at paste time, which is the only moment
    anything knows what went on."""
    panel = (_TOOLS / "panel_review.ps1").read_text(encoding="utf-8")
    assert "boards note" in panel and "--holds probe" in panel
    assert "--disposable" in panel,         "a probe board is made to be looked at once; say so while it is known"

    review = (_TOOLS / "review.ps1").read_text(encoding="utf-8")
    assert "boards note" in review and "--holds town" in review
    assert "NOT INDEXED" in review,         "a tiled paste with no -Board must say it went unrecorded, not go quiet"


def test_the_delete_loop_guards_on_the_dialog():
    """Without the guard, a delete that does not open leaves the next two
    clicks landing on the board behind the campaign panel. It fired for real on
    the nineteenth board of a twenty-board run."""
    script = (_TOOLS / "delete_boards.ps1").read_text(encoding="utf-8")
    assert "no Delete Board dialog" in script, "the guard must still be there"
    assert "throw" in script, "and it must stop rather than carry on"
    assert "Mandatory=$true" in script and "$Expect" in script,         "-Expect is the caller having checked the list, and is not optional"
