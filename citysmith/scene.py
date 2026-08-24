"""A scene: one building, the people in it, and where the party is standing.

This is the step between a town and a session. `citysmith build` puts a whole
settlement on a board at 5 ft a tile; a scene takes **one** of its buildings and
prepares the board the party actually walks onto -- the interior, an apron of
ground so the door opens onto something, four marks on the floor for the tokens,
and a brief naming who is inside and why the room is worth playing in.

What it writes, per scene, in its own directory:

    <scene_id>.slab.txt         the board, in paste order
    <scene_id>-paste-order.txt  which file first, for anything driving the paste
    scene.json                  the manifest: board name, marks, occupants
    brief.md                    the GM-facing page
    plan.svg                    the floorplan, for reference at the table

**The scene id and the board name are derived, not allocated.** Walking into
the same building twice has to produce the same id, so that the second visit can
find the first visit's board instead of building another one. The id is the town
and the building's own id -- `graybank-tavern-0014` -- and the manifest carries
the building's centroid so a re-import that shuffles the ids can be *detected*
rather than silently reusing the wrong room.

**Tokens are not in the slab, and cannot be.** A v2 slab's creature count is
always zero (`docs/slab-format-v2.md`), so a party cannot be pasted in. What is
pasted is the *marks*: one floor tile per character, replacing the boards under
them, in the room behind the front door -- so the GM drops four minis onto four
squares that are already the right squares.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
from dataclasses import asdict, dataclass, field

from . import interior
from .build import DEFAULT_CHUNK_TILES, Builder, build_interior, cell_of
from .config import Config
from .floorplan import Floorplan
from .layout import Layout, LayoutBuilding
from .palette import Palette
from .slab import encode

SCENE_VERSION = 1


# -- the manifest -------------------------------------------------------------

@dataclass
class Mark:
    """Where one character is standing when the scene opens."""

    name: str
    x: int
    z: int
    level: int = 0


@dataclass
class Scene:
    """Everything about one prepared scene that outlives the build."""

    scene_id: str
    town: str
    building_id: str
    building_name: str
    kind: str
    board: str
    seed: int
    style: str
    levels: int
    width: int
    depth: int
    #: The building's centroid in the layout, so that a re-import which moves
    #: or renumbers buildings can be caught instead of reusing the wrong board.
    centroid: tuple[float, float]
    entrance: str
    hook: str = ""
    party: list[Mark] = field(default_factory=list)
    occupants: list[dict] = field(default_factory=list)
    slabs: list[str] = field(default_factory=list)
    assets: int = 0
    source: str = ""
    built: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scene_version"] = SCENE_VERSION
        d["centroid"] = list(self.centroid)
        return d

    def save(self, path: str | os.PathLike[str]) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Scene":
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if d.get("scene_version") != SCENE_VERSION:
            raise ValueError("scene file version mismatch; rebuild the scene")
        d.pop("scene_version")
        d["party"] = [Mark(**m) for m in d.get("party", [])]
        d["centroid"] = tuple(d.get("centroid", (0.0, 0.0)))
        return cls(**d)

    def summary(self) -> str:
        return (
            f"{self.building_name} ({self.kind}) in {self.town} -- "
            f"{self.width}x{self.depth} tiles, {self.levels} level(s), "
            f"{len(self.occupants)} occupant(s), {len(self.party)} party mark(s), "
            f"{self.assets:,} assets in {len(self.slabs)} slab(s)\n"
            f"  board: {self.board}"
        )


# -- naming -------------------------------------------------------------------

def slug(text: str) -> str:
    """A filename-safe, stable slug. Lowercase, words joined by hyphens."""
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "unnamed"


def scene_id(town: str, building: LayoutBuilding) -> str:
    """Stable per town and building, because reuse depends on it.

    The building's own id rather than its name: FTG names six of Graybank's
    buildings "Farm", and two scenes that collide on an id are two visits that
    walk into each other's board.
    """
    return f"{slug(town)}-{slug(building.id)}"


def display_name(building: LayoutBuilding) -> str:
    """What to call the building. Authored where there is one, invented where
    there is not -- MFCG exports geometry only, so half the towns have none."""
    return building.name.strip() or interior._fallback_name(building)


#: How much of a board name the campaign list actually shows. **Measured, not
#: estimated**: a board renamed to `ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop`
#: renders in that list as `ABCDEFGHIJKLMNOP…` -- sixteen. The row clips on
#: *pixel* width and the list is set in small capitals, so mixed case gets
#: further: `The Halfling and the Fox - Graybank interior` shows as
#: `The Halfling and the F…`, twenty-two. Sixteen is the worst case and the one
#: worth designing to.
VISIBLE_CHARS = 16


def _building_number(building: LayoutBuilding) -> str:
    """The numeric tail of a building id: `tavern-0014` -> `14`."""
    tail = building.id.rsplit("-", 1)[-1]
    if not tail.isdigit():
        return building.id
    return tail.lstrip("0") or "0"


def _identifies(layout: Layout, name: str) -> bool:
    """True when this name picks one building out of its town *on sight*.

    Compared over the visible prefix, and against every building rather than
    only the ones that have boards: which buildings get boards is not knowable
    when the name is chosen, and a name that would collide later is a name that
    is already wrong.
    """
    key = name[:VISIBLE_CHARS].strip().lower()
    seen = 0
    for other in layout.buildings:
        if display_name(other)[:VISIBLE_CHARS].strip().lower() == key:
            seen += 1
            if seen > 1:
                return False
    return True


def board_name(cfg: Config, layout: Layout, building: LayoutBuilding) -> str:
    """What the board is called in the campaign list.

    **The building goes first, and a discriminator goes in front of it rather
    than behind**, because the list clips at :data:`VISIBLE_CHARS` and tells
    you nothing else about a board -- no size, no date, no contents. An id
    appended to the end is an id nobody can see.

    The number is added only where the name does not identify the building on
    its own, which is not the rare case it sounds like. Counted over the three
    towns: **44% to 77% of buildings share their first sixteen characters with
    another building in the same town**. `Residence` occurs 129 times in East
    Tradebourne and `The Clayclub Residence` eleven times in Pelvesthollow. The
    rule this replaces fired only on an exact duplicate name and appended the
    id -- testing the wrong condition, and writing the answer where it could
    not be read.
    """
    name = display_name(building)
    if not _identifies(layout, name):
        name = f"{_building_number(building)} {name}"

    template = cfg.get("board.name_template")
    full = template.format(
        prefix=cfg.get("board.prefix", ""), town=layout.name, building=name,
    )
    limit = int(cfg.get("board.max_name", 60))
    return full if len(full) <= limit else full[: limit - 1].rstrip() + "…"


# -- where the party stands ---------------------------------------------------

def _door_cell(fp: Floorplan) -> tuple[int, int, str]:
    for door in fp.doors:
        if door.exterior:
            return door.x, door.z, door.side
    rect = fp.rect_on(0)
    return rect.x + rect.w // 2, rect.z2 - 1, "s"


_OUTWARD = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}


def party_marks(fp: Floorplan, size: int, names: list[str],
                arrival: str = "inside", pad: int = 3) -> list[Mark]:
    """Cells for the tokens: clustered by the door, never in the doorway.

    Inside, they are the nearest floor cells to the front door -- so the party
    arrives where a party arrives, together and blocking nothing. The door cell
    itself is excluded: a mini standing in the opening plugs it, which is the
    same reason the town's doorways are centred on their wall run rather than
    put against a corner.
    """
    dx, dz, side = _door_cell(fp)
    ox, oz = _OUTWARD[side]
    rect = fp.rect_on(0)

    if arrival == "outside":
        # The apron in front of the door: outside the shell, within the pad.
        pool = [
            (x, z)
            for z in range(rect.z - pad, rect.z2 + pad)
            for x in range(rect.x - pad, rect.x2 + pad)
            if not (rect.x <= x < rect.x2 and rect.z <= z < rect.z2)
        ]
        origin = (dx + ox, dz + oz)
    else:
        pool = [(x, z) for x, z in rect.tiles() if (x, z) != (dx, dz)]
        origin = (dx + ox * -1, dz + oz * -1)  # one step in from the door

    pool.sort(key=lambda c: ((c[0] - origin[0]) ** 2 + (c[1] - origin[1]) ** 2,
                             c[0], c[1]))
    chosen = pool[:size]
    return [
        Mark(name=(names[i] if i < len(names) else f"Party {i + 1}"),
             x=x, z=z, level=0)
        for i, (x, z) in enumerate(chosen)
    ]


# -- building it --------------------------------------------------------------

def build(
    layout: Layout,
    building: LayoutBuilding,
    palette: Palette,
    cfg: Config,
) -> tuple[Scene, Builder, Floorplan]:
    """Build the scene's geometry and its manifest."""
    seed = int(cfg.get("seed", 33))
    pad = int(cfg.get("interior.pad", 3))
    spread = bool(cfg.get("interior.spread_levels", True))

    fp = interior.plan(
        layout, building,
        seed=seed,
        max_levels=int(cfg.get("interior.max_levels", 2)),
        min_room=int(cfg.get("interior.min_room", 3)),
        spread=spread,
        gap=int(cfg.get("interior.level_gap", 2)),
    )
    # Off the origin by the apron, so nothing in the scene has a negative
    # coordinate to be normalised away later.
    interior.translate(fp, pad, pad)

    b = build_interior(
        fp, palette,
        seed=seed,
        roof=bool(cfg.get("interior.roof", False)),
        prop_density=float(cfg.get("interior.prop_density", 0.12)),
        stack=not spread,
    )

    floor_top = palette.require("floor").size_y
    _lay_apron(b, fp, pad, floor_top)

    marks = party_marks(
        fp,
        size=int(cfg.get("party.size", 4)),
        names=[str(n) for n in (cfg.get("party.names") or [])],
        arrival=str(cfg.get("party.arrival", "inside")),
        pad=pad,
    )
    _lay_marks(b, marks, palette, str(cfg.get("party.mark_role", "party_mark")),
               floor_top)

    roster = {}
    roster_path = cfg.get("occupants.roster") or ""
    if roster_path:
        roster = interior.load_roster(roster_path)
    people = interior.occupants(
        building, seed=seed, roster=roster,
        hour=str(cfg.get("occupants.hour", "day")),
    )

    rect = fp.rect_on(0)
    scene = Scene(
        scene_id=scene_id(layout.name, building),
        town=layout.name,
        building_id=building.id,
        building_name=display_name(building),
        kind=building.kind,
        board=board_name(cfg, layout, building),
        seed=seed,
        style=str(cfg.get("style", "medieval")),
        levels=fp.levels,
        width=rect.w,
        depth=rect.d,
        centroid=tuple(round(v, 2) for v in building.centroid),
        entrance=_door_cell(fp)[2],
        hook=interior.hook(building, seed),
        party=marks,
        occupants=[
            {**asdict(p), "room": room}
            for p, room in zip(people, _rooms_for(people, fp, seed))
        ],
        source=layout.source,
        built=datetime.date.today().isoformat(),
    )
    return scene, b, fp


