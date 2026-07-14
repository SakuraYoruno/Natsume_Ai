from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    (
        "scripts/runtime/start_obs_side.ps1",
        "scripts/runtime/start_monitor_proxy.ps1",
    ),
)
def test_child_startup_scripts_return_control_to_host_orchestrator(
    relative_path: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*return\s*$", source)
    assert not re.search(r"(?m)^\s*exit\s+0\s*$", source)


def test_host_orchestrator_isolates_child_script_process_control() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Invoke-ChildPowerShellScript" in source
    assert "Invoke-ChildPowerShellScript `" in source
    assert "& $startObsScript" not in source
    assert "& $startMonitorScript" not in source
    assert "$output = & powershell.exe" not in source
    assert '([wmiclass]"Win32_Process").Create(' in source
    assert "Set-Content -LiteralPath $stdoutLiteral" in source
    assert "Set-Content -LiteralPath $stderrLiteral" in source
    assert "$childProcess.WaitForExit([int]($TimeoutSeconds * 1000))" in source
    assert "$childProcess.Kill()" in source
    assert 'Join-Path $env:SystemRoot "System32\\taskkill.exe"' in source
    assert "& $taskkillExe /PID $childPid /T /F" in source
    assert "child startup script timed out after $TimeoutSeconds seconds" in source
    assert source.count("WaitSeconds = $WaitSeconds") == 2
    assert source.count("-TimeoutSeconds ([Math]::Max(60, (2 * $WaitSeconds) + 30))") == 1
    assert source.count("-TimeoutSeconds ([Math]::Max(30, $WaitSeconds + 30))") == 1
    assert "Start-Process -Wait" not in source


def test_child_script_capture_does_not_wait_for_long_lived_descendant(
    tmp_path: Path,
) -> None:
    child_script = tmp_path / "spawn-descendant.ps1"
    child_script.write_text(
        """
param([int]$SleepSeconds = 5)
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = (Join-Path $PSHOME "powershell.exe")
$startInfo.Arguments = "-NoProfile -Command `"Start-Sleep -Seconds $SleepSeconds`""
$startInfo.UseShellExecute = $false
$descendant = [System.Diagnostics.Process]::Start($startInfo)
[ordered]@{ status = "started"; pid = [int]$descendant.Id } |
  ConvertTo-Json -Compress
""".strip(),
        encoding="utf-8",
    )

    host_script = ROOT / "scripts/runtime/start_host_side.ps1"
    # Load only the three functions needed by the helper. Dot-sourcing the full
    # startup script would start the real stack and make this unit test unsafe.
    harness = rf"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  '{host_script}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$names = @('ConvertTo-CmdArgument', 'Join-CmdArguments', 'Invoke-ChildPowerShellScript')
foreach ($name in $names) {{
  $definition = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  if ($null -eq $definition) {{ throw "function not found: $name" }}
  . ([ScriptBlock]::Create($definition.Extent.Text))
}}
$raw = Invoke-ChildPowerShellScript `
  -ScriptPath '{child_script}' `
  -NamedArguments @{{ SleepSeconds = 5 }}
$payload = ($raw | Out-String).Trim() | ConvertFrom-Json
Stop-Process -Id ([int]$payload.pid) -Force -ErrorAction SilentlyContinue
$payload | ConvertTo-Json -Compress
"""

    started = time.monotonic()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            harness,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip())["status"] == "started"
    assert elapsed < 3, f"helper waited {elapsed:.2f}s for the descendant"


def test_child_script_timeout_terminates_wrapper_and_descendant_tree(tmp_path: Path) -> None:
    child_script = tmp_path / "hang.ps1"
    descendant_pid_path = tmp_path / "descendant.pid"
    child_script.write_text(
        """
param([string]$DescendantPidPath)
$descendant = Start-Process `
  -FilePath (Join-Path $PSHOME "powershell.exe") `
  -ArgumentList @("-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30") `
  -WindowStyle Hidden `
  -PassThru
Set-Content -LiteralPath $DescendantPidPath -Value ([string]$descendant.Id) -Encoding ASCII
Start-Sleep -Seconds 30
""".strip(),
        encoding="utf-8",
    )

    host_script = ROOT / "scripts/runtime/start_host_side.ps1"
    harness = rf"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  '{host_script}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$names = @('ConvertTo-CmdArgument', 'Join-CmdArguments', 'Invoke-ChildPowerShellScript')
foreach ($name in $names) {{
  $definition = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  if ($null -eq $definition) {{ throw "function not found: $name" }}
  . ([ScriptBlock]::Create($definition.Extent.Text))
}}
$message = ""
try {{
  Invoke-ChildPowerShellScript `
    -ScriptPath '{child_script}' `
    -NamedArguments @{{ DescendantPidPath = '{descendant_pid_path}' }} `
    -TimeoutSeconds 1
}} catch {{
  $message = $_.Exception.Message
}}
Start-Sleep -Milliseconds 500
$descendantPid = [int](Get-Content -LiteralPath '{descendant_pid_path}' -Raw -Encoding ASCII)
$descendantAlive = $null -ne (Get-Process -Id $descendantPid -ErrorAction SilentlyContinue)
[ordered]@{{
  message = $message
  descendant_pid = $descendantPid
  descendant_alive = $descendantAlive
}} | ConvertTo-Json -Compress
if ($descendantAlive) {{ exit 19 }}
if (-not $message) {{ exit 20 }}
exit 17
"""

    started = time.monotonic()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            harness,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    elapsed = time.monotonic() - started
    output = f"{completed.stdout}\n{completed.stderr}"
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert completed.returncode == 17, output
    assert "child startup script timed out after 1 seconds" in output
    assert "cleanup=process_tree_terminated" in output
    assert payload["descendant_alive"] is False
    assert elapsed < 5, f"timeout helper returned after {elapsed:.2f}s"


def test_monitor_proxy_never_shared_caches_operator_namespaces() -> None:
    source = (ROOT / "scripts/runtime/start_monitor_proxy.ps1").read_text(
        encoding="utf-8"
    )

    for location in ("location /obs/ {", "location /api/ui/ {"):
        start = source.index(location)
        block = source[start : source.index("\n        }", start)]
        assert "proxy_cache off;" in block
        assert "proxy_cache obs_microcache;" not in block
        assert "X-Natsume-Obs-Cache BYPASS" in block


def test_monitor_proxy_preserves_original_host_port_for_bff_csrf() -> None:
    source = (ROOT / "scripts/runtime/start_monitor_proxy.ps1").read_text(
        encoding="utf-8"
    )

    assert "proxy_set_header Host `$host;" not in source
    assert source.count("proxy_set_header Host `$http_host;") == 5
