# Capture the TaleSpire client area to out/flyby/<name>.jpg
# Region defaults to the TaleSpire client area, *derived from the window* rather
# than hardcoded -- the window gets moved and resized between sessions, and a
# stale rectangle silently crops the wrong pixels, which is how a pixel probe
# started reading the board instead of the toolbar icon it was aimed at.
# Pass a region to capture something smaller: the pixels are native, so a crop
# is a real zoom rather than an upscale.
# -Format png writes lossless RGB instead. JPEG is right for looking at and
# wrong for measuring: `camerafit.find_marks` classifies a pixel by how green
# it is, and JPEG's chroma subsampling smears exactly that channel across a
# mark's edge, which moves the centroid the fit is built on. PNG costs about
# four times the bytes and nothing else.
param([Parameter(Mandatory=$true)][string]$Name,
      [int]$X = -1, [int]$Y = -1, [int]$W = -1, [int]$H = -1,
      [ValidateSet('jpg','png')][string]$Format = 'jpg')
$code = @'
using System;using System.Drawing;using System.Drawing.Imaging;
public class Grab {
  public static void Shot(string path,int x,int y,int w,int h,bool png){
    using(var bmp=new Bitmap(w,h))
    using(var g=Graphics.FromImage(bmp)){
      g.CopyFromScreen(x,y,0,0,new Size(w,h));
      if(png){ bmp.Save(path,ImageFormat.Png); return; }
      ImageCodecInfo jpg=null;
      foreach(var e in ImageCodecInfo.GetImageEncoders()) if(e.MimeType=="image/jpeg") jpg=e;
      var p=new EncoderParameters(1);
      p.Param[0]=new EncoderParameter(Encoder.Quality,82L);
      bmp.Save(path,jpg,p);
    }
  }
}
'@
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
$dir = Join-Path $PSScriptRoot "..\out\flyby"
New-Item -ItemType Directory -Force $dir | Out-Null
$path = Join-Path $dir "$Name.$Format"
if ($X -lt 0 -or $Y -lt 0 -or $W -lt 0 -or $H -lt 0) {
  $rect = & (Join-Path $PSScriptRoot "ts.ps1") client
  if ($X -lt 0) { $X = $rect.X }
  if ($Y -lt 0) { $Y = $rect.Y }
  if ($W -lt 0) { $W = $rect.W }
  if ($H -lt 0) { $H = $rect.H }
}
[Grab]::Shot($path, $X, $Y, $W, $H, ($Format -eq 'png'))
"$Name -> $((Get-Item $path).Length) bytes"
