---
name: talespire-boards
description: Manage the boards inside a TaleSpire campaign — create, name or rename, switch between, and delete or prune boards, and lay out a campaign with a board per location. Use when the user wants a board per town/dungeon/region, asks to name or rename a board, asks to delete, prune, clean up or tidy boards, asks which boards are safe to remove or which one something was pasted onto, or is about to paste a generated map and needs somewhere to put it.
---

# TaleSpire campaign and board management

A campaign holds many boards. Everything here is driven through the UI with
`tools/ts.ps1`, because TaleSpire has no API and no file-drop path — the same
constraint that makes pasting the only way to get a map in.

**Read `docs/pasting-into-talespire.md` first** for the binding table and the
duration rules. Every click below assumes TaleSpire is running **windowed**,
sized to fit the desktop, with `textinputhost.exe` allowlisted.

## Before touching anything

**Check whether a human is using the game.** Synthetic input goes to whatever
board is in front, and there is no undo for "I pressed Space during someone
else's session". `ts.ps1 shot -Name check` and look at the title bar: if the
board is not the one you left, or has content you did not paste, stop and ask.

```bash
pwsh tools/ts.ps1 shot -Name check
```

**Read the HUD before pressing `Space`.** It is a toggle over the HUD, not
over the board list, and the difference matters: with the HUD already up,
`Space` *hides* it and whatever click you had queued next lands on the board in
build mode, where a click is not a no-op.

```bash
pwsh tools/ts.ps1 hudstate
pwsh tools/ts.ps1 boardsstate
```

Both are **self-calibrating, and that is the part to preserve if you ever
rewrite them.** Each measures its widget against a control patch of bare board,
so a dark board darkens both and the difference stays near zero. The naive
shape — sample one patch, call dark pixels "the widget" — has failed three
times in this project: Graybank's grass fooled `planestate`, and a first cut of
`boardsstate` read the left tool column and reported OPEN at a closed panel.
Both return `unknown` for anything between the measured bands, and `boards`
refuses on `unknown` rather than guessing.

Then confirm the two persistent toggles are off, because both survive making a
new board and both imitate a defect:

```bash
pwsh tools/ts.ps1 planestate
```

**`planestate` only means anything in build mode.** Outside it the toolbar is
not drawn and the probe samples the board instead — Graybank's grass reads as a
confident "ON". It now says `UNKNOWN` in that case; if you see that, press `B`
and read it again. The same applies to `ts.ps1 plane`: pressed outside build
mode it once turned the plane *on* while the reading claimed it had failed to
turn it off.

## The three operations

### Create

```bash
pwsh tools/ts.ps1 newboard
```

Clicks the `+` in the top bar and drops into build mode. **It always makes a
new board and never disturbs the current one** — which is the point: a probe
belongs on its own board so a stray stamp is not misread as a defect in the
thing being probed.

New boards are called `Unknown Realm N`. `N` is assigned at creation and gaps
get reused, so **the number is not a stable identifier**. Name the board.

### Name

```bash
pwsh tools/ts.ps1 rename -Text "Graybank"
```

The `...` beside the board name in the top bar opens a **Board Name dialog
directly** — not a menu. The current name arrives already selected, so a
`Ctrl+V` over it replaces the whole thing, and `OK` commits.

**The clipboard is the only way to get text in.** TaleSpire reads raw input, so
synthetic typing does not arrive at all; this is the same reason `Ctrl+V` needs
a real scan code. Do not try to type a name character by character.

**Verify the rename, and verify it with a change you could SEE fail.** The
command prints `renamed board to '...'` whatever happened -- that is the
script's own return string, not a reading of the game. Renaming a board to the
name it already has therefore tests nothing: success and failure look
identical. Rename to something different and read the title bar back.

The cheap way is a crop of the title bar rather than a whole frame -- **3.7 KB
against 470 KB**, and the name is the only thing in it:

```bash
pwsh tools/grab.ps1 -Name titlecheck -X 1500 -Y 0 -W 420 -H 34
```

Derive that x from the client width on any other window size. Note the title
bar shows far more of a name than a list row does, so it verifies the rename
took -- it does **not** tell you the name will still be distinguishable in the
campaign list. That is what the two dozen character budget below is for.

