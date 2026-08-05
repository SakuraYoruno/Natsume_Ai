"""NE-07 external-service isolation and reproducibility contracts."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "scripts/runtime/docker-compose.external.yml"
START_DOCKER = ROOT / "scripts/runtime/start_docker_services.ps1"
START_HOST = ROOT / "scripts/runtime/start_host_side.ps1"
RUNTIME_SCRIPTS = (
    ROOT / "scripts/runtime/start_host_side.ps1",
    ROOT / "scripts/runtime/start_obs_side.ps1",
    ROOT / "scripts/runtime/start_monitor_proxy.ps1",
    ROOT / "scripts/runtime/stop_host_side.ps1",
    ROOT / "scripts/runtime/stop_obs_side.ps1",
    ROOT / "scripts/runtime/stop_monitor_proxy.ps1",
)
START_GATEWAY = ROOT / "scripts/runtime/start_gateway_side.ps1"

SYNC_DUAL_SECRETS = ROOT / "scripts/runtime/sync_dual_runtime_secret_pairs.ps1"


def test_external_images_are_digest_pinned_and_password_has_no_repo_default() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "chromadb/chroma:latest" not in compose
    assert "redis:7-alpine" not in compose
    assert "NatsuNeo4j!" not in compose
    assert compose.count("@sha256:") == 3
    assert 'NATSUME_NEO4J_PASSWORD:-}"' in compose


def test_compose_can_isolate_names_network_and_data_root() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    for variable in (
        "NATSUME_NEO4J_CONTAINER_NAME",
        "NATSUME_CHROMA_CONTAINER_NAME",
        "NATSUME_REDIS_CONTAINER_NAME",
        "NATSUME_DOCKER_NETWORK_NAME",
        "NATSUME_DOCKER_DATA_ROOT",
    ):
        assert variable in compose
    assert compose.count("healthcheck:") == 3
    assert compose.count("natsume-external") >= 4


def test_docker_start_exports_acceptance_scope_without_secret_echo() -> None:
    source = START_DOCKER.read_text(encoding="utf-8")

    for parameter in (
        "[string]$DataRoot",
        "[string]$Neo4jContainerName",
        "[string]$ChromaContainerName",
        "[string]$RedisContainerName",
        "[string]$NetworkName",
    ):
        assert parameter in source
    assert '$env:NATSUME_DOCKER_DATA_ROOT = $DataRoot' in source
    assert "config --quiet" in source
    assert "docker compose config failed" in source
    assert '$neo4jPassword = [string]$env:NATSUME_NEO4J_PASSWORD' in source
    assert "BOND_NEO4J_AUTH_REF=$neo4jAuthRef" not in source
    assert '"RUNTIME_INJECTED_REDACTED"' in source
    assert "auth_value" not in source.lower()


def test_process_environment_overrides_checked_in_env_for_acceptance() -> None:
    docker_source = START_DOCKER.read_text(encoding="utf-8")
    host_source = START_HOST.read_text(encoding="utf-8")

    assert (
        '$neo4jUri = if ($env:BOND_NEO4J_URI)'
        in docker_source
    )
    assert (
        '$chromaEndpoint = if ($env:CHROMA_ENDPOINT)'
        in docker_source
    )
    assert (
        '$redisUrl = if ($env:HOST_RATE_LIMIT_REDIS_URL)'
        in docker_source
    )
    process_lookup = host_source.index(
        '[Environment]::GetEnvironmentVariable($Name, "Process")'
    )
    file_lookup = host_source.index(
        "if ($Values -and $Values.ContainsKey($Name))",
        process_lookup,
    )
    assert process_lookup < file_lookup


def test_host_stack_scripts_accept_an_isolated_runtime_root() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in RUNTIME_SCRIPTS
    }

    for name, source in sources.items():
        assert "[string]$RuntimeRoot" in source, name
    host = sources["start_host_side.ps1"]
    assert "RuntimeRoot = $RuntimeRoot" in host
    assert 'RuntimeRoot = (Join-Path $RuntimeRoot "monitor_proxy")' in host
    assert '-DefaultValue (Join-Path $RuntimeRoot "audit.sqlite3")' in host
    assert '$envValues["NATSUME_AUDIT_DB"] = $auditDbPath' in host
    assert '"--db-path", $auditDbPath' in host
    assert "if ($ForceRestart.IsPresent -and -not $runtimeRootWasExplicit)" in host
    restart_stop = host.index("if ($ForceRestart.IsPresent) {", host.index(
        'Set-ObjectProperty -Target $localServices -Name "external_services_ready"'
    ))
    host_launch = host.index("$proc = Start-HostCommand `", restart_stop)
    restart_source = host[restart_stop:host_launch]
    assert '-RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy")' in restart_source
    assert "-RuntimeRoot $RuntimeRoot" in restart_source
    assert "Get-ListeningPid -ProbePort $MonitorProxyPort" in restart_source
    assert "Get-ListeningPid -ProbePort $obsBindPort" in restart_source
    stop_host = sources["stop_host_side.ps1"]
    assert "if (-not $runtimeRootWasExplicit)" in stop_host


def test_detached_host_uses_a_separate_runtime_secret_file() -> None:
    source = START_HOST.read_text(encoding="utf-8")

    assert '[string]$SecretEnvFile = ""' in source
    assert "HOST_RUNTIME_SECRET_ENV_FILE_NOT_FOUND" in source
    assert "HOST_RUNTIME_SECRET_ENV_FILE_MUST_BE_SEPARATE" in source
    assert "HOST_RUNTIME_ENV_FILE_MUST_BE_SEPARATE_FROM_SOURCE" in source
    assert 'set ""HOST_RUNTIME_SECRET_ENV_FILE=$runtimeSecretEnvFile""' in source
    assert (
        'set ""HOST_RUNTIME_AUXILIARY_SECRET_ENV_FILE='
        '$runtimeAuxiliarySecretEnvFile""' in source
    )
    assert "runtime_secret_env_configured = [bool]$runtimeSecretEnvFile" in source
    assert (
        "runtime_auxiliary_secret_env_configured = "
        "[bool]$runtimeAuxiliarySecretEnvFile" in source
    )
    assert "Ensure-SchedulerClockSecretEnvFile" in source
    assert "host.scheduler-clock.secret.env" in source
    assert "HOST_SCHEDULER_CLOCK_SECRET_ACL_FAILED" in source
    assert "HOST_SCHEDULER_CLOCK_HMAC_SECRET=$secret" in source
    assert "Get-Content -LiteralPath $runtimeSecretEnvFile" not in source
    assert "Read-EnvFile -Path $runtimeSecretEnvFile" not in source


def test_host_acceptance_can_pin_the_canonical_audit_database() -> None:
    source = START_HOST.read_text(encoding="utf-8")

    assert '[string]$AuditDbPath = ""' in source
    assert (
        '$configuredAuditDbPath = if (-not '
        '[string]::IsNullOrWhiteSpace($AuditDbPath))' in source
    )
    assert '[string]$AuditDbPath' in source


def test_remote_gateway_breaks_away_and_records_the_listener_pid() -> None:
    source = START_GATEWAY.read_text(encoding="utf-8")

    assert "[bool]$BreakawayFromSsh = $true" in source
    assert '$useBreakawayLauncher = [bool]($BreakawayFromSsh' in source
    assert '([wmiclass]"Win32_Process").Create(' in source
    assert "[IO.File]::ReadAllLines(" in source
    assert (
        "[Environment]::SetEnvironmentVariable($name, $value, ''Process'')"
        in source
    )
    assert "'$process = Start-Process -FilePath '" in source
    assert "'$process.WaitForExit(); exit $process.ExitCode'" in source
    assert "$gatewayPid = Get-ListeningPid -ProbePort $Port" in source
    assert "Set-Content -LiteralPath $pidFile -Value ([string]$gatewayPid)" in source
    assert "pid = [int]$gatewayPid" in source


def test_dual_runtime_secret_sync_keeps_each_wire_pair_identical() -> None:
    source = SYNC_DUAL_SECRETS.read_text(encoding="utf-8")

    assert '"EDGE_HMAC_SHARED_KEY", "HOST_EDGE_RPC_SHARED_KEY"' in source
    assert (
        '"EDGE_GATEWAY_HOST_HMAC_SECRET", '
        '"HOST_EDGE_GATEWAY_HMAC_SECRET"' in source
    )
    assert (
        '"EDGE_GATEWAY_REPLY_HMAC_SECRET", '
        '"HOST_GATEWAY_REPLY_HMAC_SECRET"' in source
    )
    assert "Write-Output $edgeValue" not in source
    assert "Write-Output $hostValue" not in source
    assert '[string]$SourcePath = ""' in source
    assert "DUAL_RUNTIME_SECRET_SOURCE_FILE_NOT_FOUND" in source
    assert "DUAL_RUNTIME_SECRET_SOURCE_PAIR_MISSING" in source
    assert "$values[$edgeName] = $sourceValue" in source


def test_dual_runtime_secret_sync_can_import_active_edge_pairs(tmp_path: Path) -> None:
    source_path = tmp_path / "edge.env"
    target_path = tmp_path / "host.env"
    source_path.write_text(
        "EDGE_HMAC_SHARED_KEY=" + "a" * 40 + "\n"
        "EDGE_GATEWAY_HOST_HMAC_SECRET=" + "b" * 40 + "\n"
        "EDGE_GATEWAY_REPLY_HMAC_SECRET=" + "c" * 40 + "\n",
        encoding="utf-8",
    )
    target_path.write_text(
        "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET=" + "d" * 40 + "\n"
        "HOST_EDGE_RPC_SHARED_KEY=" + "x" * 40 + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SYNC_DUAL_SECRETS),
            "-Path",
            str(target_path),
            "-SourcePath",
            str(source_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )

    result = json.loads(completed.stdout)
    values = dict(
        line.split("=", 1)
        for line in target_path.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line
    )
    assert result["source_imported"] is True
    assert values["HOST_EDGE_RPC_SHARED_KEY"] == values["EDGE_HMAC_SHARED_KEY"]
    assert (
        values["HOST_EDGE_GATEWAY_HMAC_SECRET"]
        == values["EDGE_GATEWAY_HOST_HMAC_SECRET"]
    )
    assert (
        values["HOST_GATEWAY_REPLY_HMAC_SECRET"]
        == values["EDGE_GATEWAY_REPLY_HMAC_SECRET"]
    )
    assert "a" * 40 not in completed.stdout


def test_host_passes_resolved_obs_endpoint_to_detached_child() -> None:
    source = START_HOST.read_text(encoding="utf-8")
    obs_args_start = source.index("$obsArgs = @{")
    obs_args_end = source.index("}", obs_args_start)
    obs_args = source[obs_args_start:obs_args_end]

    assert "BindHost = $obsBindHost" in obs_args
    assert "Port = $obsBindPort" in obs_args


def test_runtime_snapshot_excludes_secrets_but_keeps_safe_refs(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime.env"
    script = START_HOST.as_posix()
    harness = rf"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  '{script}', [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors | Out-String) }}
foreach ($name in @('Test-SensitiveRuntimeEnvEntry', 'Write-RuntimeEnvFile')) {{
  $definition = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  if ($null -eq $definition) {{ throw "function not found: $name" }}
  . ([ScriptBlock]::Create($definition.Extent.Text))
}}
$env:NATSUME_NEO4J_PASSWORD = 'must-not-be-written'
$env:NATSUME_NEO4J_USER = 'neo4j'
$env:OPENAI_API_KEY = 'must-not-be-written'
$env:EDGE_HMAC_SHARED_KEY = 'must-not-be-written'
$env:BOND_NEO4J_AUTH_REF = 'sec://neo4j/ne07'
$env:NATSUME_AUDIT_DB = 'runtime/acceptance/canonical.sqlite3'
$env:NATSUME_OPERATOR_QUERY_AUTH_BINDINGS = '[{{"token_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","principal_id":"acceptance-operator","scopes":["obs:operator:read"]}}]'
Write-RuntimeEnvFile -Path '{output.as_posix()}' -SeedValues @{{
  BOND_NEO4J_AUTH_REF = 'ignored'
  NATSUME_OPERATOR_QUERY_AUTH_BINDINGS = 'ignored'
}} | Out-Null
[ordered]@{{
  content = [string](Get-Content -Raw -LiteralPath '{output.as_posix()}')
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
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    content = json.loads(completed.stdout.strip())["content"]
    assert "must-not-be-written" not in content
    assert "NATSUME_NEO4J_USER=" not in content
    assert "BOND_NEO4J_AUTH_REF=sec://neo4j/ne07" in content
    assert "NATSUME_AUDIT_DB=runtime/acceptance/canonical.sqlite3" in content
    assert (
        'NATSUME_OPERATOR_QUERY_AUTH_BINDINGS=[{"token_sha256":"aaaaaaaa'
        in content
    )