def _lay_apron(b: Builder, fp: Floorplan, pad: int, top: float) -> None:
    """A ring of ground round every level, and paving in front of the door.

    Without it the shell is a box floating on bare board: the door opens onto
    nothing and there is nowhere to stand while the party decides to go in.
    Laid by :meth:`Builder.surface`, so its top is flush with the floor inside
    -- a 15 inch step at the threshold is the kerb defect one storey down.
    """
    if pad <= 0:
        return
    inside: set[tuple[int, int]] = set()
    for level in range(fp.levels):
        inside.update(fp.rect_on(level).tiles())

    # The path from the door out, worked out *first* so those cells are paved
    # instead of grassed. Laid over the grass they would be two coplanar
    # surfaces in one cell, which is the seam the party marks are careful to
    # avoid. It stops at anything already built: with the levels side by side
    # the ground floor's door can face the upper floor's block, and a path
    # that resumes on the far side of it reads as two paths.
    dx, dz, side = _door_cell(fp)
    ox, oz = _OUTWARD[side]
    approach: set[tuple[int, int]] = set()
    for step in range(1, pad + 1):
        cell = (dx + ox * step, dz + oz * step)
        if cell in inside:
            break
        approach.add(cell)

    laid: set[tuple[int, int]] = set()
    for level in range(fp.levels):
        rect = fp.rect_on(level)
        for z in range(rect.z - pad, rect.z2 + pad):
            for x in range(rect.x - pad, rect.x2 + pad):
                if (x, z) in inside or (x, z) in laid:
                    continue
                laid.add((x, z))
                b.surface("street" if (x, z) in approach else "ground", x, z, top)


