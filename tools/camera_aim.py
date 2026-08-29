"""Aim the camera: work out the moves, print them, and say what they will do.

This is what the model is for. `camera_read.py` says where the camera is;
`citysmith/camera.py` says what each input does to it; put the two together and
"get me a shot of the north fields" becomes a short list of `ts.ps1` calls
rather than a flight.

    python tools/camera_aim.py --from out/camcal/base.png --yaw 45 --pitch 50
    python tools/camera_aim.py --from shot.png --frame 40,120,90,170
    python tools/camera_aim.py --frame 40,120,90,170 --targets fences.json

Three things it will tell you that a flight will not:

* **Whether the shot is possible at all.** The camera stops pitching at 78
  degrees and stops pulling back at a slant range of about 50 tiles, both
  measured, so a rectangle bigger than roughly 40 by 25 tiles cannot be framed
  in one shot. It says so and offers a shot list instead of quietly cropping.
* **What will be in frame.** `--targets` takes a list of board rectangles --
  field walls, a quarter, a set of buildings -- and reports how many of them
  the framing actually holds. That is the check that was missing when fences
  were built, shipped and reviewed twice from crops that contained none of
  them.
* **Which of its numbers are guesses.** Every plan lists the constants it
  leant on that are not measurements.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import camera as C
from citysmith import camerafit as F


def read_pose(shot: str, target_json: str) -> tuple[C.Pose, C.Lens]:
    doc = json.loads(pathlib.Path(target_json).read_text())
    centres = [tuple(c) for c in doc["centres_by_descending_size"]]
    sizes = [m["size"] for m in doc["marks"]]
    squares = [(m["x"], m["z"], m["size"]) for m in doc["marks"]]
    img = F.read_png(shot)
    fit, reading = F.read_shot(shot, centres, sizes,
                               roi=F.hud_roi(img.width, img.height),
                               squares=squares)
    if fit is None:
        raise SystemExit(
            f"cannot read a pose from {shot}: {'; '.join(reading.problems)}")
    return fit.pose, C.Lens(img.width, img.height, fit.fov_v_deg)


def slab_extent(path: str) -> tuple[float, float, float, float]:
    """The ground rectangle a slab covers, as ``(x0, z0, x1, z1)``.

    **Read off the emitted boxes, not off the stored coordinates.** A stored
    coordinate is the asset's origin, which for a prop is its collider centre
    -- so a pine on the edge of a board extends past its own coordinate by
    half its canopy. `build.placed_bounds` is the one place that knows how to
    turn one back into a box, and this is the same rule the chunker follows.

    This exists so a review never has to be told how big the thing it is
    looking at is. Every probe tool here already computes an extent and then
    throws it away; the reviewer then flies around hunting for the edges,
    which cost four exchanges in the session that prompted this.
    """
    import sys as _sys
    _sys.path.insert(0, ".")
    from citysmith.build import placed_bounds
    from citysmith.catalog import load_or_build
    from citysmith.slab import decode

    byid = {a.id: a for a in load_or_build().assets}
    slab = decode(pathlib.Path(path).read_text(encoding="utf-8").strip())
    xs0, zs0, xs1, zs1 = [], [], [], []
    for pl in slab.placements:
        a = byid.get(pl.asset_id)
        if a is None:
            continue
        x0, z0, x1, z1 = placed_bounds(a, pl)
        xs0.append(x0); zs0.append(z0); xs1.append(x1); zs1.append(z1)
    if not xs0:
        raise SystemExit(f"{path}: no placements with known assets")
    return (min(xs0), min(zs0), max(xs1), max(zs1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="shot", default=None,
                    help="a PNG of the calibration target, to read the "
                         "current pose from. Without it, --at gives the pose.")
    ap.add_argument("--at", default=None,
                    help="current pose as fx,fz,dist,yaw,pitch")
    ap.add_argument("--target", default="out/camtarget.json")
    ap.add_argument("--frame", default=None,
                    help="board rectangle to frame: x0,z0,x1,z1")
    ap.add_argument("--slab", default=None,
                    help="a .slab.txt to frame -- its own ground extent is "
                         "read out of it, so a probe board never has to have "
                         "its size typed in or read off the screen")
    ap.add_argument("--targets", default=None,
                    help="JSON list of [x0,z0,x1,z1] to report coverage of")
    ap.add_argument("--yaw", type=float, default=None)
    ap.add_argument("--pitch", type=float, default=None)
    ap.add_argument("--dist", type=float, default=None)
    ap.add_argument("--margin", type=float, default=40.0,
                    help="pixels of clearance to keep inside the frame")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--config", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rig = C.load_rig(args.config)
    unknown = C.unknown_keys(args.config)
    if unknown:
        print(f"! config has keys the model does not know, and they do "
              f"NOTHING: {', '.join(unknown)}", file=sys.stderr)

    if args.shot:
        start, lens = read_pose(args.shot, args.target)
    elif args.at:
        fx, fz, dist, yaw, pitch = (float(v) for v in args.at.split(","))
        start = C.Pose(fx, fz, 0.0, dist, yaw, pitch)
        lens = rig.lens(args.width, args.height)
    else:
        raise SystemExit("give --from <shot.png> or --at fx,fz,dist,yaw,pitch")

    if args.slab and not args.frame:
        args.frame = "%g,%g,%g,%g" % slab_extent(args.slab)

    if args.frame:
        rect = [float(v) for v in args.frame.split(",")]
        framing = C.frame_rect(
            rect, rig=rig, lens=lens,
            yaw=args.yaw if args.yaw is not None else start.yaw,
            pitch=args.pitch if args.pitch is not None else start.pitch,
            margin_px=args.margin)
        target = framing.pose
    else:
        framing = None
        target = C.Pose(
            start.fx, start.fz, start.focus_y,
            args.dist if args.dist is not None else start.dist,
            args.yaw if args.yaw is not None else start.yaw,
            args.pitch if args.pitch is not None else start.pitch)

    plan = C.plan(start, target, rig=rig, lens=lens)
    cam = C.Camera(lens, plan.end)
    bounds = cam.visible_bounds()

    payload = {
        "start": start.as_json(),
        "target": target.as_json(),
        "plan": plan.as_json(),
        "visible_bounds": [round(v, 1) for v in bounds],
        "sees_horizon": cam.sees_horizon(),
        "px_per_tile": [round(v, 2) for v in cam.px_per_tile()],
    }
    if framing is not None:
        payload["framing"] = framing.as_json()

    if args.targets:
        rects = json.loads(pathlib.Path(args.targets).read_text())
        held = [i for i, r in enumerate(rects)
                if cam.covers_all(C.rect_corners(r), args.margin)]
        payload["targets"] = {"total": len(rects), "in_frame": len(held),
                              "indices": held}

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"from  {_pose(start)}")
    print(f"to    {_pose(target)}")
    if framing is not None:
        mark = "fits" if framing.fits else "DOES NOT FIT"
        print(f"frame {mark}: {framing.note}")
    print()
    for m in plan.moves:
        print(f"  {m.command}")
        print(f"      # {m.note}")
    if not plan.moves:
        print("  (already there)")
    print()
    r = plan.residual()
    print(f"lands at  yaw {plan.end.yaw:.1f}  pitch {plan.end.pitch:.1f}  "
          f"dist {plan.end.dist:.1f}")
    print(f"short by  yaw {r['yaw_deg']:+.2f} deg, pitch {r['pitch_deg']:+.2f} "
          f"deg, range {r['dist_tiles']:+.2f} tiles, focus "
          f"{r['focus_tiles']:.2f} tiles")
    print(f"in frame  x {bounds[0]:.0f} to {bounds[2]:.0f}, "
          f"z {bounds[1]:.0f} to {bounds[3]:.0f}"
          + ("   (horizon in shot)" if cam.sees_horizon() else ""))
    print(f"scale     {payload['px_per_tile'][0]:.1f} px/tile across, "
          f"{payload['px_per_tile'][1]:.1f} along")
    if "targets" in payload:
        t = payload["targets"]
        print(f"targets   {t['in_frame']} of {t['total']} in frame")
    if plan.assumed:
        print()
        print("this plan leans on constants that are NOT measurements: "
              + ", ".join(plan.assumed))
    return 0


def _pose(p: C.Pose) -> str:
    return (f"focus ({p.fx:.1f}, {p.fz:.1f})  yaw {p.yaw:.1f}  "
            f"pitch {p.pitch:.1f}  range {p.dist:.1f}  eye {p.height:.1f}")


if __name__ == "__main__":
    raise SystemExit(main())