**The chevron next to `...` is a saved-state indicator, not a board list.** It
toggles "No unsaved changes" and does nothing else. It was the obvious thing to
try and it is wrong.

### Switch

```bash
pwsh tools/ts.ps1 boards -Name list
```

`Space` raises the HUD, then the **top icon of the left-hand column** opens
*Campaign Boards*: every board, with a play arrow to jump to it.

**The panel has a `Filter...` box at the top, and it is probably the answer to
the one thing here that is not automated** (SEEN 2026-08-26, BEHAVIOUR NOT YET
VERIFIED). `CLAUDE.md` lists unattended board switching under "Not built yet",
because the list re-sorts on every rename and nothing can read text off the
screen -- "needs either OCR or a keybind we have not found". A filter box needs
neither: paste a board name into it, the list narrows to what matches, and the
first row is the one you want, at a known y, whatever the campaign is sorted
like.

What is established: the box exists, at roughly client (228, 163) on a
1920x1080 window, with an `x` to clear it at (384, 163). Text goes in by
clipboard like every other field here -- typing does not arrive.

What is NOT established, and must be measured before anything is built on it:
whether it matches on prefix or substring, whether it is case-sensitive,
whether the filtered rows start at the same y=200 as the unfiltered ones, and
whether the current board's row still expands in place when filtered. **Do not
write `scene.ps1 switch --by-name` until those four are answered.** The
procedure is: paste a name, screenshot, compare against the unfiltered shot.

