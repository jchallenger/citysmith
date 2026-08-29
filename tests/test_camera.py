"""The camera model, and the two things it must never quietly get wrong.

Every camera command in this toolkit is *relative*, so a session that only
issues them ends up over the void. `citysmith.camera` is the model that makes
"where will this frame land" arithmetic instead of a flight, and
`citysmith.camerafit` is how its constants were measured off the running game
rather than guessed.

Two properties are load-bearing and both have a test here that would have
caught a real error:

* **The projection has to reproduce the anchor slide.** `height x cot(pitch)`
  is measured, in game, and written down in CLAUDE.md; the model derives it
  from the frustum. If the two ever disagree, the model is the one that is
  wrong, because only one of them was measured.
* **The fitter has to recover a pose it was given.** It did not, at first: the
  decomposition assumed a right-handed camera basis and ours is not, which
  negated the pitch and the eye height *together* -- so `dist`, their ratio,
  came out exactly right and every other field read as correct.
"""

from __future__ import annotations

import math

import pytest

from citysmith import camera as C
from citysmith import camerafit as F


# --------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------


def test_the_model_reproduces_the_measured_anchor_slide():
    """A paste anchors on the cursor's ray hit, so an obstruction moves it.

    CLAUDE.md records the slide as `height x cot(pitch)`, measured in game on a
    36x30 crop. The model is not allowed to disagree with it.
    """
    lens = C.Lens(1920, 1080, 30.0)
    for pitch in (20.0, 45.0, 60.0, 78.0):
        cam = C.Camera(lens, C.Pose(0, 0, 0, 40, 0, pitch))
        for height in (0.5, 1.5, 3.0):
            want = height / math.tan(math.radians(pitch))
            assert cam.anchor_slide(height) == pytest.approx(want, abs=1e-9)


def test_a_vertical_pitch_is_what_makes_a_paste_safe():
    """Which is why every paste in review.ps1 is made looking straight down."""
    lens = C.Lens(1920, 1080, 30.0)
    steep = C.Camera(lens, C.Pose(0, 0, 0, 40, 0, 90))
    shallow = C.Camera(lens, C.Pose(0, 0, 0, 40, 0, 30))
    assert steep.anchor_slide(1.5) == pytest.approx(0.0, abs=1e-9)
    assert shallow.anchor_slide(1.5) > 2.0


def test_project_and_unproject_are_inverses_on_the_ground():
    lens = C.Lens(1600, 900, 30.0)
    cam = C.Camera(lens, C.Pose(12, -7, 0, 55, 143, 52))
    for sx, sy in ((800, 450), (200, 700), (1400, 620), (60, 880)):
        hit = cam.ground_hit(sx, sy)
        assert hit is not None
        back = cam.project(hit[0], 0.0, hit[1])
        assert back == pytest.approx((sx, sy), abs=1e-6)


def test_the_footprint_is_bounded_even_with_the_horizon_in_shot():
    """A frame containing the horizon is ordinary, not an error.

    The distance haze in one has twice been read as a chunk seam, so the model
    is asked directly rather than inferred from a screenshot.
    """
    lens = C.Lens(1920, 1080, 30.0)
    cam = C.Camera(lens, C.Pose(0, 0, 0, 40, 0, 8))
    assert cam.sees_horizon()
    pts = cam.footprint()
    assert len(pts) == 4
    assert all(math.isfinite(v) for p in pts for v in p)


def test_scale_across_a_row_is_constant_and_down_the_frame_is_not():
    """The whole content of "paste a row at one screen Y, then move".

    CLAUDE.md measured two pads 720 px apart vertically coming out ~57 px apart
    horizontally. The model says why: ground scale is constant along a screen
    row and varies down the frame.
    """
    lens = C.Lens(1920, 1080, 30.0)
    cam = C.Camera(lens, C.Pose(0, 0, 0, 45, 0, 55))
    near, far = cam.px_per_tile_at(800.0), cam.px_per_tile_at(400.0)
    assert near > far * 1.1


# --------------------------------------------------------------------------
# the rig
# --------------------------------------------------------------------------


