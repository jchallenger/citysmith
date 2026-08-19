"""Download community slabs from Tales Tavern into a local study library.

The site publishes each slab as a base64 string in the page HTML. WebFetch's
summariser cannot reproduce a 20 KB string verbatim, so this pulls the raw
HTML and extracts the code with a regex -- deterministic and exact.

Stored under library/<type>/<slug>.slab for offline analysis. These are
other people's builds: we study construction technique from them, we do not
ship their geometry.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "library"
CODE = re.compile(rb"H4sIA[A-Za-z0-9+/=]{500,}")
UA = "Mozilla/5.0"


def fetch(url: str) -> bytes:
    return subprocess.run(["curl", "-sS", "-m", "60", "-A", UA, url],
                          capture_output=True).stdout


def harvest(slug: str, kind: str) -> pathlib.Path | None:
    html = fetch(f"https://talestavern.com/slab/{slug}/")
    hits = CODE.findall(html)
    if not hits:
        print(f"  !! {slug}: no slab code found", file=sys.stderr)
        return None
    code = max(hits, key=len).decode()
    out = LIB / kind
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{slug}.slab"
    path.write_text(code, encoding="ascii")
    print(f"  ok {kind}/{slug}.slab  {len(code):,} chars")
    return path


def listing(type_slug: str, pages: int = 1) -> list[str]:
    """Slugs linked from a slab-type listing page."""
    slugs: list[str] = []
    for page in range(1, pages + 1):
        url = (f"https://talestavern.com/slab-type/{type_slug}/"
               + (f"page/{page}/" if page > 1 else ""))
        html = fetch(url).decode("utf-8", "ignore")
        for m in re.finditer(r'href="https://talestavern\.com/slab/([a-z0-9\-]+)/"', html):
            if m.group(1) not in slugs:
                slugs.append(m.group(1))
    return slugs


if __name__ == "__main__":
    for type_slug, kind, pages in [("cabin", "cabin", 1), ("residence-home", "residence", 1)]:
        found = listing(type_slug, pages)
        print(f"{type_slug}: {len(found)} slabs listed")
        for slug in found[:6]:
            harvest(slug, kind)
