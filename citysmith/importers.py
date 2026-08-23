"""Pick the right importer for a layout source file.

Two generators export GeoJSON that citysmith can build from, and **the file
extension does not tell them apart** -- all four combinations exist in the wild:

===================================  ===========  ======
file                                 extension    format
===================================  ===========  ======
``samples/forest_church.json``       ``.json``    MFCG
``candlewell_church.json``           ``.json``    MFCG
``East Tradebourne.geojson``         ``.geojson`` FTG
``Pelvesthollow.geojson``            ``.geojson`` FTG
===================================  ===========  ======

So the format is decided by looking inside. Both are ``FeatureCollection``s and
the discriminator is where a feature keeps its identity:

- **MFCG** puts a string ``id`` on the feature itself, from a closed vocabulary
  (``values``, ``earth``, ``buildings``, ...), and has no ``properties``.
- **FTG** has no feature ``id``; it carries ``properties.type`` in
  ``{BUILDING, EDGE, BACKGROUND, WATER}`` and an integer ``properties.id``.

Neither can be mistaken for the other, so a sniff is exact rather than a guess.
`docs/ftg-geojson-import.md` has the full reasoning and the FTG schema.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

MFCG = "mfcg"
FTG = "ftg"

#: Feature ids MFCG is known to emit. Only a couple need to be present -- the
#: set varies between exports (``water`` and ``trees`` come and go).
_MFCG_IDS = frozenset({
    "values", "earth", "walls", "roads", "districts", "buildings", "prisms",
    "squares", "fields", "planks", "rivers", "water", "greens", "trees",
})

#: ``properties.type`` values FTG is known to emit.
_FTG_TYPES = frozenset({"BUILDING", "EDGE", "BACKGROUND", "WATER"})


class SourceError(ValueError):
    """Raised when a file is not a usable layout source."""


def read_collection(path: str | os.PathLike[str]) -> dict:
    """Read a GeoJSON ``FeatureCollection``, or say why it is not one."""
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"Could not read {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise SourceError(f"{p} is not a GeoJSON FeatureCollection.")
    if not isinstance(data.get("features"), list) or not data["features"]:
        raise SourceError(f"{p} is a FeatureCollection with no features.")
    return data


def classify(data: dict) -> str | None:
    """Return ``MFCG``, ``FTG``, or ``None`` if the collection is neither."""
    features = [f for f in data.get("features", []) if isinstance(f, dict)]
    mfcg = sum(1 for f in features if f.get("id") in _MFCG_IDS)
    ftg = sum(
        1 for f in features
        if isinstance(f.get("properties"), dict)
        and f["properties"].get("type") in _FTG_TYPES
    )
    if mfcg and mfcg >= ftg:
        return MFCG
    if ftg:
        return FTG
    return None


def detect_format(path: str | os.PathLike[str]) -> str:
    """Sniff a file's format, or raise with what was actually found."""
    data = read_collection(path)
    fmt = classify(data)
    if fmt is None:
        seen = _describe(data)
        raise SourceError(
            f"{pathlib.Path(path)} is a FeatureCollection, but neither an MFCG "
            f"nor a Fantasy Town Generator export ({seen}). MFCG: export with "
            "Settlement > Export as > JSON. FTG: export as GeoJSON."
        )
    return fmt


def _describe(data: dict) -> str:
    """What the features look like, for an error a user can act on."""
    ids, types = set(), set()
    for f in data.get("features", [])[:50]:
        if not isinstance(f, dict):
            continue
        if isinstance(f.get("id"), str):
            ids.add(f["id"])
        props = f.get("properties")
        if isinstance(props, dict) and isinstance(props.get("type"), str):
            types.add(props["type"])
    if ids:
        return "feature ids: " + ", ".join(sorted(ids)[:8])
    if types:
        return "properties.type values: " + ", ".join(sorted(types)[:8])
    return "features carry neither an id nor a properties.type"


def import_layout(
    path: str | os.PathLike[str],
    *,
    fmt: str | None = None,
    **options: Any,
):
    """Import a layout from either supported format.

    ``fmt`` forces a reader; by default the file is sniffed. Options are passed
    through, and each reader ignores the ones that do not apply to it -- the two
    formats do not carry the same knobs (FTG has no wall thickness or road
    width; MFCG has no authored building types).
    """
    chosen = fmt or detect_format(path)
    if chosen == MFCG:
        from . import mfcg
        return mfcg.import_layout(path, **_filter(options, _MFCG_OPTIONS))
    if chosen == FTG:
        from . import ftg
        return ftg.import_layout(path, **_filter(options, _FTG_OPTIONS))
    raise SourceError(f"Unknown format {chosen!r}; expected {MFCG!r} or {FTG!r}.")


_MFCG_OPTIONS = frozenset({
    "house_frontage_ft", "feet_per_unit", "margin_feet", "clip", "name", "seed",
})
_FTG_OPTIONS = frozenset({
    "house_frontage_ft", "feet_per_unit", "margin_feet", "clip", "name", "seed",
    "cluster_gap_ft", "core_only", "fences",
})


def _filter(options: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k in allowed and v is not None}
