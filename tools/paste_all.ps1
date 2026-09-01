<#
Paste every town, one after another, on its own fresh board.

Sequential by construction: one game window, one cursor. `review.ps1 tiled`
makes the board, checks the build plane, pastes in manifest order and renames
and indexes the board afterwards -- all this adds is the ordering and the
build-mode toggle its plane probe needs, since the probe reads a toolbar icon
that only exists in build mode.
#>
param([switch]$WhatIf)

$ts   = Join-Path $PSScriptRoot "ts.ps1"
$rev  = Join-Path $PSScriptRoot "review.ps1"
$root = Join-Path $PSScriptRoot ".."
# A letter after the date, because the plain date is already on four boards
# from earlier runs today and two boards with one name is worse than a long
# one. The campaign list clips around sixteen capitals, so keep it short.
$stamp = (Get-Date -Format "MM-dd") + "d"

$towns = @(
  @{ stem="pelvesthollow"; board="Pelvesthollow";     src="Pelvesthollow.geojson";     every=9  },
  @{ stem="graybank";      board="Graybank";          src="Graybank.geojson";          every=11 },
  @{ stem="forest";        board="Forest Church";     src="forest_church.json";        every=18 },
  @{ stem="tradebourne";   board="East Tradebourne";  src="East Tradebourne.geojson";  every=19 }
)

foreach ($t in $towns) {
  $out = Join-Path $root "out\regen\$($t.stem)"
  if (-not (Test-Path (Join-Path $out "$($t.stem)-paste-order.txt"))) {
    "SKIP $($t.stem): no paste order -- rebuild with --by-region"
    continue
  }
  # No plane check here on purpose. `review.ps1 tiled` makes the new board and
  # then checks, which is the only order that means anything: `newboard` can
  # drop build mode, so a reading taken before it describes the wrong moment.
  $name = "$($t.board -replace '[^A-Za-z]','')$stamp"
  "=== $($t.stem) -> board '$($t.board) $stamp' ==="
  if ($WhatIf) { continue }
  & $rev -Recipe tiled -Name $name -Stem $t.stem -OutDir $out `
         -Board "$($t.board) $stamp" -Source $t.src -ShotEvery $t.every |
    Select-Object -Last 2
}
"all done"
