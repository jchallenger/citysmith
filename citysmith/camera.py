"""A model of TaleSpire's builder camera, and the moves that drive it.

Every camera command in `tools/ts.ps1` is *relative* -- orbit by so many
pixels, scroll by so many ticks, hold `w` for so many seconds -- so a session
that only issues them ends up over the void wondering where the map went.
CLAUDE.md's answer so far has been `camerastate`, which photographs the compass
rose and lets a human read a bearing off it. That tells you where you are. It
cannot tell you where to go, and it cannot tell you what a frame will contain
before you take it.

This module is the other half: a pinhole camera over the tile grid, plus the
control model that maps a `ts.ps1` input to a change in its pose. With both,
three questions become arithmetic rather than a flight:

* **What is in this frame?** :meth:`Camera.footprint` is the ground quad the
  frustum covers, so "are the field walls in this shot" is a polygon test
  rather than a screenshot read afterwards. That failure is on the record:
  fences were built, shipped and reviewed over two sessions while absent from
  every frame looked at, because both crops were dense town centre.
* **Where must the camera be to frame this?** :func:`frame_rect` solves for a
  pose, and says so plainly when the target does not fit -- the height cap is
  real and about 40 tiles wide.
* **How do I get there from here?** :func:`plan` emits the `ts.ps1` calls.

**The scale of everything here is `config/camera.json`, and each constant in it
carries where it came from.** A model with a plausible number in it is
indistinguishable from a model with a measured one until it is driven, which is
this project's most expensive recurring failure; so :class:`Rig` keeps the
provenance beside the value and :func:`plan` reports which constants a plan
leant on. See :func:`tools/camera_calib.ps1` for how they are measured.

Conventions, chosen to match what the game and the rest of citysmith already
use rather than what a graphics library would:

* **Tiles, not metres.** 1 world unit = 1 tile, as in `slab.py`.
* **`y` is up**, `x` east, `z` north -- the slab format's own axes.
* **`yaw` is a compass bearing in degrees**, 0 = looking north (toward +z),
  90 = looking east, matching the compass rose the game draws.
* **`pitch` is degrees below the horizon**, 0 = level, 90 = straight down.
  Not "elevation", not a Unity Euler angle: the whole codebase talks about
  pitching *down* to paste, and `review.ps1` passes a positive number to do it.
"""

from __future__ import annotations

import dataclasses
import json
import math
import pathlib
from typing import Iterable, Sequence

#: Where :func:`load_rig` looks when no path is given.
DEFAULT_PATH = pathlib.Path("config/camera.json")

#: A pose whose ray never meets the ground is not a framing error, it is a
#: different question, and the honest answer is a bounded one. Ground geometry
#: further than this from the camera is treated as out of frame.
MAX_RANGE_TILES = 4000.0


# --------------------------------------------------------------------------
# constants, and where each of them came from
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Constant:
    """One number, and the evidence for it.

    `source` is free text, but it is meant to name the artifact: a probe, a
    date, a settings file. `measured` is the load-bearing field -- :func:`plan`
    reports every assumed constant it leant on, so a prediction can never
    quietly present a guess as a measurement.
    """

    value: float
    source: str = "assumed"
    measured: bool = False
    residual: float | None = None

    def __float__(self) -> float:
        return float(self.value)

    def as_json(self) -> dict:
        out: dict = {"value": self.value, "source": self.source,
                     "measured": self.measured}
        if self.residual is not None:
            out["residual"] = self.residual
        return out

    @staticmethod
    def read(raw, fallback: "Constant") -> "Constant":
        """Read one constant from config, tolerating a bare number."""
        if raw is None:
            return fallback
        if isinstance(raw, (int, float)):
            return Constant(float(raw), source="config (no provenance given)")
        if isinstance(raw, dict):
            return Constant(
                value=float(raw["value"]),
                source=str(raw.get("source", "config")),
                measured=bool(raw.get("measured", False)),
                residual=raw.get("residual"),
            )
        raise TypeError(f"cannot read a camera constant from {raw!r}")


