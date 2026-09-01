<#
Drive TaleSpire's window: focus, mouse, keys, camera.

Pasting is the only way to get a slab into TaleSpire and it has to go through
the UI, so every review cycle drives the game by hand. The rules that make it
work are in CLAUDE.md; this is the one implementation of them, so a session
does not rediscover them each time.

  .\tools\ts.ps1 focus
  .\tools\ts.ps1 paste  -Slab out\anchorA.slab.txt -X 800 -Y 500
  .\tools\ts.ps1 click  -X 800 -Y 500
  .\tools\ts.ps1 drop
  .\tools\ts.ps1 key    -Keys "^c"
  .\tools\ts.ps1 orbit  -X 800 -Y 500 -DX 260 -DY 0
  .\tools\ts.ps1 rdrag  -X 800 -Y 400 -DX 0 -DY 400      # precise; hand must be clear
  .\tools\ts.ps1 pan    -X 800 -Y 300 -DX 0 -DY 500      # right drag; empty hand first
  .\tools\ts.ps1 fly    -Keys w -Hold 3.0                # long haul; WASD ramps
  .\tools\ts.ps1 clear                                   # empty the hand (right TAP)
  .\tools\ts.ps1 cutbox                                  # N: slice into a mass
  .\tools\ts.ps1 camera -DY -260                         # raise the camera; wide view
  .\tools\ts.ps1 elev   -X 900 -Y 450 -DY 200            # ctrl+right drag: elevation
  .\tools\ts.ps1 camerastate                             # where is the camera looking?
  .\tools\ts.ps1 plane                                   # G: build plane on/off
  .\tools\ts.ps1 planestate                              # is it on? (reads the icon)
  .\tools\ts.ps1 newboard
  .\tools\ts.ps1 rename -Text "Graybank"                 # name the board you are on
  .\tools\ts.ps1 boards -Name list                       # open the board switcher, shot it
  .\tools\ts.ps1 shot   -Name mystem
  .\tools\ts.ps1 zoom   -X 800 -Y 500 -Ticks -4
  .\tools\ts.ps1 select -X 400 -Y 300 -X2 1200 -Y2 700
  .\tools\ts.ps1 copyout
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet(
    'focus','client','paste','hold','commit','raise','lower','nudge','click','drop','clear','move','newboard','rename','boards','shot',
    'key','chord','fly','orbit','pan','rdrag','elev','zoom','camera','camerastate',
    'elevplane','elevstate',
    'cutbox','plane','planestate','boardsstate','hudstate','setfolder',
    'select','copyout','setclip')]
  [string]$Cmd,
  [string]$Slab, [string]$Keys, [string]$Text, [string]$Name,
  [int]$X, [int]$Y, [int]$X2, [int]$Y2, [int]$DX, [int]$DY, [int]$Ticks = 0,
  [double]$Hold = 0.25,
  [ValidateSet('vertical','plane','rotate')][string]$Mode = 'vertical',
  [switch]$Keep
)

$sig = @'
using System;using System.Runtime.InteropServices;
public struct TSRECT { public int L, T, R, B; }
public class TSWin {
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out TSRECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref System.Drawing.Point p);
}
public class TSIn {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,int dx,int dy,int d,IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk,byte scan,uint f,IntPtr e);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code,uint type);
  // Two things a synthetic keystroke needs before Unity will see it. It has to
  // be *held*: Unity polls input, so a zero-duration press lands between polls
  // and is never seen. And it has to carry a scan code: the Input System reads
  // raw input, where the scan code is what identifies the key, so a keybd_event
  // with scan 0 arrives as no key at all -- which is why Ctrl+V looked like a
  // clipboard problem for an hour.
  public static void Chord(byte[] vks,int ms){
    foreach(byte v in vks){
      keybd_event(v,(byte)MapVirtualKey(v,0),0,IntPtr.Zero);
      System.Threading.Thread.Sleep(20);
    }
    System.Threading.Thread.Sleep(ms);
    for(int i=vks.Length-1;i>=0;i--){
      keybd_event(vks[i],(byte)MapVirtualKey(vks[i],0),2,IntPtr.Zero);
      System.Threading.Thread.Sleep(20);
    }
  }
  public const uint LDOWN=0x02, LUP=0x04, MDOWN=0x20, MUP=0x40, RDOWN=0x08, RUP=0x10, WHEEL=0x800;
  public static void Move(int x,int y){ SetCursorPos(x,y); }
  public static void Btn(uint down,uint up,int ms){
    mouse_event(down,0,0,0,IntPtr.Zero);
    System.Threading.Thread.Sleep(ms);
    mouse_event(up,0,0,0,IntPtr.Zero);
  }
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $sig -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.Windows.Forms


