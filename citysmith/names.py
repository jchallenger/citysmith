"""Deterministic name generation.

Pure tables plus a seeded RNG, so a given seed always yields the same city.
The Claude layer can overwrite any of these with better prose, but nothing
depends on it -- the generator is fully usable offline.
"""

from __future__ import annotations

import random

_CITY_PREFIX = [
    "Ash", "Black", "Bright", "Cold", "Copper", "Dun", "East", "Fair", "Gray",
    "Green", "High", "Iron", "Long", "Low", "New", "North", "Oak", "Old",
    "Red", "Salt", "Silver", "South", "Stone", "Thorn", "West", "White",
]
_CITY_SUFFIX = [
    "barrow", "bridge", "brook", "burn", "bury", "crest", "dale", "fall",
    "ford", "gate", "haven", "hollow", "keep", "march", "mere", "mill",
    "moor", "reach", "ridge", "run", "shore", "stead", "vale", "watch",
]

_STREET_NOUN = [
    "Alley", "Approach", "Close", "Court", "Cross", "Gate", "Lane", "Market",
    "Mews", "Path", "Rise", "Road", "Row", "Steps", "Street", "Walk", "Way",
    "Wynd",
]
_STREET_ADJ = [
    "Anvil", "Baker", "Barrel", "Candle", "Chandler", "Cooper", "Crooked",
    "Dyer", "Fisher", "Glass", "Hollow", "Lamp", "Mason", "Narrow", "Potter",
    "Quiet", "Rope", "Salt", "Shadow", "Tanner", "Weaver", "Wool",
]

_TAVERN_ADJ = [
    "Bent", "Bleeding", "Broken", "Crooked", "Drowned", "Gilded", "Green",
    "Hanged", "Laughing", "Lucky", "Rusty", "Silent", "Sleeping", "Three",
    "Weeping", "Wild",
]
_TAVERN_NOUN = [
    "Anchor", "Antler", "Barrel", "Boar", "Candle", "Crow", "Drake", "Fox",
    "Griffin", "Hart", "Hound", "Kettle", "Lantern", "Mare", "Oar", "Raven",
    "Rose", "Sparrow", "Stag", "Wheel",
]

_FAMILY = [
    "Ashdown", "Brackwater", "Carrow", "Dunmore", "Elmsworth", "Fenwick",
    "Garrow", "Hallowell", "Ironwood", "Keswick", "Larkin", "Merrow",
    "Norwood", "Orwick", "Pemberly", "Quillon", "Ravensby", "Stroud",
    "Thackery", "Vance", "Whitlock", "Yarrow",
]
_GIVEN = [
    "Alder", "Bess", "Corin", "Dara", "Edran", "Fenn", "Greta", "Hale",
    "Isolde", "Joss", "Kesh", "Lyra", "Marek", "Nell", "Orin", "Pell",
    "Rell", "Sable", "Tobin", "Vesper", "Wren", "Yorick",
]

#: Building kind -> naming pattern.
_SHOP_TRADE = {
    "smithy": ["Forge", "Smithy", "Ironworks", "Anvil"],
    "shop": ["Goods", "Supplies", "Emporium", "Trading Post", "Market Stall"],
    "warehouse": ["Warehouse", "Storehouse", "Depot", "Granary"],
    "temple": ["Shrine", "Chapel", "Temple", "Sanctum"],
    "guildhall": ["Guildhall", "Hall", "Lodge", "Chapter House"],
    "barracks": ["Barracks", "Watchhouse", "Garrison"],
    "house": ["House", "Cottage", "Residence", "Lodgings"],
    "manor": ["Manor", "Estate", "Hall"],
    "stable": ["Stables", "Livery", "Paddock"],
    "apothecary": ["Apothecary", "Herbalist", "Physik"],
}


def city_name(rng: random.Random) -> str:
    return rng.choice(_CITY_PREFIX) + rng.choice(_CITY_SUFFIX)


def street_name(rng: random.Random) -> str:
    return f"{rng.choice(_STREET_ADJ)} {rng.choice(_STREET_NOUN)}"


def person_name(rng: random.Random) -> str:
    return f"{rng.choice(_GIVEN)} {rng.choice(_FAMILY)}"


def building_name(rng: random.Random, kind: str) -> str:
    if kind == "tavern":
        return f"The {rng.choice(_TAVERN_ADJ)} {rng.choice(_TAVERN_NOUN)}"
    options = _SHOP_TRADE.get(kind)
    if not options:
        return f"{rng.choice(_FAMILY)} {kind.title()}"
    noun = rng.choice(options)
    if rng.random() < 0.6:
        return f"{rng.choice(_FAMILY)} {noun}"
    return f"The {noun}"


_HOOKS = [
    "someone has been paying the staff to look the other way",
    "a back room is kept locked and no one will say why",
    "the owner is weeks behind on a debt to dangerous people",
    "a body was found nearby last week and the watch closed the case fast",
    "smuggled goods move through here after dark",
    "a regular vanished and their belongings are still upstairs",
    "the cellar connects to something older than the building",
    "two rival factions both claim this place as neutral ground",
    "the owner is being blackmailed over a forged document",
    "a child has been sleeping rough on the roof for a month",
    "the previous owner died and the will is contested",
    "someone is copying keys for half the street",
]


def hook(rng: random.Random) -> str:
    return rng.choice(_HOOKS)