def test_ctrl_scroll_scales_the_range_rather_than_stepping_it():
    """Read as a step the same control measures -4.38, -4.59 and -5.65 tiles
    per tick at different ranges; read as a ratio the identical legs give
    0.8666, 0.8773 and 0.8696. The model has to be the multiplicative one.
    """
    rig = C.Rig()
    far = C.Pose(0, 0, 0, 50, 0, 60)
    near = C.Pose(0, 0, 0, 25, 0, 60)
    step_far = far.dist - rig.scroll(far, 1).dist
    step_near = near.dist - rig.scroll(near, 1).dist
    assert step_far > step_near * 1.5
    ratio_far = rig.scroll(far, 1).dist / far.dist
    ratio_near = rig.scroll(near, 1).dist / near.dist
    assert ratio_far == pytest.approx(ratio_near, rel=1e-9)


def test_positive_ticks_zoom_in():
    """Measured, and the opposite of the first guess -- which sent a levelling
    loop climbing away from the target it was trying to frame."""
    rig = C.Rig()
    pose = C.Pose(0, 0, 0, 40, 0, 60)
    assert rig.scroll(pose, 1).dist < pose.dist
    assert rig.scroll(pose, -1).dist > pose.dist


def test_ticks_for_dist_inverts_scroll():
    rig = C.Rig()
    pose = C.Pose(0, 0, 0, 48, 0, 60)
    for want in (40.0, 30.0, 20.0, 12.0):
        ticks = rig.ticks_for_dist(pose, want)
        assert rig.scroll(pose, ticks).dist == pytest.approx(want, rel=0.08)


def test_the_pitch_clamp_is_respected_and_is_not_vertical():
    """The camera stops at about 78 degrees -- measured, four shots agreeing.

    It matters: at 78 degrees `cot(pitch)` is 0.21, not 0, so a paste over
    existing geometry still slides a little.
    """
    rig = C.Rig()
    pose = C.Pose(0, 0, 0, 40, 0, 60)
    driven = rig.orbit(pose, 0, 10_000)
    assert driven.pitch == pytest.approx(rig["pitch_max_deg"], abs=1e-9)
    assert driven.pitch < 85.0


def test_an_assumed_constant_is_reported_as_one():
    """A plan that leans on a guess has to say so. The whole provenance
    apparatus is pointless if a caller cannot tell the two apart."""
    rig = C.Rig()
    assert rig.assumed(["yaw_deg_per_px", "pan_gain"]) == ["pan_gain"] or \
        "pan_gain" in rig.assumed(["yaw_deg_per_px", "pan_gain"])
    measured = rig.with_measured("pan_gain", 0.5, "measured somewhere")
    assert measured.assumed(["pan_gain"]) == []


def test_config_keys_the_model_does_not_know_are_reported(tmp_path):
    """An unrecognised key is a setting that does nothing, which is exactly
    the failure `Layout.unmapped` exists for."""
    path = tmp_path / "camera.json"
    path.write_text('{"constants": {"yaw_deg_per_px": 0.2, "wibble": 3}}')
    rig = C.load_rig(path)
    assert rig["yaw_deg_per_px"] == pytest.approx(0.2)
    assert C.unknown_keys(path) == ["wibble"]


# --------------------------------------------------------------------------
# framing and planning
# --------------------------------------------------------------------------


def test_framing_a_rectangle_puts_all_of_it_in_shot():
    rig = C.Rig()
    lens = rig.lens(1920, 1080)
    rect = (10.0, 20.0, 26.0, 34.0)
    framing = C.frame_rect(rect, rig=rig, lens=lens, yaw=30, pitch=55)
    assert framing.fits
    cam = C.Camera(lens, framing.pose)
    assert cam.covers_all(C.rect_corners(rect), margin_px=40.0)


def test_a_rectangle_too_big_for_the_rig_is_refused_not_cropped():
    """The distance stop is real and measured. A framing that cannot be
    reached must say so rather than return a pose that quietly crops."""
    rig = C.Rig()
    lens = rig.lens(1920, 1080)
    framing = C.frame_rect((0.0, 0.0, 400.0, 400.0), rig=rig, lens=lens)
    assert not framing.fits
    assert "shot list" in framing.note
    assert 0.0 <= framing.covered <= 1.0


