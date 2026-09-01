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

  paste a whole build onto a fresh board: every out\<Stem>-landscape-*
        chunk, then every out\<Stem>-structure-* chunk, each held, committed
        and cleared at the client centre without the camera moving
        .\tools\review.ps1 paste -Name final -Stem forest

  tiled a `build --by-region` map onto a fresh board: every chunk at one
        cursor cell, in the order the build's own paste-order manifest gives
        -- which is NOT the filename order, because the chunk covering the
        anchor cell has to go down last

        .\tools\review.ps1 tiled -Name pelves -Stem pelves -OutDir out\pelves

  buildings
        the same, for a `build --per-building` output: the landscape first,
        then one building at a time, each on the shared marker, with a shot
        of every tenth so a paste that went wrong can be found afterwards
        .\tools\review.ps1 buildings -Name pb -Stem pb

Shots land in out\flyby\<Name>-<view>.jpg.
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet('360','flyby','paste','buildings','tiled')][string]$Recipe,
  [Parameter(Mandatory=$true)][string]$Name,
  [string]$Stem = "forest",
  [string]$OutDir,
  [int]$ShotEvery = 1,
  [string[]]$Slab,
  [int]$X = 700, [int]$Y = 600,
  [int]$ZoomOut = 6,
  [string]$Board,
  [string]$Source
)

$ts = Join-Path $PSScriptRoot "ts.ps1"
function TS { & $ts @args | Out-Null }
function Shot([string]$view) {
  & (Join-Path $PSScriptRoot "grab.ps1") -Name "$Name-$view"
}

# **Every paste is made looking straight down.** A pasted slab is anchored on
# the cursor's *ground hit point*, and that point is wherever the cursor's ray
# first meets something: the bare board for the first paste, the top of the
# grass -- or of a stump, or a pine -- for every paste after it. With the
# camera pitched, a higher hit slides the point toward the camera by
# (height x cot(pitch)), and the slab lands a cell or two short of where the
# layer before it landed. On a 36x30 crop that put every house one to two
# cells off its own floor, with a strip of floor showing on the far side of
# each -- the "dark pad beside a house" that was reported as buildings missing
# the mark. At a vertical pitch cot is zero and the hit point does not move,
# whatever is under the cursor. The pitch is restored afterwards so the
# recipes frame as they always did.
$PITCH_DOWN = 250
function Paste-Stack([string[]]$slabs, [int]$x, [int]$y) {
  TS orbit -X $x -Y $y -DX 0 -DY $PITCH_DOWN
  foreach ($s in $slabs) {
    TS paste -Slab $s -X $x -Y $y
    Start-Sleep -Seconds 3
    TS clear -X $x -Y $y
    Start-Sleep -Seconds 1
  }
  TS orbit -X $x -Y $y -DX 0 -DY (-$PITCH_DOWN)
}

