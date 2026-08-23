"""Index the asset library by *kit*, and say what each kit can and cannot build.

Three separate defects on this project came from not knowing what was in the
library: a rampart built from curtain-wall pieces, a town crowned with wooden
hoardings, and a facade that turned every corner in another kit's material.
Each was found by building the wrong thing and looking at it. This is the
lookup that should have come first.

**The kit is `folder`, not the name and not `pack`.** `pack` is the DLC
("Medieval Fantasy" covers castle, rural, tavern and thatch alike) and
`group_tag` names a *form* -- "corner", "wall" -- so the same tag covers
castle stone, rural boarding and a spaceship bulkhead. `folder` is the family,
and it is what the game's own asset library shows down its left-hand side.
Getting this wrong is not academic: `Village Roof Side Wall 02` sits in folder
**Tavern**, so a name-based match called its kit "village", found no corner
called "village *", and mitred one -- while `Tavern no floor (1x1 a)`, a
1x1x2.0 corner in the same folder, sat there unused.

Usage -- the point is to be searched, not read end to end::

    python tools/kit_index.py                    # rewrite docs/asset-index.md
    python tools/kit_index.py --kit Tavern       # everything in one kit
    python tools/kit_index.py --role corner      # every kit's corner pieces
    python tools/kit_index.py --role corner --shape cell --height 2.0
    python tools/kit_index.py --complete         # kits that can build a house

`--complete` is the one that answers the question that keeps coming up: which
kits ship a wall *and* a matching corner *and* a window at the sizes the
generator places, so a facade can be one material all the way round.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith.catalog import load_or_build

#: Roles the building generator actually places, and the shape each needs.
#: A role is a *form at a size*: "corner" alone is not a specification, which
#: is how a 2x2 quarter-tower and a 0.5-thick fin both passed as corners.
ROLES: dict[str, dict] = {
    # A wall segment: thin on one axis, one cell long, two tiles tall.
    "wall":         {"forms": ("wall",), "shape": "panel", "height": 2.0},
    "window":       {"forms": ("wall", "window"), "shape": "panel", "height": 2.0,
                     "needs": ("window",)},
    "door":         {"forms": ("door",), "shape": "panel", "height": None},
    # An outside corner: a full cell, the same height as the wall beside it.
    "corner":       {"forms": ("corner",), "shape": "cell", "height": 2.0},
    # A reflex corner: thin both ways, it only plugs the notch.
    "inner_corner": {"forms": ("corner",), "shape": "sliver", "height": 2.0,
                     "needs": ("inner",)},
    "floor":        {"forms": ("floor",), "shape": "cell", "max_height": 0.5},
    "roof":         {"forms": ("roof",), "shape": "cell", "max_height": 2.0},
    # The pack's own answer to the storey problem: wall and floor in one piece,
    # 2.5 tall, so there is no separate slab edge to see from outside.
    "wall_floor":   {"forms": ("wall", "corner"), "shape": "cell", "height": 2.5},
}

#: Roles a kit needs before a whole house can be built from it alone.
HOUSE_ROLES = ("wall", "window", "corner", "floor", "roof")


def shape_of(a) -> str:
    """A shape class, because a footprint is the thing assumptions get wrong."""
    sx, sz = a.size_x, a.size_z
    lo, hi = min(sx, sz), max(sx, sz)
    if hi > 1.0:
        return "big"
    if (sx, sz) == (1.0, 1.0):
        return "cell"
    if lo <= 0.5 and hi >= 1.0:
        return "panel"
    if lo <= 0.5 and hi <= 0.5:
        return "sliver"
    return "part"


def form_of(a) -> str:
    """The form a piece takes, from its group tag, refined by its name.

    `group_tag` is the honest signal and the name is the tiebreak -- the other
    way round is how "Tavern no floor" once satisfied a request for a floor.
    """
    g = (a.group_tag or "").strip().lower()
    n = a.name.lower()
    # A roof kit's side wall is a wall. The Village panels the facade is built
    # from are all tagged `group='roof'` because they ship in a roof set, and
    # taking the tag at its word files the only 1-cell window in the medieval
    # set under "roof" -- which is how the Tavern kit read as having no window
    # while three of its panels were windows and walls.
    if "roof" in g and "wall" in n:
        return "corner" if "corner" in n else "wall"
    for key in ("corner", "floor", "roof", "door", "stairs", "wall"):
        if key in g:
            # A "wall base" is still a wall; a "wall corner" is a corner.
            if key == "wall" and "corner" in n:
                return "corner"
            return "stairs" if key == "stairs" else key
    for key in ("corner", "window", "door", "floor", "roof", "stairs", "wall"):
        if key in n:
            return key
    return g or "other"


def matches(a, role: str) -> bool:
    spec = ROLES[role]
    if form_of(a) not in spec["forms"]:
        return False
    if shape_of(a) != spec["shape"]:
        return False
    h = spec.get("height")
    if h is not None and abs(a.size_y - h) > 1e-6:
        return False
    mh = spec.get("max_height")
    if mh is not None and a.size_y > mh + 1e-6:
        return False
    n = a.name.lower()
    for word in spec.get("needs", ()):
        if word not in n:
            return False
    if role == "corner" and "inner" in n:
        return False
    if role == "wall" and any(w in n for w in ("window", "door", "corner")):
        return False
    return True


def build(assets) -> dict:
    kits: dict[str, dict] = {}
    for a in assets:
        if a.kind != "tile":
            continue
        kit = a.folder or "(none)"
        e = kits.setdefault(kit, {"pack": a.pack, "assets": [], "roles": {}})
        e["assets"].append(a)
        for role in ROLES:
            if matches(a, role):
                e["roles"].setdefault(role, []).append(a.name)
    return kits


def render_markdown(kits: dict) -> str:
    out = [
        "# Asset index, by kit",
        "",
        "Generated by `tools/kit_index.py` -- do not hand-edit; regenerate it.",
        "",
        "**The kit is the catalog's `folder`.** `pack` is the DLC and",
        "`group_tag` is a *form*, so neither tells you whether two pieces",
        "belong together. `Village Roof Side Wall 02` is in folder `Tavern`,",
        "which is why looking for a corner named \"village *\" found nothing",
        "while `Tavern no floor (1x1 a)` sat in the same kit unused.",
        "",
        "## Which kits can build a whole house",
        "",
        "A facade is one material all the way round only if one kit supplies",
        "the wall, its window, and a corner at the size the generator places",
        "(a full cell, the wall's own height). Ticks are shapes that fit, not",
        "opinions about how they look -- probe before choosing.",
        "",
        "| kit | pack | " + " | ".join(HOUSE_ROLES) + " |",
        "|---|---|" + "---|" * len(HOUSE_ROLES),
    ]
    scored = []
    for kit, e in kits.items():
        n = sum(1 for r in HOUSE_ROLES if e["roles"].get(r))
        scored.append((n, kit))
    for n, kit in sorted(scored, reverse=True):
        if n < 2:
            continue
        e = kits[kit]
        cells = "".join(
            (" %s |" % ("**%d**" % len(e["roles"][r]) if e["roles"].get(r) else "--"))
            for r in HOUSE_ROLES
        )
        out.append(f"| `{kit}` | {e['pack']} |{cells}")

    out += ["", "## Every kit in full", ""]
    for kit in sorted(kits):
        e = kits[kit]
        out += [f"### {kit}", "", f"*{e['pack']}* -- {len(e['assets'])} tiles", ""]
        if e["roles"]:
            for role in ROLES:
                if e["roles"].get(role):
                    out.append(f"- **{role}**: " + ", ".join(
                        f"`{n}`" for n in sorted(e["roles"][role])))
            out.append("")
        out += ["| asset | form | shape | size |", "|---|---|---|---|"]
        for a in sorted(e["assets"], key=lambda a: a.name):
            out.append(f"| `{a.name}` | {form_of(a)} | {shape_of(a)} | "
                       f"{a.size_x:g} x {a.size_y:g} x {a.size_z:g} |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit")
    ap.add_argument("--role", choices=sorted(ROLES))
    ap.add_argument("--shape", choices=("cell", "panel", "sliver", "big", "part"))
    ap.add_argument("--height", type=float)
    ap.add_argument("--complete", action="store_true")
    ap.add_argument("--out", default="docs/asset-index.md")
    args = ap.parse_args()

    assets = [a for a in load_or_build().assets if not getattr(a, "deprecated", False)]
    kits = build(assets)

    if args.complete:
        for kit in sorted(kits):
            e = kits[kit]
            have = [r for r in HOUSE_ROLES if e["roles"].get(r)]
            if len(have) >= 3:
                miss = [r for r in HOUSE_ROLES if r not in have]
                print(f"{kit:24s} {e['pack']:22s} has {','.join(have)}"
                      + (f"   MISSING {','.join(miss)}" if miss else "   COMPLETE"))
        return

    if args.kit or args.role or args.shape or args.height is not None:
        for kit in sorted(kits):
            if args.kit and args.kit.lower() not in kit.lower():
                continue
            rows = kits[kit]["assets"]
            if args.role:
                rows = [a for a in rows if matches(a, args.role)]
            if args.shape:
                rows = [a for a in rows if shape_of(a) == args.shape]
            if args.height is not None:
                rows = [a for a in rows if abs(a.size_y - args.height) < 1e-6]
            for a in sorted(rows, key=lambda a: a.name):
                print(f"{kit:22s} {a.name:44s} {form_of(a):8s} {shape_of(a):6s} "
                      f"{a.size_x:g}x{a.size_y:g}x{a.size_z:g}")
        return

    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(kits), encoding="utf-8")
    blob = {kit: {"pack": e["pack"], "roles": e["roles"],
                  "assets": [{"name": a.name, "form": form_of(a), "shape": shape_of(a),
                              "size": [a.size_x, a.size_y, a.size_z]}
                             for a in sorted(e["assets"], key=lambda a: a.name)]}
            for kit, e in kits.items()}
    pathlib.Path("out/kit-index.json").write_text(
        json.dumps(blob, indent=1), encoding="utf-8")
    print(f"wrote {path} and out/kit-index.json  ({len(kits)} kits)")


if __name__ == "__main__":
    main()
