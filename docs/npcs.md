# People on the town board

A town board had no people on it. `interior.occupants` derived *who* was in
each building — a keeper, staff, visitors, each with a name and something they
were doing — but nothing ever said *where*, so the roster only existed inside a
scene and the town itself was a stage set.

`citysmith/npcs.py` places them. This is what it decides and what it measured.

## 1. A slab carries no creatures, so a "position" is two things

`creatureCount` is always 0 in a v2 slab. That is the same constraint that
makes a scene paste four party marks and leave the minis to the GM, and it is
not negotiable from here.

So each post is:

- **a row in `<stem>-npcs.json`** — name, role, duty, what they are doing, and
  the tile they stand on. This is the durable artifact; it is what a GM reads.
- **a contrasting floor tile on the board** — where the mini goes.

`tools/creature_state.py` can *read* TaleSpire's creature store, but it is
deliberately read-only: the store is content-addressed and append-only, and
what we have is a partial decode. Writing into it is not a side quest.

## 2. Three populations, and they are different problems

| duty | where they belong | depends on |
|---|---|---|
| `guard` | flanking a gate passage, and at intervals along the main street | `ftg.gates_from_roads` |
| `working` | at their own trade — their yard if they have one, else their own frontage | `interior.occupants`, `build.yard_cells` |
| `off_duty` | somewhere that is **not** their day job: the plaza, or a tavern or shop frontage | a town having anywhere to go |

The third is why the first two cannot be allocated building by building.
Somebody has to *leave* their workshop and turn up on the green, so the roster
is dealt first and then a share of it is moved. A town with nowhere to go has
no off-duty population, which is a fact about the town rather than a failure.

## 3. Two numbers that were badly wrong, and how they read

Both were found by building it and looking at the count, not by reasoning.

**`interior.occupants` deals the whole household, and that is right for a
scene and confetti out here.** Marking every occupant outdoors gave **115 posts
on a 35-building hamlet** and **3,927 on East Tradebourne** — every family in
town standing in its own front garden at once. `OUTDOOR_TRADES` gates it: a
smith works at the forge and a stablehand in the yard, but a householder is
indoors, and the scene pipeline is what opens that door. An ordinary house puts
somebody out at `HOUSE_OUTDOOR_CHANCE`, which is 0.15.

**One guard per 60 tiles of main street made a hamlet a garrison** — 12
watchmen against 10 people at work. `MAIN_STREET_PER_GUARD` is keyed on
settlement band now:

| band | watch |
|---|---|
| hamlet | none. Somebody's uncle looks out of a window. |
| village | one per 220 tiles of main street — a constable |
| town | one per 70 — a patrol, which is what the gates are for |

After both:

| town | band | guards | working | off duty | per building |
|---|---|---|---|---|---|
| Pelvesthollow | hamlet | 0 | 12 | 0 | 0.34 |
| Graybank | village | 6 | 42 | 18 | 0.44 |
| East Tradebourne | town | 167 | 405 | 118 | 0.70 |

Pelvesthollow's zero off-duty is chance rather than a rule — seeds 1–5 give 3
to 7 — because `OUT_AND_ABOUT_CHANCE` is 0.12 over 35 households.

## 4. Nothing stands in a doorway

The rule the furnishing pass learned indoors, and it matters more outdoors: a
slab has no physics, so a token dropped in a threshold is a door that does not
open for the whole session with nobody able to move it. `_door_cells` takes
both the doorway and the cell it opens onto. A gate guard is placed working
*outward* from the passage for the same reason — a mark in the carriageway is a
guard standing in the gateway.

`tests/test_npcs.py` holds this one, plus: nobody inside a shell, nobody in the
water, no two people in one cell, and a budget that drops the off-duty before
it drops a gate guard.

## 5. What the indoor vocabulary could not do

`interior._DOING` deals *"asleep in a chair"* and *"writing, and covering it
when anyone passes"*. Those read fine across a tavern table and read as
nonsense for somebody standing in a lane — and they shipped that way for one
build before anyone read the manifest. `npcs` has its own lists per duty, and
`test_outdoor_doings_are_not_the_indoor_ones` keeps them apart.

## 6. Open: the marks are verified on grass and not on paving

Read on a board (Pelvesthollow, 2026-08-25): a pale plank mark is clearly
legible on grass — the most artificial thing in the frame — and the density
reads as a working hamlet rather than a crowd, which is the thing a count could
not have told us.

**Paving is unverified and is the real risk.** The first guard tile was grey
castle stone, and a guard is posted on cobble: grey on grey. The three tiles
were swapped on that reasoning — guard timber, worker stone, idler carpet — but
reasoning is not a measurement, and this project's own standard is that an
asset is probed and read from four sides rather than picked by name. A hamlet
posts nobody on its streets, so the review that would have caught it could not
run. `npc-mark-contrast` in `tasks.json` is the probe that settles it.
