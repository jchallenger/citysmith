"""TaleSpire slab codec (format V2).

Format verified empirically against real slabs from LuPro/SlabelFish and the
official BouncyRock spec (Bouncyrock/DumbSlabStats/format.md). Every UUID in
those fixtures resolves to a real asset in the local TaleWeaver index, and
decode->encode is byte-exact. See tests/test_slab.py.

Layout on the wire::

    base64( gzip( binary ) )

    binary:
      u32  magic         0xD1CEFACE
      u16  version       2
      u16  layoutCount
      u16  creatureCount (always 0 in v2)
      Layout[layoutCount]      20 bytes each: uuid(16) + u16 count + u16 reserved
      u64[sum(counts)]         packed asset placements, grouped by layout order
      u16  trailer       0x0000

    packed placement (u64, little endian):
      | 5 bits | 5 bits |  18 bits |  18 bits |  18 bits |
      | unused |  rot   |  scaledZ |  scaledY |  scaledX |

Positions are the asset's *min corner* in tile units; the wire value is
``round(pos * 100)``, giving 1/100-tile precision. ``rot`` is a step index in
0..23 where degrees = rot * 15.
"""

from __future__ import annotations

import base64
import gzip
import json
import struct
import uuid as _uuid
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Iterator

MAGIC = 0xD1CEFACE
VERSION = 2

_HEADER = struct.Struct("<IHHH")
_LAYOUT = struct.Struct("<HH")  # count, reserved (after the 16 uuid bytes)
_PLACEMENT = struct.Struct("<Q")

#: Coordinate scale factor -- 100 wire units per tile.
SCALE = 100

#: Max value representable in an 18-bit coordinate field.
_COORD_MAX = (1 << 18) - 1

#: Rotation steps in a full turn.
ROT_STEPS = 24
DEGREES_PER_STEP = 360 / ROT_STEPS  # 15.0

#: TaleSpire refuses to paste slabs whose *compressed* payload exceeds this.
MAX_COMPRESSED_BYTES = 30720


class SlabError(ValueError):
    """Raised when slab data is malformed or cannot be represented."""


