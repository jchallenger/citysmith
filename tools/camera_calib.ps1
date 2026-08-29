<#
Measure what each camera input actually does, instead of inferring it.

`citysmith/camera.py` models TaleSpire's builder camera. Several of its
constants shipped as inferences off *our own scripts* -- `review.ps1 360` turns
`-DX 320` four times to photograph four faces, so a full turn is 1280 px, so
0.28 deg/px. That is a reading of a script we wrote, not of the game. Measured,
yaw is 0.188 deg/px: the inference was fifty per cent out.

The method: paste a size-coded target of known geometry on a fresh board, then
photograph it either side of each input. `tools/camera_solve.py` recovers the
full camera pose from each shot by fitting a homography, and the *difference*
between consecutive poses is the measurement. Nothing here reads a pixel or
asserts a number; it drives and it captures, and the solver is a separate
program that can be re-run on the same shots without touching the game.

  .\tools\camera_calib.ps1                    # fresh board, paste, frame, sweep
  .\tools\camera_calib.ps1 -KeepBoard         # board and target already there
  .\tools\camera_calib.ps1 -KeepBoard -Family yaw

Shots land in out\camcal\<step>.png and the manifest in out\camcal\steps.json.
Then:

  python tools/camera_solve.py --write

Three rules this grew, each from a sweep that produced confident rubbish:

* **Every step records the amount it applied.** The solver reads it from the
  manifest rather than keeping its own copy; when these steps were resized
  from the inferred sensitivities to the measured ones, a solver with its own
  numbers would have reported everything off by the ratio.
* **A constant comes from CONSECUTIVE steps only.** The first version chained
  each family back to `base`, so the height figures were really differences
  across the two families in between, and came out about four times too big.
* **A stop is only a stop if you drive into it twice and the pose does not
  move.** Otherwise it is just a large move, and recording it as a limit puts
  an invented ceiling into every framing the model ever solves.
#>
param(
  [ValidateSet('all','yaw','pitch','height','fly','clamps')]
  [string]$Family = 'all',
  [switch]$KeepBoard,
  [switch]$NoFrame,
  [string]$Slab = "out/camtarget.slab.txt",
  [int]$SettleMs = 900
)

$ErrorActionPreference = 'Stop'
$ts    = Join-Path $PSScriptRoot "ts.ps1"
$grab  = Join-Path $PSScriptRoot "grab.ps1"
$outd  = Join-Path $PSScriptRoot "..\out\camcal"
New-Item -ItemType Directory -Force $outd | Out-Null

function TS { & $ts @args | Out-Null }
function Read-Shot([string]$name) {
  & $grab -Name "..\camcal\$name" -Format png | Out-Null
  & python tools/camera_read.py "out/camcal/$name.png" --json | ConvertFrom-Json
}

$rect = & $ts client
$CX = [int]($rect.X + $rect.W * 0.5)
$CY = [int]($rect.Y + $rect.H * 0.5)
# Grab a drag off-centre and above the hint bar: a middle-drag that starts on
# the HUD is a click on the HUD, and the bottom strip is all HUD.
$GX = [int]($rect.X + $rect.W * 0.45)
$GY = [int]($rect.Y + $rect.H * 0.45)

$steps = New-Object System.Collections.ArrayList

# **Re-frame between families, do not trust a family to put the camera back.**
# Each family ends with a move meant to undo the ones before it, and each of
# those is quantised -- so the error accumulates, and by the last family of one
# sweep the base pose had drifted 20 degrees in yaw and the target was out of
# frame for nine shots in a row. This drives into two stops instead, which is
# the only absolute reference this camera has: the pitch stop at about 79
# degrees and the Ctrl+scroll top stop. `F2` was tried for this and does
# nothing.
#
# It cannot restore the FOCUS -- there is no control that goes to a place -- so
# the family that moves the focus (fly) is run last, where its drift can hurt
# nothing after it.
function Reframe {
  TS orbit -X $GX -Y $GY -DX 0 -DY 400              # into the pitch top stop
  TS nudge -Mode vertical -X $CX -Y $CY -Ticks -40  # into the distance top stop
  Start-Sleep -Milliseconds 700
  TS orbit -X $GX -Y $GY -DX 0 -DY -110             # back to about 60 degrees
  Start-Sleep -Milliseconds 500
}

# `amount` is the input this step applied and `kind` says which constant it
# feeds. Together they let the solver work from manifest *order* -- the shot
# before is the shot before -- instead of from a chain of names it has to be
# kept in step with.
function Step([string]$name, [scriptblock]$action, [string]$note,
              [double]$amount = 0, [string]$kind = "") {
  if ($action) { & $action }
  Start-Sleep -Milliseconds $SettleMs
  & $grab -Name "..\camcal\$name" -Format png | Out-Null
  [void]$steps.Add([ordered]@{ name = $name; note = $note;
                               amount = $amount; kind = $kind })
  "  $name  $note"
}

# --------------------------------------------------------------------------
# a fresh board with the target on it
# --------------------------------------------------------------------------

