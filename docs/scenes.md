# Scenes — walking the party into a building

A town board is where the party travels. A **scene** is where they play: one
building from that town, opened up, with the people who are in it and four
marks on the floor where the tokens go.

```bash
python -m citysmith scene out/graybank/layout.json "halfling"
```
```powershell
.\tools\scene.ps1 enter -Scene graybank-tavern-0014
```

The first command writes the board. The second puts it in TaleSpire — making a
board the first time, and **switching to the one that already exists** every
time after. Nothing is ever deleted.

---

## What you get

Per scene, in `out/scenes/<scene-id>/`:

| File | What it is |
|---|---|
| `<scene-id>.slab.txt` | the board itself, ready to paste |
| `<scene-id>-paste-order.txt` | which file first, for anything driving the paste |
| `scene.json` | the manifest: board name, marks, occupants, position |
| `brief.md` | the GM page — who is inside, the hook, where the party starts |
| `plan.svg` | the floorplan with the party marks drawn on it |

An interior is normally **one slab and one paste**. The whole tavern above is
231 assets.

---

## Choosing the building

```bash
python -m citysmith scene out/graybank/layout.json --list --top 10
```

Then name it three ways — whichever you have to hand:

```bash
python -m citysmith scene out/graybank/layout.json tavern-0014      # by id
python -m citysmith scene out/graybank/layout.json "halfling"       # by name
python -m citysmith scene out/graybank/layout.json kind:tavern      # biggest of a kind
```

A name that matches more than one building is **refused, not guessed**: FTG
names six of Graybank's buildings `Farm`, and quietly taking the first is how
the party ends up in the wrong barn.

---

## What is in the room, and where it came from

This matters more than it sounds, so it is worth being exact.

**The export names the building and says what trade it is. It contains no
people at all.** Checked rather than assumed: across all three Fantasy Town
Generator exports, every `BUILDING` feature carries exactly `id`, `type`,
`name`, `buildingType` and `material` — 1,007 of them in East Tradebourne, no
sixth key. MFCG exports geometry only and does not even name the building.

So the occupants are **derived**: the trade says what work happens there, the
authored name says whose place it is, and the footprint says how many fit. They
are stable for a given seed, so the same faces are there when you walk back in.
The brief says so on the page, every time, rather than presenting them as
exported facts.

**To use real people instead**, write a roster keyed on building id and point
at it:

```json
{
  "tavern-0014": [
    {"name": "Mathias Shore", "role": "guild speaker", "doing": "waiting for the party"}
  ]
}
```
```bash
python -m citysmith scene out/graybank/layout.json halfling --roster campaign/occupants.json
```

An authored entry replaces the derived roster for that building completely, and
the brief drops the "derived" note for it. Nothing here overwrites something a
person wrote down.

---

## Where the party stands

**Tokens cannot be pasted.** A v2 slab's creature count is always zero, so no
slab can carry a mini. What the slab carries is the *marks*: one contrasting
floor tile per character, in the room behind the front door, clustered so the
party arrives as a party — and never in the doorway itself, because a mini in
the opening plugs it.

Each mark **replaces** the floorboards in its cell rather than sitting on them,
and clears the cell's props: a character should not arrive inside a barrel.

Drop the minis on the marks by hand. `brief.md` lists the tile coordinates and
`plan.svg` draws them.

```json
"party": { "size": 4, "names": ["Cinder", "Ilian", "Karai", "Lilli"],
           "arrival": "inside" }
```

`arrival: "outside"` puts the marks on the approach instead, for a scene that
starts at the door rather than through it.

---

## What the plan looks like

Two shapes, chosen by size and by what the building is for.

**A cottage gets a BSP** — three to six rooms sharing walls, which is what a
cottage is. Below 90 tiles, and outside the kinds whose whole point is one big
volume, that is the right answer and nothing else is tried.

**Everything else gets a hall**: a principal space with rooms off it, and the
door opening onto the hall so the plan is legible from the threshold. Which
form depends on the proportions:

- **A nave** running in from the door, flanks either side, a band of rooms
  across the far end — for a building deeper than it is wide from the door.
- **A broad hall** spanning the full width against the entrance wall, with the
  back cut into rooms — for a wide, shallow one.

On an upper level the hall narrows to a three-tile corridor, which turns the
same construction into a landing with rooms off it: an inn's bedroom floor.

The principal room is named for the trade — `Nave`, `Great Hall`,
`Loading Floor`, `Common Room` — once, and no room on a level repeats another's
name.

