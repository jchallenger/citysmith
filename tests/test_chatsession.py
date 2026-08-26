"""The chat as a running thing, and the two things it must be honest about.

Two halves again, and again they are tested for opposite properties.

The **session** half is tested for *memory*: the spec survives a turn, the
history records how it got there, undo puts it back, and none of that ever
starts claiming the session can see a TaleSpire board. The last one is the
point. `CLAUDE.md` records copy-out as half solved -- structure and never
terrain, a thin slice at the elevation plane, one marquee per board -- so
"add a fireplace to the room I already pasted" is not something this can do,
and a UI that quietly builds a fresh room instead is the "correct and absent"
failure with a user watching. :attr:`ChatSession.blind_spots` is asserted to be
**never empty**, because the one answer that is always a lie is "I can see
everything".

The **clipboard** half is tested for *restraint*: it must run exactly one
command, that command must be ``ts.ps1 setclip``, and nothing in this module
may drive a click, a keystroke or the camera. The user presses Ctrl+V. Every
test here uses a fake runner, so a full ``pytest -q`` never touches the real
clipboard and every branch runs on a host with no PowerShell at all.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from citysmith import chatsession as cs
from citysmith.slabchat import SlabSpec

TS = pathlib.Path(__file__).resolve().parent.parent / "tools" / "ts.ps1"

#: Every `ts.ps1` verb that touches the game. If one of these ever appears in a
#: command this module issues, the cheap path has stopped being the cheap path.
DRIVING_VERBS = (
    "paste", "hold", "commit", "click", "drop", "clear", "move", "nudge",
    "raise", "lower", "key", "chord", "fly", "orbit", "pan", "rdrag", "elev",
    "zoom", "camera", "cutbox", "plane", "select", "copyout", "newboard",
    "rename", "boards", "focus",
)


class FakeRun:
    """Stands in for :func:`subprocess.run`, and reads what was handed over.

    It parses the temp path back out of the inline PowerShell and reads the
    file *while the call is in flight*, which is the only moment it exists --
    `to_clipboard` deletes it in a `finally`. So this asserts the base64
    genuinely reached PowerShell, rather than that a plausible-looking command
    was assembled.
    """

    def __init__(self, returncode: int = 0, stdout: str = "clipboard set",
                 stderr: str = "", boom: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.payloads: list[str] = []
        self._result = (returncode, stdout, stderr)
        self._boom = boom

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        match = re.search(r"-LiteralPath '([^']+)'", " ".join(args))
        if match:
            self.payloads.append(pathlib.Path(match.group(1)).read_text("ascii"))
        if self._boom is not None:
            raise self._boom
        code, out, err = self._result
        return type("R", (), {"returncode": code, "stdout": out, "stderr": err})()


def _session(**fields) -> cs.ChatSession:
    return cs.ChatSession(SlabSpec(kind="tavern", width=12, depth=9, **fields))


def _handoff(session: cs.ChatSession, palette, run: FakeRun) -> cs.Handoff:
    """A hand-off that cannot reach the real clipboard on any host."""
    return session.hand_off(palette, run=run, windows=True, host="powershell",
                            script=TS)


# -- the session holds the spec, not the board --------------------------------

def test_a_modification_edits_the_spec_rather_than_the_board(catalog_palette):
    """The scope line of `ui-chat-modify`, as an executable statement.

    "Make it two storeys" edits the held spec and rebuilds -- so the geometry
    changes, and the history keeps both versions. "Add a fireplace to the room
    I already pasted" is a different request entirely, and the honest answer to
    it is that this session cannot read a board. Both halves are asserted here
    because either one alone is misleading: a session that rebuilds is useless
    if it implies it edited what is on screen, and a session that admits its
    blindness is useless if it cannot edit anything.
    """
    session = _session()
    before = session.spec
    was = session.build(catalog_palette)

    step = session.apply({"storeys": 2, "note": "Two storeys."},
                         message="make it two storeys")

    # The spec moved, and so did the geometry it builds.
    assert session.spec.storeys == 2
    assert session.spec is not before and before.storeys == 1
    assert session.build(catalog_palette).placements != was.placements

    # And the session kept how it got there, rather than only where it is.
    assert [s.spec for s in session.history] == [before, step.spec]
    assert session.history[-1].message == "make it two storeys"

    # What it did NOT do is touch a board, and it says so without being asked.
    boards = [s for s in session.blind_spots if s.subject == "the board"]
    assert boards, "a session that never mentions the board is claiming to see it"
    assert "copy-out" in boards[0].because
    assert "cannot see the board" in session.cannot_see()


def test_the_current_spec_is_always_the_last_step(catalog_palette):
    """One source of truth. A second copy of the spec is a second copy to drift."""
    session = _session()
    for edits in ({"storeys": 2}, {"width": 20}, {"furnish": "bare"}):
        session.apply(edits)
        assert session.spec == session.history[-1].spec
    session.undo()
    assert session.spec == session.history[-1].spec


def test_the_history_starts_with_the_opening_spec_and_is_never_empty():
    opening = SlabSpec(kind="temple", width=15)
    session = cs.ChatSession(opening)
    assert session.history[0].spec == opening
    assert not session.can_undo
    assert session.undo() is None
    assert len(session.history) == 1


def test_undo_goes_back_one_step_and_stops_at_the_opening():
    session = _session()
    session.apply({"storeys": 2})
    session.apply({"storeys": 3})
    assert session.spec.storeys == 3

    undone = session.undo()
    assert undone is not None and undone.spec.storeys == 3
    assert session.spec.storeys == 2
    session.undo()
    assert session.spec.storeys == 1
    assert session.undo() is None, "the opening spec is not undoable"


def test_undo_does_not_unpaste_what_was_handed_over(catalog_palette):
    """There is no erase in TaleSpire, so a transcript cannot rewrite a board.

    `boards.py` follows the same rule for a STALE scene: a board is somewhere
    the party has been, and re-pasting cannot replace what is already on it.
    An undo that quietly dropped the hand-off record would leave the session
    claiming a board holds something it does not.
    """
    session = _session()
    session.apply({"storeys": 2})
    _handoff(session, catalog_palette, FakeRun())
    assert len(session.handed_off) == 1

    session.undo()
    assert session.spec.storeys == 1
    assert len(session.handed_off) == 1, "undo un-pasted a board"
    assert any(s.subject == "the older build" for s in session.blind_spots)
    # And it is still on the page, rather than orphaned off a dead index.
    assert "since been undone" in session.transcript()


# -- what it cannot see -------------------------------------------------------

def test_the_session_never_reports_that_it_can_see_everything():
    """The one answer that is always wrong.

    Checked on a fresh session, a busy one, and one loaded from junk, because
    an empty list here would render as a UI panel saying nothing is missing.
    """
    for session in (cs.ChatSession(), _session(), cs.ChatSession.from_dict("junk")):
        assert session.blind_spots, "a session with no blind spots is lying"
        for spot in session.blind_spots:
            assert spot.subject and spot.cannot and spot.because, spot


def test_a_hand_off_adds_the_paste_it_cannot_observe(catalog_palette):
    session = _session()
    assert not any(s.subject == "the paste" for s in session.blind_spots)

    _handoff(session, catalog_palette, FakeRun())

    paste = [s for s in session.blind_spots if s.subject == "the paste"]
    assert paste, "handing geometry over and then not mentioning it is the bug"
    assert "Ctrl+V" in paste[0].because
    assert "1 build" in paste[0].cannot


def test_editing_after_a_hand_off_says_the_board_may_hold_the_older_build(
        catalog_palette):
    session = _session()
    _handoff(session, catalog_palette, FakeRun())
    assert not any(s.subject == "the older build" for s in session.blind_spots)

    session.apply({"storeys": 2}, message="make it two storeys")

    older = [s for s in session.blind_spots if s.subject == "the older build"]
    assert older, "the board is now out of date and nothing said so"
    assert "no erase" in older[0].because
    assert "1 storey" in older[0].because, "it should say what was pasted"


def test_the_blind_spots_are_a_different_channel_from_unapplied():
    """Three failures, three channels, and they must not be merged.

    `problems` is what Python repaired, `unapplied` is what the spec has no
    field for, and `blind_spots` is what this session cannot know about the
    world. A UI that pooled them would tell a user that "put the bar on the
    north wall" and "I cannot read your board" are the same kind of problem.
    """
    session = _session()
    step = session.apply({
        "width": 400,
        "unsupported": ["put the bar on the north wall"],
    })
    assert any("width" in p for p in step.problems)
    assert step.unapplied == ("put the bar on the north wall",)
    subjects = {s.subject for s in session.blind_spots}
    assert "the board" in subjects
    assert not any("bar" in s.cannot or "bar" in s.because
                   for s in session.blind_spots)


def test_the_transcript_shows_every_turn_and_ends_with_what_it_cannot_see(
        catalog_palette):
    session = _session()
    session.apply({"storeys": 2, "note": "Two storeys."},
                  message="make it two storeys")
    _handoff(session, catalog_palette, FakeRun())
    text = session.transcript()
    assert "make it two storeys" in text
    assert "Two storeys." in text
    assert "handed over" in text
    assert text.rstrip().endswith(session.cannot_see().splitlines()[-1])


# -- the session survives everything ------------------------------------------

def test_a_garbage_edit_leaves_a_buildable_spec_and_a_step_that_says_so(
        catalog_palette):
    session = _session()
    before = session.spec
    step = session.apply("make it enormous")          # not a dict at all
    assert step.spec == before
    assert step.problems
    assert session.build(catalog_palette).placements


def test_a_session_round_trips_through_json(catalog_palette):
    session = _session(name="The Fox")
    session.apply({"storeys": 2, "note": "Two storeys."}, message="two storeys")
    _handoff(session, catalog_palette, FakeRun())

    back = cs.ChatSession.from_dict(json.loads(json.dumps(session.to_dict())))
    assert back.spec == session.spec
    assert [s.spec for s in back.history] == [s.spec for s in session.history]
    assert [s.message for s in back.history] == [s.message for s in session.history]
    assert [h.spec for h in back.handed_off] == [h.spec for h in session.handed_off]
    assert back.blind_spots == session.blind_spots


@pytest.mark.parametrize("junk", [None, [], "spec", 7, {"history": "no"},
                                  {"history": [1, 2]}, {"handed_off": 3},
                                  {"handed_off": ["junk", 5]}])
def test_a_session_loads_from_anything_at_all(junk, catalog_palette):
    """It reads a value the browser was holding. It cannot be allowed to raise."""
    session = cs.ChatSession.from_dict(junk)
    assert isinstance(session.spec, SlabSpec)
    assert session.history
    assert session.build(catalog_palette).placements


# -- the clipboard, and nothing else ------------------------------------------

def test_a_chat_slab_goes_to_the_clipboard_not_through_the_game(catalog_palette):
    """The whole of `ui-chat-clipboard`, as one statement about one command.

    A town is 8 to 135 chunks and has to be driven: camera straight down so the
    ray-hit anchor cannot slide, every chunk at one cursor cell, in paste
    order. A single small slab needs none of it. So this asserts what is
    *absent* as hard as what is present -- one command, `ts.ps1 setclip`, and
    not one of the verbs that touches the game.
    """
    session = _session()
    run = FakeRun()
    handoff = _handoff(session, catalog_palette, run)

    assert handoff.ok and handoff.how == cs.CLIPBOARD
    assert len(run.calls) == 1, "the cheap path runs exactly one command"

    line = " ".join(run.calls[0])
    assert "ts.ps1" in line and "setclip" in line
    for verb in DRIVING_VERBS:
        assert not re.search(rf"\b{verb}\b", line), (
            f"the clipboard path issued '{verb}'; the user presses Ctrl+V"
        )

    # The slab really got to PowerShell, and it is the slab we built.
    from citysmith.slab import encode
    assert run.payloads == [encode(session.build(catalog_palette))]
    assert handoff.text == run.payloads[0]
    assert "Ctrl+V" in handoff.instruction()


def test_the_base64_never_goes_on_the_command_line(catalog_palette):
    """The measured limit, and why the file route is not optional.

    `CreateProcess`'s command line tops out at 32,767 characters -- bisected on
    this machine against `powershell.exe`, a payload of 32,588 started and
    32,589 raised `WinError 206, "The filename or extension is too long"`. That
    budget is shared with the interpreter path, the script path and the flags,
    so it shrinks when the repo moves. And a legal chat slab can be bigger than
    the whole of it: the compressed cap is 30,720 bytes, which is 40,960
    characters of base64.

    So the command line has to be *independent* of the slab, and that is what
    is asserted -- the same command for 200 characters and for 40,000.
    """
    assert cs.MAX_SLAB_CHARS > cs.MAX_COMMAND_LINE, (
        "if a maximum slab fits on a command line this test is measuring the "
        "wrong thing, but the file route is still right"
    )
    lines = []
    for size in (200, cs.MAX_SLAB_CHARS):
        run = FakeRun()
        handoff = cs.to_clipboard("A" * size, run=run, windows=True,
                                  host="powershell", script=TS)
        assert handoff.ok
        assert run.payloads == ["A" * size], "the payload went somewhere else"
        line = " ".join(run.calls[0])
        assert "A" * 200 not in line, "the base64 is on the command line"
        lines.append(line)

    # 200 characters and 40,960 produce the same command to within the width of
    # a temp filename -- so the command line is a constant and the slab travels
    # entirely in the file.
    assert abs(len(lines[0]) - len(lines[1])) < 32, (
        "the command grew with the slab; the base64 is on the command line"
    )
    assert max(len(line) for line in lines) < 1000


def test_a_payload_past_the_command_line_ceiling_still_gets_through():
    """42,000 characters -- past the 32,588 that CreateProcess would take."""
    run = FakeRun()
    payload = "AB12+/" * 7000
    assert len(payload) > cs.MAX_COMMAND_LINE
    handoff = cs.to_clipboard(payload, run=run, windows=True, host="powershell",
                              script=TS)
    assert handoff.ok and handoff.chars == len(payload)
    assert run.payloads == [payload]


def test_off_windows_it_says_so_and_hands_back_the_base64():
    """The generate half is cross-platform; this half is not, and it must not
    fail obscurely. The caller gets the slab either way."""
    run = FakeRun()
    handoff = cs.to_clipboard("SLABDATA", run=run, windows=False)
    assert handoff.how == cs.MANUAL and not handoff.ok
    assert handoff.text == "SLABDATA"
    assert "Windows only" in handoff.detail
    assert not run.calls, "it must not even try"
    assert "Copy the 8 characters yourself" in handoff.instruction()


@pytest.mark.parametrize("run,expect", [
    (FakeRun(returncode=1, stderr="ts.ps1 blew up"), "blew up"),
    (FakeRun(boom=OSError("no such file")), "could not run"),
    (FakeRun(boom=TimeoutError("took too long")), "could not run"),
])
def test_a_failed_clipboard_call_still_hands_back_the_base64(run, expect):
    """A chat that produced a perfectly good slab and then lost it to a broken
    shell has failed at the last inch. Never an exception, never nothing."""
    handoff = cs.to_clipboard("SLABDATA", run=run, windows=True,
                              host="powershell", script=TS)
    assert handoff.how == cs.MANUAL and handoff.text == "SLABDATA"
    assert expect in handoff.detail


def test_a_missing_script_or_shell_is_reported_rather_than_raising():
    missing = cs.to_clipboard("SLABDATA", windows=True, host="powershell",
                              script="no/such/ts.ps1", run=FakeRun())
    assert missing.how == cs.MANUAL and "missing" in missing.detail
    assert missing.text == "SLABDATA"

    # `host=None` on a host with no PowerShell at all.
    noshell = cs.to_clipboard("SLABDATA", windows=True, script=TS,
                              run=FakeRun())
    assert noshell.text == "SLABDATA"
    assert noshell.how in (cs.CLIPBOARD, cs.MANUAL)
    if noshell.how == cs.MANUAL:
        assert "PowerShell" in noshell.detail


def test_the_module_names_only_the_two_read_only_ts_commands():
    """A structural guard on the tracker's one hard rule: drive no input.

    `to_clipboard` and `talespire_running` are the only shell-outs here, and
    `setclip` and `client` are the only `ts.ps1` verbs either of them may
    name. Both are read-only with respect to the board -- one sets the
    clipboard, one asks whether the process exists.
    """
    source = pathlib.Path(cs.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]          # past the module docstring
    for verb in DRIVING_VERBS:
        assert f'"{verb}"' not in body and f"'{verb}'" not in body, verb
    assert "setclip" in body, "the one verb it is allowed went missing"


def test_the_handoff_says_whether_it_is_on_the_clipboard_without_a_second_flag():
    """`ok` is derived from `how`, so the two cannot drift apart."""
    assert cs.Handoff(text="x", how=cs.CLIPBOARD, detail="").ok
    assert not cs.Handoff(text="x", how=cs.MANUAL, detail="").ok
    assert cs.Handoff(text="abc", how=cs.MANUAL, detail="").chars == 3


def test_importing_the_module_does_not_import_anthropic():
    """`say()` is the only networked thing here and it imports lazily, the same
    way `slabchat.edit` does. Everything else -- holding a spec, undoing,
    building, handing over -- has to work with `anthropic` uninstalled."""
    import os
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import citysmith.chatsession; "
         "print('anthropic' in sys.modules)"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_talespire_running_answers_none_rather_than_guessing(monkeypatch):
    """Advisory, never a precondition: the clipboard works whether or not the
    game is up, and refusing there would invent a failure this path lacks."""
    monkeypatch.setattr(cs.os, "name", "posix")
    assert cs.talespire_running() is None
