"""Slab codec tests against real TaleSpire slabs.

The fixtures in ``tests/fixtures`` are genuine slabs copied out of TaleSpire
(from the LuPro/SlabelFish test corpus). They are the ground truth for this
codec: if the format ever changes, these fail first.
"""

from __future__ import annotations

import base64
import gzip
import pathlib
import random

import pytest

from citysmith.slab import (
    MAX_COMPRESSED_BYTES,
    Placement,
    Slab,
    SlabError,
    decode,
    degrees_to_rot,
    encode,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ALL_FIXTURES = sorted(FIXTURES.glob("*.slab"))


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_fixtures_present():
    assert ALL_FIXTURES, "no slab fixtures found"


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.stem)
def test_decode_every_fixture(path: pathlib.Path):
    slab = decode(path.read_text(encoding="utf-8"))
    assert len(slab) > 0
    for p in slab:
        assert p.x >= 0 and p.y >= 0 and p.z >= 0
        assert 0 <= p.rot < 24


def payload(slab_text: str) -> bytes:
    """The decompressed slab binary -- what TaleSpire actually reads."""
    cleaned = slab_text.strip().strip("`").strip()
    return gzip.decompress(base64.b64decode(cleaned))


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.stem)
def test_round_trip_reproduces_payload_exactly(path: pathlib.Path):
    """decode -> encode must reproduce the slab binary byte for byte.

    This pins header, layout grouping order, placement packing and trailer all
    at once. The gzip *container* is deliberately not compared: .NET's deflate
    encoder and zlib's produce different (equally valid) streams for the same
    input, so only the decompressed bytes are a meaningful invariant.
    """
    original = path.read_text(encoding="utf-8")
    assert payload(encode(decode(original))) == payload(original)


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.stem)
def test_reencoding_is_stable(path: pathlib.Path):
    """Our own output must be a fixed point: encoding it again changes nothing."""
    once = encode(decode(path.read_text(encoding="utf-8")))
    assert encode(decode(once)) == once


def test_single_tile_decodes_to_known_asset():
    slab = decode(read("castle_floor_1x1.slab"))
    assert len(slab) == 1
    p = slab.placements[0]
    # "Castle Floor 1" in the Medieval Fantasy pack.
    assert p.asset_id == "32cfd208-c363-4434-b817-8ba59faeed17"
    assert (p.x, p.y, p.z, p.rot) == (0.0, 0.0, 0.0, 0)


def test_xyz_fixture_has_expected_axes():
    """The xyz fixture places one tile 4 units along each axis from origin."""
    slab = decode(read("xyz.slab"))
    coords = {(p.x, p.y, p.z) for p in slab}
    assert coords == {(0, 0, 0), (4, 0, 0), (0, 4, 0), (0, 0, 4)}


def test_rot_fixture_covers_cardinal_rotations():
    slab = decode(read("rot.slab"))
    assert {p.rot for p in slab} == {0, 6, 12, 18}
    assert {p.degrees for p in slab} == {0.0, 90.0, 180.0, 270.0}


def test_multi_asset_fixture_groups_correctly():
    slab = decode(read("castle_tavern_wprop.slab"))
    counts: dict[str, int] = {}
    for p in slab:
        counts[p.asset_id] = counts.get(p.asset_id, 0) + 1
    assert sum(counts.values()) == len(slab)
    assert len(counts) == 4


def test_large_fixture():
    slab = decode(read("validation_slab.slab"))
    assert len(slab) > 1000


# -- encoding behaviour -------------------------------------------------------

ASSET_A = "32cfd208-c363-4434-b817-8ba59faeed17"
ASSET_B = "e62c6746-cecf-46bf-8b20-f81738f1d220"


def test_encode_decode_synthetic():
    slab = Slab([
        Placement(ASSET_A, 0, 0, 0, 0),
        Placement(ASSET_B, 1.25, 0.5, 2.75, 7),
        Placement(ASSET_A, 3, 0, 4, 23),
    ])
    out = decode(encode(slab))
    assert {(p.asset_id, p.x, p.y, p.z, p.rot) for p in out} == {
        (ASSET_A, 0, 0, 0, 0),
        (ASSET_B, 1.25, 0.5, 2.75, 7),
        (ASSET_A, 3, 0, 4, 23),
    }


def test_fractional_precision_is_hundredths():
    slab = Slab([Placement(ASSET_A, 1.23, 0.45, 6.78, 0)])
    p = decode(encode(slab)).placements[0]
    assert (p.x, p.y, p.z) == (1.23, 0.45, 6.78)


def test_negative_coordinates_rejected_with_actionable_message():
    slab = Slab([Placement(ASSET_A, -1, 0, 0, 0)])
    with pytest.raises(SlabError, match="normalized"):
        encode(slab)


def test_normalized_shifts_to_origin():
    slab = Slab([
        Placement(ASSET_A, -2, 1, -5, 0),
        Placement(ASSET_A, 3, 4, 0, 0),
    ]).normalized()
    assert slab.bounds()[0] == (0.0, 0.0, 0.0)
    encode(slab)  # must not raise


def test_empty_slab_rejected():
    with pytest.raises(SlabError, match="empty"):
        encode(Slab())


def test_coordinate_overflow_rejected():
    with pytest.raises(SlabError, match="18-bit"):
        encode(Slab([Placement(ASSET_A, 99_999, 0, 0, 0)]))


def test_rotation_wraps():
    slab = Slab([Placement(ASSET_A, 0, 0, 0, 25)])
    assert decode(encode(slab)).placements[0].rot == 1


def test_oversized_slab_rejected():
    # Genuinely random placements so the data does not compress away.
    rng = random.Random(1)
    placements = [
        Placement(
            ASSET_A,
            rng.randrange(2000) / 100,
            rng.randrange(2000) / 100,
            rng.randrange(2000) / 100,
            rng.randrange(24),
        )
        for _ in range(40_000)
    ]
    with pytest.raises(SlabError, match=str(MAX_COMPRESSED_BYTES)):
        encode(Slab(placements))


def test_layout_instance_cap_rejected():
    placements = [Placement(ASSET_A, i % 900, 0, (i // 900) % 900, 0) for i in range(70_000)]
    with pytest.raises(SlabError, match="65535"):
        encode(Slab(placements))


def test_encode_is_deterministic():
    slab = Slab([Placement(ASSET_A, 1, 2, 3, 4), Placement(ASSET_B, 0, 0, 0, 0)])
    assert encode(slab) == encode(slab)


# -- decoding robustness ------------------------------------------------------

def test_backtick_fenced_slab_accepted():
    raw = read("castle_floor_1x1.slab").strip()
    assert len(decode(f"```{raw}```")) == 1


def test_whitespace_and_newlines_tolerated():
    raw = read("castle_floor_1x1.slab").strip()
    wrapped = "\n".join(raw[i : i + 20] for i in range(0, len(raw), 20))
    assert len(decode(f"  \n{wrapped}\n  ")) == 1


@pytest.mark.parametrize(
    "bad, match",
    [
        ("", "empty"),
        ("not base64!!!", "base64"),
        ("aGVsbG8gd29ybGQ=", "gzip"),
    ],
)
def test_malformed_input_rejected(bad: str, match: str):
    with pytest.raises(SlabError, match=match):
        decode(bad)


def test_degrees_to_rot():
    assert degrees_to_rot(0) == 0
    assert degrees_to_rot(90) == 6
    assert degrees_to_rot(180) == 12
    assert degrees_to_rot(270) == 18
    assert degrees_to_rot(360) == 0
    assert degrees_to_rot(-90) == 18
    assert degrees_to_rot(88) == 6  # snaps to nearest step
