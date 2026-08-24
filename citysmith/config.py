"""The one config both halves of a scene read.

`citysmith scene` builds the slabs from it and `tools/scene.ps1` drives
TaleSpire from it, so it is JSON rather than TOML or Python: PowerShell reads
JSON with one cmdlet and needs no parser of its own.

Two rules, both learned elsewhere in this project:

- **Defaults live in code, the file is an overlay.** Every key here has a
  working default, so a fresh checkout with no `config/scene.json` still
  builds. Deleting a key from the file restores the default rather than
  breaking the load.

- **An unrecognised key is reported, never ignored.** A typo in a config is
  exactly the failure mode `Layout.unmapped` exists for: the run succeeds, the
  setting does nothing, and the difference shows up on the board an hour later.
  :attr:`Config.unknown` collects them and the CLI prints them as warnings.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
from typing import Any

CONFIG_VERSION = 1

#: Where the CLI looks when no `--config` is given.
DEFAULT_PATH = pathlib.Path("config/scene.json")

#: Every setting, with the value used when the file does not say otherwise.
DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "style": "medieval",
    "seed": 33,
    "out_dir": "out/scenes",
    # **Not under out/.** That directory is gitignored build output and gets
    # cleared wholesale; this file is the only record of which board holds
    # which scene, and it cannot be regenerated from anything. It was lost
    # once already, to a worktree being removed.
    "registry": "campaign/boards.json",
    "board": {
        # `GRB/T14 The Halfling and the Fox Interior`.
        #
        # **The town and the code come first because they are the part that
        # survives.** The campaign board list clips a row at SIXTEEN capital
        # letters -- renamed to `ABCDEFGHIJKLMNOPQRSTUVWXYZabc...`, a board
        # renders as `ABCDEFGHIJKLMNOP...` -- and it is the only thing
        # TaleSpire will tell you about a board. `GRB/T14 The Half...` fits the
        # town, a unique code and the start of the name inside that; anything
        # identifying at the end is invisible.
        #
        # Placeholders: {town_code} {code} {building} {town} {prefix}.
        "prefix": "",
        "name_template": "{town_code}/{code} {building} Interior",
        "max_name": 60,
        # Town -> tag, where the derivation reads badly or two towns collide.
        # Derived otherwise: initials for a multi-word name, first letter plus
        # the next two consonants for a single word.
        "town_codes": {},
    },
    "interior": {
        # Three, because the layout says three for 30% of buildings (352 of
        # 1,176 across the towns) and a cap of two silently threw a storey
        # away. Utility buildings are one storey regardless -- see
        # `interior.storeys_for`.
        "max_levels": 3,
        "min_room": 3,
        "spread_levels": True,
        "level_gap": 2,
        "prop_density": 0.12,
        "roof": False,
        "pad": 3,
        "max_assets": 4000,
    },
    "party": {
        "size": 4,
        "names": [],
        "arrival": "inside",
        "mark_role": "party_mark",
    },
    "occupants": {
        "roster": "",
        "hour": "day",
    },
    "paste": {
        "pitch_down": 250,
        "hold_seconds": 3,
        "commit_seconds": 4,
        "settle_seconds": 2,
        "shot_every": 1,
    },
}


class Config:
    """Settings, read by dotted path: ``cfg.get("party.size")``."""

    def __init__(self, data: dict[str, Any], unknown: list[str] | None = None,
                 path: pathlib.Path | None = None):
        self.data = data
        self.unknown = unknown or []
        self.path = path

    # -- reading --------------------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    # -- loading --------------------------------------------------------------

    @classmethod
    def defaults(cls) -> "Config":
        """Every setting at its built-in value, reading no file."""
        return cls(copy.deepcopy(DEFAULTS))

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        """Defaults, with ``path`` merged over them. A missing file is fine."""
        p = pathlib.Path(path) if path else DEFAULT_PATH
        data = copy.deepcopy(DEFAULTS)
        if not p.exists():
            # Only an explicit --config that does not exist is an error. The
            # default path being absent is the ordinary case.
            if path:
                raise FileNotFoundError(f"no config at {p}")
            return cls(data, [], None)

        raw = json.loads(p.read_text(encoding="utf-8"))
        version = raw.get("config_version", CONFIG_VERSION)
        if version != CONFIG_VERSION:
            raise ValueError(
                f"{p}: config_version {version}, expected {CONFIG_VERSION}"
            )
        unknown: list[str] = []
        _merge(data, raw, DEFAULTS, "", unknown)
        return cls(data, unknown, p)


def _merge(into: dict, over: dict, known: dict, prefix: str,
           unknown: list[str]) -> None:
    for key, value in over.items():
        # `_comment` is how a JSON file carries its own documentation. Anything
        # starting with an underscore is a note to the reader, not a setting.
        if key.startswith("_"):
            continue
        path = f"{prefix}{key}"
        if key not in known:
            unknown.append(path)
            into[key] = value
            continue
        if isinstance(value, dict) and isinstance(known[key], dict):
            _merge(into[key], value, known[key], f"{path}.", unknown)
        else:
            into[key] = value
