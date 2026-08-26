"""Read the creature state TaleSpire persists to disk, so a token's position is
a measurement rather than a reading off a screenshot.

TaleSpire writes one file per creature under

    %USERPROFILE%/AppData/LocalLow/BouncyRock Entertainment/TaleSpire/
        primary/Persistence/<campaign>/<board>/Creatures/<id>

zlib-deflated (`78 01`), wrapping a blob that opens with the same
0xD1CEFACE magic as a slab -- version 4 here rather than 2. Enough of it is
understood to locate the position, which is what a movement test needs:

    u16 len + <len> bytes   a ':' + 32 hex content id (the mini's asset)
    16 bytes                a GUID, identical across creatures on this board
    f32 x, f32 y, f32 z     the creature's position, in tile units

The rest is not decoded and does not need to be. This is a *read-only* probe --
nothing here writes back into TaleSpire's store.

    python tools/creature_state.py            # dump every creature, newest board
    python tools/creature_state.py --watch 30 # poll and print changes
"""

from __future__ import annotations

import argparse
import os
import pathlib
import struct
import sys
import time
import zlib

ROOT = pathlib.Path(os.environ["USERPROFILE"]) / (
    "AppData/LocalLow/BouncyRock Entertainment/TaleSpire/primary/Persistence"
)


def board_dirs() -> list[pathlib.Path]:
    """Every board directory that has a Creatures folder, newest first."""
    out = []
    for campaign in ROOT.iterdir():
        if not campaign.is_dir():
            continue
        for board in campaign.iterdir():
            if board.is_dir() and (board / "Creatures").is_dir():
                out.append(board)
    return sorted(out, key=lambda p: (p / "Creatures").stat().st_mtime, reverse=True)


def parse(blob: bytes) -> dict:
    """Pull the content id and position out of one decompressed creature blob."""
    magic = struct.unpack_from("<I", blob, 4)[0]
    if magic != 0xD1CEFACE:
        raise ValueError("not a creature blob (magic %08x)" % magic)
    version = struct.unpack_from("<H", blob, 8)[0]

    # Find the content id by its shape rather than by a fixed offset: a u16
    # length followed by that many bytes starting with ':'. Fixed offsets are
    # what this project keeps getting wrong when a format gains a field.
    idx = blob.find(b":")
    while idx > 2:
        n = struct.unpack_from("<H", blob, idx - 2)[0]
        if n and idx + n <= len(blob) and blob[idx : idx + 1] == b":":
            break
        idx = blob.find(b":", idx + 1)
    else:
        raise ValueError("no content id found")

    content = blob[idx : idx + n].decode("ascii", "replace")
    after = idx + n
    guid = blob[after : after + 16].hex()
    x, y, z = struct.unpack_from("<fff", blob, after + 16)
    return {"version": version, "content": content, "guid": guid, "pos": (x, y, z)}


def read_board(board: pathlib.Path) -> dict[str, dict]:
    """Newest state first. The store is content-addressed and append-only -- a
    move writes a *new* file whose name is a hash of the state, not a creature
    id -- so the filename order means nothing and the newest file is the
    current position. Sorting by name and reading the last row is how three
    saved states of one mini were first read as three separate minis."""
    out = {}
    for f in sorted((board / "Creatures").iterdir(),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            rec = parse(zlib.decompress(f.read_bytes()))
        except Exception as exc:  # a partially written file during a save
            out[f.name] = {"error": str(exc)}
            continue
        rec["mtime"] = f.stat().st_mtime
        out[f.name] = rec
    return out


def show(board: pathlib.Path, state: dict[str, dict]) -> None:
    print("board %s  (%d saved states, newest first)" % (board.name, len(state)))
    for i, (cid, rec) in enumerate(state.items()):
        if "error" in rec:
            print("  %s  !! %s" % (cid[:12], rec["error"]))
            continue
        x, y, z = rec["pos"]
        print(
            "  %s %s  pos (%8.3f, %7.3f, %8.3f)  %s"
            % (
                "CURRENT" if i == 0 else "       ",
                cid[:12],
                x,
                y,
                z,
                time.strftime("%H:%M:%S", time.localtime(rec["mtime"])),
            )
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0.0, help="poll for N seconds")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--board", default=None, help="board id (default: newest)")
    args = ap.parse_args()

    boards = board_dirs()
    if not boards:
        print("no board with creatures found under", ROOT)
        return 1
    board = next((b for b in boards if b.name.startswith(args.board)), boards[0]) if args.board else boards[0]

    state = read_board(board)
    show(board, state)
    if not args.watch:
        return 0

    print("\nwatching for %.0f s ..." % args.watch)
    t0 = time.time()
    while time.time() - t0 < args.watch:
        time.sleep(args.interval)
        new = read_board(board)
        for cid, rec in new.items():
            old = state.get(cid)
            if old is None:
                print("  +%6.1fs  NEW %s at %s" % (time.time() - t0, cid[:12], rec.get("pos")))
            elif old.get("pos") != rec.get("pos"):
                print(
                    "  +%6.1fs  MOVE %s  %s -> %s"
                    % (time.time() - t0, cid[:12], old.get("pos"), rec.get("pos"))
                )
        for cid in state:
            if cid not in new:
                print("  +%6.1fs  GONE %s" % (time.time() - t0, cid[:12]))
        state = new
    return 0


if __name__ == "__main__":
    sys.exit(main())
