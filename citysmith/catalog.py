"""Asset catalog built from TaleSpire's own TaleWeaver index files.

TaleSpire ships a readable ``index.json`` per asset pack under
``<install>/Taleweaver/<pack-uuid>/index.json``. That is the authoritative
source for asset ids, names, tags and collider bounds, so the catalog is
derived from the user's actual install rather than a hardcoded list -- if they
own extra packs, those assets become available automatically.

Footprints come from ``ColliderBoundsBound`` rather than from parsing the "1x1"
style tags, because the tags are inconsistent and a wrong footprint is what
produces floating or overlapping geometry.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

CATALOG_VERSION = 1

#: Common Steam library roots, checked when no explicit path is given.
_STEAM_HINTS = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Steam",
    r"D:\Steam",
    r"D:\Games\SteamLibrary",
    r"D:\SteamLibrary",
    r"E:\Games\Steam",
    r"E:\SteamLibrary",
]

_LIBRARY_VDF_PATH = re.compile(r'"path"\s+"([^"]+)"')


class CatalogError(RuntimeError):
    """Raised when the TaleSpire asset index cannot be located or parsed."""


@dataclass(frozen=True)
class Asset:
    """One placeable TaleSpire asset."""

    id: str
    name: str
    kind: str  # "tile" | "prop" | "creature"
    pack: str
    group_tag: str
    tags: tuple[str, ...]
    folder: str
    # Footprint in tile units, derived from collider extents.
    size_x: float = 1.0
    size_y: float = 1.0
    size_z: float = 1.0
    deprecated: bool = False

    @property
    def footprint(self) -> tuple[float, float]:
        """Ground footprint (x, z) in tile units."""
        return (self.size_x, self.size_z)

    @property
    def height(self) -> float:
        return self.size_y

    def matches(self, *terms: str) -> bool:
        """True when every term appears in the asset's searchable text."""
        return all(t.lower() in self.search_text for t in terms)

    @property
    def search_text(self) -> str:
        return " ".join(
            [self.name, self.group_tag, self.folder, " ".join(self.tags)]
        ).lower()

    @property
    def tag_set(self) -> frozenset[str]:
        """Lowercased exact tags -- the reliable way to filter by material/theme."""
        return frozenset(t.lower() for t in self.tags)

    @property
    def group(self) -> str:
        """Lowercased GroupTag. This is the reliable role indicator."""
        return self.group_tag.lower()


def _steam_library_roots() -> list[pathlib.Path]:
    """Resolve Steam library folders, including ones on other drives."""
    roots: list[pathlib.Path] = []
    for hint in _STEAM_HINTS:
        p = pathlib.Path(hint)
        if p.exists():
            roots.append(p)
        vdf = p / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _LIBRARY_VDF_PATH.findall(text):
                candidate = pathlib.Path(match.replace("\\\\", "\\"))
                if candidate.exists():
                    roots.append(candidate)
    # De-duplicate, preserving order.
    seen: set[str] = set()
    unique: list[pathlib.Path] = []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def find_talespire_install(explicit: str | os.PathLike[str] | None = None) -> pathlib.Path:
    """Locate the TaleSpire install directory.

    Resolution order: explicit argument, ``TALESPIRE_PATH`` env var, then a
    scan of known Steam library roots.
    """
    candidates: list[pathlib.Path] = []
    if explicit:
        candidates.append(pathlib.Path(explicit))
    env = os.environ.get("TALESPIRE_PATH")
    if env:
        candidates.append(pathlib.Path(env))
    for root in _steam_library_roots():
        candidates.append(root / "steamapps" / "common" / "TaleSpire")

    for c in candidates:
        if (c / "Taleweaver").is_dir():
            return c
        # Tolerate being handed the Taleweaver dir itself.
        if c.name.lower() == "taleweaver" and c.is_dir():
            return c.parent

    raise CatalogError(
        "Could not find your TaleSpire install. Set TALESPIRE_PATH or pass "
        "--talespire-path pointing at the folder that contains 'Taleweaver'."
    )


def _extent(bounds: dict | None, axis: str) -> float:
    if not bounds:
        return 1.0
    extent = bounds.get("m_Extent") or {}
    value = extent.get(axis)
    if not value or value <= 0:
        return 1.0
    # Extents are half-sizes; round to hundredths to kill float noise.
    return round(value * 2, 2)


def _asset_from_entry(entry: dict, kind: str, pack: str) -> Asset:
    bounds = entry.get("ColliderBoundsBound")
    tags = tuple(str(t) for t in entry.get("Tags") or ())
    return Asset(
        id=str(entry["Id"]).lower(),
        name=str(entry.get("Name") or "").strip(),
        kind=kind,
        pack=pack,
        group_tag=str(entry.get("GroupTag") or "").strip(),
        tags=tags,
        folder=str(entry.get("Folder") or "").strip(),
        size_x=_extent(bounds, "x"),
        size_y=_extent(bounds, "y"),
        size_z=_extent(bounds, "z"),
        deprecated=bool(entry.get("IsDeprecated")),
    )


