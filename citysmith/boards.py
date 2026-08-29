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

#: Bumped to 2 when the index arrived beside the scene records. A version 1
#: file loads with an empty index rather than being refused: it is a true
#: record of the scenes, and the thing it is missing is the thing that could
#: not be recovered from anywhere else either.
REGISTRY_VERSION = 2

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


#: What a board can hold. `other` is not a failure -- it is the honest answer
#: for a board somebody made by hand, and it still buys a name, a date and a
#: note, which is more than the campaign list gives.
HOLDS = ("town", "scene", "probe", "other")

#: Which of those are disposable unless told otherwise. A probe board is made
#: to be looked at once; a town or a scene is where something happened.
DISPOSABLE_BY_DEFAULT = ("probe",)


@dataclass
class BoardEntry:
    """One board in the campaign, and what was put on it.

    **TaleSpire tells you a name and nothing else.** No asset count, no size,
    no date, no contents -- the campaign list is a column of strings, and two
    boards holding a finished town and last week's throwaway look identical in
    it. That is why deleting is dangerous and why `prunable` can only ever
    recommend the boards a *scene* superseded: everything else was unrecorded,
    so the honest answer was "look at it yourself".

    So the index is written **at paste time**, because that is the only moment
    anything knows what is going onto the board. Nothing can be recovered
    afterwards. Same argument `BoardRecord` already makes for scenes, pointed
    at every board rather than at the ones a scene owns.

    What it deliberately does NOT do is claim to know the board's *current*
    state. An entry is a record of a paste, and a person can paste over it,
    rename it or delete it with no way for anything here to notice.
    `reconcile` against a campaign listing is what turns that from a silent
    lie into a reported difference.
    """

    board: str
    holds: str = "other"
    #: What it was built from: a layout path, a scene id, a slab, a sentence.
    source: str = ""
    #: The folder it was filed under, as of `recorded`. "" is loose.
    folder: str = ""
    #: The build stem, where there was one -- what the slab files are called.
    stem: str = ""
    #: How many slabs went on, and how many assets they held. Zero means "not
    #: recorded", which is different from "empty" and is why neither is None.
    chunks: int = 0
    assets: int = 0
    #: Digest of the files pasted, where the caller had them. Lets a rebuild
    #: notice that the board and the files have parted company, the same way a
    #: scene does.
    digest: str = ""
    recorded: str = ""
    #: Safe to delete without looking. Set from `holds` unless overridden --
    #: and it is a claim by whoever recorded it, not a deduction.
    disposable: bool = False
    note: str = ""

    @property
    def summary(self) -> str:
        bits = [self.holds]
        if self.source:
            bits.append(self.source)
        if self.chunks:
            bits.append(f"{self.chunks} slab(s)")
        if self.assets:
            bits.append(f"{self.assets:,} assets")
        return ", ".join(bits)


