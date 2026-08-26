<#
Measure how fast TaleSpire actually answers synthetic input.

Everything in `ts.ps1` is built on one rule -- "a zero-duration press is
swallowed, so hold it" -- and a pile of Start-Sleep constants that were chosen
to be safely large and never measured. That is fine for correctness and
expensive for throughput: a 114-chunk board pays every one of those sleeps 114
times. This script is the measurement the constants never had.

The oracle is the screen. A capture of a small region is bounded by the desktop
compositor at ~60 Hz whatever its size (measured: 200x200 and 400x400 both come
back at 16.7 ms), so a background thread samples a patch at that rate, stamps
each sample against one Stopwatch, and the input is fired from the main thread
against the same clock. First sample whose difference from the baseline clears
a threshold is the response. Resolution is therefore one compositor frame,
~17 ms, which is finer than anything TaleSpire does.

  .\tools\probe_input.ps1 caprate                       # what the oracle can see
  .\tools\probe_input.ps1 renderrate                    # what TaleSpire draws at
  .\tools\probe_input.ps1 latency -Action key -Keys q   # ms until the screen moves
  .\tools\probe_input.ps1 sweep   -Action key -Keys q -Values "0,10,20,40,80,150,250"
  .\tools\probe_input.ps1 sweep   -Action scroll -Values "0"
  .\tools\probe_input.ps1 watch   -Ms 3000              # just record, fire nothing

`sweep` repeats each value -Trials times and reports how many registered, which
is the number that matters: a hold that works 9 times in 10 is not a hold that
works.
#>
param(
  [Parameter(Mandatory=$true)][ValidateSet('caprate','renderrate','latency','sweep','watch')]
  [string]$Cmd,
  [ValidateSet('key','click','rclick','scroll','none')][string]$Action = 'key',
  [string]$Keys = 'q',
  [string]$Values = '0,10,20,30,40,60,80,120,180,250',
  [int]$Trials = 5,
  [int]$Ms = 1200,
  [int]$Hold = 100,
  [int]$Ticks = -2,
  [int]$X = -1, [int]$Y = -1,
  [int]$RegionW = 320, [int]$RegionH = 240,
  [double]$Threshold = 2.0
)

$ErrorActionPreference = 'Stop'

$code = @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Diagnostics;
using System.Threading;
using System.Runtime.InteropServices;

