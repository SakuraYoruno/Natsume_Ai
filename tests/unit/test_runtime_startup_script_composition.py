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


def test_host_orchestrator_requires_post_sidecar_stability_before_success() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )

    monitor_start = source.index('if ($monitorProxyEnabled) {', source.index("$monitorProxyResult"))
    stability_gate = source.index("$hostStable = Test-HostStartupStability")
    success_result = source.index('$result = [ordered]@{', stability_gate)

    assert monitor_start < stability_gate < success_result
    assert '-RequiredConsecutiveChecks 5' in source[stability_gate:success_result]
    assert '"HOST_STACK_STARTUP_STABILITY_FAILED: host=$hostStable; "' in source
    assert "Stop-ProcessTree -ProcessId $hostPid" in source[stability_gate:success_result]
    assert '& $stopObsScript -EnvFile $EnvFile -RuntimeRoot $RuntimeRoot' in source
    assert (
        '-RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy")'
        in source[stability_gate:success_result]
    )
    assert source.index("Get-SanitizedLogTail", stability_gate) < success_result


def test_host_stability_gate_checks_expected_listener_and_both_payload_identities(
) -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    function_start = source.index("function Test-HostStartupStability")
    function_end = source.index("\nfunction Get-SanitizedLogTail", function_start)
    function_source = source[function_start:function_end]

    for required in (
        "Is-HostProcess -ProcessId $ExpectedPid",
        "Get-ListeningPid -ProbePort $ProbePort",
        "[int]$listeningPid -eq $ExpectedPid",
        '-ExpectedCatalogStage $ExpectedCatalogStage',
        '-Kind "health"',
        '-Kind "readiness"',
        "$consecutive -ge $RequiredConsecutiveChecks",
    ):
        assert required in function_source


def test_host_waits_for_final_catalog_identity_not_an_intermediate_listener() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    identity_start = source.index("function Test-HttpPayloadIdentity")
    identity_end = source.index("\nfunction Wait-HostCatalogStage", identity_start)
    identity_source = source[identity_start:identity_end]
    launch_start = source.index("$healthUrl = ")
    launch_end = source.index("$hostPid = [int]$proc.Id", launch_start)
    launch_source = source[launch_start:launch_end]

    for header in (
        "x-natsume-catalog-stage",
        "x-natsume-catalog-candidate-owner",
        "x-natsume-catalog-operation-digest",
        "x-natsume-catalog-snapshot-digest",
    ):
        assert header in identity_source
    assert '$candidateOwner -ne "host.main"' in identity_source
    assert identity_source.count("sha256:[0-9a-f]{64}") == 2
    assert "$healthOk = Wait-HostCatalogStage `" in launch_source
    assert "-ExpectedCatalogStage $catalogTargetStage" in launch_source
    assert "-ExpectedProcessId ([int]$proc.Id)" in launch_source
    assert "Wait-Http -Url $healthUrl" not in launch_source
    assert "/obs/metrics" not in launch_source
    assert "Get-ListeningPid -ProbePort $Port" not in launch_source


def test_host_catalog_wait_stops_when_launched_process_exits() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    function_start = source.index("function Wait-HostCatalogStage")
    function_end = source.index(
        "\nfunction Test-HostStartupStability",
        function_start,
    )
    function_source = source[function_start:function_end]

    assert "[int]$ExpectedProcessId = 0" in function_source
    assert "Get-Process -Id $ExpectedProcessId" in function_source
    assert function_source.index("Get-Process -Id $ExpectedProcessId") < (
        function_source.index("Test-HttpPayloadIdentity")
    )


