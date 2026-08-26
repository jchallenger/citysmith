"""Semantic role -> concrete asset resolution.

Generation code asks for a "floor" or an "exterior wall"; the palette decides
which of the 3,200 catalog assets that means for the chosen style. Keeping this
in one place is what lets the same city generator emit a medieval town or a
cyberpunk block without touching geometry code.

Each role lists candidate queries in priority order. The first query that
matches anything wins, so a style can be specific ("tavern wood floor") and
still fall back to something generic rather than failing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .catalog import Asset, Catalog, CatalogError

#: A role query: positional search terms plus catalog filter kwargs.
Query = tuple[tuple[str, ...], dict]


def q(*terms: str, **kwargs) -> Query:
    return (terms, kwargs)


#: Roles every style must be able to resolve for building generation to work.
REQUIRED_ROLES = ("floor", "wall", "door", "roof")

#: Roles placed into a wall segment; all must share the wall's footprint.
WALL_SEGMENT_ROLES = ("door", "wall_window", "wall_interior")

#: Roles that fill a whole cell in the *wall* course: an outside corner is one
#: full-cell piece rather than two wall segments meeting. Their height must
#: match the wall's or the storeys either side of a corner drift apart.
WALL_COURSE_ROLES = ("wall_corner", "wall_corner_civic")

#: Roles laid one-per-cell across the tile grid. Each must be exactly one tile
#: square, or it overhangs its neighbours -- the failure that put a 2-wide door
#: in a 1-wide wall and dragged an entire board half a tile off the grid.
CELL_ROLES = (
    "floor", "floor_upper", "floor_civic", "ground", "street", "water",
    "gravel", "roof", "bridge_deck",
) + WALL_COURSE_ROLES

#: Roles laid across a 2x2 block of cells.
BLOCK_ROLES = ("ground_2x2", "field")


@dataclass
class Style:
    """A named look, expressed as role -> candidate queries."""

    name: str
    roles: dict[str, list[Query]]
    #: Props keyed by room purpose, used to dress interiors.
    props: dict[str, list[Query]] = field(default_factory=dict)
    description: str = ""


def _tile(*terms: str, **kw) -> Query:
    kw.setdefault("kind", "tile")
    return q(*terms, **kw)


def _prop(*terms: str, **kw) -> Query:
    kw.setdefault("kind", "prop")
    # Appended rather than replacing, so a category that needs one of these on
    # purpose can still pass its own `exclude` and have both applied.
    kw["exclude"] = tuple(kw.get("exclude", ())) + _PROP_EXCLUDE
    return q(*terms, **kw)


# 1x1 footprint keeps placement math simple and predictable; the resolver falls
# back to looser queries when a style has no 1x1 variant.
_UNIT = {"size": (1.0, 1.0)}
_WALLSZ = {"size": (1.0, 0.5)}

#: Name fragments that mark a wall variant as something other than a plain
#: solid wall. Tags alone are not enough -- "concrete wall 1x1 window" carries
#: no "window" tag, only the name says so.
_WALL_NAME_EXCLUDE = ("window", "door", "arch", "corner", "gate", "broken", "half")

#: Name fragments marking edge/trim pieces rather than full field tiles.
_TRIM_NAME_EXCLUDE = ("decor", "edge", "corner", "side", "trim", "inner", "outer")

#: Themes that should never be picked up by a generic query -- they carry
#: strong visual baggage (ruined, haunted, alien) that reads as a mistake in an
#: ordinary town building.
_OFF_THEME = (
    "haunted", "abandoned", "dilapidated", "ruins", "aberration", "lava",
    "underdark", "bellowgloom", "cave", "burrow", "broken", "worn",
)

#: Name fragments naming a *different place* -- another biome or another
#: culture. Tags cannot catch these: "Desert floor 01" and "shogunFloor_1x1"
#: both tag as plain stone/wood flooring, and both were picked for a European
#: village, which is how Candlewell ended up paved in desert tiles with shogun
#: interiors. Excluded by name for every village-appropriate role.
_WRONG_SETTING = (
    "desert", "shogun", "jungle", "snow", "palace", "marble", "moorgoth",
    "dungeon", "harbor", "ship", "sand", "swamp", "pirate", "temple of",
)


#: Name fragments that disqualify a prop whatever category asked for it.
#:
#: **Every prop query in this file is a free-text term search**, which is the
#: one thing `CLAUDE.md`'s hard constraints tell you not to do -- "asset names
#: are inconsistent, matching them loosely lets 'Tavern no floor' satisfy a
#: request for a floor". Nothing enforced it on props, so ``_prop("bed")``
#: matched `Bed Double Moorgoth`, ``_prop("tree")`` matched `Tree, Festive`
#: and ``_prop("altar")`` matched `Altar (Evil)`. That was found by reading a
#: yard's clutter list and seeing four kinds of bed standing outdoors.
#:
#: Three groups, and each is a different mistake:
#:
#: * **seasonal and joke** -- a Christmas tree in a hamlet's woodland
#: * **grim and monstrous** -- an evil altar in the village chapel, a torture
#:   table in somebody's kitchen. A campaign may want those; it should ask.
#: * **wrong setting** -- the same `_WRONG_SETTING` already applied to tiles,
#:   which props never got.
_PROP_EXCLUDE = _WRONG_SETTING + (
    "festive", "christmas", "halloween", "pumpkin", "turkey", "snow flake",
    "easter", "birthday", "party",
    "evil", "torture", "aberration", "demon", "hell", "gore", "blood",
    "skull", "corpse", "sacrific", "cursed", "haunted", "grim",
)



#: Scope the medieval style to its own pack, so a shared tag on a sci-fi asset
#: cannot satisfy a medieval query.
_MED = {"pack": "Medieval Fantasy"}

#: Tags that disqualify a tile from being a plain solid wall.
_NOT_PLAIN_WALL = _OFF_THEME + ("roof", "window", "door", "arch")

#: Standard floor slab height in this pack; 1x1 floors that are not 0.5 tall
#: are usually decorative pieces (docks, ramps) rather than usable flooring.
_FLOOR_H = {"height": 0.5}

MEDIEVAL = Style(
    name="medieval",
    description="Generic medieval fantasy town -- timber, stone and thatch.",
    roles={
        "floor": [
            _tile(group="floor", tags=("wood", "floor"), exclude_tags=_OFF_THEME,
                  exclude=_TRIM_NAME_EXCLUDE + _WRONG_SETTING,
                  **_MED, **_FLOOR_H, **_UNIT),
            _tile(group="floor", tags=("stone", "floor"), exclude_tags=_OFF_THEME,
                  exclude=_TRIM_NAME_EXCLUDE + _WRONG_SETTING,
                  **_MED, **_FLOOR_H, **_UNIT),
            _tile(group="floor", tags=("floor",), exclude_tags=_OFF_THEME,
                  exclude=_TRIM_NAME_EXCLUDE + _WRONG_SETTING,
                  **_MED, **_FLOOR_H, **_UNIT),
        ],
        "floor_upper": [
            _tile(group="floor", tags=("wood", "floor"), exclude_tags=_OFF_THEME,
                  exclude=_WRONG_SETTING, **_MED, **_FLOOR_H, **_UNIT),
            _tile(group="floor", tags=("floor",), exclude_tags=_OFF_THEME,
                  exclude=_WRONG_SETTING, **_MED, **_FLOOR_H, **_UNIT),
        ],
        # Timber-framed village panel. Probed side by side against the
        # megadungeon brick this replaces and against Rural boarding: the md
        # blocks are dungeon masonry with deep relief, so they protrude at
        # different depths and leave dark gaps at every join -- the jumbled
        # seam that showed wherever a floor slab met a wall. Village is a flat
        # plane, and it is the family our window already comes from, so a
        # facade is now one kit instead of three meeting at each corner.
        "wall": [
            _tile(name=("Village Roof Side Wall 01", "Village Roof Side Wall 02"), **_MED),
            _tile(name=("Wall (Plain, Small)", "md_wall_1x1_01", "md_wall_1x1_02"), **_MED),
            _tile(group=("wall", "Wall"), tags=("wall",), exclude_tags=_NOT_PLAIN_WALL,
                  exclude=_WALL_NAME_EXCLUDE + _WRONG_SETTING, height=2.0,
                  **_MED, **_WALLSZ),
        ],
        # Timber-framed panel with a window: the village kit's own, and the
        # right window for a cottage. The castle window is a gothic arch --
        # probe-tested side by side, it reads as a chapel on a peasant house,
        # so it is reserved for civic buildings below.
        "wall_window": [
            _tile(name="Village Roof Side Wall With Window 01", **_MED),
            _tile(name="castle wall 1x1 window", **_MED),
            _tile(group=("wall", "Wall"), tags=("window",),
                  exclude_tags=_OFF_THEME + ("roof",), exclude=_WRONG_SETTING,
                  height=2.0, **_MED, **_WALLSZ),
        ],
        # **A partition comes from the wall's own kit too.** This was the last
        # place `md_wall_1x1_02` survived, and it is MegaDungeon masonry: the
        # same deep-relief blocks that were taken off the facade for leaving a
        # jumbled seam at every join. Nothing showed it until an interior was
        # built and looked at, because a town's partitions are inside a closed
        # shell with a roof on -- read from above at the first scene, the four
        # rooms of a tavern were a heap of pale rubble filling the floor, in a
        # cream timber-framed building.
        #
        # `Village Roof Side Wall 01` is the facade's own panel (the exterior
        # is `02`), same kit, same 1.0x2.0x0.5 footprint -- which the wall
        # segment check requires, and which the alternative from this kit,
        # `Tavern Wall - Small 01`, does not have: it is authored 0.5x2.0x1.0,
        # thin on the other axis. `place_wall` handles either, `validate` does
        # not, and teaching it that is a bigger change than this needs.
        "wall_interior": [
            _tile(name="Village Roof Side Wall 01", **_MED),
            _tile(name=("Wall (Plain, Small)", "md_wall_1x1_02"), **_MED),
            _tile(group=("wall", "Wall"), tags=("wall",), exclude_tags=_NOT_PLAIN_WALL,
                  exclude=_WRONG_SETTING, height=2.0, **_MED, **_WALLSZ),
        ],
        # Doors carry no GroupTag in this pack, so they can only be found by
        # name. Cap the height so the 4-tall double gate doors stay out.
        # A door replaces one wall segment, so it must have the wall's
        # footprint. A 2-wide door on a 1-wide cell overhangs its neighbours
        # by half a tile and drags the whole board off the grid.
        "door": [
            _tile(name="Door -Peasant", **_MED),
            _tile("door", tags=("wood",), exclude_tags=_OFF_THEME,
                  exclude=("tall", "double", "gate") + _WRONG_SETTING, max_height=2.5,
                  **_MED, **_WALLSZ),
        ],
        #: Flat ridge cap. The Village kit had no such piece, which is why our
        #: roofs showed an open trough; the Thatched kit does.
        "roof": [
            _tile(name="Thatched roof flat 01", **_MED),
            _tile(name="Village Roof Side 01", **_MED),
            _tile(group="roof", exclude_tags=_OFF_THEME,
                  exclude=("chimney", "end", "corner") + _WRONG_SETTING,
                  **_MED, **_UNIT),
        ],
        "stairs": [
            _tile(group=("stairs", "stair"), tags=("wood",), exclude_tags=_OFF_THEME,
                  exclude=("ramp", "railing") + _WRONG_SETTING, **_MED),
            _tile(group=("stairs", "stair"), exclude_tags=_OFF_THEME,
                  exclude=("ramp",) + _WRONG_SETTING, **_MED),
        ],
        # -- landscape ---------------------------------------------------
        # Terrain is pinned by name. It is the largest thing on the board by
        # far -- tens of thousands of tiles -- so getting it wrong is not a
        # detail, it is the entire first impression of the map.
        "street": [
            _tile(name="CobbleStone Floor Small", **_MED),
            _tile(group="floor", any_tags=("cobblestone", "cobble", "pavement"),
                  exclude_tags=_OFF_THEME, exclude=_TRIM_NAME_EXCLUDE + _WRONG_SETTING,
                  **_MED, **_UNIT),
        ],
        "ground": [
            _tile(name="Grass 1x1", **_MED),
            _tile(group="grassland", exclude=_TRIM_NAME_EXCLUDE + ("road",),
                  **_MED, **_FLOOR_H, **_UNIT),
        ],
        #: Where a character stands when the scene opens. It has to read as
        #: deliberate from directly overhead and be exactly one cell, because
        #: it *replaces* the floor tile under it rather than sitting on it --
        #: two coplanar surfaces in one cell is the seam that shifts with the
        #: camera. There is no marker asset in this pack (no rune, no circle,
        #: no chalk -- all searched), so it is a contrasting floor: a square of
        #: carpet on boards. The Moorgoth kit is normally excluded by
        #: ``_WRONG_SETTING`` and is pinned by name here on purpose, because
        #: what is wanted is precisely a tile that does not match the room.
        "party_mark": [
            _tile(name="Moorgoth Floor - Carpet Centre", **_MED, **_UNIT),
            _tile(name="CobbleStone Floor Small", **_MED, **_UNIT),
        ],
        #: Where an NPC stands, one role per duty so the three read apart at a
        #: glance. Same device as ``party_mark`` and for the same reason -- a
        #: v2 slab carries no creatures, so a position is a tile you drop a
        #: mini onto -- and pinned by name for the same reason too: the
        #: undeclared-role fallback is a bare name search across the whole
        #: catalog, and "npc_guard_mark" would resolve to nothing useful.
        #: Deliberately *not* the party's own carpet: a player has to be able
        #: to tell where their character starts from where the town watch is.
        #: **A mark has to contrast with what it is standing on, and the three
        #: duties stand on different things.** A guard is posted on paving --
        #: a gate passage, a main street -- so a grey stone mark would be grey
        #: on grey and invisible, which is what the first pick was; it gets
        #: timber. The other two stand on grass, earth or a gravel yard, so
        #: they get stone and a dark carpet. Verified on grass only so far:
        #: `npc-mark-contrast` in tasks.json is the probe that settles paving,
        #: and until it runs this is a reasoned pick rather than a measured
        #: one.
        "npc_guard_mark": [
            _tile(name="Rural Floor 02", **_MED, **_UNIT),
            _tile(name="Tavern Floor 01", **_MED, **_UNIT),
        ],
        "npc_work_mark": [
            _tile(name="castle floor 1x1", **_MED, **_UNIT),
            _tile(name="Castle Ruins floor stone 1x1", **_MED, **_UNIT),
        ],
        "npc_idle_mark": [
            _tile(name="Moorgoth Floor - Carpet Centre", **_MED, **_UNIT),
            _tile(name="Tavern Floor 01", **_MED, **_UNIT),
        ],
        #: 2x2 twin of ``ground``. Open country is tiled with these first and
        #: only edged in 1x1, which cuts the tile count for a field of grass by
        #: about four -- the difference between a village fitting the board
        #: comfortably and a town crowding the 1M asset cap.
        "ground_2x2": [_tile(name="Grass - Lush", **_MED)],
        "field": [
            _tile(name="Tilled Earth", **_MED),
            _tile(name="Grass - Sparse", **_MED),
        ],
        "water": [
            _tile(name="tempWater1x1", **_MED),
        ],
        #: What the river runs over. TaleSpire's water is translucent, so the
        #: bed is not hidden -- it is the thing you actually look at, and the
        #: only thing that gives the water a colour. Laying it in the ``ground``
        #: role put a lawn under the river and the board read as two sheets of
        #: turf with a blue film between them, which is what "a second layer of
        #: land" turned out to mean.
        #:
        #: Which bed reads as *water* is a rendering question, so
        #: ``tools/water_probe.py`` asked the game: six candidate beds under
        #: one to four tiles of water, side by side. Bright beds -- sand,
        #: desert stone -- barely tint at all and the river reads as a dry
        #: wash. A grey stony bed goes deep teal by two tiles down and holds
        #: the shallows pale, so the depth of the channel is legible from
        #: above. That is the whole reason the bed steps down away from the
        #: bank, and it was invisible until the bed stopped being bright.
        "riverbed": [
            _tile(name="Cave Floor - Rock 2", **_MED, **_UNIT),
            _tile(name="gravel_1x1_01", **_MED),
        ],
        "riverbed_2x2": [
            _tile(name="Cave Floor - Rock 01", size=(2.0, 2.0), **_MED),
        ],
        #: 1x1 stand-in for the 2x2 ``field`` block, for leftover cells the
        #: block pass cannot cover. Gravel reads as worked dirt at this scale;
        #: plain grass is the fallback of last resort.
        #: Broadleaf to stand beside the conifer. A forest of one species reads
        #: as a plantation; the pack has exactly one other single-piece tree
        #: that is neither jungle nor palm.
        # A conifer is a *kit*, not an asset: Stump -> Middle -> Top, and the
        # Top on its own is a 2.42-tall canopy cone. Planted alone it is a
        # bush sitting on the grass with no trunk under it, which is what 577
        # of these looked like on the board -- while stumps scattered as
        # separate felled-tree dressing read as trunks that did not line up
        # with any leaves. ``build._plant_conifer`` stacks them.
        "tree_conifer_trunk": [_tile(name="Stackable Pine Stump", kind="prop", **_MED)],
        "tree_conifer_mid": [_tile(name="Stackable Pine Middle 04", kind="prop", **_MED)],
        "tree_conifer_crown": [_tile(name="Stackable Pine Top", kind="prop", **_MED)],
        "tree_broadleaf": [_tile(name="Tree 01", kind="prop", **_MED)],
        "tree_dead": [
            _tile(name="Dead Tree 03", kind="prop", **_MED),
            _tile(name="Dead Tree 02", kind="prop", **_MED),
        ],
        "field_1x1": [
            _tile(name="gravel_1x1_01", **_MED),
            _tile(name="Grass 1x1", **_MED),
        ],
        # -- pitched roof kit --------------------------------------------
        # A flat plane of roof tiles reads as a warehouse, not a village. These
        # are the pieces of a real gabled roof; ``build`` picks per cell from
        # its position in the footprint.
        "roof_side": [_tile(name="Thatched Roof 01", **_MED)],
        "roof_corner": [_tile(name="Thatched Roof Corner 01", **_MED)],
        # -- the other three roof materials -------------------------------
        # Every one of these was on the shelf the whole time. `_lay_roofs`
        # stacks a hip using rotations read out of one community-built
        # cottage, and those are *the Thatched kit's*; dropped onto another
        # kit's pieces they produce a rank of fins, which read as "this kit
        # has no hip pieces". It does. `tools/roofrot_probe.py --hips` lays
        # the same hip four times, once per quarter turn, and exactly one
        # closes -- see `build.ROOF_ROT_OFFSET` for the measured table.
        #
        # Terracotta tile, the Tavern kit's own -- so a house whose walls are
        # `Village Roof Side Wall 0x` can be roofed in its own family.
        "roof_side_tile": [_tile(name="Village Roof Side 01", **_MED, **_UNIT)],
        "roof_corner_tile": [_tile(name="Village Roof Corner 01", **_MED, **_UNIT)],
        "roof_corner_inner_tile": [
            _tile(name="Village Roof Inner Corner 01", **_MED, **_UNIT)],
        "roof_tile": [_tile(name="Tavern Roof flat 01", **_MED, **_UNIT)],
        "roof_chimney_tile": [_tile(name="Village Roof Side/Chimney", **_MED, **_UNIT)],
        # Grey slate. Reads as the dearest roof on the board, which is why it
        # goes on the civic tier: slate on dressed stone is a temple or a
        # guildhall, and thatch on dressed stone says nothing at all.
        "roof_side_slate": [_tile(name="Haunted roof 1x1", **_MED, **_UNIT)],
        "roof_corner_slate": [
            _tile(name="haunted roof corner out tip", **_MED, **_UNIT)],
        "roof_corner_inner_slate": [
            _tile(name="haunted roof corner inner tip", **_MED, **_UNIT)],
        "roof_slate": [_tile(name="haunted roof 1x1 flat", **_MED, **_UNIT)],
        #: The Abandoned Village kit ships no 1x1 chimney; the thatched one is
        #: a chimney rather than a roof surface, so it reads fine on slate.
        "roof_chimney_slate": [_tile(name="Thatched Chimney", **_MED, **_UNIT)],
        # A reflex corner is not an outside corner turned round -- it is its
        # own piece, and the kit ships one. Half of all corner cells on this
        # map are reflex (223 of 467), because an L-shaped plan has one at its
        # elbow and rev 18 cut twenty of them. Building those with the outside
        # piece is what made the roofscape look jumbled.
        "roof_corner_inner": [_tile(name="Thatched Roof Inner Corner 01", **_MED)],
        "roof_chimney": [_tile(name="Thatched Chimney", **_MED)],
        # -- corners ------------------------------------------------------
        # An outside corner is one full-cell piece, not two wall segments
        # meeting. Calling place_wall() for both exposed sides put two wall
        # ends in the same square on 192 of our cells; a decoded hand-built
        # community cottage does it zero times out of sixteen, because human
        # builders reach for these instead.
        #
        # Pinned by name *and* shape: "md_wall_corner_1x1_01" exists twice --
        # once in group Corner at 2.0 tall, once in Combinations at 2.5. The
        # 2.5 variant is the wrong height for the wall course and would lift
        # every storey above it out of line.
        # **The facade's own corner, and it took an index to find it.** The
        # wall is `Village Roof Side Wall 01/02` and the corner was
        # `Rural Corner` -- cream timber framing meeting dark horizontal
        # boarding at every corner of every house. Hunting a corner named
        # "Village *" found nothing, because the kit is not in the name: the
        # catalog's `folder` is the family, and those panels live in folder
        # **Tavern**, which ships `Tavern no floor (1x1 a)` -- 1x1, two tiles
        # tall, group `corner`, exactly the piece. Its name is the same one
        # CLAUDE.md warns about for matching "floor" loosely; as a corner it
        # is the right piece. `tools/kit_index.py` is the lookup now.
        # The wall has the same reflex problem as the roof; this is the piece
        # for it. Thin, because unlike an outside corner it does not fill a
        # cell -- it tucks into the angle.
        "wall_corner_inner": [
            _tile(name="Tavern Inner Corner 2", **_MED),
            _tile(name="Rural Inner Corner", **_MED),
        ],
        "wall_corner": [
            _tile(name="Tavern no floor (1x1 a)", height=2.0, **_MED, **_UNIT),
            _tile(name="Rural Corner", height=2.0, **_MED, **_UNIT),
            _tile(name=("md_wall_corner_1x1_01", "md_wall_corner_1x1_02"),
                  group="Corner", height=2.0, **_MED, **_UNIT),
            _tile(name="bg_wall_1x1_corner_01", height=2.0, **_MED, **_UNIT),
        ],
        # -- utility fabric -----------------------------------------------
        # A barn is not a small house. Rural ships a wall *and* a matching 1x1
        # corner and **no 1-cell window at all**, which reads as a limitation
        # until you ask what a warehouse or a stable actually looks like: dark
        # horizontal boarding, one storey, no glass. Probed as its own design
        # in `tools/facade_probe.py` (candidate 3) it is the clearest tier of
        # the four -- a different building at a glance, from any angle.
        #
        # This is also why the facade is *not* Rural: a house needs a window
        # and only two 1-cell windows exist in the whole Medieval Fantasy pack
        # (this kit has neither). The missing window is what makes Rural the
        # right kit here and the wrong kit everywhere else.
        "wall_utility": [
            _tile(name="Rural Wall 01", height=2.0, **_MED),
            _tile(name=("Village Roof Side Wall 01", "Village Roof Side Wall 02"),
                  **_MED),
        ],
        "wall_corner_utility": [
            _tile(name="Rural Corner", height=2.0, **_MED, **_UNIT),
            _tile(name="Tavern no floor (1x1 a)", height=2.0, **_MED, **_UNIT),
        ],
        # -- civic fabric -------------------------------------------------
        # Guildhall, temple, manor and barracks are built in dressed stone
        # with arched openings, so a party can read importance off the
        # architecture from a street away instead of counting storeys.
        #: Dressed stone paving, from the civic wall's own kit. A temple built
        #: in `castle wall 1x1` was standing on `Rural Floor 01` -- timber
        #: planking under coursed masonry, which is the kit mismatch this
        #: project keeps relearning, one surface lower down.
        "floor_civic": [
            _tile(name="castle floor 1x1", **_MED, **_FLOOR_H, **_UNIT),
            _tile(name="Moorgoth Floor 01", **_MED, **_FLOOR_H, **_UNIT),
        ],
        "wall_civic": [
            _tile(name="castle wall 1x1", **_MED),
        ],
        "wall_window_civic": [
            _tile(name="castle wall 1x1 window", **_MED),
        ],
        #: The castle kit's own corner, so a civic building turns the corner in
        #: the same dressed stone its walls are built from.
        "wall_corner_civic": [
            _tile(name="castle wall corner 1x1 base", height=2.0, **_MED, **_UNIT),
            _tile(name="Castle Ruins Wall Corner", height=2.0, **_MED, **_UNIT),
        ],
        "door_civic": [
            _tile(name="Door - Fancy", **_MED),
            _tile(name="Door -Peasant", **_MED),
        ],
        #: The flight up onto the wall-walk. Same kit as the walk paving and
        #: the crenellations (`Castle Ruins *`), so the rampart's dressing is
        #: one family even though its mass is another. The generic `stairs`
        #: role is no use here: it resolves to `Rural Stairs Flat`, 0.25 tall,
        #: which would take twenty-eight treads to climb a 35 ft wall.
        "city_wall_stair": [
            _tile(name="Castle Ruins Stair", height=1.0, **_MED, **_UNIT),
            _tile(name="md_stairs_01", height=1.0, **_MED, **_UNIT),
        ],
        "city_wall": [
            _tile(name="castle wall 1x1", **_MED),
            _tile(group="wall", tags=("stone", "wall"), exclude_tags=_NOT_PLAIN_WALL,
                  height=2.0, exclude=_WRONG_SETTING, **_MED, **_WALLSZ),
        ],
        # The rampart's mass. The castle kit's wall pieces are 0.5 deep --
        # they are *curtain wall*, authored to stand on a cell boundary, not
        # to fill a cell. Laying one per cell across a rampart four cells
        # thick left a 0.5-tile slot between every course: 2.5 ft of daylight
        # through the wall, the length of the whole circuit. The mass is built
        # from a full-cell block and the thin pieces face it.
        # Open water tiles perfectly and is the biggest single surface on a
        # river map, so the 2x2 pays for itself four cells at a time. Both
        # water assets in the pack are named "temp"; that is TaleSpire's
        # naming, not a placeholder -- there is nothing else to pin.
        "water_2x2": [_tile(name="tempWater2x2", **_MED)],
        # Where paving meets water there is no shingle to lay -- a road is not
        # going to become gravel -- so the bank was simply a cliff the cobbles
        # stopped at. A low harbour rail turns that into a quay edge. Piers
        # deliberately do not get one: a pier exists to reach the water.
        # Hung beside the door of a trade building. A street of 28 identical
        # cottages tells a party nothing; a sign says which door is the inn.
        # One query listing several names, not one query per name: ``resolve``
        # takes the *first* query that matches anything and then picks within
        # it, so a query per name makes every variant resolve to the same
        # asset. All six of this role's signs came out identical that way.
        #
        # Every "Market Sign" in the catalog is Cyberpunk pack -- the ``_MED``
        # filter is what stops a medieval village hanging neon over its inns.
        #: Hung beside the door of a building too low for a porch. A hood
        #: seats at `storey_h + 0.5`, so on a one-storey cottage it lands at
        #: its own eaves and reads as a second roof grafted on; a lantern says
        #: the same thing -- someone lives here, knock -- at cottage scale.
        "door_lantern": [
            _tile(name="Lantern on hook 01", kind="prop", **_MED),
            _tile(name="Lantern -Small", kind="prop", **_MED),
        ],
        "shop_sign": [
            _prop(name=("Sign (Generic) 01", "Sign (Generic) 02",
                        "Sign (Village) 01", "Sign (Village) 02",
                        "Sign (Village) 03", "Spike sign"), **_MED),
        ],
        # Back lanes between houses: worn ground, not paving. Gravel doubles
        # as the shingle shore, but the two never meet -- one is between
        # buildings, the other is against water.
        "lane": [
            _tile(name="gravel_1x1_01", **_MED),
            _tile(name="Dirt 1x1", **_MED),
        ],
        # -- dressing by place ---------------------------------------------
        # Market stalls and their goods, for the square.
        "market_goods": [
            _prop(name=("Crate - Large", "Baskets", "Apple Basket",
                        "Barrels", "Sack", "Bench -Shabby"), **_MED),
        ],
        # A yard is a working back-of-house, not a park: firewood, a barrow,
        # something being mended. Cut from the same corner the notch opened.
        "yard_clutter": [
            _prop(name=("Log Pile", "Stackable Pine chopped", "Wooden Cart",
                        "Ladder wood short 01", "bucket_wood", "Barrels"), **_MED),
        ],
        # Low growth for softening a hard tile seam. Nothing here is taller
        # than knee height -- a seam wants breaking up, not hiding.
        "verge": [
            _prop(name=("Rock pebbles 01", "bush_berry_01", "Fern 01",
                        "Rubble pebbles spread out"), **_MED),
        ],
        # Hedgerow along a field boundary: enclosure, and cover a scout can
        # crawl behind. Low enough not to wall the field off visually.
        "hedge": [
            _prop(name=("bush_reg_01", "bush_wild_01", "bush_berry_01",
                        "Bush - Medium", "bush_thorns_01"), **_MED),
        ],
        # Field boundaries, laid along their true bearing by `_lay_fences`
        # rather than stroked into cells. Every piece here is 2.0 tiles long,
        # which is the module the run steps at -- swapping a style is a change
        # of asset, never a change of geometry.
        #
        # **The kit is `Fences`** (Medieval Fantasy), and everything in it is a
        # *prop*, so these store a collider centre and go down through
        # `place_centered`, not `place_tile`.
        #
        # Careful with `Harbor Fence 02`: the name exists TWICE, as a `Harbor`
        # tile (0.5 x 0.5 x 1.0) and as a `Fences` prop (0.98 x 0.48 x 0.20).
        # `quay_rail` below pins the tile. A `_prop` query for that name here
        # would silently take the other one.
        "field_wall": [
            _prop(name=("Stone Wall 01", "Stone fence 02"), **_MED),
        ],
        # 1.39 tall against the field wall's 1.00, and half as thick again:
        # an estate or churchyard wall rather than a boundary between fields.
        "field_wall_tall": [
            _prop(name=("Stone Wall 02", "Stone Wall 01"), **_MED),
        ],
        # The joint. 0.51 square and 1.02 tall -- a hair proud of the wall it
        # ends, which is what a gate post does. 72% of fence vertices turn less
        # than 20 degrees (`docs/fencing.md` §2.3), so a post is the piece that
        # fits the whole distribution; the kit's corner pieces are authored at
        # 90 degrees and suit one vertex in eight.
        "field_wall_post": [
            _prop(name=("Stone fence 01", "Stone Wall 01"), **_MED),
        ],
        # Timber paling at 3.5 ft: a paddock or a yard boundary, not a field.
        "yard_fence": [
            _prop(name=("Wooden Fence",), **_MED),
        ],
        # The same kit's corner, for the one vertex in eight that really does
        # turn a right angle. Authored as an L, so it is only ever placed where
        # the turn is hard enough to fill.
        "yard_fence_corner": [
            _prop(name=("Wooden Fence Corner",), **_MED),
        ],
        # A living boundary on the same 2.0-tile module as the walls, so one
        # run-the-line pass serves both. `Nature` kit rather than `Fences`,
        # which is the one deliberate kit crossing here -- a hedge is not
        # masonry and is not trying to match it.
        "field_hedge": [
            _prop(name=("hedge_piece_01",), **_MED),
        ],
        # Outdoor surfaces beyond the three the map used to have. Every pick
        # here was read off a board (`tools/surface_probe.py` under
        # `review.ps1 360`), because nothing in the catalog says what a tile
        # looks like and this project has three findings that end that way.
        #
        # The raster distinguishes six surfaces and classes every road
        # main/cart/lane; all of it used to arrive as cobble, gravel, grass --
        # with `lane`, `gravel` and `field_1x1` resolving to the *same* asset
        # and `plaza` resolving to nothing at all. See `docs/district-surfaces.md`.

        # A cart street: grey-brown coursed flag, weathered. Humbler than the
        # through road's setts, which is the distinction the class already
        # makes and the board never showed.
        "street_cart": [
            _tile(name="Castle Ruins floor stone 1x1", **_MED),
            _tile(name="Castle Ruins Stone Floor 2", **_MED),
        ],
        # A market square or a temple forecourt: cream regular flagstone, and
        # visibly *dressed* rather than laid. This is the one surface a town
        # spends money on, and 631 cells of East Tradebourne were paved as road.
        "plaza": [
            _tile(name="castle floor 1x1", **_MED),
            _tile(name="Castle Ruins floor stone 1x1", **_MED),
        ],
        # A back lane: dark wet brown, and it is not a road surface at all --
        # it is what a way looks like when nobody has paved it. Reads as earth
        # trodden to mud, which is what a medieval lane was.
        "lane_earth": [
            _tile(name="Swamp floor 1x1", **_MED),
            _tile(name="gravel_1x1_01", **_MED),
        ],
        # The ragged edge of a ploughed field: pale, dry, wind-scoured stubble.
        # Its own tile at last -- it used to share the lane's gravel, so a field
        # edge and a track were built from one asset.
        "field_edge": [
            _tile(name="Desert Ground Dry 01", **_MED),
            _tile(name="gravel_1x1_01", **_MED),
        ],
        # A craft yard: bright orange coarse pebbles. `gravel_1x1_01` is much
        # louder than its name suggests, which is wrong for a lane and exactly
        # right for hard standing outside a forge.
        "yard_gravel": [
            _tile(name="gravel_1x1_01", **_MED),
        ],
        "quay_rail": [
            _tile(name="Harbor Fence 02", **_MED),
            _tile(name="Desert fence low", **_MED),
        ],
        # The deck of a plank bridge: a harbour tile a whole tile thick, laid
        # by its top so the planking meets the bank flush and the underside
        # rests on the water a tile below. `tools/tower_probe.py` put six
        # candidates across one channel -- three harbour decks, a thin floor
        # on dock legs, a 2x1 plank and a stone causeway -- and the harbour
        # decks were the ones that read as a timber pier from every angle,
        # with their own beams showing down to the waterline; the thin floor
        # floated, and the causeway read as a fortification.
        "bridge_deck": [
            _tile(name="Harbor Middle 06", **_MED),
            _tile(name="Harbor Float 01", **_MED),
            _tile(name="Harbor Extention 02", **_MED),
        ],
        # Rope on posts, the same piece as the quay, so a bridge and the
        # quay it leaves from are one piece of carpentry.
        "bridge_rail": [
            _tile(name="Harbor Fence 02", **_MED),
            _tile(name="Desert fence low", **_MED),
        ],
        # The rampart's mass, one block per cell.
        #
        # **A full-cell collider does not mean a full-cell mesh.** The previous
        # pick, `md_wall_1x1_diag_01`, measures 1.0 x 2.0 x 1.0 and is a thin
        # blade cutting the cell corner to corner -- the "diag" in its name is
        # the whole story. It won a probe of six candidates laid as flat 3x3x2
        # masses and read from the front, where a rank of blades hides its own
        # gaps. On a circuit, seen from anywhere else, it is a comb: a vertical
        # slot of daylight between every pair of cells, the length of the wall.
        # `Tall 1x1x2` fails the same way for the same reason.
        #
        # `tools/wall_probe.py` builds each candidate as a straight run *and*
        # as the stair-stepped diagonal a raster circuit actually is, and it is
        # read from overhead as well as from the side. Of the six:
        # `md_pref_wall_1x1_01` tiles as separate posts with gaps between them;
        # `castle wall corner 1x1 base` is a corner mesh whose two-faced relief
        # reads as stacked crates; `md_stairblock_01` is solid but coarse.
        # This one is solid, finely coursed, closes on the stair as cleanly as
        # on the straight, and shares its tone with the ruins floor that paves
        # the wall-walk on top of it.
        # Third pick, and the first one reviewed from all four sides.
        # `Castle Ruins Wallbase 02` is *ruined* masonry -- the whole kit is
        # broken wall by design -- and it read solid from overhead and from one
        # oblique because at those angles its own front face covers its holes.
        # Tiled into a town it made a lattice of piers and lintels you could see
        # straight through. A stair block is a plain solid cube: no relief to
        # hide a hole in, nothing directional to line up, and it stays shut from
        # every angle and in a run one cell thick, which is the harshest shape
        # `tools/wall_probe.py` builds.
        "city_wall_core": [
            _tile(name="md_stairblock_01", **_MED, **_UNIT),
            _tile(name="md_stairblock_02", **_MED, **_UNIT),
            _tile(name="bg_stairblock_01", **_MED, **_UNIT),
        ],
        # Battlements, on the outer ring only, so the middle of the rampart
        # stays walkable as a wall-walk.
        #
        # The previous pin, `castle merlon 1x1`, was chosen off its group tag
        # ("merlon") and never looked at. It is a *hoarding* -- the boarded
        # timber gallery a garrison hangs off a wall in a siege -- and the
        # whole `castle merlon` family is the same timber. Laid one per cell it
        # crowned the circuit with a row of separate wooden crates, each
        # cantilevered over the corner of the step below it.
        #
        # A parapet is a thin thing standing on the *lip* of the wall with room
        # to walk behind it, which is why this is a 0.5-deep curtain piece and
        # `_lay_town_wall` puts it on the outward edge rather than filling the
        # cell. Same kit as the rampart block and the wall-walk paving, so the
        # three read as one structure. See `tools/parapet_probe.py`.
        "city_wall_cap": [
            _tile(name="Castle Ruins Crenellation - Small", **_MED),
            _tile(name="Castle Ruins Crenellation - Large", **_MED),
        ],
        # The wall-walk behind the parapet, so the top of the rampart is a
        # surface a party can be fought along rather than the bare top face of
        # the blocks that make up its mass.
        #
        # Weathered stone, not dressed floor. Six candidates were pasted side
        # by side on wall blocks and looked at: "castle floor 1x1" is a clean
        # interior flag that reads as a bright ribbon laid along the rampart,
        # and "castle floor 1x1 edge" is brighter still. The ruins floors sit
        # in the same tonal range as the wall they cap.
        "city_wall_walk": [
            _tile(name="Castle Ruins floor stone 1x1", **_MED),
            _tile(name="Castle Ruins Floor - Small", **_MED),
            _tile(name="castle floor 1x1", **_MED),
        ],
        # -- gatehouse -----------------------------------------------------
        # A gate used to be an unbuilt hole: the cells were skipped and
        # nothing took their place, leaving a 35 ft breach through a rampart.
        # The portcullis is 4 tiles wide, which is also the main-street width,
        # so a gate spans one carriageway.
        "city_gate": [
            _tile(name="Door - Portcullis double", **_MED),
            _tile(name="Door - Portcullis", **_MED),
            _tile(name="Door - Metal Gate double", **_MED),
        ],
        "city_gate_arch": [_tile(name="Castle Ruins Arch 02", **_MED)],
    },
    props={
        "tavern": [_prop("table", **_MED), _prop("chair", **_MED), _prop("barrel", **_MED),
                   _prop("tankard", **_MED), _prop("food", **_MED)],
        "shop": [_prop("crate", **_MED), _prop("barrel", **_MED), _prop("sack", **_MED),
                 _prop("shelf", **_MED)],
        "smithy": [_prop("anvil", **_MED), _prop("forge", **_MED), _prop("weapon", **_MED),
                   _prop("barrel", **_MED)],
        #: **There is no benign altar in this pack, and that is a fact about
        #: the library rather than a gap in the query.** The catalog holds
        #: exactly one asset matching "altar" -- `Altar (Evil)`, in the *Grim*
        #: folder -- so the temple's altar slot could only ever have resolved
        #: to an evil altar, and did, in every chapel on every map. Same shape
        #: as "there is no Village corner to find": the answer is not a looser
        #: query, it is to build the thing out of what does exist. A dressed
        #: table under candles and a statue reads as a shrine; an evil altar
        #: reads as a plot the GM did not ask for.
        "temple": [_prop("candle", **_MED), _prop("table", **_MED),
                   _prop("statue", **_MED), _prop("brazier", **_MED)],
        "house": [_prop("bed", **_MED), _prop("table", **_MED), _prop("chair", **_MED),
                  _prop("chest", **_MED)],
        "warehouse": [_prop("crate", **_MED), _prop("barrel", **_MED), _prop("sack", **_MED),
                      _prop("rope", **_MED)],
        "street": [_prop("cart", **_MED), _prop("barrel", **_MED), _prop("crate", **_MED),
                   _prop("lantern", **_MED)],
        "nature": [_prop("tree", **_MED), _prop("plant", **_MED), _prop("rock", **_MED)],
        "light": [_prop("lantern", **_MED), _prop("torch", **_MED), _prop("candle", **_MED)],
    },
)


#: Restrict the cyberpunk style to the sci-fi pack so medieval assets cannot
#: satisfy a query through an incidental shared tag.
_SCIFI = {"pack": "Cyberpunk and Sci-fi"}

CYBERPUNK = Style(
    name="cyberpunk",
    description="Neon-lit modern block -- concrete, steel and signage.",
    roles={
        "floor": [
            _tile(group="floor", any_tags=("concrete", "metal", "modern"),
                  exclude=("stair", "ramp"), **_SCIFI, **_FLOOR_H, **_UNIT),
            _tile(group="floor", exclude=("stair", "ramp"), **_SCIFI, **_FLOOR_H, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "floor_upper": [
            _tile(group="floor", exclude=("stair", "ramp"), **_SCIFI, **_FLOOR_H, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "wall": [
            _tile(group="wall", any_tags=("concrete", "metal"),
                  exclude_tags=("window", "door"), exclude=_WALL_NAME_EXCLUDE,
                  height=2.0, **_SCIFI, **_WALLSZ),
            _tile(group="wall", exclude_tags=("window", "door"),
                  exclude=_WALL_NAME_EXCLUDE, height=2.0, **_SCIFI, **_WALLSZ),
            _tile(group="wall", **_SCIFI, **_WALLSZ),
        ],
        "wall_window": [
            _tile(group="wall", tags=("window",), **_SCIFI, **_WALLSZ),
            _tile(group="wall", height=2.0, **_SCIFI, **_WALLSZ),
        ],
        "wall_interior": [
            _tile(group="wall", height=2.0, **_SCIFI, **_WALLSZ),
            _tile(group="wall", **_SCIFI, **_WALLSZ),
        ],
        # Declared, not left to the undeclared-role fallback: a bare search for
        # "wall_corner" matches ``md_wall_corner_1x1_01`` too, and that is a
        # medieval stone corner the right size to pass every shape check and
        # land on a cyberpunk block. Both of these are pieces whose rotation
        # convention was measured directly out of
        # ``library/residence/tiny-modern-city-pack-5.slab``.
        "wall_corner": [
            _tile(name=("wall corner bulk", "tech wall corner full"),
                  height=2.0, **_SCIFI, **_UNIT),
            _tile(group="corner", exclude=("inner", "curved", "slim", "filler"),
                  height=2.0, **_SCIFI, **_UNIT),
        ],
        "door": [
            _tile(group=("door", "hatch"), **_SCIFI, **_WALLSZ),
            _tile("door", kind="tile", **_SCIFI, **_WALLSZ),
            _tile(group=("door", "hatch"), **_SCIFI),
        ],
        "roof": [
            _tile(group="roof", **_SCIFI, **_UNIT),
            _tile(group="roof", **_SCIFI),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "stairs": [
            _tile(group=("stair", "stairs"), **_SCIFI),
        ],
        "street": [
            _tile(group="sidewalk", **_SCIFI),
            _tile(group="floor", any_tags=("concrete", "asphalt", "street"), **_SCIFI, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "ground": [
            _tile(name="concrete floor 1x1", **_SCIFI),
            _tile(group="floor", any_tags=("concrete",), **_SCIFI, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        #: Declared for the same reason the landscape roles below are: the
        #: undeclared-role fallback is a bare name search with no pack filter,
        #: and "party_mark" would find whatever happens to be named like it.
        #: A lit grate reads as a spot to stand on under a sci-fi floor.
        "party_mark": [
            _tile(group="floor", any_tags=("metal", "grate"), **_SCIFI, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        #: Same three duties as the medieval set; declared here for the same
        #: reason -- so the undeclared-role fallback never gets a chance to
        #: search the whole catalog by name.
        "npc_guard_mark": [
            _tile(group="floor", any_tags=("metal",), **_SCIFI, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "npc_work_mark": [
            _tile(group="floor", any_tags=("concrete",), **_SCIFI, **_UNIT),
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        "npc_idle_mark": [
            _tile(group="floor", **_SCIFI, **_UNIT),
        ],
        # -- landscape ----------------------------------------------------
        # These are *declared*, not left to the undeclared-role fallback. That
        # fallback is a bare name search across the whole catalog with no pack
        # filter, and on this install it resolves "water" to the medieval
        # rowing boat "boat small v1 mid paddles" (2.37x2.0) and "park" to
        # "balloon_cart_01" -- the exact failure asset-conventions.md records
        # for the medieval style. `field_1x1` resolved to nothing at all,
        # which made `--style cyberpunk` abort outright: _lay_terrain's 1x1
        # pass calls require() for it on every leftover field cell.
        #
        # 2x2 twin of ``ground``, for the block pass in ``_lay_terrain``.
        "ground_2x2": [
            _tile(name="durable floor 2x2", **_SCIFI),
            _tile(name="concrete floor 2x2 strip", **_SCIFI),
        ],
        #: MFCG farmland has no cyberpunk equivalent; a rusted industrial deck
        #: reads as the open lot between blocks, which is what that space is
        #: doing on this kind of map.
        "field": [
            _tile(name=("industrial_floor_2x2_01", "industrial_floor_2x2_02"), **_SCIFI),
        ],
        "field_1x1": [
            _tile(name="industrial_floor_1x1_01", **_SCIFI),
            _tile(name="concrete floor 1x1", **_SCIFI),
        ],
        #: A canal here is a concrete channel, not a river. Same reasoning as
        #: the medieval bed: the water is translucent, so whatever is under it
        #: is on show, and the ground role would put the pavement of the block
        #: beside it at the bottom of the canal.
        "riverbed": [
            _tile(name="industrial_floor_1x1_01", **_SCIFI),
            _tile(name="concrete floor 1x1", **_SCIFI),
        ],
        "riverbed_2x2": [
            _tile(name=("industrial_floor_2x2_01", "industrial_floor_2x2_02"), **_SCIFI),
        ],
        #: No water tile ships in the sci-fi pack, so this is a deliberate
        #: cross-pack pin rather than an accidental one. An explicit borrow
        #: beats the bare-name fallback dragging in a rowing boat.
        "water": [
            _tile(name="tempWater1x1", pack="Medieval Fantasy"),
        ],
        "gravel": [
            _tile(name=("concrete floor 1x1 cracked v1", "concrete floor 1x1 cracked v2",
                        "concrete floor 1x1 cracked v3"), **_SCIFI),
        ],
        "city_wall": [
            _tile(group="wall", any_tags=("concrete", "metal"), height=2.0, **_SCIFI),
            _tile(group="wall", **_SCIFI),
        ],
    },
    props={
        "tavern": [_prop("chair", **_SCIFI), _prop("kitchen", **_SCIFI),
                   _prop("console", **_SCIFI), _prop("trash", **_SCIFI)],
        "shop": [_prop("console", **_SCIFI), _prop("signage", **_SCIFI),
                 _prop("container", **_SCIFI)],
        "smithy": [_prop("technology", **_SCIFI), _prop("pipe", **_SCIFI),
                   _prop("container", **_SCIFI)],
        "temple": [_prop("console", **_SCIFI), _prop("light", **_SCIFI)],
        "house": [_prop("furniture", **_SCIFI), _prop("kitchen", **_SCIFI),
                  _prop("chair", **_SCIFI)],
        "warehouse": [_prop("container", **_SCIFI), _prop("crate", **_SCIFI),
                      _prop("pipe", **_SCIFI)],
        "street": [_prop("trash", **_SCIFI), _prop("signage", **_SCIFI),
                   _prop("fence", **_SCIFI)],
        "nature": [_prop("plant", **_SCIFI)],
        "light": [_prop("light", **_SCIFI), _prop("lamp", **_SCIFI)],
    },
)


STYLES: dict[str, Style] = {s.name: s for s in (MEDIEVAL, CYBERPUNK)}


class Palette:
    """Resolves a :class:`Style` against a :class:`Catalog`, with caching.

    Resolution is deterministic for a given seed: the same role asked for with
    the same variant key always returns the same asset, so a regenerated city
    is identical.
    """

    def __init__(self, catalog: Catalog, style: Style, seed: int = 0):
        self.catalog = catalog
        self.style = style
        self.seed = seed
        self._cache: dict[tuple[str, int], Asset | None] = {}

    @classmethod
    def named(cls, catalog: Catalog, style_name: str, seed: int = 0) -> "Palette":
        try:
            style = STYLES[style_name]
        except KeyError:
            raise CatalogError(
                f"Unknown style {style_name!r}. Available: {', '.join(sorted(STYLES))}"
            ) from None
        return cls(catalog, style, seed)

    def _candidates(self, role: str) -> list[Query]:
        queries = self.style.roles.get(role)
        if queries is None:
            queries = self.style.props.get(role)
        if queries is None:
            # Unknown role: fall back to a bare search on the role name.
            queries = [q(role)]
        return queries

    def resolve(self, role: str, variant: int = 0) -> Asset | None:
        """Resolve ``role`` to an asset. ``variant`` picks between equals."""
        key = (role, variant)
        if key in self._cache:
            return self._cache[key]

        rng = random.Random(f"{self.seed}:{self.style.name}:{role}:{variant}")
        chosen: Asset | None = None
        for terms, kwargs in self._candidates(role):
            matches = self.catalog.find(*terms, **kwargs)
            if matches:
                chosen = rng.choice(matches)
                break
        self._cache[key] = chosen
        return chosen

    def require(self, role: str, variant: int = 0) -> Asset:
        asset = self.resolve(role, variant)
        if asset is None:
            raise CatalogError(
                f"Style {self.style.name!r} could not resolve role {role!r} against "
                f"the installed asset packs ({', '.join(self.catalog.packs)}). "
                "You may be missing the asset pack this style expects."
            )
        return asset

    def prop(self, category: str, rng: random.Random) -> Asset | None:
        """Pick a random prop for a room category (varies per call)."""
        queries = self.style.props.get(category)
        if not queries:
            return None
        terms, kwargs = rng.choice(queries)
        matches = self.catalog.find(*terms, **kwargs)
        return rng.choice(matches) if matches else None

    def validate(self) -> list[str]:
        """Return a list of problems -- empty means the style is usable.

        This checks *shape*, not taste: nothing here can tell that a fishing
        net is a poor stand-in for water. It catches the failures that produce
        broken geometry -- a role that resolves to nothing, or to something the
        wrong size for the slot it is dropped into.
        """
        problems = []
        for role in REQUIRED_ROLES:
            if self.resolve(role) is None:
                problems.append(f"role {role!r} resolves to nothing")

        # A role the style *declares* but cannot resolve is a silent hole: the
        # builder skips those cells and the map comes out with gaps in it.
        for role in self.style.roles:
            if self.resolve(role) is None:
                problems.append(f"role {role!r} is declared but resolves to nothing")

        # Only roles the style actually declares are checked for size. An
        # undeclared role falls back to a bare name search, which is how
        # "water" once resolved to a rowing boat -- worth failing on when the
        # style claims to provide water, but not worth holding a style to a
        # role it never offered.
        for role in CELL_ROLES:
            if role not in self.style.roles:
                continue
            asset = self.resolve(role)
            if asset is not None and (asset.size_x, asset.size_z) != (1.0, 1.0):
                problems.append(
                    f"role {role!r} resolves to {asset.name!r} with footprint "
                    f"{asset.size_x}x{asset.size_z}, but it is laid one per cell "
                    "-- it must be 1.0x1.0"
                )

        for role in BLOCK_ROLES:
            if role not in self.style.roles:
                continue
            asset = self.resolve(role)
            if asset is not None and (asset.size_x, asset.size_z) != (2.0, 2.0):
                problems.append(
                    f"role {role!r} resolves to {asset.name!r} with footprint "
                    f"{asset.size_x}x{asset.size_z}, but it fills a 2x2 block "
                    "-- it must be 2.0x2.0"
                )

        # **Prop categories were never validated, so an empty one was silent.**
        # `Palette.prop` picks one query at random from the category's list and
        # returns None when it matches nothing -- so a dead query does not
        # fail, it just makes that category thinner every time it comes up, at
        # a rate nobody would notice. It went unnoticed here: tightening the
        # prop queries emptied `temple/altar` outright, and the only reason it
        # was caught is that somebody counted the matches by hand.
        for category, queries in sorted(self.style.props.items()):
            for terms, kwargs in queries:
                if not self.catalog.find(*terms, **kwargs):
                    problems.append(
                        f"prop category {category!r} has a query "
                        f"{'+'.join(terms) or '<no terms>'!r} that matches "
                        "nothing -- it will silently thin the category"
                    )

        # Roles placed into a wall segment must match the wall's footprint.
        # Otherwise the asset overhangs the cell, overlapping its neighbours
        # and shifting the whole board off the tile grid on normalisation.
        wall = self.resolve("wall")
        if wall is not None:
            for role in WALL_SEGMENT_ROLES:
                other = self.resolve(role)
                if other is None or other is wall:
                    continue
                if other.footprint != wall.footprint:
                    problems.append(
                        f"role {role!r} resolves to {other.name!r} with footprint "
                        f"{other.size_x}x{other.size_z}, but it is placed into a wall "
                        f"segment of {wall.size_x}x{wall.size_z} -- it would overhang "
                        "the cell"
                    )
            # A corner sits in the same course as the wall and is stacked once
            # per storey alongside it. A corner of a different height leaves a
            # gap or an overlap at every floor line above the ground.
            for role in WALL_COURSE_ROLES:
                if role not in self.style.roles:
                    continue
                other = self.resolve(role)
                if other is not None and other.size_y != wall.size_y:
                    problems.append(
                        f"role {role!r} resolves to {other.name!r}, {other.size_y} tall, "
                        f"but it stacks in the same course as a {wall.size_y}-tall wall "
                        "-- storeys above it would not line up"
                    )
        return problems
