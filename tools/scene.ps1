<#
Walk the party into a building: make the board if there is not one, and switch
to it if there is.

    .\tools\scene.ps1 enter  -Scene graybank-tavern-0014
    .\tools\scene.ps1 enter  -Scene graybank-tavern-0014 -WhatIf
    .\tools\scene.ps1 switch -Scene graybank-tavern-0014 -Row 3
    .\tools\scene.ps1 list

`enter` asks `citysmith boards status` what to do and branches on its exit
code. There are four answers and only one of them pastes anything:

  NEW (4)    no board recorded. New board, camera straight down, paste every
             slab at one cursor cell, rename the board, record it.
  READY (0)  the board exists and holds this build. Open the campaign board
             list, screenshot it, and stop -- see below.
  STALE (3)  the board exists but the scene has been rebuilt since. Still
             reuse it. -Rebuild pastes onto a SECOND board instead; nothing
             here ever deletes the first, because a board is where something
             happened and the board list cannot tell you what.
  MOVED (5)  the town was re-imported and this id may be a different building
             now. Reported, never guessed at.

**Switching boards needs one human look and that is deliberate.** The board
list is a picture: rows sort alphabetically and re-sort on every rename, there
is no API, and nothing here can read text off the screen. So `enter` opens the
list and saves a shot, you read which row your board is on, and
`switch -Row N` clicks it. Guessing the row means jumping to somebody else's
board -- and on a 387k-asset town that is a thirty second mistake.

Everything else about the paste is the procedure `review.ps1 tiled` uses and
`CLAUDE.md` explains: camera pitched vertical (the paste anchors on the
cursor's ray hit, and only a vertical camera keeps that hit still), one cursor
cell for every slab, hand emptied after each.
#>
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('enter','switch','list','status')]
  [string]$Cmd,
  [string]$Scene,
  [string]$Config = "config/scene.json",
  [int]$Row = 0,
  [int]$RowY = 0,
  [switch]$Rebuild,
  [switch]$WhatIf,
  [int]$X = 0,
  [int]$Y = 0
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$ts   = Join-Path $PSScriptRoot "ts.ps1"
$grab = Join-Path $PSScriptRoot "grab.ps1"

function TS { & $ts @args | Out-Null }
function Shot([string]$view) { & $grab -Name "scene-$view" | Out-Null }

function Cfg {
  $path = Join-Path $repo $Config
  if (-not (Test-Path $path)) { return $null }
  return (Get-Content $path -Raw | ConvertFrom-Json)
}

function Setting($cfg, [string]$section, [string]$key, $fallback) {
  if ($cfg -and $cfg.$section -and ($null -ne $cfg.$section.$key)) {
    return $cfg.$section.$key
  }
  return $fallback
}

$cfg      = Cfg
$outDir   = Join-Path $repo "out/scenes"
if ($cfg -and $cfg.out_dir) { $outDir = Join-Path $repo $cfg.out_dir }
$pitch    = [int](Setting $cfg "paste" "pitch_down" 250)
$holdSec  = [double](Setting $cfg "paste" "hold_seconds" 3)
$commitSec= [double](Setting $cfg "paste" "commit_seconds" 4)
$settle   = [double](Setting $cfg "paste" "settle_seconds" 2)

function Scene-Dir([string]$id) {
  if (Test-Path $id) {
    if ((Get-Item $id).PSIsContainer) { return (Resolve-Path $id).Path }
    return (Split-Path (Resolve-Path $id).Path -Parent)
  }
  $d = Join-Path $outDir $id
  if (-not (Test-Path $d)) {
    throw "no scene at $d -- build it first: python -m citysmith scene <layout> <building>"
  }
  return $d
}

function Scene-Manifest([string]$id) {
  $dir = Scene-Dir $id
  $manifest = Join-Path $dir "scene.json"
  if (-not (Test-Path $manifest)) { throw "no scene.json in $dir" }
  return (Get-Content $manifest -Raw | ConvertFrom-Json)
}

function Board-Status([string]$id) {
  Push-Location $repo
  try {
    $out = & python -m citysmith boards status $id
    $code = $LASTEXITCODE
  } finally { Pop-Location }
  # The first line is "<STATE> <board name>", and the name on it is the
  # RECORDED one -- which is not always the one in the manifest. A rebuild
  # lands on a second board, and the name to go looking for in the campaign
  # list is the one the board actually has.
  $first = @($out)[0]
  $recorded = ""
  if ($first -match '^[A-Z]+\s+(.+)$') { $recorded = $Matches[1] }
  return [pscustomobject]@{
    Text = ($out -join "`n"); Code = $code; Board = $recorded
  }
}

function Open-BoardList {
  # `Space` is a TOGGLE and ts.ps1 boards does not check the HUD state -- it
  # has failed twice for exactly that. Screenshot afterwards and look: if the
  # Campaign Boards panel is not up, Space closed it and it wants running
  # again. Nothing here can tell the difference, which is why this stops and
  # hands over rather than clicking a row it guessed at.
  TS boards -Name scene-boards
}