Also on the panel, unrecorded elsewhere: **`Create new board`** is a button at
the top of it (so `newboard`'s `+` in the top bar is not the only route), and
**`Add Board Copy`** with an `Enter Copy ID` field sits at the bottom -- a
board-import path nothing here has tried.

Three things about that list:

- **`Space` is a toggle, and `ts.ps1 boards` does not handle that.** It has
  failed twice in one session for this reason: the HUD was already up, `Space`
  closed it, and the click landed on the board. The reliable form is two
  separate calls — check the HUD state in a screenshot, then only press `Space`
  if it is down, then click the icon:
  ```bash
  pwsh tools/ts.ps1 click -X <clientX+17> -Y <clientY+57> -Hold 0.3
  ```
- **The row you are standing on is HIGHLIGHTED, not expanded** — corrected
  2026-08-26 against `out/flyby/finalcheck.jpg`, where `Unknown Realm 22` is
  the current board: orange fill, a **person icon in place of the play arrow**,
  and the same height as every other row, with the 42 px pitch uniform through
  it. The previous note here said it expands in place and pushes everything
  below it down by ~134 px, and that is wrong: that is what a row looks like
  once you click its expander at x=79, which is a thing you do, not a thing the
  current board does. Row arithmetic from `200 + (N-1)*42` is therefore sound
  in a resting list. It is still not sound across a *rename*, so read the rows
  off the shot; `scene.ps1 switch` takes `-RowY <pixels>` when you would rather
  give the measured y than a row index.
- **The title bar is not the list.** The bar at the top right shows far more of
  a name than a list row does (`GRB/T123 Chapel of Hermes Int...` against
  `GRB/T123 Chapel of He...`), so never judge whether a name fits by reading
  the title bar.
- **The rows are plain alphabetical.** The current board is highlighted in
  place, not floated to the top — an earlier note here said otherwise and it
  was wrong; `Pelvesthollow` merely happened to sort first. Renaming still
  reshuffles the list, so read the rows off the screenshot and never reuse a
  position from earlier in a session. Rows start at client y=200, 42 apart,
  play arrow at x=360, row expander at x=79.
- **Switching to a large board takes tens of seconds.** A 387k-asset town needs
  ~30 s before it is safe to click anything. Wait, then screenshot to confirm
  the title bar changed before doing anything else.

### Group into folders

**A campaign has folders, and this is almost always what you want instead of a
second campaign.** Characterised end to end on throwaway boards 2026-08-26.
There is **no "move to campaign"** anywhere, so separating work across two
campaigns costs a copy or a re-paste of every board; separating it across two
folders costs one dialog.

**A folder is not an object you create.** It exists only while at least one
board is in it: name one in the dialog and it appears, move the last board out
and it is **gone**. Measured -- `Workshop` vanished from the list the moment
its only board was filed elsewhere. So you cannot pre-create `Ready to publish`
and then fill it, and there is no folder-rename: renaming means re-filing every
board in it under the new name.

**How to file a board.** Expand a row with the `▶` at its **left** (x=79).
Offsets are from the row's own y, and **the menu is shorter for a board you are
not standing on** -- *Reload Board* only appears for the current board:

| item | any board | current board |
|---|---|---|
| **Delete board** | 150, `row + 42` | 150, `row + 37` |
| **Set Folder** | 150, `row + 68` | 150, `row + 63` |
| *Reload Board* | — | 150, `row + 89` |

*Set Folder* opens a centred **Move to folder** dialog -- "enter a new folder
name or pick an existing folder from the dropdown". Text goes in by clipboard
like every other field here, and the dropdown flips itself to
`- CREATE NEW FOLDER -` as soon as you type:

| control | client |
|---|---|
| text field | `CX, CY - 16` |
| folder dropdown | `CX, CY + 20` |
| **ACCEPT** | `CX - 105, CY + 70` |
| CANCEL | `CX + 95, CY + 70` |

The dropdown lists **`- NO FOLDER -`** first and then every existing folder, so
filing is reversible and a board can be pulled back out. A board is in at most
one folder, and the move is a **move** -- there is no second board afterwards.

**Filing many boards is a command, not a click sequence.**

```bash
pwsh tools/ts.ps1 setfolder -Y 283 -Text "Workshop"
```

`-Y` is the row's own y, **read off a `boards` shot and never computed from a
row index**. It clicks the expander, then *Set Folder* at `row + 68` -- one
number for both menu shapes, because items are ~26 px tall so +68 lands inside
*Set Folder* whether it sits at +63 (current board) or +68 (any other). *Delete
board* is a full item away at +37/+42, which is the reason this is a command
rather than six clicks by hand.

**The target folder AUTO-EXPANDS when a board lands in it**, and the filed
board's own row menu stays open, so the list below moves. That makes a naive
loop file the wrong boards after the first. Collapse the target again and the
geometry returns exactly:

| | client y |
|---|---|
| first folder header | 200 |
| second folder header | 241 |
| **first ungrouped board** | **283** |

With both folders collapsed, a filed board leaves the ungrouped list and the
next one rises into 283 — so *file at 283, collapse the folder, repeat* is a
loop with a fixed target, and it is how twenty-seven boards get filed without
hunting for a row each time.

**What folders do to the list, which is the point:**

- They sort **alphabetically among themselves, above every ungrouped board**
  (`Ready to publish` sits above `Workshop`, both above `East Tradebourne`).
- Each **collapses to a single row** on the triangle at x≈70. Twenty-one
  `Unknown Realm N` rows become one line you can fold away.
- **They break row arithmetic, so stop counting.** A folder header eats a row
  slot, an expanded folder inserts its children, and an expanded *row menu*
  inserts two or three more. The 42 px pitch still holds, but the mapping from
  "Nth board" to a y is gone. Read positions off the shot, every time.
- The folder header carries an icon at its far right (x≈378, a figure in a
  dashed circle) that is **unidentified** -- it shows no tooltip on hover and
  has not been clicked.

### Index

**The campaign list is a column of names and nothing else.** No contents, no
asset count, no size, no date. A board holding a finished town and one holding
last week's throwaway are the same row, and that single fact is why deleting is
dangerous, why `prune` has three buckets that are really just "go and look",
and why a nineteen-board clean-up on 2026-08-27 had to be done off screenshots
and memory.

So write it down at the moment it is knowable:

```bash
citysmith boards note --board "East Tradebourne" --holds town \
    --source out/tradebourne-v2/layout.json --stem et --chunks 114 --keep
```

`--holds` is `town`, `scene`, `probe` or `other`. **`other` is not a failure**
-- it is the honest answer for a board somebody made by hand, and it still
buys a name, a date and a note, which is more than the list gives. `probe` is
marked disposable automatically; `--keep` and `--disposable` override either
way, because it is a claim by whoever recorded it and not a deduction.

**The index is written at PASTE time, and that is the whole design.** Nothing
can be recovered afterwards, so both drivers do it themselves:

- `panel_review.ps1` records its probe board as disposable.
- `review.ps1 tiled -Board "Graybank"` renames the board after the paste and
  records the town. Without `-Board` it prints `NOT INDEXED` rather than going
  quiet -- the same rule as "rename a board the moment a paste lands on it",
  turned into one command.
- `citysmith boards record <scene>` indexes the scene board too.

Read it back, and hold it against the game:

```bash
citysmith boards index --seen-file campaign/campaign-list.txt
```

Three states, all of them ordinary, which is why this reports rather than
passes or fails:

| | what it means |
|---|---|
| **matched** | indexed and still on screen. Most of them, once it is filled |
| **GONE** | indexed, not in the list: deleted or renamed by hand. Nothing here is told either way -- `boards rename` follows a rename, `boards drop` forgets a deletion |
| **UNRECORDED** | in the list, nothing written down. **The bucket worth shrinking**: it is exactly the set of boards you cannot decide anything about without opening them |

Exit is 1 while anything is unrecorded, so a session can be held to leaving the
campaign accounted for.

**An entry is a record of a paste, not a reading of the board.** A person can
paste over a board, rename it or delete it with no way for anything here to
notice; reconciling against a transcribed listing is what turns that from a
silent lie into a reported difference. Backfilled entries should say so in
their `--note` -- this campaign's town boards carry *"backfilled from the
layout still in out/; NOT recorded at paste time, so the digest is unknown"*,
which is the difference between a record and a guess.

### Delete

**Decide what to delete before you learn how.** This is the one irreversible
operation here, the list tells you a name and nothing else, and a board with
someone's work on it looks exactly like an empty one from the list. So the
first step is never a click:

```bash
python -m citysmith boards prune --seen-file campaign/campaign-list.txt
```

Write the campaign list into a text file first, read off a `ts.ps1 boards`
screenshot and laid out **the way the panel lays it out** -- folder headers at
the left margin, their boards indented under them:

```
Ready to publish:
  East Tradebourne
  GRB/T14 The Halfling and the Fox Interior
Workshop:
  Unknown Realm 3
```

**Indentation, not `Folder/Name`.** The obvious format is wrong here: this
project's own scheme is `GRB/T14 The Halfling and the Fox Interior`, so a
slash-separated listing files it under a folder called `GRB` and the published
set reads as empty -- silently. Indenting also makes the file a transcription
of the screen rather than a translation of it. `prune` then sorts them into three:

| bucket | what it means | what to do |
|---|---|---|
| **keepers** | a scene in `campaign/boards.json` points at it | leave alone |
| **unfit to publish** | unnamed, but filed under `Ready to publish` | **FAIL** -- name it or unfile it |
| **disposable** | the **index** records it as safe to delete | safe to delete |
| **prunable** | *provably* disposable: a scene name the registry itself recorded as superseded | safe to delete |
| **unnamed** | still called `Unknown Realm N` | **switch to each and look**, then name the keepers |
| **unclaimed** | named by hand, claimed by no scene | **no recommendation** -- ask |

**`disposable` is the only bucket here that is a record rather than the absence
of one.** The others all mean "nothing was written down", in three flavours:
`unclaimed` is ask-the-owner, `unnamed` is switch-and-look, and `prunable` can
only ever see the boards a scene explicitly superseded. `disposable` is
somebody having said what a board was when they pasted it. A board a live scene
claims is never listed there, whatever its entry says -- the scene registry is
the older claim.

Anything the index accounts for drops out of `unclaimed` and `unnamed`, because
those exist to flag mysteries and an indexed board is not one. On this campaign
that took the go-and-look list from ten boards to two.

**Two of the remaining buckets are deliberately not delete lists, and each cost
a bug.**

`unclaimed` exists because the registry only ever tracked *scene* boards, so
East Tradebourne, Graybank and Pelvesthollow are claimed by nothing, and a
first version that offered to delete "whatever the registry does not own"
listed all three finished towns. Anything a human named, a human decides on.

`unnamed` exists because the fix for *that* went too far the other way. It
treated `Unknown Realm N` as provably disposable -- the default name is what
`newboard` hands out, so surely nobody came back to it. On 2026-08-26 the
board in front of us was `Unknown Realm 22`, holding the newest build of a
town its owner wanted, and `prune` listed it for deletion. **A default name is
the absence of evidence, not evidence of absence**, which is the same sentence
this file already uses about the list two sections down: a board with
somebody's work on it looks exactly like an empty one from the list. Unnamed
boards are a work list. Switching to each and looking is the only way to
decide, and that is a person's job.

Two cheap habits keep the bucket small: **rename a board the moment a paste
lands on it**, and give throwaways a `Probe - ` prefix so they read as
disposable without anyone having to remember which they were. Both are now one
flag on the driver that did the paste -- `review.ps1 tiled -Board`, and
`panel_review.ps1`, which indexes its probe without being asked.

There is a delete, and it is deliberately awkward. Expand a row with the `▶` at
its **left** (x=79) — not the play arrow at x=360, which switches to the board —
and the row opens to show **Delete board** and **Set Folder**. Delete raises a
dialog that requires you to type the literal word `DELETE` into a field before
`OK` will do anything.

| Step | Client x, y |
|---|---|
| row expander | 79, `row` |
| **Delete board** | 145, `row + 38` |
| confirmation field | 800, 460 |
| `OK` | 670, 515 |

The field takes a paste like any other (`setclip` then `Ctrl+V`); typing does
not arrive.

**The current board's row DOES offer Delete board** — seen 2026-08-24, on a
board that was in front at the time, with `Delete board`, `Set Folder` and
`Reload Board` under its expander like any other. An earlier note here said the
opposite ("a different icon set and no delete") and it is wrong. Whether the
delete *succeeds* on the current board is untested and not worth testing
casually.

Because the list is alphabetical, repeatedly deleting the *top* row is the
simplest loop, and it needs no re-reading of positions between iterations.

**This is irreversible and there is no undo.** Confirm the scope with the owner
of the campaign before starting, and look at what is on each board first — a
board with someone's probe on it looks exactly like a board with nothing on it
from the list alone.

## Laying out a campaign

The pattern that works for a multi-location campaign: **one board per place,
named for the place, pasted in one unattended run.**

```bash
python -m citysmith --out-dir out/graybank import Graybank.geojson
```
```bash
python -m citysmith --out-dir out/graybank build out/graybank/layout.json --stem gb --seed 33 --by-region --chunk-tiles 80 --keep-open-country
```
```bash
pwsh tools/review.ps1 tiled -Name gb -Stem gb -OutDir out\graybank -ShotEvery 6
```
```bash
pwsh tools/ts.ps1 rename -Text "Graybank"
```

`review.ps1 tiled` calls `newboard` itself, so each town lands on a fresh board
with nothing under it. `-ShotEvery N` thins the screenshots — a 114-chunk town
otherwise takes 228 grabs, and the first and last are kept regardless because
the first proves the run started on bare board and the last proves it finished.

Rename **after** the paste, not before: if the paste goes wrong you want to
re-run it onto another fresh board, not clean up a board that already has a
name you care about.

### Naming conventions

**The list clips a row at about two dozen characters, so what identifies the
board has to be in the first two dozen.** Measured on the real list: two
interiors named `Interior - Graybank - The Halfling and the Fox` and
`Interior - Graybank - The Baron's Rabbit` both render as
`INTERIOR - GRAYBANK - T...`. Two boards, one visible name, in the only list
there is. A prefix that groups is worth nothing if it eats the part that tells
them apart.

- A location board gets the location's name, exactly as the source calls it:
  `Graybank`, `East Tradebourne`. It has to match what the GM will say at the
  table and what the export is filed under.
- An interior board leads with the **building**:
  `The Halfling and the Fox - Graybank interior`. A name the export repeats, or
  one that had to be invented because MFCG supplied none, carries its building
  id too: `Farm (stable-0003) - Graybank interior`. `citysmith scene` does this
  from `config/scene.json`.
- A throwaway gets a `Probe - ` prefix: `Probe - East Tradebourne rampart`.
  Short enough to survive the clipping, and it makes them obviously deletable.
- Leave `Unknown Realm N` alone on boards you did not make. One of them is the
  campaign's default empty board and others may be someone's work in progress.

### A board per building, and going back into one

`citysmith` keeps its own record of which board holds which scene, because the
list cannot be asked: `campaign/boards.json`, written after a paste lands.
Not under `out/` -- that is gitignored build output and this record cannot be
regenerated from anything.

```powershell
.\tools\scene.ps1 enter -Scene graybank-tavern-0014
```

First visit: new board, paste, rename, record. Every visit after: it opens the
board list, screenshots it, and stops -- **it will not guess a row**, because
the list re-sorts alphabetically on every rename and nothing can read text off
the screen. Read the row off `out/flyby/scene-boards.jpg`, then:

```powershell
.\tools\scene.ps1 switch -Scene graybank-tavern-0014 -Row 3
```

If the scene has been rebuilt since the board was pasted it reports `STALE` and
**still reuses the board**. A board is where something happened, a paste cannot
replace what is already on it, and `-Rebuild` therefore makes a second board
rather than touching the first. Nothing in that script deletes anything.

## Verifying a board holds what you think

The title bar is the only cheap check, and it only tells you the name. To
confirm the *content*:

- The last chunk of a tiled paste is the one covering the map's anchor, so
  `out/flyby/<Name>-NNN-down.jpg` from the final chunk is the one frame that
  actually shows geometry. Every earlier chunk lands off-screen and its
  screenshot is bare board — that is expected, not a failed paste.
- To review the whole board afterwards, `review.ps1 flyby -Name <n>` tours it
  at play height from four sides plus overhead and eye level.
- Judging a step or a seam needs the camera **below** the height slider's
  midpoint; higher than that, distance fog draws a hard full-width line that
  has been mistaken for a chunk boundary twice.

## What is not solved

- ~~`ts.ps1 boards` is unreliable~~ **FIXED 2026-08-26.** It reads
  `hudstate` and `boardsstate` before it touches anything, presses `Space` only
  when the HUD is genuinely down, does nothing when the panel is already open,
  and refuses on an unreadable state. It was **unsafe**, not merely unreliable:
  there are three states and the old code saw two, so with the HUD already up
  `Space` hid it and the following click landed on the board *in build mode*.
- **The `Filter...` box is unmeasured.** Four questions above; answering them
  probably closes "switching to an existing board, unattended", which is the
  oldest open item in `CLAUDE.md`'s "Not built yet".
- ~~Delete is not scripted~~ **SCRIPTED 2026-08-27**, `tools/delete_boards.ps1`.
  It deletes whatever is at `-RowY` (default 283, the first ungrouped board
  with both folders collapsed) and repeats, which needs no re-reading of
  positions because a deleted row's successor rises into the same slot.
  `-Expect` is the count you have *seen* on a screenshot and is mandatory:
  nothing here can read a board name, so the caller checking the list first is
  the only thing between this and somebody's town.

  **It guards on the dialog's own orange banner and stops rather than clicking
  blind.** Without that, a delete that does not open leaves the next two clicks
  landing on the board behind the panel. The guard fired for real on the
  nineteenth of a twenty-board run -- the row expander simply did not take --
  and cost a re-run of three rows instead of two unexplained clicks on a map.

  **The coordinates in the table above are stale.** On a 1920x1080 client the
  confirmation dialog is centred: field at (960, 551), OK at (862, 605), banner
  at (960, 453). The script derives all three from the client rect.
- **No board metadata is readable, and that has not changed** -- asset count,
  size and last-modified are not exposed anywhere the driver can see. What
  changed is that *what you wrote down when you pasted it* is now a place
  rather than a habit: `citysmith boards index`, filled by the drivers
  themselves. The gap that remains is real and worth naming: the index knows
  what was **put** on a board and can never know what is **on** it now. Only
  reconciling against a hand-transcribed listing closes any of that, and the
  transcription is itself a person reading a screenshot.
