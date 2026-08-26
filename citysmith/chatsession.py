r"""The chat as a running thing: what it holds, what it hands over, and what
it cannot see.

:mod:`citysmith.slabchat` is a pure function of one spec -- a sentence goes in,
a slab comes out, offline and byte-identical every time. It deliberately holds
no state. This module is the state: the spec across turns, the history of how
it got there, undo, and the two sentences the UI must be able to render before
any of that is honest.

**Why this is not in `slabchat.py`.** That module's whole promise is that the
generation half is pure and offline, and its suite proves it in a subprocess
with ``anthropic`` unimportable and ``socket.socket`` replaced by a raiser.
This one holds mutable session state and *shells out to PowerShell*. Keeping
them apart means the claim "nothing below :func:`slabchat.edit` reaches the
network" stays a property of a file rather than of a careful reading. The
clipboard hand-off lives here rather than in a third module because it is the
act that *creates* the blind spot below: the moment geometry leaves this
session, the session stops knowing what is true.

What it cannot see, and why that is a data structure
----------------------------------------------------

The tracker entry this module closes says it plainly:

    'Make it two storeys', 'put the bar on the north wall' -- edits the held
    spec and rebuilds. **'Add a fireplace to the room I already pasted' does
    not, and the UI must not pretend otherwise.**

The reason is measured, not squeamish. `CLAUDE.md` records copy-out as **half
solved**: driven synthetically it returns *structure* and never *terrain*, it
behaves as a thin horizontal slice at the elevation plane rather than a volume,
and only one marquee could be driven per board. So there is no route by which
this session can read a TaleSpire board back. "Modify an existing board"
therefore means *modify the thing this session built*, and the difference has
to be visible.

:attr:`ChatSession.blind_spots` is that difference, as a **queryable property**
and not a comment, because this project has shipped the other thing: fences
were built, reviewed over two sessions and written up while being absent from
every screenshot anyone looked at, and nothing in the report said so. A
limitation that lives only in a docstring is the same failure with a shorter
blast radius. :meth:`ChatSession.cannot_see` renders it; a test asserts the
list is **never empty**, because "I can see everything" is the one answer that
is always wrong.

Keep it apart from :attr:`citysmith.slabchat.Turn.unapplied`, which is a
different thing that fails differently:

``Turn.problems``      what Python repaired in the model's edits.
``Turn.unapplied``     what the **spec** cannot express -- no field exists.
``ChatSession.blind_spots``  what this session cannot know about the **board**.

The first two are about a sentence. This one is about the world.

Getting it into the game
------------------------

A town is 8 to 135 chunks and has to be *driven*: camera pitched straight down
so the ray-hit anchor cannot slide, every chunk at one cursor cell, in the
order `paste-order.txt` gives. A single small slab needs none of that.
:func:`to_clipboard` puts the base64 on the clipboard through ``ts.ps1
setclip`` and stops. **The user presses Ctrl+V.** No synthetic input, no camera
discipline, no anchor rules, and nothing can go wrong that the user did not do
-- which also means nothing here observes whether it happened, and that is the
third blind spot.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from .slab import MAX_COMPRESSED_BYTES, Slab, encode
from .slabchat import SlabSpec, Turn, apply_edits, build as build_slab, plan_for

CHATSESSION_VERSION = 1


# -- one turn, kept ------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One committed turn of the chat, and the spec it left behind.

    A :class:`~citysmith.slabchat.Turn` is the *result* of an edit and has no
    idea what was asked for -- :func:`~citysmith.slabchat.apply_edits` only
    ever sees a dict of fields. A transcript needs the sentence beside the
    result, so a step is a turn plus the message that caused it.

    Frozen for the same reason :class:`~citysmith.slabchat.SlabSpec` is: the
    history is the record of what happened, and a record that can be edited in
    place is not one.
    """

    spec: SlabSpec
    #: What the user said. Empty for the opening step and for a programmatic
    #: edit that came with no sentence.
    message: str = ""
    #: The model's one line for the transcript.
    note: str = ""
    problems: tuple[str, ...] = ()
    unapplied: tuple[str, ...] = ()
    at: str = ""

    @classmethod
    def of(cls, turn: Turn, message: str = "") -> "Step":
        return cls(
            spec=turn.spec, message=_clean(message), note=turn.note,
            problems=turn.problems, unapplied=turn.unapplied, at=_now(),
        )

    def as_turn(self) -> Turn:
        """Back to the :mod:`slabchat` type, so a UI has one renderer."""
        return Turn(spec=self.spec, note=self.note, problems=self.problems,
                    unapplied=self.unapplied)

    @property
    def clean(self) -> bool:
        return not self.problems and not self.unapplied

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "message": self.message,
            "note": self.note,
            "problems": list(self.problems),
            "unapplied": list(self.unapplied),
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Step":
        """Never raises -- see :meth:`ChatSession.from_dict`."""
        if not isinstance(data, dict):
            return cls(spec=SlabSpec(), at=_now())
        return cls(
            spec=SlabSpec.from_dict(data.get("spec")),
            message=_clean(data.get("message")),
            note=_clean(data.get("note")),
            problems=_strings(data.get("problems")),
            unapplied=_strings(data.get("unapplied")),
            at=_clean(data.get("at")),
        )