def _lay_marks(b: Builder, marks: list[Mark], palette: Palette, role: str,
               top: float) -> None:
    """One tile per character, *replacing* the floor under them.

    Replacing rather than covering: a mark laid on top of a floor tile is two
    coplanar surfaces in one cell, which is the seam that shifts with the
    camera. The cell's props go too -- a character does not arrive inside a
    barrel.
    """
    if palette.resolve(role) is None:
        return
    cells = {(m.x, m.z) for m in marks}
    b.clear_cells(cells, below=top + 0.01)
    for m in marks:
        b.surface(role, m.x, m.z, top)


def _rooms_for(people: list, fp: Floorplan, seed: int) -> list[str]:
    """Put each occupant in a room, biggest rooms first, so the brief can say
    where they are. Geometry stays out of it -- 'in the common room' is what a
    GM needs, and a coordinate is not."""
    rooms = sorted(fp.rooms_on(0), key=lambda r: -r.rect.area) or []
    if not rooms:
        return ["" for _ in people]
    return [rooms[i % len(rooms)].name for i in range(len(people))]


# -- writing it out -----------------------------------------------------------

def write(scene: Scene, b: Builder, fp: Floorplan, out_dir: str | os.PathLike[str],
          cfg: Config) -> list[pathlib.Path]:
    """Write the slabs, the manifest, the brief and the plan.

    Chunked the same way a tiled town is -- every layer together, no packing --
    so if an interior ever does exceed the byte cap its pieces tile rather than
    overlap. Almost every one is a single slab.
    """
    directory = pathlib.Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.slab.txt"):
        stale.unlink()

    # One cell big enough to hold the whole scene, so an interior is ONE paste.
    # At the 24-tile default a two-level plan laid side by side spans two grid
    # columns and comes out as two slabs -- two pastes and a join, for 459
    # assets. Over the byte cap the quadtree still subdivides, so this only
    # removes splits that were never needed.
    span = 0
    for level in range(fp.levels):
        rect = fp.rect_on(level)
        span = max(span, rect.x2, rect.z2)
    plan = b.chunk_plan(
        max_assets=int(cfg.get("interior.max_assets", 4000)),
        chunk_tiles=max(DEFAULT_CHUNK_TILES, span + 8),
        skip_open_country=False,
        pack=False,
        by_layer=False,
    )
    written: list[pathlib.Path] = []
    single = len(plan.chunks) == 1
    for chunk in plan.chunks:
        name = (f"{scene.scene_id}.slab.txt" if single
                else f"{scene.scene_id}-{chunk.label}.slab.txt")
        path = directory / name
        path.write_text(encode(chunk.slab), encoding="utf-8")
        written.append(path)

    scene.slabs = [p.name for p in written]
    scene.assets = sum(len(c.slab.placements) for c in plan.chunks)

    (directory / f"{scene.scene_id}-paste-order.txt").write_text(
        "\n".join(scene.slabs) + "\n", encoding="utf-8"
    )
    scene.save(directory / "scene.json")
    (directory / "brief.md").write_text(brief(scene), encoding="utf-8")

    from . import render
    render.write(render.floorplan_svg(fp, marks=scene.party), directory / "plan.svg")

    return written


