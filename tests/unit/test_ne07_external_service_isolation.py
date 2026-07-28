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
    assert "if ($ForceRestart.IsPresent -and -not $runtimeRootWasExplicit)" in host
    stop_host = sources["stop_host_side.ps1"]
    assert "if (-not $runtimeRootWasExplicit)" in stop_host


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
Write-RuntimeEnvFile -Path '{output.as_posix()}' -SeedValues @{{
  BOND_NEO4J_AUTH_REF = 'ignored'
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
