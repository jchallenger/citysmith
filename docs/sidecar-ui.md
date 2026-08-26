# A sidecar UI, and what the research settled before any of it was built

A second-monitor window that drives the whole pipeline — pick a town, build it,
watch it verify, paste it — plus a chat that turns a sentence into a small slab.

Written before the code, per the standing rule that cost this project several
sessions on paste anchoring: **spend twenty minutes on the host's community
before reverse-engineering the host.**

## 1. Prior art: there isn't any, and that is itself the finding

Every TaleSpire companion tool that exists is a **dice-roll relay**:
`roll20-to-talespire`, `tales-beyond` and `ddb_observeGameLog` all pipe rolls
from a character sheet into the dice tray. The map generators —
`taleslab` (Go), `TaleSpire_Generator` (Python), Baldrax's Houdini toolset —
are all headless: a CLI or a framework, no UI, no paste automation.

Nobody has built a sidecar that drives generation *and* ingestion. So there is
no design to copy and no protocol to reuse, but also nothing already solved
that we would be duplicating.

## 2. Symbiotes are real, first-party, and cannot do the one thing we need

TaleSpire has an official extension mechanism — **Symbiotes**, a web view in a
sidebar with a JavaScript API (`TS.players`, `TS.clients`, `TS.sync`). It is
documented, it takes a URL, and it is exactly the shape of thing this task
sounds like it wants.

**It cannot edit board state.** BouncyRock's own docs: *"Symbiotes can't do
anything that gets saved to the board."* No placing tiles, no placing assets,
no slabs. That is a deliberate, stated limitation with expansion promised
later.

So a Symbiote cannot paste a map, and the ingestion path stays what it already
is: `Ctrl+V` driven through `tools/ts.ps1`, with every finding in `CLAUDE.md`
about hold durations, scan codes and the ray-hit anchor still load-bearing.

Two things worth keeping, though:

- Symbiotes **load a URL**, including a local one. A web UI built for the
  second monitor can later be pointed at from inside the game as a read-only
  panel — the same artifact, no rewrite — once we want that.
- The API can read players and view mode and sync between clients. Nothing we
  need today; worth knowing it is there.

## 3. What that makes the architecture

**A stdlib `http.server` on `127.0.0.1`, serving hand-written HTML/CSS/JS, open
in a browser window on the second monitor.** Three reasons, in order of weight:

1. **The hard constraints allow nothing else.** Core is Python stdlib only, no
   dependencies, no build step. That rules out Electron, React, FastAPI and
   anything with a bundler. Tkinter is stdlib and would qualify —
2. **— but a web UI can be driven and screenshotted by the tools we already
   have.** "Verify functionality on screen" is cheap and repeatable through the
   Browser pane (`preview_start`, `read_page`, `computer`, `resize_window`);
   a Tkinter window needs desktop computer-use for every check. Given how much
   of this project's history is *reading a screenshot wrong*, the surface that
   is easiest to inspect wins.
3. It doubles as a Symbiote later (§2) and works on any OS for the half that
   is not paste.

**Paste is Windows-only and that is fine.** `ts.ps1` is PowerShell driving
Win32 synthetic input. The generate/build/verify half is cross-platform; the
paste half is not, and the UI should say so rather than offering a dead button.

## 4. `cli.py` is not the thin shell it claims to be

`README.md` and `CLAUDE.md` both say *"`cli.py` is a thin shell over the core
modules so a UI can be added without touching generation code."*

**`cmd_build` is 142 lines**, and they are not argument parsing: it derives the
chunk budget, builds the NPC population, calls the builder, plans the chunks,
writes them, writes the manifest, runs `verify`, formats the report and prints
the chunk table. A UI has two options against that: shell out to the CLI and
scrape stdout, or reimplement it and drift.

So the first task is not the UI. It is lifting that orchestration into the core
where both callers can share it.

## 5. The chat, and the rule it must not break

> **Claude is a translation layer, not a generation layer.** It maps natural
> language to generator parameters and writes prose. It must never produce
> coordinates, asset UUIDs, or slab bytes.

That is a hard constraint in `CLAUDE.md`, and a chat that "generates a slab
from a prompt" is exactly where it would get broken. The design that keeps it:

    sentence -> [Claude, strict tool schema] -> a SPEC -> [Python] -> a slab

The chat holds a **spec object** — room size, purpose, storeys, fixtures,
style, seed. Claude's only output is edits to that spec, through a strict tool
schema exactly like `ai._CITY_TOOL`. Python rebuilds deterministically from the
spec every turn. A bad model response gives a boring room, never a broken one,
and the same spec always gives the same slab.

This is also what makes **"modifications"** tractable, and the scope has to be
stated plainly:

- *"make it two storeys", "put the bar on the north wall"* — edits the spec and
  rebuilds. **This works.**
- *"add a fireplace to the room I already pasted"* — needs the board read back.
  `CLAUDE.md` records copy-out as **half solved**: it returns structure and
  never terrain, behaves as a thin slice at the elevation plane, and only one
  marquee can be driven per board. **Out of scope**, and the UI should not
  pretend otherwise.

So "modify an existing board" means *modify the thing this session built*, and
the UI keeps that history explicitly rather than implying it can see the game.

## 6. Getting a small slab into the game

For a whole town, the paste is 8 to 135 chunks and has to be driven —
`review.ps1 tiled`, camera straight down, one cursor cell.

For a **single small slab** there is a much cheaper path already built:
`ts.ps1 setclip` puts text on the clipboard, and the user presses `Ctrl+V`
themselves. No synthetic input, no camera discipline, no anchor rules, and
nothing can go wrong that the user did not do. The chat should default to this
and leave driving the game to the town-scale flow.

## 7. Safety, because this is a local server that shells out

- Bind **`127.0.0.1` only**, never `0.0.0.0`. A second monitor is still this
  machine.
- The API exposes **named operations with typed parameters**, never a command
  string. Nothing in a request reaches a shell.
- Paths from the browser are resolved against the project's own `out/`
  directory and rejected outside it.
- The API key stays server-side. The browser never sees it and never talks to
  Anthropic directly.

## Sources

- [Symbiotes Documentation — BouncyRock](https://symbiote-docs.talespire.com/)
- [Bouncyrock/symbiotes-examples](https://github.com/Bouncyrock/symbiotes-examples)
- [TaleSpire Dev Log 390 — Symbiotes Documentation](https://bouncyrock.com/news/articles/talespire-dev-log-390-symbiotes-documentation)
- [`talespire` topic on GitHub](https://github.com/topics/talespire)
- [johnfercher/taleslab](https://github.com/johnfercher/taleslab)