@dataclass(frozen=True)
class Handed:
    """A build that left this session. The only thing that can be on a board.

    Recorded whether or not the clipboard call succeeded, because
    :class:`Handoff` always returns the base64 to the caller -- so by the time
    one of these exists the user has the geometry in their hands one way or
    another, and this session can no longer claim to know where it went.
    """

    #: Index into :attr:`ChatSession.history` at the moment of the hand-off.
    #: **Not a live reference**: undo can shorten the history past it, which is
    #: exactly the situation this record exists to report.
    step: int
    spec: SlabSpec
    chars: int
    how: str
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "spec": self.spec.to_dict(),
                "chars": self.chars, "how": self.how, "at": self.at}

    @classmethod
    def from_dict(cls, data: Any) -> "Handed":
        if not isinstance(data, dict):
            return cls(step=0, spec=SlabSpec(), chars=0, how=MANUAL, at=_now())
        return cls(
            step=_int(data.get("step")),
            spec=SlabSpec.from_dict(data.get("spec")),
            chars=_int(data.get("chars")),
            how=_clean(data.get("how")) or MANUAL,
            at=_clean(data.get("at")),
        )


# -- what it cannot see --------------------------------------------------------

@dataclass(frozen=True)
class BlindSpot:
    """One thing this session does not know, and why it does not know it.

    ``because`` is a required field on purpose. "The UI cannot read the board"
    invites somebody to go and make it read the board; "copy-out returns
    structure and never terrain, behaves as a thin slice at the elevation
    plane, and only one marquee can be driven per board" tells them what they
    would be signing up for.
    """

    #: What is not visible, in two or three words. The UI's row label.
    subject: str
    #: The limitation, as a sentence starting with a verb.
    cannot: str
    #: The measurement or the mechanism behind it.
    because: str

    def line(self) -> str:
        return f"{self.subject}: cannot {self.cannot} -- {self.because}"


#: True whatever has happened in the session. Nothing here reads a board, and
#: no amount of chatting will change that, so these are stated up front rather
#: than raised when somebody asks for something impossible.
STANDING_BLIND_SPOTS: tuple[BlindSpot, ...] = (
    BlindSpot(
        subject="the board",
        cannot="read back what is on a TaleSpire board",
        because=(
            "copy-out is half solved -- driven synthetically it returns "
            "structure and never terrain, behaves as a thin horizontal slice "
            "at the elevation plane rather than a volume, and only one marquee "
            "can be driven per board"
        ),
    ),
    BlindSpot(
        subject="anything built elsewhere",
        cannot="see geometry this session did not generate",
        because=(
            "a board also holds hand-placed props, earlier pastes and whatever "
            "else the campaign put there, and none of it exists in this spec"
        ),
    ),
)


# -- the session ---------------------------------------------------------------

