<#
One probe panel, on its own board, framed identically to every other panel.

`review.ps1 360` walks around ONE anomaly. This is the other shape of review:
several treatments of the same site that have to be compared against each
other, where what matters is that the camera is in the same place for all of
them.

**Why one board per panel rather than a row on one board.** Ctrl+scroll camera
height is capped -- measured, two 1920x1080 frames 45 and 200 ticks apart
differ by 0.59 on the mean-abs-diff metric against a 2.0 noise floor -- and at
that cap an oblique covers about 40 tiles. Four 34-tile panels in a row are
151 tiles, so the row can only be read by flying along it, and WASD ramps:
1.6 s of `a` moved most of a panel and 2.2 s went off the end of the map. A
fresh board resets the camera, so the same commands in the same order put
every panel in the same pixels.

Every paste is made looking straight down, for the reason `review.ps1` gives:
the anchor is the cursor's ray hit, and at a vertical pitch nothing under the
cursor can slide it.

    .\tools\panel_review.ps1 -Slab out\yardprobe\structure-1-shipped.slab.txt `
                             -Name ystr-1-shipped -Board "PROBE yard shipped"
#>
param(
  [Parameter(Mandatory=$true)][string]$Slab,
  [Parameter(Mandatory=$true)][string]$Name,
  [Parameter(Mandatory=$true)][string]$Board,
  [int]$X = 760, [int]$Y = 540,
  [int]$Height = 200,      # ticks of Ctrl+scroll; large enough to hit the cap
  [int]$Oblique = 170      # pitch back up from vertical, in drag pixels
)

$ts = Join-Path $PSScriptRoot "ts.ps1"
function TS { & $ts @args | Out-Null }
function Shot([string]$view) {
  & (Join-Path $PSScriptRoot "grab.ps1") -Name "$Name-$view" | Out-Null
}

# --- will this even fit in one shot? -----------------------------------------
#
# **Asked BEFORE the paste, because the answer is often no and finding that
# out by flying costs more than the review.** Measured in the session that
# prompted this: the 53x45 all-wall-kits board needs 95 tiles of slant range
# and Ctrl+scroll stops at 49.75, so it cannot be photographed whole at any
# pitch -- and that was discovered by building it, pasting it, and hunting
# around it for four exchanges.
#
# The pose this asks about is the one the shot is actually taken at. The two
# commands below drive into the measured stops (200 ticks and 250 px of pitch
# both exceed the range), so `dist_max` and `pitch_max` are known without
# reading anything back, and -Oblique pitches back from there. That is why
# open-loop framing works here with no calibration target on the board.
#
# It REPORTS and does not drive. `camera-drive-open-loop` is open against a
# driven move landing 14 degrees off in yaw, so until that is understood the
# model is trusted to say what a frame will hold and not to steer.
$rig    = Get-Content (Join-Path $PSScriptRoot "..\config\camera.json") -Raw | ConvertFrom-Json
$distMx = $rig.constants.dist_max.value
$pitchMx = $rig.constants.pitch_max_deg.value
$pitchPx = $rig.constants.pitch_deg_per_px.value
$shotPitch = $pitchMx - ($Oblique * $pitchPx)

$aim = & python (Join-Path $PSScriptRoot "camera_aim.py") `
        --at "0,0,$distMx,0,$shotPitch" --slab $Slab --pitch $shotPitch --json 2>$null
if ($LASTEXITCODE -eq 0 -and $aim) {
  $f = ($aim | ConvertFrom-Json).framing
  if ($f.fits) {
    "FRAME OK at pitch $([math]::Round($shotPitch,1)) deg -- the whole slab is in shot"
  } else {
    # The note carries "N of 4 corners in frame", which is measured against the
    # frustum. `framing.covered` is NOT used here on purpose: it is an overlap
    # against `visible_bounds`, the axis-aligned box round the trapezoid, so on
    # a wide shallow slab it reads 100% while no corner is actually in shot --
    # measured on out\wallkits-used.slab.txt, 53x5 tiles, covered 1.00 and 0 of
    # 4 corners. Printing that number beside "TOO BIG" would be a metric
    # disagreeing with its own headline.
    "FRAME TOO BIG at pitch $([math]::Round($shotPitch,1)) deg"
    "   $($f.note)"
    "   Shrink the probe, or expect to fly. Not an error; the shots still land."
  }
} else {
  "frame check unavailable (camera_aim.py did not run) -- shooting blind"
}

TS newboard
Start-Sleep -Seconds 4
TS rename -Text $Board
Start-Sleep -Seconds 2

# The build plane survives a new board and makes a paste land a course high
# with nothing wrong in the file. Its icon only exists in build mode, and an
# unreadable probe is not a pass: require an explicit "off".
TS key -Keys b -Hold 0.1
Start-Sleep -Milliseconds 700
$plane = & $ts planestate
if ($plane -notmatch '^build plane off') { throw "plane not readable as off: $plane" }
TS key -Keys b -Hold 0.1
Start-Sleep -Milliseconds 500

# Height first, then pitch, then paste. Ctrl+scroll moves the camera without
# moving the focal target, so a height change after framing throws the whole
# frame out of focus -- set it once and never touch it again.
TS nudge -X $X -Y $Y -Ticks (-$Height) -Mode vertical
Start-Sleep -Milliseconds 900

TS orbit -X $X -Y $Y -DX 0 -DY 250
Start-Sleep -Milliseconds 500
TS paste -Slab $Slab -X $X -Y $Y
Start-Sleep -Seconds 3
TS clear -X $X -Y $Y
Start-Sleep -Seconds 1

# Pasting puts the board in build mode, which lands the build toolbar and a
# "BUILDING" banner across the top of every frame. Leave it before shooting.
TS key -Keys b -Hold 0.1
Start-Sleep -Milliseconds 600

Shot "plan"
TS orbit -X 960 -Y 500 -DX 0 -DY (-$Oblique)
Start-Sleep -Milliseconds 700
Shot "obl"
TS orbit -X 960 -Y 500 -DX 0 -DY (-90)
Start-Sleep -Milliseconds 700
Shot "low"

# Index it as disposable, now, because a probe board is made to be looked at
# once and then deleted -- and the campaign list will not remember that. A
# nineteen-board prune done off screenshots and memory is what this line is
# for; `citysmith boards prune` can now name them itself.
& python -m citysmith boards note --board $Board --holds probe `
    --source $Slab --disposable `
    --note "probe panel from tools/panel_review.ps1; shots in out\flyby\$Name-*" |
  Write-Output

"$Name -> out\flyby\$Name-{plan,obl,low}.jpg   board '$Board'"
