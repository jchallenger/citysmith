r"""Driving a tiled paste from the UI: the preconditions first, then the run.

**Why this is not in `uiserver.py`.** That module's safety paragraph promises
that no endpoint takes a command, and
`test_nothing_in_the_server_can_reach_a_shell` reads the source and asserts the
word ``subprocess`` does not appear in it. This half of the UI has to run
PowerShell. The same split `chatsession.py` made for the same reason applies
here: keeping the shelling-out in its own module means "the request handler
cannot run anything" stays a property of a *file* rather than of a careful
reading, and the guard test stays meaningful instead of being deleted the first
time somebody needs a subprocess.

The rule this module keeps in exchange: **nothing that came from a request ever
reaches a ``-Command`` string.** `-Name`, `-Stem` and `-OutDir` go to
``review.ps1`` through ``-File`` as separate argv entries, so there is no
composed command line for them to be inside of, and ``shell=True`` appears
nowhere. The one ``-Command`` here interpolates a single path derived from
``__file__``, quoted with :func:`_psquote`.

Windows only, and it says so rather than failing obscurely
---------------------------------------------------------

Generating, importing, building and verifying a town is pure Python and runs
anywhere. This half is `tools/ts.ps1` and `tools/review.ps1` driving Win32
against TaleSpire's own window, and there is no equivalent elsewhere. So
:attr:`Driver.available` is false off Windows, `/api/options` says so, and the
page renders the sentence instead of a button that could only fail.

The preconditions are the point
-------------------------------

A tiled paste of East Tradebourne is 102 chunks and the better part of half an
hour of driven input. Every one of those chunks lands wrong if a build plane is
up -- ``G`` raises a grid that a paste snaps to instead of the ground, it
survives making a new board, and nothing in any slab file is wrong when it
happens. So the run is refused before it starts rather than discovered at chunk
sixty.

**An unreadable probe is not a pass, and this is the crux.** `CLAUDE.md`
records the failure in its own words: ``-match 'ON'`` does not match
``UNKNOWN``, so a "not ON means it must be off" test reads an unreadable probe
as safe and pastes anyway. `ts.ps1 planestate` returns exactly one of three
prefixes -- ``build plane off``, ``build plane ON``, ``build plane UNKNOWN`` --
and the UNKNOWN branch's own message ends "Do NOT paste on this reading."
:func:`read_plane_state` therefore recognises **off** explicitly and treats
everything else, including output it does not recognise at all, as a refusal.
:data:`PLANE_STATES` names the four outcomes and only one of them proceeds.

What the run reports
--------------------

`review.ps1 tiled` prints one ``i/n : file`` line per chunk and one
``<name> -> <bytes> bytes`` line per screen grab, and the grabs land in
``out/flyby``. :meth:`Driver.paste` reads those lines and yields an event per
chunk carrying the grabs taken *for that chunk* -- checked against the disk,
not assumed from the recipe -- so the page can show the picture beside the row
and a bad paste is visible without alt-tabbing to the game.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterator

#: Windows PowerShell 5.1 first: `ts.ps1` uses `System.Windows.Forms.Clipboard`
#: and `System.Drawing`, and 5.1 is STA by default where `pwsh` is not.
POWERSHELL_HOSTS = ("powershell", "pwsh")

#: One probe. Seconds, not minutes -- `planestate` focuses the window, grabs
#: two small bitmaps and returns. A hang here is a hung game, not slow work.
PROBE_TIMEOUT_S = 60.0

#: The sentence the page shows off Windows, in place of any control.
WINDOWS_ONLY = (
    "Pasting is Windows only. It drives TaleSpire's own window with synthetic "
    "input through tools/ts.ps1 and tools/review.ps1, which are PowerShell "
    "over Win32; there is no equivalent on this platform. Generating, "
    "building and verifying a town works everywhere -- the slabs are on disk "
    "and can be pasted from a Windows machine."
)

#: What `ts.ps1 planestate` can say. Read the prefixes off the script, not off
#: memory: it prints the reading and the pixel values after them.
PLANE_OFF = "build plane off"
PLANE_ON = "build plane ON"
PLANE_UNKNOWN = "build plane UNKNOWN"

#: The four outcomes of reading the build plane, and what each one means for a
#: paste. **Exactly one of them proceeds.** "unreadable" is not a theoretical
#: branch: it is what a renamed message, a changed script or a PowerShell error
#: on stdout comes out as, and it must refuse like the rest.
PLANE_STATES = {
    "off": "the build plane is down; a paste will land on the ground",
    "on": ("the build plane is UP -- a paste snaps to it instead of the "
           "ground, so every chunk lands a course high with nothing wrong in "
           "any file. Press G in TaleSpire, then check again."),
    "unknown": ("the probe could not see the build toolbar, so it has no "
                "reading. Its own message says not to paste on it. Press B "
                "for build mode in TaleSpire, then check again."),
    "unreadable": ("ts.ps1 planestate said something this does not recognise. "
                   "Treated as a refusal: an unread probe is not an off one."),
}

#: `review.ps1 tiled` prints this per chunk, after the chunk has landed.
_PROGRESS = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*:\s*(\S.*?)\s*$")

#: `grab.ps1` prints this per screen grab, and the grabs come before the
#: progress line for the chunk they belong to.
_SHOT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]{0,79}) -> (\d+) bytes\s*$")

_MANIFEST_SUFFIX = "-paste-order.txt"


class PasteRefused(RuntimeError):
    """The run will not start, and the sentence to show the user.

    Raised rather than returned so that a caller cannot forget to look: the one
    thing this module exists to prevent is a 102-chunk run beginning on a
    reading nobody checked.
    """


# -- preconditions -------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One precondition, and what the probe actually said.

    ``ok`` is **tri-state**: True passed, False failed, ``None`` was never run
    because an earlier check stopped the sequence. None is not a pass --
    :attr:`Preflight.ok` requires every check to be exactly True -- but it is
    also not a failure to report against the user, and telling them apart is
    the difference between "the build plane is up" and "the build plane was
    never looked at because the game is not running".
    """

    name: str
    ok: bool | None
    detail: str
    #: What came back, verbatim, so a reading can be argued with.
    raw: str = ""

    @property
    def state(self) -> str:
        return "ok" if self.ok is True else "fail" if self.ok is False else "skipped"

    def as_json(self) -> dict:
        return {"name": self.name, "state": self.state, "detail": self.detail,
                "raw": self.raw}

    def line(self) -> str:
        return f"{self.name}: {self.detail}"


