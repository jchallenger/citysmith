<#
How fast can a camera drag be driven before it stops delivering the full move?

`ts.ps1`'s Drag() walks 60 steps of 40 ms -- 2.4 s of dragging, plus a second
of fixed settling either side, for one camera move. CLAUDE.md records that
"24 x 16 ms outruns the camera and registers as nothing", which was read off a
screenshot rather than measured, and every review pass has paid the 60x40 since.

The test is a comparison, not a judgement about a picture. A slow drag of +DX
is the reference: rotate, capture, rotate back. Then each candidate cadence
does the same +DX and its result is compared against that reference. If the
candidate delivered the same rotation the two frames are the same frame, so
the difference sits at the noise floor; if it under-delivered, the view is
short of the reference and the difference is large.

Middle-drag orbit is used because it is safe on any board and it is what the
360 review pass spends its time on.

  .\tools\drag_speed.ps1
  .\tools\drag_speed.ps1 -Cadences "60x40,40x25,30x20,20x16,12x16,8x10"
#>
param(
  [int]$DX = 260,
  [string]$Cadences = "60x40,40x25,30x20,20x16,12x16,8x10",
  [int]$Settle = 500,
  [int]$GrabPause = 200
)
$ErrorActionPreference = 'Stop'

$sig = @'
using System;using System.Drawing;using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class DS {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,int dx,int dy,int d,IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public const uint MDOWN=0x20, MUP=0x40;
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
    for(int i=0;i<a.Length&&i<b.Length;i+=8){ int d=a[i]-b[i]; acc+=d<0?-d:d; n++; }
    return (double)acc/n;
  }
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $sig -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue

$rect = & (Join-Path $PSScriptRoot 'ts.ps1') client
$p = Get-Process TaleSpire; [DS]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 400

$sx = $rect.X + [int]($rect.W/2) - 250
$sy = $rect.Y + [int]($rect.H*0.28)
$sw = 500; $sh = 340
$px = $rect.X + [int]($rect.W/2)
$py = $rect.Y + [int]($rect.H*0.42)

function Orbit([int]$dx,[int]$steps,[int]$ms) {
  [DS]::SetCursorPos($px,$py) | Out-Null; Start-Sleep -Milliseconds 200
  [DS]::mouse_event([DS]::MDOWN,0,0,0,[IntPtr]::Zero)
  # The pause between the press and the first motion is separate from the
  # cadence, and it is the half that actually matters -- see -GrabPause.
  if ($GrabPause -gt 0) { Start-Sleep -Milliseconds $GrabPause }
  for ($i = 1; $i -le $steps; $i++) {
    [DS]::SetCursorPos($px + [int]($dx*$i/$steps), $py) | Out-Null
    Start-Sleep -Milliseconds $ms
  }
  Start-Sleep -Milliseconds 150
  [DS]::mouse_event([DS]::MUP,0,0,0,[IntPtr]::Zero)
  Start-Sleep -Milliseconds $Settle
}

$home_ = [DS]::Snap($sx,$sy,$sw,$sh)
Orbit $DX 60 40
$ref = [DS]::Snap($sx,$sy,$sw,$sh)
Orbit (-$DX) 60 40
$back = [DS]::Snap($sx,$sy,$sw,$sh)

"reference: 60 steps x 40 ms  ({0} ms of dragging)" -f (60*40)
"round-trip repeatability (home vs returned): {0}" -f [math]::Round([DS]::Diff($home_,$back),3)
"reference view differs from home by {0}  <- the size of the move being tested" -f [math]::Round([DS]::Diff($home_,$ref),3)
""
"cadence     drag_ms   vs_reference   verdict"
foreach ($c in ($Cadences -split ',')) {
  $parts = $c.Trim() -split 'x'
  $steps = [int]$parts[0]; $ms = [int]$parts[1]
  Orbit $DX $steps $ms
  $got = [DS]::Snap($sx,$sy,$sw,$sh)
  $d = [DS]::Diff($got,$ref)
  Orbit (-$DX) 60 40
  $verdict = if ($d -lt 4) { "same move" } elseif ($d -lt 12) { "close" } else { "SHORT" }
  "{0,-10}  {1,7}   {2,12}   {3}" -f $c.Trim(), ($steps*$ms), [math]::Round($d,2), $verdict
}