#: Key names to virtual-key codes, for the chord sender.
$VK = @{
  ctrl=0x11; shift=0x10; alt=0x12; esc=0x1B; escape=0x1B; space=0x20;
  enter=0x0D; tab=0x09; delete=0x2E;
  # The whole function row, not just the two that happened to be needed. A
  # missing name here does not fall back to anything sensible -- `Send-Chord`
  # only special-cases single characters, so `-Keys f8` threw "unknown key".
  f1=0x70; f2=0x71; f3=0x72; f4=0x73; f5=0x74; f6=0x75;
  f7=0x76; f8=0x77; f9=0x78; f10=0x79; f11=0x7A; f12=0x7B;
  up=0x26; down=0x28; left=0x25; right=0x27;
  home=0x24; end=0x23; insert=0x2D; pgup=0x21; pgdn=0x22
}
# 120 ms, not 150, and not the 40 ms that the measurement allows. TaleSpire
# runs under a 25 fps cap here, so one frame is ~41 ms, and a key has to be
# held across at least one frame to be seen at all: swept with
# `tools/probe_input.ps1`, a 0 ms press registered 0/12 times, 10 ms 2/12,
# 20 ms 8/12, 30 ms 11/12 and 40 ms 12/12. 120 ms is three frames, which keeps
# a margin over the edge of the measurement -- a missed Ctrl+V is the most
# expensive failure this tool has, so this one is deliberately not tuned to
# the minimum.
function Send-Chord([string]$spec, [int]$ms = 120) {
  $codes = foreach ($part in $spec.ToLower().Split('+')) {
    if ($VK.ContainsKey($part)) { [byte]$VK[$part] }
    elseif ($part.Length -eq 1) { [byte][char]$part.ToUpper() }
    else { throw "unknown key '$part'" }
  }
  [TSIn]::Chord([byte[]]$codes, $ms)
  # Was 400. The screen answers a keypress in 42-55 ms (measured over 10
  # trials, `probe_input.ps1 latency`), so 150 is still three times the
  # observed response.
  Start-Sleep -Milliseconds 150
}

# Every screen coordinate in here is derived from TaleSpire's client rect, not
# hardcoded. The window gets moved and resized between sessions; a stale
# rectangle does not fail loudly, it silently aims a click or a pixel probe at
# the wrong thing -- which is how `planestate` came back reading the board.
function Get-Client {
  $p = Get-TS
  $pt = New-Object System.Drawing.Point 0,0
  $c = New-Object TSRECT
  [TSWin]::GetClientRect($p.MainWindowHandle, [ref]$c) | Out-Null
  [TSWin]::ClientToScreen($p.MainWindowHandle, [ref]$pt) | Out-Null
  [pscustomobject]@{
    X = $pt.X; Y = $pt.Y; W = $c.R; H = $c.B
    CX = $pt.X + [int]($c.R/2); CY = $pt.Y + [int]($c.B/2)
  }
}

# **Measure the GLYPHS, not the plate.** The first cut of these probes counted
# dark pixels and compared the widget against a control patch of board. That
# survives a dark board but NOT a dark *screen*: a freshly made board is an
# empty void, the control saturates at 100% dark, and the difference goes
# negative however bright the widget is -- so `hudstate` reported `down` at a
# HUD that was plainly up, and refused to open the board list at all.
#
# What separates a HUD from any board, bright or void, is near-white glyph
# pixels: measured across five real frames, the icon column is 6.7-8.6% light
# with the HUD up and 0.0% with it down, and the control strip is 0.0% in every
# state. Same for the list: 2.6-3.2% light with the panel open, 0.0% closed, on
# a bright board and on a void one. Dark fraction is kept only for reporting.
function Get-PatchStats([int]$x0, [int]$y0, [int]$w, [int]$h) {
  Add-Type -AssemblyName System.Drawing
  $bmp = New-Object System.Drawing.Bitmap $w,$h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($x0, $y0, 0, 0, (New-Object System.Drawing.Size $w,$h))
  $n = 0; $d = 0; $l = 0
  for ($yy = 0; $yy -lt $h; $yy += 3) {
    for ($xx = 0; $xx -lt $w; $xx += 3) {
      $c = $bmp.GetPixel($xx, $yy); $n++
      if ([Math]::Max([Math]::Max($c.R,$c.G),$c.B) -lt 95) { $d++ }
      if ([Math]::Min([Math]::Min($c.R,$c.G),$c.B) -gt 185) { $l++ }
    }
  }
  $g.Dispose(); $bmp.Dispose()
  return [pscustomobject]@{ dark = 100.0*$d/$n; light = 100.0*$l/$n }
}

function Read-Hud {
  # Is the left tool column up? **`Space` is a toggle over the HUD, not over
  # the board list, and conflating the two is what made `boards` unsafe.**
  # There are three states, not two: HUD down + panel closed (Space raises it),
  # HUD UP + panel closed (Space HIDES it, and the click that follows lands on
  # the board), and HUD up + panel open. A probe that only asks about the panel
  # cannot tell the first two apart, which is exactly the mistake that put two
  # stray clicks on a live board on 2026-08-26.
  #
  # Measured on the icon column's GLYPHS against a control strip of board, so
  # it holds on a bright board and on an empty void alike: up 6.7 / 6.7 / 8.6,
  # down 0.0 / 0.0, control 0.0 in every state. See Get-PatchStats for why the
  # dark-plate version had to go.
  $cl = Get-Client
  $y = $cl.Y + [int]($cl.H * 0.037)
  $h = [int]($cl.H * 0.167)
  $column  = Get-PatchStats ($cl.X + 5) $y 40 $h
  $control = Get-PatchStats ($cl.X + $cl.W - 220) $y 40 $h
  $delta = $column.light - $control.light
  $state = if ($column.light -gt 3.0 -and $delta -gt 2.0) { 'up' }
           elseif ($column.light -lt 1.0) { 'down' }
           else { 'unknown' }
  return [pscustomobject]@{ state = $state; delta = $delta
                            column = $column.light; control = $control.light }
}