if (-not $KeepBoard) {
  "== fresh board =="
  TS newboard
  Start-Sleep -Seconds 4
  TS rename -Text "PROBE camera model"
  Start-Sleep -Seconds 2

  # `G` and `N` survive a new board and each imitates a defect: a build plane
  # makes a paste land a course high, a cut box reads as a hole in the ground.
  #
  # Recognise "off" explicitly and refuse everything else, including output
  # this does not recognise at all. Matching "not ON" is the test that reads
  # `build plane UNKNOWN` as safe, which is the one reading the probe's own
  # message tells you not to act on.
  #
  # **A fresh board does not necessarily open in build mode** -- measured here:
  # the hint bar came up with the camera bindings and `planestate` correctly
  # reported that the toolbar it reads is not on screen. So `B` is pressed in
  # *response to a reading*, and only while the reading says the toolbar is
  # absent. Blind toggling is how this project once turned the build plane on
  # with the keystroke meant to turn it off.
  $plane = ""
  for ($try = 0; $try -lt 3; $try++) {
    $plane = (& $ts planestate) -join " "
    if ($plane -match '^build plane' -and $plane -notmatch 'UNKNOWN') { break }
    "  not in build mode (try $($try + 1)); pressing B"
    TS key -Keys b -Hold 0.15
    Start-Sleep -Milliseconds 900
  }
  "  plane state: $plane"
  if ($plane -notmatch '^build plane off') {
    throw ("the build plane does not read as off (got '$plane'). " +
           "An unreadable probe is not a pass -- fix it before measuring.")
  }

  # **Go to the widest view before pasting.** Camera height persists across
  # boards -- measured: a brand new board opened with the target rendering two
  # pixels wide -- and `F2` does not reset it. Driving into the pitch top stop
  # and then the Ctrl+scroll top stop is a *repeatable* starting pose, which is
  # what `F2` was expected to give and does not.
  TS orbit -X $GX -Y $GY -DX 0 -DY 400
  TS nudge -Mode vertical -X $CX -Y $CY -Ticks -40
  Start-Sleep -Seconds 2

  # Is the board actually empty? `newboard` is a click on a small target in the
  # top bar, and when it misses, the "new" board is the one already open --
  # which is how a calibration ends up fitting a homography across two pastes
  # that landed tens of tiles apart. Checked by looking, now that the view is
  # wide enough for a stray paste to be visible rather than sub-pixel.
  $pre = Read-Shot "_precheck"
  if ($pre.blobs -gt 0) {
    throw ("the new board is not empty -- $($pre.blobs) turf blobs already " +
           "on it, areas $($pre.areas -join ','). newboard did not take, or " +
           "this board has been used. Make one by hand and re-run -KeepBoard.")
  }
  "  board is empty"

  "== paste the target =="
  # The anchor is the cursor's ray hit, and at a near-vertical pitch nothing
  # under the cursor can slide it. It also lands the target centred on the
  # cursor, which is what the framing below starts from.
  TS paste -Slab $Slab -X $CX -Y $CY
  Start-Sleep -Seconds 3
  TS clear -X $CX -Y $CY
  Start-Sleep -Seconds 1
}

# --------------------------------------------------------------------------
# frame it: an oblique, off-axis, with room to move in both directions
# --------------------------------------------------------------------------

if (-not $NoFrame) {
  "== frame the target =="
  # Three things the base pose has to be, each learned from a sweep that was
  # not:
  #
  #  * **Oblique**, because a plan view of a plane cannot separate the focal
  #    length from the distance at all -- they are one parameter there.
  #  * **Off-axis in yaw**, because one of the homography's two readings of the
  #    focal length degenerates when the target's axes line up with the screen,
  #    and a fit that cannot cross-check itself is not allowed to call itself
  #    trustworthy.
  #  * **At the Ctrl+scroll top stop**, which is the widest view there is. A
  #    16-tile target only just fits the frame from there, so the height family
  #    below measures *inward* -- the one direction with room. Backing off the
  #    stop first was tried and overflowed the frame within three ticks.
  Reframe
  TS orbit -X $GX -Y $GY -DX 110 -DY 0              # about 20 degrees off-axis
  Start-Sleep -Seconds 1
}

"== base =="
Step "base" $null "reference pose" 0 "base"
# Everything downstream is a difference from this pose. If it cannot be
# measured, nothing after it can be either, and running the sweep anyway would
# produce a directory of shots and a report of blanks.
$b = & python tools/camera_read.py "out/camcal/base.png" --json | ConvertFrom-Json
if (-not $b.ok) {
  throw ("the base shot cannot be measured: $($b.problems -join '; '). " +
         "Frame the target -- all five marks inside the search area -- and " +
         "re-run with -KeepBoard -NoFrame.")
}
$bp = $b.pose.pose
"  base: yaw $([math]::Round($bp.yaw,2))  pitch $([math]::Round($bp.pitch,2))" +
"  eye_y $([math]::Round($bp.eye_y,2))  dist $([math]::Round($bp.dist,2))"

