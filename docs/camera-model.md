# The camera, measured

Every camera command in `tools/ts.ps1` is *relative*: orbit by so many pixels,
scroll by so many ticks, hold `w` for so long. CLAUDE.md's answer to "where am
I" has been `camerastate`, which photographs the compass rose and leaves a
human to read a bearing off it. That tells you where you are. It cannot tell
you where to go, and it cannot tell you what a frame will contain before you
take it.

This is the other half: a pinhole camera over the tile grid
(`citysmith/camera.py`), a fitter that recovers a real pose from a screenshot
(`citysmith/camerafit.py`), and the constants that connect them
(`config/camera.json`) — **measured off the running game, not guessed**.

Three questions become arithmetic:

* **What is in this frame?** `Camera.footprint()` is the ground quad the
  frustum covers, so "are the field walls in this shot" is a polygon test
  rather than a screenshot read afterwards. That failure is on the record:
  fences were built, shipped and reviewed over two sessions while absent from
  every frame looked at, because both crops were dense town centre.
* **Where must the camera be to frame this?** `frame_rect` solves for a pose,
  and says plainly when the target does not fit.
* **How do I get there from here?** `plan` emits the `ts.ps1` calls.

## 1. What the camera is

**An orbit camera.** A focus point on the board, a slant range back from it,
and two angles. That is not a modelling convenience, it is what the game does:
across a pitch change and a yaw change the fitted focus stayed at
(8.01, 8.01) — the exact centre of the target — while the eye moved from 50.7
tiles of height to 43.4. `review.ps1 360` has always depended on this without
saying so: it photographs four faces of one probe with four equal `orbit -DX`
calls, which only frames the subject each time if the drag turns the camera
about the thing it is looking at.

Conventions, chosen to match the game and the rest of citysmith:

| | |
|---|---|
| units | tiles; 1 world unit = 1 tile, as in `slab.py` |
| axes | `y` up, `x` east, `z` north — the slab format's own |
| `yaw` | compass bearing, 0 = north, 90 = east, matching the compass rose |
| `pitch` | degrees **below** the horizon, 0 = level |
| datum | `y = 0` is the board surface, which is where `place_tile` puts a tile's min corner |

## 2. The constants, and what they replaced

| constant | measured | was |
|---|---|---|
| `fov_v_deg` | **30.03** | 60, "Unity's default" |
| `yaw_deg_per_px` | **0.1876** | 0.281, inferred off `review.ps1` |
| `pitch_deg_per_px` | **0.1689** | 0.281, same inference |
| `pitch_max_deg` | **78.07** | 90, "review.ps1 pastes at a vertical pitch" |
| `dist_scale_per_tick` | **0.8721** (a ratio) | 1.0 tiles, a step |
| `dist_max` | **49.75** | 60 tiles of eye height |
| `fly_speed_max` | **21.4 tiles/s** over 0.08–0.14 s holds | 70 tiles/s |

Three of the old values were **inferences off our own scripts**, and this is
the trap they represent. `review.ps1 360` turns `-DX 320` four times to
photograph four faces, so a full circle is 1280 px, so 0.28 deg/px. The
arithmetic is right and the conclusion is wrong: 320 px is not a quarter turn,
it is about 60 degrees, and the recipe works because four 60-degree steps still
show four different sides. A reading of a script we wrote is not a measurement
of the game.

Still assumed, and labelled so everywhere they surface: `pitch_min_deg`,
`dist_min` and `pan_gain`. The first two need the camera driven to the far end
of its range, where the target is a sliver and nothing can be fitted; `pan_gain`
is CLAUDE.md's own 53% figure, carried over rather than re-measured.

### Two findings that correct CLAUDE.md

**The camera does not reach vertical.** It stops at **78.07°** — walked into,
three consecutive shots agreeing to 0.01°, and confirmed at 78.25/78.26 in a
sweep taken hours earlier. CLAUDE.md's paste rule says every paste is made
"looking straight down", where `cot(pitch)` is zero and nothing under the
cursor can slide the anchor. At 78° `cot` is 0.21, so a 1.5-tile obstruction
still slides the anchor **0.32 tiles**. That is a third of a cell, well under
the one-to-two-cell error the rule was written to prevent, so the procedure
stands — but the claim that the slide is *zero* does not.

**Ctrl+scroll scales the range; it does not step it.** Read as a step, the same
control measures −4.38, −4.59 and −5.65 tiles per tick at different ranges,
29% apart, which reads as a noisy constant. Read as a ratio the identical three
legs give 0.8666, 0.8773 and 0.8696 and agree to 1.2%. The additive version
survived a whole calibration and was caught only by driving a planned move and
measuring where the camera actually landed.