# --------------------------------------------------------------------------
# the lens
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Lens:
    """The client area and the angle it subtends.

    `width` and `height` are the *client* rect in pixels -- what `ts.ps1
    client` reports and what `grab.ps1` captures -- not the window, and not the
    monitor. The window gets moved and resized between sessions, which is why
    nothing here is allowed to hardcode 1920x1080.

    Pixels are square, so one focal length in pixels serves both axes and the
    horizontal field of view falls out of the aspect ratio rather than being a
    second free parameter.
    """

    width: int = 1920
    height: int = 1080
    fov_v_deg: float = 60.0

    @property
    def focal_px(self) -> float:
        """Focal length in pixels: the one number projection actually uses."""
        return (self.height / 2.0) / math.tan(math.radians(self.fov_v_deg) / 2.0)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.width / 2.0, self.height / 2.0)

    @property
    def aspect(self) -> float:
        return self.width / self.height

    @property
    def fov_h_deg(self) -> float:
        return math.degrees(2.0 * math.atan(
            (self.width / 2.0) / self.focal_px))

    def with_fov(self, fov_v_deg: float) -> "Lens":
        return dataclasses.replace(self, fov_v_deg=fov_v_deg)


# --------------------------------------------------------------------------
# the pose
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Pose:
    """Where the camera is and where it is looking.

    Held as an **orbit**: a focus point on the board, a distance back from it,
    and two angles. That is a choice about the game and not a convenience --
    `review.ps1 360` walks four faces of one probe with four equal `orbit -DX`
    calls, which only frames the subject each time if the middle-drag turns the
    camera *about the thing it is looking at* rather than about itself.
    :func:`orbit_is_about_the_focus` is the measurement that settles it.

    The focus sits at `focus_y` -- board level, 0.0, unless something has moved
    it -- and `dist` is the slant range to it, so the eye height is
    `focus_y + dist*sin(pitch)`.
    """

    fx: float = 0.0
    fz: float = 0.0
    focus_y: float = 0.0
    dist: float = 40.0
    yaw: float = 0.0
    pitch: float = 45.0

    # -- derived geometry ---------------------------------------------------

    @property
    def eye(self) -> tuple[float, float, float]:
        """The camera position, in tiles."""
        p = math.radians(self.pitch)
        y = math.radians(self.yaw)
        horiz = self.dist * math.cos(p)
        return (self.fx - horiz * math.sin(y),
                self.focus_y + self.dist * math.sin(p),
                self.fz - horiz * math.cos(y))

    @property
    def height(self) -> float:
        """Eye height above the focus plane."""
        return self.dist * math.sin(math.radians(self.pitch))

    @property
    def forward(self) -> tuple[float, float, float]:
        p = math.radians(self.pitch)
        y = math.radians(self.yaw)
        return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))

    @property
    def right(self) -> tuple[float, float, float]:
        y = math.radians(self.yaw)
        return (math.cos(y), 0.0, -math.sin(y))

    @property
    def up(self) -> tuple[float, float, float]:
        # cross(forward, right), not cross(right, forward). Checked against the
        # one case with an obvious answer: level and facing north, screen-up
        # has to come out as world up, and the other order gives -y.
        return _cross(self.forward, self.right)

    def at_height(self, eye_y: float) -> "Pose":
        """The same focus and angles, with the eye at `eye_y`.

        Raising the camera along its own view ray keeps the centre of the frame
        on the same board cell, which is what makes a height change a *zoom*
        rather than a move.
        """
        s = math.sin(math.radians(self.pitch))
        if s <= 1e-9:
            raise ValueError("a level camera has no height to set")
        return dataclasses.replace(self, dist=(eye_y - self.focus_y) / s)

    def looking_at(self, fx: float, fz: float) -> "Pose":
        return dataclasses.replace(self, fx=fx, fz=fz)

    def as_json(self) -> dict:
        return {"fx": round(self.fx, 3), "fz": round(self.fz, 3),
                "focus_y": round(self.focus_y, 3),
                "dist": round(self.dist, 3), "yaw": round(self.yaw, 2),
                "pitch": round(self.pitch, 2),
                "eye_y": round(self.eye[1], 3)}


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# --------------------------------------------------------------------------
# the camera: a lens at a pose, and the questions you can ask it
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Camera:
    """A :class:`Lens` at a :class:`Pose`, and the projection between them."""

    lens: Lens = dataclasses.field(default_factory=Lens)
    pose: Pose = dataclasses.field(default_factory=Pose)

    # -- projection ---------------------------------------------------------

    def project(self, x: float, y: float, z: float) -> tuple[float, float] | None:
        """World point -> client pixel, or None when it is behind the camera."""
        eye = self.pose.eye
        d = (x - eye[0], y - eye[1], z - eye[2])
        zc = _dot(d, self.pose.forward)
        if zc <= 1e-6:
            return None
        f = self.lens.focal_px
        cx, cy = self.lens.centre
        return (cx + f * _dot(d, self.pose.right) / zc,
                cy - f * _dot(d, self.pose.up) / zc)

    def ray(self, sx: float, sy: float) -> tuple[float, float, float]:
        """The unit direction of the ray through a client pixel."""
        f = self.lens.focal_px
        cx, cy = self.lens.centre
        a, u, fwd = self.pose.right, self.pose.up, self.pose.forward
        dx, dy = (sx - cx) / f, (cy - sy) / f
        v = (fwd[0] + a[0] * dx + u[0] * dy,
             fwd[1] + a[1] * dx + u[1] * dy,
             fwd[2] + a[2] * dx + u[2] * dy)
        n = math.sqrt(_dot(v, v))
        return (v[0] / n, v[1] / n, v[2] / n)

    def ground_hit(self, sx: float, sy: float, plane_y: float = 0.0
                   ) -> tuple[float, float] | None:
        """Where the ray through a pixel meets a horizontal plane.

        **This is the function the whole paste system rests on.** A pasted slab
        anchors on the cursor's ray hit, not on any coordinate in the file, so
        "where will this land" is exactly this call -- and "what happens if the
        ray stops on a stump instead of the board" is the same call with a
        different `plane_y`. See :meth:`anchor_slide`.

        None when the ray never meets the plane (it points up, or along it), or
        when it only does so beyond :data:`MAX_RANGE_TILES`, which is the
        honest answer for a frame that contains the horizon.
        """
        eye = self.pose.eye
        d = self.ray(sx, sy)
        if abs(d[1]) < 1e-9:
            return None
        t = (plane_y - eye[1]) / d[1]
        if t <= 0 or t > MAX_RANGE_TILES:
            return None
        return (eye[0] + t * d[0], eye[2] + t * d[2])

    def anchor_slide(self, obstruction_height: float,
                     at: tuple[float, float] | None = None) -> float:
        """How far a paste anchor moves when the ray stops short, in tiles.

        CLAUDE.md records this as `height x cot(pitch)`, measured in game: a
        chunk pasted over an earlier one lands a cell or two toward the camera,
        because the cursor's hit point is now on top of the grass rather than
        on the board. This returns the same quantity from the projection, and
        `test_the_model_reproduces_the_measured_anchor_slide` holds the two
        against each other -- if they ever disagree the model is wrong, because
        the slide was measured and the model was not.

        Zero at a vertical pitch, which is why every paste in `review.ps1` is
        made looking straight down.
        """
        sx, sy = at if at is not None else self.lens.centre
        low = self.ground_hit(sx, sy, 0.0)
        high = self.ground_hit(sx, sy, obstruction_height)
        if low is None or high is None:
            return float("nan")
        return math.dist(low, high)

    # -- scale --------------------------------------------------------------

    def px_per_tile(self) -> tuple[float, float]:
        """Pixels per tile at the centre of the frame: (across, along).

        `across` is the scale sideways, `along` the scale into the screen,
        which is shorter by `sin(pitch)` because the ground is tilted away.
        This is the number the tiling procedure calibrates by hand with a 24x24
        pad, and the reason CLAUDE.md's tiling note says the step is exact
        along one screen row and wrong between rows: `across` is constant along
        a row and both vary down the frame.
        """
        across = self.lens.focal_px / self.pose.dist
        return (across, across * math.sin(math.radians(self.pose.pitch)))

    def px_per_tile_at(self, sy: float) -> float:
        """Sideways pixels per tile on the ground at one screen row.

        Constant along that row, which is the whole content of "paste a row at
        one screen Y, then move".
        """
        cx, _ = self.lens.centre
        a = self.ground_hit(cx, sy)
        b = self.ground_hit(cx + 100.0, sy)
        if a is None or b is None:
            return float("nan")
        return 100.0 / math.dist(a, b)

    # -- what is in frame ---------------------------------------------------

    def footprint(self, plane_y: float = 0.0) -> list[tuple[float, float]]:
        """The ground polygon the frustum covers, corners first.

        Returned clockwise from the top-left of the screen. When the top edge
        of the frame is above the horizon its rays never land, and the polygon
        is closed at :data:`MAX_RANGE_TILES` instead -- a bounded answer rather
        than an exception, because "the horizon is in shot" is a perfectly
        ordinary frame and the useful reply is still "here is the ground you
        can see".
        """
        w, h = self.lens.width, self.lens.height
        corners = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
        out: list[tuple[float, float]] = []
        for sx, sy in corners:
            hit = self.ground_hit(sx, sy, plane_y)
            if hit is None:
                hit = self._far_point(sx, sy)
            out.append(hit)
        return out

    def _far_point(self, sx: float, sy: float) -> tuple[float, float]:
        eye = self.pose.eye
        d = self.ray(sx, sy)
        horiz = math.hypot(d[0], d[2]) or 1e-9
        t = MAX_RANGE_TILES / horiz
        return (eye[0] + t * d[0], eye[2] + t * d[2])

    def sees_horizon(self, plane_y: float = 0.0) -> bool:
        """True when the top of the frame is above the ground plane.

        Worth asking before trusting a footprint: a frame with the horizon in
        it covers an unbounded strip of board, and the distance haze in it has
        twice been read as a chunk seam.
        """
        return self.ground_hit(self.lens.centre[0], 0.0, plane_y) is None

    def covers(self, x: float, z: float, y: float = 0.0,
               margin_px: float = 0.0) -> bool:
        """Is this board point inside the frame?"""
        p = self.project(x, y, z)
        if p is None:
            return False
        sx, sy = p
        return (margin_px <= sx <= self.lens.width - margin_px
                and margin_px <= sy <= self.lens.height - margin_px)

    def covers_all(self, points: Iterable[tuple[float, float]],
                   margin_px: float = 0.0) -> bool:
        return all(self.covers(x, z, margin_px=margin_px) for x, z in points)

    def visible_bounds(self, plane_y: float = 0.0
                       ) -> tuple[float, float, float, float]:
        """Axis-aligned (x0, z0, x1, z1) around the ground footprint."""
        pts = self.footprint(plane_y)
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        return (min(xs), min(zs), max(xs), max(zs))