class Registry:
    """The scene -> board record, loaded from and saved to one JSON file."""

    def __init__(self, path: pathlib.Path, records: dict[str, BoardRecord],
                 campaign: str = "", index: dict[str, BoardEntry] | None = None):
        self.path = path
        self.records = records
        self.campaign = campaign
        #: Board name -> what it holds. Keyed on the NAME, because that is the
        #: only handle the campaign list gives and the only thing a person can
        #: match a row against.
        self.index: dict[str, BoardEntry] = index or {}

    # -- io -------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Registry":
        p = pathlib.Path(path)
        if not p.exists():
            return cls(p, {})
        data = json.loads(p.read_text(encoding="utf-8"))
        version = data.get("registry_version")
        # A version 1 file predates the index and is otherwise correct, so it
        # is read rather than refused -- refusing it would throw away the scene
        # records, which cannot be regenerated from anything.
        if version not in (1, REGISTRY_VERSION):
            raise ValueError(
                f"{p}: registry_version {version}, expected {REGISTRY_VERSION}"
            )
        records = {}
        for scene_id, raw in (data.get("boards") or {}).items():
            raw = dict(raw)
            raw["centroid"] = tuple(raw.get("centroid", (0.0, 0.0)))
            raw.setdefault("scene_id", scene_id)
            records[scene_id] = BoardRecord(**raw)
        index = {}
        for name, raw in (data.get("index") or {}).items():
            raw = dict(raw)
            raw.setdefault("board", name)
            index[name] = BoardEntry(**raw)
        return cls(p, records, data.get("campaign", ""), index)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "registry_version": REGISTRY_VERSION,
            "campaign": self.campaign,
            "boards": {
                sid: {**asdict(r), "centroid": list(r.centroid)}
                for sid, r in sorted(self.records.items())
            },
            "index": {name: asdict(e) for name, e in sorted(self.index.items())},
        }
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # -- reading --------------------------------------------------------------

    def get(self, scene_id: str) -> BoardRecord | None:
        return self.records.get(scene_id)

    # -- the index ------------------------------------------------------------

    def note(self, board: str, *, holds: str = "other", source: str = "",
             folder: str = "", stem: str = "", chunks: int = 0, assets: int = 0,
             digest: str = "", note: str = "",
             disposable: bool | None = None) -> BoardEntry:
        """Record what a board holds, replacing any earlier entry for it.

        Upserts rather than appends, because a board is one thing at a time:
        pasting a second town onto a board does not make it two boards, and an
        index that grew a row per paste would be a log pretending to be an
        index.
        """
        if holds not in HOLDS:
            raise ValueError(f"holds must be one of {HOLDS}, got {holds!r}")
        was = self.index.get(board)
        entry = BoardEntry(
            board=board, holds=holds, source=source, folder=folder, stem=stem,
            chunks=chunks, assets=assets, digest=digest,
            recorded=_now(),
            disposable=(holds in DISPOSABLE_BY_DEFAULT
                        if disposable is None else disposable),
            note=note or (was.note if was else ""),
        )
        self.index[board] = entry
        return entry

    def drop(self, board: str) -> bool:
        """Forget a board. Does not touch the board itself -- nothing here can."""
        return self.index.pop(board, None) is not None

    def rename_board(self, old: str, new: str) -> BoardEntry | None:
        """Follow a rename.

        The index is keyed on the name and TaleSpire has no board id, so a
        rename is the one edit that silently orphans an entry. Every other
        difference between the index and the list is reported by `reconcile`;
        this one has to be told.
        """
        entry = self.index.pop(old, None)
        if entry is None:
            return None
        for record in self.records.values():
            if record.board == old:
                record.board = new
                if old not in record.superseded:
                    record.superseded.append(old)
        entry.board = new
        self.index[new] = entry
        return entry

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

#: The folder a board has to be in before anyone else sees it. A campaign has
#: folders (verified 2026-08-26: `Set Folder` on a board's row menu), they sort
#: above the loose boards and collapse to one line, and a board is in at most
#: one -- so they are the cheapest way to keep work-in-progress apart from what
#: is finished. There is no "move to campaign" anywhere in TaleSpire, so this
#: is the separation that exists.
PUBLISHED_FOLDER = "Ready to publish"


@dataclass
class SeenBoard:
    """One row of the campaign list, with the folder it was filed under."""

    name: str
    folder: str = ""

    @property
    def is_unnamed(self) -> bool:
        return self.name.startswith(UNNAMED_PREFIX)


def parse_seen(lines) -> list[SeenBoard]:
    """Read a campaign listing, laid out the way the panel lays it out.

    A folder header sits at the left margin and its boards are indented under
    it, which is exactly what you are copying off the screen::

        Ready to publish:
          East Tradebourne
          GRB/T14 The Halfling and the Fox Interior
        Workshop:
          Unknown Realm 3
        Unknown Realm 9

    An unindented line is a folder header if anything is indented under it, and
    a loose board otherwise; the trailing colon is optional.

    **The separator is indentation and not a character in the line, because
    every character worth using is already in a board name.** `Folder/Name` was
    the obvious format and it is wrong here: this project's own scheme is
    `GRB/T14 The Halfling and the Fox Interior`, so a loose board would parse
    as folder `GRB` and the published set would read as empty. That failure is
    silent, which is the kind this file keeps a section about.
    """
    out: list[SeenBoard] = []
    pending: SeenBoard | None = None
    folder = ""
    for raw in lines:
        if not raw.strip():
            continue
        indented = raw[:1].isspace()
        name = raw.strip().rstrip(":").strip() if not indented else raw.strip()
        if indented:
            # Whatever was above us is a header, not a board.
            if pending is not None:
                folder = pending.name
                pending = None
            out.append(SeenBoard(raw.strip(), folder))
        else:
            if pending is not None:
                out.append(pending)          # nothing under it: a loose board
            folder = ""
            pending = SeenBoard(name, "")
    if pending is not None:
        out.append(pending)
    return out


