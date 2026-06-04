param(
  [string]$ProcessName = "MateEngineX",
  [string]$WindowTitle = "",
  [string]$OutputPath = "",
  [switch]$RaiseNoActivate,
  [switch]$EnsureOnScreen
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
  $OutputPath = Join-Path $repoRoot "runtime\captures\mateengine_window.png"
}

$outputDir = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
  New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;

public struct RECT {
  public int Left;
  public int Top;
  public int Right;
  public int Bottom;
}

public static class NatsumeWindowCaptureNative {
  public const int SW_SHOWNOACTIVATE = 4;
  public const int SW_RESTORE = 9;
  public const int SWP_NOSIZE = 0x0001;
  public const int SWP_NOMOVE = 0x0002;
  public const int SWP_NOACTIVATE = 0x0010;
  public const int SWP_SHOWWINDOW = 0x0040;

  [DllImport("user32.dll", SetLastError=true)]
  public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

  [DllImport("user32.dll", SetLastError=true)]
  public static extern bool IsIconic(IntPtr hWnd);

  [DllImport("user32.dll", SetLastError=true)]
  public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

  [DllImport("user32.dll", SetLastError=true)]
  public static extern bool SetWindowPos(
    IntPtr hWnd,
    IntPtr hWndInsertAfter,
    int X,
    int Y,
    int cx,
    int cy,
    uint uFlags
  );

  [DllImport("user32.dll")]
  public static extern int GetSystemMetrics(int nIndex);
}
"@

$candidates = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Where-Object {
  $_.MainWindowHandle -ne 0
})

if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
  $candidates = @($candidates | Where-Object { $_.MainWindowTitle -like "*$WindowTitle*" })
}

$target = $candidates | Select-Object -First 1
if ($null -eq $target) {
  throw "Window not found for process '$ProcessName' title filter '$WindowTitle'."
}

$hwnd = [IntPtr]$target.MainWindowHandle
$restoreAttempted = $false
$raiseAttempted = $false

if ([NatsumeWindowCaptureNative]::IsIconic($hwnd)) {
  $restoreAttempted = $true
  [NatsumeWindowCaptureNative]::ShowWindow($hwnd, [NatsumeWindowCaptureNative]::SW_RESTORE) | Out-Null
  Start-Sleep -Milliseconds 250
} else {
  [NatsumeWindowCaptureNative]::ShowWindow($hwnd, [NatsumeWindowCaptureNative]::SW_SHOWNOACTIVATE) | Out-Null
}

if ($RaiseNoActivate.IsPresent) {
  $raiseAttempted = $true
  $flags = [uint32](
    [NatsumeWindowCaptureNative]::SWP_NOMOVE -bor
    [NatsumeWindowCaptureNative]::SWP_NOSIZE -bor
    [NatsumeWindowCaptureNative]::SWP_NOACTIVATE -bor
    [NatsumeWindowCaptureNative]::SWP_SHOWWINDOW
  )
  [NatsumeWindowCaptureNative]::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, 0, 0, $flags) | Out-Null
  Start-Sleep -Milliseconds 350
}

$rect = New-Object RECT
if (-not [NatsumeWindowCaptureNative]::GetWindowRect($hwnd, [ref]$rect)) {
  $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
  throw "GetWindowRect failed for '$($target.MainWindowTitle)' pid=$($target.Id), last_error=$lastError."
}

$virtualLeft = [NatsumeWindowCaptureNative]::GetSystemMetrics(76)
$virtualTop = [NatsumeWindowCaptureNative]::GetSystemMetrics(77)
$virtualWidth = [NatsumeWindowCaptureNative]::GetSystemMetrics(78)
$virtualHeight = [NatsumeWindowCaptureNative]::GetSystemMetrics(79)
if ($virtualWidth -le 0 -or $virtualHeight -le 0) {
  $virtualLeft = 0
  $virtualTop = 0
  $virtualWidth = [NatsumeWindowCaptureNative]::GetSystemMetrics(0)
  $virtualHeight = [NatsumeWindowCaptureNative]::GetSystemMetrics(1)
}

$ensureOnScreenMoved = $false
if ($EnsureOnScreen.IsPresent) {
  $windowWidth = [Math]::Max(1, $rect.Right - $rect.Left)
  $windowHeight = [Math]::Max(1, $rect.Bottom - $rect.Top)
  $targetLeft = [Math]::Min([Math]::Max($rect.Left, $virtualLeft), $virtualLeft + $virtualWidth - $windowWidth)
  $targetTop = [Math]::Min([Math]::Max($rect.Top, $virtualTop), $virtualTop + $virtualHeight - $windowHeight)
  if ($targetLeft -ne $rect.Left -or $targetTop -ne $rect.Top) {
    $ensureOnScreenMoved = $true
    $flags = [uint32](
      [NatsumeWindowCaptureNative]::SWP_NOACTIVATE -bor
      [NatsumeWindowCaptureNative]::SWP_SHOWWINDOW
    )
    [NatsumeWindowCaptureNative]::SetWindowPos($hwnd, [IntPtr]::Zero, $targetLeft, $targetTop, $windowWidth, $windowHeight, $flags) | Out-Null
    Start-Sleep -Milliseconds 350
    if (-not [NatsumeWindowCaptureNative]::GetWindowRect($hwnd, [ref]$rect)) {
      $lastError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      throw "GetWindowRect after EnsureOnScreen failed for '$($target.MainWindowTitle)' pid=$($target.Id), last_error=$lastError."
    }
  }
}

$captureLeft = [Math]::Max($rect.Left, $virtualLeft)
$captureTop = [Math]::Max($rect.Top, $virtualTop)
$captureRight = [Math]::Min($rect.Right, $virtualLeft + $virtualWidth)
$captureBottom = [Math]::Min($rect.Bottom, $virtualTop + $virtualHeight)
$captureWidth = [Math]::Max(1, $captureRight - $captureLeft)
$captureHeight = [Math]::Max(1, $captureBottom - $captureTop)

$bitmap = New-Object System.Drawing.Bitmap $captureWidth, $captureHeight
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
  $graphics.CopyFromScreen($captureLeft, $captureTop, 0, 0, $bitmap.Size)
  $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
  $graphics.Dispose()
  $bitmap.Dispose()
}

[ordered]@{
  ok = $true
  image = (Resolve-Path -LiteralPath $OutputPath).Path
  process = @{
    id = $target.Id
    name = $target.ProcessName
    title = $target.MainWindowTitle
    hwnd = $hwnd.ToInt64()
  }
  restore_attempted = $restoreAttempted
  raise_no_activate_attempted = $raiseAttempted
  ensure_on_screen_attempted = $EnsureOnScreen.IsPresent
  ensure_on_screen_moved = $ensureOnScreenMoved
  window_rect = @{
    left = $rect.Left
    top = $rect.Top
    right = $rect.Right
    bottom = $rect.Bottom
  }
  capture_rect = @{
    left = $captureLeft
    top = $captureTop
    width = $captureWidth
    height = $captureHeight
  }
  note = "Captured by screen coordinates without SetForegroundWindow."
} | ConvertTo-Json -Depth 6