# --------------------------------------------------------------------------
# the rig: what each input does to the pose
# --------------------------------------------------------------------------

#: Every control constant, with the evidence for it as shipped.
#:
#: **Three of these are inferences off our own scripts rather than
#: measurements, and they are labelled as such.** `review.ps1 360` turns
#: `-DX 320` four times to photograph four faces, and separately pitches
#: `-DY 190` from a low oblique to vertical and `-DY -320` back to eye level;
#: both readings land on 0.28 deg/px, which is a coherent prior and is still
#: not a measurement. `tools/camera_calib.ps1` replaces them.
RIG_DEFAULTS: dict[str, Constant] = {
    "fov_v_deg": Constant(
        60.0, "Unity's default vertical FOV; not read from the game"),
    "yaw_deg_per_px": Constant(
        0.28125, "inferred: review.ps1 360 turns -DX 320 four times for a "
                 "full circle"),
    "pitch_deg_per_px": Constant(
        0.28125, "inferred: review.ps1 360 pitches -DY 190 from a low oblique "
                 "to vertical and -DY -320 back to eye level"),
    "pitch_min_deg": Constant(
        5.0, "assumed; CLAUDE.md records only that the pitch clamps at both "
             "ends and that a drag against the clamp looks like a dead input"),
    "pitch_max_deg": Constant(
        78.0, "assumed pending measurement, but seen twice at 78.26 and 78.25 "
              "-- the camera does NOT reach vertical"),
    # **Ctrl+scroll changes the orbit distance, not the eye height.** Measured:
    # across a scroll the focus point does not move and the pitch does not
    # change, so what varies is how far back the camera sits. Reporting it as
    # eye height would tie the constant to the pitch it was measured at.
    # **A RATIO, not a step.** Ctrl+scroll scales the slant range; it does not
    # subtract from it. Read as a step the same control measures -4.38, -4.59
    # and -5.65 tiles per tick at different ranges -- 29% apart, which reads as
    # a noisy constant. Read as a ratio the identical three legs give 0.8666,
    # 0.8773 and 0.8696, and they agree to 1.2%. Under 1, because a positive
    # tick zooms in.
    #
    # The additive version was believed for one whole calibration and then
    # caught by driving a planned move and measuring where the camera actually
    # landed: yaw was out by 0.13 degrees, pitch by 0.49, and range by 3.5
    # tiles in 13.5. Two of those are noise and the third was a wrong law.
    "dist_scale_per_tick": Constant(
        0.8712, "assumed: the slant range scales by this factor per tick"),
    "dist_max": Constant(
        50.0, "assumed; the top stop is real -- further ticks change nothing "
              "-- but it has not been read back twice yet"),
    "dist_min": Constant(5.0, "assumed"),
    "pan_gain": Constant(
        0.53, "measured: a 462 px right-drag moved the world 245 px "
              "(CLAUDE.md, Tiling)"),
    "fly_speed_max": Constant(
        70.0, "inferred: 3 s of the w key crosses a 187-tile map, tiles/sec"),
    "fly_ramp_tau": Constant(
        0.8, "inferred: 0.4 s of the w key crawls a few tiles, so the ramp's "
             "time constant is of that order"),
}


