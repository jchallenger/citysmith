"""A local web UI over :func:`citysmith.pipeline.build_town`.

`http.server` plus hand-written HTML, CSS and JavaScript. That is not a taste
call: the core is **stdlib only and has no build step**, which rules out
Electron, React and FastAPI on the same grounds. Tkinter would have qualified
too; a web page wins because it can be *driven and screenshotted* by the same
browser tooling the rest of this project already uses, and this project's
history is mostly screenshots read wrong.

**The report is the product.** For anyone who cannot open TaleSpire, the
`verify` findings are the only thing that says whether a town is playable, and
each of those sentences is the residue of a session that got something wrong.
So the UI shows every finding in full, at its own level, in the order the CLI
prints them. It never rolls them into a tick.

Safety is part of the design and not a follow-up:

- **Loopback only.** :func:`make_server` refuses any bind address that is not
  127.0.0.1 / ::1, so there is no configuration in which this listens on a
  network. Every response also carries no CORS header at all, and every request
  is rejected unless its ``Host`` header is a loopback name -- which is the
  guard against DNS rebinding, where a page on the open web resolves its own
  hostname to 127.0.0.1 and talks to this server through the user's browser.
- **Named operations, typed parameters.** There is no endpoint that takes a
  command, a flag string, an argv list, or anything else that is handed onward
  as text. :func:`read_build_request` coerces every field to an int, a bool, or
  a member of a closed set, **rejects unknown keys**, and :func:`run_build`
  passes them to ``build_town`` as explicit keywords. Nothing in this module
  imports ``subprocess`` or ``os.system``, and a test asserts that. The paste
  screen needs to run PowerShell, so that work is in
  :mod:`citysmith.pastedrive` and this module keeps the guard -- the same split
  :mod:`citysmith.chatsession` made, so "a request handler cannot run anything"
  stays a property of a file rather than of a careful reading.
- **The browser never names a file.** Input files are chosen from a scan the
  server does (:func:`scan_sources`); the request carries an opaque id, not a
  path. Output files are served only from ``out_dir``, through
  :func:`resolve_in`, which rejects ``..``, absolute paths and symlinks that
  leave the directory.
- **The Anthropic key stays here.** The browser is never told it, never told
  whether one is set beyond a boolean, and cannot reach api.anthropic.com even
  if it wanted to: the page is served under a Content-Security-Policy of
  ``default-src 'self'``.

A build is minutes on a big town, so :meth:`_Handler.api_build_start` starts a
worker and returns an id immediately; the page polls
``/api/build/<id>?after=<seq>`` for new events. Nothing here holds a request
open. Polling rather than server-sent events is deliberate: a poll has no
half-open connection to leak when the page is closed, and at a 400 ms interval
the cost is a few hundred bytes against a build measured in minutes.

There are two screens. **Build** is cross-platform, because generating and
verifying a town is pure Python. **Paste** is not: it drives TaleSpire's own
window through PowerShell, so off Windows the page says so and offers no
control rather than a button that could only fail. The precondition rule for a
paste is in :mod:`citysmith.pastedrive`, and it is the point of that screen --
a tiled run is up to 102 chunks of driven input, and every one of them lands a
course high if a build plane is up.

Run it::

    python -m citysmith.uiserver --out-dir out

The chat screen is not built here. Adding one is a row in :data:`_ROUTES` and a
tab in ``ui/index.html``; nothing in this module knows what a screen is.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import pathlib
import math
import re
import secrets
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field

from . import camera
from . import preview
from .layout import Layout
from . import pastedrive
from .build import DEFAULT_CHUNK_TILES, DEFAULT_FENCE_STYLE, FENCE_STYLES
from .palette import STYLES
from .pipeline import STAGES, build_town

#: The only addresses this will bind. Not a default -- a whitelist. Serving a
#: build tool that reads and writes local files on 0.0.0.0 hands the filesystem
#: of whoever runs it to the network they are on.
LOOPBACK = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "::1"}

#: Host header values a request may carry, without the port. Anything else is
#: refused: a name that is not one of these, resolving to 127.0.0.1, is a
#: rebinding attack rather than a user.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: What the file endpoint will hand back. An allowlist rather than a blocklist,
#: and every one of them is something this program wrote: the raster SVG, the
#: slabs, the paste order, the NPC manifest, a brief.
SERVABLE_SUFFIXES = frozenset({".svg", ".json", ".txt", ".md", ".slab"})

#: What the screen-grab endpoint will hand back, and it is a *different*
#: allowlist over a *different* root. `grab.ps1` writes to ``out/flyby`` beside
#: the repo whatever ``--out-dir`` says, so the grabs are not reachable through
#: the file endpoint and get their own route rather than a widened one.
SHOT_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})

#: How much JSON a request may carry. The build form is a few hundred bytes.
MAX_BODY = 64 * 1024

#: How much of a candidate file is read to tell a layout from a GeoJSON. Both
#: formats are discriminated in their first bytes -- `layout_version` is the
#: first key `Layout.to_dict` writes, and `type: FeatureCollection` is the
#: first key of a collection -- so a 400 MB export is never parsed to list it.
SNIFF_BYTES = 8192

#: Where sources are looked for, relative to the working directory, on top of
#: the server's own ``out_dir``. One level of subdirectories each.
DEFAULT_ROOTS = ("out", "samples", ".")

HOURS = ("day", "night")


class BadRequest(ValueError):
    """A request that will not be run, and the sentence to show the user."""


class PaletteError(RuntimeError):
    """The style cannot be built from the packs installed on this machine."""


# -- sources ------------------------------------------------------------------
#
# The browser picks from this list and sends back an `id`. It never sends a
# path, so there is no path to escape from: an id that is not in the current
# scan is simply not a file.

@dataclass(frozen=True)
class Source:
    """One file this server is willing to build from."""

    id: str
    path: pathlib.Path
    #: "layout" for a `layout.json`, "geojson" for an MFCG or FTG export.
    kind: str
    label: str
    #: A short line of what is in it -- the town's name, or the format.
    detail: str
    size: int

    def as_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "detail": self.detail, "size": self.size}


def _source_id(path: pathlib.Path) -> str:
    """A stable token for a file, so a rescan keeps the form's selection."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def _classify(path: pathlib.Path) -> tuple[str, str] | None:
    """``(kind, detail)`` for a file, or None if it is not a source.

    Read from a prefix rather than parsed. Classifying by parsing means reading
    every 400 MB export in the directory to draw a dropdown.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(SNIFF_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    if '"layout_version"' in head:
        name = re.search(r'"name"\s*:\s*"([^"]{0,60})"', head)
        source = re.search(r'"source"\s*:\s*"([^"]{0,20})"', head)
        return "layout", " ".join(
            p for p in (name.group(1) if name else "",
                        f"({source.group(1)})" if source else "") if p)
    if '"FeatureCollection"' in head:
        if '"generator"' in head and '"mfcg"' in head:
            return "geojson", "MFCG export"
        return "geojson", "GeoJSON export -- format sniffed at import"
    return None


def scan_sources(roots) -> list[Source]:
    """Every layout and town export under ``roots``, newest first.

    One level of subdirectories, because ``out/`` grows a directory per scene
    and a town's ``layout.json`` habitually sits one down.
    """
    seen: dict[pathlib.Path, Source] = {}
    mtimes: dict[pathlib.Path, float] = {}
    for root in roots:
        root = pathlib.Path(root)
        if not root.is_dir():
            continue
        candidates: list[pathlib.Path] = []
        try:
            for entry in sorted(root.iterdir()):
                if entry.is_file():
                    candidates.append(entry)
                elif entry.is_dir() and not entry.name.startswith("."):
                    try:
                        candidates += sorted(
                            c for c in entry.iterdir() if c.is_file())
                    except OSError:
                        continue
        except OSError:
            continue
        for path in candidates:
            if path.suffix.lower() not in (".json", ".geojson"):
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            what = _classify(path)
            if what is None:
                continue
            kind, detail = what
            try:
                display = str(path.relative_to(pathlib.Path.cwd()))
            except ValueError:
                display = str(path)
            try:
                stat = resolved.stat()
            except OSError:      # gone between the listing and here
                continue
            mtimes[resolved] = stat.st_mtime
            seen[resolved] = Source(
                id=_source_id(resolved), path=resolved, kind=kind,
                label=display.replace("\\", "/"), detail=detail,
                size=stat.st_size,
            )
    # Newest first: the file somebody just imported is the one they want, and
    # `out/layout.json` is rewritten by every import.
    return sorted(seen.values(), key=lambda s: (-mtimes[s.path], s.label))


# -- path safety --------------------------------------------------------------

def resolve_in(root, relative: str, *, suffixes=SERVABLE_SUFFIXES) -> pathlib.Path:
    """Resolve ``relative`` inside ``root``, or raise :class:`BadRequest`.

    ``suffixes`` is the allowlist of kinds this call will serve. It is a
    parameter rather than a module constant because the screen grabs live under
    a different root with a different set, and one widened allowlist over the
    output directory would let a request ask for kinds of file that directory
    has no business handing out.

    Four separate escapes are refused, and the order matters because each one
    catches something the next cannot:

    * a component of ``..``, rejected by name before anything touches the disk,
      so a traversal is reported as a traversal rather than as a missing file;
    * an absolute or UNC path -- ``/etc/passwd``, ``C:\\Windows\\win.ini``,
      ``//host/share/x`` -- which ``root / rel`` silently *adopts* whole.
      Refused **by name**, and that is the whole point: the containment check
      below would catch it, but only after ``Path.resolve()``, and resolving a
      UNC root is a NETWORK CALL. ``//etc/passwd`` blocks for **16 seconds** on
      this machine while SMB gives up on a host named ``etc`` (measured
      2026-08-30). A request string that stalls a handler for 16 seconds is a
      denial of service with a one-line payload, so it never reaches the disk;
    * a drive-relative path -- ``C:notes.svg`` -- which is not absolute, and is
      still taken against that drive's working directory rather than ``root``;
    * a symlink inside ``root`` pointing out of it, which is why the comparison
      is made on ``resolve()``'d paths and not on the strings.

    The absolute test is made with :class:`pathlib.PureWindowsPath` on every
    platform rather than with the native flavour. A Windows-shaped escape sent
    to a POSIX host is harmless there, but one rule that refuses it everywhere
    is easier to hold than two that differ, and the only thing it costs is a
    file literally named ``C:something`` -- which nothing here writes, since
    every name this serves comes from `_STEM` or is a fixed one like
    ``city-raster.svg``.
    """
    rel = urllib.parse.unquote(relative)
    if not rel or "\x00" in rel:
        raise BadRequest("no file named")
    parts = re.split(r"[/\\]", rel)
    if any(p == ".." for p in parts):
        raise BadRequest(f"{rel!r} leaves the output directory")
    # Before the disk, per the docstring. `drive` covers `C:` and `\\host\share`,
    # `root` covers a leading `/` or `\`; together they are every shape that
    # `root / rel` would adopt whole.
    windows = pathlib.PureWindowsPath(rel)
    if windows.drive or windows.root:
        raise BadRequest(f"{rel!r} is not a path inside the output directory")
    root = pathlib.Path(root).resolve()
    candidate = (root / rel)
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover -- platform-dependent
        raise BadRequest(f"{rel!r} cannot be resolved: {exc}") from exc
    if not resolved.is_relative_to(root):
        raise BadRequest(f"{rel!r} is outside {root}")
    if resolved.suffix.lower() not in suffixes:
        raise BadRequest(
            f"{rel!r} is not a kind of file this serves "
            f"({', '.join(sorted(suffixes))})")
    return resolved


# -- the request, as types ----------------------------------------------------
#
# Every field below is an int, a bool, or a member of a closed set. There is no
# field whose value is passed on as text to anything that could interpret it,
# and an unknown key is an error rather than something ignored -- which is what
# stops a caller reaching a `build_town` keyword this form does not offer.

#: Filename stems that cannot surprise anything downstream: `write_chunks`
#: globs on the stem and writes `<stem>-<label>.slab.txt` beside the slabs.
_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

_FIELDS = frozenset({
    "source", "stem", "style", "seed", "storeys", "chunk_tiles", "max_assets",
    "npc_budget", "fence_style", "hour", "raster_scale", "roofs", "bridges",
    "quarters", "npcs", "keep_open_country", "per_building", "by_region",
    "multi_slab", "crop",
})


def _int(body: dict, key: str, default, lo: int, hi: int, *, nullable=False):
    value = body.get(key, default)
    if value is None and nullable:
        return None
    # `bool` is an `int` subclass, so `isinstance(True, int)` is True and a
    # checkbox posted into a number field would sail through as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{key}: expected a whole number, got {value!r}")
    if not lo <= value <= hi:
        raise BadRequest(f"{key}: must be between {lo} and {hi}, got {value}")
    return value


def _bool(body: dict, key: str, default: bool) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise BadRequest(f"{key}: expected true or false, got {value!r}")
    return value


def _choice(body: dict, key: str, default: str, allowed) -> str:
    value = body.get(key, default)
    if value not in allowed:
        raise BadRequest(
            f"{key}: expected one of {', '.join(sorted(allowed))}, got {value!r}")
    return value


def read_build_request(body, sources) -> dict:
    """Turn a JSON body into typed keywords for :func:`run_build`.

    ``sources`` is the current scan; ``body['source']`` is an id from it, so a
    request cannot name a file that the server did not offer.
    """
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    unknown = sorted(set(body) - _FIELDS)
    if unknown:
        raise BadRequest(
            f"unknown field(s): {', '.join(unknown)}. This endpoint takes "
            f"named parameters only; there is no place to pass a flag or a "
            f"command through.")

    by_id = {s.id: s for s in sources}
    source_id = body.get("source")
    if not isinstance(source_id, str) or source_id not in by_id:
        raise BadRequest(
            "source: pick a file from the list. (If it was there a moment ago, "
            "refresh -- the list is the server's own scan, and a request can "
            "only name a file that is in it.)")

    stem = body.get("stem", "city")
    if not isinstance(stem, str) or not _STEM.match(stem):
        raise BadRequest(
            "stem: letters, digits, dot, dash and underscore only, up to 40")

    crop = body.get("crop")
    if crop is not None:
        if not isinstance(crop, dict) or set(crop) - {"x", "z", "w", "d"}:
            raise BadRequest("crop: expected {x, z, w, d} or null")
        crop = (
            _int(crop, "x", 0, 0, 100_000), _int(crop, "z", 0, 0, 100_000),
            _int(crop, "w", 1, 1, 100_000), _int(crop, "d", 1, 1, 100_000),
        )

    return {
        "source": by_id[source_id],
        "stem": stem,
        "style": _choice(body, "style", "medieval", set(STYLES)),
        "seed": _int(body, "seed", 0, 0, 2 ** 31 - 1),
        "storeys": _int(body, "storeys", 3, 1, 12),
        "chunk_tiles": _int(body, "chunk_tiles", DEFAULT_CHUNK_TILES, 8, 512),
        "max_assets": _int(body, "max_assets", None, 100, 500_000, nullable=True),
        "npc_budget": _int(body, "npc_budget", None, 0, 100_000, nullable=True),
        "fence_style": _choice(body, "fence_style", DEFAULT_FENCE_STYLE,
                               set(FENCE_STYLES)),
        "hour": _choice(body, "hour", "day", set(HOURS)),
        "raster_scale": _int(body, "raster_scale", 3, 1, 12),
        "roofs": _bool(body, "roofs", True),
        "bridges": _bool(body, "bridges", True),
        "quarters": _bool(body, "quarters", True),
        "npcs": _bool(body, "npcs", True),
        "keep_open_country": _bool(body, "keep_open_country", False),
        "per_building": _bool(body, "per_building", False),
        "by_region": _bool(body, "by_region", False),
        "multi_slab": _bool(body, "multi_slab", False),
        "crop": crop,
    }


# -- the paste request, as types ----------------------------------------------
#
# Same rule as the build form, and it matters more here because the values end
# up as arguments to a PowerShell script: a stem chosen from the server's own
# scan, a name matched against `_STEM`, an int in a range. Nothing else, and an
# unknown key is an error. `pastedrive` then passes each of them as its own
# argv entry -- there is no command line for them to be inside of.

_PASTE_FIELDS = frozenset({"stem", "name", "shot_every"})


def read_paste_request(body, plans) -> dict:
    """Turn a JSON body into typed keywords for :func:`run_paste`.

    ``plans`` is :func:`pastedrive.scan_plans`' output, so ``body['stem']`` can
    only name a paste order the server itself found -- the same shape as the
    build form's ``source``, and for the same reason.
    """
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    unknown = sorted(set(body) - _PASTE_FIELDS)
    if unknown:
        raise BadRequest(
            f"unknown field(s): {', '.join(unknown)}. This endpoint takes "
            f"named parameters only; there is no place to pass a flag, a "
            f"recipe or a command through.")

    stems = {p["stem"] for p in plans}
    stem = body.get("stem")
    if not isinstance(stem, str) or stem not in stems:
        raise BadRequest(
            "stem: pick a build from the list. (The list is the server's scan "
            "for <stem>-paste-order.txt in the output directory; a request can "
            "only name one that is in it.)")

    # The shot name becomes `-Name` and, through `grab.ps1`, a filename. It is
    # matched rather than sanitised: a name that has to be cleaned up is a name
    # that should have been refused.
    name = body.get("name", stem)
    if not isinstance(name, str) or not _STEM.match(name):
        raise BadRequest(
            "name: letters, digits, dot, dash and underscore only, up to 40. "
            "It names the screen grabs.")

    return {
        "stem": stem,
        "name": name,
        # One grab in hand and one after it lands, per chunk. On a hundred-chunk
        # town that is two hundred captures, so this thins them; `review.ps1`
        # always keeps the first and the last whatever it is set to.
        "shot_every": _int(body, "shot_every", 1, 1, 1000),
    }


# -- progress, as lines -------------------------------------------------------

def _pipeline_line(stage: str, fields: dict) -> str:
    """The sentence ``cli.cmd_build`` prints for one pipeline stage.

    Deliberately the same words. Two front ends narrating the same build in
    different vocabularies is how a screenshot and a terminal stop being
    comparable, which on this project is the whole review method.
    """
    if stage == "rasterized":
        return fields["tilemap"].summary()
    if stage == "npcs":
        return f"npcs: {fields['population'].summary()}"
    if stage == "budget":
        return f"chunk budget: {fields['assets']:,} assets (from board size)"
    if stage == "npc_manifest":
        return f"wrote {fields['path']}  ({fields['posts']} post(s))"
    raise KeyError(stage)


#: Every stage `pipeline.STAGES` declares must render. A new stage arriving
#: with no line is a build the UI narrates with a gap in it, and
#: `test_every_pipeline_stage_has_a_line` fails rather than letting that ship.
PIPELINE_STAGES = frozenset(STAGES)


# -- the job ------------------------------------------------------------------

@dataclass
class Job:
    """One build, running on its own thread.

    Events accumulate with a sequence number so the page can poll for what it
    has not seen; ``result`` is the whole report as JSON, filled in once.
    """

    id: str
    #: "build" or "paste". One job runs at a time across both, so the refusal
    #: has to be able to say which one is in the way.
    kind: str = "build"
    state: str = "running"          # running | done | error
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def say(self, stage: str, text: str, **extra) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "stage": stage,
                                "text": text, **extra})

    def snapshot(self, after: int = -1) -> dict:
        with self.lock:
            return {
                "job": self.id,
                "kind": self.kind,
                "state": self.state,
                "events": [e for e in self.events if e["seq"] > after],
                "next": len(self.events),
                "result": self.result,
                "error": self.error,
            }


def run_build(job: Job, params: dict, *, out_dir, palette_factory) -> None:
    """Do the work one job describes. Called on a worker thread.

    Note what is *not* here: no formatting decision, no string handed to a
    shell, and no keyword assembled from the request. ``build_town`` is called
    with the parameters spelled out, one by one, which is what makes the set of
    things this endpoint can do a closed list.
    """
    from . import importers
    
    source: Source = params["source"]
    out_dir = pathlib.Path(out_dir)
    try:
        job.say("started", f"building {source.label}")
        palette = palette_factory(params["style"], params["seed"])

        if source.kind == "geojson":
            job.say("importing", f"importing {source.label}")
            layout = importers.import_layout(source.path, seed=params["seed"])
            saved = out_dir / "layout.json"
            layout.save(saved)
            job.say("imported", f"{layout.summary()}\n  wrote {saved}")
        else:
            layout = Layout.load(source.path)

        def progress(stage: str, **fields) -> None:
            job.say(stage, _pipeline_line(stage, fields))

        result = build_town(
            layout,
            palette=palette,
            out_dir=out_dir,
            stem=params["stem"],
            seed=params["seed"],
            storeys=params["storeys"],
            roofs=params["roofs"],
            bridges=params["bridges"],
            crop=params["crop"],
            quarters=params["quarters"],
            fence_style=params["fence_style"],
            npcs=params["npcs"],
            npc_budget=params["npc_budget"],
            hour=params["hour"],
            max_assets=params["max_assets"],
            chunk_tiles=params["chunk_tiles"],
            keep_open_country=params["keep_open_country"],
            per_building=params["per_building"],
            by_region=params["by_region"],
            multi_slab=params["multi_slab"],
            raster_scale=params["raster_scale"],
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        from .slab import SlabError
        if isinstance(exc, SlabError):
            detail = (f"Could not encode slab: {exc}\nTry a smaller assets "
                      f"per chunk, or a smaller chunk edge.")
        elif isinstance(exc, (PaletteError, BadRequest, ValueError)):
            # Already a sentence written for a person -- the palette check and
            # the importers both raise with advice in them. Prefixing that with
            # a class name turns advice back into a stack trace.
            detail = str(exc)
        else:
            detail = f"{type(exc).__name__}: {exc}"
        # The event goes on the list BEFORE the state changes. A poller stops
        # polling the moment it sees a state that is not "running", so a last
        # line appended afterwards is a line nobody ever sees.
        job.say("failed", detail, traceback=traceback.format_exc())
        with job.lock:
            job.state = "error"
            job.error = detail
        return

    payload = result_json(result, out_dir)
    job.say("finished", payload["summary"])
    with job.lock:
        job.state = "done"
        job.result = payload


def run_paste(job: Job, params: dict, *, driver, out_dir) -> None:
    """Drive one tiled paste. Called on a worker thread.

    Everything that touches PowerShell is `driver`'s, including the
    preconditions -- which run *inside* the job rather than only behind the
    button, so a run started from a page that has been open since before
    somebody pressed ``G`` is still refused. `PasteRefused` is the refusal, and
    it lands as a job error with the sentence that explains it, because a paste
    that will not start is not a crash.
    """
    try:
        job.say("started", f"pasting {params['stem']} onto the current board")
        for event in driver.paste(
                stem=params["stem"], name=params["name"], out_dir=out_dir,
                shot_every=params["shot_every"]):
            stage = event.pop("stage", "said")
            text = event.pop("text", "")
            job.say(stage, text, **event)
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        if isinstance(exc, (pastedrive.PasteRefused, BadRequest, ValueError)):
            detail = str(exc)          # already a sentence written for a person
        else:
            detail = f"{type(exc).__name__}: {exc}"
        # Before the state changes, for the reason `run_build` gives: a poller
        # stops the moment the state is not "running".
        job.say("failed", detail, traceback=traceback.format_exc())
        with job.lock:
            job.state = "error"
            job.error = detail
        return

    with job.lock:
        job.state = "done"
        job.result = {"stem": params["stem"], "name": params["name"]}


# -- the result, as JSON ------------------------------------------------------

def result_json(result, out_dir) -> dict:
    """Everything ``cli.cmd_build`` prints after the build, as data.

    The findings arrive as objects with a level, a check and the whole detail
    sentence, and their ORDER IS THE REPORT'S -- worst first, exactly as
    ``Report.text()`` renders it. The page does not sort them and neither does
    this.

    ``paste_order`` and the chunk list are likewise in the order
    ``pipeline.write_chunks`` wrote them, which is **not** filename order: the
    chunk covering the anchor cell is written last so the anchor is still bare
    board for every paste before it. Sorting this list, in Python or in
    JavaScript, is the bug that leaves a quarter of a map a course high.
    """
    out_dir = pathlib.Path(out_dir).resolve()

    def rel(path) -> str | None:
        if path is None:
            return None
        p = pathlib.Path(path).resolve()
        try:
            return p.relative_to(out_dir).as_posix()
        except ValueError:  # pragma: no cover -- out_dir is where we wrote it
            return None

    report = result.report
    findings = [{"level": f.level, "check": f.check, "detail": f.detail,
                 "text": str(f)}
                for f in sorted(report.findings, key=lambda f: -_LEVELS[f.level])]
    counts = {level: sum(1 for f in findings if f["level"] == level)
              for level in ("fail", "warn", "pass")}

    covered = {cell for c in result.chunks
               for cell in (c.covers or ((c.row, c.col),))}
    grid = [[(r, c) in covered for c in range(result.cols)]
            for r in range(result.rows)]

    summary = (f"{result.assets_emitted:,} assets in "
               f"{len(result.chunks)} chunk(s)")
    if result.skipped:
        summary += (f"; {len(result.skipped)} open-country chunk(s) skipped "
                    f"({result.assets_skipped:,} assets)")

    # One copy of the paste instructions, and it is the command's. Duplicating
    # them here would put a second, drifting set of directions on the screen
    # people actually paste from.
    from .cli import PASTE_HELP, TILE_HELP

    return {
        "stem": result.stem,
        "out_dir": str(out_dir),
        "failed": result.failed,
        "worst": report.worst,
        "summary": summary,
        "layout": result.layout.summary(),
        "tilemap": result.tilemap.summary(),
        "findings": findings,
        "counts": counts,
        "assets_emitted": result.assets_emitted,
        "assets_skipped": result.assets_skipped,
        "chunk_budget": result.chunk_budget,
        "budget_from_board_size": result.budget_from_board_size,
        "largest_slab_bytes": result.largest_slab_bytes,
        "by_region": result.by_region,
        "rows": result.rows,
        "cols": result.cols,
        "tile_size": result.tile_size,
        "grid": grid,
        "paste_order": list(result.paste_order),
        "chunks": [
            {"file": c.path.name, "label": c.label, "region": c.region,
             "layer": c.layer, "name": c.name, "row": c.row, "col": c.col,
             "x0": c.x0, "x1": c.x1, "z0": c.z0, "z1": c.z1,
             "assets": c.assets, "buildings": c.buildings,
             "size_bytes": c.size_bytes, "cells": len(c.covers)}
            for c in result.chunks
        ],
        "skipped": [
            {"label": c.label, "region": c.region, "layer": c.layer,
             "x0": c.x0, "x1": c.x1, "z0": c.z0, "z1": c.z1,
             "assets": c.assets}
            for c in result.skipped
        ],
        "raster_svg": rel(result.raster_svg),
        "npc_manifest": rel(result.npc_manifest),
        "multislab": rel(result.multislab),
        "paste_order_file": rel(result.paste_order_path),
        "help": TILE_HELP if result.by_region else PASTE_HELP,
    }


#: Same order `verify.Report.text()` sorts by. Imported lazily elsewhere; kept
#: here so `result_json` does not pay for `verify` on a module import.
_LEVELS = {"pass": 0, "warn": 1, "fail": 2}


# -- palette ------------------------------------------------------------------

def catalog_palette_factory(catalog_path=None, install=None):
    """The real palette, with the catalog loaded once and kept.

    Building a catalog reads the whole TaleSpire install, so it happens on the
    worker thread the first time a build runs -- never in a request handler.
    A style the installed packs cannot supply fails the *job*, with the same
    sentences the command line prints, rather than 500ing a request.
    """
    from .catalog import load_or_build
    from .palette import Palette

    cache: dict[str, object] = {}
    lock = threading.Lock()

    def factory(style: str, seed: int):
        with lock:
            if "catalog" not in cache:
                cache["catalog"] = load_or_build(catalog_path, install)
        palette = Palette.named(cache["catalog"], style, seed)
        problems = palette.validate()
        if problems:
            raise PaletteError(
                f"Style {style!r} cannot be used with your installed packs:\n  "
                + "\n  ".join(problems))
        return palette

    return factory


# -- HTTP ---------------------------------------------------------------------

_UI_DIR = pathlib.Path(__file__).resolve().parent / "ui"

#: The whole API. A method, a path pattern, and the handler that answers it --
#: there is no route that takes a verb or an operation *as data*, which is the
#: structural half of "named operations". A chat screen is one more row.
# --------------------------------------------------------------------------
# the camera screen
# --------------------------------------------------------------------------

#: Everything the camera screen may send. Same rule as the build form: an
#: unrecognised key is refused rather than ignored, because a control that
#: silently does nothing is the worst kind of broken.
_CAMERA_FIELDS = {"rect", "yaw", "pitch", "width", "height", "margin",
                  "targets", "at", "source"}


def _num(body: dict, key: str, default, lo: float, hi: float):
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadRequest(f"{key}: expected a number, got {value!r}")
    if not lo <= float(value) <= hi:
        raise BadRequest(f"{key}: must be between {lo} and {hi}, got {value}")
    return float(value)


def _rect(value, key: str):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   for v in value)):
        raise BadRequest(f"{key}: expected four numbers [x0, z0, x1, z1]")
    x0, z0, x1, z1 = (float(v) for v in value)
    if x1 <= x0 or z1 <= z0:
        raise BadRequest(f"{key}: the second corner must be past the first")
    return (x0, z0, x1, z1)


#: How many building outlines the board plan will send. East Tradebourne has
#: 989 and a plan view a few hundred pixels wide cannot show them apart, so
#: beyond this they are sent as boxes only -- and past `_BOARD_MAX` the biggest
#: ones win, because a plan that drops the market hall to keep nine sheds is
#: worse than one that says it dropped anything.
_BOARD_MAX = 1200


def _bbox(ring):
    xs = [p[0] for p in ring]
    zs = [p[1] for p in ring]
    return [round(min(xs), 1), round(min(zs), 1),
            round(max(xs), 1), round(max(zs), 1)]


def board_plan(path) -> dict:
    """The town in plan, in TILE coordinates, for drawing under a frustum.

    **Layout coordinates are already tiles.** `raster.rasterize` takes
    `layout.width` and `layout.depth` as the tile dimensions and shifts rings
    by a padding of zero, so a building's ring is in the same space as
    `Camera.footprint()`. That is what makes this overlay exact: there is no
    scale to agree on and nothing to line up.

    Boxes rather than outlines. At the size this is drawn a building is a few
    pixels across, an outline and its bounding box are the same picture, and
    the box is a quarter of the bytes.
    """
    layout = Layout.load(path)
    buildings = [(_bbox(b.ring), b.kind) for b in layout.buildings if b.ring]
    dropped = 0
    if len(buildings) > _BOARD_MAX:
        buildings.sort(
            key=lambda bk: (bk[0][2] - bk[0][0]) * (bk[0][3] - bk[0][1]),
            reverse=True)
        dropped = len(buildings) - _BOARD_MAX
        buildings = buildings[:_BOARD_MAX]
    # **Water as its own outline, not a box.** A river is a long diagonal
    # polygon and its bounding box is meaningless: East Tradebourne's second
    # water area spans (-278, -784) to (1468, 607), which is larger than the
    # whole 739x598 board and extends well off it. Drawn as a rectangle it
    # painted most of the town teal -- a picture that is not merely ugly but
    # wrong about where the river is.
    #
    # They are sent whole and the SVG clips them. On an FTG import they often
    # reach a long way outside the cropped core -- `ftg.core_cluster` crops to
    # the settled cluster and does not trim the area rings -- so on East
    # Tradebourne one of them runs from z=-784 to z=607 against a 598-tile
    # board, and most of it is simply off the plan. That is the map being
    # honest about its own source, not a drawing fault.
    water = [[[round(x, 1), round(z, 1)] for x, z in a.ring[:400]]
             for a in layout.areas_of("water") if len(a.ring) >= 3][:40]
    return {
        "name": layout.name,
        "extent": [0.0, 0.0, round(layout.width, 1), round(layout.depth, 1)],
        "buildings": [b for b, _ in buildings],
        "kinds": [k for _, k in buildings],
        "water": water,
        "walls": [[[round(x, 1), round(z, 1)] for x, z in ring]
                  for ring in layout.walls[:4]],
        "dropped": dropped,
    }


def boxes_in_frame(cam, boxes, margin: float) -> list[int]:
    """Which boxes are wholly inside the frame. Indices, so the page can
    colour them differently rather than being told a number to believe."""
    return [i for i, b in enumerate(boxes)
            if cam.covers_all(camera.rect_corners(b), margin)]


def read_camera_request(body, sources=()) -> dict:
    """Turn a JSON body into typed keywords for :func:`plan_camera`.

    ``sources`` is the current scan; ``body['source']`` is an id from it, so a
    request cannot name a file the server did not offer -- the same rule the
    build form follows, and the reason neither screen takes a path.
    """
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    unknown = sorted(set(body) - _CAMERA_FIELDS)
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")
    if "rect" not in body:
        raise BadRequest("rect: required, as [x0, z0, x1, z1] in tiles")

    targets = body.get("targets") or []
    if not isinstance(targets, list):
        raise BadRequest("targets: expected a list of [x0, z0, x1, z1]")
    if len(targets) > 500:
        raise BadRequest(f"targets: at most 500, got {len(targets)}")

    at = body.get("at")
    if at is not None:
        if (not isinstance(at, (list, tuple)) or len(at) != 5
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       for v in at)):
            raise BadRequest("at: expected [fx, fz, dist, yaw, pitch]")
        at = tuple(float(v) for v in at)

    source = body.get("source") or None
    chosen = None
    if source is not None:
        by_id = {s.id: s for s in sources}
        if source not in by_id:
            raise BadRequest(f"source: no such file offered ({source!r})")
        chosen = by_id[source]
        if chosen.kind != "layout":
            raise BadRequest(
                "source: the board plan needs a layout.json. A GeoJSON export "
                "has not been rasterised yet, so it has no tile coordinates "
                "to draw in -- build it first.")

    return {
        "source": chosen,
        "rect": _rect(body["rect"], "rect"),
        "yaw": _num(body, "yaw", 0.0, -3600.0, 3600.0),
        "pitch": _num(body, "pitch", 55.0, 0.0, 90.0),
        "width": _int(body, "width", 1920, 320, 7680),
        "height": _int(body, "height", 1080, 240, 4320),
        "margin": _num(body, "margin", 40.0, 0.0, 400.0),
        "targets": [_rect(t, f"targets[{i}]") for i, t in enumerate(targets)],
        "at": at,
    }


def plan_camera(params: dict, *, config_path=None) -> dict:
    """Frame a board rectangle, and say how to get the camera there.

    Pure arithmetic over :mod:`citysmith.camera` -- no game driven, nothing
    spawned, and no file touched beyond `config/camera.json`. That is what lets
    the camera screen work on any machine, unlike paste.

    (The word for spawning a child process is deliberately not written here:
    `test_nothing_in_the_server_can_reach_a_shell` reads this module's source
    for it, and a blunt guard that has to reason about context is not a guard.)
    """
    rig = camera.load_rig(config_path)
    lens = rig.lens(params["width"], params["height"])
    rect = params["rect"]
    framing = camera.frame_rect(rect, rig=rig, lens=lens, yaw=params["yaw"],
                                pitch=params["pitch"],
                                margin_px=params["margin"])
    cam = camera.Camera(lens, framing.pose)

    start = (camera.Pose(*_at_order(params["at"]))
             if params["at"] is not None else None)
    plan = (camera.plan(start, framing.pose, rig=rig, lens=lens)
            if start is not None else None)

    targets = params["targets"]
    held = [i for i, t in enumerate(targets)
            if cam.covers_all(camera.rect_corners(t), params["margin"])]

    board = None
    if params.get("source") is not None:
        board = board_plan(params["source"].path)
        board["in_frame"] = boxes_in_frame(cam, board["buildings"],
                                           params["margin"])

    shots = []
    if not framing.fits and targets:
        shots = [f.as_json() for f in camera.shot_list(
            targets, rig=rig, lens=lens, yaw=params["yaw"],
            pitch=params["pitch"], margin_px=params["margin"])]

    return {
        "framing": framing.as_json(),
        "footprint": [[round(x, 2), round(z, 2)] for x, z in cam.footprint()],
        "visible_bounds": [round(v, 2) for v in cam.visible_bounds()],
        "sees_horizon": cam.sees_horizon(),
        "px_per_tile": [round(v, 2) for v in cam.px_per_tile()],
        "anchor_slide_1_5": round(cam.anchor_slide(1.5), 3),
        "plan": plan.as_json() if plan is not None else None,
        "targets": {"total": len(targets), "in_frame": len(held),
                    "indices": held},
        "shot_list": shots,
        "board": board,
        "rig": rig_json(rig, config_path),
    }


def rig_json(rig, config_path=None) -> dict:
    """The constants, with provenance, and any config key that does nothing."""
    return {
        "constants": [
            {"name": name, "value": c.value, "source": c.source,
             "measured": c.measured, "residual": c.residual}
            for name, c in sorted(rig.const.items(),
                                  key=lambda kv: (kv[1].measured, kv[0]))
        ],
        "unknown_keys": camera.unknown_keys(config_path),
    }


def _at_order(at):
    fx, fz, dist, yaw, pitch = at
    return (fx, fz, 0.0, dist, yaw, pitch)


# --------------------------------------------------------------------------
# the 3D preview
# --------------------------------------------------------------------------

_VIEW_FIELDS = {"source", "pose", "drag", "width", "height"}

#: What the preview's mouse does, and it is deliberately the game's own set.
#: Dragging the preview runs the *rig* -- the measured control model -- so the
#: sensitivities are TaleSpire's and so are the stops. You cannot drag the
#: preview past a 78-degree pitch because the camera cannot go there, which
#: means the preview can never show a shot that cannot be taken.
_DRAGS = ("orbit", "pan", "scroll", "none")


def read_view_request(body, sources=()) -> dict:
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    unknown = sorted(set(body) - _VIEW_FIELDS)
    if unknown:
        raise BadRequest(f"unknown field(s): {', '.join(unknown)}")

    pose = body.get("pose")
    if (not isinstance(pose, (list, tuple)) or len(pose) != 5
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   for v in pose)):
        raise BadRequest("pose: expected [fx, fz, dist, yaw, pitch]")
    fx, fz, dist, yaw, pitch = (float(v) for v in pose)
    if not 0.1 <= dist <= 10_000:
        raise BadRequest(f"pose: slant range out of range ({dist})")

    drag = body.get("drag") or {}
    if not isinstance(drag, dict):
        raise BadRequest("drag: expected an object")
    kind = _choice(drag, "kind", "none", _DRAGS)

    by_id = {s.id: s for s in sources}
    source = body.get("source") or None
    if source is not None and source not in by_id:
        raise BadRequest(f"source: no such file offered ({source!r})")
    chosen = by_id[source] if source else None
    if chosen is not None and chosen.kind != "layout":
        raise BadRequest("source: the preview needs a layout.json")

    return {
        "source": chosen,
        "pose": camera.Pose(fx, fz, 0.0, dist, yaw, pitch),
        "drag": {"kind": kind,
                 "dx": _int(drag, "dx", 0, -20_000, 20_000),
                 "dy": _int(drag, "dy", 0, -20_000, 20_000),
                 "ticks": _int(drag, "ticks", 0, -200, 200)},
        "width": _int(body, "width", 960, 160, 7680),
        "height": _int(body, "height", 540, 120, 4320),
    }


#: One layout, parsed once. A drag asks for a frame every few milliseconds and
#: East Tradebourne's layout.json is 550 KB; re-reading it per frame turns a
#: pan into a slideshow. Keyed on the path and its mtime, so editing a layout
#: still takes effect.
_LAYOUT_CACHE: dict = {}


def _cached_scene(path):
    key = (str(path), path.stat().st_mtime_ns)
    hit = _LAYOUT_CACHE.get(key)
    if hit is None:
        layout = Layout.load(path)
        hit = (preview.boxes_from_layout(layout),
               preview.ground_grid([0.0, 0.0, layout.width, layout.depth]),
               [[[x, z] for x, z in a.ring[:400]]
                for a in layout.areas_of("water") if len(a.ring) >= 3][:40],
               layout.name)
        _LAYOUT_CACHE.clear()
        _LAYOUT_CACHE[key] = hit
    return hit


def preview_view(params: dict, *, config_path=None) -> dict:
    """Apply the drag through the rig, then project the board through it."""
    rig = camera.load_rig(config_path)
    pose = params["pose"]
    drag = params["drag"]
    if drag["kind"] == "orbit":
        pose = rig.orbit(pose, drag["dx"], drag["dy"])
    elif drag["kind"] == "scroll":
        pose = rig.scroll(pose, drag["ticks"])
    elif drag["kind"] == "pan":
        # Pan moves what the camera is looking AT, in the camera's own frame,
        # scaled by how big a tile is on screen at the centre -- so a drag of
        # N pixels moves the town under the cursor by about N pixels, which is
        # what a hand on a map does.
        lens = rig.lens(params["width"], params["height"])
        cam = camera.Camera(lens, pose)
        across, along = cam.px_per_tile()
        if across > 0 and along > 0:
            yr = math.radians(pose.yaw)
            side = -drag["dx"] / across
            ahead = drag["dy"] / along
            pose = dataclasses.replace(
                pose,
                fx=pose.fx + side * math.cos(yr) + ahead * math.sin(yr),
                fz=pose.fz - side * math.sin(yr) + ahead * math.cos(yr))

    lens = rig.lens(params["width"], params["height"])
    cam = camera.Camera(lens, pose)

    boxes, grid, water, name = ((), (), (), None)
    if params["source"] is not None:
        boxes, grid, water, name = _cached_scene(params["source"].path)

    scene = preview.render(cam, boxes, ground=grid, water=water)
    scene["pose"] = pose.as_json()
    scene["town"] = name
    scene["px_per_tile"] = [round(v, 2) for v in cam.px_per_tile()]
    scene["sees_horizon"] = cam.sees_horizon()
    scene["at_stop"] = {
        "pitch_max": abs(pose.pitch - rig["pitch_max_deg"]) < 0.05,
        "pitch_min": abs(pose.pitch - rig["pitch_min_deg"]) < 0.05,
        "dist_max": abs(pose.dist - rig["dist_max"]) < 0.05,
        "dist_min": abs(pose.dist - rig["dist_min"]) < 0.05,
    }
    return scene


_ROUTES = (
    ("GET", re.compile(r"^/$"), "page_index"),
    ("GET", re.compile(r"^/app\.css$"), "page_css"),
    ("GET", re.compile(r"^/app\.js$"), "page_js"),
    ("GET", re.compile(r"^/api/options$"), "api_options"),
    ("GET", re.compile(r"^/api/sources$"), "api_sources"),
    ("POST", re.compile(r"^/api/build$"), "api_build_start"),
    ("GET", re.compile(r"^/api/build/([0-9a-f]{16})$"), "api_build_poll"),
    ("GET", re.compile(r"^/api/files/(.+)$"), "api_file"),
    # The paste screen. Note that no two rows share a pattern: `_dispatch`
    # answers 405 on the first pattern that matches with the wrong verb, so a
    # second row for the same path with a different verb would be unreachable.
    ("GET", re.compile(r"^/api/paste/plans$"), "api_paste_plans"),
    ("POST", re.compile(r"^/api/paste/preflight$"), "api_paste_preflight"),
    ("POST", re.compile(r"^/api/paste$"), "api_paste_start"),
    ("GET", re.compile(r"^/api/paste/([0-9a-f]{16})$"), "api_paste_poll"),
    ("GET", re.compile(r"^/api/paste/shots/(.+)$"), "api_paste_shot"),
    # The camera screen. Pure arithmetic over `citysmith.camera`, so unlike
    # paste it works on any machine -- there is no game to drive, only a model
    # of one.
    ("GET", re.compile(r"^/api/camera/rig$"), "api_camera_rig"),
    ("POST", re.compile(r"^/api/camera/plan$"), "api_camera_plan"),
    ("POST", re.compile(r"^/api/camera/view$"), "api_camera_view"),
    ("POST", re.compile(r"^/api/camera/drive$"), "api_camera_drive"),
)

#: No external origin is reachable from the page, so an Anthropic key could not
#: leave through the browser even if something put one there. `img-src` allows
#: data: for nothing in particular today; `connect-src 'self'` is the clause
#: that matters.
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
        "frame-ancestors 'none'; base-uri 'none'")


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "citysmith"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing -------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 -- stdlib name
        self.server.log(f"{self.address_string()} {fmt % args}")

    def _headers(self, code: int, ctype: str, length: int, extra=()) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No Access-Control-Allow-Origin, ever. A page on another origin can
        # send this server a request; without that header it can never read
        # the answer.
        self.send_header("Cache-Control", "no-store")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()

    def _send(self, code: int, ctype: str, body: bytes, extra=()) -> None:
        self._headers(code, ctype, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(payload).encode("utf-8"))

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def _host_is_loopback(self) -> bool:
        """Refuse anything whose Host header is not a loopback name.

        This is the DNS-rebinding guard. A page on the open web can make the
        browser resolve its own hostname to 127.0.0.1 and then talk to whatever
        is listening here; the one thing it cannot forge is the Host header.
        """
        host = self.headers.get("Host", "")
        if not host:
            return False
        if host.startswith("["):                       # [::1]:8765
            name = host[:host.index("]") + 1] if "]" in host else host
        else:
            name = host.rsplit(":", 1)[0] if ":" in host else host
        return name in LOOPBACK_HOSTS

    def _dispatch(self, method: str) -> None:
        if not self._host_is_loopback():
            self._error(403, "This server answers loopback requests only.")
            return
        path = urllib.parse.urlsplit(self.path).path
        for verb, pattern, name in _ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            if verb != method:
                self._error(405, f"{path} takes {verb}, not {method}")
                return
            try:
                getattr(self, name)(*match.groups())
            except BadRequest as exc:
                self._error(400, str(exc))
            except FileNotFoundError as exc:
                self._error(404, str(exc))
            except Exception as exc:  # noqa: BLE001 -- answered, not dropped
                # Without this the connection is closed mid-response and the
                # page reports "lost contact with the server", which is a
                # much worse description of a bug than the bug is.
                self.server.log(traceback.format_exc())
                self._error(500, f"{type(exc).__name__}: {exc}")
            return
        self._error(404, f"no such endpoint: {path}")

    def do_GET(self):  # noqa: N802 -- stdlib name
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802 -- stdlib name
        self._dispatch("POST")

    def _body(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            # Also the CSRF guard: a cross-origin <form> can only post
            # form-encoded or plain text, so requiring JSON means any
            # cross-origin write has to be preflighted, and the preflight
            # fails because no CORS header is ever sent.
            raise BadRequest("expected Content-Type: application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise BadRequest("bad Content-Length") from None
        if length > MAX_BODY:
            raise BadRequest(f"body over {MAX_BODY} bytes")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BadRequest(f"body is not JSON: {exc}") from exc

    # -- pages ----------------------------------------------------------------

    def _static(self, name: str, ctype: str) -> None:
        # A fixed name from `_ROUTES`, not anything the request said.
        try:
            body = (_UI_DIR / name).read_bytes()
        except OSError as exc:  # pragma: no cover -- shipped with the package
            self._error(500, f"missing UI asset {name}: {exc}")
            return
        self._send(200, ctype, body)

    def page_index(self):
        self._static("index.html", "text/html; charset=utf-8")

    def page_css(self):
        self._static("app.css", "text/css; charset=utf-8")

    def page_js(self):
        self._static("app.js", "text/javascript; charset=utf-8")

    # -- API ------------------------------------------------------------------

    def api_options(self):
        """The closed vocabularies the form is built from.

        Sent rather than hard-coded in the page for one reason: a style or a
        fence style that exists in `palette.py` and not in the dropdown is a
        feature the UI silently hides.
        """
        self._json(200, {
            "styles": sorted(STYLES),
            "fence_styles": sorted(FENCE_STYLES),
            "hours": list(HOURS),
            "out_dir": str(self.server.out_dir),
            "defaults": {
                "stem": "city", "style": "medieval", "seed": 0, "storeys": 3,
                "chunk_tiles": DEFAULT_CHUNK_TILES,
                "fence_style": DEFAULT_FENCE_STYLE, "hour": "day",
                "raster_scale": 3, "roofs": True, "bridges": True,
                "quarters": True, "npcs": True, "keep_open_country": False,
                "per_building": False, "by_region": False,
                "multi_slab": False,
            },
            # A boolean and nothing else. The key itself never leaves this
            # process, and the browser has no route to api.anthropic.com.
            "ai_available": bool(os.environ.get("ANTHROPIC_API_KEY")),
            # Whether this machine can drive a paste at all, and the sentence
            # to show when it cannot. The page renders the sentence rather than
            # a control, because a button that can only fail is worse than no
            # button: it makes a platform limit look like a broken feature.
            "paste": self.server.paste_driver.as_json(),
        })

    def api_sources(self):
        # The Rescan button. It is the one caller that must not be served a
        # cached list: its whole purpose is to pick up a file that appeared.
        self._json(200, {"sources": [s.as_json()
                                     for s in self.server.sources(fresh=True)]})

    #: Why a second job is refused, keyed on what is already running. Two
    #: builds sharing an output directory delete each other's slabs; a build
    #: during a paste rewrites the very files being pasted, one chunk at a
    #: time, which is the same damage arriving more slowly.
    _BUSY = {
        "build": ("A build is already running. Two builds sharing an output "
                  "directory delete each other's slab files, so this waits "
                  "rather than racing."),
        "paste": ("A paste is running. It is driving TaleSpire's window with "
                  "synthetic input and reading the slabs off disk as it goes, "
                  "so nothing else starts until it is done."),
    }

    def _start(self, kind: str, target, params: dict, **kwargs) -> None:
        """Claim the one job slot and run ``target`` on a worker.

        The look and the claim are one lock hold. Split into two they are a
        race that only shows up when somebody double-clicks, which is exactly
        when it matters.
        """
        with self.server.jobs_lock:
            busy = next((j for j in self.server.jobs.values()
                         if j.state == "running"), None)
            if busy is None:
                job = Job(id=secrets.token_hex(8), kind=kind)
                self.server.jobs[job.id] = job
        if busy is not None:
            self._error(409, self._BUSY[busy.kind])
            return
        thread = threading.Thread(
            target=target, args=(job, params), kwargs=kwargs,
            daemon=True, name=f"citysmith-{kind}-{job.id}")
        thread.start()
        self._json(202, {"job": job.id})

    def api_build_start(self):
        params = read_build_request(self._body(), scan_sources(self.server.roots))
        self._start("build", run_build, params,
                    out_dir=self.server.out_dir,
                    palette_factory=self.server.palette_factory)

    def api_build_poll(self, job_id: str):
        self._poll("build", job_id)

    def _poll(self, kind: str, job_id: str) -> None:
        job = self.server.jobs.get(job_id)
        if job is None or job.kind != kind:
            raise FileNotFoundError(f"no {kind} job {job_id}")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            after = int(query.get("after", ["-1"])[0])
        except ValueError:
            raise BadRequest("after: expected a whole number") from None
        self._json(200, job.snapshot(after))

    # -- the paste screen -----------------------------------------------------
    #
    # Windows only, and it is the server that says so: the page asks
    # `/api/options` and renders a sentence instead of a control off Windows.
    # These endpoints refuse there anyway, because a hidden button is a UI
    # decision and this is the rule.

    def _require_paste(self) -> None:
        driver = self.server.paste_driver
        if not driver.available:
            raise BadRequest(driver.note)

    def api_paste_plans(self):
        """Every build in the output directory that has a paste order.

        The browser picks a stem from this and sends the stem back, so a
        request can only name a plan the server itself found -- the same shape
        as the build form's source list.
        """
        self._json(200, {
            "paste": self.server.paste_driver.as_json(),
            "plans": pastedrive.scan_plans(self.server.out_dir),
        })

    def api_paste_preflight(self):
        """Look at the preconditions now, and say what each probe returned.

        This one *does* block its request, which nothing else here does. It is
        bounded -- two probes, seconds, each with a timeout -- and it is the
        thing the user pressed the button for; the server is threaded, so a
        slow probe does not hold up the page. The build endpoint's rule is
        about work measured in minutes.
        """
        self._body()                        # enforce the JSON content type
        self._require_paste()
        self._json(200, self.server.paste_driver.preflight().as_json())

    def api_paste_start(self):
        """Start a tiled paste run. 202 and a job id, like a build.

        The preconditions are checked again on the worker before anything is
        spawned: the button's check can be minutes old, and ``G`` is one
        keystroke.
        """
        # The body is read before the platform check so the connection is left
        # in a state the next request can use, whichever way this goes.
        body = self._body()
        self._require_paste()
        params = read_paste_request(
            body, pastedrive.scan_plans(self.server.out_dir))
        self._start("paste", run_paste, params,
                    driver=self.server.paste_driver,
                    out_dir=self.server.out_dir)

    def api_paste_poll(self, job_id: str):
        self._poll("paste", job_id)

    def api_paste_shot(self, relative: str):
        """One screen grab from the run, so a bad paste shows on this page.

        Its own root and its own allowlist: `grab.ps1` writes to ``out/flyby``
        beside the repository whatever ``--out-dir`` is, so these are not under
        the directory :meth:`api_file` serves and must not be reached by
        widening that one.
        """
        path = resolve_in(self.server.shots_dir, relative,
                          suffixes=SHOT_SUFFIXES)
        if not path.is_file():
            raise FileNotFoundError(f"{relative} has not been captured")
        ctype = {".png": "image/png"}.get(path.suffix.lower(), "image/jpeg")
        self._send(200, ctype, path.read_bytes())

    def api_camera_rig(self):
        """The camera constants and where each of them came from.

        Sent rather than baked into the page for the same reason the style list
        is: a constant that is an assumption has to look different from one
        that was measured, wherever it is read.
        """
        self._json(200, rig_json(camera.load_rig(self.server.camera_config),
                                 self.server.camera_config))

    def api_camera_plan(self):
        try:
            params = read_camera_request(self._body(), self.server.sources())
        except BadRequest as exc:
            self._error(400, str(exc))
            return
        self._json(200, plan_camera(params,
                                    config_path=self.server.camera_config))

    def api_camera_view(self):
        try:
            params = read_view_request(self._body(), self.server.sources())
        except BadRequest as exc:
            self._error(400, str(exc))
            return
        self._json(200, preview_view(params,
                                     config_path=self.server.camera_config))

    def api_camera_drive(self):
        """Run the plan that takes the real camera to the preview's pose.

        **Open loop, and it says so.** Nothing here reads the game back: the
        page supplies where the camera is, this works out the moves and runs
        them, and the reply reports where the plan *lands*, not where the
        camera *is*. Those agree to about a degree (measured over four driven
        moves) and they drift as errors accumulate, which is why the reply
        names `camera_read.py` rather than pretending otherwise.
        """
        body = self._body()
        if not isinstance(body, dict):
            self._error(400, "expected a JSON object")
            return
        unknown = sorted(set(body) - {"at", "pose", "width", "height"})
        if unknown:
            self._error(400, f"unknown field(s): {', '.join(unknown)}")
            return
        try:
            at = [float(v) for v in body["at"]]
            target = [float(v) for v in body["pose"]]
            if len(at) != 5 or len(target) != 5:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            self._error(400, "at and pose: each [fx, fz, dist, yaw, pitch]")
            return

        driver = self.server.paste_driver
        if not driver.available:
            self._error(409, driver.note)
            return

        rig = camera.load_rig(self.server.camera_config)
        lens = rig.lens(_int(body, "width", 1920, 320, 7680),
                        _int(body, "height", 1080, 240, 4320))
        start = camera.Pose(at[0], at[1], 0.0, at[2], at[3], at[4])
        want = camera.Pose(target[0], target[1], 0.0,
                           target[2], target[3], target[4])
        plan = camera.plan(start, want, rig=rig, lens=lens)
        if not plan.moves:
            self._json(200, {"text": "already there -- no moves needed.",
                             "landed": start.as_json(), "moves": []})
            return
        try:
            out = driver.camera_moves([m.as_json() for m in plan.moves])
        except pastedrive.PasteRefused as exc:
            self._error(409, str(exc))
            return

        end = plan.end
        residual = plan.residual()
        text = (f"ran {len(out['moves'])} move(s). The plan lands at bearing "
                f"{end.yaw:.1f}, pitch {end.pitch:.1f}, range {end.dist:.1f} "
                f"-- open loop, so read it back with camera_read.py before "
                f"trusting it far.")
        if not out["ok"]:
            text = "a move failed: " + (out["moves"][-1]["out"] or "no output")
        self._json(200, {"text": text, "landed": end.as_json(),
                         "residual": residual, "moves": out["moves"],
                         "ok": out["ok"]})

    def api_file(self, relative: str):
        path = resolve_in(self.server.out_dir, relative)
        if not path.is_file():
            raise FileNotFoundError(f"{relative} has not been written")
        ctype = {".svg": "image/svg+xml",
                 ".json": "application/json",
                 ".md": "text/markdown; charset=utf-8"}.get(
                     path.suffix.lower(), "text/plain; charset=utf-8")
        self._send(200, ctype, path.read_bytes())


class _Server(http.server.ThreadingHTTPServer):
    """Threaded so a poll is answered while a build runs.

    ``allow_reuse_address`` is off deliberately. On Windows SO_REUSEADDR lets a
    second process bind a port another process is already listening on and
    steal its traffic, so the stdlib default is exactly wrong for a server that
    is only ever local and only ever short-lived.
    """

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, *, out_dir, roots, palette_factory,
                 log, paste_driver, camera_config=None):
        super().__init__(address, handler)
        #: `config/camera.json` unless a test says otherwise. Held here rather
        #: than read at each request so a test can point it at a fixture.
        self.camera_config = camera_config
        self.out_dir = pathlib.Path(out_dir)
        self.roots = tuple(roots)
        self.palette_factory = palette_factory
        self.paste_driver = paste_driver
        #: Where the screen grabs are, which is `grab.ps1`'s directory and NOT
        #: ``out_dir``. Held here so a test can point it somewhere harmless.
        self.shots_dir = pathlib.Path(paste_driver.shots)
        self.log = log
        self.jobs: dict[str, Job] = {}
        self.jobs_lock = threading.Lock()
        self._sources: tuple[float, list] = (0.0, [])

    #: How long a source scan is reused. **This is a performance fix, not a
    #: convenience.** `scan_sources` walks `out/` and one level under it,
    #: opening and sniffing every candidate; against a working directory full
    #: of build artefacts and screen grabs it measured **706 ms**, while the
    #: preview render it was called for measured 2.9. Every dragged frame paid
    #: it, and the preview was unusable on a big town for that reason alone.
    #:
    #: Short, because a build writes a new layout and the page should offer it
    #: without a restart; the Rescan button bypasses this entirely.
    SOURCE_TTL = 3.0

    def sources(self, *, fresh: bool = False) -> list:
        now = time.monotonic()
        when, cached = self._sources
        if fresh or not cached or now - when > self.SOURCE_TTL:
            cached = scan_sources(self.roots)
            self._sources = (now, cached)
        return cached


def make_server(*, host: str = "127.0.0.1", port: int = 8765, out_dir="out",
                roots=None, palette_factory=None, log=None, paste_driver=None,
                camera_config=None):
    """Bind a server without serving it. ``port=0`` takes an ephemeral one.

    Raises :class:`ValueError` for any host that is not loopback. That is the
    whole of the network exposure story: there is no flag, no environment
    variable and no config file that makes this listen anywhere else.
    """
    if host not in LOOPBACK:
        raise ValueError(
            f"refusing to bind {host!r}: this serves 127.0.0.1 only. It reads "
            f"and writes local files and runs builds, so a listener on a "
            f"network interface would hand those to the network.")
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if roots is None:
        roots = (out_dir, *DEFAULT_ROOTS)
    if palette_factory is None:
        palette_factory = catalog_palette_factory()
    if paste_driver is None:
        paste_driver = pastedrive.Driver()
    if log is None:
        def log(line: str) -> None:
            print(f"  {line}", flush=True)

    return _Server((LOOPBACK[host], port), _Handler, out_dir=out_dir,
                   roots=roots, palette_factory=palette_factory, log=log,
                   paste_driver=paste_driver, camera_config=camera_config)


def serve(*, host: str = "127.0.0.1", port: int = 8765, out_dir="out",
          roots=None, palette_factory=None, log=None, paste_driver=None) -> int:
    """Serve the build UI until interrupted. Loopback only, always."""
    server = make_server(host=host, port=port, out_dir=out_dir, roots=roots,
                         palette_factory=palette_factory, log=log,
                         paste_driver=paste_driver)
    bound = server.server_address
    print(f"citysmith UI on http://{bound[0]}:{bound[1]}/")
    print(f"  output directory: {server.out_dir}")
    if server.paste_driver.available:
        print(f"  screen grabs:     {server.shots_dir}")
    else:
        print("  paste screen:     not on this platform (Windows only)")
    print("  loopback only; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:  # pragma: no cover -- an entry point
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m citysmith.uiserver",
        description="Local web UI for citysmith builds. Loopback only.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out-dir", default="out",
                        help="where builds are written, and the only "
                             "directory the browser can read files from")
    parser.add_argument("--catalog", default=None,
                        help="catalog.json (default: the usual place)")
    parser.add_argument("--talespire-path", default=None)
    args = parser.parse_args(argv)
    return serve(port=args.port, out_dir=args.out_dir,
                 palette_factory=catalog_palette_factory(
                     args.catalog, args.talespire_path))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
