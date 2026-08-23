---
name: talespire-boards
description: Manage the boards inside a TaleSpire campaign — create a board, name or rename one, switch between them, and lay out a campaign with a board per location. Use when the user wants a board per town/dungeon/region, asks to name or rename a board, asks which board something was pasted onto, wants to organise or tidy a campaign's boards, or is about to paste a generated map and needs somewhere to put it.
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

**The chevron next to `...` is a saved-state indicator, not a board list.** It
toggles "No unsaved changes" and does nothing else. It was the obvious thing to
try and it is wrong.

### Switch

```bash
pwsh tools/ts.ps1 boards -Name list
```

`Space` raises the HUD, then the **top icon of the left-hand column** opens
*Campaign Boards*: every board, with a play arrow to jump to it.

Three things about that list:

- **`Space` is a toggle, and `ts.ps1 boards` does not handle that.** It has
  failed twice in one session for this reason: the HUD was already up, `Space`
  closed it, and the click landed on the board. The reliable form is two
  separate calls — check the HUD state in a screenshot, then only press `Space`
  if it is down, then click the icon:
  ```bash
  pwsh tools/ts.ps1 click -X <clientX+17> -Y <clientY+57> -Hold 0.3
  ```
- **The rows are plain alphabetical.** The current board is highlighted in
  place, not floated to the top — an earlier note here said otherwise and it
  was wrong; `Pelvesthollow` merely happened to sort first. Renaming still
  reshuffles the list, so read the rows off the screenshot and never reuse a
  position from earlier in a session. Rows start at client y=200, 42 apart,
  play arrow at x=360, row expander at x=79.
- **Switching to a large board takes tens of seconds.** A 387k-asset town needs
  ~30 s before it is safe to click anything. Wait, then screenshot to confirm
  the title bar changed before doing anything else.

### Delete

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

**You cannot delete the board you are standing on** — its row shows a different
icon set and no delete. Delete everything else first, then make or switch to
another board and delete the last one. Because the list is alphabetical,
repeatedly deleting the *top* row is the simplest loop, and it needs no
re-reading of positions between iterations.

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

- A location board gets the location's name, exactly as the source calls it:
  `Graybank`, `East Tradebourne`. It has to match what the GM will say at the
  table and what the export is filed under.
- A throwaway gets a `Probe - ` prefix: `Probe - East Tradebourne rampart`.
  That sorts them together in the list and makes them obviously deletable.
- Leave `Unknown Realm N` alone on boards you did not make. One of them is the
  campaign's default empty board and others may be someone's work in progress.

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

- **`ts.ps1 boards` is unreliable** — it has failed twice and worked once, all
  down to the `Space` toggle. Until it checks HUD state first, drive the two
  steps separately and screenshot between them.
- **Delete is not scripted**, only documented above. The coordinates are
  verified over eight consecutive deletions, but nothing guards against a
  mis-click landing on the play arrow and switching boards mid-loop, so
  screenshot every few iterations.
- **No board metadata is readable.** Asset count, size and last-modified are
  not exposed anywhere the driver can see, so the only record of what is on a
  board is what you wrote down when you pasted it. This is why deleting is
  dangerous: the list tells you a name and nothing else.