def brief(scene: Scene) -> str:
    """The GM-facing page: what the room is, who is in it, where the party is."""
    lines = [
        f"# {scene.building_name}",
        "",
        f"*{scene.kind.title()} in {scene.town}* -- {scene.width}x{scene.depth} "
        f"tiles ({scene.width * 5}x{scene.depth * 5} ft), {scene.levels} level(s), "
        f"door on the {_COMPASS[scene.entrance]} side.",
        "",
        f"**Board:** `{scene.board}`",
        "",
        "## The hook",
        "",
        f"{scene.hook.capitalize()}.",
        "",
        "## Who is inside",
        "",
    ]
    if scene.occupants:
        lines.append("| Who | Role | Doing | Where |")
        lines.append("|---|---|---|---|")
        for p in scene.occupants:
            lines.append(
                f"| {p['name']} | {p['role']} | {p.get('doing', '')} | "
                f"{p.get('room', '')} |"
            )
        if not any(p.get("authored") for p in scene.occupants):
            lines += [
                "",
                "These are derived, not exported: the GeoJSON carries a name, a type "
                "and a material per building and no people at all. They are stable "
                f"for seed {scene.seed}, so the same faces are here next visit. To "
                "replace them, write a roster keyed on `{}` and point "
                "`occupants.roster` at it.".format(scene.building_id),
            ]
    else:
        lines.append("Nobody. It is a store shed.")

    lines += [
        "",
        "## Where the party starts",
        "",
        "| Character | Tile |",
        "|---|---|",
    ]
    for m in scene.party:
        lines.append(f"| {m.name} | ({m.x}, {m.z}) |")
    lines += [
        "",
        "Each mark is a tile of its own in the floor, in the room behind the "
        "front door. A slab cannot carry creatures -- a v2 slab's creature count "
        "is always zero -- so the minis are dropped onto the marks by hand.",
        "",
        "## Getting it onto a board",
        "",
        "```powershell",
        f".\\tools\\scene.ps1 enter -Scene {scene.scene_id}",
        "```",
        "",
        "That reuses the board if this building has been visited before, and "
        "builds one if it has not. Nothing is ever deleted.",
        "",
    ]
    return "\n".join(lines)


_COMPASS = {"n": "north", "s": "south", "e": "east", "w": "west"}
