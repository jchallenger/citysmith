# Capture the TaleSpire client area to out/flyby/<name>.jpg
param([Parameter(Mandatory=$true)][string]$Name)
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
[Grab]::Shot($path, 156, 129, 1598, 834)
"$Name -> $((Get-Item $path).Length) bytes"