class ChatSession:
    """The spec across turns, its history, undo, and its blind spots.

    **The history is the state.** ``history[-1].spec`` *is* the current spec --
    there is no second copy that could drift out of step with it, which is the
    shape of bug this project keeps finding in its own metrics ("metrics must
    read the artifact, not the plan"). ``history[0]`` is the spec the session
    opened with, so it is never empty and undo always has somewhere to land.
    """

    def __init__(self, spec: SlabSpec | None = None) -> None:
        opening = spec if isinstance(spec, SlabSpec) else SlabSpec()
        self._history: list[Step] = [Step(spec=opening, at=_now())]
        self._handed: list[Handed] = []

    # -- reading --------------------------------------------------------------

    @property
    def spec(self) -> SlabSpec:
        return self._history[-1].spec

    @property
    def history(self) -> tuple[Step, ...]:
        """Every committed step, oldest first. Never empty."""
        return tuple(self._history)

    @property
    def handed_off(self) -> tuple[Handed, ...]:
        """Builds that left this session, oldest first.

        Undo never touches this. There is no erase in TaleSpire, so a paste
        that happened stays happened whatever the transcript says afterwards --
        the same rule `boards.py` follows when it refuses to overwrite a STALE
        board record.
        """
        return tuple(self._handed)

    @property
    def can_undo(self) -> bool:
        return len(self._history) > 1

    # -- editing --------------------------------------------------------------

    def apply(self, edits: Any, message: str = "") -> Step:
        """Apply a model's edits to the held spec. **Never raises.**

        This is the offline half: the caller already has the model's tool
        output (or is driving the session from a test or a script) and the
        network is not involved. Everything hostile about the input is
        :func:`~citysmith.slabchat.apply_edits`'s problem, and it does not
        raise either -- the worst case is a step whose spec equals the one
        before it, plus a note saying why.
        """
        step = Step.of(apply_edits(self.spec, edits), message)
        self._history.append(step)
        return step

    def say(self, message: str, **kwargs) -> Step:
        """One conversational turn, through Claude. Networked.

        Raises :class:`citysmith.ai.AIError` when the model layer is
        unavailable -- the same contract :func:`citysmith.slabchat.edit` has,
        and for the same reason: an unusable *response* is a spec that did not
        change, but a missing API key is something the user has to fix.

        **No conversation history is sent by default**, and that is deliberate
        rather than an omission. :func:`~citysmith.slabchat.edit` puts the
        *entire current spec* in front of the model every turn, so "make it
        bigger" resolves against `width: 12` rather than against a remembered
        sentence. Replaying prior turns would also mean replaying assistant
        tool calls without their results, which is not a valid transcript.
        Pass ``history=`` explicitly if a caller wants one anyway.
        """
        from .slabchat import edit          # imports `anthropic` only when called

        step = Step.of(edit(self.spec, message, **kwargs), message)
        self._history.append(step)
        return step

    def undo(self) -> Step | None:
        """Drop the last step and go back to the one before it.

        Returns the step that was undone, or ``None`` when there is nothing to
        undo. **It does not un-paste anything**: if that step's build was
        handed off, :attr:`blind_spots` starts saying so on the next call.
        """
        if not self.can_undo:
            return None
        return self._history.pop()

    # -- building -------------------------------------------------------------

    def plan(self):
        """The floorplan of the held spec. Needs no catalog."""
        return plan_for(self.spec)

    def build(self, palette) -> Slab:
        """The slab of the held spec. Offline, deterministic."""
        return build_slab(self.spec, palette)

    def hand_off(self, palette, **kwargs) -> "Handoff":
        """Build the held spec and put it where the user can paste it.

        The default and only route for a chat slab: the base64 goes on the
        clipboard and the user presses Ctrl+V. See :func:`to_clipboard` for
        what happens off Windows.

        Recorded in :attr:`handed_off` **whichever way it went**, including a
        failed clipboard call -- the text is returned to the caller either way,
        so the geometry has left the session and this is the last moment
        anything here knows the truth.
        """
        handoff = to_clipboard(encode(self.build(palette)), **kwargs)
        self._handed.append(Handed(
            step=len(self._history) - 1, spec=self.spec,
            chars=handoff.chars, how=handoff.how, at=_now(),
        ))
        return handoff

    # -- what it cannot see ---------------------------------------------------

    @property
    def blind_spots(self) -> tuple[BlindSpot, ...]:
        """Everything this session does not know. **Never empty.**

        Two standing entries (see :data:`STANDING_BLIND_SPOTS`) plus up to two
        that only exist once something has been handed off:

        * whether the paste actually happened -- the hand-off is text on a
          clipboard and nothing reports back;
        * whether the board is now holding an *older* build than the one this
          session holds, which is what an edit or an undo after a paste means.
          There is no erase in TaleSpire, so a re-paste adds rather than
          replaces.
        """
        spots = list(STANDING_BLIND_SPOTS)
        if not self._handed:
            return tuple(spots)

        n = len(self._handed)
        spots.append(BlindSpot(
            subject="the paste",
            cannot=f"tell whether the {_plural(n, 'build')} handed over got pasted",
            because=(
                "the hand-off is base64 on the clipboard and the user presses "
                "Ctrl+V; nothing reports back, which is the whole reason this "
                "path cannot go wrong on its own"
            ),
        ))
        last = self._handed[-1].spec
        if last != self.spec:
            spots.append(BlindSpot(
                subject="the older build",
                cannot="take back what has already been pasted",
                because=(
                    f"the last hand-off was {last.describe().splitlines()[0]}, "
                    "which is not what this session now holds, and TaleSpire "
                    "has no erase -- pasting again adds to the board rather "
                    "than replacing"
                ),
            ))
        return tuple(spots)

    def cannot_see(self) -> str:
        """:attr:`blind_spots` as prose, for the transcript."""
        lines = ["This session cannot see the board. Specifically:"]
        lines += [f"  - {s.line()}" for s in self.blind_spots]
        return "\n".join(lines)

    def transcript(self) -> str:
        """The whole session as text: every step, then what it cannot see.

        A hand-off whose step has since been undone is **not dropped** -- it is
        listed on its own at the end. Hanging it off a history index and
        letting an undo orphan it would delete the record of geometry that has
        left the building, which is the exact failure this module exists to
        make impossible.
        """
        out: list[str] = []
        for i, step in enumerate(self._history):
            head = f"[{i}] {step.message}" if step.message else f"[{i}] opening spec"
            out.append(head)
            out.append(step.as_turn().summary())
            for h in self._handed:
                if h.step == i:
                    out.append(f"  -> handed over ({h.how}, {h.chars} chars)")
            out.append("")

        orphans = [h for h in self._handed if h.step >= len(self._history)]
        if orphans:
            out.append("Handed over on steps that have since been undone:")
            for h in orphans:
                out.append(f"  - {h.spec.describe().splitlines()[0]} "
                           f"({h.how}, {h.chars} chars, {h.at})")
            out.append("")

        out.append(self.cannot_see())
        return "\n".join(out)

    # -- serialisation --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe. The UI holds this across page loads."""
        return {
            "chatsession_version": CHATSESSION_VERSION,
            "history": [s.to_dict() for s in self._history],
            "handed_off": [h.to_dict() for h in self._handed],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ChatSession":
        """Rebuild from :meth:`to_dict`, or from anything at all.

        **Never raises**, for the reason :meth:`SlabSpec.from_dict
        <citysmith.slabchat.SlabSpec.from_dict>` gives: this reads a value the
        browser has been holding, and a session that cannot be loaded is a chat
        that cannot continue. Every spec inside goes back through the same
        clamping, so nothing unbuildable can be smuggled in through storage.
        """
        session = cls()
        if not isinstance(data, dict):
            return session
        raw = data.get("history")
        steps = [Step.from_dict(s) for s in raw] if isinstance(raw, list) else []
        if steps:
            session._history = steps
        raw = data.get("handed_off")
        if isinstance(raw, list):
            session._handed = [Handed.from_dict(h) for h in raw]
        return session


# -- getting it into the game --------------------------------------------------
#
# Windows only, and it says so rather than failing obscurely. The generate and
# build half of this project is cross-platform; `ts.ps1` is PowerShell driving
# Win32, and it is not.

#: It is on the clipboard. Press Ctrl+V in TaleSpire.
CLIPBOARD = "clipboard"

#: It is not on the clipboard, and :attr:`Handoff.text` is how the user gets
#: it. Never an exception and never an empty result -- a chat that produced a
#: perfectly good slab and then lost it to a missing shell has failed at the
#: last inch.
MANUAL = "manual"

#: `CreateProcess`'s `lpCommandLine` ceiling, and the reason the base64 goes
#: through a file. Measured on this machine by bisection, passing an argument
#: of growing length to `powershell.exe` through `subprocess.run` (which builds
#: the command line directly, so `cmd.exe`'s much lower 8,191 does not apply):
#: **32,588 characters of payload started, 32,589 raised `WinError 206, "The
#: filename or extension is too long"`** -- a whole-command-line total of
#: 32,767.
#:
#: **The budget is not a property of the payload**, which is what makes it a
#: trap: it is 32,767 *minus* the interpreter path, the script path and every
#: flag, so the same slab that works from a short checkout fails from a deep
#: worktree. And a legal chat slab can exceed it outright -- the compressed cap
#: is 30,720 bytes, which is :data:`MAX_SLAB_CHARS` of base64, comfortably past
#: it. So the argument route is not used at all: the text goes to a temp file
#: and PowerShell reads it, which carried 42,000 characters through a
#: 152-character command line intact.
MAX_COMMAND_LINE = 32767

#: Base64 of a maximum-size slab. Derived from the cap rather than measured,
#: because it is arithmetic: 4 characters per 3 bytes, rounded up.
MAX_SLAB_CHARS = -(-MAX_COMPRESSED_BYTES // 3) * 4

#: `ts.ps1 setclip` uses `System.Windows.Forms.Clipboard`, which needs an STA
#: thread. Windows PowerShell 5.1 is STA by default; `pwsh` is not, so it is a
#: fallback rather than a preference and a failure there is reported rather
#: than worked around.
POWERSHELL_HOSTS = ("powershell", "pwsh")

_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Handoff:
    """What happened when a slab was handed to the user, and the slab itself.

    :attr:`text` is populated **always**, on every path including every
    failure. That is the contract: off Windows, with no PowerShell, with
    `ts.ps1` missing or erroring, the caller still has the base64 and can show
    it for the user to copy. Nothing here is allowed to swallow a build.
    """

    #: The base64 slab. Always present.
    text: str
    #: :data:`CLIPBOARD` or :data:`MANUAL`.
    how: str
    #: One line saying what happened, suitable for the UI.
    detail: str
    #: The command that was run, for a UI that shows its working. Empty when
    #: nothing was run.
    command: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when it is on the clipboard.

        A property rather than a stored field so it cannot disagree with
        :attr:`how`.
        """
        return self.how == CLIPBOARD

    @property
    def chars(self) -> int:
        return len(self.text)

    def instruction(self) -> str:
        """What to tell the user. One paste, and no chunk order to keep.

        The camera line is not decoration: a paste anchors on the cursor's
        ray-hit, so with the camera pitched, a hit on top of an existing floor
        slides the anchor toward the camera by height x cot(pitch). A chat slab
        usually goes down *inside* somewhere that already has a floor, which is
        exactly that case.
        """
        if self.ok:
            head = f"On the clipboard ({self.chars} characters)."
        else:
            head = f"{self.detail} Copy the {self.chars} characters yourself."
        return (
            f"{head} In TaleSpire: pitch the camera straight down, put the "
            "cursor where it should land, and press Ctrl+V. One slab, one "
            "paste -- no chunk order and nothing to line up."
        )


def to_clipboard(text: str, *, script: str | os.PathLike[str] | None = None,
                 host: str | None = None, run: Callable[..., Any] | None = None,
                 windows: bool | None = None) -> Handoff:
    """Put ``text`` on the clipboard through ``ts.ps1 setclip``.

    **Drives no synthetic input.** No click, no keystroke, no camera move, and
    it does not need TaleSpire to be running or even installed. The user
    presses Ctrl+V, which is the entire point: a town needs the paste driven
    and this does not, so nothing can go wrong that the user did not do.

    Off Windows, or with no PowerShell, or with `ts.ps1` missing, it returns
    :data:`MANUAL` and the text. It never raises and it never returns nothing.

    ``script``, ``host``, ``run`` and ``windows`` are seams for the tests, so
    every branch -- the non-Windows path, the missing shell, the failing call
    -- can be exercised on any machine without a clipboard being touched.
    """
    text = text or ""
    on_windows = (os.name == "nt") if windows is None else bool(windows)
    if not on_windows:
        # Only name the host when it is really the host talking. Under the
        # `windows=` seam `sys.platform` says win32 and printing it would be a
        # small lie in the one message whose whole job is being straight about
        # where this does and does not work.
        where = "" if windows is not None else f" (this host is {sys.platform})"
        return Handoff(
            text=text, how=MANUAL,
            detail=(
                f"The clipboard route is Windows only{where}: ts.ps1 is "
                "PowerShell driving Win32, and there is no equivalent here."
            ),
        )

    path = pathlib.Path(script) if script is not None else _ts_script()
    if not path.exists():
        return Handoff(text=text, how=MANUAL,
                       detail=f"{path} is missing, so nothing could be run.")

    host = host or _powershell()
    if host is None:
        return Handoff(
            text=text, how=MANUAL,
            detail=("No PowerShell found on PATH (looked for "
                    f"{', '.join(POWERSHELL_HOSTS)})."),
        )

    runner = run if run is not None else subprocess.run
    # The base64 goes through a file, never through the command line -- see
    # MAX_COMMAND_LINE. `ts.ps1 setclip` is called exactly as it is by hand;
    # what changes is only where its -Text comes from.
    # `mkstemp` rather than a name built from the pid: the sidecar is a
    # threaded local server, so two chats handing over at once would otherwise
    # write the same file and one of them would paste the other's building.
    try:
        handle, name = tempfile.mkstemp(prefix="citysmith-slab-", suffix=".b64")
        os.close(handle)
    except OSError as exc:
        return Handoff(text=text, how=MANUAL,
                       detail=f"could not open a temp file for the slab: {exc}.")
    tmp = pathlib.Path(name)
    inline = (
        f"& {_psquote(str(path))} setclip "
        f"-Text ((Get-Content -Raw -LiteralPath {_psquote(str(tmp))}).Trim())"
    )
    command = (host, "-NoProfile", "-NonInteractive", "-Command", inline)
    try:
        # Base64 is ASCII, so the encoding cannot bite; written without a
        # trailing newline of its own and trimmed in PowerShell anyway, the
        # same way `ts.ps1 paste` trims a slab file.
        tmp.write_text(text, encoding="ascii", newline="")
        result = runner(list(command), capture_output=True, text=True,
                        timeout=_TIMEOUT_S)
    except Exception as exc:                  # OSError, TimeoutExpired, anything
        return Handoff(text=text, how=MANUAL, command=command,
                       detail=f"ts.ps1 setclip could not run: {exc}.")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    code = getattr(result, "returncode", 1)
    if code != 0:
        err = _clean(getattr(result, "stderr", "")) or f"exit {code}"
        return Handoff(text=text, how=MANUAL, command=command,
                       detail=f"ts.ps1 setclip failed: {err}.")
    said = _clean(getattr(result, "stdout", "")) or "clipboard set"
    return Handoff(text=text, how=CLIPBOARD, command=command, detail=said)


def talespire_running() -> bool | None:
    """Is TaleSpire up? ``None`` when that cannot be answered here.

    Advisory only, and deliberately **not** a precondition of
    :func:`to_clipboard`: the clipboard holds text whether or not the game is
    running, and a user who copies first and launches second has done nothing
    wrong. Refusing there would invent a failure the cheap path does not have.
    It is worth *reporting*, though, so the UI can say "TaleSpire is not
    running yet" beside the paste instruction instead of leaving the user to
    wonder why Ctrl+V did nothing.

    ``ts.ps1 client`` throws "TaleSpire is not running." through ``Get-TS``, so
    a non-zero exit is the answer rather than an error.
    """
    if os.name != "nt":
        return None
    path = _ts_script()
    host = _powershell()
    if host is None or not path.exists():
        return None
    try:
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-File", str(path), "client"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except Exception:
        return None
    return result.returncode == 0


# -- plumbing ------------------------------------------------------------------

def _ts_script() -> pathlib.Path:
    """`tools/ts.ps1`, found from the package rather than the cwd."""
    return pathlib.Path(__file__).resolve().parent.parent / "tools" / "ts.ps1"


def _powershell() -> str | None:
    for name in POWERSHELL_HOSTS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _psquote(value: str) -> str:
    """A PowerShell single-quoted string. Nothing inside it is expanded."""
    return "'" + value.replace("'", "''") + "'"


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if c.isprintable()).split())


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_clean(v) for v in value if isinstance(v, str))


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
