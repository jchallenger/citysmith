<#
Move a creature on the board, and prove which synthetic motion actually does it.

TaleSpire's own hint bar, read while the cursor is over a mini, is the
authority on this interaction:

    [mouse] PICKUP CREATURE
    ALT   + [mouse] ROTATE CREATURE
    CTRL  + [mouse] ELEVATE CREATURE
    SHIFT + [mouse] TELEPORT CREATURE

So a creature is *picked up and carried*, not click-placed like a slab: the
button stays down, the mini follows the cursor with a leash line and a live
"N TILES" readout, and the release drops it.

  .\tools\creature_drag.ps1 -X 1272 -Y 455 -DX -200 -DY 120
  .\tools\creature_drag.ps1 -X 1272 -Y 455 -DX -200 -DY 120 -Method setcursorpos
  .\tools\creature_drag.ps1 -X 1272 -Y 455 -Modifier shift    # teleport instead

-Shot writes a capture taken *while the button is still down*, which is the
only way to see the readout and the leash -- after the release there is
nothing on screen to say whether it worked.
#>
param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [int]$DX = 0, [int]$DY = 0,
  [ValidateSet('relative','setcursorpos')][string]$Method = 'relative',
  [ValidateSet('none','shift','ctrl','alt')][string]$Modifier = 'none',
  [int]$Steps = 25, [int]$StepMs = 45,
  [string]$Shot = ''
)
$ErrorActionPreference = 'Stop'

$sig = @'
using System;using System.Drawing;using System.Runtime.InteropServices;
public class CD {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point p);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,int dx,int dy,int d,IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk,byte scan,uint f,IntPtr e);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint c,uint t);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public const uint MOVE=0x0001, LDOWN=0x02, LUP=0x04;
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $sig -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue

$p = Get-Process TaleSpire -ErrorAction Stop | Select-Object -First 1
[CD]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400

$mod = switch ($Modifier) { 'shift' {0x10} 'ctrl' {0x11} 'alt' {0x12} default {0} }
if ($mod) {
  [CD]::keybd_event([byte]$mod,[byte][CD]::MapVirtualKey($mod,0),0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds 120
}

[CD]::SetCursorPos($X,$Y) | Out-Null
Start-Sleep -Milliseconds 350
[CD]::mouse_event([CD]::LDOWN,0,0,0,[IntPtr]::Zero)
Start-Sleep -Milliseconds 250            # let the pickup register before moving

for ($i = 1; $i -le $Steps; $i++) {
  if ($Method -eq 'setcursorpos') {
    # Kept so the failure stays reproducible rather than becoming folklore.
    [CD]::SetCursorPos($X + [int]($DX*$i/$Steps), $Y + [int]($DY*$i/$Steps)) | Out-Null
  } else {
    # Relative motion is what a real mouse sends. Pointer acceleration means
    # the cursor travels further than the sum of the steps, so the landing
    # point is read back rather than assumed.
    [CD]::mouse_event([CD]::MOVE, [int]($DX/$Steps), [int]($DY/$Steps), 0, [IntPtr]::Zero)
  }
  Start-Sleep -Milliseconds $StepMs
}
Start-Sleep -Milliseconds 350

$pt = New-Object System.Drawing.Point
[CD]::GetCursorPos([ref]$pt) | Out-Null

if ($Shot) { & (Join-Path $PSScriptRoot 'grab.ps1') -Name $Shot | Out-Null }

[CD]::mouse_event([CD]::LUP,0,0,0,[IntPtr]::Zero)
Start-Sleep -Milliseconds 300
if ($mod) { [CD]::keybd_event([byte]$mod,[byte][CD]::MapVirtualKey($mod,0),2,[IntPtr]::Zero) }
Start-Sleep -Milliseconds 700

"$Method drag from ($X,$Y) by ($DX,$DY); cursor came to rest at $($pt.X),$($pt.Y)"
if ($Shot) { "mid-drag capture -> out/flyby/$Shot.jpg" }
