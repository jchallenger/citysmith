"""The tracker has to catch a claim that is not true, or it is just a list.

Yards were designed, written up in two documents, twice described as "waiting
on nothing", and deferred by three consecutive passes -- because a paragraph
saying a thing is unbuilt looks exactly like one saying it is built. So a task
carries evidence and `check` imports it.
"""

from __future__ import annotations

import json

import pytest

from citysmith import tasks as T


def test_a_task_marked_done_whose_evidence_is_missing_is_reported():
    task = T.Task(id="x", what="something", state="done",
                  evidence="citysmith.build.definitely_not_a_real_symbol")
    status, detail = task.check()
    assert status == T.MISSING
    assert "no 'definitely_not_a_real_symbol'" in detail


def test_a_task_marked_done_whose_evidence_exists_is_fine():
    task = T.Task(id="x", what="yards", state="done",
                  evidence="citysmith.build._lay_yards")
    assert task.check()[0] == T.OK


def test_a_task_still_open_whose_evidence_already_exists_is_stale():
    """The other direction, and it matters just as much: work that landed
    without the record catching up is how a backlog stops being trusted."""
    task = T.Task(id="x", what="yards", state="open",
                  evidence="citysmith.build._lay_yards")
    assert task.check()[0] == T.STALE


def test_a_task_with_no_evidence_says_so_rather_than_passing():
    task = T.Task(id="x", what="something vague", state="done")
    status, detail = task.check()
    assert status == T.UNCHECKABLE
    assert "no evidence" in detail


def test_evidence_can_name_a_test():
    exists = T.Task(id="x", what="", state="done",
                    evidence="test:test_evidence_can_name_a_test")
    assert exists.check()[0] == T.OK
    missing = T.Task(id="y", what="", state="done",
                     evidence="test:test_no_such_test_anywhere")
    assert missing.check()[0] == T.MISSING


def test_a_broken_module_path_never_raises():
    """`check` runs over every task; one bad entry must not take the report
    down with it."""
    for bad in ("", "nomodule", "no.such.module.at.all", "citysmith"):
        status, _ = T.Task(id="x", what="", state="done", evidence=bad).check()
        assert status in (T.MISSING, T.UNCHECKABLE)


def test_the_store_round_trips(tmp_path):
    items = []
    T.add(items, "Do a thing", id="thing", doc="docs/x.md",
          evidence="citysmith.build._lay_yards", tags=["a"])
    path = T.save(items, tmp_path / "tasks.json")
    again = T.load(path)
    assert [t.id for t in again] == ["thing"]
    assert again[0].evidence == "citysmith.build._lay_yards"
    assert again[0].tags == ["a"]
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["what"] == "Do a thing"


def test_ids_do_not_collide():
    items = []
    a = T.add(items, "Same text")
    b = T.add(items, "Same text")
    assert a.id != b.id


def test_the_shipped_backlog_is_honest():
    """The repo's own `tasks.json`, checked. This is the test that fails when
    someone marks work done that is not, or lands work without updating the
    record -- which is the whole point of keeping the file."""
    items = T.load()
    if not items:
        pytest.skip("no tasks.json in this checkout")
    problems = []
    for t in items:
        status, detail = t.check()
        if status in (T.MISSING, T.STALE):
            problems.append(f"{t.id} [{t.state}]: {detail}")
    assert not problems, "tasks.json disagrees with the code:\n  " + "\n  ".join(problems)