@dataclass(frozen=True)
class Preflight:
    """Every precondition that was looked at, and whether the run may start."""

    checks: tuple[Check, ...] = ()
    #: TaleSpire's client rect, when `ts.ps1 client` gave one. Advisory: it is
    #: shown so the user can see the probe reached the right window.
    client: dict[str, int] | None = None

    @property
    def ok(self) -> bool:
        """True only when every check ran and passed.

        An empty list is False. A precondition set that is vacuously satisfied
        is the same bug as a probe that could not read the toolbar: it says yes
        without having looked.
        """
        return bool(self.checks) and all(c.ok is True for c in self.checks)

    def refusal(self) -> str:
        """Why the run will not start. Empty when it may."""
        if self.ok:
            return ""
        bad = [c for c in self.checks if c.ok is not True]
        head = ("Not pasting. " if bad else "Not pasting: nothing was checked. ")
        return head + " ".join(c.line() for c in bad)

    def summary(self) -> str:
        if self.ok:
            return "preconditions ok: " + "; ".join(c.line() for c in self.checks)
        return self.refusal()

    def as_json(self) -> dict:
        return {"ok": self.ok, "checks": [c.as_json() for c in self.checks],
                "client": self.client, "refusal": self.refusal()}


def read_plane_state(text: Any) -> str:
    """Classify `ts.ps1 planestate` output. One of :data:`PLANE_STATES`.

    **Recognise ``off`` explicitly; refuse everything else.** Matching "not ON"
    is the shape of test that reads ``build plane UNKNOWN`` as safe, which is
    the reading the probe's own message tells you not to act on. This project
    has made that mistake once already, in `review.ps1`, and the note about it
    is in `CLAUDE.md`.
    """
    if not isinstance(text, str):
        return "unreadable"
    line = text.strip()
    if line.startswith(PLANE_OFF):
        return "off"
    if line.startswith(PLANE_ON):
        return "on"
    if line.startswith(PLANE_UNKNOWN):
        return "unknown"
    return "unreadable"


# -- the plan on disk ----------------------------------------------------------