@dataclasses.dataclass(frozen=True)
class Rig:
    """The control model: `ts.ps1` input in, pose change out.

    Kept apart from :class:`Camera` on purpose. The camera is geometry and is
    the same on any machine; the rig is a set of measured sensitivities that
    belong to this install, this window size and this version of the game, and
    they live in `config/camera.json` where a re-measurement can replace them
    without touching code.
    """

    const: dict[str, Constant] = dataclasses.field(
        default_factory=lambda: dict(RIG_DEFAULTS))

    def __getitem__(self, key: str) -> float:
        return float(self.const[key].value)

    def is_measured(self, key: str) -> bool:
        return self.const[key].measured

    def assumed(self, keys: Iterable[str]) -> list[str]:
        """Which of these constants are not measurements. Order preserved."""
        return [k for k in keys if not self.is_measured(k)]

    def lens(self, width: int, height: int) -> Lens:
        return Lens(width, height, self["fov_v_deg"])

    # -- inputs -------------------------------------------------------------

    def orbit(self, pose: Pose, dx: int, dy: int) -> Pose:
        """`ts.ps1 orbit`: middle-drag. +dy pitches down toward vertical."""
        yaw = (pose.yaw + dx * self["yaw_deg_per_px"]) % 360.0
        pitch = _clamp(pose.pitch + dy * self["pitch_deg_per_px"],
                       self["pitch_min_deg"], self["pitch_max_deg"])
        return dataclasses.replace(pose, yaw=yaw, pitch=pitch)

    def scroll(self, pose: Pose, ticks: int) -> Pose:
        """`ts.ps1 nudge -Mode vertical`: Ctrl+scroll, with an empty hand.

        The hint bar calls it MOVE CAMERA VERTICALLY and the eye does indeed
        rise, but what actually changes is the **orbit distance**: measured,
        the focus stays put and the pitch does not move, so the eye rises and
        retreats together along the view ray. Modelling it as eye height would
        make the constant depend on the pitch it happened to be measured at.

        **Positive ticks zoom IN.** Measured, and the opposite of the first
        guess -- which sent a levelling loop climbing away from its target.

        **Multiplicative**: the range is scaled, not stepped. See
        `dist_scale_per_tick`.
        """
        dist = _clamp(pose.dist * self["dist_scale_per_tick"] ** ticks,
                      self["dist_min"], self["dist_max"])
        return dataclasses.replace(pose, dist=dist)

    def ticks_for_dist(self, pose: Pose, dist: float) -> int:
        """How many Ctrl+scroll ticks to reach a slant range, clamped."""
        want = _clamp(dist, self["dist_min"], self["dist_max"])
        if pose.dist <= 0 or want <= 0:
            return 0
        return round(math.log(want / pose.dist)
                     / math.log(self["dist_scale_per_tick"]))

    def fly_distance(self, hold_s: float) -> float:
        """Tiles covered by holding a WASD key, over the velocity ramp.

        WASD eases up to a maximum, which is why a tap looks like a dead
        binding and 3 s crosses a map. Modelled first-order: the distance is
        `v*(t - tau*(1 - exp(-t/tau)))`.
        """
        v, tau = self["fly_speed_max"], self["fly_ramp_tau"]
        if tau <= 0:
            return v * hold_s
        return v * (hold_s - tau * (1.0 - math.exp(-hold_s / tau)))

    def fly_hold_for(self, tiles: float) -> float:
        """Invert :meth:`fly_distance`. Bisection: the curve is monotone."""
        tiles = abs(tiles)
        if tiles <= 0:
            return 0.0
        lo, hi = 0.0, 1.0
        while self.fly_distance(hi) < tiles and hi < 60.0:
            hi *= 2.0
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if self.fly_distance(mid) < tiles:
                lo = mid
            else:
                hi = mid
        return round((lo + hi) / 2.0, 2)

    # -- persistence --------------------------------------------------------

    def as_json(self) -> dict:
        return {"config_version": 1,
                "constants": {k: v.as_json() for k, v in self.const.items()}}

    def with_measured(self, key: str, value: float, source: str,
                      residual: float | None = None) -> "Rig":
        const = dict(self.const)
        const[key] = Constant(value, source, measured=True, residual=residual)
        return Rig(const)

    def provenance(self) -> list[str]:
        """One line per constant, worst-evidenced first."""
        rows = sorted(self.const.items(), key=lambda kv: (kv[1].measured, kv[0]))
        out = []
        for key, c in rows:
            mark = "measured" if c.measured else "ASSUMED "
            res = "" if c.residual is None else f"  (residual {c.residual:g})"
            out.append(f"  {mark}  {key:<18} {c.value:<10.5g} {c.source}{res}")
        return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def load_rig(path=None) -> Rig:
    """Read `config/camera.json`, defaults in code and the file an overlay.

    The same rule `config.py` follows: every constant has a working value here,
    so a fresh checkout with no file still predicts -- it just predicts from
    assumptions, and says so.
    """
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    const = dict(RIG_DEFAULTS)
    if p.exists():
        raw = json.loads(p.read_text())
        for key, value in (raw.get("constants") or {}).items():
            if key not in const:
                continue  # reported by unknown_keys, never silently applied
            const[key] = Constant.read(value, const[key])
    return Rig(const)


