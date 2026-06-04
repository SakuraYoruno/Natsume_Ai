param(
  [string]$ProcessName = "MateEngineX",
  [string]$OutputDir = "",
  [int]$KeyThreshold = 8,
  [switch]$SkipComposite
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $repoRoot "runtime\captures"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$basePath = Join-Path $OutputDir "desktop_base_$timestamp.png"
$overlayPath = Join-Path $OutputDir "desktop_pet_overlay_$timestamp.png"
$compositePath = Join-Path $OutputDir "desktop_pet_composite_$timestamp.png"
$metadataPath = Join-Path $OutputDir "desktop_pet_composite_$timestamp.json"

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public struct NatsumePetRect {
  public int Left;
  public int Top;
  public int Right;
  public int Bottom;
}

public sealed class NatsumePetWindowInfo {
  public IntPtr Hwnd;
  public uint Pid;
  public string Title;
  public string ClassName;
  public bool Visible;
  public NatsumePetRect Rect;
  public int ExStyle;
  public int Style;
}

public static class NatsumePetCaptureNative {
  public const int SM_XVIRTUALSCREEN = 76;
  public const int SM_YVIRTUALSCREEN = 77;
  public const int SM_CXVIRTUALSCREEN = 78;
  public const int SM_CYVIRTUALSCREEN = 79;
  public const int SM_CXSCREEN = 0;
  public const int SM_CYSCREEN = 1;
  public const int SRCCOPY = 0x00CC0020;
  public const int CAPTUREBLT = unchecked((int)0x40000000);
  public const int GWL_STYLE = -16;
  public const int GWL_EXSTYLE = -20;
  public const int WS_EX_LAYERED = 0x00080000;
  public const int WS_EX_TRANSPARENT = 0x00000020;
  public const int WS_EX_TOPMOST = 0x00000008;
  public const int WS_EX_TOOLWINDOW = 0x00000080;

  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern int GetClassNameW(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr hWnd, out NatsumePetRect rect);
  [DllImport("user32.dll")] public static extern int GetWindowLongW(IntPtr hWnd, int nIndex);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint flags);
  [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
  [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleBitmap(IntPtr hdc, int width, int height);
  [DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);
  [DllImport("gdi32.dll", SetLastError=true)] public static extern bool BitBlt(IntPtr dst, int xDst, int yDst, int width, int height, IntPtr src, int xSrc, int ySrc, int rop);
  [DllImport("gdi32.dll")] public static extern bool DeleteObject(IntPtr obj);
  [DllImport("gdi32.dll")] public static extern bool DeleteDC(IntPtr hdc);

  public static List<NatsumePetWindowInfo> Enumerate() {
    var rows = new List<NatsumePetWindowInfo>();
    EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
      uint pid;
      GetWindowThreadProcessId(hWnd, out pid);
      var title = new StringBuilder(512);
      var cls = new StringBuilder(256);
      NatsumePetRect rect;
      GetWindowTextW(hWnd, title, title.Capacity);
      GetClassNameW(hWnd, cls, cls.Capacity);
      GetWindowRect(hWnd, out rect);
      rows.Add(new NatsumePetWindowInfo {
        Hwnd = hWnd,
        Pid = pid,
        Title = title.ToString(),
        ClassName = cls.ToString(),
        Visible = IsWindowVisible(hWnd),
        Rect = rect,
        ExStyle = GetWindowLongW(hWnd, GWL_EXSTYLE),
        Style = GetWindowLongW(hWnd, GWL_STYLE)
      });
      return true;
    }, IntPtr.Zero);
    return rows;
  }
}
"@

function Get-ProcessNameById {
  param([int]$ProcessIdValue)
  $proc = Get-Process -Id $ProcessIdValue -ErrorAction SilentlyContinue
  if ($null -eq $proc) { return $null }
  return $proc.ProcessName
}

