<#
Point the camera at something, by prediction rather than by flying.

`camera_aim.py` works out the moves; this runs them, then photographs the
result and reads the pose back, so the claim "the camera is now at 45 degrees
looking north-east" is a measurement and not an intention.

  .\tools\camera_aim.ps1 -Yaw 100 -Pitch 45 -Dist 35
  .\tools\camera_aim.ps1 -Frame "40,120,90,170"
  .\tools\camera_aim.ps1 -Yaw 100 -Shot northfields      # keep the capture

**It needs the calibration target in frame to read a pose from.** That is the
honest limit of this tool: the fit is against known marks, and a town board has
none. Two ways round it, in order of preference --

  * calibrate once on a probe board, then drive a real board *open loop* with
    `camera_aim.py --at`, which needs no target because it trusts the model;
  * or paste the target into a corner of the working board.

Verification runs the plan and then reports the error between where the model
said the camera would be and where it is. That number is the only evidence any
of this works, so it is on by default; -NoVerify turns it off.

**Start from a framed base.** The reader has an envelope: at a short slant
range, or at a yaw that swings the square target's diagonal onto the frame's
short axis, the marks crowd the edges and the size ranking can settle on an
ordering that reprojects acceptably and is not the right one. Measured from a
properly framed base the error is a third of a degree; measured from a pose
left behind by an earlier run it has come back five degrees out. Drive into the
pitch and distance stops and back off, as `camera_calib.ps1` does, before
trusting a reading.
#>
param(
  [double]$Yaw = [double]::NaN,
  [double]$Pitch = [double]::NaN,
  [double]$Dist = [double]::NaN,
  [string]$Frame,
  [string]$Shot,
  [switch]$NoVerify,
  [int]$SettleMs = 900
)

$ErrorActionPreference = 'Stop'
$ts   = Join-Path $PSScriptRoot "ts.ps1"
$grab = Join-Path $PSScriptRoot "grab.ps1"
function TS { & $ts @args | Out-Null }

# Where is it now? Read it, do not remember it -- every camera command in this
# toolkit is relative, which is how a session ends up over the void wondering
# where the map went.
& $grab -Name "..\camcal\_aim_before" -Format png | Out-Null
$before = & python tools/camera_read.py "out/camcal/_aim_before.png" --json |
          ConvertFrom-Json
if (-not $before.ok) {
  throw ("cannot read the camera: $($before.problems -join '; '). This needs " +
         "the calibration target in frame; use camera_aim.py --at to drive " +
         "open loop instead.")
}

$argv = @("tools/camera_aim.py", "--from", "out/camcal/_aim_before.png", "--json")
if (-not [double]::IsNaN($Yaw))   { $argv += @("--yaw", "$Yaw") }
if (-not [double]::IsNaN($Pitch)) { $argv += @("--pitch", "$Pitch") }
if (-not [double]::IsNaN($Dist))  { $argv += @("--dist", "$Dist") }
if ($Frame) { $argv += @("--frame", $Frame) }
$plan = & python @argv | ConvertFrom-Json

"from   yaw $([math]::Round($plan.start.yaw,2))  pitch $([math]::Round($plan.start.pitch,2))  range $([math]::Round($plan.start.dist,2))"
"to     yaw $([math]::Round($plan.target.yaw,2))  pitch $([math]::Round($plan.target.pitch,2))  range $([math]::Round($plan.target.dist,2))"
if ($plan.framing -and -not $plan.framing.fits) {
  "FRAME  does not fit: $($plan.framing.note)"
}
foreach ($m in $plan.plan.moves) {
  "  $($m.command)"
  # A hashtable splat, not an array one: PowerShell's array splat reads any
  # element beginning with `-` as a parameter name, so a plan carrying
  # `-DY -91` bound `-91` as a switch and shifted every argument after it.
  # Nothing here is ever assembled into a command string and handed to a shell.
  $h = @{}
  $m.params.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }
  & $ts $m.cmd @h | Out-Null
  Start-Sleep -Milliseconds 400
}
Start-Sleep -Milliseconds $SettleMs

if ($Shot) { & $grab -Name $Shot | Out-Null; "shot   out\flyby\$Shot.jpg" }

if (-not $NoVerify) {
  # **Read the result with the prediction as a hint.** Close in, the target's
  # marks can be reordered by perspective -- a near 4x4 covered more pixels
  # than a far 5x5 at a slant range of 35 -- and matching by size then hands
  # each mark the other's coordinates. Matching by predicted position cannot
  # do that. Using the prediction to read the result is not circular: a wrong
  # prediction moves the blobs past the matcher's distance limit and it
  # refuses rather than confirming itself.
  $q = $plan.plan.moves[-1].after
  if (-not $q) { $q = $plan.start }
  $ex = "$($q.fx),$($q.fz),$($q.dist),$($q.yaw),$($q.pitch)"
  & $grab -Name "..\camcal\_aim_after" -Format png | Out-Null
  $after = & python tools/camera_read.py "out/camcal/_aim_after.png" --expect $ex --json |
           ConvertFrom-Json
  if (-not $after.ok) {
    "VERIFY unreadable after the move: $($after.problems -join '; ')"
    return
  }
  $p = $plan.plan.moves[-1].after
  if (-not $p) { $p = $plan.start }
  $a = $after.pose.pose
  $dy = [math]::Abs((($a.yaw - $p.yaw + 540) % 360) - 180)
  "predicted  yaw $([math]::Round($p.yaw,2))  pitch $([math]::Round($p.pitch,2))  range $([math]::Round($p.dist,2))"
  "actual     yaw $([math]::Round($a.yaw,2))  pitch $([math]::Round($a.pitch,2))  range $([math]::Round($a.dist,2))"
  "error      yaw $([math]::Round($dy,2)) deg  pitch $([math]::Round([math]::Abs($a.pitch - $p.pitch),2)) deg  range $([math]::Round([math]::Abs($a.dist - $p.dist),2)) tiles"
}
