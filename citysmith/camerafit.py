"""Recover a real camera pose from a screenshot of a known target.

`camera.py` is a model of TaleSpire's camera. This is how the model's constants
stop being guesses: put marks on the board at coordinates we chose, photograph
them, and solve for the pose that would put them where they landed. Do it twice
either side of one input -- an orbit, a scroll tick -- and the difference is a
measurement of what that input does.

Three pieces, each testable on its own:

* :func:`read_png` -- a PNG decoder, because the core is stdlib only and there
  is no Pillow to lean on. Deliberately narrow: 8-bit RGB/RGBA, no interlace.
  `grab.ps1 -Format png` writes exactly that.
* :func:`find_marks` -- connected turf-coloured blobs, which is what a grass
  square on a bare board looks like. Marks are **size-coded**, 2x2 up to 6x6,
  so which blob is which survives being read at a bad angle -- the same trick
  the wall probes use, for the same reason.
* :func:`solve_pose` -- a plane-to-image homography, decomposed into a focal
  length and a pose.

**The homography over-determines the focal length, and that is the point.**
A planar target gives two independent equations for `f`, one from the
orthogonality of the two rotation columns and one from their equal length. They
are only forced to agree if the camera really is a pinhole with square pixels
and the marks really were found where we think. :attr:`Fit.focal_disagreement`
is the gap between them, and a fit that does not report it would be a probe
that answers without looking.
"""

from __future__ import annotations

import dataclasses
import math
import pathlib
import struct
import zlib
from typing import Sequence

from citysmith.camera import Camera, Lens, Pose


# --------------------------------------------------------------------------
# a PNG, without a dependency
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Image:
    width: int
    height: int
    pixels: bytes          # RGB, three bytes per pixel, row major

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return (self.pixels[i], self.pixels[i + 1], self.pixels[i + 2])


def read_png(path) -> Image:
    """Decode an 8-bit non-interlaced PNG to RGB.

    Everything else raises rather than guessing: a decoder that silently
    mis-reads a bit depth produces a picture that is wrong in a way no
    downstream check can see.
    """
    raw = pathlib.Path(path).read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, hdr = 8, [], None
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if hdr is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour, compress, filt, interlace = hdr
    if depth != 8 or colour not in (2, 6) or interlace != 0:
        raise ValueError(
            f"unsupported PNG: depth={depth} colour={colour} "
            f"interlace={interlace}; want 8-bit RGB or RGBA, non-interlaced")
    chan = 3 if colour == 2 else 4
    data = zlib.decompress(b"".join(idat))
    stride = width * chan
    out = bytearray(width * height * 3)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        ftype = data[p]
        p += 1
        line = bytearray(data[p:p + stride])
        p += stride
        _unfilter(ftype, line, prev, chan)
        if chan == 3:
            out[y * width * 3:(y + 1) * width * 3] = line
        else:
            row = out
            base = y * width * 3
            for x in range(width):
                row[base + x * 3:base + x * 3 + 3] = line[x * 4:x * 4 + 3]
        prev = line
    return Image(width, height, bytes(out))


def _unfilter(ftype: int, line: bytearray, prev: bytearray, bpp: int) -> None:
    if ftype == 0:
        return
    n = len(line)
    if ftype == 1:
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif ftype == 2:
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 0xFF
    elif ftype == 3:
        for i in range(n):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[i] = (line[i] + pred) & 0xFF
    else:
        raise ValueError(f"unknown PNG filter {ftype}")


# --------------------------------------------------------------------------
# finding the marks
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Mark:
    """One blob: where it is on screen and how big it looked."""

    u: float
    v: float
    area_px: int
    bbox: tuple[int, int, int, int]

    def as_json(self) -> dict:
        return {"u": round(self.u, 2), "v": round(self.v, 2),
                "area_px": self.area_px, "bbox": list(self.bbox)}