# --------------------------------------------------------------------------
# the families
# --------------------------------------------------------------------------
#
# Every step is sized from the measured sensitivities -- yaw 0.188 deg/px,
# pitch 0.145 deg/px -- to move the pose enough to measure and little enough to
# keep the target inside the frame. A shot the reader refuses measures nothing.

if ($Family -in @('all','yaw')) {
  "== yaw: middle-drag DX =="
  Step "yaw_a" { TS orbit -X $GX -Y $GY -DX 100 -DY 0 }  "orbit -DX +100" 100 "yaw"
  Step "yaw_b" { TS orbit -X $GX -Y $GY -DX 100 -DY 0 }  "again, for linearity" 100 "yaw"
  Step "yaw_c" { TS orbit -X $GX -Y $GY -DX -200 -DY 0 } "back to base" -200 "yaw"
}

if ($Family -in @('all','pitch','height')) {
  Reframe
  "== pitch: middle-drag DY =="
  # **Downward, away from the top stop.** A leg that ends against a stop
  # measures the stop and not the sensitivity: an earlier sweep's three legs
  # read 0.166, 0.106 and 0.164 deg/px, and the odd one out was the one that
  # ran into it.
  Step "pit_a" { TS orbit -X $GX -Y $GY -DX 0 -DY -60 } "orbit -DY -60" -60 "pitch"
  Step "pit_b" { TS orbit -X $GX -Y $GY -DX 0 -DY -60 } "again, for linearity" -60 "pitch"

  "== distance: Ctrl+scroll, empty hand, at the low pitch =="
  # Ctrl is vertical only with an EMPTY hand; the hand was cleared after the
  # paste. Positive ticks zoom IN -- measured, and the opposite of the first
  # guess, which sent a levelling loop climbing away from its own target.
  #
  # **Measured at about 40 degrees, not at the base pose.** The base sits at
  # the Ctrl+scroll top stop, so the only travel available there is inward, and
  # inward at 60 degrees pushed the nearest mark straight off the bottom of the
  # frame. At the shallower pitch the target's footprint is compressed.
  #
  # In by four first, so the legs that measure the sensitivity are clear of the
  # stop, then out one tick at a time -- which measures it again on the way
  # back and finds the stop at the end of the same walk.
  Step "dst_in"  { TS nudge -Mode vertical -X $CX -Y $CY -Ticks 4 } "Ctrl+scroll +4 (in)" 0 "jump"
  Step "dst_a"   { TS nudge -Mode vertical -X $CX -Y $CY -Ticks -1 } "Ctrl+scroll -1 (out)" -1 "dist"
  Step "dst_b"   { TS nudge -Mode vertical -X $CX -Y $CY -Ticks -1 } "again, for linearity" -1 "dist"
  foreach ($i in 1..5) {
    Step "diststop_$i" { TS nudge -Mode vertical -X $CX -Y $CY -Ticks -1 } "Ctrl+scroll -1 (out)" -1 "diststop"
  }
}

if ($Family -in @('all','clamps')) {
  Reframe
  "== the pitch stop: walk into it until it stops responding =="
  # **Walked into, not slammed into.** Driving straight at a stop needs the
  # shot AT the stop to be readable, and at these extremes it usually is not:
  # a sweep that did so came back with 94 px residuals -- the target half out
  # of frame, the size ranking matching a mark to a fragment of another -- and
  # reported a pitch stop of 59.6 degrees after driving *up* from 60.3.
  #
  # Walking in means the answer is the last two readable shots that agree, and
  # every shot past the stop can fail harmlessly.
  foreach ($i in 1..6) {
    Step "pitchstop_$i" { TS orbit -X $GX -Y $GY -DX 0 -DY 40 } "pitch +40 px" 40 "pitchstop"
  }
}

if ($Family -in @('all','fly')) {
  Reframe
  "== fly: WASD, which ramps =="
  # **Last, because it is the one family that moves the focus** and nothing can
  # put the focus back -- there is no control that goes to a place. Run earlier,
  # its drift carried the target out of frame for every family after it.
  #
  # Very short holds: WASD eases up to a maximum, and even 0.2 s carries the
  # camera far enough that a 16-tile target leaves the frame entirely. These
  # are down in the part of the ramp curve actually being measured.
  Step "fly_a" { TS fly -Keys w -Hold 0.08 } "hold w 0.08 s" 0.08 "fly"
  Step "fly_b" { TS fly -Keys s -Hold 0.08 } "hold s 0.08 s" 0.08 "fly"
  Step "fly_c" { TS fly -Keys w -Hold 0.14 } "hold w 0.14 s" 0.14 "fly"
  Step "fly_d" { TS fly -Keys s -Hold 0.14 } "hold s 0.14 s" 0.14 "fly"
}

$manifest = [ordered]@{
  client = @{ x = $rect.X; y = $rect.Y; w = $rect.W; h = $rect.H }
  grab   = @{ x = $GX; y = $GY }
  family = $Family
  taken  = (Get-Date).ToString("s")
  steps  = $steps
}
$path = Join-Path $outd "steps.json"
$manifest | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 $path
""
"$($steps.Count) shots -> out\camcal\  and the manifest -> $path"
"next: python tools/camera_solve.py"
