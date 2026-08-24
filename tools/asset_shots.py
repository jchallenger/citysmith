"""Look a candidate asset up as a *picture* before building a probe for it.

CLAUDE.md records the rule and then does not follow it: "before
reverse-engineering a host application's behaviour, spend twenty minutes on its
modding community", with Tales Tavern's asset archive named as the thing that
"would have shortened several of the probe sessions". It never got wired in.
The cost is measurable -- `wall_probe.py`, `parapet_probe.py` and
`tower_probe.py` are 418 lines that exist to answer *what shape and material is
this piece*, and every one of their findings is legible in a screenshot:

  * `md_wall_1x1_diag_01` measures a full cell and is a blade across it.
  * `castle merlon 1x1` is tagged `group='merlon'` and is a *wooden hoarding*.
    That one crowned the whole circuit in crates for eleven revisions.
  * `Castle Ruins Wallbase 02` is ruined masonry, holes by design.
  * `md_tower_wall_01` is 4x2x4 and is a *quarter* of an 8x8 drum.

Nothing in `ColliderBoundsBound` distinguishes any of those from a solid block.
A render does, instantly.

**This does not replace the probes and must not be read as doing so.** A
picture answers "what is this piece"; it cannot answer "which quarter turn
closes a hip" (`roofrot_probe.py`), "how does this bed read through translucent
water" (`water_probe.py`), or "does a run one cell thick show daylight"
(`wall_probe.py`'s actual harshest case). The archive is the *shortlist*; the
probe is the *verification*, and CLAUDE.md's standard -- orbit four sides plus
overhead, keep known-bad pieces as controls -- stands unchanged. What this
buys is probing three candidates instead of eight, and never probing a piece a
render rules out in a second.

Measured 2026-08-24, against the site's own sitemap (3,596 asset pages):

  * The slug is the asset name lowercased, with runs of non-alphanumerics
    becoming a single hyphen -- **except that underscores are kept**. That
    exception is the whole thing: `castle merlon 1x1` -> `castle-merlon-1x1`
    but `md_wall_1x1_diag_01` -> `md_wall_1x1_diag_01`. Hyphenating everything
    resolves 77.2% of the catalog; keeping underscores resolves **95.9%**.
  * Coverage is **100% on every kit the generator builds from** -- Rural,
    Tavern, Castle Fortified, Abandoned Village, MegaDungeon, CastleRuins,
    Harbor, Furniture, Food & Drink -- and 98.8% of Nature. The 131 misses are
    almost all creature minis (Human, Elf, Undead, Monstrous), which the
    generator never places, plus 12 oddly-punctuated Doors.
  * **The link is self-verifying, and that is why the slug rule is allowed to
    be a rule.** Each page carries the asset's UUID, and it is the *same
    namespace as ours*: `castle-merlon-1x1` reports
    `fc6e9582-377f-4e5c-aa7f-1c1108254a9f`, which is what our catalog holds.
    `--verify` fetches and checks that, so a slug that silently lands on the
    wrong asset is caught rather than believed.

**No mirroring, and it is deliberate.** `docs/asset-index.md` is regenerated
locally rather than committed because it is an extraction of someone else's
packs; the same reasoning applies harder to someone else's renders. This emits
*links*. The site's REST API is closed to unauthenticated callers, so nothing
here goes near it; `robots.txt` is `Disallow:` (empty) and advertises the
sitemap, which is the sanctioned route `--verify` uses, one polite request per
asset with a delay.

Usage::

    python tools/asset_shots.py --role corner --kit Tavern
    python tools/asset_shots.py --name "castle merlon 1x1"
    python tools/asset_shots.py --kit Rural --role roof --verify

Beware the one thing a name cannot do: **371 of 3,200 assets share a name with
another asset** (139 names, up to nine deep -- `Aberration Floor 2x2` covers
sizes from 2x0.5x2 to 2x2.5x2). None of the pieces the generator currently pins
is ambiguous except `md_wall_1x1_diag_01`, which is already rejected, but a
name-keyed lookup is not safe in general. `--verify` prints AMBIGUOUS when the
catalog holds more than one asset under the name being linked.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, ".")

from citysmith.catalog import load_or_build

from kit_index import ROLES, form_of, matches, shape_of  # noqa: E402

BASE = "https://talestavern.com/asset/"

#: Polite, identifiable, and honest about what it is.
USER_AGENT = "citysmith-asset-shots/1.0 (+https://github.com/; asset lookup)"

#: Seconds between requests in --verify. The archive is a volunteer-run
#: community site; nothing here is time-critical.
DELAY = 1.0

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_IMG = re.compile(r'src="(https://talestavern\.com/wp-content/uploads/20\d\d/\d\d/[^"]+\.(?:png|jpg))"')


def slug_for(name: str) -> str:
    """The archive's slug for an asset name.

    Underscores survive; every other run of non-alphanumerics becomes one
    hyphen. See the module docstring for the measurement that settled this --
    hyphenating underscores too costs 19 points of coverage and every
    MegaDungeon piece.
    """
    s = re.sub(r"[^a-z0-9_]+", "-", name.lower())
    return re.sub(r"-+", "-", s).strip("-")


def url_for(name: str) -> str:
    return f"{BASE}{slug_for(name)}/"


def _fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def verify(asset, dupes: dict[str, int]) -> tuple[str, str]:
    """Fetch the page and check it is the asset we mean.

    Returns (status, detail). The UUID is the check that matters: a slug can
    land on a *different* piece with a similar name, and the catalog's own id
    is the only thing that settles it.
    """
    url = url_for(asset.name)
    try:
        html = _fetch(url)
    except Exception as e:  # noqa: BLE001 - a network failure is a status, not a crash
        return "ERROR", str(e)
    if html is None:
        return "MISSING", url
    found = {u.lower() for u in _UUID.findall(html)}
    if asset.id.lower() not in found:
        return "MISMATCH", f"page has {sorted(found) or 'no uuid'}"
    # The render is not the first image on the page -- the site logo is, and
    # taking the first match reported every asset as `cropped-test_ista.png`.
    # The render's filename is built from the slug, so match on that.
    slug = slug_for(asset.name)
    img = next((u for u in _IMG.findall(html) if slug in u.rsplit("/", 1)[-1]), None)
    tag = "AMBIGUOUS" if dupes.get(asset.name, 0) > 1 else "OK"
    return tag, img or url


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kit", help="folder, e.g. Tavern -- the kit is the folder")
    ap.add_argument("--role", choices=sorted(ROLES))
    ap.add_argument("--shape", choices=("cell", "panel", "sliver", "big", "part"))
    ap.add_argument("--height", type=float)
    ap.add_argument("--name", action="append", default=[],
                    help="an exact asset name; repeatable")
    ap.add_argument("--verify", action="store_true",
                    help="fetch each page and check its UUID against ours "
                         f"({DELAY:g}s apart)")
    ap.add_argument("--limit", type=int, default=40,
                    help="stop after this many rows (default 40)")
    args = ap.parse_args()

    catalog = load_or_build()
    dupes = collections.Counter(a.name for a in catalog.assets)
    assets = [a for a in catalog.assets if not getattr(a, "deprecated", False)]

    if args.name:
        wanted = {n.lower() for n in args.name}
        rows = [a for a in assets if a.name.lower() in wanted]
    else:
        rows = [a for a in assets if a.kind == "tile"]
        if args.kit:
            rows = [a for a in rows if args.kit.lower() in (a.folder or "").lower()]
        if args.role:
            rows = [a for a in rows if matches(a, args.role)]
        if args.shape:
            rows = [a for a in rows if shape_of(a) == args.shape]
        if args.height is not None:
            rows = [a for a in rows if abs(a.size_y - args.height) < 1e-6]

    rows.sort(key=lambda a: ((a.folder or ""), a.name))
    if len(rows) > args.limit:
        print(f"# {len(rows)} matches, showing {args.limit} -- narrow with "
              f"--kit/--role/--shape or raise --limit\n")
        rows = rows[:args.limit]

    for a in rows:
        line = (f"{(a.folder or '-'):20s} {a.name:40s} {form_of(a):8s} "
                f"{a.size_x:g}x{a.size_y:g}x{a.size_z:g}")
        if args.verify:
            status, detail = verify(a, dupes)
            print(f"{line}  {status:9s} {detail}")
            time.sleep(DELAY)
        else:
            print(f"{line}  {url_for(a.name)}")

    if not rows:
        print("no matches")
    elif not args.verify:
        print(f"\n# {len(rows)} candidate(s). Open them, drop the ones that are "
              f"the wrong shape,\n# then probe what is left -- a render cannot "
              f"judge rotation, tiling or\n# how a run one cell thick reads. "
              f"Add --verify to check each link is the\n# asset the catalog "
              f"means.")


if __name__ == "__main__":
    main()