def test_forced_host_restart_stops_existing_sidecars_before_new_host() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    restart_stop = source.index("if ($ForceRestart.IsPresent) {", source.index(
        'Set-ObjectProperty -Target $localServices -Name "external_services_ready"'
    ))
    host_launch = source.index("$proc = Start-HostCommand `", restart_stop)
    restart_source = source[restart_stop:host_launch]

    assert '& $stopMonitorScript `' in restart_source
    assert '& $stopObsScript `' in restart_source
    assert "-Port $MonitorProxyPort" in restart_source
    assert "-Port $obsBindPort" in restart_source
    assert "Get-ListeningPid -ProbePort $MonitorProxyPort" in restart_source
    assert "Get-ListeningPid -ProbePort $obsBindPort" in restart_source
    assert restart_source.index("& $stopMonitorScript `") < (
        restart_source.index("& $stopObsScript `")
    )


def test_host_launcher_rejects_workspace_source_overlap_before_creating_root(
    tmp_path: Path,
) -> None:
    source_repository = tmp_path / "source"
    source_repository.mkdir()
    nested_workspace = source_repository / "runtime" / "agent-workspaces"
    parent_workspace = tmp_path
    external_workspace = tmp_path / "acceptance-workspaces"

    host_script = ROOT / "scripts/runtime/start_host_side.ps1"
    harness = rf"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  '{host_script}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$names = @(
  'Ensure-Directory',
  'Get-NormalizedDirectoryPath',
  'Test-DirectoryPathsOverlap',
  'Assert-AgentWorkspaceRootSeparated'
)
foreach ($name in $names) {{
  $definition = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  if ($null -eq $definition) {{ throw "function not found: $name" }}
  . ([ScriptBlock]::Create($definition.Extent.Text))
}}
$source = Get-NormalizedDirectoryPath -Path '{source_repository}' -BasePath '{tmp_path}'
$nested = Get-NormalizedDirectoryPath -Path '{nested_workspace}' -BasePath '{tmp_path}'
$parent = Get-NormalizedDirectoryPath -Path '{parent_workspace}' -BasePath '{tmp_path}'
$external = Get-NormalizedDirectoryPath -Path '{external_workspace}' -BasePath '{tmp_path}'
$nestedError = ""
$parentError = ""
try {{
  Assert-AgentWorkspaceRootSeparated -WorkspaceRoot $nested -SourceRepository $source
  Ensure-Directory -Path $nested
}} catch {{
  $nestedError = $_.Exception.Message
}}
try {{
  Assert-AgentWorkspaceRootSeparated -WorkspaceRoot $parent -SourceRepository $source
}} catch {{
  $parentError = $_.Exception.Message
}}
Assert-AgentWorkspaceRootSeparated -WorkspaceRoot $external -SourceRepository $source
Ensure-Directory -Path $external
[ordered]@{{
  nested_error = $nestedError
  parent_error = $parentError
  nested_created = Test-Path -LiteralPath $nested
  external_created = Test-Path -LiteralPath $external
}} | ConvertTo-Json -Compress
"""
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

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    payload = json.loads(completed.stdout.strip())
    expected = "HOST_AGENT_WORKSPACE_ROOT_OVERLAPS_SOURCE"
    assert expected in payload["nested_error"]
    assert expected in payload["parent_error"]
    assert payload["nested_created"] is False
    assert payload["external_created"] is True


def test_host_launcher_exports_explicit_workspace_root_to_runtime_snapshot() -> None:
    source = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(
        encoding="utf-8"
    )
    preflight = source.index("$configuredAgentWorkspaceRoot = ")
    docker_start = source.index("$startDockerScript = ")
    runtime_snapshot = source.index(
        "$null = Write-RuntimeEnvFile",
        docker_start,
    )

    assert preflight < docker_start < runtime_snapshot
    assert (
        '$env:HOST_AGENT_WORKSPACE_MANAGED_ROOT = $resolvedAgentWorkspaceRoot'
        in source[preflight:docker_start]
    )
    assert (
        '$envValues["HOST_AGENT_WORKSPACE_MANAGED_ROOT"] = '
        "$resolvedAgentWorkspaceRoot"
        in source[preflight:docker_start]
    )
    assert "agent_workspace_root = $resolvedAgentWorkspaceRoot" in source


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