#: Sampled off a real capture (`out/camcal/level0.png`, 2026-08-28) rather than
#: reasoned about. `Grass 1x1` under TaleSpire's default light is **yellow**-
#: green -- r and g run within a few points of each other, and it is *blue*
#: that drops away:
#:
#:     grass       (157,172,90) (184,185,100) (159,156,95) (146,143,87)
#:     bare board  ( 95, 96,96) ( 97, 97, 97) ( 92, 94,94)
#:
#: So the separation is chroma on the yellow-blue axis, `(r+g)/2 - b`: about
#: 70 on grass and about 0 on the board. The first version of this asked
#: whether green beat both other channels, which on those samples is *false* --
#: `(159,156,95)` has g below r -- and it found seven blobs of noise and none
#: of the six marks. Guessing a threshold is the same mistake as guessing a
#: constant; both get measured here now.
GRASS_CHROMA = 30.0
GRASS_MIN_LUMA = 60


def is_grass(r: int, g: int, b: int) -> bool:
    """Turf, against a bare board and against the HUD.

    Two conditions, and the second is not optional. The chroma test alone
    fires on every warm colour in the interface -- the orange BUILDING banner,
    the orange frame, the red Role card -- because they are just as far off the
    blue axis. Requiring green to keep up with red rejects those (orange runs
    r=230 against g=120) while passing grass, where the two channels track each
    other to within a few points.
    """
    return (g >= GRASS_MIN_LUMA
            and (r + g) / 2.0 - b > GRASS_CHROMA
            and g > r - 25)


#: The HUD, as pixel offsets from the client edges it is anchored to.
#:
#: Measured on a 1920x1080 client at UI scale x1.00, by finding turf-coloured
#: blobs on a **board with nothing on it** -- the honest way to find out what
#: the interface contributes, since anything that survives there is not board.
#: Two things did, and both would have been read as marks:
#:
#:     (1789, 94)-(1817, 115)   the green marker on the elevation ruler
#:     (  94, 225)-( 129, 238)   the word YOU in the Role card, in green
#:
#: `is_grass` cannot reject them -- they really are that colour -- so they are
#: excluded by *place*, which is the one thing about the HUD that is reliable:
#: every piece of it is anchored to an edge and stays there.
#:
#: The bottom margin is 60 and not the 160 it started at, because 160 clipped
#: the target itself -- a mark reached y=919 against a border at 920 and the
#: shot was correctly refused. Swept on an empty board at 160/120/80/60/40:
#: nothing turf-coloured appears anywhere in that strip. The compass is the
#: one bottom-left widget with green in it and it sits at x<170, so the *left*
#: margin already excludes it.
#:
#: Measured **in build mode**, which is where calibration runs. Exploration
#: mode puts a dice tray and a hotbar across the bottom; if this is ever used
#: there, re-measure rather than assuming the strip is still clear.
HUD_MARGINS = {"left": 180, "top": 60, "right": 150, "bottom": 60}


def hud_roi(width: int, height: int) -> tuple[int, int, int, int]:
    """The part of the frame that is board rather than interface.

    Derived from the client rect, never hardcoded: the window gets moved and
    resized between sessions, and a stale rectangle does not fail loudly -- it
    silently aims at the wrong thing. That failure has three entries in
    CLAUDE.md already.
    """
    return (HUD_MARGINS["left"], HUD_MARGINS["top"],
            width - HUD_MARGINS["right"], height - HUD_MARGINS["bottom"])


