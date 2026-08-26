<#
Does a synthetic drag actually reach TaleSpire?

`ts.ps1`'s Drag() walks the cursor with SetCursorPos. That moves the pointer,
but Unity's Input System reads mouse *motion* from raw input, and SetCursorPos
generates no motion event -- it teleports the pointer without telling anyone.
This is the same shape of failure as keybd_event with scan=0, which CLAUDE.md
already records: the input looks right from outside and arrives as nothing.

Measured against a creature drag it is unambiguous: the creature is picked up,
the cursor crosses 250 px, and the game's own readout says "0 TILES". Swap the
walk for relative mouse_event(MOUSEEVENTF_MOVE) and the same gesture reads
"4.1 TILES" and lands.

This compares the two on the *camera*, which is safe to move and easy to see,
so the finding is not resting on one creature.

  .\tools\drag_compare.ps1                 # middle-drag orbit, both methods
  .\tools\drag_compare.ps1 -Button right   # right-drag pan, both methods
#>
param(
  [ValidateSet('middle','right','left')][string]$Button = 'middle',
  [int]$DX = 260, [int]$DY = 0,
  [int]$Steps = 40, [int]$StepMs = 25
)
$ErrorActionPreference = 'Stop'

$sig = @'
using System;using System.Drawing;using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class DC {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point p);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,int dx,int dy,int d,IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public const uint MOVE=0x0001, LDOWN=0x02, LUP=0x04, MDOWN=0x20, MUP=0x40, RDOWN=0x08, RUP=0x10;

  public static byte[] Snap(int x,int y,int w,int h){
    Bitmap b=new Bitmap(w,h,PixelFormat.Format24bppRgb);
    Graphics g=Graphics.FromImage(b);
    g.CopyFromScreen(x,y,0,0,new Size(w,h));
    BitmapData d=b.LockBits(new Rectangle(0,0,w,h),ImageLockMode.ReadOnly,PixelFormat.Format24bppRgb);
    byte[] buf=new byte[d.Stride*h];
    Marshal.Copy(d.Scan0,buf,0,buf.Length);
    b.UnlockBits(d); g.Dispose(); b.Dispose();
    return buf;
  }
  public static double Diff(byte[] a,byte[] b){
    long acc=0; int n=0;
    for(int i=0;i<a.Length && i<b.Length;i+=8){ int d=a[i]-b[i]; acc += d<0?-d:d; n++; }
    return (double)acc/n;
  }
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $sig -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue

$rect = & (Join-Path $PSScriptRoot 'ts.ps1') client
$p = Get-Process TaleSpire; [DC]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400

$down, $up = switch ($Button) {
  'middle' { [DC]::MDOWN, [DC]::MUP }
  'right'  { [DC]::RDOWN, [DC]::RUP }
  'left'   { [DC]::LDOWN, [DC]::LUP }
}
# Sample a patch of board, away from the bars and the library strip.
$sx = $rect.X + [int]($rect.W/2) - 200
$sy = $rect.Y + [int]($rect.H*0.30)
$sw = 400; $sh = 300
$startX = $rect.X + [int]($rect.W/2)
$startY = $rect.Y + [int]($rect.H*0.40)

function Run-Drag([string]$how) {
  [DC]::SetCursorPos($startX,$startY) | Out-Null; Start-Sleep -Milliseconds 350
  $before = [DC]::Snap($sx,$sy,$sw,$sh)
  [DC]::mouse_event($down,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds 250
  for ($i = 1; $i -le $Steps; $i++) {
    if ($how -eq 'setcursorpos') {
      [DC]::SetCursorPos($startX + [int]($DX*$i/$Steps), $startY + [int]($DY*$i/$Steps)) | Out-Null
    } else {
      [DC]::mouse_event([DC]::MOVE, [int]($DX/$Steps), [int]($DY/$Steps), 0, [IntPtr]::Zero)
    }
    Start-Sleep -Milliseconds $StepMs
  }
  Start-Sleep -Milliseconds 300
  [DC]::mouse_event($up,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds 700
  $after = [DC]::Snap($sx,$sy,$sw,$sh)
  return [DC]::Diff($before,$after)
}

"$Button-drag of ($DX,$DY) px in $Steps steps of $StepMs ms"
"  SetCursorPos walk   : screen change {0}" -f [math]::Round((Run-Drag 'setcursorpos'),3)
Start-Sleep -Milliseconds 800
"  relative mouse_event: screen change {0}" -f [math]::Round((Run-Drag 'relative'),3)
""
"(a static board reads ~0.5; anything under ~2 means the drag did not arrive)"