def unknown_keys(path=None) -> list[str]:
    """Keys in the file that the model does not recognise.

    An unrecognised key is a setting that does nothing, which is the failure
    `Layout.unmapped` exists for: the run succeeds and the difference shows up
    on the board an hour later.
    """
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    if not p.exists():
        return []
    raw = json.loads(p.read_text())
    return sorted(k for k in (raw.get("constants") or {})
                  if k not in RIG_DEFAULTS)


def save_rig(rig: Rig, path=None) -> pathlib.Path:
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rig.as_json(), indent=2) + "\n")
    return p


# --------------------------------------------------------------------------
# framing: where does the camera have to be to see this?
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Framing:
    """A pose that frames a target, and an honest verdict on whether it does.

    `fits` is the whole point. The height cap is real -- at the top of
    Ctrl+scroll a frame is about 40 tiles across, measured -- so a great many
    perfectly reasonable requests cannot be satisfied by one shot, and the
    useful answer is to say so and hand back a shot list rather than to return
    a pose that quietly crops the target.
    """

    pose: Pose
    fits: bool
    covered: float
    note: str

    def as_json(self) -> dict:
        return {"pose": self.pose.as_json(), "fits": self.fits,
                "covered": round(self.covered, 3), "note": self.note}


def rect_corners(rect: Sequence[float]) -> list[tuple[float, float]]:
    x0, z0, x1, z1 = rect
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def frame_rect(rect: Sequence[float], *, rig: Rig, lens: Lens,
               yaw: float = 0.0, pitch: float = 60.0,
               margin_px: float = 40.0,
               focus_y: float = 0.0) -> Framing:
    """Solve for a pose that puts a board rectangle in frame.

    Bisects on the slant range: the projection shrinks monotonically as the
    camera pulls back, so there is exactly one crossing and no need for
    anything cleverer. The focus goes at the rect's centre, which keeps the
    subject centred rather than merely inside the frame -- an off-centre
    subject at a grazing angle is where the snap is hardest to judge, and the
    preview note in CLAUDE.md says to pan it to the middle before committing.

    The pitch is a *request*, not a guarantee: it is clamped into the rig's
    measured range, because a pose the rig cannot reach is not a plan.
    """
    x0, z0, x1, z1 = rect
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
    pitch = _clamp(pitch, rig["pitch_min_deg"], rig["pitch_max_deg"])
    corners = rect_corners(rect)

    def at(dist: float) -> Camera:
        return Camera(lens, Pose(cx, cz, focus_y, dist, yaw % 360.0, pitch))

    span = max(x1 - x0, z1 - z0, 1e-3)
    lo, hi = 1e-3, max(4.0 * span, 8.0)
    while not at(hi).covers_all(corners, margin_px) and hi < MAX_RANGE_TILES:
        hi *= 2.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        if at(mid).covers_all(corners, margin_px):
            hi = mid
        else:
            lo = mid

    pose = at(hi).pose
    cap = rig["dist_max"]
    if pose.dist <= cap + 1e-6:
        return Framing(pose, True, 1.0,
                       f"{x1 - x0:.0f}x{z1 - z0:.0f} tiles at "
                       f"{pose.dist:.1f} tiles of slant range "
                       f"({pose.height:.1f} of eye height)")

    # Too big for one frame. Return the best pose the rig can actually reach,
    # and say what fraction of the target it holds -- a number, not a shrug.
    capped = Camera(lens, dataclasses.replace(pose, dist=cap))
    inside = sum(1 for c in corners if capped.covers(*c, margin_px=margin_px))
    vb = capped.visible_bounds(focus_y)
    frac = (_overlap(x0, x1, vb[0], vb[2]) * _overlap(z0, z1, vb[1], vb[3])
            / max((x1 - x0) * (z1 - z0), 1e-9))
    return Framing(
        capped.pose, False, min(1.0, frac),
        f"needs {pose.dist:.0f} tiles of slant range; Ctrl+scroll stops at "
        f"{cap:.0f}. {inside} of 4 corners in frame -- split it into a "
        "shot list")


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def shot_list(targets: Sequence[Sequence[float]], *, rig: Rig, lens: Lens,
              yaw: float = 0.0, pitch: float = 60.0,
              margin_px: float = 40.0) -> list[Framing]:
    """Cover every target rect with as few frames as the height cap allows.

    Greedy, and deliberately so: it takes the target furthest from anything
    already framed, frames it at the cap, and absorbs every other target that
    frame happens to contain. That is the shape of the problem this exists for
    -- *"22 field walls on East Tradebourne, zero in either frame"* -- where
    the targets are scattered and the question is how many trips it takes to
    photograph all of them, not how to be optimal about it.
    """
    todo = list(range(len(targets)))
    out: list[Framing] = []
    while todo:
        seed = targets[todo[0]]
        f = frame_rect(seed, rig=rig, lens=lens, yaw=yaw, pitch=pitch,
                       margin_px=margin_px)
        cam = Camera(lens, f.pose)
        taken = [i for i in todo
                 if cam.covers_all(rect_corners(targets[i]), margin_px)]
        if not taken:            # the seed itself does not fit: keep it anyway
            taken = [todo[0]]    # so a too-big target cannot loop forever
        out.append(dataclasses.replace(
            f, note=f"{f.note}; holds {len(taken)} of {len(targets)} targets"))
        todo = [i for i in todo if i not in set(taken)]
    return out