def find_marks(img: Image, *, min_area: int = 60,
               roi: tuple[int, int, int, int] | None = None) -> list[Mark]:
    """Connected green blobs, largest first.

    `roi` crops before searching, which is how the HUD is kept out: the Role
    card, the compass and the hint bar are all in fixed corners and none of
    them is green, but a board seen past the frame edge can be.
    """
    x0, y0, x1, y1 = roi or (0, 0, img.width, img.height)
    w = x1 - x0
    mask = bytearray(w * (y1 - y0))
    for y in range(y0, y1):
        base = (y - y0) * w
        for x in range(x0, x1):
            if is_grass(*img.rgb(x, y)):
                mask[base + (x - x0)] = 1

    marks: list[Mark] = []
    h = y1 - y0
    for start in range(len(mask)):
        if not mask[start]:
            continue
        stack = [start]
        mask[start] = 0
        cells = []
        while stack:
            i = stack.pop()
            cells.append(i)
            cy, cx = divmod(i, w)
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if mask[j]:
                        mask[j] = 0
                        stack.append(j)
        if len(cells) < min_area:
            continue
        xs = [c % w for c in cells]
        ys = [c // w for c in cells]
        marks.append(Mark(
            u=x0 + sum(xs) / len(xs), v=y0 + sum(ys) / len(ys),
            area_px=len(cells),
            bbox=(x0 + min(xs), y0 + min(ys), x0 + max(xs), y0 + max(ys))))
    marks.sort(key=lambda m: -m.area_px)
    return marks


def match_by_size(marks: Sequence[Mark],
                  world: Sequence[tuple[float, float]]
                  ) -> list[tuple[tuple[float, float], Mark]]:
    """Pair size-coded marks with the coordinates they were built at.

    `world` is in *descending mark size* -- the 6x6 first. Marks come back
    sorted by area, so the pairing is by rank, and rank survives perspective
    as long as the size steps are wider than the foreshortening. That holds
    for 6/5/4/3/2 at any pitch worth calibrating from; it would not hold for
    marks all the same size, which is why they are not.
    """
    if len(marks) < len(world):
        raise ValueError(
            f"found {len(marks)} marks, expected {len(world)} -- "
            "check the frame actually contains the whole target")
    return list(zip(world, marks[:len(world)]))


# --------------------------------------------------------------------------
# the homography, and the pose inside it
# --------------------------------------------------------------------------


def solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. Small and exact enough."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ValueError("singular system: the marks are degenerate "
                             "(collinear, or the same point twice)")
        m[col], m[piv] = m[piv], m[col]
        pivot = m[col][col]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / pivot
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def homography(pairs: Sequence[tuple[tuple[float, float], tuple[float, float]]]
               ) -> list[float]:
    """Fit (X, Z) -> (u, v) for a plane. Returns h11..h32, with h33 = 1.

    Least squares through the normal equations. Four pairs is exact; more is
    over-determined, which is what makes :attr:`Fit.residual_px` meaningful.
    """
    if len(pairs) < 4:
        raise ValueError("a homography needs four marks")
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (bx, bz), (u, v) in pairs:
        rows.append([bx, bz, 1, 0, 0, 0, -u * bx, -u * bz])
        rhs.append(u)
        rows.append([0, 0, 0, bx, bz, 1, -v * bx, -v * bz])
        rhs.append(v)
    ata = [[sum(rows[k][i] * rows[k][j] for k in range(len(rows)))
            for j in range(8)] for i in range(8)]
    atb = [sum(rows[k][i] * rhs[k] for k in range(len(rows))) for i in range(8)]
    return solve_linear(ata, atb)


@dataclasses.dataclass(frozen=True)
class Fit:
    """A pose recovered from one screenshot, and how much to trust it."""

    pose: Pose
    focal_px: float
    fov_v_deg: float
    residual_px: float
    focal_disagreement: float
    marks: int
    focal_cross_checked: bool = True

    #: The residual above which a "fit" is a mis-correspondence rather than a
    #: noisy measurement. **Set from the observed distribution, not by taste.**
    #: Over a full calibration sweep the fits fall into two clumps with nothing
    #: in between: real ones run 0.15 to 2.42 px, and shots where the target
    #: had left the frame -- so the size ranking handed a mark's coordinates to
    #: a fragment of another -- come back at 93.96 to 95.14. Any threshold in
    #: that gap separates them; 4.0 sits in it with two orders of magnitude of
    #: clearance above and 40% of margin below.
    MAX_RESIDUAL_PX = 4.0

    @property
    def trustworthy(self) -> bool:
        """Every self-check that could run, ran and passed.

        **A fit whose cross-check could not run is not thereby a good fit**,
        and this does not pretend otherwise -- it reports `focal_cross_checked`
        false and the calibration procedure shoots from an oblique yaw, where
        both readings exist, rather than accepting the weaker verdict.

        The 4% bar on the focal length is the looser of the two, because the
        second estimate leans on the bottom row of the homography and that row
        goes to zero as the view approaches plan.
        """
        if self.residual_px > self.MAX_RESIDUAL_PX:
            return False
        return (not self.focal_cross_checked) or self.focal_disagreement <= 0.04

    def as_json(self) -> dict:
        return {"pose": self.pose.as_json(),
                "focal_px": round(self.focal_px, 2),
                "fov_v_deg": round(self.fov_v_deg, 3),
                "residual_px": round(self.residual_px, 3),
                "focal_disagreement": (None if not self.focal_cross_checked
                                       else round(self.focal_disagreement, 4)),
                "focal_cross_checked": self.focal_cross_checked,
                "marks": self.marks, "trustworthy": self.trustworthy}