def scan_plans(out_dir) -> list[dict]:
    """Every ``<stem>-paste-order.txt`` in ``out_dir``, newest first.

    The browser picks a stem from this and sends it back, the same way the
    build form picks a source: the server did the scan, so a request can only
    name a plan that exists. ``missing`` is carried rather than filtered --
    a manifest whose slabs have been deleted is a fact worth showing, not a
    plan to quietly drop.
    """
    out_dir = pathlib.Path(out_dir)
    plans: list[tuple[float, dict]] = []
    try:
        manifests = sorted(out_dir.glob("*" + _MANIFEST_SUFFIX))
    except OSError:  # pragma: no cover -- an unreadable out_dir
        return []
    for manifest in manifests:
        stem = manifest.name[: -len(_MANIFEST_SUFFIX)]
        if not stem:
            continue
        try:
            names = _manifest_names(manifest)
            mtime = manifest.stat().st_mtime
        except OSError:
            continue
        files, missing, total = [], [], 0
        for name in names:
            path = out_dir / name
            try:
                size = path.stat().st_size
            except OSError:
                missing.append(name)
                continue
            total += size
            files.append({"file": name, "bytes": size})
        plans.append((mtime, {
            "stem": stem, "files": files, "missing": missing,
            "chunks": len(names), "bytes": total,
            "manifest": manifest.name,
        }))
    return [p for _, p in sorted(plans, key=lambda item: -item[0])]


def read_paste_order(out_dir, stem: str) -> list[str]:
    """The chunk names, **in the order the build wrote them**.

    Not a glob, and nothing here sorts it. The chunk covering the anchor cell
    is written last so the anchor is still bare board for every paste before
    it; alphabetised, that chunk lands in the middle and everything after it
    inherits its height -- a quarter of a map standing a course proud with
    nothing wrong in any file.
    """
    out_dir = pathlib.Path(out_dir)
    manifest = out_dir / f"{stem}{_MANIFEST_SUFFIX}"
    if not manifest.is_file():
        raise PasteRefused(
            f"No {manifest.name} in {out_dir}. The paste order is not the "
            f"filename order, so this will not glob for one -- build the town "
            f"again and it will be written beside the slabs.")
    names = _manifest_names(manifest)
    if not names:
        raise PasteRefused(f"{manifest.name} is empty.")
    missing = [n for n in names if not (out_dir / n).is_file()]
    if missing:
        raise PasteRefused(
            f"{len(missing)} slab(s) named in {manifest.name} are not on disk "
            f"({', '.join(missing[:4])}). An unpasted chunk is not a gap in "
            f"the map, it is bare board -- rebuild before pasting.")
    return names


def _manifest_names(manifest: pathlib.Path) -> list[str]:
    text = manifest.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


# -- the driver ----------------------------------------------------------------

