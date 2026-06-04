param(
  [string]$HostUrl = "http://127.0.0.1:18080",
  [string]$RepoRoot = "",
  [string]$ProcessName = "MateEngineX",
  [string]$OutputDir = "",
  [int]$KeyThreshold = 8,
  [int]$DelayMs = 350,
  [switch]$SkipCapture
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $RepoRoot "runtime\captures"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$requestPrefix = "desktop_pet_host_verify_$timestamp"
$summaryPath = Join-Path $OutputDir "desktop_pet_host_verify_$timestamp.json"

function Resolve-PythonExe {
  param([string]$Root)

  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($null -ne $pythonCommand) {
    return $pythonCommand.Source
  }
  throw "python not found; expected $venvPython or python on PATH."
}

$pythonExe = Resolve-PythonExe -Root $RepoRoot
$hostUrlValue = $HostUrl.TrimEnd("/")

$pythonScript = @'
import argparse
import json
import time
import urllib.error
import urllib.request


def post_json(url: str, payload: dict, timeout: float = 20.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return {"ok": 200 <= response.status < 300, "status": response.status, "body": parsed}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return {"ok": False, "status": exc.code, "body": parsed}
    except Exception as exc:
        return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-url", required=True)
    parser.add_argument("--request-prefix", required=True)
    parser.add_argument("--delay-ms", type=int, default=350)
    args = parser.parse_args()

    endpoint = args.host_url.rstrip("/") + "/v1/mateengine/action"
    sequence = [
        {
            "action": "reset",
            "text": "Reset local desktop pet state.",
        },
        {
            "action": "state",
            "state": "listening",
            "text": "Natsume host desktop pet is listening.",
        },
        {
            "action": "emote",
            "emotion_tag": "LOVE",
            "intensity": 1.0,
            "text": "Natsume host desktop pet emotion cue.",
        },
        {
            "action": "speak",
            "state": "speaking",
            "text": "Natsume host desktop pet control is active.",
        },
        {
            "action": "move",
            "position_x": 0.85,
            "position_y": 0.0,
            "text": "Move local desktop pet window cue.",
        },
        {
            "action": "move",
            "position_x": 0.0,
            "position_y": 0.0,
            "text": "Return local desktop pet window cue.",
        },
    ]

    rows = []
    for index, payload in enumerate(sequence):
        current = dict(payload)
        current["request_id"] = f"{args.request_prefix}_{index:02d}"
        started = time.time()
        result = post_json(endpoint, current)
        rows.append(
            {
                "index": index,
                "request": current,
                "elapsed_ms": round((time.time() - started) * 1000.0, 2),
                "response": result,
            }
        )
        time.sleep(max(0, args.delay_ms) / 1000.0)

    ok = all(bool(row["response"].get("ok")) for row in rows)
    print(json.dumps({"ok": ok, "endpoint": endpoint, "actions": rows}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
'@

$hostActionRaw = $pythonScript | & $pythonExe - --host-url $hostUrlValue --request-prefix $requestPrefix --delay-ms $DelayMs
$hostExitCode = $LASTEXITCODE
$hostActions = $hostActionRaw | ConvertFrom-Json

$capture = $null
if (-not $SkipCapture.IsPresent) {
  $captureScript = Join-Path $RepoRoot "tools\capture_desktop_pet_composite.ps1"
  if (-not (Test-Path -LiteralPath $captureScript)) {
    throw "Capture script not found: $captureScript"
  }
  $captureRaw = powershell -NoProfile -ExecutionPolicy Bypass -File $captureScript -ProcessName $ProcessName -OutputDir $OutputDir -KeyThreshold $KeyThreshold
  $capture = $captureRaw | ConvertFrom-Json
}

$result = [ordered]@{
  ok = (($hostExitCode -eq 0) -and ($null -eq $capture -or [bool]$capture.ok))
  host_url = $hostUrlValue
  request_prefix = $requestPrefix
  host_actions = $hostActions
  capture = $capture
  files = @{
    summary = $summaryPath
  }
  generated_at = (Get-Date).ToString("o")
}

$result | ConvertTo-Json -Depth 16 | Set-Content -Path $summaryPath -Encoding utf8
$result | ConvertTo-Json -Depth 16