def solve_pose(pairs: Sequence[tuple[tuple[float, float], tuple[float, float]]],
               width: int, height: int, *, focus_y: float = 0.0,
               focal_px: float | None = None) -> Fit:
    """Recover focal length and pose from marks on the ground plane.

    The plane makes this a homography rather than a general resection, and the
    homography's two rotation columns must be orthonormal. Those two conditions
    give two independent readings of the focal length; the fit keeps both and
    reports how far apart they are.

    **Pass `focal_px` once it is known and the pose gets much easier.** Both
    readings of the focal length degenerate as the view approaches plan -- they
    lean on the bottom row of the homography, which goes to zero there -- so a
    near-plan shot returns a focal length that is wrong, and everything derived
    from it, including the centroid correction, is wrong with it. Measured:
    shots at the 78-degree pitch stop came back with 9 px of residual and a
    field of view of 28.6 against the 30.03 the obliques agree on. With the
    focal length held fixed there is nothing left to degenerate.
    """
    h = homography(pairs)
    cx, cy = width / 2.0, height / 2.0
    # Work with the principal point at the origin, so K is diag(f, f, 1) and
    # the constraints below have one unknown instead of three.
    m = [[h[0] - cx * h[6], h[1] - cx * h[7], h[2] - cx],
         [h[3] - cy * h[6], h[4] - cy * h[7], h[5] - cy],
         [h[6], h[7], 1.0]]
    c1 = (m[0][0], m[1][0], m[2][0])
    c2 = (m[0][1], m[1][1], m[2][1])
    c3 = (m[0][2], m[1][2], m[2][2])

    denom_a = c1[2] * c2[2]
    denom_b = c2[2] ** 2 - c1[2] ** 2
    est: list[float] = []
    if focal_px is not None:
        # Told, not inferred. The two constraints are still evaluated below so
        # the fit can report whether they agree with what it was told, but they
        # no longer decide anything.
        est = []
    if abs(denom_a) > 1e-12:
        f2 = -(c1[0] * c2[0] + c1[1] * c2[1]) / denom_a
        if f2 > 0:
            est.append(math.sqrt(f2))
    if abs(denom_b) > 1e-12:
        f2 = ((c1[0] ** 2 + c1[1] ** 2) - (c2[0] ** 2 + c2[1] ** 2)) / denom_b
        if f2 > 0:
            est.append(math.sqrt(f2))
    if focal_px is None and not est:
        raise ValueError(
            "the focal length is not observable from this view: both "
            "constraints are degenerate, which happens at an exactly plan "
            "view. Fit the focal length from an oblique and hold it fixed.")
    focal = focal_px if focal_px is not None else sum(est) / len(est)
    disagreement = (abs(est[0] - est[1]) / focal) if len(est) == 2 else float("nan")

    kinv = lambda v: (v[0] / focal, v[1] / focal, v[2])  # noqa: E731
    r1, r2, t = kinv(c1), kinv(c2), kinv(c3)
    scale = 1.0 / math.sqrt(sum(c * c for c in r1))
    if t[2] < 0:                      # the target must be in front of the eye
        scale = -scale
    r1 = tuple(c * scale for c in r1)
    r2 = tuple(c * scale for c in r2)
    t = tuple(c * scale for c in t)
    # R's columns are world x, world y, world z in camera axes. A ground plane
    # can only ever show two of them, so the y column has to be reconstructed
    # -- and its SIGN is the whole difficulty. The usual `c_z x c_x` assumes a
    # right-handed frame; ours is not one. `camera.Pose` builds its basis as
    # right / up / forward with `right x up = forward`, so the camera axes
    # (right, -up, forward) have determinant -1, and the cofactor identity
    # picks up that sign: c_y = det(R) * (c_z x c_x) = c_x x c_z.
    #
    # Getting this backwards is not loud. It negates the pitch AND the eye
    # height together, so `dist` -- their ratio -- still comes out exactly
    # right, and a fit reads as correct in every field but one. The assertion
    # below is what makes it loud.
    ry = (r1[1] * r2[2] - r1[2] * r2[1],
          r1[2] * r2[0] - r1[0] * r2[2],
          r1[0] * r2[1] - r1[1] * r2[0])
    rot = [[r1[0], ry[0], r2[0]],
           [r1[1], ry[1], r2[1]],
           [r1[2], ry[2], r2[2]]]
    eye = [-sum(rot[k][i] * t[k] for k in range(3)) for i in range(3)]

    fwd = (rot[2][0], rot[2][1], rot[2][2])
    if eye[1] <= focus_y:
        raise ValueError(
            f"the fit puts the camera at y={eye[1]:.2f}, at or below the "
            f"ground plane at y={focus_y:.2f}. A camera under the board did "
            "not take this picture, so the decomposition is wrong -- check "
            "the marks are matched to the right coordinates.")
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -fwd[1]))))
    yaw = math.degrees(math.atan2(fwd[0], fwd[2])) % 360.0
    dist = ((eye[1] - focus_y) / math.sin(math.radians(pitch))
            if abs(math.sin(math.radians(pitch))) > 1e-6 else float("inf"))

    lens = Lens(width, height, math.degrees(2 * math.atan(cy / focal)))
    # The solve gives an eye; a Pose is held as the focus it orbits. They are
    # the same thing seen from opposite ends: walk `dist` along the view ray.
    p_rad, y_rad = math.radians(pitch), math.radians(yaw)
    pose = Pose(fx=eye[0] + dist * math.sin(y_rad) * math.cos(p_rad),
                fz=eye[2] + dist * math.cos(y_rad) * math.cos(p_rad),
                focus_y=focus_y, dist=dist, yaw=yaw, pitch=pitch)

    cam = Camera(lens, pose)
    errs = []
    for (bx, bz), (u, v) in pairs:
        p = cam.project(bx, focus_y, bz)
        errs.append(math.hypot(p[0] - u, p[1] - v) if p else 1e6)
    return Fit(pose, focal, lens.fov_v_deg,
               sum(errs) / len(errs), disagreement, len(pairs),
               focal_cross_checked=len(est) == 2)