# --------------------------------------------------------------------------
# the plan: how to get from here to there
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Move:
    """One `ts.ps1` call, with the pose it leaves behind.

    Carried as a verb and a **parameter map**, not as a flat argument list.
    A list has to be splatted, and PowerShell's array splat reads any element
    beginning with `-` as a parameter name -- so a plan containing `-DY -91`
    bound `-91` as a switch and shifted every argument after it, which surfaced
    as `Cannot convert value "-DX" to type "System.Int32"`. A hashtable splat
    names each parameter and cannot be misread. It also means nothing is ever
    assembled into a command string and handed to a shell.
    """

    cmd: str
    params: dict
    note: str
    after: Pose

    @property
    def argv(self) -> list[str]:
        out = [self.cmd]
        for key, value in self.params.items():
            out += [f"-{key}", str(value)]
        return out

    @property
    def command(self) -> str:
        return ".\\tools\\ts.ps1 " + " ".join(self.argv)

    def as_json(self) -> dict:
        return {"cmd": self.cmd, "params": dict(self.params),
                "argv": self.argv, "note": self.note,
                "command": self.command, "after": self.after.as_json()}


@dataclasses.dataclass(frozen=True)
class Plan:
    """A sequence of moves, and the evidence the sequence rests on."""

    moves: list[Move]
    start: Pose
    target: Pose
    assumed: list[str]

    @property
    def end(self) -> Pose:
        return self.moves[-1].after if self.moves else self.start

    @property
    def script(self) -> str:
        return "\n".join(m.command for m in self.moves)

    def residual(self) -> dict[str, float]:
        """How far the plan lands from the pose asked for.

        Non-zero by construction: Ctrl+scroll is quantised in ticks, a drag is
        quantised in pixels, and a WASD hold is quantised at two decimal
        places. Worth printing rather than hiding -- a plan that ends 4 tiles
        short is fine for a screenshot and not fine for a paste.
        """
        e, t = self.end, self.target
        dyaw = (e.yaw - t.yaw + 180.0) % 360.0 - 180.0
        return {"yaw_deg": round(dyaw, 2),
                "pitch_deg": round(e.pitch - t.pitch, 2),
                "dist_tiles": round(e.dist - t.dist, 2),
                "focus_tiles": round(math.dist((e.fx, e.fz), (t.fx, t.fz)), 2)}

    def as_json(self) -> dict:
        return {"moves": [m.as_json() for m in self.moves],
                "start": self.start.as_json(),
                "target": self.target.as_json(),
                "assumed": list(self.assumed),
                "residual": self.residual(),
                "script": self.script}


