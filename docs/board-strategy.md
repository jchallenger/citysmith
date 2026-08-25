# Boards: interior, exterior, and getting the party between them

What the TaleSpire community actually recommends, gathered before deriving
anything ourselves — `CLAUDE.md`'s standing rule, after several sessions were
spent reverse-engineering paste anchoring that a BepInEx plugin had solved
years earlier.

Sources are listed at the end. Where a finding agrees or disagrees with a
decision citysmith has already made, that is called out, because the point of
looking is to change our mind or to stop worrying.

## 1. A board is not a slab, and they fail differently

A **board** carries the whole build plus its minis and is the thing a session
happens on. A **slab** carries only what was selected, no creatures, and is the
thing that travels. citysmith emits slabs and pastes them onto boards, which is
the only ingestion path there is.

The consequence the community states plainly and we already live with: boards
"aren't as easy to use if you're trying to mix and match together pieces",
which is precisely why the generator's unit of work is a slab and the board is
just where it lands.

## 2. Multiple floors: the community splits them across BOARDS

This is the strongest finding and it bears directly on a decision already made.

The cut-away (the green elevation track — the same widget `ts.ps1 elevplane`
drives) is what you are supposed to use to see inside a building, and it is
reported as unreliable: it "depends on the camera height and position, which
can mean sometimes a roof slips in if you keep the camera flat", and it is
worst on exactly the big buildings that need it most.

So builders **put each floor on its own board** — "one board titled 'Tavern F1'
and the next 'Tavern F2'" — and the Steam building guide gives the same advice
in general form: *"Use a second board for different levels/underground."*

**citysmith solves the same problem a third way, and the community's experience
supports the reasoning.** `interior.plan(spread=True)` lays levels **side by
side** on one board rather than stacking them, because "TaleSpire has no way to
hide an upper floor, so a stacked three-storey inn is one visible attic and two
rooms the camera has to be flown inside to use". The community independently
reached "stacked floors cannot be seen into"; they route around it with more
boards, we route around it with more floor area.

Which is better is a real question and not settled here:

| | side by side (ours) | board per floor (theirs) |
|---|---|---|
| whole building at one glance | **yes** | no |
| stairs mean anything | no — they go nowhere | no — the transition is a board switch |
| board count per building | 1 | one per floor |
| paste cost | one set of chunks | a set per floor |
| a party split across floors | one board, both visible | two boards, GM on one |

The last row is the interesting one and argues for ours. The rest of the table
is close enough that this is not worth changing on someone else's say-so.

## 3. "Don't build directly on the ground"

The Steam guide lists this under height management, alongside "add cutout views
if possible". No source found states the reason, and it is worth being explicit
that it is unexplained rather than quietly adopting it.

The plausible reading is that leaving air under a build lets the cut-away take
a slice *below* the ground floor, and lets a cellar be added later without
lifting everything. citysmith builds from y=0 up and its registration and datum
checks (`verify.chunk_datum`) actively require a chunk to reach y=0, so
adopting this would mean changing machinery that exists for a measured reason.
**Not adopted. Recorded as understood-but-declined.**

## 4. Board size: big enough to navigate

"Make your board big enough for navigation" — leave room around the built area
so the camera can get outside it. citysmith's `--margin` and the edge taper
already do this; the finding is only that it matters to other people too.

## 5. Getting the party between boards

There is a real mechanism and it is worth knowing before designing any
scene-to-scene workflow:

- **The GM summons.** Players menu → *"Summon Players To This Board"*. The
  caveat is that it takes **everyone loaded in the campaign**, so it is not
  usable for moving half a party.
- **Or the GM names the board and players navigate.** `Space` → the top-left
  icon → pick the board from the list. That is the same list `ts.ps1 boards`
  drives and the same list whose sixteen-character clip
  `docs/scenes.md` designs names around — so the naming scheme is load-bearing
  for the *player* experience, not just for our own bookkeeping.
- **A player who arrives on a board with no GM is locked to a fixed view**
  until a GM joins.
- **Minis are campaign-level, not board-level.** A mini can be marked "unique"
  so "only one can exist at a time, which saves time when moving between
  boards".

That last point matters to us. `CLAUDE.md` records that `creatureCount` is
always 0 in a v2 slab, so a scene pastes four *marks* on the floor and the
minis go on by hand. Because minis are campaign-level and can be unique, the
hand step is **once per campaign, not once per scene** — the same mini walks
onto every board. The marks are still the right output; the cost of them is
lower than the note implies.

## 6. What this changes

Nothing is rebuilt on the strength of this. Three things are now decided rather
than assumed:

- **Levels side by side stays.** The community hit the same wall and took a
  different door out of it; ours keeps the building readable in one glance and
  handles a split party better.
- **The board naming scheme is more important than it looked.** It is what a
  *player* reads when a GM says "go to the tavern board", not only what we use
  to find a board again.
- **The party-marks approach is cheaper than recorded**, because minis persist
  across boards.

And one thing is now on the list: `docs/scenes.md` should say how a GM moves the
party to a scene board, because that is the step after everything citysmith
does and it is currently unwritten.

## Sources

- [TaleSpire Guide to Sharing/Using Slabs and Boards — Tales Tavern](https://talestavern.com/talespire-guide-to-sharing-copying-using-slabs-and-boards/)
- [The Ultimate Player's Guide to TaleSpire — Tales Tavern](https://talestavern.com/the-ultimate-players-guide-to-talespire/)
- [Building Tips in TaleSpire — Steam Community guide](https://steamcommunity.com/sharedfiles/filedetails/?id=2182163851)
- [2 floor mapping (Tavern) — TaleSpire Q&A](https://steamcommunity.com/app/720620/discussions/0/2260186248421820724/)
- [Impossible camera movement and "hiding" tiles — TaleSpire feedback](https://feedback.talespire.com/p/impossible-camera-movement-and-hiding-tiles)
- [Green Elevation Cut Away Bug — TaleSpire issue tracker](https://github.com/Bouncyrock/TaleSpire-Beta-Public-Issue-Tracker/issues/543)