# --------------------------------------------------------------------------
# reading one shot of the target, with its own objections
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Reading:
    """The marks found in one screenshot, and everything wrong with them.

    `problems` is the load-bearing field. A calibration that quietly accepts a
    shot where one mark was clipped by the frame edge produces a pose that is
    confidently wrong, and every constant derived from it inherits the error
    without a hint. Anything in this list means the shot should be dropped, not
    weighted down.
    """

    marks: list[Mark]
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def locate(img: Image, sizes: Sequence[int], *,
           roi: tuple[int, int, int, int] | None = None,
           min_area: int = 60, edge_px: int = 2) -> Reading:
    """Find the size-coded marks, and object rather than guess.

    Three ways a shot can be unusable, all of them silent if unchecked:

    * **Too few blobs.** Part of the target is out of frame, or a mark has
      fallen under `min_area` because the camera climbed.
    * **A blob touching the search border.** Its centroid is then the centroid
      of whatever survived the crop, which is not where the mark is. This is
      the one that would otherwise pass every other check.
    * **Areas in the wrong proportion.** The marks are built square with sides
      `sizes`, so their areas should fall off as the square of the side. If the
      measured areas do not, the ranking has picked up something that is not a
      mark -- and rank is exactly how each blob gets its coordinates.
    """
    x0, y0, x1, y1 = roi or (0, 0, img.width, img.height)
    found = find_marks(img, min_area=min_area, roi=roi)
    problems: list[str] = []
    if len(found) < len(sizes):
        problems.append(
            f"found {len(found)} marks, need {len(sizes)}: part of the target "
            "is out of frame, or a mark is under the area floor")
        return Reading(found, problems)

    marks = found[:len(sizes)]
    for want, m in zip(sizes, marks):
        bx0, by0, bx1, by1 = m.bbox
        if (bx0 <= x0 + edge_px or by0 <= y0 + edge_px
                or bx1 >= x1 - edge_px - 1 or by1 >= y1 - edge_px - 1):
            problems.append(
                f"the {want}x{want} mark touches the search border at "
                f"{m.bbox}: its centroid is the centroid of the visible part, "
                "not of the mark")

    # Areas should scale as the square of the side. Perspective stretches this
    # -- a near mark covers more pixels than a far one of the same size -- so
    # the tolerance is wide. It is still tight enough to catch a blob that is
    # not a mark at all, which is what it is for.
    scale = marks[0].area_px / (sizes[0] ** 2)
    for want, m in zip(sizes, marks):
        expect = scale * want ** 2
        if not (expect / 3.0 <= m.area_px <= expect * 3.0):
            problems.append(
                f"the mark ranked as {want}x{want} covers {m.area_px} px, "
                f"against {expect:.0f} expected: the size ranking has picked "
                "up something that is not a mark")
    return Reading(marks, problems)


