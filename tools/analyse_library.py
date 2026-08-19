"""Learn construction technique from hand-built community slabs.

Answers the questions our own probes could not: which assets experienced
builders reach for, how they layer a building vertically, and -- critically --
how they rotate roof pieces, which is the convention we have been guessing at.
"""
from __future__ import annotations

import pathlib, sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from citysmith.catalog import load_or_build
from citysmith.slab import decode

cat = load_or_build()
byid = {a.id: a for a in cat.assets}
LIB = pathlib.Path("library")

for path in sorted(LIB.rglob("*.slab")):
    try:
        s = decode(path.read_text())
    except Exception as exc:
        print(f"{path}: FAILED {exc}"); continue
    known = [(p, byid[p.asset_id]) for p in s.placements if p.asset_id in byid]
    unknown = len(s.placements) - len(known)
    (mn), (mx) = s.bounds()
    print(f"\n=== {path.relative_to(LIB)} — {len(s.placements):,} placements "
          f"({unknown} unknown assets) span {mx[0]-mn[0]:.0f}x{mx[2]-mn[2]:.0f} "
          f"h{mx[1]-mn[1]:.1f}")
    groups = Counter(a.group_tag for _, a in known)
    print("  groups:", ", ".join(f"{g or '-'}x{k}" for g, k in groups.most_common(6)))
    for role in ("roof", "Roof", "wall", "Wall", "floor", "Floor"):
        items = [(p, a) for p, a in known if a.group_tag == role]
        if not items: continue
        names = Counter(a.name for _, a in items)
        rots = Counter(p.rot for p, _ in items)
        ys = Counter(round(p.y, 1) for p, _ in items)
        print(f"  [{role}] {len(items):4d}  rots={dict(sorted(rots.items()))}")
        print(f"        top: {', '.join(f'{n}x{k}' for n,k in names.most_common(3))}")
        print(f"        y:   {dict(sorted(ys.items())[:8])}")