switch ($Recipe) {

  '360' {
    if (-not $Slab) { throw "360 needs -Slab" }

    # An isolated board, every time. Sharing a board with the last probe is how
    # a stray stamp gets read as a defect in the thing being probed.
    TS newboard
    Paste-Stack $Slab $X $Y
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

    # Then cut it open. Six views around the outside say the faces close;
    # they cannot say the mass behind them is solid, and a mesh that hides
    # its own holes hides them from outside -- which is how the diagonal
    # blade and then the ruined wallbase each got approved. The cut box
    # (`N`) removes a region so the section is on show, and a blade reads as
    # a blade the moment you see it end-on. It is also how buried geometry,
    # the tile seams `verify` warns about, becomes visible.
    TS orbit -X 800 -Y 420 -DX 0 -DY 130     # leave it usable
    TS cutbox
    Start-Sleep -Milliseconds 800
    Shot "cut"
    TS cutbox                                # leave the board as found

    "360 on $Slab -> out\flyby\$Name-{n,e,s,w,plan,eye,cut}.jpg"
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

  'paste' {
    # The whole map, the way PASTE_HELP says: landscape first, then the
    # structures, all at one cursor cell, camera untouched between pastes.
    # The anchor is derived from the window rather than written down, and a
    # shot is taken with each chunk in hand and again after it lands, so a
    # paste that snapped wrong can be found afterwards without re-running it.
    $cl = & $ts client
    $cx = $cl.CX; $cy = $cl.CY
    TS newboard
    Start-Sleep -Seconds 3
    # **`newboard` can drop build mode, and the plane probe reads a toolbar
    # icon that only exists in it.** Checking before the new board is useless
    # -- that is exactly how a four-town run died on its first town with the
    # plane verified moments earlier. So read, and if the toolbar is not there
    # to read, toggle once and read again. Still unreadable is a stop: an
    # unreadable probe is not a pass.
    $plane = & $ts planestate
    if ($plane -notmatch '^build plane off') {
      TS key -Keys b -Hold 0.12
      Start-Sleep -Milliseconds 1000
      $plane = & $ts planestate
    }
    if ($plane -notmatch '^build plane off') { throw "$plane -- fix this before pasting" }
    $out = Join-Path $PSScriptRoot "..\out"
    $chunks = @(Get-ChildItem (Join-Path $out "$Stem-landscape-*.slab.txt")) +
              @(Get-ChildItem (Join-Path $out "$Stem-structure-*.slab.txt"))
    if (-not $chunks) { throw "no out\$Stem-*.slab.txt chunks to paste" }
    # Straight down for the whole stack -- see Paste-Stack for why.
    TS orbit -X $cx -Y $cy -DX 0 -DY $PITCH_DOWN
    $i = 0
    foreach ($c in $chunks) {
      $i++
      TS hold -Slab $c.FullName -X $cx -Y $cy
      Start-Sleep -Seconds 3
      Shot ("{0:d2}-hold" -f $i)
      TS commit -X $cx -Y $cy
      Start-Sleep -Seconds 4
      TS clear -X $cx -Y $cy
      Start-Sleep -Seconds 2
      Shot ("{0:d2}-down" -f $i)
      "$i : $($c.Name)"
    }
    TS orbit -X $cx -Y $cy -DX 0 -DY (-$PITCH_DOWN)
    "pasted $i chunk(s) of $Stem at $cx,$cy -> out\flyby\$Name-NN-{hold,down}.jpg"
  }

  'buildings' {
    # One building per paste, each on the shared registration marker.
    #
    # A structure chunk of forty buildings lands or fails as one thing, and
    # nothing in the result says which building went wrong -- that is what
    # made a single missing shell take a whole session to chase. Cut by
    # building (`build --per-building`) and each is its own paste: a bad one
    # is re-pasted alone, and the rest of the town is untouched.
    #
    # Every slab still carries the map's two markers, so they all go at the
    # same cursor cell and land in their own places. Camera straight down for
    # all of them -- see Paste-Stack.
    $cl = & $ts client
    $cx = $cl.CX; $cy = $cl.CY
    TS newboard
    Start-Sleep -Seconds 3
    $plane = & $ts planestate
    # **UNKNOWN means the toolbar is not drawn, which is a mode, not a fault.**
    # `newboard` can land outside build mode, and then `planestate` has no icon
    # to read and says so -- which used to kill a hundred-chunk run before its
    # first paste, on a board that was perfectly fine. Press B and read again;
    # only a second unreadable answer is a real stop.
    if ($plane -match 'UNKNOWN') {
      TS key -Keys b -Hold 0.12
      Start-Sleep -Milliseconds 800
      $plane = & $ts planestate
    }
    # Require an explicit "off". Matching 'ON' would sail straight past
    # "build plane UNKNOWN", which is what `planestate` now says when the
    # toolbar is not drawn and it has nothing to read.
    if ($plane -notmatch 'off') { throw "$plane -- fix this before pasting" }
    $out = Join-Path $PSScriptRoot "..\out"
    $land = @(Get-ChildItem (Join-Path $out "$Stem-landscape-*.slab.txt"))
    $bld  = @(Get-ChildItem (Join-Path $out "$Stem-structure-*.slab.txt"))
    if (-not $bld) { throw "no out\$Stem-structure-*.slab.txt slabs; build with --per-building" }

    TS orbit -X $cx -Y $cy -DX 0 -DY $PITCH_DOWN
    $i = 0
    foreach ($c in ($land + $bld)) {
      $i++
      TS paste -Slab $c.FullName -X $cx -Y $cy
      Start-Sleep -Seconds 2
      TS clear -X $cx -Y $cy
      Start-Sleep -Milliseconds 600
      if ($i -le $land.Count -or $i % 10 -eq 0) { Shot ("{0:d2}" -f $i) }
      "$i/$($land.Count + $bld.Count) : $($c.BaseName)"
    }
    TS orbit -X $cx -Y $cy -DX 0 -DY (-$PITCH_DOWN)
    "pasted $($land.Count) landscape + $($bld.Count) building slab(s) at $cx,$cy"
  }

  'tiled' {
    # A `build --by-region` map: one slab per region, every layer in it, so
    # nothing is ever pasted over anything and every chunk rests on bare board.
    #
    # **The order comes from the manifest, not from a glob.** The chunk whose
    # region covers the anchor cell is written last on purpose, so the anchor
    # is still bare board for every paste before it; sorted by filename it
    # lands in the middle instead, and the four chunks after it inherit its
    # height. That is a stepped map with nothing wrong in the files.
    $cl = & $ts client
    $cx = $cl.CX; $cy = $cl.CY
    TS newboard
    Start-Sleep -Seconds 3
    $plane = & $ts planestate
    # **UNKNOWN means the toolbar is not drawn, which is a mode, not a fault.**
    # `newboard` can land outside build mode, and then `planestate` has no icon
    # to read and says so -- which used to kill a hundred-chunk run before its
    # first paste, on a board that was perfectly fine. Press B and read again;
    # only a second unreadable answer is a real stop.
    if ($plane -match 'UNKNOWN') {
      TS key -Keys b -Hold 0.12
      Start-Sleep -Milliseconds 800
      $plane = & $ts planestate
    }
    # Require an explicit "off". Matching 'ON' would sail straight past
    # "build plane UNKNOWN", which is what `planestate` now says when the
    # toolbar is not drawn and it has nothing to read.
    if ($plane -notmatch 'off') { throw "$plane -- fix this before pasting" }
    $out = Join-Path $PSScriptRoot "..\out"
    if ($OutDir) { $out = $OutDir }
    $manifest = Join-Path $out "$Stem-paste-order.txt"
    if (-not (Test-Path $manifest)) {
      throw "no $manifest -- rebuild with --by-region so the paste order is written down"
    }
    $chunks = Get-Content $manifest | Where-Object { $_ } |
              ForEach-Object { Join-Path $out $_ }
    foreach ($c in $chunks) { if (-not (Test-Path $c)) { throw "missing chunk $c" } }

    TS orbit -X $cx -Y $cy -DX 0 -DY $PITCH_DOWN
    $i = 0
    foreach ($c in $chunks) {
      $i++
      # A shot with the slab in hand and again after it lands is how a paste
      # that snapped wrong is found afterwards without re-running the map. On a
      # hundred-chunk town that is two hundred grabs, so -ShotEvery thins them;
      # the first and the last are always kept, because the first proves the
      # run started on bare board and the last proves it finished.
      $watch = ($ShotEvery -le 1) -or ($i % $ShotEvery -eq 0) -or
               ($i -eq 1) -or ($i -eq $chunks.Count)
      TS hold -Slab $c -X $cx -Y $cy
      Start-Sleep -Seconds 3
      if ($watch) { Shot ("{0:d3}-hold" -f $i) }
      TS commit -X $cx -Y $cy
      Start-Sleep -Seconds 4
      TS clear -X $cx -Y $cy
      Start-Sleep -Seconds 2
      if ($watch) { Shot ("{0:d3}-down" -f $i) }
      "$i/$($chunks.Count) : $(Split-Path $c -Leaf)"
    }
    TS orbit -X $cx -Y $cy -DX 0 -DY (-$PITCH_DOWN)

    # **Name it and index it here, while anything still knows what it is.**
    # `newboard` leaves `Unknown Realm N`, and once the run ends nothing can
    # recover what went on: TaleSpire exposes no contents, no size and no date,
    # so a board holding a finished town and one holding last week's throwaway
    # are the same row in the campaign list. That is the whole reason a
    # twenty-board prune has to be done by eye.
    if ($Board) {
      TS rename -Text $Board
      Start-Sleep -Milliseconds 800
      # Windows PowerShell 5.1: no ternary, no null-coalescing.
      $src = $Source
      if (-not $src) { $src = $Stem }
      & python -m citysmith boards note --board $Board --holds town `
          --source $src --stem $Stem --chunks $chunks.Count --keep |
        Write-Output
    } else {
      "NOT INDEXED -- pass -Board to name this board and record what is on it."
    }
    "tiled $i chunk(s) of $Stem at $cx,$cy -> out\flyby\$Name-NN-{hold,down}.jpg"
  }
}