switch ($Cmd) {

  'list' {
    Push-Location $repo
    try { & python -m citysmith boards list } finally { Pop-Location }
  }

  'status' {
    if (-not $Scene) { throw "status needs -Scene <id>" }
    $s = Board-Status $Scene
    $s.Text
    "exit $($s.Code)"
  }

  'switch' {
    if ($Row -lt 1 -and $RowY -lt 1) {
      throw ("switch needs -Row N or -RowY <pixels>, read off the board-list " +
             "screenshot. Rows start at client y=200 and are 42 apart; the " +
             "play arrow is at x=360. The list re-sorts on every rename, so " +
             "never reuse a position from an earlier session.")
    }
    $cl = & $ts client
    # **-Row is arithmetic and the arithmetic is not always right.** The row of
    # the board you are standing on is EXPANDED in the list -- it shows Delete
    # board / Set Folder / Reload Board -- which pushes every row below it down
    # by about 134 px. So a target that sorts after the current board is not
    # where 200 + (N-1)*42 says it is. Measure the y off the screenshot and
    # pass -RowY when the current board sorts above your target.
    $rowY = 200 + ($Row - 1) * 42
    if ($RowY -gt 0) { $rowY = $RowY }
    if ($WhatIf) { "would click the play arrow on row $Row (client 360,$rowY)"; break }
    TS click -X (360 + $cl.X) -Y ($rowY + $cl.Y) -Hold 0.3
    # A big board takes tens of seconds to come up. Wait, then look, before
    # anything else is driven at it.
    Start-Sleep -Seconds 12
    Shot "switched"
    if ($Scene) {
      Push-Location $repo
      try { & python -m citysmith boards visit $Scene } finally { Pop-Location }
    }
    "clicked row $Row -- check out\flyby\scene-switched.jpg before driving anything else"
  }

  'enter' {
    if (-not $Scene) { throw "enter needs -Scene <id>" }
    $manifest = Scene-Manifest $Scene
    $dir      = Scene-Dir $Scene
    $board    = $manifest.board
    $status   = Board-Status $Scene
    $status.Text

    $mustPaste = ($status.Code -eq 4) -or $Rebuild
    if ($status.Code -eq 5 -and -not $Rebuild) {
      throw ("the recorded board may not be this building any more. Check it, " +
             "then either -Rebuild onto a new board or " +
             "`python -m citysmith boards forget $Scene`.")
    }

    if (-not $mustPaste) {
      "`nThe board is already there. Nothing will be pasted."
      "Open the campaign list, find the row named:"
      if ($status.Board) { "    $($status.Board)" } else { "    $board" }
      if ($WhatIf) { "(-WhatIf: not touching the game)"; break }
      Open-BoardList
      "`nout\flyby\scene-boards.jpg -- read the row number, then:"
      "    .\tools\scene.ps1 switch -Scene $Scene -Row <N>"
      break
    }

    # A rebuild goes onto a NEW board beside the old one. A pasted board
    # cannot be emptied -- there is no erase -- and deleting one is a manual,
    # irreversible operation this script has no business doing.
    if ($Rebuild -and $status.Code -ne 4) {
      $stamp = Get-Date -Format "MMdd"
      $board = "$($manifest.board) ($stamp)"
      "`nRebuilding onto a second board: $board"
      "The first one is left exactly as it is."
    }

    $order = Join-Path $dir "$($manifest.scene_id)-paste-order.txt"
    if (-not (Test-Path $order)) { throw "no paste order at $order" }
    $slabs = Get-Content $order | Where-Object { $_ } | ForEach-Object { Join-Path $dir $_ }
    foreach ($s in $slabs) { if (-not (Test-Path $s)) { throw "missing slab $s" } }

    if ($WhatIf) {
      "`nwould: new board, then paste $($slabs.Count) slab(s) at the client centre:"
      foreach ($s in $slabs) { "    $(Split-Path $s -Leaf)" }
      "  then rename the board to: $board"
      "  then record it against $($manifest.scene_id)"
      break
    }

    $cl = & $ts client
    $cx = $cl.CX; $cy = $cl.CY
    if ($X -gt 0) { $cx = $X }
    if ($Y -gt 0) { $cy = $Y }

    TS newboard
    Start-Sleep -Seconds 3
    $plane = & $ts planestate
    # An explicit "off". `-match 'ON'` sails straight past "build plane
    # UNKNOWN", which is what planestate says when it cannot read the toolbar,
    # and a build plane makes every slab land a course high with nothing wrong
    # in the file.
    if ($plane -notmatch 'off') { throw "$plane -- fix this before pasting" }

    TS orbit -X $cx -Y $cy -DX 0 -DY $pitch
    $i = 0
    foreach ($s in $slabs) {
      $i++
      TS hold -Slab $s -X $cx -Y $cy
      Start-Sleep -Seconds $holdSec
      Shot ("{0:d2}-hold" -f $i)
      TS commit -X $cx -Y $cy
      Start-Sleep -Seconds $commitSec
      TS clear -X $cx -Y $cy
      Start-Sleep -Seconds $settle
      Shot ("{0:d2}-down" -f $i)
      "$i/$($slabs.Count) : $(Split-Path $s -Leaf)"
    }
    TS orbit -X $cx -Y $cy -DX 0 -DY (-$pitch)

    # Named AFTER the paste, the way a town is: if the paste goes wrong you
    # want to re-run it onto another fresh board, not tidy up a board that
    # already has a name you care about.
    TS rename -Text $board
    Start-Sleep -Seconds 1
    Shot "named"

    Push-Location $repo
    try { & python -m citysmith boards record $Scene --board $board }
    finally { Pop-Location }

    "`n$($manifest.building_name) is on board '$board'."
    "The party marks are the odd tiles inside the door -- drop the minis on them."
    "Brief: $(Join-Path $dir 'brief.md')"
  }
}

# The exit code is the interface for anything driving this, and every branch
# above shells out to `citysmith boards status`, whose whole job is to exit
# non-zero. Without this the script inherits a 4 from a successful NEW run.
exit 0