**Why this exists.** The BSP does not scale, and the numbers said where it
stopped: rooms per level ran 3.3 under 50 tiles and **23.5 above 200**. A 29x15
warehouse — 145 x 75 ft — came out as 31 rooms of about 15x25 ft, four names
cycled seven times each, and 52 doorways. Now it is a 120x35 ft loading floor
with a counting room, a rope store, a cooperage, a cold store and six bays off
it.

---

## What it is built from

The **tier** picks the whole set — wall, partition, floor, window, door,
corner — because a building that changes material at the corner reads as a
mistake rather than as variety.

| Tier | Kinds | Fabric |
|---|---|---|
| civic | temple, guildhall, manor, barracks, **and anything stone** | dressed stone, arched windows, a fancy door |
| trade | tavern, shop, apothecary, smithy | the house wall, told apart by its door |
| common | house, and anything unrecognised | timber-framed panels |
| utility | warehouse, stable, shed | boarding, one storey, **no glass** |

FTG marks some buildings `material: STONE_BRICK`, and stone wins: a stone shop
is a stone building whatever is sold in it.

Windows follow the same rule the town facades use — dense at the front, sparse
on the flank, **never on the back**, one fewer on the ground floor. A barn has
none at all, and the kit it is built from does not have one to give it.

Storeys come from the layout, capped by `interior.max_levels` (three), except
that a warehouse or stable is **one** however many the layout invented: those
are sheds with one big volume.

---

## The board, and why nothing gets deleted

`campaign/boards.json` records which board holds which scene. **It is the
only record there can be**: the TaleSpire campaign list shows a name and
nothing else — no size, no date, no contents, no API — so a board cannot be
asked what is on it. It lives in `campaign/`, not `out/` -- build output is
regenerable and this is not.

`scene.ps1 enter` looks the scene up and takes one of four routes:

| | |
|---|---|
| **NEW** | Nothing recorded. New board, camera straight down, paste, rename, record. |
| **READY** | The board exists and holds this build. Opens the campaign list. Pastes nothing. |
| **STALE** | The board exists, but the scene has been rebuilt since. Still reuses it. |
| **MOVED** | The town was re-imported and this id may be a different building now. Reported, not resolved. |

**STALE reuses on purpose.** A board is where something happened — a fight, a
conversation, tokens someone moved — and a paste cannot replace what is already
on it, because there is no erase. `-Rebuild` puts the new build on a *second*
board and leaves the first exactly as it is.

**Switching boards is the one step that stops and asks.** The list re-sorts
alphabetically on every rename and nothing can read text off the screen, so
`enter` opens it, screenshots it, and hands over:

```powershell
.\tools\scene.ps1 switch -Scene graybank-tavern-0014 -Row 3
```

Read the row off `out/flyby/scene-boards.jpg`. A guessed row is a jump to
somebody else's board, and on a 387k-asset town that is a thirty-second
mistake.

To delete a board, do it by hand — `.claude/skills/talespire-boards/SKILL.md`
has the procedure and the warning. Then:

```bash
python -m citysmith boards forget graybank-tavern-0014
```

### The naming scheme

Settled, and the whole of it:

| What | Form | Example |
|---|---|---|
| Town board | `<Town>` | `Graybank` |
| **Interior board** | `<TWN>/<code> <building> Interior` | `GRB/T14 The Halfling and the Fox Interior` |
| Throwaway board | `Probe - <what>` | `Probe - Halfling old partitions` |
| Scene id | `<town-slug>-<building-id>` | `graybank-tavern-0014` |
| Scene directory | `out/scenes/<scene-id>/` | |
| Slab | `<scene-id>.slab.txt`, plus `-rNNcNN` if it ever splits | |
| Paste order | `<scene-id>-paste-order.txt` | |
| Board record | `campaign/boards.json` | |

**The board name is the only one of these a person reads under pressure**, and
it is the one with a hard constraint on it.

**The campaign list clips a row at sixteen capital letters.** Measured, not
estimated: a board renamed to `ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop`
renders in the list as `ABCDEFGHIJKLMNOP…`. The clip is on pixel width and the
list is set in small capitals, so ordinary mixed-case prose gets a little
further — `The Halfling and the Fox - Graybank interior` shows as
`The Halfling and the F…` — but sixteen is the number to design to. It is also
the *only* thing TaleSpire will tell you about a board: no size, no date, no
contents, no API.