#: Where a drag is grabbed. Not the client centre: a middle-drag that starts
#: over the HUD is a click on the HUD. This is the middle of the board area on
#: a maximised window, and callers on other window sizes pass their own.
def drag_origin(lens: Lens) -> tuple[int, int]:
    return (int(lens.width * 0.45), int(lens.height * 0.45))


def plan(start: Pose, target: Pose, *, rig: Rig, lens: Lens,
         grab: tuple[int, int] | None = None) -> Plan:
    """The `ts.ps1` calls that take the camera from `start` to `target`.

    The order is not arbitrary and each step earns its place:

    1. **Pitch first**, because `review.ps1` has to paste at a vertical pitch
       and because a pitch drag against the clamp does nothing -- doing it
       first means a clamped result is visible in the plan's residual rather
       than corrupting the moves after it.
    2. **Then yaw**, because WASD flies in the camera's own frame, so the
       heading has to be right before any distance is covered.
    3. **Then fly**, along the new heading.
    4. **Height last**, because moving the eye up its own view ray leaves the
       centre of the frame on the same cell and so cannot undo step 3.
    """
    gx, gy = grab if grab is not None else drag_origin(lens)
    moves: list[Move] = []
    pose = start
    used: list[str] = []

    dpitch = target.pitch - pose.pitch
    if abs(dpitch) > 0.2:
        dy = round(dpitch / rig["pitch_deg_per_px"])
        if dy:
            pose = rig.orbit(pose, 0, dy)
            used.append("pitch_deg_per_px")
            moves.append(Move(
                "orbit", {"X": gx, "Y": gy, "DX": 0, "DY": dy},
                f"pitch {start.pitch:.0f} -> {pose.pitch:.0f} deg", pose))

    dyaw = (target.yaw - pose.yaw + 180.0) % 360.0 - 180.0
    if abs(dyaw) > 0.2:
        dx = round(dyaw / rig["yaw_deg_per_px"])
        if dx:
            pose = rig.orbit(pose, dx, 0)
            used.append("yaw_deg_per_px")
            moves.append(Move(
                "orbit", {"X": gx, "Y": gy, "DX": dx, "DY": 0},
                f"yaw -> {pose.yaw:.0f} deg ({_bearing_name(pose.yaw)})",
                pose))

    # Fly in the camera's frame: +forward is the yaw bearing, +right is 90 to
    # its clockwise side. Two keys rather than one diagonal hold, because the
    # ramp is per key and a diagonal would need both curves at once.
    dx_w = target.fx - pose.fx
    dz_w = target.fz - pose.fz
    yr = math.radians(pose.yaw)
    ahead = dx_w * math.sin(yr) + dz_w * math.cos(yr)
    side = dx_w * math.cos(yr) - dz_w * math.sin(yr)
    for amount, keys in ((ahead, ("w", "s")), (side, ("d", "a"))):
        if abs(amount) < 0.5:
            continue
        key = keys[0] if amount > 0 else keys[1]
        hold = rig.fly_hold_for(amount)
        travelled = math.copysign(rig.fly_distance(hold), amount)
        pose = dataclasses.replace(
            pose,
            fx=pose.fx + (travelled * math.sin(yr) if key in "ws"
                          else travelled * math.cos(yr)),
            fz=pose.fz + (travelled * math.cos(yr) if key in "ws"
                          else -travelled * math.sin(yr)),
        )
        used += ["fly_speed_max", "fly_ramp_tau"]
        moves.append(Move(
            "fly", {"Keys": key, "Hold": f"{hold:g}"},
            f"{abs(travelled):.0f} tiles {_leg_name(key)}", pose))

    ticks = rig.ticks_for_dist(pose, target.dist)
    if ticks:
        pose = rig.scroll(pose, ticks)
        used.append("dist_scale_per_tick")
        moves.append(Move(
            "nudge", {"Mode": "vertical", "X": gx, "Y": gy, "Ticks": ticks},
            f"slant range -> {pose.dist:.1f} tiles "
            f"({pose.height:.1f} of eye height)", pose))

    return Plan(moves, start, target, rig.assumed(dict.fromkeys(used)))


def _bearing_name(yaw: float) -> str:
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((yaw % 360.0 + 22.5) // 45) % 8]


def _leg_name(key: str) -> str:
    return {"w": "forward", "s": "back", "a": "left", "d": "right"}[key]