public class Probe {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,int dx,int dy,int d,IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk,byte scan,uint f,IntPtr e);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code,uint type);
  public const uint LDOWN=0x02, LUP=0x04, RDOWN=0x08, RUP=0x10, WHEEL=0x800;

  // One clock for the capture thread and the input, so a latency is a
  // subtraction rather than a guess about two unrelated timers.
  public static Stopwatch Clock = Stopwatch.StartNew();
  public static double Now(){ return Clock.Elapsed.TotalMilliseconds; }

  static Thread worker;
  static volatile bool running;
  static int rx,ry,rw,rh;
  public static double[] Times = new double[4000];
  public static double[] Diffs = new double[4000];
  public static int Count = 0;

  // Sum of absolute channel differences against the first sample, over every
  // 16th byte. Subsampling keeps a capture inside the compositor's 16.7 ms
  // budget; the metric only has to separate "the screen moved" from "it did
  // not", not to quantify anything.
  static byte[] Snap(Bitmap bmp, Graphics g){
    g.CopyFromScreen(rx,ry,0,0,new Size(rw,rh));
    BitmapData d = bmp.LockBits(new Rectangle(0,0,rw,rh), ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
    int n = d.Stride*rh;
    byte[] buf = new byte[n];
    Marshal.Copy(d.Scan0, buf, 0, n);
    bmp.UnlockBits(d);
    return buf;
  }

  static void Loop(){
    Bitmap bmp = new Bitmap(rw,rh,PixelFormat.Format24bppRgb);
    Graphics g = Graphics.FromImage(bmp);
    byte[] base_ = Snap(bmp,g);
    Count = 0;
    while(running && Count < Times.Length){
      byte[] cur = Snap(bmp,g);
      long acc = 0;
      for(int i=0;i<cur.Length;i+=16){ int d2 = cur[i]-base_[i]; acc += d2<0?-d2:d2; }
      Times[Count] = Now();
      Diffs[Count] = (double)acc / (cur.Length/16.0);
      Count++;
    }
    g.Dispose(); bmp.Dispose();
  }

  public static void StartWatch(int x,int y,int w,int h){
    rx=x; ry=y; rw=w; rh=h; running=true;
    worker = new Thread(new ThreadStart(Loop));
    worker.IsBackground = true;
    worker.Start();
    Thread.Sleep(120);              // let the baseline and a few samples land
  }
  public static void StopWatch(){
    running=false;
    if(worker!=null) worker.Join(2000);
  }

  // Consecutive-frame differences instead of differences from a baseline --
  // this is what counts how often the *game* draws, not how far it has moved.
  public static double RenderRate(int x,int y,int w,int h,int ms){
    rx=x; ry=y; rw=w; rh=h;
    Bitmap bmp = new Bitmap(rw,rh,PixelFormat.Format24bppRgb);
    Graphics g = Graphics.FromImage(bmp);
    byte[] prev = Snap(bmp,g);
    double t0 = Now(); int frames=0, changed=0;
    while(Now()-t0 < ms){
      byte[] cur = Snap(bmp,g);
      long acc=0;
      for(int i=0;i<cur.Length;i+=16){ int d2=cur[i]-prev[i]; acc += d2<0?-d2:d2; }
      if(acc > cur.Length/16) changed++;
      frames++; prev=cur;
    }
    double el = Now()-t0;
    g.Dispose(); bmp.Dispose();
    LastFrames=frames; LastChanged=changed; LastElapsed=el;
    return changed*1000.0/el;
  }
  public static int LastFrames, LastChanged;
  public static double LastElapsed;

  public static string CapBench(int x,int y,int w,int h,int n){
    Bitmap bmp=new Bitmap(w,h); Graphics g=Graphics.FromImage(bmp);
    Stopwatch sw=Stopwatch.StartNew();
    for(int i=0;i<n;i++) g.CopyFromScreen(x,y,0,0,new Size(w,h));
    sw.Stop(); double per=sw.Elapsed.TotalMilliseconds/n;
    g.Dispose(); bmp.Dispose();
    return w+"x"+h+"  "+per.ToString("F2")+" ms/frame ("+(1000.0/per).ToString("F0")+" Hz)";
  }

  // Input, fired against the same Clock. Returns the moment the press went
  // DOWN -- latency is measured from the press, not from the release, because
  // that is what a human perceives and what Unity polls.
  public static double Key(string k,int holdMs){
    byte vk = (byte)k.ToUpper()[0];
    byte sc = (byte)MapVirtualKey(vk,0);
    double t = Now();
    keybd_event(vk,sc,0,IntPtr.Zero);
    Thread.Sleep(holdMs);
    keybd_event(vk,sc,2,IntPtr.Zero);
    return t;
  }
  public static double Click(uint down,uint up,int holdMs){
    double t = Now();
    mouse_event(down,0,0,0,IntPtr.Zero);
    Thread.Sleep(holdMs);
    mouse_event(up,0,0,0,IntPtr.Zero);
    return t;
  }
  public static double Scroll(int ticks){
    double t = Now();
    mouse_event(WHEEL,0,0,ticks*120,IntPtr.Zero);
    return t;
  }
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue

function Get-TSRect {
  # Derived from the window, never hardcoded -- the window was 1600x900 in an
  # earlier session's notes and is 1920x1080 now, and a stale rectangle aims
  # the probe at the wrong pixels without failing.
  $r = & (Join-Path $PSScriptRoot 'ts.ps1') client
  return $r
}
function Focus-TS {
  $p = Get-Process TaleSpire -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $p) { throw "TaleSpire is not running." }
  [Probe]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 300
}

$rect = Get-TSRect
# Default the sample patch to the middle of the board area. The bottom of the
# client is the asset library and the top is the bar; both change on their own
# and would read as a response to input that never happened.
if ($X -lt 0) { $X = $rect.X + [int]($rect.W/2) - [int]($RegionW/2) }
if ($Y -lt 0) { $Y = $rect.Y + [int]($rect.H*0.35) }

function Fire([string]$act,[int]$holdMs) {
  switch ($act) {
    'key'    { return [Probe]::Key($Keys,$holdMs) }
    'click'  { return [Probe]::Click([Probe]::LDOWN,[Probe]::LUP,$holdMs) }
    'rclick' { return [Probe]::Click([Probe]::RDOWN,[Probe]::RUP,$holdMs) }
    'scroll' { return [Probe]::Scroll($Ticks) }
    'none'   { return [Probe]::Now() }
  }
}

# One trial: watch the patch, fire the action, find the first sample after the
# press whose difference clears the threshold.
function Measure-Once([string]$act,[int]$holdMs,[int]$windowMs) {
  [Probe]::StartWatch($X,$Y,$RegionW,$RegionH)
  Start-Sleep -Milliseconds 150
  $t0 = Fire $act $holdMs
  Start-Sleep -Milliseconds $windowMs
  [Probe]::StopWatch()
  $n = [Probe]::Count
  $lat = -1.0; $peak = 0.0
  for ($i = 0; $i -lt $n; $i++) {
    $t = [Probe]::Times[$i]; $d = [Probe]::Diffs[$i]
    if ($t -lt $t0) { continue }
    if ($d -gt $peak) { $peak = $d }
    if ($lat -lt 0 -and $d -gt $Threshold) { $lat = $t - $t0 }
  }
  [pscustomobject]@{ Latency = $lat; Peak = [math]::Round($peak,2); Samples = $n }
}