function Read-BoardsPanel {
  # Is the Campaign Boards panel up? **Measured against BOTH states, and
  # deliberately self-calibrating**, because the obvious version of this probe
  # is the one that has now failed three times in this project: sample one
  # patch, call dark pixels "panel", and read the board instead. Graybank's
  # grass did it to `planestate`; the left tool column did it to a first cut of
  # this one, which reported OPEN at a closed panel and put a click on the
  # board.
  #
  # The fix is a control AND the right signal. The panel is anchored LEFT and
  # ends at a hard vertical edge around x=400, so sample row text just inside it
  # against board just outside. Measured on the LIGHT (glyph) fraction, which is
  # what survives a void board: open 2.61 / 2.61 / 3.23, closed 0.00 on a bright
  # board, on a void board and with the HUD down. The band between is nobody's
  # measurement and returns `unknown` rather than a guess.
  $cl = Get-Client
  $y = $cl.Y + [int]($cl.H * 0.231)
  $h = [int]($cl.H * 0.565)
  $in  = Get-PatchStats ($cl.X + 90)  $y 250 $h
  $out = Get-PatchStats ($cl.X + 430) $y 250 $h
  $delta = $in.light - $out.light
  $state = if ($in.light -gt 1.2 -and $delta -gt 1.0) { 'open' }
           elseif ($in.light -lt 0.5) { 'closed' }
           else { 'unknown' }
  return [pscustomobject]@{ state = $state; delta = $delta
                            inside = $in.light; outside = $out.light }
}

