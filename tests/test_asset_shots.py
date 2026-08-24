"""The archive slug rule, pinned. Offline -- nothing here touches the network.

`tools/asset_shots.py` turns a catalog name into a Tales Tavern URL by a rule
measured against that site's sitemap. The rule has one counter-intuitive
clause -- **underscores survive, everything else non-alphanumeric becomes a
hyphen** -- and it is exactly the clause someone tidies away. Measured cost of
tidying it: catalog coverage falls from 95.9% to 77.2%, and *every* MegaDungeon
piece stops resolving, including the two this project has been bitten by.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from asset_shots import slug_for, url_for  # noqa: E402


def test_spaces_become_hyphens():
    assert slug_for("castle merlon 1x1") == "castle-merlon-1x1"
    assert slug_for("Castle Ruins Wallbase 02") == "castle-ruins-wallbase-02"


def test_underscores_survive():
    """The clause the coverage depends on. Confirmed live against the archive."""
    assert slug_for("md_wall_1x1_diag_01") == "md_wall_1x1_diag_01"
    assert slug_for("md_tower_wall_01") == "md_tower_wall_01"


def test_punctuation_collapses_to_one_hyphen():
    assert slug_for("Door - Stone Double") == "door-stone-double"
    assert slug_for("Tavern no floor (1x1 a)") == "tavern-no-floor-1x1-a"


def test_url_is_the_asset_permalink():
    assert url_for("castle merlon 1x1") == \
        "https://talestavern.com/asset/castle-merlon-1x1/"