So the town tag and the building code come **first**, and what the list shows
is `GRB/T14 The Half…` — which town, which building, and enough of the name to
recognise it.

- **The town tag** is three letters, derived: initials for a multi-word name
  (`East Tradebourne` → `ETR`), first letter plus the next two consonants for a
  single word (`Graybank` → `GRB`, `Pelvesthollow` → `PLV`). Set
  `board.town_codes` to override one.
- **The building code** is the kind's initial and the building's number:
  `tavern-0014` → `T14`. **The number alone is already unique** — both
  importers number every footprint from one global counter, so `tavern-0014`
  and `temple-0014` cannot both exist. Checked across all 1,227 buildings in
  the four towns here: zero clashes. The letter is there to be read, not to
  disambiguate, which is why tavern and temple sharing a `T` does not matter.

That makes every row distinct by construction. The rule this replaced added a
number only when two buildings shared a name, and appended it — testing the
wrong condition (44% to 77% of buildings in a town share their first sixteen
characters; `Residence` occurs 129 times in East Tradebourne) and writing the
answer where the list could not show it.

---

## Getting the party onto the board

This is the step *after* everything citysmith does, and it was unwritten — the
tool builds a board and then stops, and how five people actually get onto it
was left to the reader. There are two ways and the choice is not a detail.

**The GM summons everyone.** Players menu → *"Summon Players To This Board"*.
One click, nobody has to navigate. The catch is that it takes **everyone
loaded in the campaign**, so it cannot move half a party — if the rogue is
casing the warehouse while the rest drink in the tavern, this ends that.

**Or the GM names the board and the players navigate.** `Space` → the top-left
icon → pick the row. This is the one that handles a split party, and it is why
the naming scheme in this document is load-bearing for *players* and not only
for our own bookkeeping: the row they are looking for is the row that gets
clipped at sixteen capitals. `GRB/T14 The Half…` is findable; `Interior -
Graybank - The Halfling and the Fox` is one of nine identical rows.

Two things that follow, both from `docs/board-strategy.md`:

- **A player who arrives on a board with no GM is locked to a fixed view**
  until a GM joins. If you send them ahead, get there first.
- **Minis are campaign-level, not board-level**, and one can be marked
  "unique". So the hand-placing that a scene's party marks imply is **once per
  campaign, not once per scene** — the same mini walks onto every board. The
  marks are still the right output; they cost less than `CLAUDE.md` implies.

The same applies to the NPC marks a *town* board carries (`docs/npcs.md`): the
board says where, `<stem>-npcs.json` says who, and the minis are placed once
and travel.

---

## Settings

Everything is in `config/scene.json`, read by the builder *and* by the driver —
they have to agree about the board name. Every key has a working default in
`citysmith/config.py`, so the file is an overlay and a missing one is fine.
**A key it does not recognise is reported, not ignored**: a typo otherwise runs
clean, does nothing, and shows up on the board an hour later.

| Key | Default | What it does |
|---|---|---|
| `seed` | 33 | Deals the palette, the plan and the occupants. |
| `style` | `medieval` | Which palette. |
| `board.name_template` | `{prefix}{building} - {town} interior` | See above. |
| `interior.max_levels` | 2 | Storeys built, however many the town gave it. |
| `interior.spread_levels` | true | Levels side by side, not stacked (below). |
| `interior.pad` | 3 | Tiles of ground round the building. |
| `interior.prop_density` | 0.12 | How dressed the rooms are. |
| `interior.roof` | false | A roof hides the interior; off is the useful default. |
| `party.size` / `party.names` | 4 / — | How many marks, and whose. |
| `occupants.hour` | `day` | `night` thins the room. |
| `paste.*` | — | Timings for the driver. |

### Levels go side by side

A two-storey inn is built as two floors *next to each other*, not stacked. It
is the same argument that leaves the roof off: **TaleSpire cannot hide an upper
floor**, so a stacked inn is one visible attic and two rooms the camera has to
be flown inside to use. Side by side, the whole building reads from directly
overhead, which is how a battle map gets used. The stair is built at both ends
so the connection is legible. `interior.spread_levels: false` stacks them.

---

## Where this sits in the pipeline

```
GeoJSON  ->  citysmith import  ->  layout.json  ->  citysmith build   ->  the town board
                                         |
                                         +-------->  citysmith scene  ->  a scene board
```

A scene needs only `layout.json`. You do not have to have built the town to
walk into one of its buildings.
