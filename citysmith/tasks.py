"""What was designed, what was built, and the difference between the two.

**This exists because "still open" is not a state anything checks.** Yards were
designed in `docs/building-massing.md`, listed as outstanding in two documents,
described twice as "waiting on nothing" -- and deferred by three consecutive
passes without anyone noticing, because a paragraph of prose saying a thing is
unbuilt looks exactly like a paragraph of prose saying it is built.

So a task carries **evidence**: the dotted path of the symbol that will exist
when it is done, or the test that will pass. ``citysmith tasks check`` imports
each one. A task marked DONE whose evidence is missing is reported as a lie,
and a task marked OPEN whose evidence is already there is reported as stale
bookkeeping. Neither is a judgement about quality -- `verify` does that -- it
is only the question of whether the thing exists at all.

The store is `tasks.json` at the repo root, the same shape of record as
`campaign/boards.json`: one file, hand-editable, no schema migrations, and the
only place the answer lives.

    citysmith tasks                     everything, grouped by state
    citysmith tasks --check             verify every claim against the code
    citysmith tasks --add "..." --doc docs/fencing.md --evidence citysmith.build._lay_fences
    citysmith tasks --done fence-gate
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import pathlib
import re
from dataclasses import dataclass, field

#: Where the record lives, relative to the repo root.
STORE = "tasks.json"

#: The states a task can be in. Deliberately few: this tracks whether a
#: designed thing exists, not how work feels.
STATES = ("open", "doing", "done", "dropped")

#: What `check` can say about a task.
OK, MISSING, STALE, UNCHECKABLE = "ok", "missing", "stale", "uncheckable"


@dataclass
class Task:
    """One designed thing, and how to tell whether it is built."""

    id: str
    what: str
    state: str = "open"
    #: The design document this came out of. A task with no document is a
    #: chore; a task with one is a promise.
    doc: str = ""
    #: Dotted path to the symbol that exists when this is done -- a function,
    #: a class, a module-level constant -- or ``test:<name>`` for a test.
    #: Empty means nothing can be checked, which is allowed and reported.
    evidence: str = ""
    #: Free note: why it is deferred, what it is waiting on.
    note: str = ""
    tags: list[str] = field(default_factory=list)

    def check(self) -> tuple[str, str]:
        """``(status, detail)`` -- does the evidence for this task exist?"""
        if not self.evidence:
            return UNCHECKABLE, "no evidence recorded"
        exists, detail = _symbol_exists(self.evidence)
        if self.state == "done":
            return (OK, detail) if exists else (MISSING, detail)
        if self.state in ("open", "doing"):
            return (STALE, detail) if exists else (OK, detail)
        return OK, detail


def _symbol_exists(path: str) -> tuple[bool, str]:
    """Whether a dotted symbol path resolves. Never raises."""
    if path.startswith("test:"):
        name = path[5:]
        root = _root() / "tests"
        for f in sorted(root.glob("test_*.py")):
            if re.search(rf"^def {re.escape(name)}\b", f.read_text(encoding="utf-8"),
                         re.M):
                return True, f"{f.name}::{name}"
        return False, f"no test named {name!r} in tests/"

    module, _, attr = path.rpartition(".")
    if not module:
        return False, f"{path!r} is not a dotted path"
    try:
        mod = importlib.import_module(module)
    except Exception as exc:                      # noqa: BLE001 - report, never raise
        return False, f"cannot import {module}: {exc.__class__.__name__}"
    if hasattr(mod, attr):
        return True, path
    return False, f"{module} has no {attr!r}"


def _root() -> pathlib.Path:
    """The repo root, found from this file rather than the working directory."""
    return pathlib.Path(__file__).resolve().parent.parent


def load(path: str | pathlib.Path | None = None) -> list[Task]:
    p = pathlib.Path(path) if path else _root() / STORE
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [Task(**t) for t in raw.get("tasks", [])]


def save(tasks: list[Task], path: str | pathlib.Path | None = None) -> pathlib.Path:
    p = pathlib.Path(path) if path else _root() / STORE
    p.write_text(
        json.dumps({"tasks": [dataclasses.asdict(t) for t in tasks]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return p


def add(tasks: list[Task], what: str, *, id: str = "", **kw) -> Task:
    """Append a task.

    ``id`` is a short handle you type at the command line; without one it is
    slugged from the text, which is unambiguous and unpleasant to type.
    """
    base = id or (re.sub(r"[^a-z0-9]+", "-", what.lower()).strip("-")[:32] or "task")
    taken = {t.id for t in tasks}
    tid, n = base, 2
    while tid in taken:
        tid, n = f"{base}-{n}", n + 1
    task = Task(id=tid, what=what, **kw)
    tasks.append(task)
    return task


def summary(tasks: list[Task]) -> dict[str, int]:
    return {s: sum(1 for t in tasks if t.state == s) for s in STATES}


def report(tasks: list[Task], *, check: bool = False) -> str:
    """The tracker as text, grouped by state, worst news first."""
    if not tasks:
        return "No tasks recorded. `citysmith tasks --add` starts one."

    lines: list[str] = []
    counts = summary(tasks)
    lines.append("  ".join(f"{s}: {counts[s]}" for s in STATES if counts[s]))
    lines.append("")

    problems: list[str] = []
    for state in STATES:
        rows = [t for t in tasks if t.state == state]
        if not rows:
            continue
        lines.append(f"{state.upper()}")
        for t in rows:
            mark = ""
            if check:
                status, detail = t.check()
                mark = {OK: "", MISSING: "  [MISSING]", STALE: "  [STALE]",
                        UNCHECKABLE: "  [no evidence]"}[status]
                if status == MISSING:
                    problems.append(
                        f"  {t.id}: marked done, but {detail}")
                elif status == STALE:
                    problems.append(
                        f"  {t.id}: marked {t.state}, but {detail} already exists")
            lines.append(f"  {t.id:34} {t.what}{mark}")
            if t.doc:
                lines.append(f"  {'':34} {t.doc}")
            if t.note:
                lines.append(f"  {'':34} -- {t.note}")
        lines.append("")

    if check:
        lines.append("MISMATCHES" if problems else "Every claim checks out.")
        lines.extend(problems)
    return "\n".join(lines).rstrip() + "\n"
