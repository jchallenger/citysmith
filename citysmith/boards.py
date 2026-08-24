"""Which TaleSpire board holds which scene, and whether it is still current.

The party walks into the same tavern twice. The second time there must be no
second board: the first one is still there, with whatever happened on it, and
making another is both a waste of a paste and a way to lose the first.

**Nothing in TaleSpire can answer "does a board for this exist".** The campaign
board list is a list of names -- no size, no date, no contents, and no API of
any kind -- so a board is identified by the name we gave it and by nothing
else. This file is therefore the record, and it is the only record:
`out/scenes/boards.json`, one entry per scene, written after a paste lands and
read before the next one starts.

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
