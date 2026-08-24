# Slab format V2

The slab format is **BouncyRock's**, not ours. This file used to hold a copy of
their published specification; it does not any more, because republishing
someone else's document is not ours to do. What follows is our own working
description, written from the implementation in `citysmith/slab.py` and
verified against real slabs in `tests/fixtures/`.

**Canonical source:** BouncyRock's own slab format documentation, published
with TaleSpire. Go there for the authoritative wording.

## Shape of the thing

A slab on the clipboard is text: base64 of a gzip stream, sometimes fenced in
triple backticks. Decompressed it is a small binary record.

```
u32  magic          0xD1CEFACE
u16  version        2
u16  layoutCount
u16  creatureCount  always 0 in a v2 slab
Layout[layoutCount] uuid assetKind (16 bytes, .NET byte order)
                    u16  count
                    u16  reserved
u64[sum(counts)]    the placements, grouped by layout, in layout order
u16  trailer        0x0000
```

Each placement packs into one little-endian `u64`:

```
 most significant -------------------------------- least significant
| 5 unused | 5 rot | 18 bits z | 18 bits y | 18 bits x |
```

## The parts that cost us time

These are the details that are easy to get wrong, and each one cost a session
to find. They are the reason this file exists at all.

- **A position is the asset's minimum corner, after rotation** — not its
  centre, and not its pre-rotation corner. The footprint swaps axes on odd
  quarter turns. `build.rotated_footprint()` implements it and
  `test_placement_matches_talespire_measurements` guards it against ground
  truth copied out of the game.
- **Wire value is `round(position * 100)`**, so coordinates are stored to a
  hundredth of a tile. 1 world unit = 1 tile = 5 ft.
- **Rotation is a step index 0..23**; degrees are `rot * 15`.
- **The compressed payload must stay under 30,720 bytes.** This is the single
  hardest constraint on the whole project and the reason a town is emitted as
  chunks rather than one file.
- **Decode → encode reproduces the original *binary* byte for byte, but not
  the original base64.** .NET's deflate and zlib's differ in their choices.
  That is expected and harmless; `tests/test_slab.py` asserts on the binary,
  never on the text.
- **The `.NET byte order` on the layout uuid is not the usual one.** The first
  three fields of a GUID are little-endian on the wire. Reading it as a plain
  big-endian uuid gives you a valid-looking id that matches no asset.

## Where the numbers came from

`tests/fixtures/*.slab` are real slabs, and the codec is tested against them
rather than against our own output — a codec tested only on what it wrote is a
codec that agrees with itself. See `tests/test_slab.py`.