def read_shot(path, centres: Sequence[tuple[float, float]],
              sizes: Sequence[int], *,
              roi: tuple[int, int, int, int] | None = None,
              min_area: int = 60, focus_y: float = 0.0,
              squares: Sequence[tuple[float, float, float]] | None = None,
              focal_px: float | None = None,
              expect: Camera | None = None
              ) -> tuple[Fit | None, Reading]:
    """One PNG in, one pose out -- or None and the reason why.

    Pass `squares` -- each mark's low corner and side -- to take out the
    centroid bias described in :func:`projected_centroid`. Without it the fit
    is measuring blob centroids against predicted centres, which are not the
    same point.

    Pass `expect` -- a camera at roughly the right pose -- and the blobs are
    matched to marks by **position** instead of by size. That is the better
    correspondence whenever anything already knows roughly where the camera is,
    which after a planned move it does: perspective can reorder mark areas, but
    it cannot move a mark to another mark's place.
    """
    img = read_png(path)
    reading = locate(img, sizes, roi=roi, min_area=min_area)
    if not reading.ok:
        return None, reading
    measured = [(m.u, m.v) for m in reading.marks]
    if expect is not None and squares:
        blobs = find_marks(img, min_area=min_area, roi=roi)
        pairs, problems = match_near(blobs, squares, expect, focus_y=focus_y)
        if problems or len(pairs) < 4:
            return None, dataclasses.replace(
                reading, problems=problems or ["too few marks matched"])
        squares = [sq for sq, _ in zip(squares, pairs)]
        measured = [uv for _, uv in pairs]
    else:
        # **Where the size ranking is close, TRY BOTH and let the residual
        # decide.** Refusing was tried first and refused a pose that then
        # fitted at 0.53 px: at the base pose the 5x5 and 4x4 marks differ by
        # only 1.20x in area, which is ambiguous-looking and still correctly
        # ordered. The residual is the discriminator that already exists -- a
        # swapped pair reprojects an order of magnitude worse -- so ambiguity
        # is a reason to check, not a reason to stop.
        best = None
        for order in _candidate_orders(reading.marks, sizes):
            trial = [measured[i] for i in order]
            try:
                cand = solve_pose(list(zip(centres, trial)),
                                  img.width, img.height, focus_y=focus_y,
                                  focal_px=focal_px)
            except ValueError:
                continue
            if best is None or cand.residual_px < best[0].residual_px:
                best = (cand, trial)
        if best is None:
            return None, dataclasses.replace(
                reading, problems=["no ordering of the marks solved"])
        fit, measured = best
        if squares:
            fit = refine_pose(fit, squares, measured, img.width, img.height,
                              focus_y=focus_y, focal_px=focal_px)
        return fit, reading
    try:
        fit = solve_pose(pairs, img.width, img.height, focus_y=focus_y,
                         focal_px=focal_px)
    except ValueError:
        return None, dataclasses.replace(
            reading, problems=["the homography did not solve"])
    if squares:
        fit = refine_pose(fit, squares, measured, img.width, img.height,
                          focus_y=focus_y, focal_px=focal_px)
    return fit, reading