def test_a_shot_list_covers_every_target():
    """The answer to "22 field walls on the map, zero in either frame"."""
    rig = C.Rig()
    lens = rig.lens(1920, 1080)
    targets = [(x, z, x + 4.0, z + 4.0)
               for x in (0.0, 60.0, 120.0) for z in (0.0, 70.0)]
    shots = C.shot_list(targets, rig=rig, lens=lens, pitch=60)
    assert shots
    covered = set()
    for f in shots:
        cam = C.Camera(lens, f.pose)
        for i, t in enumerate(targets):
            if cam.covers_all(C.rect_corners(t), 40.0):
                covered.add(i)
    assert covered == set(range(len(targets)))


def test_a_plan_lands_where_the_model_says_it_will():
    """The plan's own arithmetic has to be self-consistent, whatever the game
    then does -- that half is checked by `tools/camera_aim.ps1`, which drives
    it and reads the pose back."""
    rig = C.Rig()
    lens = rig.lens(1920, 1080)
    start = C.Pose(6, 6, 0, 49.5, 20.6, 60.3)
    target = C.Pose(6, 6, 0, 35.0, 100.0, 45.0)
    plan = C.plan(start, target, rig=rig, lens=lens)
    r = plan.residual()
    assert abs(r["yaw_deg"]) < 0.5
    assert abs(r["pitch_deg"]) < 0.5
    assert abs(r["dist_tiles"]) < 3.0


def test_a_move_is_a_named_parameter_map_not_an_argument_list():
    """PowerShell's array splat reads any element beginning with `-` as a
    parameter name, so a plan carrying `-DY -91` bound `-91` as a switch and
    shifted every argument after it."""
    rig = C.Rig()
    lens = rig.lens(1920, 1080)
    plan = C.plan(C.Pose(0, 0, 0, 40, 0, 60), C.Pose(0, 0, 0, 40, 0, 40),
                  rig=rig, lens=lens)
    assert plan.moves
    for m in plan.moves:
        assert isinstance(m.params, dict)
        assert m.cmd and not m.cmd.startswith("-")


# --------------------------------------------------------------------------
# the fitter, against poses the model itself generated
# --------------------------------------------------------------------------


TARGET = [(0, 0, 6), (7, 0, 5), (0, 8, 4), (9, 9, 3), (5, 7, 2)]


def _synthetic(pose: C.Pose, lens: C.Lens):
    cam = C.Camera(lens, pose)
    pairs = []
    for x0, z0, size in TARGET:
        centre = (x0 + size / 2.0, z0 + size / 2.0)
        p = cam.project(centre[0], 0.0, centre[1])
        assert p is not None
        pairs.append((centre, p))
    return pairs


@pytest.mark.parametrize("yaw,pitch,dist,fov", [
    (0.0, 60.0, 50.0, 30.0),
    (37.0, 42.0, 80.0, 30.0),
    (215.0, 75.0, 30.0, 30.0),
    (140.0, 25.0, 120.0, 55.0),
])
def test_the_fitter_recovers_a_pose_it_was_given(yaw, pitch, dist, fov):
    lens = C.Lens(1920, 1080, fov)
    pose = C.Pose(6.0, 6.0, 0.0, dist, yaw, pitch)
    fit = F.solve_pose(_synthetic(pose, lens), 1920, 1080)
    # Yaw is a bearing, so 359.9999 and 0.0 are the same answer; compare the
    # signed difference rather than the numbers.
    assert (fit.pose.yaw - yaw + 180.0) % 360.0 - 180.0 == pytest.approx(
        0.0, abs=1e-3)
    assert fit.pose.pitch == pytest.approx(pitch, abs=1e-3)
    assert fit.pose.dist == pytest.approx(dist, abs=1e-3)
    assert fit.fov_v_deg == pytest.approx(fov, abs=1e-3)
    assert fit.residual_px < 1e-6


