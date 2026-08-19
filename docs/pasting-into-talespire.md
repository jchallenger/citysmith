# Pasting a slab into TaleSpire

Everything here was confirmed in-game. The interaction has several failure
modes that look exactly like a broken tool, so read this before deciding
citysmith emitted a bad slab.

## The one-paragraph version

Run TaleSpire **windowed**. Enter build mode. Copy the entire contents of a
`.slab.txt` file. Press `Ctrl+V` — the slab appears in hand at the cursor,
snapped to the grid. Commit it with a left mouse press that you **hold for
about 0.2 s**. It stays in hand afterwards as a repeat stamp; **right-click**
to clear the hand. For a multi-chunk map, paste and commit every chunk at the
**same grid cell without moving the camera**.

## Run windowed, not exclusive fullscreen

Exclusive fullscreen captures as a black frame and drops window focus
constantly — synthetic input and screenshots both stop working. `Alt+Enter`
toggles it, and the choice persists to the `Windowed Mode` setting.

Size the window to fit the desktop. The bottom hint bar shows the contextual
key bindings for whatever tool you are holding and is the best control
reference in the game; if the window overflows the desktop it is clipped
off-screen.

## The hold is not optional

**A pasted slab commits on a left press with a real hold: down, ~0.2 s, up.**
A zero-duration click is swallowed by Unity's input polling. This is the single
most expensive lesson in this project — it made paste look completely broken
for an entire session. The same applies to keystrokes: hold them briefly rather
than tapping.

If you are driving the game programmatically, issue every click as explicit
mouse-down → short wait → mouse-up. Never press-and-hold on the window title
bar, that drags the window; and resizing the window from outside stalls Unity's
renderer until the mouse moves back over the client area.

## Clearing the hand

**Right-click clears the hand.** Switching to another tool on the build toolbar
also drops it.

`Escape` does **not** clear the hand — it backs out toward the main menu
instead. Neither does toggling `B`.

## Paste is cursor-anchored, not coordinate-anchored

The absolute coordinates inside a slab do not decide where it lands. The slab
arrives at the **cursor**, snapped to the global grid, anchored by its own
bounding-box corner. Copying a placed slab back out returns it normalised to
its own corner, which confirms the anchoring is relative.

This is the whole reason multi-chunk boards need care. `build` cuts a town on a
spatial grid; each chunk covers a different region of the map and would
otherwise have a different bounding-box corner, so pasting them all at one
anchor would scatter them. `Builder.chunk_plan()` therefore adds one
**registration marker** tile at the *whole map's* minimum corner to every
chunk, giving them all an identical origin.

Chunk files are named for the grid cell they start at — `mytown-r08c12+126`
begins at row 8, column 12 and spans 126 further cells. `build` prints a table
of which chunk covers which tile range.

The procedure that follows from that:

1. Position the camera. **Do not move it again** until every chunk is down.
2. Paste a chunk, commit with a held left press.
3. Right-click to clear, paste the next, commit **over the same grid cell**.
4. Repeat for every chunk. **Order does not matter** — they share an origin,
   so each lands where it belongs regardless of sequence.
5. The registration markers stack in a single corner cell. Delete them
   afterwards if you care; they are one tile.

You may paste a **subset**. Each chunk is a contiguous run of grid cells, so
pasting two of five gives you a connected swathe of town on an otherwise empty
board. The split is chosen by the slab byte budget rather than by district, so
check the printed chunk map to see which file covers the ground you want.

Chunks holding nothing but open country are not written at all, so a numbering
gap in the filenames is expected, not a missing file.

If the chunks come out offset from each other, the camera moved or a chunk was
committed on a different cell — not a generator bug.

## Never stack overlapping props in a slab

**TaleSpire silently drops props whose colliders overlap on paste.** No error,
no warning; the extra prop simply does not exist in the result. This is the
community "missing parts" bug.

It was diagnosed here with a one-tree probe: two-piece pines built from a stump
plus a canopy lost roughly a third of their canopies on paste. Trees are now
single-piece assets. If a build looks like it is missing scenery, suspect
overlapping colliders before suspecting the encoder.

## `talespire://` links do not import boards

There is no link-based, file-drop, or API ingestion path for a board. Slab
paste is the only way in. Plan around it.

## Bindings worth knowing

| Key | Does |
|---|---|
| `B` | toggle build mode |
| `Ctrl+V` | paste slab into hand |
| left press (held) | commit what is in hand |
| right-click | clear the hand |
| `Ctrl+Z` | undo |
| `X` + drag | select |
| left-click | pick up a placed asset |
| middle-drag | rotate camera |
| scroll | zoom |
| `Shift` + scroll | raise / lower |
| `F2` | recentre |
| `Space` | menus |
| `F1` | help — a video overlay; it does not screen-capture |
| `Alt+Enter` | toggle windowed / fullscreen |

## If you are automating the paste

- Allowlist **`textinputhost.exe`** alongside `TaleSpire.exe`. It steals
  foreground and makes every synthetic click and keystroke get refused.
- Everything above about held presses applies doubly.
- Judge the result by **decoding the slab and measuring it**, not by reading a
  screenshot. See `docs/asset-conventions.md`.