# --------------------------------------------------------------------------
# a mark's centroid is not its centre
# --------------------------------------------------------------------------


def projected_centroid(cam: Camera, x0: float, z0: float, size: float,
                       plane_y: float = 0.0, n: int = 11
                       ) -> tuple[float, float] | None:
    """Where the *centroid* of a square mark lands on screen.

    Not the same point as the projection of the square's centre, and the gap
    is not small: perspective magnifies the near half of a mark more than the
    far half, so the blob's centre of area sits nearer the camera than its
    middle does. On a 6x6 mark at 40 px/tile that was **3.1 px of residual**,
    which is above the threshold at which a fit is allowed to call itself
    trustworthy -- so it read as "the pinhole model does not hold here" when
    it was really "the thing being measured is not the thing being predicted".

    The weighting is what makes it a centroid rather than an average: image
    area per unit of world area goes as `1/depth^3` for a plane, so each
    sample is weighted that way. Averaging the projected samples unweighted
    gives the image of the world centroid -- which is the very quantity that
    is wrong.
    """
    su = sv = sw = 0.0
    eye, fwd = cam.pose.eye, cam.pose.forward
    for i in range(n):
        for j in range(n):
            x = x0 + (i + 0.5) / n * size
            z = z0 + (j + 0.5) / n * size
            p = cam.project(x, plane_y, z)
            if p is None:
                continue
            d = (x - eye[0], plane_y - eye[1], z - eye[2])
            depth = _dot(d, fwd)
            if depth <= 1e-6:
                continue
            w = 1.0 / (depth ** 3)
            su += p[0] * w
            sv += p[1] * w
            sw += w
    if sw <= 0:
        return None
    return (su / sw, sv / sw)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def refine_pose(fit: Fit, squares: Sequence[tuple[float, float, float]],
                measured: Sequence[tuple[float, float]],
                width: int, height: int, *, focus_y: float = 0.0,
                focal_px: float | None = None, rounds: int = 3) -> Fit:
    """Re-fit with each mark's centroid bias taken out.

    One pass would do most of it; three costs nothing and lets the correction
    settle, since the bias depends on the pose it is correcting. Returns the
    original fit unchanged if a round fails to solve -- a refinement that
    cannot improve a fit must not be allowed to destroy one.
    """
    best = fit
    for _ in range(rounds):
        lens = Lens(width, height, best.fov_v_deg)
        cam = Camera(lens, best.pose)
        pairs = []
        for (x0, z0, size), (u, v) in zip(squares, measured):
            centre = (x0 + size / 2.0, z0 + size / 2.0)
            want = cam.project(centre[0], focus_y, centre[1])
            got = projected_centroid(cam, x0, z0, size, focus_y)
            if want is None or got is None:
                pairs.append((centre, (u, v)))
                continue
            # The blob's centroid sits at `got`; the homography wants the
            # centre's own image. Move the measurement by the same offset.
            pairs.append((centre, (u - (got[0] - want[0]),
                                   v - (got[1] - want[1]))))
        try:
            cand = solve_pose(pairs, width, height, focus_y=focus_y,
                              focal_px=focal_px)
        except ValueError:
            return best
        if cand.residual_px >= best.residual_px and best is not fit:
            return best
        best = cand
    return best


# --------------------------------------------------------------------------
# matching blobs to marks when a pose is already expected
# --------------------------------------------------------------------------


