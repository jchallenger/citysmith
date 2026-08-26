"""Which TaleSpire board holds which scene, and whether it is still current.

The party walks into the same tavern twice. The second time there must be no
second board: the first one is still there, with whatever happened on it, and
making another is both a waste of a paste and a way to lose the first.

**Nothing in TaleSpire can answer "does a board for this exist".** The campaign
board list is a list of names -- no size, no date, no contents, and no API of
any kind -- so a board is identified by the name we gave it and by nothing
else. This file is therefore the record, and it is the only record:
`campaign/boards.json`, one entry per scene, written after a paste lands and
read before the next one starts. **Not under `out/`**: that is gitignored build
output and gets cleared wholesale, and this file cannot be regenerated from
anything -- it was lost once that way already.

Four states, and each one has a different right answer:

``NEW``      nothing recorded. Make a board, paste, name it, record it.
``READY``    recorded, and the scene on disk is the build that was pasted.
             Switch to it. Do not paste anything.
``STALE``    recorded, but the scene has been rebuilt since -- new seed, new
             config, new geometry. The board holds the *old* build. Reuse is
             still the default: a board is somewhere the party has been, and
             re-pasting cannot replace what is already on it (there is no
             erase). Rebuilding is an explicit act and it makes a *second*
             board rather than touching the first.
``MOVED``    recorded, but the building's centroid has shifted, which means the
             town was re-imported and this id may now be a different building.
             Reported rather than resolved: nobody can tell from here whether
             the export changed or the crop did.

Deletion is not in this module on purpose. `talespire-boards` documents how to
delete a board by hand and why it is dangerous; nothing automated should do it,
because the list cannot tell you what is on a board before it goes.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
from dataclasses import asdict, dataclass, field

REGISTRY_VERSION = 1

NEW = "NEW"
READY = "READY"
STALE = "STALE"
MOVED = "MOVED"


@dataclass
class BoardRecord:
    """One board, and what was on it when we last pasted."""

    scene_id: str
    board: str
    town: str = ""
    building_id: str = ""
    centroid: tuple[float, float] = (0.0, 0.0)
    #: Digest of the slab files as pasted. A rebuild that changes any geometry
    #: changes this, which is the only way to notice that the board and the
    #: files have parted company.
    digest: str = ""
    created: str = ""
    last_entered: str = ""
    visits: int = 0
    #: Board names this scene used to live on. Never deleted, only left behind.
    superseded: list[str] = field(default_factory=list)


class Registry:
    """The scene -> board record, loaded from and saved to one JSON file."""

    def __init__(self, path: pathlib.Path, records: dict[str, BoardRecord],
                 campaign: str = ""):
        self.path = path
        self.records = records
        self.campaign = campaign

    # -- io -------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Registry":
        p = pathlib.Path(path)
        if not p.exists():
            return cls(p, {})
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("registry_version") != REGISTRY_VERSION:
            raise ValueError(
                f"{p}: registry_version {data.get('registry_version')}, "
                f"expected {REGISTRY_VERSION}"
            )
        records = {}
        for scene_id, raw in (data.get("boards") or {}).items():
            raw = dict(raw)
            raw["centroid"] = tuple(raw.get("centroid", (0.0, 0.0)))
            raw.setdefault("scene_id", scene_id)
            records[scene_id] = BoardRecord(**raw)
        return cls(p, records, data.get("campaign", ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "registry_version": REGISTRY_VERSION,
            "campaign": self.campaign,
            "boards": {
                sid: {**asdict(r), "centroid": list(r.centroid)}
                for sid, r in sorted(self.records.items())
            },
        }
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # -- reading --------------------------------------------------------------

    def get(self, scene_id: str) -> BoardRecord | None:
        return self.records.get(scene_id)

    def status(self, scene, digest: str) -> tuple[str, BoardRecord | None]:
        """What to do about this scene: see the four states in the module doc."""
        record = self.records.get(scene.scene_id)
        if record is None:
            return NEW, None
        if record.centroid and scene.centroid and \
                _apart(record.centroid, scene.centroid) > 1.0:
            return MOVED, record
        if record.digest and digest and record.digest != digest:
            return STALE, record
        return READY, record

    # -- writing --------------------------------------------------------------

    def record(self, scene, digest: str, board: str = "") -> BoardRecord:
        """Note that ``scene`` has been pasted onto a board.

        A board name that differs from the one already recorded does not
        replace it silently -- the old one is kept in ``superseded``, because
        it still exists in the campaign and somebody will wonder what it was.
        """
        now = _now()
        board = board or scene.board
        existing = self.records.get(scene.scene_id)
        if existing is not None:
            if existing.board != board:
                existing.superseded.append(existing.board)
                existing.board = board
            existing.digest = digest
            existing.centroid = tuple(scene.centroid)
            existing.last_entered = now
            existing.visits += 1
            self.save()
            return existing

        record = BoardRecord(
            scene_id=scene.scene_id, board=board, town=scene.town,
            building_id=scene.building_id, centroid=tuple(scene.centroid),
            digest=digest, created=now, last_entered=now, visits=1,
        )
        self.records[scene.scene_id] = record
        self.save()
        return record

    def rename(self, scene_id: str, board: str) -> BoardRecord | None:
        """Point an existing record at a new board name, and nothing else.

        For a board renamed in game -- a naming scheme changing under a
        campaign that already has boards in it. **Deliberately not `record`**:
        that one recomputes the digest from the files on disk, so using it to
        change a name would quietly relabel a board holding an older build as
        holding the current one. The whole point of the digest is to notice
        that, and a rename is not a paste.
        """
        rec = self.records.get(scene_id)
        if rec is None:
            return None
        if rec.board != board:
            rec.superseded.append(rec.board)
            rec.board = board
            self.save()
        return rec

    def visit(self, scene_id: str) -> BoardRecord | None:
        """Count a return trip. The board is untouched; only the record moves."""
        record = self.records.get(scene_id)
        if record is None:
            return None
        record.visits += 1
        record.last_entered = _now()
        self.save()
        return record

    def forget(self, scene_id: str) -> bool:
        """Drop an entry -- for a board that was deleted in game by hand.

        This does not delete anything in TaleSpire and cannot: the board list
        has no API. It only stops us claiming a board exists when it does not.
        """
        if scene_id not in self.records:
            return False
        del self.records[scene_id]
        self.save()
        return True


def digest_of(paths) -> str:
    """A digest over what is *on the board*, so a rebuild is detectable.

    Over the decoded placements, sorted -- not over the file bytes. The
    question this answers is "does the board hold this build", and two files
    that place the same assets in the same places are the same board however
    they are ordered inside. Hashing the bytes read STALE on a rebuild that had
    changed nothing: `_interior_walls` returns a set keyed partly on a string,
    Python randomises string hashing per process, and the partitions came out
    in a different order every run. That is fixed at the source too, but a
    digest that only holds while every set in the builder happens to iterate
    the same way is a digest that will lie again later.
    """
    from .slab import decode

    h = hashlib.sha256()
    for path in sorted(pathlib.Path(p) for p in paths):
        h.update(path.name.encode("utf-8"))
        rows = sorted(
            (p.asset_id, round(p.x, 3), round(p.y, 3), round(p.z, 3), p.rot)
            for p in decode(path.read_text(encoding="utf-8")).placements
        )
        for row in rows:
            h.update(repr(row).encode("utf-8"))
    return "sha256:" + h.hexdigest()[:16]


def digest_of_scene(directory: str | os.PathLike[str], scene) -> str:
    """The digest of the slabs a scene manifest names, in its directory."""
    d = pathlib.Path(directory)
    paths = [d / name for name in scene.slabs]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"{scene.scene_id}: the manifest names slabs that are not there: "
            + ", ".join(missing)
        )
    return digest_of(paths)


def _apart(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


# -- what nothing needs any more ----------------------------------------------
#
# TaleSpire will tell you *nothing* about a board: no size, no date, no
# contents, no API, and a campaign list that clips a row at sixteen capitals.
# So a board accumulates and there is no way to look at the list and say which
# of nine `Unknown Realm N` rows is last week's probe. That is what this is
# for: name the disposable ones from what the registry already knows, before
# anyone opens the campaign list.
#
# **Deleting is deliberately not automated.** Delete board sits behind the
# per-board triangle in the campaign list, immediately beside the play arrow,
# and the row order changes on every rename -- so a synthetic click that misses
# by one row deletes the wrong board, and there is no undo. This prints; a
# person clicks.

#: A board name the generator hands out when it has nothing better. Every one
#: of these is a paste nobody named, which is what a probe or a rebuild leaves.
UNNAMED_PREFIX = "Unknown Realm"


@dataclass
class Prunable:
    """A board nothing points at any more, and why."""

    board: str
    why: str
    scene_id: str = ""

    def describe(self) -> str:
        owner = f"  ({self.scene_id})" if self.scene_id else ""
        return f"{self.board} -- {self.why}{owner}"


def prunable(registry: "Registry", seen: list[str] | None = None) -> list[Prunable]:
    """Boards that are *provably* disposable. Deliberately conservative.

    Two sources, and only two, because there is no undo:

    * **Superseded names.** A rebuild does not erase a board -- there is no
      erase -- so `-Rebuild` makes a second board and leaves the first sitting
      there under the old name. Every entry in a record's ``superseded`` is a
      real board in the campaign that nothing points at.
    Unnamed boards are **not** here, and the reason is a counterexample from
    the campaign itself. This function used to list every ``Unknown Realm N``
    on the grounds that the name is what `newboard` hands out, so the board
    must be a probe nobody came back to. On 2026-08-26 the board in front of
    us was ``Unknown Realm 22``, and it held the newest build of a town its
    owner very much wanted -- so the rule offered up live work, in the one
    operation with no undo. **A default name is the absence of evidence, not
    evidence of absence**, and the skill file says as much a few lines later:
    a board with somebody's work on it looks exactly like an empty one from
    the list. They go to :func:`unnamed` now, which recommends looking.

    **A board with a name a person typed is never listed here**, even when no
    scene record claims it, and that is the whole reason this function is not
    simply "everything the registry does not own". The registry tracks *scene*
    boards only; the town boards -- ``East Tradebourne``, ``Graybank``,
    ``Pelvesthollow`` -- are named by hand and recorded nowhere, so treating
    "unclaimed" as "disposable" would have offered to delete the three finished
    towns. :func:`unclaimed` reports those separately, without a recommendation.

    ``seen`` has to be supplied by a person reading `ts.ps1 boards`, because
    nothing here can read text off the screen -- the same limit that stops
    `scene.ps1 enter` switching boards unattended.
    """
    out: list[Prunable] = []
    claimed = _claimed(registry)

    for record in sorted(registry.records.values(), key=lambda r: r.board):
        for old in record.superseded:
            # **A superseded name is only prunable if the board is still
            # there.** The registry remembers every name a scene has ever had,
            # and remembering is not the same as the board existing: delete it
            # and the entry stays, so `prune` goes on naming it for ever. That
            # is not merely noise. The campaign list is the only way to act on
            # this, rows move on every rename, and the skill's own warning is
            # that a click missing by one deletes the wrong board with no undo
            # -- so sending somebody hunting for a row that is not there is
            # pointing them at a neighbour. Measured on the real campaign
            # 2026-08-26: both superseded interior names had already gone, and
            # both were still being recommended.
            if seen and old not in set(seen):
                continue
            out.append(Prunable(
                old, "superseded by a rebuild; nothing points at it",
                record.scene_id))

    return out


def unnamed(registry: "Registry", seen: list[str] | None = None) -> list[str]:
    """Boards still carrying `newboard`'s default name, contents unknown.

    Almost always a probe or a rebuild nobody came back to -- and *almost* is
    the whole point, which is why these are not in :func:`prunable`. Nothing
    readable distinguishes a scratch board from the one holding this week's
    town: the campaign list gives a name and nothing else, and the name here
    is the one the game invented. Deciding means switching to each and looking.

    Useful as a work list, dangerous as a delete list. Two cheap habits empty
    it safely: name a board the moment a paste lands on it (`ts.ps1 rename`),
    and give throwaways a ``Probe - `` prefix so they read as disposable
    without anyone having to remember.
    """
    claimed = _claimed(registry)
    return sorted({
        n for n in (seen or [])
        if n not in claimed and n.startswith(UNNAMED_PREFIX)
    })


def unclaimed(registry: "Registry", seen: list[str] | None = None) -> list[str]:
    """Named boards the registry knows nothing about.

    Reported, never recommended. A name somebody typed is evidence that the
    board mattered to them once, and the registry only ever tracked scenes --
    so this list is where the town boards live, and it is also where a board
    the registry has *lost* would show up.
    """
    claimed = _claimed(registry)
    return sorted({
        n for n in (seen or [])
        if n not in claimed and not n.startswith(UNNAMED_PREFIX)
    })


def _claimed(registry: "Registry") -> set[str]:
    out = set()
    for record in registry.records.values():
        out.add(record.board)
        out.update(record.superseded)
    return out


def keepers(registry: "Registry") -> list[BoardRecord]:
    """The boards worth keeping: one live board per scene.

    The complement of :func:`prunable` over what the registry knows. A board is
    where something happened -- a session, a party, notes on it -- so the
    default is always to keep, and this is the list to check a deletion against
    before making it.
    """
    return sorted(registry.records.values(), key=lambda r: r.board)
