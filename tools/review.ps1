<#
Two review recipes, so that "look at it from multiple angles" is a command
rather than a resolution.

Three wall blocks in a row were chosen from one or two views and all three put
daylight through the finished circuit. Each time the probe was fine and the
*reading* of it was not: a mesh that hides its own holes only hides them from
some directions, so a single screenshot cannot tell you it is solid. These are
the two passes that catch that, written down so they run the same way twice.

  360   an anomaly on its own board, walked all the way round
        .\tools\review.ps1 360 -Slab out\wallprobe.slab.txt -Name wall

  flyby a pasted map, toured at play height and from above
        .\tools\review.ps1 flyby -Name rev33

Shots land in out\flyby\<Name>-<view>.jpg.
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet('360','flyby')][string]$Recipe,
  [Parameter(Mandatory=$true)][string]$Name,
  [string]$Slab,
  [int]$X = 700, [int]$Y = 600,
  [int]$ZoomOut = 6
)

$ts = Join-Path $PSScriptRoot "ts.ps1"
function TS { & $ts @args | Out-Null }
function Shot([string]$view) {
  & (Join-Path $PSScriptRoot "grab.ps1") -Name "$Name-$view"
}

switch ($Recipe) {

  '360' {
    if (-not $Slab) { throw "360 needs -Slab" }

    # An isolated board, every time. Sharing a board with the last probe is how
    # a stray stamp gets read as a defect in the thing being probed.
    TS newboard
    TS paste -Slab $Slab -X $X -Y $Y
    Start-Sleep -Seconds 3
    TS clear -X $X -Y $Y
    1..$ZoomOut | ForEach-Object { TS zoom -X 800 -Y 450 -Ticks -12 }

    # Four faces at a low oblique. Low, because that is the angle a hole in a
    # mesh shows through -- from overhead a piece can cover its own gaps.
    TS orbit -X 800 -Y 420 -DX 0 -DY 60
    foreach ($face in @('n','e','s','w')) {
      Shot $face
      TS orbit -X 800 -Y 420 -DX 320 -DY 0
    }

    # Then the two extremes: straight down, which is what condemned the notched
    # roofs, and eye level, which is where a party actually stands.
    TS orbit -X 800 -Y 420 -DX 0 -DY 190
    Shot "plan"
    TS orbit -X 800 -Y 420 -DX 0 -DY -320
    Shot "eye"
    TS orbit -X 800 -Y 420 -DX 0 -DY 130     # leave it usable

    "360 on $Slab -> out\flyby\$Name-{n,e,s,w,plan,eye}.jpg"
  }

  'flyby' {
    # A tour of whatever is already pasted. WASD rather than a drag, because
    # this crosses a whole map: velocity ramps, so the key is held for seconds.
    TS clear -X 800 -Y 450
    Shot "00-start"

    # Legs are right-drags, not WASD. WASD ramps, and by two seconds it has
    # crossed a 187-tile map and gone off the far side into grey -- fine for a
    # hop between quarters, useless for a tour. A drag moves a screen at a time
    # and lands where it is aimed.
    $legs = @(
      @{ dx =    0; dy =  420; view = '01-north' },
      @{ dx = -460; dy =    0; view = '02-east'  },
      @{ dx =    0; dy = -420; view = '03-south' },
      @{ dx =  460; dy =    0; view = '04-west'  }
    )
    foreach ($leg in $legs) {
      TS rdrag -X 800 -Y 450 -DX $leg.dx -DY $leg.dy
      Shot $leg.view
    }

    TS orbit -X 800 -Y 450 -DX 0 -DY 180
    Shot "05-plan"
    TS orbit -X 800 -Y 450 -DX 0 -DY -300
    Shot "06-eye"
    TS orbit -X 800 -Y 450 -DX 320 -DY 0
    Shot "07-turned"

    "flyby -> out\flyby\$Name-*.jpg"
  }
}
