"""Turn a calibration sweep into measured camera constants.

`camera_calib.ps1` drives the game and captures; this reads what it captured
and does the arithmetic. They are separate programs on purpose -- the solver
can be re-run, argued with and fixed without touching TaleSpire, and a bad
number can be traced back to the shot it came from.

    python tools/camera_solve.py                 # report, change nothing
    python tools/camera_solve.py --write         # write config/camera.json

Every constant here is a **difference between two recovered poses**, never a
single reading. That matters: a pose fit carries an absolute error from the
mark centroids, and differencing two fits taken seconds apart cancels most of
it, because the same marks are being found the same way in both.

**A step whose shot could not be read is dropped and named, never averaged in.**
The shape of failure this project keeps meeting is a probe that answers without
looking; a calibration that silently skips half its evidence and prints a
confident number is that failure with better presentation.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, ".")

from citysmith import camera as C
from citysmith import camerafit as F

HERE = pathlib.Path(__file__).resolve().parent.parent
CAL_DIR = HERE / "out" / "camcal"
TARGET = HERE / "out" / "camtarget.json"


def load_target():
    doc = json.loads(TARGET.read_text())
    return ([tuple(c) for c in doc["centres_by_descending_size"]],
            [m["size"] for m in doc["marks"]],
            [(m["x"], m["z"], m["size"]) for m in doc["marks"]])


def fit_all(manifest: dict, centres, sizes, squares,
            roi=None, min_area=60, focal_px=None, quiet=False) -> dict:
    """Fit every shot in the sweep. Returns {step name: Fit or None}."""
    out: dict[str, object] = {}
    for step in manifest["steps"]:
        name = step["name"]
        path = CAL_DIR / f"{name}.png"
        if not path.exists():
            print(f"  {name:<12} MISSING {path.name}")
            out[name] = None
            continue
        fit, reading = F.read_shot(path, centres, sizes, roi=roi,
                                   min_area=min_area, squares=squares,
                                   focal_px=focal_px)
        if fit is None:
            if not quiet:
                print(f"  {name:<12} unreadable: {reading.problems[0]}")
            out[name] = None
            continue
        if quiet:
            out[name] = fit
            continue
        p = fit.pose
        flag = "" if fit.trustworthy else "  <-- NOT TRUSTWORTHY"
        xc = "" if fit.focal_cross_checked else " (focal not cross-checked)"
        print(f"  {name:<12} yaw {p.yaw:7.2f}  pitch {p.pitch:6.2f}  "
              f"eye_y {p.height:7.2f}  focus ({p.fx:7.2f},{p.fz:7.2f})  "
              f"fov {fit.fov_v_deg:5.2f}  resid {fit.residual_px:5.2f}"
              f"{xc}{flag}")
        if reading.problems:
            for pb in reading.problems:
                print(f"               ! {pb}")
        out[name] = fit
    return out


def _delta_yaw(a: float, b: float) -> float:
    return (b - a + 180.0) % 360.0 - 180.0


def derive(fits: dict, steps: list, stamp: str) -> tuple[dict, list[str]]:
    """Constant name -> (value, source, residual), plus notes on what failed.

    Works from the manifest's own **order** and each step's `kind`. The shot
    before a step is the shot before it, full stop. An earlier version chained
    every family back to `base` by name, so the height family's first leg was
    really a difference across the yaw and pitch families in between -- 1.46
    tiles of drift read as 0.73 tiles per tick, against a true 0.005.

    Nothing here repeats a number the driver knows: `amount` comes out of the
    manifest too.
    """
    got: dict[str, tuple[float, str, float | None]] = {}
    notes: list[str] = []

    dropped: list[str] = []

    def usable(name):
        """A fit good enough to difference. Untrustworthy ones are DROPPED.

        Not a formality. A sweep whose clamp shots came back at 94 px of
        residual -- the target half out of frame, so the size ranking matched a
        mark to a fragment of another -- was differenced anyway, and reported a
        pitch stop of 59.6 degrees when the camera had been driven *up* from
        60.3. A number that cannot be true, presented as a measurement.
        """
        f = fits.get(name)
        if f is None:
            return None
        if not f.trustworthy:
            dropped.append(name)
            return None
        return f

    def legs(kind: str):
        """(before, after, amount) for consecutive steps of one kind."""
        out = []
        for i, st in enumerate(steps):
            if st.get("kind") != kind or not st.get("amount") or i == 0:
                continue
            a, b = usable(steps[i - 1]["name"]), usable(st["name"])
            if a is not None and b is not None:
                out.append((a.pose, b.pose, float(st["amount"])))
        return out

    def walk_to_stop(kind: str, get, what: str, tol: float):
        """Find where a control stops responding, by walking into it.

        Driving straight into a stop needs the shot AT the stop to be readable,
        and at the extremes of this camera it usually is not. Walking in by
        small steps means the answer is where the readings stop moving, and
        every shot past that can fail harmlessly.

        **The evidence here is agreement across THREE consecutive shots, not
        one shot's residual.** The pitch stop is the case that forced the
        distinction: those shots sit near plan, where the fit's focal length
        degenerates and the residual runs to 9 px -- five times the bar that
        `Fit.trustworthy` sets. But they are not mis-correspondences. A
        mis-correspondence gives 94 px and a pose that jumps about; these gave
        78.07, 78.07, 78.08 and 78.10 in one sweep and 78.25, 78.26 in another
        taken hours apart. So this reads every fit, demands three in a row
        inside `tol`, and puts the residuals in the provenance so the weaker
        evidence is visible in the report rather than hidden by it.
        """
        vals = [(st["name"], fits[st["name"]]) for st in steps
                if st.get("kind") == kind and fits.get(st["name"]) is not None]
        if len(vals) < 3:
            return None, None, f"{what}: fewer than three readable shots"
        for i in range(len(vals) - 2):
            trio = vals[i:i + 3]
            got_v = [get(f.pose) for _, f in trio]
            if max(got_v) - min(got_v) <= tol:
                names = ", ".join(n for n, _ in trio)
                res = ", ".join(f"{f.residual_px:.1f}" for _, f in trio)
                return (sum(got_v) / 3.0, max(got_v) - min(got_v),
                        f"{names} agreed within {max(got_v) - min(got_v):.2f} "
                        f"(residuals {res} px)")
        moved = ", ".join(f"{get(f.pose):.2f}" for _, f in vals)
        return None, None, (f"{what}: the walk never settled ({moved}), so no "
                            "stop was reached -- do not record one")

    def record(key, vals, what):
        if not vals:
            notes.append(f"{key} not measured: {what}")
            return
        mean = sum(vals) / len(vals)
        spread = (max(vals) - min(vals)) if len(vals) > 1 else None
        got[key] = (round(mean, 5),
                    f"measured {stamp}: {len(vals)} consecutive step(s) "
                    "against the target in tools/camera_probe.py",
                    round(spread, 5) if spread is not None else None)

    # -- the lens ----------------------------------------------------------
    fovs = sorted(f.fov_v_deg for f in fits.values()
                  if f is not None and f.trustworthy and f.focal_cross_checked)
    if fovs:
        # Median, not mean. Every shot in the sweep contributes a reading and
        # they are not equally good -- the ones taken near a stop, with the
        # target crowding the frame edge, carry most of the error. A median is
        # not moved by them; a mean is.
        mid = len(fovs) // 2
        mean = fovs[mid] if len(fovs) % 2 else (fovs[mid - 1] + fovs[mid]) / 2
        spread = max(fovs) - min(fovs)
        got["fov_v_deg"] = (
            round(mean, 3),
            f"measured {stamp}: median over {len(fovs)} shots whose two "
            f"independent focal-length readings agreed, spread {spread:.2f} deg",
            round(spread, 3))
    else:
        notes.append("field of view: no shot had both focal readings available "
                     "(they collapse into one when the target's axes line up "
                     "with the screen), so it stays an assumption")

    # -- the three sensitivities -------------------------------------------
    record("yaw_deg_per_px",
           [abs(_delta_yaw(a.yaw, b.yaw)) / abs(amt) for a, b, amt in legs("yaw")],
           "no consecutive pair of yaw shots both fitted")
    record("pitch_deg_per_px",
           [abs(b.pitch - a.pitch) / abs(amt) for a, b, amt in legs("pitch")],
           "no consecutive pair of pitch shots both fitted")
    # Ctrl+scroll is reported in **slant range**, not eye height. Measured, the
    # focus point does not move and the pitch does not change when it is used,
    # so what it changes is the distance the camera orbits at -- and expressing
    # it as eye height would make the constant depend on the pitch it was
    # measured at.
    # As a RATIO per tick. The same legs read as a step disagree by 29% and
    # read as a ratio agree to 1.2%: Ctrl+scroll scales the range.
    record("dist_scale_per_tick",
           [(b.dist / a.dist) ** (1.0 / amt)
            for a, b, amt in legs("dist") if a.dist > 0 and b.dist > 0],
           "no consecutive pair of distance shots both fitted")

    # -- clamps ------------------------------------------------------------
    for kind, key, get, what, tol in (
            ("pitchstop", "pitch_max_deg", lambda p: p.pitch,
             "the top of the pitch range", 0.15),
            ("diststop", "dist_max", lambda p: p.dist,
             "the top of Ctrl+scroll", 0.3)):
        value, spread, why = walk_to_stop(kind, get, what, tol)
        if value is None:
            notes.append(f"{key} not measured: {why}")
        else:
            got[key] = (round(value, 3),
                        f"measured {stamp}: walked into {what} until it "
                        f"stopped responding -- {why}", round(spread, 3))

    # -- the WASD ramp -----------------------------------------------------
    ramp = [(abs(amt), math.dist((a.fx, a.fz), (b.fx, b.fz)))
            for a, b, amt in legs("fly")]
    if len({h for h, _ in ramp}) >= 2:
        v, tau, err = _fit_ramp(ramp)
        detail = ", ".join(f"{h:g}s->{d:.1f}t" for h, d in ramp)
        lo, hi = min(h for h, _ in ramp), max(h for h, _ in ramp)
        # **Say the range it was measured over.** WASD ramps, and these holds
        # are all short: at 0.08-0.14 s the curve is still nearly straight, so
        # the fit reports almost no ramp. That is a true statement about this
        # interval and NOT a licence to extrapolate -- CLAUDE.md's own note
        # that 3 s crosses a 187-tile map implies about 62 tiles/second
        # averaged, three times what is measured here. Longer holds need
        # longer-hold measurements, and the target leaves the frame long
        # before then.
        rng = f"over holds of {lo:g}-{hi:g} s only; do NOT extrapolate"
        got["fly_speed_max"] = (
            round(v, 3),
            f"measured {stamp}: {len(ramp)} WASD holds, {detail} -- {rng}",
            round(err, 3))
        got["fly_ramp_tau"] = (
            round(tau, 4),
            f"measured {stamp}: fitted with fly_speed_max over the same holds "
            f"-- {rng}", round(err, 3))
    else:
        notes.append("the WASD ramp needs two distinct hold times that both "
                     f"fit; got {len(ramp)} usable leg(s)")

    if dropped:
        notes.append("dropped as untrustworthy (residual over "
                     f"{F.Fit.MAX_RESIDUAL_PX:g} px, or the focal cross-check "
                     f"failed): {', '.join(sorted(set(dropped)))}")
    return got, notes


def _fit_ramp(legs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares `v` and `tau` for d(t) = v*(t - tau*(1-exp(-t/tau))).

    Grid search on `tau`, closed form on `v`. Two parameters and a handful of
    points -- anything more sophisticated would be pretending to a precision
    the holds do not have.
    """
    best = (float("inf"), 1.0, 0.5)
    tau = 0.02
    while tau < 6.0:
        shape = [t - tau * (1.0 - math.exp(-t / tau)) for t, _ in legs]
        denom = sum(s * s for s in shape)
        if denom > 1e-12:
            v = sum(s * d for s, (_, d) in zip(shape, legs)) / denom
            err = math.sqrt(sum((v * s - d) ** 2
                                for s, (_, d) in zip(shape, legs)) / len(legs))
            if err < best[0]:
                best = (err, v, tau)
        tau *= 1.05
    return best[1], best[2], best[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="write config/camera.json")
    ap.add_argument("--config", default=None)
    ap.add_argument("--min-area", type=int, default=60)
    ap.add_argument("--roi", default=None,
                    help="x0,y0,x1,y1, or 'full'. Default: the board area, "
                         "with the HUD excluded by camerafit.hud_roi -- the "
                         "interface contributes turf-coloured blobs of its own")
    args = ap.parse_args()

    manifest = json.loads((CAL_DIR / "steps.json").read_text(encoding="utf-8-sig"))
    centres, sizes, squares = load_target()
    if args.roi == "full":
        roi = None
    elif args.roi:
        roi = tuple(int(v) for v in args.roi.split(","))
    else:
        roi = F.hud_roi(manifest["client"]["w"], manifest["client"]["h"])

    print(f"target: {len(sizes)} marks, sizes {sizes}")
    print(f"shots:  {CAL_DIR}")

    # **A two-pass version of this was built and then measured away.** The
    # idea was to take the median focal length from the shots that could
    # cross-check it, hand it back to every shot and solve pose alone -- which
    # should have rescued the near-plan shots, where both focal readings
    # degenerate.
    #
    # It made every shot worse, not better: `base` went from 0.53 px of
    # residual to 2.04, `dst_a` from 2.21 to over 4 (dropped entirely), and the
    # pitch-stop shots from 9.3 to 16.3. The reason is that a per-shot focal
    # length is a nuisance parameter: it quietly absorbs that shot's own
    # systematic error -- centroid bias, blob threshold, whatever lens
    # distortion there is -- and forcing the physically right value exposes all
    # of it. The median across shots is still the right number to *report*; it
    # is the wrong number to force on each fit.
    #
    # `solve_pose(..., focal_px=)` is kept, because it is the right tool for
    # reading a pose off a board where the target cannot be seen at an oblique.
    print()
    fits = fit_all(manifest, centres, sizes, squares, roi=roi,
                   min_area=args.min_area)
    print()

    stamp = manifest.get("taken", "")[:10]
    got, notes = derive(fits, manifest["steps"], stamp)

    rig = C.load_rig(args.config)
    for key, (value, source, residual) in got.items():
        rig = rig.with_measured(key, value, source, residual)

    print("constants")
    for line in rig.provenance():
        print(line)
    if notes:
        print()
        print("not measured by this sweep")
        for n in notes:
            print(f"  - {n}")

    if args.write:
        path = C.save_rig(rig, args.config)
        print()
        print(f"wrote {path}")
    else:
        print()
        print("(report only; pass --write to save)")


if __name__ == "__main__":
    main()
