# Capture the TaleSpire client area to out/flyby/<name>.jpg
# Region defaults to the TaleSpire client area, *derived from the window* rather
# than hardcoded -- the window gets moved and resized between sessions, and a
# stale rectangle silently crops the wrong pixels, which is how a pixel probe
# started reading the board instead of the toolbar icon it was aimed at.
# Pass a region to capture something smaller: the pixels are native, so a crop
# is a real zoom rather than an upscale.
param([Parameter(Mandatory=$true)][string]$Name,
      [int]$X = -1, [int]$Y = -1, [int]$W = -1, [int]$H = -1)
$code = @'
using System;using System.Drawing;using System.Drawing.Imaging;
public class Grab {
  public static void Shot(string path,int x,int y,int w,int h){
    using(var bmp=new Bitmap(w,h))
    using(var g=Graphics.FromImage(bmp)){
      g.CopyFromScreen(x,y,0,0,new Size(w,h));
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
$path = Join-Path $dir "$Name.jpg"
if ($X -lt 0 -or $Y -lt 0 -or $W -lt 0 -or $H -lt 0) {
  $rect = & (Join-Path $PSScriptRoot "ts.ps1") client
  if ($X -lt 0) { $X = $rect.X }
  if ($Y -lt 0) { $Y = $rect.Y }
  if ($W -lt 0) { $W = $rect.W }
  if ($H -lt 0) { $H = $rect.H }
}
[Grab]::Shot($path, $X, $Y, $W, $H)
"$Name -> $((Get-Item $path).Length) bytes"