function Get-TS {
  $p = Get-Process TaleSpire -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $p) { throw "TaleSpire is not running." }
  return $p
}
function Focus-TS {
  $p = Get-TS
  [TSIn]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 350
}
# A zero-duration synthetic click is swallowed by Unity's input polling, so
# every press is down / short hold / up.
# Every camera move in TaleSpire is a drag, and every drag has to be slow: the
# camera *follows* the cursor rather than jumping to it, so a fast synthetic
# drag outruns it and registers as nothing at all. One implementation, three
# callers (right pan, middle orbit), so they cannot drift apart. A LEFT
# drag is not among them: it does not move the camera, and in build mode it
# picks the map up -- see `pan`.
function Drag([int]$px,[int]$py,[int]$dx,[int]$dy,[uint32]$down,[uint32]$up,
              [int]$steps = 60,[int]$ms = 40,
              [int]$grab = 250,[int]$pre = 250,[int]$hold = 300,[int]$post = 600) {
  [TSIn]::Move($px,$py); Start-Sleep -Milliseconds $pre
  [TSIn]::mouse_event($down,0,0,0,[IntPtr]::Zero)
  if ($grab -gt 0) { Start-Sleep -Milliseconds $grab }
  for ($i = 1; $i -le $steps; $i++) {
    [TSIn]::Move($px + [int]($dx*$i/$steps), $py + [int]($dy*$i/$steps))
    Start-Sleep -Milliseconds $ms
  }
  Start-Sleep -Milliseconds $hold
  [TSIn]::mouse_event($up,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds $post
}

# The cadence a *camera* drag actually needs, measured rather than guessed.
# `tools/drag_speed.ps1` rotates by a fixed amount at a range of cadences and
# compares each result against a 60x40 reference frame: 60x40, 40x25, 30x20,
# 20x16, 12x16 and 8x10 all land on the identical view, at the 0.47 noise
# floor, and they do it with the post-press pause removed as well. So the
# 2.4 s of dragging this file used to spend was buying nothing.
#
# CLAUDE.md said "24 x 16 ms outruns the camera and registers as nothing".
# That is refuted: 20x16 and 8x10 both deliver the full move. Whatever the
# original failure was, it was not the cadence.
#
# 16x12 is set here rather than the fastest that worked, so there is a margin
# over the measurement instead of sitting on its edge.
$CAM = @{ steps = 16; ms = 12; grab = 60; pre = 100; hold = 60; post = 200 }

function Press([int]$px,[int]$py,[uint32]$down,[uint32]$up,[int]$ms = -1) {
  if ($ms -lt 0) { $ms = [int]($Hold*1000) }
  [TSIn]::Move($px,$py); Start-Sleep -Milliseconds 120
  [TSIn]::Btn($down,$up,$ms); Start-Sleep -Milliseconds 250
}

switch ($Cmd) {
  'focus'   { Focus-TS; "focused" }
  'client'  { Get-Client }
  'setclip' { [System.Windows.Forms.Clipboard]::SetText($Text); "clipboard set ($($Text.Length) chars)" }
  'paste'   {
    $txt = (Get-Content -Raw $Slab).Trim()
    [System.Windows.Forms.Clipboard]::SetText($txt)
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 150
    Send-Chord "ctrl+v"; Start-Sleep -Milliseconds 1500
    Press $X $Y ([TSIn]::LDOWN) ([TSIn]::LUP)
    # A committed paste can stay in hand as a repeat stamp. Leaving it there is
    # how a stray copy of the map ends up under the next click, so unless the
    # caller asks to keep stamping, the hand is emptied by switching tools --
    # see 'clear' for why that rather than a right-click.
    if (-not $Keep) {
      Start-Sleep -Milliseconds 400
      Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP) 40
    }
    "pasted $Slab at $X,$Y"
  }
  'hold'    {
    # Refuse to hold anything while the build plane is up. It is the one piece
    # of state that silently changes where a paste lands, and it survives making
    # a new board, so "I did not turn it on this session" is not a defence.
    $plane = & $PSCommandPath planestate
    if ($plane -match 'ON') { throw "$plane -- press G (ts.ps1 plane) first" }

    # Ctrl+V only: the slab arrives in hand at the cursor and is NOT committed.
    # Paste is cursor-anchored *and snaps to whatever is under the cursor*, so
    # once one chunk is down the next one hovering over that new terrain can
    # snap a course higher -- which lands a whole chunk one step above its
    # neighbours and reads as grass over grass. Look at the preview before
    # committing; that is the whole point of splitting this from `paste`.
    $txt = (Get-Content -Raw $Slab).Trim()
    [System.Windows.Forms.Clipboard]::SetText($txt)
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 200
    Send-Chord "ctrl+v"; Start-Sleep -Milliseconds 1800
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 400
    "holding $Slab at $X,$Y -- not committed"
  }
  'commit'  { Focus-TS; Press $X $Y ([TSIn]::LDOWN) ([TSIn]::LUP); "committed at $X,$Y" }
  'nudge'   {
    # The three modifiers TaleSpire's own hint bar lists for a held object:
    #
    #   Ctrl  + scroll   move vertically
    #   Shift + scroll   move on the plane
    #   Alt   + scroll   rotate in place
    #
    # `raise`/`lower` were built on Shift, which is the *horizontal* one, so
    # every "nudge it down a course" test in this session was sliding the slab
    # sideways instead. Ctrl is the one that changes height.
    Focus-TS
    $vk = switch ($Mode) { 'vertical' {0x11} 'plane' {0x10} 'rotate' {0x12} }
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 150
    [TSIn]::keybd_event($vk,[byte][TSIn]::MapVirtualKey($vk,0),0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 150
    [TSIn]::mouse_event(0x800, 0, 0, ($Ticks*120), [IntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    [TSIn]::keybd_event($vk,[byte][TSIn]::MapVirtualKey($vk,0),2,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 300
    "nudged $Mode by $Ticks"
  }
  'raise'   { & $PSCommandPath nudge -X $X -Y $Y -Ticks $Ticks -Mode vertical }
  'lower'   { & $PSCommandPath nudge -X $X -Y $Y -Ticks (-$Ticks) -Mode vertical }
  'click'   { Focus-TS; Press $X $Y ([TSIn]::LDOWN) ([TSIn]::LUP); "clicked $X,$Y" }
  'move'    { Focus-TS; [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 200; "moved to $X,$Y" }
  'drop'    { Focus-TS; Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP) 40; "dropped" }
  'dblrclick' {
    # Double RIGHT click centres the camera on the point clicked -- the
    # community finding CLAUDE.md records as the primitive the planner was
    # missing. The two taps are inlined raw rather than made of two Press
    # calls, because Press sleeps 250 ms after the button and the pair then
    # misses the double-click window -- measured: two Presses moved nothing.
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 120
    [TSIn]::Btn([TSIn]::RDOWN,[TSIn]::RUP,40)
    Start-Sleep -Milliseconds 140
    [TSIn]::Btn([TSIn]::RDOWN,[TSIn]::RUP,40)
    "double right-clicked $X,$Y"
  }
  'clear'   {
    # Empty the hand. A right-click does it -- but it has to be a *tap*.
    #
    # This is the exact opposite of the left click that commits a paste, which
    # Unity's input polling swallows unless it is held ~200 ms. A right-click
    # held that long is read as the start of a drag and the slab stays in hand,
    # which is why `drop` appeared not to work for a whole session and stray
    # copies of the map kept landing under the next click. Verified on a blank
    # board: 250 ms holds it, 40 ms drops it.
    #
    # Things that do *not* clear the hand, all tested the same way: `K` (it
    # only toggles its own tool), clicking a tool on the build toolbar, and
    # `B`. `Escape` is untested and stays that way -- it backs out toward the
    # main menu.
    Focus-TS
    Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP) 40
    "cleared"
  }
  'key'     { Focus-TS; Send-Chord $Keys ([int]($Hold*1000)); "sent $Keys" }
  'fly'     {
    # WASD moves the camera, but velocity eases up to a maximum, so the key has
    # to be *held*: 0.4 s crawls a few tiles, 3 s crosses a 187-tile map. A tap
    # looks like a dead binding. Use this for distance, a drag for precision.
    Focus-TS
    Send-Chord $Keys ([int]($Hold*1000))
    "flew $Keys for $Hold s"
  }
  'camera'  {
    # This command was built on a wrong model and there is no quiet way to fix
    # it, so it refuses rather than doing something plausible.
    #
    # It scanned a column at `client.X + 1540` for the brightest pixel and
    # dragged it, on the belief that the right-hand vertical track is a camera
    # *height* slider. Two things are wrong with that. The offset is measured
    # from the LEFT edge while the widget is anchored to the RIGHT border, so
    # on a 1920-wide window it samples x=1540 -- which in Cutscene Mode is the
    # blue "Grab Shot" button, rgb(0,114,165). And the track is not a camera
    # control at all: dragged, it moves the elevation cut plane; and a large
    # camera height change (Ctrl+scroll) leaves it exactly where it was.
    #
    # Camera height is Ctrl+scroll or Ctrl+right-drag: `ts.ps1 nudge -Mode
    # vertical` with an empty hand, or `ts.ps1 elev`.
    throw ("ts.ps1 camera is withdrawn: the right-hand track is the ELEVATION " +
           "cut plane, not a camera height slider, and its column was measured " +
           "from the wrong window edge. Use 'elevplane' to drive the plane, or " +
           "'nudge -Mode vertical' / 'elev' to change camera height.")
  }
  'elevplane' {
    # Drive the elevation cut plane on the right-hand ruler.
    #
    # The ruler is graduated in TILES -- hover its markers and the game names
    # them: the sliding handle reads "0 TILES" at the bottom, a fixed reticle
    # beside it reads "0.5 TILES", and the locked green marker at the top of
    # the track reads "60 TILES". Everything below the plane renders with a
    # heavy green tint, which is how you can tell at a glance that it is
    # raised.
    #
    # Two things have to be right or this does nothing at all:
    #
    #  * **Grab the CHEVRONS, not the diamond on the track.** The blue chevron
    #    cluster (rgb(28,175,255)) is the handle; the diamond sitting on the
    #    track line is a fixed 0-tile marker and a press on it goes through to
    #    the board.
    #  * **Move with relative mouse motion.** A `SetCursorPos` walk presses the
    #    handle and never carries it, exactly as with a creature -- see
    #    `tools/creature_drag.ps1`.
    #
    # Negative -DY raises the plane, positive lowers it, in screen pixels.
    Focus-TS
    $st = & (Join-Path $PSScriptRoot 'elevstate.ps1') -Json | ConvertFrom-Json
    $hx = $st.handleX; $hy = $st.handleY
    [TSIn]::Move($hx,$hy); Start-Sleep -Milliseconds 300
    [TSIn]::mouse_event([TSIn]::LDOWN,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    $steps = 30
    for ($i = 1; $i -le $steps; $i++) {
      [TSIn]::mouse_event(0x0001, 0, [int]($DY/$steps), 0, [IntPtr]::Zero)
      Start-Sleep -Milliseconds 25
    }
    Start-Sleep -Milliseconds 300
    [TSIn]::mouse_event([TSIn]::LUP,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 600
    $after = & (Join-Path $PSScriptRoot 'elevstate.ps1') -Json | ConvertFrom-Json
    "elevation plane: handle y $hy -> $($after.handleY)  (frac $($st.frac) -> $($after.frac))"
  }
  'elevstate' { & (Join-Path $PSScriptRoot 'elevstate.ps1') }
  'camerastate' {
    # Every camera command here is a *relative* move, which is how a session
    # ends up lost over the void wondering why the map vanished. What the game
    # actually displays is the compass rose, which gives bearing by where N
    # points and pitch by how squashed the circle is.
    #
    # The height-slider reading that used to be printed here is GONE: it was
    # scanning a left-anchored column that lands on a Cutscene-mode button, and
    # the widget it thought it was reading is the elevation plane anyway. Use
    # `elevstate` for that.
    #
    # The compass is anchored to the BOTTOM-LEFT of the client, and the crop is
    # derived from the rect rather than remembered -- the old (490, 660) was
    # right for a 1600x900 window and lands in open board on a 1920x1080 one.
    Focus-TS
    $cl = Get-Client
    $grab = Join-Path $PSScriptRoot "grab.ps1"
    & $grab -Name "camstate-compass" -X ($cl.X + 55) -Y ($cl.Y + $cl.H - 175) -W 150 -H 100 | Out-Null
    "compass -> out/flyby/camstate-compass.jpg  (bearing from where N points, pitch from how flat the circle is)"
    "elevation plane: run 'ts.ps1 elevstate'"
  }

  'cutbox'  {
    # `N` toggles the cut box, which hides everything inside a region so you can
    # look *into* solid geometry rather than at its faces.
    #
    # This is the check the wall probes were missing. A mesh that covers its own
    # holes covers them from outside; cut the mass open and a blade reads as a
    # blade immediately. It is also the way to see buried geometry -- the tile
    # seams `verify` warns about -- and eventually interiors.
    Focus-TS
    Send-Chord "n" 150
    "cutbox toggled"
  }
  'plane'   {
    # `G` toggles the build plane: a grid at a fixed elevation that a paste
    # snaps to *instead of* to the terrain under the cursor. Shift+scroll moves
    # it. Both survive making a new board.
    #
    # This is a paste-time fault with the exact shape of "grass above grass": a
    # chunk lands a course above its neighbours and nothing in the slab data is
    # wrong. It is easy to leave on by accident -- pressing `g` while probing
    # keybinds is enough -- and the only tell is a small orange highlight on one
    # toolbar icon.
    Focus-TS
    Send-Chord "g" 150
    "build plane toggled"
  }
  'planestate' {
    # Read the G icon rather than remembering. TaleSpire highlights an active
    # tool with an orange box, so the whole icon square is averaged: a single
    # pixel lands on the white glyph and reads the same either way. Calibrated
    # in-game -- off is rgb(71,71,71), on is rgb(173,117,73), so red minus blue
    # separates them by a mile.
    Focus-TS
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap 40,40
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $cl = Get-Client
    # **The build toolbar is CENTRED**, so its icons move with the client width.
    # 752 was right for a 1600-wide window and 160px wrong the moment the window
    # was maximised to 1920 -- the probe then sampled empty toolbar or bare
    # board and reported UNKNOWN on a board where the plane was plainly off.
    # Derive it from the centre, the same way every other coordinate here is
    # derived from the rect rather than remembered.
    $gx = $cl.X + [int]($cl.W/2) - 48
    $gfx.CopyFromScreen($gx, $cl.Y + 124, 0, 0, (New-Object System.Drawing.Size 40,40))
    $r = 0; $g = 0; $b = 0; $n = 0
    for ($y = 0; $y -lt 40; $y += 2) {
      for ($x = 0; $x -lt 40; $x += 2) {
        $c = $bmp.GetPixel($x, $y); $r += $c.R; $g += $c.G; $b += $c.B; $n++
      }
    }
    # **Is the toolbar even on screen?** Outside build mode it is not drawn at
    # all, and then this samples the board instead -- Graybank's grass reads
    # rgb(177,176,69), r-b = 108, a confident "ON" with no icon anywhere near
    # the probe. Believing that cost a toggle in the wrong direction: the plane
    # was off, `plane` turned it ON, and this said it had failed to turn off.
    #
    # Two tests, because either alone has a false positive. The strip above the
    # icons must be dark and grey (terrain is not), and the icon patch must
    # contain some near-white pixels (the glyph; terrain rarely does).
    $lit = 0
    for ($y = 0; $y -lt 40; $y += 2) {
      for ($x = 0; $x -lt 40; $x += 2) {
        $c = $bmp.GetPixel($x, $y)
        if ($c.R -gt 200 -and $c.G -gt 200 -and $c.B -gt 200) { $lit++ }
      }
    }
    $strip = New-Object System.Drawing.Bitmap 320,6
    $sg = [System.Drawing.Graphics]::FromImage($strip)
    $sg.CopyFromScreen($cl.X + [int]($cl.W/2) - 180, $cl.Y + 120, 0, 0, (New-Object System.Drawing.Size 320,6))
    $sr = 0; $sgc = 0; $sb = 0; $sn = 0
    for ($y = 0; $y -lt 6; $y += 2) {
      for ($x = 0; $x -lt 320; $x += 4) {
        $c = $strip.GetPixel($x, $y); $sr += $c.R; $sgc += $c.G; $sb += $c.B; $sn++
      }
    }
    $sg.Dispose(); $strip.Dispose()
    $sr = [int]($sr/$sn); $sgc = [int]($sgc/$sn); $sb = [int]($sb/$sn)
    $lum = [int](($sr + $sgc + $sb)/3)
    $grey = ([Math]::Abs($sr - $sgc) -lt 25) -and ([Math]::Abs($sgc - $sb) -lt 25)

    $gfx.Dispose(); $bmp.Dispose()
    $r = [int]($r/$n); $g = [int]($g/$n); $b = [int]($b/$n)

    if (-not ($grey -and $lum -lt 95 -and $lit -ge 2)) {
      "build plane UNKNOWN -- the build toolbar is not on screen (strip rgb($sr,$sgc,$sb), " +
      "glyph px $lit). Press B for build mode, then read it again. Do NOT paste on this reading."
    }
    elseif ($r - $b -gt 50) { "build plane ON  (rgb($r,$g,$b)) -- a paste will snap to it, not to the ground" }
    else                    { "build plane off (rgb($r,$g,$b))" }
  }
  'newboard' {
    Focus-TS
    $cl = Get-Client
    # The `+` is anchored to the RIGHT of the top bar, so its offset is from
    # the far edge. Hardcoded at 1332 it was correct for a 1600-wide window and
    # silently missed by 320px once the window was maximised to 1920 -- the
    # click landed on nothing and the "new" board was the one already open.
    Press ($cl.X + $cl.W - 268) ($cl.Y + 14) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Seconds 2
    Send-Chord "b" 150                     # a fresh board opens out of build mode
    Start-Sleep -Milliseconds 600
    "new board, building"
  }
  'rename'  {
    # Name the board you are on. The "..." beside the board name in the top
    # bar opens a Board Name dialog directly -- not a menu -- with the current
    # name already selected, so a Ctrl+V over it replaces the lot. Typing is
    # not needed and would not work anyway: TaleSpire reads raw input, so the
    # clipboard is the only reliable way to get text in.
    #
    # Both coordinates are derived from the client rect. The "..." is anchored
    # to the right edge; the dialog is centred, and OK sits left of centre.
    Focus-TS
    $cl = Get-Client
    [System.Windows.Forms.Clipboard]::SetText($Text)
    Press ($cl.X + $cl.W - 73) ($cl.Y + 14) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 900
    Send-Chord "ctrl+v" 150
    Start-Sleep -Milliseconds 400
    Press ($cl.X + [int]($cl.W/2) - 100) ($cl.Y + [int]($cl.H/2) + 67) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 800
    "renamed board to '$Text'"
  }
  'boards'  {
    # The board switcher is NOT the chevron beside the board name -- that is a
    # saved-state indicator. It is `Space` (which raises the HUD) and then the
    # top icon of the left-hand column: "Campaign Boards", a list with a play
    # arrow per board. **The list is PLAIN alphabetical** -- an earlier note
    # here claimed the current board is floated to the top, and that was wrong;
    # it is highlighted in place, and `Pelvesthollow` merely happened to sort
    # first on the campaign it was read off. It is also STRING alphabetical, so
    # `Unknown Realm 14` sorts before `Unknown Realm 2`. Row positions move as
    # boards are renamed, so screenshot it and read the rows rather than
    # assuming an order.
    #
    # **The current board's row is HIGHLIGHTED, not expanded** (measured off
    # out/flyby/finalcheck.jpg, 2026-08-26): orange fill, a person icon in
    # place of the play arrow, and the SAME height as every other row -- the
    # 42 px pitch is uniform through it. An earlier note claimed it expands in
    # place and pushes rows below it down ~134 px; that is what a row looks
    # like after you click its expander at x=79, not what the current board
    # does on its own. Row arithmetic is therefore usable -- but the list still
    # re-sorts on every rename, so read the shot anyway.
    #
    # The panel also carries a **Filter box** at the top, which is the thing
    # that would make this unattended; see the skill before building on it.
    #
    # **`Space` is a TOGGLE, so this used to be unsafe rather than merely
    # unreliable.** It pressed Space blind and then clicked a fixed point: with
    # the panel already up, Space closed the HUD and that click landed on the
    # BOARD, in build mode. Seen twice on 2026-08-26, and a click on a board in
    # build mode is not a no-op. It reads the panel first now, and refuses on
    # anything it cannot read.
    Focus-TS
    $cl = Get-Client
    $st = Read-BoardsPanel
    if ($st.state -eq 'open') {
      # Already there. Pressing Space here would hide it again, which is how
      # this command used to "fail" every other call.
    }
    else {
      $hud = Read-Hud
      if ($st.state -eq 'unknown' -or $hud.state -eq 'unknown') {
        "campaign boards UNKNOWN (panel delta $([int]$st.delta), hud delta " +
        "$([int]$hud.delta)) -- refusing to press Space blind. The click that " +
        "follows Space lands on the BOARD in build mode when the HUD is " +
        "already up, and that is not a no-op. Look at the window and retry."
        break
      }
      # Space toggles the HUD, so only press it when the HUD is actually down.
      if ($hud.state -eq 'down') {
        Send-Chord "space" 150
        Start-Sleep -Milliseconds 900
        $hud = Read-Hud
      }
      if ($hud.state -ne 'up') {
        "the HUD did not come up (state $($hud.state), delta " +
        "$([int]$hud.delta)). Nothing was clicked. Retry."
        break
      }
      Press ($cl.X + 17) ($cl.Y + 57) ([TSIn]::LDOWN) ([TSIn]::LUP)
      Start-Sleep -Seconds 2
      $st = Read-BoardsPanel
    }
    $shot = if ($Name) { $Name } else { "boards" }
    & (Join-Path $PSScriptRoot "grab.ps1") -Name $shot
    if ($st.state -ne 'open') {
      "campaign boards did NOT open (state $($st.state), delta " +
      "$([int]$st.delta)). Nothing was clicked on the board. Retry."
      break
    }
    # NOT a claim that it opened -- `Space` is a toggle, so if the HUD was
    # already up this closed it and the click landed on the board instead.
    # Look at the shot before trusting any row position.
    "campaign boards OPEN, verified (panel delta $([int]$st.delta)). Rows start " +
    "at client y=200, 42 apart; play arrow at x=360, row EXPANDER at x=79 -- the " +
    "expander opens Delete board, so aim at 360 unless you mean it. The list is " +
    "plain alphabetical (string order, so Unknown Realm 14 sorts before 2) and " +
    "the current board is HIGHLIGHTED in place at the same row height, not " +
    "expanded -- row arithmetic holds in a resting list. It re-sorts on every " +
    "rename, so read the rows off the shot rather than reusing a position."
  }
  'setfolder' {
    # File the board whose row is at -Y into the folder -Text, creating it if
    # it does not exist. `Y` is the row's own y, read off a `boards` shot --
    # **never computed from a row index**, because a folder header eats a slot
    # and an expanded folder inserts its children, so the Nth-board-to-y
    # mapping stops holding the moment folders exist.
    #
    # +68 is deliberately one number for two cases. `Reload Board` only appears
    # for the board you are standing on, so Set Folder sits at row+63 there and
    # row+68 everywhere else; menu items are ~26 px tall, so +68 falls inside
    # the item in both. Delete board is at +37/+42, a full item away -- which is
    # the reason this is a command and not six clicks by hand.
    if (-not $Text) { throw "setfolder needs -Text <folder name>" }
    if ($Y -lt 1)   { throw "setfolder needs -Y <row y>, read off a boards shot" }
    Focus-TS
    $cl = Get-Client
    Press ($cl.X + 79) $Y ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 1200
    Press ($cl.X + 150) ($Y + 68) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 1500
    [System.Windows.Forms.Clipboard]::SetText($Text)
    Press $cl.CX ($cl.CY - 16) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 300
    Send-Chord "ctrl+v" 150
    Start-Sleep -Milliseconds 400
    Press ($cl.CX - 105) ($cl.CY + 70) ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Milliseconds 1500
    "filed the board at row y=$Y into '$Text' -- VERIFY on a shot, this cannot read the row it clicked"
  }
  'hudstate' {
    $h = Read-Hud
    "hud $($h.state) (column $([math]::Round($h.column,2))% glyph, control " +
    "$([int]$h.control)%, delta $([int]$h.delta))"
  }
  'boardsstate' {
    $st = Read-BoardsPanel
    "campaign boards $($st.state) (inside $([math]::Round($st.inside,2))% glyph, outside " +
    "$([int]$st.outside)%, delta $([int]$st.delta))"
  }
  'shot'    { & (Join-Path $PSScriptRoot "grab.ps1") -Name $Name }
  'chord'   { Focus-TS; Send-Chord $Keys; "sent $Keys" }
  'orbit'   {
    # Middle drag rotates the camera. Reviewing from one angle is how three
    # wrong wall blocks got chosen, so this is not optional dressing.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::MDOWN) ([TSIn]::MUP) $CAM.steps $CAM.ms $CAM.grab $CAM.pre $CAM.hold $CAM.post
    "orbited $DX,$DY"
  }
  'pan'     {
    # **A LEFT drag does not pan, and in build mode it is destructive.** This
    # used to send LDOWN/LUP on the strength of a comment reading "Left drag
    # pans". Measured against a 0.59 noise floor on a 900x500 crop: a left
    # drag of 300 px moves the frame by 0.59 -- nothing at all -- while the
    # same right drag moves it 14.60. The binding table in
    # docs/pasting-into-talespire.md has said left-drag does NOT pan since it
    # was read off the hint bar; this verb simply contradicted it.
    #
    # Out of build mode that was a silent no-op, which cost a review pass
    # spent believing the camera would not move. IN build mode a left drag is
    # PICK UP OBJECT, so each call lifted whatever lay under the cursor and
    # dropped it somewhere else -- two calls quarried a two-tile hole out of a
    # board's terrain and left a grass tile standing in the void, and the
    # growing hole was very nearly read as a defect in the roof work being
    # reviewed. A driving verb that edits the map while claiming to move the
    # camera is the worst failure mode this tool has.
    #
    # It is the right drag now, same as `rdrag`, which is what actually pans.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::RDOWN) ([TSIn]::RUP) $CAM.steps $CAM.ms $CAM.grab $CAM.pre $CAM.hold $CAM.post
    "panned $DX,$DY"
  }
  'rdrag'   {
    # Right drag also pans, and more precisely -- but only with an empty hand.
    # Holding right *with something in hand* is read as the start of a drag and
    # the slab stays put, which is the same confusion that made `drop` look
    # broken. Clear first.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::RDOWN) ([TSIn]::RUP) $CAM.steps $CAM.ms $CAM.grab $CAM.pre $CAM.hold $CAM.post
    "right-dragged $DX,$DY"
  }
  'elev'    {
    # Ctrl + right-click drag controls elevation. Without it the working plane
    # -- which is what an X+drag selection is cut at -- stays wherever it was
    # last left, so a selection over open ground can come back completely
    # empty: the box is in the air above the grass. Copy-out kept returning a
    # 31-byte empty slab for exactly this reason.
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 200
    [TSIn]::keybd_event(0x11,[byte][TSIn]::MapVirtualKey(0x11,0),0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 200
    Drag $X $Y $DX $DY ([TSIn]::RDOWN) ([TSIn]::RUP) 40 30
    [TSIn]::keybd_event(0x11,[byte][TSIn]::MapVirtualKey(0x11,0),2,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 400
    "elevation dragged $DY"
  }
  'zoom'    { Focus-TS; [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 120; [TSIn]::mouse_event(0x800, 0, 0, ($Ticks*120), [IntPtr]::Zero); Start-Sleep -Milliseconds 400; "zoomed $Ticks" }
  'select'  {
    # X + left drag marks a region. The drag has to be slow for the same reason
    # every other drag does -- this one was still the fast version written
    # before that was understood, which is why it selected nothing.
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 200
    [TSIn]::keybd_event(0x58,[byte][TSIn]::MapVirtualKey(0x58,0),0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    Drag $X $Y ($X2-$X) ($Y2-$Y) ([TSIn]::LDOWN) ([TSIn]::LUP)
    [TSIn]::keybd_event(0x58,[byte][TSIn]::MapVirtualKey(0x58,0),2,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 500
    "selected $X,$Y -> $X2,$Y2"
  }
  'copyout' {
    Focus-TS
    Send-Chord "ctrl+c"; Start-Sleep -Milliseconds 1000
    $t = [System.Windows.Forms.Clipboard]::GetText()
    "$($t.Length) chars"
    $t | Set-Content -Encoding ascii (Join-Path $PSScriptRoot "..\out\copyout.slab.txt")
  }
}
