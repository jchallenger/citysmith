"""The board seen *through the measured camera*, as flat polygons to draw.

The camera screen's plan view answers "where does the frame fall". It cannot
answer "what will the shot look like", and that is the question a reviewer
actually has before flying anywhere.

This projects the town through :class:`citysmith.camera.Camera` -- the same
model, with the same measured constants, that decides where the real camera
goes -- and returns screen-space polygons. **The browser does no 3D.** It draws
the quads it is handed, in the order it is handed them.

That split is deliberate and it is the same rule the plan view follows: two
implementations of a frustum is two frustums, and only one of them was measured
against the game. Here it matters more, because a preview that projects
differently from the planner would show you a shot and then take a different
one -- which is precisely the failure the whole camera model exists to end.

What you see is therefore a *prediction of the frame*, not an illustration of
it. If a building is off the left edge here it will be off the left edge in
TaleSpire.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Iterable, Sequence

from .camera import Camera

#: A storey is the wall, and the wall is 2.0 tiles. Same number the builder
#: pitches its floors at -- see CLAUDE.md, "The storey is the wall". A preview
#: whose buildings are a different height from the ones the builder emits would
#: mislead about exactly the thing it is for.
STOREY_TILES = 2.0

#: Sunlight for the shading, as a direction. Not the game's light: TaleSpire's
#: is a board setting with a day cycle behind it. This exists so the three
#: faces of a box read as three faces, and it is fixed so that turning the
#: camera does not make the town appear to change colour.
_LIGHT = (0.40, 0.82, -0.41)


@dataclasses.dataclass(frozen=True)
class Box:
    """One building, as the box a plan view can honestly claim to know.

    It carries the metadata the layout actually has, because a preview that
    draws every building the same colour is a picture of a town's *shape* and
    nothing else. Measured on East Tradebourne: 709 of 991 buildings are
    `house`, so colouring by kind leaves 72% of the town one colour -- while
    `floors` runs 169/575/247 across one, two and three storeys and varies
    everywhere. Which field is worth looking at is a question about the data,
    not a matter of taste, so the client is given both and chooses.
    """

    x0: float
    z0: float
    x1: float
    z1: float
    height: float
    kind: str = "house"
    name: str = ""
    floors: int = 1
    stone: bool = False


def boxes_from_layout(layout, *, max_boxes: int = 4000) -> list[Box]:
    """Building footprints and heights, in tiles.

    Bounding boxes rather than outlines. At preview scale the two are the same
    picture, and the box is what the plan view already sends -- one shape for
    both views means they cannot disagree about where a building is.
    """
    out: list[Box] = []
    for b in layout.buildings:
        if not b.ring:
            continue
        xs = [p[0] for p in b.ring]
        zs = [p[1] for p in b.ring]
        floors = max(1, b.floors)
        out.append(Box(min(xs), min(zs), max(xs), max(zs),
                       floors * STOREY_TILES, b.kind,
                       name=b.name, floors=floors, stone=b.stone))
    return out[:max_boxes]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _shade(normal) -> float:
    """0.35 to 1.0. Flat lambert, clamped so nothing goes fully black."""
    return round(0.35 + 0.65 * max(0.0, _dot(normal, _LIGHT)), 3)


#: The six faces of an axis-aligned box: which corners, and the outward normal.
_FACES = (
    ((4, 5, 6, 7), (0.0, 1.0, 0.0)),    # top
    ((0, 1, 5, 4), (0.0, 0.0, -1.0)),   # -z
    ((1, 2, 6, 5), (1.0, 0.0, 0.0)),    # +x
    ((2, 3, 7, 6), (0.0, 0.0, 1.0)),    # +z
    ((3, 0, 4, 7), (-1.0, 0.0, 0.0)),   # -x
)


def _corners(box: Box):
    return (
        (box.x0, 0.0, box.z0), (box.x1, 0.0, box.z0),
        (box.x1, 0.0, box.z1), (box.x0, 0.0, box.z1),
        (box.x0, box.height, box.z0), (box.x1, box.height, box.z0),
        (box.x1, box.height, box.z1), (box.x0, box.height, box.z1),
    )


def render(cam: Camera, boxes: Sequence[Box], *,
           ground: Sequence[Sequence[Sequence[float]]] = (),
           water: Sequence[Sequence[Sequence[float]]] = (),
           max_faces: int = 2500) -> dict:
    """Project a scene into screen-space polygons, furthest first.

    Back-face culled and painter-sorted. Painter's algorithm is wrong in
    general -- it cannot resolve boxes that interleave -- and it is right
    enough here, because these are separated buildings on flat ground rather
    than a mesh. Saying so is the point: this is a preview of a framing, not a
    renderer, and it is not asked to resolve anything the model cannot.

    Anything wholly off screen is dropped before it is projected further, which
    is what keeps a 991-building town interactive: in a typical frame a handful
    of buildings are visible and the rest cost one bounding test each.
    """
    # **The basis, computed once.** `Pose.eye`, `.forward`, `.right` and `.up`
    # are properties that each run their own sin/cos, and `Camera.project`
    # asks for three of them per call. Over 991 boxes, five faces and four
    # corners that is tens of thousands of trig calls and it measured 93 ms a
    # frame -- a slideshow to drag. Hoisting them takes the same arithmetic
    # from the same camera and does it once.
    eye = cam.pose.eye
    right, up, fwd = cam.pose.right, cam.pose.up, cam.pose.forward
    focal = cam.lens.focal_px
    cx, cy = cam.lens.centre
    w, h = cam.lens.width, cam.lens.height

    def project(x, y, z):
        dx, dy, dz = x - eye[0], y - eye[1], z - eye[2]
        zc = dx * fwd[0] + dy * fwd[1] + dz * fwd[2]
        if zc <= 1e-6:
            return None
        s = focal / zc
        return (cx + (dx * right[0] + dy * right[1] + dz * right[2]) * s,
                cy - (dx * up[0] + dy * up[1] + dz * up[2]) * s, zc)

    def project_ring(ring, y=0.0):
        pts = []
        for x, z in ring:
            q = project(x, y, z)
            if q is None:
                return None
            pts.append([round(q[0], 1), round(q[1], 1)])
        return pts

    faces: list[tuple[float, dict]] = []

    for ring in water:
        pts = project_ring(ring)
        if pts and _touches(pts, w, h):
            # Furthest of all: water is a surface at y=0 and every building
            # stands on top of it.
            faces.append((float("inf"), {"pts": pts, "kind": "water",
                                         "shade": 1.0}))
    for ring in ground:
        pts = project_ring(ring)
        if pts and _touches(pts, w, h):
            faces.append((float("inf") - 1, {"pts": pts, "kind": "ground",
                                             "shade": 1.0}))

    for bi, box in enumerate(boxes):
        centre = ((box.x0 + box.x1) / 2.0, box.height / 2.0,
                  (box.z0 + box.z1) / 2.0)
        # **Reject the box before projecting its faces.** One projection of the
        # centre plus a radius test replaces twenty of its corners, and in a
        # typical frame it rejects almost the whole town: East Tradebourne has
        # 991 buildings and a few dozen are ever in shot.
        mid = project(*centre)
        if mid is None:
            continue
        radius = 0.5 * math.dist(
            (box.x0, 0.0, box.z0), (box.x1, box.height, box.z1))
        pad = radius * focal / mid[2] + 8.0
        if (mid[0] < -pad or mid[0] > w + pad
                or mid[1] < -pad or mid[1] > h + pad):
            continue
        corners = _corners(box)
        depth = math.dist(centre, eye)
        drawn = []
        for idx, normal in _FACES:
            # Back-face cull against the eye, not the view direction: a face
            # is visible when the eye is on its outward side.
            first = corners[idx[0]]
            to_eye = (eye[0] - first[0], eye[1] - first[1], eye[2] - first[2])
            if _dot(normal, to_eye) <= 0:
                continue
            pts = []
            for i in idx:
                p = project(*corners[i])
                if p is None:
                    pts = None
                    break
                pts.append([round(p[0], 1), round(p[1], 1)])
            if pts and _touches(pts, w, h):
                drawn.append({"pts": pts, "kind": box.kind,
                              "shade": _shade(normal), "b": bi})
        for face in drawn:
            faces.append((depth, face))

    faces.sort(key=lambda d: -d[0])
    clipped = len(faces) - max_faces
    kept = [f for _, f in faces[:max_faces]]
    # Only the buildings with a face in shot. Sending all 991 every frame to
    # name the dozen that are visible is most of the payload for none of the
    # value; the face's `b` is re-pointed at this shorter list.
    seen: dict[int, int] = {}
    for face in kept:
        bi = face.get("b")
        if bi is None:
            continue
        if bi not in seen:
            seen[bi] = len(seen)
        face["b"] = seen[bi]
    shown = [None] * len(seen)
    for bi, j in seen.items():
        b = boxes[bi]
        shown[j] = {"name": b.name, "kind": b.kind, "floors": b.floors,
                    "stone": b.stone,
                    "at": [round((b.x0 + b.x1) / 2, 1),
                           round((b.z0 + b.z1) / 2, 1)],
                    "size": [round(b.x1 - b.x0, 1), round(b.z1 - b.z0, 1)]}
    return {
        "faces": kept,
        "buildings": shown,
        "dropped": max(0, clipped),
        "frame": [w, h],
    }


def _touches(pts, w: float, h: float, slack: float = 400.0) -> bool:
    """Is any of this polygon near the frame?

    Generous, because a building can be mostly off screen and still show a
    sliver. It is a cheap reject for the thousand that are nowhere near, not a
    clip.
    """
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) > -slack and min(xs) < w + slack
            and max(ys) > -slack and min(ys) < h + slack)


def ground_grid(extent, step: float = 20.0, limit: int = 120):
    """The board edge and a coarse grid on it, as rings at y=0.

    Something has to establish the ground plane or a box floats in a void with
    no way to read its distance. A grid does it with almost no geometry.
    """
    x0, z0, x1, z1 = extent
    rings = [[[x0, z0], [x1, z0], [x1, z1], [x0, z1]]]
    n = 0
    x = x0 + step
    while x < x1 and n < limit:
        rings.append([[x, z0], [x, z1]])
        x += step
        n += 1
    z = z0 + step
    while z < z1 and n < limit:
        rings.append([[x0, z], [x1, z]])
        z += step
        n += 1
    return rings
