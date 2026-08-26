<#
Read the elevation ruler down the right-hand edge of TaleSpire's client.

There is a vertical scale hanging on the right of the board, graduated in
TILES: hovering its markers names them ("0 TILES" on the sliding handle,
"0.5 TILES" on the reticle, "60 TILES" on the locked green marker at the top).
It is the readout for the working elevation plane -- the thing `ts.ps1 elev`
drives, the thing a build tool cuts at, and the thing an `X`+drag selection
slices at. Until now that plane was driven blind, which is most of why
copy-out is still unsolved: "the plane could not be driven down to the turf"
was a guess, because nothing read it back.

**The track is anchored to the RIGHT window border.** `ts.ps1 camera` and
`camerastate` scanned a column at `client.X + 1540`, which is 60 px from the
right edge of a 1600-wide window and 380 px from the edge of a 1920-wide one.
On the maximised window that column lands on the Cutscene "Grab Shot" button,
rgb(0,114,165), and `camerastate` duly reported a handle position derived from
a button -- the third time in this project a probe has read the board or the
wrong widget because a coordinate was remembered instead of derived.

The handle is found by colour rather than position: its chevrons are
rgb(28,175,255), which nothing else in the band matches (the Grab Shot button
is rgb(0,114,165) -- close in hue, well clear on the green channel).

  .\tools\elevstate.ps1            # where is the plane?
  .\tools\elevstate.ps1 -Json
#>
param([switch]$Json)
$ErrorActionPreference = 'Stop'

$code = @'
using System;using System.Drawing;
public class ES {
  // Blue handle chevrons: rgb(28,175,255). Require a high blue AND a high
  // green, which is what separates the handle from the Cutscene-mode "Grab
  // Shot" button at rgb(0,114,165) sitting in the same band.
  public static int[] Handle(int x0,int y0,int w,int h){
    var b=new Bitmap(w,h); var g=Graphics.FromImage(b);
    g.CopyFromScreen(x0,y0,0,0,new Size(w,h));
    int n=0,sy=0,sx=0,miny=int.MaxValue,maxy=-1;
    for(int j=0;j<h;j++) for(int i=0;i<w;i++){
      var c=b.GetPixel(i,j);
      if(c.B>220 && c.G>150 && c.R<80){ n++; sy+=j; sx+=i; if(j<miny)miny=j; if(j>maxy)maxy=j; }
    }
    g.Dispose(); b.Dispose();
    if(n==0) return new int[]{0,-1,-1,-1,-1};
    return new int[]{ n, x0+sx/n, y0+sy/n, y0+miny, y0+maxy };
  }
  // The graduated track itself: a column of rgb(178,178,178) markers.
  public static int[] Track(int x0,int y0,int w,int h){
    var b=new Bitmap(w,h); var g=Graphics.FromImage(b);
    g.CopyFromScreen(x0,y0,0,0,new Size(w,h));
    int bestx=-1,bestn=0;
    for(int i=0;i<w;i++){
      int n=0;
      for(int j=0;j<h;j++){ var c=b.GetPixel(i,j);
        if(Math.Abs(c.R-178)<45 && Math.Abs(c.G-178)<45 && Math.Abs(c.B-178)<45) n++; }
      if(n>bestn){ bestn=n; bestx=x0+i; }
    }
    g.Dispose(); b.Dispose();
    return new int[]{ bestx, bestn };
  }
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue

$cl = & (Join-Path $PSScriptRoot 'ts.ps1') client
# Everything is offset from the RIGHT edge, because that is what the widget is
# anchored to. 280 px of band is enough for the chevrons, the bar, the track
# and the reticle at any window width seen.
$bx = $cl.X + $cl.W - 280
$by = $cl.Y + 80
$bw = 260
$bh = $cl.H - 200

$h = [ES]::Handle($bx,$by,$bw,$bh)
$t = [ES]::Track(($cl.X + $cl.W - 140), $by, 120, $bh)

if ($h[0] -eq 0) {
  "elevation ruler NOT FOUND in x $bx..$($bx+$bw). The widget may be hidden, or"
  " the client rect is wrong -- ts.ps1 client says W=$($cl.W)."
  exit 1
}

# Fraction along the track, 0 at the top (high) to 1 at the bottom (ground).
$top = $cl.Y + 104
$bot = $cl.Y + $cl.H - 170
$frac = [math]::Round((($h[2] - $top) / [double]($bot - $top)), 3)

if ($Json) {
  [pscustomobject]@{
    handleX = $h[1]; handleY = $h[2]; pixels = $h[0]
    trackX = $t[0]; frac = $frac
    clientW = $cl.W; clientH = $cl.H
  } | ConvertTo-Json -Compress
} else {
  "elevation ruler: track at x=$($t[0])  ($($cl.W - ($t[0] - $cl.X)) px from the right border)"
  "handle at ($($h[1]), $($h[2]))  rows $($h[3])..$($h[4])  [$($h[0]) px]"
  "position along track: frac $frac  (0 = top/high, 1 = bottom/ground)"
  "hover the markers to read them in tiles -- the handle, the reticle beside it,"
  "and the locked green marker at the top of the track."
}