def unfit_to_publish(seen: list[SeenBoard],
                     folder: str = PUBLISHED_FOLDER) -> list[str]:
    """Boards in the published folder that nobody ever named.

    **This is a hard error, where the same board loose in the campaign is only
    a "look first".** The difference is that filing something under `Ready to
    publish` is a claim about it, and `Unknown Realm 14` is the name the game
    invents when nobody made one -- so the claim and the name contradict each
    other. Somebody either filed the wrong row (the rows move on every rename)
    or meant to name it and did not.

    Deliberately narrow. It does not judge whether the *content* is fit to
    publish, because nothing here can see a board's content -- that stays a
    person's call, and pretending otherwise is how a check starts lying.
    """
    return sorted(b.name for b in seen if b.folder == folder and b.is_unnamed)


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


@dataclass
class Reconciliation:
    """The index held against a campaign listing, three ways.

    Every one of the three is a real state and none of them is an error on its
    own -- which is the point. The index is a record of pastes and the listing
    is a transcription of a screen; they part company for ordinary reasons, and
    saying *how* is more use than a pass/fail.
    """

    #: Indexed, and still in the list. The boring case, and most of them.
    matched: list[BoardEntry]
    #: Indexed, and not in the list any more: deleted or renamed by hand.
    missing: list[BoardEntry]
    #: In the list, and nothing recorded about it. This is the bucket that
    #: makes pruning dangerous, so it is the one worth shrinking.
    unrecorded: list[SeenBoard]

    @property
    def coverage(self) -> float:
        seen = len(self.matched) + len(self.unrecorded)
        return len(self.matched) / seen if seen else 0.0


def reconcile(registry: "Registry", seen: list[SeenBoard]) -> Reconciliation:
    """Hold the index against what the campaign list actually shows.

    **The index cannot notice anything on its own.** A person can paste over a
    board, rename it or delete it and nothing here is told; the only handle is
    the name, and the only place the names live is a panel no API can read. So
    the listing -- transcribed off a `ts.ps1 boards` screenshot -- is the
    ground truth, and this says where the record disagrees with it rather than
    trusting either one.
    """
    names = {b.name for b in seen}
    matched = [e for name, e in sorted(registry.index.items()) if name in names]
    missing = [e for name, e in sorted(registry.index.items())
               if name not in names]
    unrecorded = [b for b in seen if b.name not in registry.index]
    return Reconciliation(matched, missing, unrecorded)


def disposable(registry: "Registry", seen: list[str] | None = None) -> list[BoardEntry]:
    """Boards the index itself records as safe to delete.

    **The fourth bucket, and the only one that is a recommendation.** The other
    three exist because nothing was written down: `unclaimed` is "somebody
    named it, so ask them", `unnamed` is "switch to it and look", and
    `prunable` can only see the boards a scene *superseded*. All three are the
    absence of a record dressed up as advice.

    This one is a record. Somebody pasted a probe, said so at the time, and the
    entry has carried that ever since -- so it can be acted on without opening
    the board. A board claimed by a live scene is never listed, whatever its
    entry says, because the scene registry is the older claim.
    """
    claimed = _claimed(registry)
    out = [e for e in registry.index.values()
           if e.disposable and e.board not in claimed]
    if seen is not None:
        out = [e for e in out if e.board in set(seen)]
    return sorted(out, key=lambda e: e.board)


def keepers(registry: "Registry") -> list[BoardRecord]:
    """The boards worth keeping: one live board per scene.

    The complement of :func:`prunable` over what the registry knows. A board is
    where something happened -- a session, a party, notes on it -- so the
    default is always to keep, and this is the list to check a deletion against
    before making it.
    """
    return sorted(registry.records.values(), key=lambda r: r.board)