def rank_is_ambiguous(marks: Sequence[Mark], sizes: Sequence[int],
                      ratio: float = 1.35) -> str | None:
    """Is the size ranking safe here? Names the offending pair if not.

    Size-coding marks 6, 5, 4, 3, 2 works while perspective spreads areas less
    than the size steps do. Close in, it does not: at a slant range of 35 tiles
    a 4x4 mark near the camera covered 49,540 px against a far 5x5's 56,165 --
    an 11% margin on a step that should be 56%, and the next frame in would
    have swapped them and handed each the other's coordinates.

    The ratio floor is `(n/(n-1))^2` for the tightest step in use, halved:
    5x5 against 6x6 is 1.44, so 1.35 flags a pair before it actually crosses.
    """
    for i in range(len(marks) - 1):
        big, small = marks[i].area_px, marks[i + 1].area_px
        if small <= 0:
            continue
        if big / small < ratio:
            return (f"the {sizes[i]}x{sizes[i]} and {sizes[i + 1]}x"
                    f"{sizes[i + 1]} marks differ by only "
                    f"{big / small:.2f}x in area ({big} against {small}); "
                    "perspective is close to reordering them, so which blob "
                    "is which cannot be trusted from size alone")
    return None


def _candidate_orders(marks: Sequence[Mark], sizes: Sequence[int],
                      max_swaps: int = 3) -> list[list[int]]:
    """Index orders worth trying: the ranked one, plus ambiguous pairs swapped.

    Only *adjacent* ranks can swap -- perspective can make a near mark outrank
    the next size up, and has, but it does not reorder marks two sizes apart at
    any pose this camera can reach. Capped so the search stays at eight
    orderings rather than a factorial.
    """
    close = [i for i in range(len(marks) - 1)
             if marks[i + 1].area_px > 0
             and marks[i].area_px / marks[i + 1].area_px < 1.35][:max_swaps]
    orders = [list(range(len(marks)))]
    for i in close:
        for base in list(orders):
            swapped = list(base)
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            orders.append(swapped)
    return orders


def match_near(marks: Sequence[Mark],
               squares: Sequence[tuple[float, float, float]],
               cam: Camera, *, focus_y: float = 0.0,
               max_px: float = 200.0
               ) -> tuple[list[tuple[tuple[float, float],
                                     tuple[float, float]]], list[str]]:
    """Pair blobs with marks by *where they should be*, given a pose.

    Size-coding is how a shot is read cold. This is how it is read when
    something already knows roughly where the camera is -- after a planned
    move, for instance, where the plan's own prediction is available. It is
    strictly better when it applies: perspective can reorder areas but it
    cannot move a mark to another mark's place.

    Greedy nearest-first, and it **refuses** rather than guessing when a mark's
    nearest blob is further than `max_px` or when two marks want the same
    blob -- either means the prediction was too far out to bootstrap from, and
    a wrong correspondence produces a confident, wrong pose.
    """
    want = []
    for x0, z0, size in squares:
        centre = (x0 + size / 2.0, z0 + size / 2.0)
        p = projected_centroid(cam, x0, z0, size, focus_y)
        if p is None:
            return [], ["a mark is behind the predicted camera; the "
                        "prediction is too far out to match against"]
        want.append((centre, p))

    problems: list[str] = []
    used: dict[int, int] = {}
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i, (centre, expect) in enumerate(want):
        best, best_d = None, None
        for j, m in enumerate(marks):
            d = math.hypot(m.u - expect[0], m.v - expect[1])
            if best_d is None or d < best_d:
                best, best_d = j, d
        if best is None or best_d > max_px:
            problems.append(
                f"the mark at {centre} was predicted at "
                f"({expect[0]:.0f}, {expect[1]:.0f}) and the nearest blob is "
                f"{best_d:.0f} px away, past the {max_px:.0f} px limit")
            continue
        if best in used:
            problems.append(
                f"two marks both matched the same blob (#{best}); the "
                "prediction is not close enough to match against")
            continue
        used[best] = i
        pairs.append((centre, (marks[best].u, marks[best].v)))
    return pairs, problems