## 3. How they were measured

A **size-coded ground target** — five squares of grass, 6×6 down to 2×2, at
coordinates we chose — pasted on a fresh board and photographed either side of
each input. `camera_solve.py` recovers the full pose from each shot by fitting
a plane-to-image homography, and the *difference* between consecutive poses is
the measurement.

The homography over-determines the focal length: a planar target gives two
independent equations for it, one from the orthogonality of the two rotation
columns and one from their equal length. They only agree if the camera really
is a pinhole with square pixels and the marks really were found where we think.
`Fit.focal_disagreement` is the gap, and it is reported rather than averaged
away.

    python tools/camera_probe.py > out/camtarget.slab.txt   # build the target
    .\tools\camera_calib.ps1                                # drive and capture
    python tools/camera_solve.py --write                    # fit and record

### What the target had to learn

The target was **three times too large** to begin with, and each shrink came
from a measurement rather than a guess:

* CLAUDE.md records that a frame holds about 40 tiles at the top of
  Ctrl+scroll. That is an **oblique** figure — the far half of a tilted frame
  covers a lot of ground. Near plan, which is the one view a paste cannot
  slide, the frame holds about **24 tiles**.
* Climbing out of trouble does not work at an oblique. Raising the eye moves
  the ground point under the frame centre away by `height × cot(pitch)`, so the
  target slides off rather than shrinking; four ticks up lost it completely.
* **What has to fit is the diagonal.** A 16-tile target is 22.6 tiles corner to
  corner, and as the camera turns that diagonal swings onto the frame's short
  axis. It fitted at one yaw and not at another, which read as a flaky solver:
  nine shots in a row failed with the target simply off the bottom of the frame.

12 tiles is 17 on the diagonal and leaves real margin at any yaw.

### Four failures worth keeping

* **The classifier was guessed.** The first mark detector asked whether green
  beat both other channels. Sampled off a real capture, `Grass 1x1` under
  TaleSpire's light is *yellow*-green — (159, 156, 95) has green **below** red
  — so it found seven blobs of noise and none of the five marks. The separation
  that works is chroma on the yellow-blue axis: about 70 on grass, about 0 on
  the bare board. Guessing a threshold is the same mistake as guessing a
  constant.
* **The HUD contributes marks.** On a board with nothing on it, two
  turf-coloured blobs survive: the green marker on the elevation ruler and the
  word YOU in the Role card. `is_grass` cannot reject them — they really are
  that colour — so they are excluded by *place*, using the one reliable thing
  about the interface: every piece of it is anchored to an edge.
* **A mark's centroid is not its centre.** Perspective magnifies the near half
  of a square more than the far half, so a blob's centre of area sits nearer
  the camera than its middle does. On a 6×6 mark at 40 px/tile that was
  **3.1 px of residual**, above the bar at which a fit may call itself
  trustworthy — so it read as "the pinhole model does not hold" when it was
  "the thing being measured is not the thing being predicted". Correcting it
  took the residual to 0.62 px.
* **Fixing the focal length made every fit worse.** The obvious improvement —
  take the median focal length from the shots that can cross-check it, hand it
  back to every shot, solve pose alone — was built and then measured away:
  `base` went from 0.53 px to 2.04, the pitch-stop shots from 9.3 to 16.3. A
  per-shot focal length is a nuisance parameter that absorbs that shot's own
  systematic error, and forcing the physically right value exposes all of it.
  The median is the right number to *report* and the wrong number to force.

## 4. Does it work?

Three planned moves, driven and then measured by reading the pose back off the
result:

| move | yaw error | pitch error | range error |
|---|---|---|---|
| pitch 60→45, range 49.6→35 | 0.07° | 0.58° | 0.33 tiles |
| yaw 100→230, pitch 44→65 | 0.23° | 0.64° | 0.10 tiles |
| yaw 230→15, pitch 66→35 | 0.85° | 1.15° | 0.11 tiles |

The worst case is about a degree on a 215-degree turn. `tools/camera_aim.ps1`
runs the plan and then reports that error itself, so the claim "the camera is
now at 45 degrees looking east" is a measurement rather than an intention.

## 5. The preview, and what it is not

The camera screen's plan view says *where* the frame falls. The preview says
what the frame will look like: the town projected through the same `Camera`
that plans the move, returned as screen-space quads. **The browser does no 3D**
-- it draws the polygons it is handed, in the order it is handed them. Two
implementations of a frustum is two frustums, and only one of them was measured
against the game.

