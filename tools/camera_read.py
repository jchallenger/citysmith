"""Where is the camera, in numbers, from one screenshot.

`ts.ps1 camerastate` photographs the compass rose and leaves a human to read a
bearing off it. That is genuinely useful and it is not a measurement: it cannot
give a height, a distance or a focus point, and it cannot be compared with
anything. This can. Point it at a PNG of a board carrying the calibration
target and it reports the pose that took the picture.

    python tools/camera_read.py out/camcal/base.png
    python tools/camera_read.py out/flyby/shot.png --json

It answers three different questions and it is worth knowing which one you are
asking, because they fail differently:

* **Where is the camera?** Needs all six marks. Reports pose, field of view and
  both self-checks.
* **Is the target framed at a usable size?** Needs only the blobs, so it still
  answers when the pose does not solve. This is what the levelling loop in
  `camera_calib.ps1` reads: `largest_area` against a band.
* **Is this shot fit to measure from?** `ok` and `problems`, which is the
  question a calibration must ask before believing anything downstream.

Exit status is 0 when the pose solved, 1 when it did not, so a driving script
can branch on it without parsing anything.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import camera as C
from citysmith import camerafit as F

TARGET = pathlib.Path("out/camtarget.json")


def load_target(path=TARGET):
    doc = json.loads(pathlib.Path(path).read_text())
    return ([tuple(c) for c in doc["centres_by_descending_size"]],
            [m["size"] for m in doc["marks"]],
            [(m["x"], m["z"], m["size"]) for m in doc["marks"]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("shot")
    ap.add_argument("--target", default=str(TARGET))
    ap.add_argument("--roi", default=None,
                    help="x0,y0,x1,y1, or 'full'. Default: the board area, "
                         "with the HUD excluded by camerafit.hud_roi")
    ap.add_argument("--min-area", type=int, default=60)
    ap.add_argument("--expect", default=None,
                    help="fx,fz,dist,yaw,pitch -- a pose the camera is "
                         "expected to be near. Marks are then matched by "
                         "WHERE they should be rather than by size, which is "
                         "the better correspondence close in, where "
                         "perspective can reorder the mark areas.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    centres, sizes, squares = load_target(args.target)
    img = F.read_png(args.shot)
    if args.roi == "full":
        roi = None
    elif args.roi:
        roi = tuple(int(v) for v in args.roi.split(","))
    else:
        roi = F.hud_roi(img.width, img.height)
    blobs = F.find_marks(img, min_area=args.min_area, roi=roi)
    reading = F.locate(img, sizes, roi=roi, min_area=args.min_area)

    out: dict = {
        "shot": args.shot,
        "frame": [img.width, img.height],
        "blobs": len(blobs),
        "areas": [b.area_px for b in blobs[:len(sizes) + 2]],
        "largest_area": blobs[0].area_px if blobs else 0,
        "ok": reading.ok,
        "problems": reading.problems,
        "pose": None,
        "roi": list(roi) if roi else None,
    }

    # **`ok` and a solved pose have to agree.** They did not: `read_shot`
    # returns None rather than raising when the homography will not solve, so a
    # shot could come back `ok: true` with `pose: null` -- and the caller that
    # branched on `ok` then read zeroes and reported them as the camera's
    # position. Whatever stops the pose being solved is a problem with the
    # shot, and it is recorded as one.
    fit = None
    if reading.ok:
        try:
            expect = None
            if args.expect:
                fx, fz, dist, yaw, pitch = (
                    float(v) for v in args.expect.split(","))
                expect = C.Camera(
                    C.Lens(img.width, img.height,
                           C.load_rig()["fov_v_deg"]),
                    C.Pose(fx, fz, 0.0, dist, yaw, pitch))
            fit, again = F.read_shot(args.shot, centres, sizes, roi=roi,
                                     min_area=args.min_area, squares=squares,
                                     expect=expect)
            if fit is None:
                out["problems"] = again.problems or ["the pose did not solve"]
                out["ok"] = False
        except ValueError as exc:
            out["problems"] = [str(exc)]
            out["ok"] = False
    # **A pose the rig cannot reach is a wrong fit, whatever its residual.**
    # This caught one: a shot whose marks all sat inside the search area, whose
    # ranking looked sound and whose fit came back at a slant range of 92.9
    # tiles -- against a Ctrl+scroll stop measured at 49.75. Nothing else in
    # the reader could see that, because every other check is internal to the
    # shot. The constants are an *independent* statement about the same camera,
    # so they are worth spending here.
    #
    # Only the measured limits are used. An assumed one would reject good
    # poses on the strength of a guess, which is the opposite of the point.
    if fit is not None:
        rig = C.load_rig()
        for key, value, what, slack in (
                ("dist_max", fit.pose.dist, "slant range", 1.10),
                ("pitch_max_deg", fit.pose.pitch, "pitch", 1.03)):
            if rig.is_measured(key) and value > rig[key] * slack:
                out["problems"] = out["problems"] + [
                    f"the fit puts the {what} at {value:.2f}, past the "
                    f"measured stop of {rig[key]:.2f}. The camera cannot be "
                    "there, so the marks were matched wrongly."]
                out["ok"] = False
                fit = None
                break

    if fit is not None:
        out["pose"] = fit.as_json()
        lens = C.Lens(img.width, img.height, fit.fov_v_deg)
        cam = C.Camera(lens, fit.pose)
        across, along = cam.px_per_tile()
        out["px_per_tile"] = {"across": round(across, 2),
                              "along": round(along, 2)}
        out["visible_bounds"] = [round(v, 1) for v in cam.visible_bounds()]
        out["sees_horizon"] = cam.sees_horizon()

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if fit is not None else 1

    print(f"{args.shot}  {img.width}x{img.height}  searched {roi}")
    print(f"  green blobs {len(blobs)}, areas {out['areas']}")
    # `out["problems"]`, not `reading.problems`: the reach check below the fit
    # adds to the first and not the second, and printing only the reading's own
    # objections reported "pose: NOT SOLVED" with no reason under it.
    for problem in out["problems"]:
        print(f"  ! {problem}")
    if fit is None:
        print("  pose: NOT SOLVED")
        return 1
    p = fit.pose
    print(f"  yaw {p.yaw:.2f} deg ({C._bearing_name(p.yaw)})   "
          f"pitch {p.pitch:.2f} deg below horizon")
    print(f"  eye height {p.height:.2f} tiles   slant range {p.dist:.2f} tiles")
    print(f"  looking at ({p.fx:.2f}, {p.fz:.2f}) in target coordinates")
    print(f"  field of view {fit.fov_v_deg:.2f} deg vertical")
    print(f"  px per tile: {out['px_per_tile']['across']} across, "
          f"{out['px_per_tile']['along']} along")
    print(f"  ground in frame: x {out['visible_bounds'][0]} to "
          f"{out['visible_bounds'][2]}, z {out['visible_bounds'][1]} to "
          f"{out['visible_bounds'][3]}"
          + ("  (horizon in shot)" if out["sees_horizon"] else ""))
    xc = "" if fit.focal_cross_checked else "  focal NOT cross-checked"
    print(f"  fit: {fit.residual_px:.2f} px residual over {fit.marks} marks"
          f"{xc}"
          + ("" if fit.trustworthy else "   <-- NOT TRUSTWORTHY"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
