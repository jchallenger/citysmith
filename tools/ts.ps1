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
  .\tools\ts.ps1 pan    -X 800 -Y 300 -DX 0 -DY 500      # left drag, also short range
  .\tools\ts.ps1 fly    -Keys w -Hold 3.0                # long haul; WASD ramps
  .\tools\ts.ps1 clear                                   # empty the hand (right TAP)
  .\tools\ts.ps1 newboard
  .\tools\ts.ps1 shot   -Name mystem
  .\tools\ts.ps1 zoom   -X 800 -Y 500 -Ticks -4
  .\tools\ts.ps1 select -X 400 -Y 300 -X2 1200 -Y2 700
  .\tools\ts.ps1 copyout
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet(
    'focus','paste','click','drop','clear','move','newboard','shot',
    'key','chord','fly','orbit','pan','rdrag','zoom','select','copyout','setclip')]
  [string]$Cmd,
  [string]$Slab, [string]$Keys, [string]$Text, [string]$Name,
  [int]$X, [int]$Y, [int]$X2, [int]$Y2, [int]$DX, [int]$DY, [int]$Ticks = 0,
  [double]$Hold = 0.25,
  [switch]$Keep
)

$sig = @'
using System;using System.Runtime.InteropServices;
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
      System.Threading.Thread.Sleep(30);
    }
    System.Threading.Thread.Sleep(ms);
    for(int i=vks.Length-1;i>=0;i--){
      keybd_event(vks[i],(byte)MapVirtualKey(vks[i],0),2,IntPtr.Zero);
      System.Threading.Thread.Sleep(30);
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
Add-Type -TypeDefinition $sig -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.Windows.Forms


#: Key names to virtual-key codes, for the chord sender.
$VK = @{
  ctrl=0x11; shift=0x10; alt=0x12; esc=0x1B; escape=0x1B; space=0x20;
  enter=0x0D; tab=0x09; delete=0x2E; f1=0x70; f2=0x71;
  up=0x26; down=0x28; left=0x25; right=0x27
}
function Send-Chord([string]$spec, [int]$ms = 150) {
  $codes = foreach ($part in $spec.ToLower().Split('+')) {
    if ($VK.ContainsKey($part)) { [byte]$VK[$part] }
    elseif ($part.Length -eq 1) { [byte][char]$part.ToUpper() }
    else { throw "unknown key '$part'" }
  }
  [TSIn]::Chord([byte[]]$codes, $ms)
  Start-Sleep -Milliseconds 400
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
# callers (left pan, right pan, middle orbit), so they cannot drift apart.
function Drag([int]$px,[int]$py,[int]$dx,[int]$dy,[uint32]$down,[uint32]$up,
              [int]$steps = 60,[int]$ms = 40) {
  [TSIn]::Move($px,$py); Start-Sleep -Milliseconds 250
  [TSIn]::mouse_event($down,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds 250          # let the grab register before moving
  for ($i = 1; $i -le $steps; $i++) {
    [TSIn]::Move($px + [int]($dx*$i/$steps), $py + [int]($dy*$i/$steps))
    Start-Sleep -Milliseconds $ms
  }
  Start-Sleep -Milliseconds 300          # let it settle before letting go
  [TSIn]::mouse_event($up,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds 600
}

function Press([int]$px,[int]$py,[uint32]$down,[uint32]$up,[int]$ms = -1) {
  if ($ms -lt 0) { $ms = [int]($Hold*1000) }
  [TSIn]::Move($px,$py); Start-Sleep -Milliseconds 120
  [TSIn]::Btn($down,$up,$ms); Start-Sleep -Milliseconds 250
}

switch ($Cmd) {
  'focus'   { Focus-TS; "focused" }
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
  'click'   { Focus-TS; Press $X $Y ([TSIn]::LDOWN) ([TSIn]::LUP); "clicked $X,$Y" }
  'move'    { Focus-TS; [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 200; "moved to $X,$Y" }
  'drop'    { Focus-TS; Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP) 40; "dropped" }
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
  'newboard' {
    Focus-TS
    Press 1493 111 ([TSIn]::LDOWN) ([TSIn]::LUP)
    Start-Sleep -Seconds 2
    Send-Chord "b" 150                     # a fresh board opens out of build mode
    Start-Sleep -Milliseconds 600
    "new board, building"
  }
  'shot'    { & (Join-Path $PSScriptRoot "grab.ps1") -Name $Name }
  'chord'   { Focus-TS; Send-Chord $Keys; "sent $Keys" }
  'orbit'   {
    # Middle drag rotates the camera. Reviewing from one angle is how three
    # wrong wall blocks got chosen, so this is not optional dressing.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::MDOWN) ([TSIn]::MUP) 48 35
    "orbited $DX,$DY"
  }
  'pan'     {
    # Left drag pans. No pre-emptive right-click here: with an empty hand a
    # right-click opens the asset library over the board.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::LDOWN) ([TSIn]::LUP)
    "panned $DX,$DY"
  }
  'rdrag'   {
    # Right drag also pans, and more precisely -- but only with an empty hand.
    # Holding right *with something in hand* is read as the start of a drag and
    # the slab stays put, which is the same confusion that made `drop` look
    # broken. Clear first.
    Focus-TS
    Drag $X $Y $DX $DY ([TSIn]::RDOWN) ([TSIn]::RUP)
    "right-dragged $DX,$DY"
  }
  'zoom'    { Focus-TS; [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 120; [TSIn]::mouse_event(0x800, 0, 0, ($Ticks*120), [IntPtr]::Zero); Start-Sleep -Milliseconds 400; "zoomed $Ticks" }
  'select'  {
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 120
    [TSIn]::keybd_event(0x58,[byte][TSIn]::MapVirtualKey(0x58,0),0,[IntPtr]::Zero); Start-Sleep -Milliseconds 60
    [TSIn]::mouse_event([TSIn]::LDOWN,0,0,0,[IntPtr]::Zero)
    $steps = 20
    for ($i=1; $i -le $steps; $i++) {
      [TSIn]::Move($X + [int](($X2-$X)*$i/$steps), $Y + [int](($Y2-$Y)*$i/$steps))
      Start-Sleep -Milliseconds 20
    }
    Start-Sleep -Milliseconds 150
    [TSIn]::mouse_event([TSIn]::LUP,0,0,0,[IntPtr]::Zero)
    [TSIn]::keybd_event(0x58,[byte][TSIn]::MapVirtualKey(0x58,0),2,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 400
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