function Save-VirtualScreen {
  param([string]$Path)

  $left = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_XVIRTUALSCREEN)
  $top = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_YVIRTUALSCREEN)
  $width = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_CXVIRTUALSCREEN)
  $height = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_CYVIRTUALSCREEN)
  if ($width -le 0 -or $height -le 0) {
    $left = 0
    $top = 0
    $width = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_CXSCREEN)
    $height = [NatsumePetCaptureNative]::GetSystemMetrics([NatsumePetCaptureNative]::SM_CYSCREEN)
  }

  $src = [NatsumePetCaptureNative]::GetDC([IntPtr]::Zero)
  $mem = [NatsumePetCaptureNative]::CreateCompatibleDC($src)
  $hBitmap = [NatsumePetCaptureNative]::CreateCompatibleBitmap($src, $width, $height)
  $old = [NatsumePetCaptureNative]::SelectObject($mem, $hBitmap)
  try {
    $rop = [NatsumePetCaptureNative]::SRCCOPY -bor [NatsumePetCaptureNative]::CAPTUREBLT
    $ok = [NatsumePetCaptureNative]::BitBlt($mem, 0, 0, $width, $height, $src, $left, $top, $rop)
    if (-not $ok) {
      $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      throw "BitBlt desktop capture failed, last_error=$lastError"
    }
    $bitmap = [System.Drawing.Image]::FromHbitmap($hBitmap)
    try {
      $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
      $bitmap.Dispose()
    }
  } finally {
    [NatsumePetCaptureNative]::SelectObject($mem, $old) | Out-Null
    [NatsumePetCaptureNative]::DeleteObject($hBitmap) | Out-Null
    [NatsumePetCaptureNative]::DeleteDC($mem) | Out-Null
    [NatsumePetCaptureNative]::ReleaseDC([IntPtr]::Zero, $src) | Out-Null
  }

  return [ordered]@{
    left = $left
    top = $top
    width = $width
    height = $height
  }
}

function Save-WindowPrint {
  param(
    [IntPtr]$Hwnd,
    [string]$Path
  )

  $rect = New-Object NatsumePetRect
  if (-not [NatsumePetCaptureNative]::GetWindowRect($Hwnd, [ref]$rect)) {
    $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "GetWindowRect failed, last_error=$lastError"
  }

  $width = [Math]::Max(1, $rect.Right - $rect.Left)
  $height = [Math]::Max(1, $rect.Bottom - $rect.Top)
  $bitmap = [System.Drawing.Bitmap]::new($width, $height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $hdc = $graphics.GetHdc()
    try {
      $ok = [NatsumePetCaptureNative]::PrintWindow($Hwnd, $hdc, 0x00000002)
      $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    } finally {
      $graphics.ReleaseHdc($hdc)
    }
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }

  return [ordered]@{
    ok = $ok
    last_error = $lastError
    left = $rect.Left
    top = $rect.Top
    right = $rect.Right
    bottom = $rect.Bottom
    width = $width
    height = $height
  }
}

function Compose-KeyedOverlay {
  param(
    [string]$BasePath,
    [string]$OverlayPath,
    [string]$OutPath,
    [int]$OverlayLeft,
    [int]$OverlayTop,
    [int]$Threshold
  )

  $base = [System.Drawing.Bitmap]::FromFile($BasePath)
  $overlay = [System.Drawing.Bitmap]::FromFile($OverlayPath)
  $kept = 0
  $skipped = 0
  $minX = $overlay.Width
  $minY = $overlay.Height
  $maxX = -1
  $maxY = -1
  try {
    for ($y = 0; $y -lt $overlay.Height; $y++) {
      for ($x = 0; $x -lt $overlay.Width; $x++) {
        $targetX = $OverlayLeft + $x
        $targetY = $OverlayTop + $y
        if ($targetX -lt 0 -or $targetY -lt 0 -or $targetX -ge $base.Width -or $targetY -ge $base.Height) {
          $skipped += 1
          continue
        }
        $pixel = $overlay.GetPixel($x, $y)
        $maxChannel = [Math]::Max($pixel.R, [Math]::Max($pixel.G, $pixel.B))
        if ($maxChannel -gt $Threshold) {
          $base.SetPixel($targetX, $targetY, $pixel)
          $kept += 1
          if ($x -lt $minX) { $minX = $x }
          if ($y -lt $minY) { $minY = $y }
          if ($x -gt $maxX) { $maxX = $x }
          if ($y -gt $maxY) { $maxY = $y }
        } else {
          $skipped += 1
        }
      }
    }
    $base.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $overlay.Dispose()
    $base.Dispose()
  }

  $bbox = $null
  if ($maxX -ge 0 -and $maxY -ge 0) {
    $bbox = [ordered]@{
      left = $minX
      top = $minY
      right = $maxX
      bottom = $maxY
      width = ($maxX - $minX + 1)
      height = ($maxY - $minY + 1)
    }
  }
  return [ordered]@{
    kept_pixels = $kept
    skipped_pixels = $skipped
    key_threshold = $Threshold
    overlay_content_bbox = $bbox
  }
}