def test_the_fitter_refuses_a_camera_under_the_board():
    """The handedness error that started this was silent: it negated the pitch
    and the eye height together, so `dist` -- their ratio -- came out exactly
    right and only one field of the fit was visibly wrong. This is the
    assertion that makes it loud."""
    lens = C.Lens(1920, 1080, 30.0)
    pairs = _synthetic(C.Pose(6, 6, 0, 50, 30, 60), lens)
    flipped = [(w, (u, 1080 - v)) for w, (u, v) in pairs]
    with pytest.raises(ValueError, match="below the ground plane|did not"):
        F.solve_pose(flipped, 1920, 1080)


def test_a_close_shot_can_reorder_the_marks_and_the_fit_still_wins():
    """Size-coding is not safe close in: at a slant range of 35 a near 4x4
    covered 49,540 px against a far 5x5's 56,165. The reader tries the
    ambiguous orderings and keeps the one that reprojects best."""
    marks = [F.Mark(0, 0, 100, (0, 0, 1, 1)), F.Mark(0, 0, 92, (0, 0, 1, 1)),
             F.Mark(0, 0, 40, (0, 0, 1, 1))]
    orders = F._candidate_orders(marks, [6, 5, 4])
    assert list(range(3)) in orders
    assert [1, 0, 2] in orders


def test_the_centroid_of_a_mark_is_not_the_projection_of_its_centre():
    """Perspective magnifies the near half of a square more than the far half.
    Ignoring that cost 3.1 px of residual -- above the bar at which a fit is
    allowed to call itself trustworthy."""
    lens = C.Lens(1920, 1080, 30.0)
    cam = C.Camera(lens, C.Pose(6, 6, 0, 30, 0, 35))
    centre = cam.project(3.0, 0.0, 3.0)
    centroid = F.projected_centroid(cam, 0, 0, 6)
    assert centre is not None and centroid is not None
    assert math.dist(centre, centroid) > 0.5


def test_a_review_says_whether_the_slab_fits_the_frame(tmp_path):
    """A probe board is often too big to photograph, and nothing said so.

    Measured in the 2026-08-28 wall session: the 53x45 all-wall-kits board
    needs 95 tiles of slant range against a Ctrl+scroll stop at 49.75, so it
    cannot be framed whole at any pitch -- and that was found out by building
    it, pasting it, and hunting round it for four exchanges. `panel_review.ps1`
    now asks before it shoots.
    """
    import sys

    sys.path.insert(0, ".")
    from citysmith import camera as C

    rig = C.load_rig(None)
    lens = rig.lens(1920, 1080)
    stops = dict(rig=rig, lens=lens, yaw=0.0, pitch=float(rig["pitch_max_deg"]))

    big = C.frame_rect([0, 0, 53, 45], **stops)
    assert not big.fits
    assert "slant range" in big.note and "shot list" in big.note

    small = C.frame_rect([0, 0, 20, 14], **stops)
    assert small.fits, small.note
    assert small.pose.dist <= float(rig["dist_max"]) + 1e-6


def test_covered_is_a_bounds_overlap_and_not_a_corner_count():
    """`framing.covered` overstates on a wide shallow rectangle.

    It is the overlap against `visible_bounds`, which is the axis-aligned box
    round the frustum's trapezoid -- so a 53x5 slab can read 1.00 covered while
    `covers_all` puts none of its corners in shot. Both numbers are correct
    about different things; what matters is that a caller reporting a headline
    of "too big" must not print `covered` beside it. `panel_review.ps1` prints
    the note's own corner count instead, and this pins the reason.
    """
    import sys

    sys.path.insert(0, ".")
    from citysmith import camera as C

    rig = C.load_rig(None)
    lens = rig.lens(1920, 1080)
    wide = C.frame_rect([0, 0, 53, 5], rig=rig, lens=lens, yaw=0.0, pitch=43.4)
    assert not wide.fits
    assert wide.covered > 0.9, "the looseness this test exists to record"
    assert "0 of 4 corners" in wide.note
