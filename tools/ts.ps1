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
  .\tools\ts.ps1 zoom   -X 800 -Y 500 -Ticks -4
  .\tools\ts.ps1 select -X 400 -Y 300 -X2 1200 -Y2 700
  .\tools\ts.ps1 copyout
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet(
    'focus','paste','click','drop','key','chord','orbit','pan','zoom','select','copyout','setclip')]
  [string]$Cmd,
  [string]$Slab, [string]$Keys, [string]$Text,
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
function Press([int]$px,[int]$py,[uint32]$down,[uint32]$up) {
  [TSIn]::Move($px,$py); Start-Sleep -Milliseconds 120
  [TSIn]::Btn($down,$up,[int]($Hold*1000)); Start-Sleep -Milliseconds 250
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
    # A committed paste stays in hand as a repeat stamp. Leaving it there is
    # how a stray copy of the map ends up under the next click, so unless the
    # caller asks to keep stamping, the hand is emptied here.
    if (-not $Keep) {
      Start-Sleep -Milliseconds 400
      Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP)
    }
    "pasted $Slab at $X,$Y"
  }
  'click'   { Focus-TS; Press $X $Y ([TSIn]::LDOWN) ([TSIn]::LUP); "clicked $X,$Y" }
  'drop'    { Focus-TS; Press $X $Y ([TSIn]::RDOWN) ([TSIn]::RUP); "dropped" }
  'key'     { Focus-TS; Send-Chord $Keys ([int]($Hold*1000)); "sent $Keys" }
  'chord'   { Focus-TS; Send-Chord $Keys; "sent $Keys" }
  'orbit'   {
    Focus-TS
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 120
    [TSIn]::mouse_event([TSIn]::MDOWN,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    $steps = 48
    for ($i=1; $i -le $steps; $i++) {
      [TSIn]::Move($X + [int]($DX*$i/$steps), $Y + [int]($DY*$i/$steps))
      Start-Sleep -Milliseconds 35
    }
    Start-Sleep -Milliseconds 300
    [TSIn]::mouse_event([TSIn]::MUP,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 400
    "orbited $DX,$DY"
  }
  'pan'     {
    # TaleSpire pans on a left drag over the board. Keys do not move the camera
    # and the wheel only zooms, so this is the only way across a 187-tile map.
    #
    # It has to be done *slowly*. A 24-step drag at 16 ms outruns the camera's
    # follow and the view snaps back; at 60 steps of 40 ms it tracks the cursor
    # the whole way. Same family of mistake as a zero-duration click.
    Focus-TS
    # No pre-emptive right-click here: with an empty hand right-click opens the
    # asset library over the board, which is worse than the stray stamp it was
    # meant to prevent. `paste` empties its own hand instead.
    [TSIn]::Move($X,$Y); Start-Sleep -Milliseconds 250
    [TSIn]::mouse_event([TSIn]::LDOWN,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 250          # let the grab register before moving
    $steps = 60
    for ($i=1; $i -le $steps; $i++) {
      [TSIn]::Move($X + [int]($DX*$i/$steps), $Y + [int]($DY*$i/$steps))
      Start-Sleep -Milliseconds 40
    }
    Start-Sleep -Milliseconds 300          # let it settle before letting go
    [TSIn]::mouse_event([TSIn]::LUP,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 600
    "panned $DX,$DY"
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
