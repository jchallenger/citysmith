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
    "floor", "floor_upper", "ground", "street", "water", "gravel", "roof",
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
        "wall": [
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
        "wall_interior": [
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
        "wall_corner": [
            _tile(name=("md_wall_corner_1x1_01", "md_wall_corner_1x1_02"),
                  group="Corner", height=2.0, **_MED, **_UNIT),
            _tile(name="Rural Corner", height=2.0, **_MED, **_UNIT),
            _tile(name="bg_wall_1x1_corner_01", height=2.0, **_MED, **_UNIT),
        ],
        # -- civic fabric -------------------------------------------------
        # Guildhall, temple, manor and barracks are built in dressed stone
        # with arched openings, so a party can read importance off the
        # architecture from a street away instead of counting storeys.
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
        "quay_rail": [
            _tile(name="Harbor Fence 02", **_MED),
            _tile(name="Desert fence low", **_MED),
        ],
        "city_wall_core": [
            _tile(name="castle wall corner 1x1 base", **_MED),
            _tile(name="Castle Ruins Wallbase 01", **_MED),
            _tile(name="Cave Wall 01", **_MED),
        ],
        # Battlements, on the outer ring only, so the middle of the rampart
        # stays walkable as a wall-walk. Full-cell, unlike the half piece this
        # replaces -- which was also a 0.5-deep curtain fragment.
        "city_wall_cap": [_tile(name="castle merlon 1x1", **_MED)],
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
        "temple": [_prop("candle", **_MED), _prop("altar", **_MED), _prop("statue", **_MED),
                   _prop("brazier", **_MED)],
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