$allWindows = [NatsumePetCaptureNative]::Enumerate()
$targetPids = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
if ($targetPids.Count -eq 0) {
  throw "Process '$ProcessName' not found."
}

$targetWindow = $allWindows |
  Where-Object {
    $targetPids -contains $_.Pid -and
    $_.Visible -and
    ($_.Rect.Right - $_.Rect.Left) -gt 40 -and
    ($_.Rect.Bottom - $_.Rect.Top) -gt 40
  } |
  Sort-Object @{ Expression = { (($_.ExStyle -band [NatsumePetCaptureNative]::WS_EX_TOPMOST) -ne 0) }; Descending = $true },
              @{ Expression = { (($_.ExStyle -band [NatsumePetCaptureNative]::WS_EX_LAYERED) -ne 0) }; Descending = $true },
              @{ Expression = { ($_.Rect.Right - $_.Rect.Left) * ($_.Rect.Bottom - $_.Rect.Top) }; Descending = $true } |
  Select-Object -First 1

if ($null -eq $targetWindow) {
  throw "Visible window for process '$ProcessName' not found."
}

$fg = [NatsumePetCaptureNative]::GetForegroundWindow()
$fgPidRaw = [uint32]0
[NatsumePetCaptureNative]::GetWindowThreadProcessId($fg, [ref]$fgPidRaw) | Out-Null

$screen = Save-VirtualScreen -Path $basePath
$overlayRect = Save-WindowPrint -Hwnd $targetWindow.Hwnd -Path $overlayPath

$compositeStats = $null
if (-not $SkipComposite.IsPresent) {
  $compositeStats = Compose-KeyedOverlay `
    -BasePath $basePath `
    -OverlayPath $overlayPath `
    -OutPath $compositePath `
    -OverlayLeft $overlayRect.left `
    -OverlayTop $overlayRect.top `
    -Threshold $KeyThreshold
}

$metadata = [ordered]@{
  ok = $true
  method = "desktop BitBlt(CAPTUREBLT) + MateEngine PrintWindow(PW_RENDERFULLCONTENT) + near-black key composite"
  process_name = $ProcessName
  foreground = @{
    hwnd = $fg.ToInt64()
    pid = [int]$fgPidRaw
    process = Get-ProcessNameById -ProcessIdValue ([int]$fgPidRaw)
  }
  files = @{
    base = (Resolve-Path -LiteralPath $basePath).Path
    overlay = (Resolve-Path -LiteralPath $overlayPath).Path
    composite = if ($SkipComposite.IsPresent) { $null } else { (Resolve-Path -LiteralPath $compositePath).Path }
    metadata = $metadataPath
  }
  screen = $screen
  target_window = @{
    hwnd = $targetWindow.Hwnd.ToInt64()
    pid = [int]$targetWindow.Pid
    process = Get-ProcessNameById -ProcessIdValue ([int]$targetWindow.Pid)
    title = $targetWindow.Title
    class = $targetWindow.ClassName
    visible = $targetWindow.Visible
    rect = $overlayRect
    exstyle = ("0x{0:X8}" -f $targetWindow.ExStyle)
    layered = (($targetWindow.ExStyle -band [NatsumePetCaptureNative]::WS_EX_LAYERED) -ne 0)
    transparent = (($targetWindow.ExStyle -band [NatsumePetCaptureNative]::WS_EX_TRANSPARENT) -ne 0)
    topmost = (($targetWindow.ExStyle -band [NatsumePetCaptureNative]::WS_EX_TOPMOST) -ne 0)
    toolwindow = (($targetWindow.ExStyle -band [NatsumePetCaptureNative]::WS_EX_TOOLWINDOW) -ne 0)
  }
  composite = $compositeStats
}

$metadata | ConvertTo-Json -Depth 8 | Set-Content -Path $metadataPath -Encoding utf8
$metadata | ConvertTo-Json -Depth 8