@dataclass
class Catalog:
    """Queryable collection of TaleSpire assets."""

    assets: list[Asset] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)

    # -- loading --------------------------------------------------------------

    @classmethod
    def from_install(
        cls, install: str | os.PathLike[str] | None = None, *, include_deprecated: bool = False
    ) -> "Catalog":
        root = find_talespire_install(install)
        indexes = sorted((root / "Taleweaver").glob("*/index.json"))
        if not indexes:
            raise CatalogError(f"No asset packs found under {root / 'Taleweaver'}")

        assets: list[Asset] = []
        packs: list[str] = []
        for index_path in indexes:
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CatalogError(f"Could not read {index_path}: {exc}") from exc
            pack = str(data.get("Name") or index_path.parent.name)
            packs.append(pack)
            for key, kind in (("Tiles", "tile"), ("Props", "prop"), ("Creatures", "creature")):
                for entry in data.get(key) or ():
                    if not entry.get("Id"):
                        continue
                    asset = _asset_from_entry(entry, kind, pack)
                    if asset.deprecated and not include_deprecated:
                        continue
                    assets.append(asset)
        return cls(assets=assets, packs=packs)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Catalog":
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if data.get("catalog_version") != CATALOG_VERSION:
            raise CatalogError(
                f"{path} was built by a different catalog version; rebuild it "
                "with `citysmith catalog build`."
            )
        assets = [Asset(**{**a, "tags": tuple(a.get("tags", ()))}) for a in data["assets"]]
        return cls(assets=assets, packs=list(data.get("packs", [])))

    def save(self, path: str | os.PathLike[str]) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "catalog_version": CATALOG_VERSION,
            "packs": self.packs,
            "assets": [{**asdict(a), "tags": list(a.tags)} for a in self.assets],
        }
        p.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # -- querying -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.assets)

    def by_id(self, asset_id: str) -> Asset | None:
        key = asset_id.lower()
        for a in self.assets:
            if a.id == key:
                return a
        return None

    def find(
        self,
        *terms: str,
        name: str | Sequence[str] | None = None,
        kind: str | None = None,
        pack: str | None = None,
        group: str | Sequence[str] | None = None,
        tags: Sequence[str] = (),
        any_tags: Sequence[str] | None = None,
        exclude_tags: Sequence[str] = (),
        any_of: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
        size: tuple[float, float] | None = None,
        height: float | None = None,
        max_height: float | None = None,
    ) -> list[Asset]:
        """Return assets matching every filter, sorted for determinism.

        ``name`` matches the asset name **exactly** (case-insensitively), and
        accepts several names to choose between. It is the strongest filter and
        the right one for anything whose look matters: tags describe what an
        asset is made of, not what it reads as, so "stone floor" is satisfied
        equally by a cobbled street and a desert flagstone. Pin those by name.

        Otherwise prefer the structured filters -- ``group`` (exact GroupTag)
        and ``tags`` (exact tag membership) -- over the free-text ``terms``.
        Asset *names* are inconsistent and matching them loosely produces
        nonsense like "Tavern no floor" satisfying a request for a floor.

        ``terms`` still matches anywhere in the searchable text, for the cases
        where no structured field captures what you want.
        """
        want_names = None
        if name is not None:
            want_names = {name.lower()} if isinstance(name, str) else {n.lower() for n in name}
        groups = None
        if group is not None:
            groups = {group.lower()} if isinstance(group, str) else {g.lower() for g in group}
        want_tags = {t.lower() for t in tags}
        any_tag_set = {t.lower() for t in any_tags} if any_tags else None
        block_tags = {t.lower() for t in exclude_tags}

        out: list[Asset] = []
        for a in self.assets:
            if want_names is not None and a.name.lower() not in want_names:
                continue
            if kind and a.kind != kind:
                continue
            if pack and a.pack != pack:
                continue
            if groups is not None and a.group not in groups:
                continue
            atags = a.tag_set
            if want_tags and not want_tags <= atags:
                continue
            if any_tag_set is not None and not (any_tag_set & atags):
                continue
            if block_tags & atags:
                continue
            text = a.search_text
            if not all(t.lower() in text for t in terms):
                continue
            if any_of and not any(t.lower() in text for t in any_of):
                continue
            if any(t.lower() in text for t in exclude):
                continue
            if size and (a.size_x, a.size_z) != size:
                continue
            if height is not None and a.size_y != height:
                continue
            if max_height is not None and a.size_y > max_height:
                continue
            out.append(a)
        out.sort(key=lambda a: (a.name, a.id))
        return out

    def pick(
        self,
        *terms: str,
        rng: random.Random | None = None,
        **kwargs,
    ) -> Asset | None:
        """Pick one matching asset, deterministically when given a seeded rng."""
        matches = self.find(*terms, **kwargs)
        if not matches:
            return None
        if rng is None:
            return matches[0]
        return rng.choice(matches)

    def require(self, *terms: str, **kwargs) -> Asset:
        """Like :meth:`pick` but raises with a useful message when nothing matches."""
        asset = self.pick(*terms, **kwargs)
        if asset is None:
            raise CatalogError(
                f"No asset matched {terms!r} with {kwargs!r}. "
                "Run `citysmith catalog search` to see what is available."
            )
        return asset

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.assets:
            out[a.kind] = out.get(a.kind, 0) + 1
        return out


def default_catalog_path() -> pathlib.Path:
    return pathlib.Path.cwd() / "catalog.json"


def load_or_build(
    path: str | os.PathLike[str] | None = None,
    install: str | os.PathLike[str] | None = None,
) -> Catalog:
    """Load a cached catalog, building it from the install if absent."""
    p = pathlib.Path(path) if path else default_catalog_path()
    if p.exists():
        try:
            return Catalog.load(p)
        except CatalogError:
            pass  # stale version -- rebuild below
    catalog = Catalog.from_install(install)
    catalog.save(p)
    return catalog
