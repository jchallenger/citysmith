<#
Delete boards off the top of the campaign list, one at a time, with a guard.

**This is the one irreversible operation in the whole toolset**, so read
`.claude/skills/talespire-boards/SKILL.md` ("Decide what to delete before you
learn how") and settle the scope before running it. It deletes whatever is at
`-RowY`, which is the FIRST UNGROUPED BOARD when both folders are collapsed --
client y=283 on a 1920x1080 window. Because the list is alphabetical and a
deleted row's successor rises into the same slot, deleting the top row
repeatedly needs no re-reading of positions between iterations.

**-Expect is not optional and not decoration.** Nothing here can read a board
name off the screen, so the only thing standing between this and deleting
somebody's town is that the caller checked the list first. Pass the number of
boards you have *seen* sharing the prefix you mean to remove, run it, and read
the list back before running it again.

The guard is the dialog's own orange title bar: if `Delete Board` is not up
where it should be, the paste and the OK click would land on the board behind
the panel instead, so the loop stops rather than clicking blind.

Coordinates are derived from the client rect. The skill's table lists
(800, 460) and (670, 515) for the field and OK; those are stale -- on a
1920x1080 client the dialog is centred, field at (960, 551), OK at (862, 605).

    .\tools\delete_boards.ps1 -Expect 4
#>
param(
  [Parameter(Mandatory=$true)][int]$Expect,
  [int]$RowY = 283,
  [switch]$WhatIf
)

Add-Type -AssemblyName System.Drawing
$ts = Join-Path $PSScriptRoot "ts.ps1"
function TS { & $ts @args | Out-Null }

function Get-Client {
  $c = & $ts client
  @{ X = [int]($c.X); Y = [int]($c.Y); W = [int]($c.W); H = [int]($c.H) }
}

function Pixel([int]$x, [int]$y) {
  $bmp = New-Object System.Drawing.Bitmap 1, 1
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size 1, 1))
  $p = $bmp.GetPixel(0, 0)
  $g.Dispose(); $bmp.Dispose()
  $p
}

$cl = Get-Client
$cx = $cl.X + [int]($cl.W / 2)
$cy = $cl.Y + [int]($cl.H / 2)
$titleY = $cy - 87      # the dialog's orange banner
$fieldY = $cy + 11
$okX    = $cx - 98
$okY    = $cy + 65

TS setclip -Text "DELETE"

for ($i = 1; $i -le $Expect; $i++) {
  TS click -X ($cl.X + 79)  -Y ($cl.Y + $RowY)      -Hold 0.3
  Start-Sleep -Milliseconds 900
  TS click -X ($cl.X + 145) -Y ($cl.Y + $RowY + 38) -Hold 0.3
  Start-Sleep -Milliseconds 1100

  # The dialog, or nothing. Its banner is a strong orange; the board behind the
  # panel is not, whatever is pasted on it.
  $p = Pixel $cx $titleY
  if (-not ($p.R -gt 170 -and $p.G -lt 130 -and $p.B -lt 90)) {
    throw ("delete $i of ${Expect}: no Delete Board dialog at ($cx, $titleY) " +
           "-- got rgb($($p.R),$($p.G),$($p.B)). Stopped; nothing further clicked.")
  }
  if ($WhatIf) { TS click -X ($cx + 98) -Y $okY -Hold 0.3; "would delete row $RowY ($i)"; continue }

  TS click -X $cx -Y $fieldY -Hold 0.3
  Start-Sleep -Milliseconds 600
  TS chord -Keys "ctrl+v"
  Start-Sleep -Milliseconds 600
  TS click -X $okX -Y $okY -Hold 0.3
  Start-Sleep -Milliseconds 1800
  "deleted $i of $Expect"
}

& (Join-Path $PSScriptRoot "grab.ps1") -Name "deleted-$Expect" -X ($cl.X + 60) -Y ($cl.Y + 180) -W 340 -H 500 | Out-Null
"list -> out\flyby\deleted-$Expect.jpg -- read it before running again"