switch ($Cmd) {

  'caprate' {
    "capture ceiling -- the finest the oracle can resolve"
    [Probe]::CapBench($X,$Y,200,200,60)
    [Probe]::CapBench($X,$Y,320,240,60)
    [Probe]::CapBench($X,$Y,640,480,40)
    [Probe]::CapBench($rect.X,$rect.Y,$rect.W,$rect.H,15)
  }

  'renderrate' {
    # Hold a camera key so the scene is continuously in motion, then count how
    # many captured frames differ from the one before. Capture runs at ~60 Hz,
    # so the answer is TaleSpire's draw rate as long as it is below that.
    Focus-TS
    [Probe]::SetCursorPos($rect.X + [int]($rect.W/2), $rect.Y + [int]($rect.H*0.4)) | Out-Null
    $vk = [byte][char]'Q'
    $sc = [byte][Probe]::MapVirtualKey($vk,0)
    [Probe]::keybd_event($vk,$sc,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 250
    $r = [Probe]::RenderRate($X,$Y,$RegionW,$RegionH,$Ms)
    [Probe]::keybd_event($vk,$sc,2,[IntPtr]::Zero)
    "captured {0} frames in {1} ms ({2} Hz capture)" -f [Probe]::LastFrames, [int][Probe]::LastElapsed, [int]([Probe]::LastFrames*1000/[Probe]::LastElapsed)
    "of those, {0} differed from the previous frame" -f [Probe]::LastChanged
    "TaleSpire draw rate ~= {0} Hz  (frame period ~{1} ms)" -f [int]$r, [math]::Round(1000/$r,1)
  }

  'watch' {
    [Probe]::StartWatch($X,$Y,$RegionW,$RegionH)
    Start-Sleep -Milliseconds $Ms
    [Probe]::StopWatch()
    $n = [Probe]::Count
    $mx = 0.0
    for ($i=0; $i -lt $n; $i++) { if ([Probe]::Diffs[$i] -gt $mx) { $mx = [Probe]::Diffs[$i] } }
    "{0} samples over {1} ms; peak drift {2}" -f $n, $Ms, [math]::Round($mx,3)
    "(this is the noise floor -- a threshold must sit above it)"
  }

  'latency' {
    Focus-TS
    [Probe]::SetCursorPos($rect.X + [int]($rect.W/2), $rect.Y + [int]($rect.H*0.4)) | Out-Null
    Start-Sleep -Milliseconds 200
    $res = @()
    for ($t = 0; $t -lt $Trials; $t++) {
      $r = Measure-Once $Action $Hold 900
      $res += $r
      "trial {0}: latency {1} ms   peak {2}" -f ($t+1), $(if ($r.Latency -lt 0) { 'NONE' } else { [int]$r.Latency }), $r.Peak
      Start-Sleep -Milliseconds 400
    }
    $ok = $res | Where-Object { $_.Latency -ge 0 }
    if ($ok) {
      $lats = $ok | ForEach-Object { $_.Latency }
      "--- {0}/{1} registered; latency min {2} / median {3} / max {4} ms" -f `
        $ok.Count, $Trials, [int]($lats | Measure-Object -Minimum).Minimum,
        [int](($lats | Sort-Object)[[int]($lats.Count/2)]),
        [int]($lats | Measure-Object -Maximum).Maximum
    } else { "--- 0/{0} registered" -f $Trials }
  }

  'sweep' {
    Focus-TS
    [Probe]::SetCursorPos($rect.X + [int]($rect.W/2), $rect.Y + [int]($rect.H*0.4)) | Out-Null
    Start-Sleep -Milliseconds 200
    "hold_ms  registered  median_latency_ms  peak_diff"
    foreach ($v in ($Values -split ',')) {
      $h = [int]$v.Trim()
      $lats = @(); $hits = 0; $peaks = @()
      for ($t = 0; $t -lt $Trials; $t++) {
        $r = Measure-Once $Action $h 800
        $peaks += $r.Peak
        if ($r.Latency -ge 0) { $hits++; $lats += $r.Latency }
        Start-Sleep -Milliseconds 350
      }
      $med = if ($lats.Count) { [int](($lats | Sort-Object)[[int]($lats.Count/2)]) } else { -1 }
      $pk  = [math]::Round((($peaks | Measure-Object -Average).Average),2)
      "{0,7}  {1,10}  {2,17}  {3,9}" -f $h, "$hits/$Trials", $med, $pk
    }
  }
}
