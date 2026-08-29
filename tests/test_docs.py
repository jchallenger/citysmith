"""Where a document makes a checkable claim about the code, check it.

`tasks.json` has `citysmith tasks --check` and a test in the suite. CLAUDE.md
has neither, and it is the file every session reads first -- so a claim there
outlives its truth silently. One did: a section headed "OPEN: `entombed()`
hollows the rampart" survived the fix by an unknown number of sessions and was
found by accident while planning something else.

This does not try to verify the whole file, which is prose and mostly cannot be.
It pins the specific pairings where a doc and the code can be made to disagree.
"""

import pathlib
import sys

sys.path.insert(0, ".")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_claude_md_has_no_stale_open_rampart_section():
    """The doc must not call the rampart hollow while the code builds it solid.

    `entombed()` laid only the top course of a wall cell walled in on all four
    sides, which `check_placements` reads as a hole -- 85 of 300 on Forest
    Church. It is gone, `rampart-solid` is done, and a Forest Church build lays
    300 wall cells with no masonry failure. Either both halves say so or the
    pair is inconsistent and this fails.
    """
    build = (ROOT / "citysmith" / "build.py").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    hollowed = "def entombed(" in build
    claims_open = "OPEN: `entombed()` hollows the rampart" in claude

    assert hollowed == claims_open, (
        "CLAUDE.md says the rampart is hollowed OPEN and build.py has no "
        "`entombed()`" if claims_open else
        "build.py has re-introduced `entombed()` and CLAUDE.md does not warn "
        "that the masonry check reads an entombed cell as a hole")


def test_the_docs_index_in_claude_md_points_at_files_that_exist():
    """A doc index that names a missing file sends the next session hunting.

    CLAUDE.md opens with a list of the user-facing documents; every path it
    names in backticks under `docs/` has to be there.
    """
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    import re

    named = set(re.findall(r"`(docs/[a-z0-9\-]+\.md)`", claude))
    assert named, "no docs referenced -- has the index moved?"
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert not missing, f"CLAUDE.md names documents that do not exist: {missing}"