@dataclass(frozen=True)
class Placement:
    """A single asset instance positioned in slab space.

    ``x``/``y``/``z`` are in tile units and measured from the asset's min
    corner. ``rot`` is a step index in 0..23.
    """

    asset_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rot: int = 0

    @property
    def degrees(self) -> float:
        return self.rot * DEGREES_PER_STEP

    def moved(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> "Placement":
        return Placement(self.asset_id, self.x + dx, self.y + dy, self.z + dz, self.rot)


@dataclass
class Slab:
    """A decoded slab: an unordered bag of placements."""

    placements: list[Placement] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.placements)

    def __iter__(self) -> Iterator[Placement]:
        return iter(self.placements)

    def add(self, placement: Placement) -> None:
        self.placements.append(placement)

    def extend(self, placements: Iterable[Placement]) -> None:
        self.placements.extend(placements)

    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Return ``(min_xyz, max_xyz)`` over placement origins.

        Note this is the bound over *origins*, not over occupied volume -- it
        does not account for each asset's footprint.
        """
        if not self.placements:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [p.x for p in self.placements]
        ys = [p.y for p in self.placements]
        zs = [p.z for p in self.placements]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def translated(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> "Slab":
        return Slab([p.moved(dx, dy, dz) for p in self.placements])

    def normalized(self) -> "Slab":
        """Shift so the minimum corner sits at the origin.

        Slab coordinates must be non-negative, so anything assembled with
        negative coordinates has to pass through here before encoding.
        """
        (mx, my, mz), _ = self.bounds()
        return self.translated(-mx, -my, -mz)

    def encode(self) -> str:
        return encode(self)

    # -- construction helpers -------------------------------------------------

    @classmethod
    def decode(cls, text: str) -> "Slab":
        return decode(text)


def _uuid_to_bytes(asset_id: str) -> bytes:
    """Serialize a UUID in .NET Guid order (first three groups little-endian)."""
    try:
        return _uuid.UUID(asset_id).bytes_le
    except (ValueError, AttributeError, TypeError) as exc:
        raise SlabError(f"invalid asset id {asset_id!r}") from exc


def _bytes_to_uuid(raw: bytes) -> str:
    return str(_uuid.UUID(bytes_le=raw))


def _pack_placement(p: Placement) -> int:
    rot = int(p.rot) % ROT_STEPS
    coords = []
    for label, value in (("x", p.x), ("y", p.y), ("z", p.z)):
        scaled = int(round(value * SCALE))
        if scaled < 0:
            raise SlabError(
                f"{label}={value} is negative; slab coordinates must be >= 0. "
                "Call Slab.normalized() before encoding."
            )
        if scaled > _COORD_MAX:
            raise SlabError(
                f"{label}={value} exceeds the 18-bit coordinate range "
                f"(max {_COORD_MAX / SCALE} tiles)."
            )
        coords.append(scaled)
    x, y, z = coords
    return x | (y << 18) | (z << 36) | (rot << 54)


def _unpack_placement(asset_id: str, value: int) -> Placement:
    return Placement(
        asset_id=asset_id,
        x=(value & _COORD_MAX) / SCALE,
        y=((value >> 18) & _COORD_MAX) / SCALE,
        z=((value >> 36) & _COORD_MAX) / SCALE,
        rot=(value >> 54) & 0x1F,
    )


def _gzip(data: bytes) -> bytes:
    """gzip ``data`` with the same container framing TaleSpire emits.

    ``gzip.compress`` stamps XFL=2 and OS=0xFF; real slabs carry XFL=0 and
    OS=0x0B. The deflate stream itself still differs from .NET's encoder --
    that is unavoidable and harmless, since only the decompressed bytes are
    meaningful. Building the container by hand keeps mtime at 0 so output is
    deterministic.
    """
    deflate = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = deflate.compress(data) + deflate.flush()
    header = b"\x1f\x8b\x08\x00" + struct.pack("<I", 0) + b"\x00\x0b"
    trailer = struct.pack("<II", zlib.crc32(data) & 0xFFFFFFFF, len(data) & 0xFFFFFFFF)
    return header + body + trailer


def encode(slab: Slab) -> str:
    """Encode a slab to the base64 string TaleSpire accepts on paste.

    Placements are grouped by asset id; groups are emitted in first-appearance
    order so encoding is deterministic and a decode->encode round trip of a
    real slab is byte-exact.
    """
    groups: dict[str, list[Placement]] = {}
    for p in slab.placements:
        groups.setdefault(p.asset_id, []).append(p)

    if not groups:
        raise SlabError("cannot encode an empty slab")

    for asset_id, items in groups.items():
        if len(items) > 0xFFFF:
            raise SlabError(
                f"asset {asset_id} has {len(items)} instances; the format caps a "
                "single layout at 65535. Split into multiple slabs."
            )

    out = bytearray()
    out += _HEADER.pack(MAGIC, VERSION, len(groups), 0)
    for asset_id, items in groups.items():
        out += _uuid_to_bytes(asset_id)
        out += _LAYOUT.pack(len(items), 0)
    for items in groups.values():
        for p in items:
            out += _PLACEMENT.pack(_pack_placement(p))
    out += b"\x00\x00"

    compressed = _gzip(bytes(out))
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise SlabError(
            f"slab compresses to {len(compressed)} bytes, over TaleSpire's "
            f"{MAX_COMPRESSED_BYTES}-byte limit ({len(slab.placements)} assets). "
            "Split it into several slabs."
        )
    return base64.b64encode(compressed).decode("ascii")


def decode(text: str) -> Slab:
    """Decode a slab string as copied from TaleSpire.

    Tolerates surrounding whitespace and the triple-backquote fencing that
    the community commonly wraps slabs in when sharing them.
    """
    cleaned = text.strip()
    # Shared slabs are usually fenced, sometimes more than once after a
    # round trip through chat clients.
    while cleaned.startswith("```") or cleaned.endswith("```"):
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    cleaned = "".join(cleaned.split())
    if not cleaned:
        raise SlabError("empty slab string")

    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception as exc:
        raise SlabError(f"not valid base64: {exc}") from exc

    try:
        data = gzip.decompress(raw)
    except Exception as exc:
        raise SlabError(f"not valid gzip data: {exc}") from exc

    if len(data) < _HEADER.size:
        raise SlabError("slab too short to contain a header")

    magic, version, layout_count, creature_count = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise SlabError(f"bad magic 0x{magic:08X}, expected 0x{MAGIC:08X}")
    if version != VERSION:
        raise SlabError(
            f"slab format version {version} is not supported (this codec "
            f"implements version {VERSION})"
        )
    if creature_count:
        raise SlabError(
            f"slab declares {creature_count} creatures; v2 slabs must declare 0"
        )

    offset = _HEADER.size
    layouts: list[tuple[str, int]] = []
    for i in range(layout_count):
        end = offset + 20
        if end > len(data):
            raise SlabError(f"truncated layout table at layout {i}")
        asset_id = _bytes_to_uuid(data[offset : offset + 16])
        count, _reserved = _LAYOUT.unpack_from(data, offset + 16)
        layouts.append((asset_id, count))
        offset = end

    slab = Slab()
    for asset_id, count in layouts:
        for _ in range(count):
            if offset + 8 > len(data):
                raise SlabError(f"truncated placement data for asset {asset_id}")
            (value,) = _PLACEMENT.unpack_from(data, offset)
            slab.add(_unpack_placement(asset_id, value))
            offset += 8

    return slab


def degrees_to_rot(degrees: float) -> int:
    """Snap an angle in degrees to the nearest of the 24 rotation steps."""
    return int(round(degrees / DEGREES_PER_STEP)) % ROT_STEPS


#: Field names of LordAshes' multi-slab JSON, as read off the plugin's own
#: documentation. Kept in one place because they are *their* names, not ours,
#: and a typo here fails silently as "the plugin did nothing".
_MULTISLAB_FIELDS = ("autoDrop", "dropX", "dropY", "dropZ", "slabs")


def multislab(entries, drop=(0.0, 0.0, 0.0), auto_drop: bool = True) -> str:
    """Serialise slabs as a multi-slab document for the TaleSpire paste plugins.

    **This exists so the map does not have to be aimed.** A vanilla ``Ctrl+V``
    is cursor-anchored: the slab arrives at whatever the cursor's ray hits, so
    a map cut into chunks only assembles if every chunk presents the identical
    bounding box and every paste is made at one cell with the camera straight
    down. That is what the registration markers, the even-extent rule and the
    written paste order are all for.

    LordAshes' ``MultiPasteSlabsPlugin`` / ``SlabPlugin_CCM`` read a JSON
    document instead and place each slab at a stated position, so none of that
    machinery is needed on that path::

        {"autoDrop": true, "dropX": 0, "dropY": 0, "dropZ": 0,
         "slabs": [{"code": "<base64>", "offsetX": 0, "offsetY": 0, "offsetZ": 0}]}

    ``entries`` is an iterable of ``Slab`` or ``(Slab, (x, y, z))``. Chunks cut
    by :meth:`Builder.chunk_plan` with ``register=False`` keep their true
    in-map coordinates, so their offsets are all zero and ``drop`` alone moves
    the town -- which is the whole point, and why this is a dozen lines rather
    than a coordinate system.

    The plugin is third-party and breaks on TaleSpire updates from time to
    time; the chunk files remain the default so a vanilla install still works.
    """
    slabs = []
    for entry in entries:
        piece, off = entry if isinstance(entry, tuple) else (entry, (0.0, 0.0, 0.0))
        ox, oy, oz = off
        slabs.append({
            "code": encode(piece),
            "offsetX": round(float(ox), 2),
            "offsetY": round(float(oy), 2),
            "offsetZ": round(float(oz), 2),
        })
    dx, dy, dz = drop
    doc = {
        "autoDrop": bool(auto_drop),
        "dropX": round(float(dx), 2),
        "dropY": round(float(dy), 2),
        "dropZ": round(float(dz), 2),
        "slabs": slabs,
    }
    assert set(doc) == set(_MULTISLAB_FIELDS)
    return json.dumps(doc, indent=1)