**The mouse runs the rig.** Left-drag is `orbit`, wheel is Ctrl+scroll, shift-
or right-drag pans, and each goes through the measured control model on the
server. One consequence is worth stating: the preview cannot show you a shot
the camera cannot take. Drag the pitch past 78.07 degrees and it stops, and the
caption says which stop it is against -- because a control at its stop is
otherwise indistinguishable from a dead one, and this project has misread that
in the game itself more than once.

It is a **prediction of the framing**, not a render of the board. Buildings are
boxes at `floors x 2.0` tiles, the shading is a fixed lamp, and the painter's
sort cannot resolve geometry that interleaves. What it is accurate about is
where things fall in the frame.

Two measurements shaped it. Projecting a 991-building town took **93 ms a
frame** -- a slideshow to drag -- because `Pose.eye`, `.forward`, `.right` and
`.up` are properties that each run their own trigonometry and `project` asks
for three of them per corner. Hoisting the basis and rejecting each box on one
projection of its centre took that to **4-7 ms**.

## 6. The tools

| | |
|---|---|
| `tools/camera_probe.py` | builds the calibration target slab |
| `tools/camera_calib.ps1` | drives the game and captures the sweep |
| `tools/camera_solve.py` | fits the shots, writes `config/camera.json` |
| `tools/camera_read.py` | one screenshot in, one camera pose out |
| `tools/camera_aim.py` | works out the moves, and what will be in frame |
| `tools/camera_aim.ps1` | runs them, then verifies against the game |

And in the sidecar UI, a **Camera** screen: give it a board rectangle and a
bearing, and it draws the ground the frustum covers over what you asked for,
says whether it fits, emits the `ts.ps1` calls, and lists every constant with
whether it was measured or assumed.

## 7. Limits

* **Reading a pose needs the target in frame.** The fit is against known marks
  and a town board has none. Calibrate once on a probe board, then drive a real
  board open loop with `camera_aim.py --at`, which trusts the model instead of
  reading it; or paste the target into a corner of the working board.
* **The WASD ramp is measured over 0.08–0.14 s only** and must not be
  extrapolated. CLAUDE.md's note that 3 s crosses a 187-tile map implies about
  62 tiles/second averaged, three times what is measured here — the ramp is
  real and this sweep is at the bottom of it. Longer holds carry the target out
  of frame long before the curve bends.
* **Size-coding is not safe close in.** At a slant range of 35 a near 4×4 mark
  covered 49,540 px against a far 5×5's 56,165. The reader tries the ambiguous
  orderings and keeps whichever reprojects best, and `match_near` uses a
  predicted pose instead when one is available.
* **A cold pose read has a readable envelope, and outside it the answer can be
  wrong rather than absent.** Driving the camera to a slant range of 30 or to a
  yaw around 315 degrees crowds the 12-tile target against the frame edges, and
  the size ranking can then settle on an ordering that reprojects acceptably
  and is not the right one. Two guards catch the gross cases: a mark touching
  the search border is refused outright, and a fit whose slant range or pitch
  is past the *measured* stop is refused as unreachable — that one caught a fit
  claiming 92.9 tiles of range against a stop at 49.75. A moderate error can
  still slip past both. **Read the pose from a framed base**, which is what
  `camera_calib.ps1`'s framing step produces, and treat a read taken from an
  arbitrary pose as a hint.
* **OPEN: one driven move landed 14 degrees off in yaw.** `/api/camera/drive`
  ran its four moves and the camera moved, but it finished at yaw 164.25
  against a planned 150.06, and pitch 41.41 against 44.94. It is not a gain
  error: re-measured on the same board minutes later, a 100 px drag gave
  0.1858 deg/px and a 400 px drag 0.1879, both matching the calibrated
  0.18761. Yaw came up short while pitch overshot, which rules out a single
  scale factor. Two untested candidates: the plan's `fly d 0.37` is well
  outside the 0.08-0.14 s the ramp was measured over, and the final pose sits
  at a 37-tile range, inside the envelope where the reader can mis-rank a near
  mark. Four earlier moves driven through `camera_aim.ps1` landed within 0.9
  degrees, so this is a specific regression and not the general behaviour --
  and until it is understood, treat a driven move as approximate and read the
  camera back.
* **`pan_gain` is inherited, not re-measured**, and neither end of the pitch
  range nor the near end of Ctrl+scroll has been read back.