@dataclass
class Driver:
    """Everything that runs PowerShell, behind seams the tests can replace.

    ``run``, ``popen``, ``windows``, ``tools`` and ``shots`` exist so the whole
    of this module can be exercised on any machine without TaleSpire, without
    PowerShell and without a single pixel being driven. Nothing in the suite
    invokes the real scripts.
    """

    #: Where `ts.ps1`, `review.ps1` and `grab.ps1` live.
    tools: pathlib.Path = field(default_factory=lambda: _tools_dir())
    #: Where `grab.ps1` writes. It is fixed relative to the tools directory --
    #: `out/flyby` beside the repo -- and is NOT the server's ``out_dir``,
    #: which is why it is served through its own route.
    shots: pathlib.Path | None = None
    run: Any = None
    popen: Any = None
    windows: bool | None = None
    #: The PowerShell to use. A seam, and a way to pin 5.1 on a machine that
    #: also has `pwsh`; when unset it is looked up on PATH.
    host: str | None = None

    def __post_init__(self) -> None:
        self.tools = pathlib.Path(self.tools)
        if self.shots is None:
            self.shots = self.tools.parent / "out" / "flyby"
        self.shots = pathlib.Path(self.shots)

    # -- what this platform can do --------------------------------------------

    @property
    def on_windows(self) -> bool:
        return (os.name == "nt") if self.windows is None else bool(self.windows)

    @property
    def available(self) -> bool:
        """Can this machine drive a paste at all? Platform only.

        Deliberately not "and TaleSpire is running": that changes minute to
        minute and belongs in :meth:`preflight`, which the user asks for. This
        one decides whether the screen offers a control or a sentence.
        """
        return self.on_windows

    @property
    def note(self) -> str:
        return "" if self.available else WINDOWS_ONLY

    @property
    def ts(self) -> pathlib.Path:
        return self.tools / "ts.ps1"

    @property
    def review(self) -> pathlib.Path:
        return self.tools / "review.ps1"

    def as_json(self) -> dict:
        return {"available": self.available, "note": self.note,
                "recipe": "review.ps1 tiled"}

    # -- preconditions --------------------------------------------------------

    def preflight(self) -> Preflight:
        """Look at everything that has to be true, and stop at the first no.

        The order is not cosmetic: each check is a precondition of the one
        after it, and running `planestate` with the game down would report
        "cannot see the toolbar" for a reason that has nothing to do with the
        toolbar. A check that was never run comes back ``skipped`` -- which
        still blocks the run (see :attr:`Preflight.ok`) but does not accuse the
        user of something that was never measured.
        """
        if not self.available:
            return Preflight(checks=(Check("platform", False, WINDOWS_ONLY),))

        checks: list[Check] = []
        host = self._powershell()
        if host is None:
            checks.append(Check("PowerShell", False,
                                "not on PATH (looked for "
                                f"{', '.join(POWERSHELL_HOSTS)})"))
            return Preflight(checks=self._skip_rest(checks))
        checks.append(Check("PowerShell", True, host))

        absent = [p.name for p in (self.ts, self.review) if not p.exists()]
        if absent:
            checks.append(Check("scripts", False,
                                f"{', '.join(absent)} missing from {self.tools}"))
            return Preflight(checks=self._skip_rest(checks))
        checks.append(Check("scripts", True, f"ts.ps1 and review.ps1 in {self.tools}"))

        rect, window = self._check_window(host)
        checks.append(window)
        if window.ok is not True:
            return Preflight(checks=self._skip_rest(checks))

        checks.append(self._check_plane(host))
        return Preflight(checks=tuple(checks), client=rect)

    @staticmethod
    def _skip_rest(checks: list[Check]) -> tuple[Check, ...]:
        """Name the checks that never ran, rather than leaving them out.

        A list that stops early reads like a shorter list of requirements. It
        is not: these still have to be true, they were simply not reachable.
        """
        done = {c.name for c in checks}
        for name, why in (("TaleSpire", "not checked"),
                          ("build plane", "not checked")):
            if name not in done:
                checks.append(Check(name, None, why))
        return tuple(checks)

    def _check_window(self, host: str) -> tuple[dict | None, Check]:
        """`ts.ps1 client`: is the game up, and where is its window?

        ``Get-TS`` throws "TaleSpire is not running." when it is not, so a
        non-zero exit is the answer rather than an error. The rect is asked for
        as JSON because a `pscustomobject` printed by PowerShell is a text
        table, and parsing a text table is how a probe starts reading the wrong
        pixels.
        """
        # The ONLY -Command string in this module, and the only thing inside it
        # is a path built from `__file__`. Nothing from a request is ever
        # interpolated into a command line -- see `_paste_command`.
        inline = f"& {_psquote(str(self.ts))} client | ConvertTo-Json -Compress"
        code, out, err = self._call([host, "-NoProfile", "-NonInteractive",
                                     "-Command", inline])
        said = (err or out).strip()
        if code != 0:
            return None, Check(
                "TaleSpire", False,
                "not running (ts.ps1 client could not find the window). Start "
                "TaleSpire, open the board you are pasting onto, and check "
                "again.", said)
        rect = _rect_from(out)
        if rect is None:
            return None, Check(
                "TaleSpire", False,
                "ts.ps1 client returned something that is not a window rect, "
                "so nothing here knows where to click.", said)
        return rect, Check(
            "TaleSpire", True,
            f"running, client {rect['W']}x{rect['H']} at "
            f"{rect['X']},{rect['Y']} (paste point {rect['CX']},{rect['CY']})",
            said)

    def _check_plane(self, host: str) -> Check:
        """`ts.ps1 planestate`: an explicit ``off``, or no run.

        Three outcomes exist and only one of them is a pass. See
        :func:`read_plane_state` -- this is the check the whole module is
        arranged around.
        """
        code, out, err = self._call([host, "-NoProfile", "-NonInteractive",
                                     "-File", str(self.ts), "planestate"])
        said = (out or err).strip()
        if code != 0:
            return Check("build plane", False,
                         "ts.ps1 planestate could not run, so the build plane "
                         "was never read. An unread probe is not an off one.",
                         (err or out).strip())
        state = read_plane_state(said)
        return Check("build plane", state == "off", PLANE_STATES[state], said)

    # -- the run --------------------------------------------------------------

    def paste(self, *, stem: str, name: str, out_dir, shot_every: int = 1
              ) -> Iterator[dict]:
        """Drive `review.ps1 tiled`, yielding one event per thing that happened.

        Preflight runs **inside** this, before anything is spawned, so a run
        started from a stale page still cannot begin on a plane that came up in
        the meantime. :class:`PasteRefused` is raised rather than yielded: a
        refusal is not progress.
        """
        pre = self.preflight()
        yield {"stage": "preflight", "text": pre.summary(),
               "checks": [c.as_json() for c in pre.checks], "ok": pre.ok}
        if not pre.ok:
            raise PasteRefused(pre.refusal())

        out_dir = pathlib.Path(out_dir).resolve()
        files = read_paste_order(out_dir, stem)
        yield {"stage": "plan", "files": files, "total": len(files),
               "text": (f"{len(files)} chunk(s) of {stem}, in the order the "
                        f"build wrote them -- which is not filename order. "
                        f"Every one goes down at the same cursor cell, camera "
                        f"straight down, and the last one covers the anchor.")}

        host = self._powershell()
        command = self._paste_command(host, stem=stem, name=name,
                                      out_dir=out_dir, shot_every=shot_every)
        yield {"stage": "running", "text": "running " + " ".join(command),
               "command": list(command)}

        popen = self.popen if self.popen is not None else subprocess.Popen
        process = popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)

        # Grabs are printed BEFORE the progress line of the chunk they belong
        # to, so they are held and flushed with it. A chunk's row on the page
        # then carries its own two pictures rather than the previous chunk's.
        pending: list[dict] = []
        landed = 0
        for raw in process.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            shot = _SHOT.match(line)
            if shot:
                found = self._shot(shot.group(1), int(shot.group(2)))
                if found:
                    pending.append(found)
                continue
            progress = _PROGRESS.match(line)
            if progress:
                landed = int(progress.group(1))
                yield {"stage": "chunk", "index": landed,
                       "total": int(progress.group(2)),
                       "file": progress.group(3), "shots": pending,
                       "text": line}
                pending = []
                continue
            yield {"stage": "said", "text": line}

        code = process.wait()
        if code != 0:
            raise PasteRefused(
                f"review.ps1 stopped after {landed} chunk(s) (exit {code}). "
                f"The lines above are its own output; nothing here retries a "
                f"paste, because a second attempt stamps a second copy.")
        yield {"stage": "finished", "landed": landed,
               "text": f"{landed} chunk(s) pasted."}

    def _paste_command(self, host: str, *, stem: str, name: str, out_dir,
                       shot_every: int) -> tuple[str, ...]:
        """The argv for one tiled run. **A list, and never a string.**

        Every value that came from a request -- the stem, the shot name, the
        output directory -- is its own argv entry behind its own ``-``switch.
        There is no command line for them to be quoted into and no shell to
        re-split them, which is what makes "named operations, typed
        parameters" hold on this side of the UI as well as on the build side.
        """
        return (host, "-NoProfile", "-NonInteractive",
                "-File", str(self.review),
                "-Recipe", "tiled",
                "-Name", name,
                "-Stem", stem,
                "-OutDir", str(out_dir),
                "-ShotEvery", str(int(shot_every)))

    def _shot(self, name: str, said_bytes: int) -> dict | None:
        """A grab, **checked against the disk** rather than taken on trust.

        `grab.ps1` says what it wrote; this looks. Reporting a picture the page
        then cannot fetch is the small version of the failure this project
        keeps writing down -- reading the plan instead of the artifact.
        """
        path = self.shots / f"{name}.jpg"
        try:
            size = path.stat().st_size
        except OSError:
            return None
        return {"name": f"{name}.jpg", "bytes": size, "said": said_bytes,
                "view": "hold" if name.endswith("-hold") else
                        "down" if name.endswith("-down") else ""}

    # -- plumbing -------------------------------------------------------------

    def _call(self, command) -> tuple[int, str, str]:
        """One bounded probe. Never raises -- a failure is a reading."""
        runner = self.run if self.run is not None else subprocess.run
        try:
            result = runner(list(command), capture_output=True, text=True,
                            timeout=PROBE_TIMEOUT_S)
        except Exception as exc:  # OSError, TimeoutExpired, anything
            return 1, "", f"{type(exc).__name__}: {exc}"
        return (int(getattr(result, "returncode", 1) or 0),
                str(getattr(result, "stdout", "") or ""),
                str(getattr(result, "stderr", "") or ""))

    def _powershell(self) -> str | None:
        if self.host:
            return self.host
        for name in POWERSHELL_HOSTS:
            found = shutil.which(name)
            if found:
                return found
        return None


def _tools_dir() -> pathlib.Path:
    """`tools/`, found from the package rather than the working directory."""
    return pathlib.Path(__file__).resolve().parent.parent / "tools"


def _psquote(value: str) -> str:
    """A PowerShell single-quoted string. Nothing inside it is expanded."""
    return "'" + value.replace("'", "''") + "'"


def _rect_from(text: str) -> dict[str, int] | None:
    """``{X, Y, W, H, CX, CY}`` out of `ConvertTo-Json`, or None."""
    try:
        data = json.loads((text or "").strip() or "null")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    rect = {}
    for key in ("X", "Y", "W", "H", "CX", "CY"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        rect[key] = int(value)
    if rect["W"] <= 0 or rect["H"] <= 0:
        return None
    return rect
