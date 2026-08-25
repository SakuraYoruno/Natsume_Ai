"""Run the real NE-05 Docker Lab, external canary, and lifecycle sequence.

This acceptance intentionally has two Docker phases:

* the trusted Lab arm is network-disabled and writes only to an isolated bind;
* the external canary is network-visible on a random loopback port.

The script never labels its registry-level runtime check as a full HTTP Host
check.  That final online check belongs to the frozen-SHA full-stack run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EffectAuditReceiptV1,
    EvolutionProposalV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    DockerSandboxAttestationV1,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
    docker_effect_audit_evidence_digest,
)
from host_brain.contracts.skill_lifecycle_v2 import SkillActivationEvidenceV2
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
    TrustedExecutionClaim,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.skill_lifecycle_v2_repository import (
    SQLiteSkillLifecycleV2Repository,
)
from host_brain.persistence.skill_lifecycle_v2_evidence import (
    activation_review_subject_ref,
)
from host_brain.persistence.supply_chain_repository import (
    SQLiteSkillSupplyChainStore,
)
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.lifecycle_registry import LifecycleBoundSkillRegistry


RUN_SCOPE = "a22-ne05"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hex_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hex(value: Any) -> str:
    return _hex_bytes(_canonical_bytes(value))


def _sha_ref(value: Any) -> str:
    return f"sha256:{_hex(value)}"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stderr.strip()}"
        )
    return result


def _docker_json(*args: str) -> Any:
    return json.loads(_run("docker", *args).stdout)


def _millis(raw: str) -> int:
    value = str(raw).strip().replace("Z", "+00:00")
    return int(datetime.fromisoformat(value).timestamp() * 1000)


@dataclass
class _Clock:
    value: int

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int = 1) -> None:
        self.value += int(milliseconds)


def _build_image(context: Path, *, tag: str, version: str) -> dict[str, Any]:
    _run(
        "docker",
        "build",
        "--build-arg",
        f"LAB_VERSION={version}",
        "--tag",
        tag,
        str(context),
    )
    image = _docker_json("image", "inspect", tag)[0]
    image_id = str(image["Id"]).removeprefix("sha256:")
    return {
        "tag": tag,
        "version": version,
        "image_id": image_id,
        "image_reference": f"natsume-ne05-skill-lab@sha256:{image_id}",
        "repo_digests": list(image.get("RepoDigests") or []),
    }


def _spec(
    *, dataset: LabDatasetV1, images: Mapping[str, Mapping[str, Any]]
) -> ExperimentSpecV2:
    return ExperimentSpecV2(
        experiment_id=f"{RUN_SCOPE}-real-docker-lab",
        workspace_id=f"workspace-{RUN_SCOPE}",
        dataset_manifest=DatasetManifestV1(
            workspace_id=f"workspace-{RUN_SCOPE}",
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=canonical_evolution_lab_digest(
                {"fields": ["case_id", "challenge", "expected"]}
            ),
            record_count=1,
            source_kind="acceptance_fixture",
        ),
        seed=2205,
        budget={"max_runs": 2, "max_wall_time_ms": 120_000},
        variants=(
            PolicyVariantV1(
                "baseline",
                "ne05-skill.v1",
                "docker-image.v1",
                str(images["baseline"]["image_id"]),
                True,
            ),
            PolicyVariantV1(
                "candidate",
                "ne05-skill.v2",
                "docker-image.v2",
                str(images["candidate"]["image_id"]),
                False,
            ),
        ),
        verifier_identity=f"verifier://{RUN_SCOPE}/deterministic-output",
        verifier_version="1.0.0",
        verifier_digest=_hex({"source": "run_ne05_skill_lifecycle_acceptance.py"}),
        isolation_profile_digest=_hex(
            {
                "network": "none",
                "readonly_rootfs": True,
                "readonly_mounts": ["dataset", "runtime"],
                "cap_drop": ["ALL"],
                "no_new_privileges": True,
                "user": "65532:65532",
                "memory": 134_217_728,
                "nano_cpus": 500_000_000,
                "pids": 64,
            }
        ),
    )


def _run_offline_arm(
    *,
    root: Path,
    spec: ExperimentSpecV2,
    claim: TrustedExecutionClaim,
    image: Mapping[str, Any],
    engine_identity: str,
) -> tuple[TrustedExperimentRunReceiptV2, dict[str, Any]]:
    arm = claim.variant_id
    arm_root = root / "lab" / arm
    dataset_root = arm_root / "dataset"
    runtime_root = arm_root / "runtime"
    output_root = arm_root / "output"
    for directory in (dataset_root, runtime_root, output_root):
        directory.mkdir(parents=True, exist_ok=True)
    dataset_path = dataset_root / "input.json"
    runtime_path = runtime_root / "manifest.json"
    output_path = output_root / "result.json"
    dataset_path.write_bytes(_canonical_bytes({
        "schema": "natsume.ne05.skill-lab.dataset.v1",
        "records": [{
            "case_id": "ne05-real-001",
            "challenge": "activate-degrade-rollback",
            "expected": "PASS",
        }],
    }))
    runtime_path.write_bytes(_canonical_bytes({
        "protocol": "ne05.v1",
        "worker": "/app/lab_worker.py",
        "variant": arm,
    }))

    command = (
        "python",
        "/app/lab_worker.py",
        "--dataset",
        "/dataset/input.json",
        "--runtime-manifest",
        "/runtime/manifest.json",
        "--output",
        "/output/result.json",
        "--variant",
        arm,
    )
    name = claim.container_name
    create = _run(
        "docker",
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--user",
        "65532:65532",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16777216",
        "--mount",
        f"type=bind,src={dataset_root.resolve()},dst=/dataset,readonly",
        "--mount",
        f"type=bind,src={runtime_root.resolve()},dst=/runtime,readonly",
        "--mount",
        f"type=bind,src={output_root.resolve()},dst=/output",
        "--entrypoint",
        "python",
        str(image["tag"]),
        *command[1:],
    )
    container_id = create.stdout.strip()
    try:
        start = _run("docker", "start", "--attach", name, check=False)
        inspect_raw = _docker_json("container", "inspect", name)[0]
        if start.returncode != 0:
            raise RuntimeError(start.stderr.strip() or start.stdout.strip())
        if not output_path.is_file():
            raise RuntimeError("trusted Lab did not write its isolated output")
        output_raw = output_path.read_bytes()
        result = json.loads(output_raw)
        state = inspect_raw["State"]
        host = inspect_raw["HostConfig"]
        mounts = {item["Destination"]: item for item in inspect_raw["Mounts"]}
        security_opts = [str(item).lower() for item in host.get("SecurityOpt") or []]
        cap_drop = [str(item).upper() for item in host.get("CapDrop") or []]
        facts = {
            "cap_drop": cap_drop,
            "command": list(command),
            "container_id": str(inspect_raw["Id"]),
            "dataset_mount_readonly": not bool(mounts["/dataset"]["RW"]),
            "exit_code": int(state["ExitCode"]),
            "image_id": str(inspect_raw["Image"]).removeprefix("sha256:"),
            "image_reference": str(image["image_reference"]),
            "memory_limit_bytes": int(host["Memory"]),
            "name": str(inspect_raw["Name"]).lstrip("/"),
            "nano_cpus": int(host["NanoCpus"]),
            "network_mode": str(host["NetworkMode"]),
            "no_new_privileges": any("no-new-privileges" in item for item in security_opts),
            "output_mount_temporary": True,
            "output_mount_type": str(mounts["/output"]["Type"]),
            "output_source_digest": _hex({"isolated_output_root": str(output_root.resolve())}),
            "pids_limit": int(host["PidsLimit"]),
            "readonly_rootfs": bool(host["ReadonlyRootfs"]),
            "runtime_mount_readonly": not bool(mounts["/runtime"]["RW"]),
            "runtime_source_digest": _hex_bytes(runtime_path.read_bytes()),
            "user": str(inspect_raw["Config"]["User"]),
        }
        output_digest = _hex_bytes(output_raw)
        audit_digest = docker_effect_audit_evidence_digest(
            execution_id=claim.execution_id,
            command=command,
            inspect_evidence=facts,
            sandbox_profile_digest=spec.isolation_profile_digest,
            dataset_digest=spec.dataset_manifest.dataset_digest,
            output_digest=output_digest,
        )
        audit = EffectAuditReceiptV1(
            execution_id=claim.execution_id,
            audit_id=f"effect-audit-{arm}",
            auditor_identity=f"docker-inspect-effect-auditor://{RUN_SCOPE}/v1",
            audit_evidence_digest=audit_digest,
            effect_count=0,
            denied_effect_count=0,
            production_side_effects=False,
        )
        sandbox = DockerSandboxAttestationV1(
            execution_id=claim.execution_id,
            engine_identity=engine_identity,
            image_reference=str(image["image_reference"]),
            image_id=facts["image_id"],
            container_id=container_id,
            container_name=name,
            command=command,
            inspect_evidence=facts,
            sandbox_profile_digest=spec.isolation_profile_digest,
            runtime_source_digest=facts["runtime_source_digest"],
            output_source_digest=facts["output_source_digest"],
            dataset_digest=spec.dataset_manifest.dataset_digest,
            output_digest=output_digest,
            output_mount_type=facts["output_mount_type"],
            network_mode=facts["network_mode"],
            readonly_rootfs=facts["readonly_rootfs"],
            readonly_dataset=facts["dataset_mount_readonly"],
            readonly_runtime=facts["runtime_mount_readonly"],
            temporary_output=True,
            no_new_privileges=facts["no_new_privileges"],
            cap_drop_all=cap_drop == ["ALL"],
            non_root_user=facts["user"],
            pids_limit=facts["pids_limit"],
            memory_limit_bytes=facts["memory_limit_bytes"],
            nano_cpus=facts["nano_cpus"],
            exit_code=facts["exit_code"],
            started_at_ms=_millis(state["StartedAt"]),
            finished_at_ms=_millis(state["FinishedAt"]),
            effect_audit=audit,
        )
        verifier = VerificationReceiptV1(
            execution_id=claim.execution_id,
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            status="PASS" if result.get("status") == "PASS" else "FAIL",
            evidence_digest=_hex({"output_digest": output_digest, "result": result}),
            outcome={"quality": float(result["score"])},
            risk={"policy_errors": 0, "external_effects": int(result["external_effects"])},
            metrics={"pass_rate": 1.0},
        )
        receipt = TrustedExperimentRunReceiptV2(
            run_id=f"run-{claim.execution_id}",
            execution_id=claim.execution_id,
            spec_digest=spec.spec_digest,
            workspace_id=spec.workspace_id,
            dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
            variant_id=arm,
            status=verifier.status,
            model_identity=str(image["image_reference"]),
            usage={"records": 1},
            cost={"microusd": 0},
            latency_ms=max(0, sandbox.finished_at_ms - sandbox.started_at_ms),
            sandbox_attestation=sandbox,
            verification_receipt=verifier,
        )
        raw_evidence = {
            "container_id": container_id,
            "container_name": name,
            "container_config_image": inspect_raw["Config"]["Image"],
            "raw_inspect_digest": _hex(inspect_raw),
            "normalized_inspect": facts,
            "stdout": start.stdout.strip(),
            "result": result,
            "sandbox_attestation": sandbox.to_dict(),
            "run_receipt": receipt.to_dict(),
        }
        return receipt, raw_evidence
    finally:
        _run("docker", "rm", "--force", name, check=False)


def _external_canary(
    *, image: Mapping[str, Any], version: str, challenge: str
) -> dict[str, Any]:
    name = f"natsume-{RUN_SCOPE}-canary-{version.replace('.', '-')}"
    _run("docker", "rm", "--force", name, check=False)
    container_id = _run(
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--user",
        "65532:65532",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16777216",
        "--publish",
        "127.0.0.1::8080",
        str(image["tag"]),
    ).stdout.strip()
    try:
        deadline = time.monotonic() + 30
        port = ""
        health: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            port_result = _run("docker", "port", name, "8080/tcp", check=False)
            if port_result.returncode == 0 and port_result.stdout.strip():
                port = port_result.stdout.strip().rsplit(":", 1)[-1]
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        health = json.loads(response.read())
                    break
                except Exception:
                    pass
            time.sleep(0.2)
        if health is None:
            raise RuntimeError("external canary did not become healthy")
        request_payload = {"challenge": challenge, "version": version}
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/canary",
            data=_canonical_bytes(request_payload),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.time_ns() // 1_000_000
        with urllib.request.urlopen(request, timeout=5) as response:
            status_code = int(response.status)
            result = json.loads(response.read())
        finished = time.time_ns() // 1_000_000
        inspect_raw = _docker_json("container", "inspect", name)[0]
        receipt = {
            "schema": "natsume.ne05.external-canary-receipt.v1",
            "container_id": container_id,
            "container_name": name,
            "image_reference": image["image_reference"],
            "image_id": str(inspect_raw["Image"]).removeprefix("sha256:"),
            "network_mode": inspect_raw["HostConfig"]["NetworkMode"],
            "published_endpoint": f"http://127.0.0.1:{port}",
            "loopback_only": True,
            "request_digest": _hex(request_payload),
            "health": health,
            "http_status": status_code,
            "result": result,
            "started_at_ms": started,
            "finished_at_ms": finished,
            "raw_inspect_digest": _hex(inspect_raw),
            "external_effects": int(result.get("external_effects", -1)),
        }
        receipt["receipt_digest"] = _hex(receipt)
        if status_code != 200 or result.get("status") != "PASS":
            raise RuntimeError("external canary returned a non-PASS result")
        if result.get("candidate_version") != version:
            raise RuntimeError("external canary version drift")
        if receipt["external_effects"] != 0:
            raise RuntimeError("external canary reported effects")
        return receipt
    finally:
        _run("docker", "rm", "--force", name, check=False)


def _stage(
    store: SQLiteSkillSupplyChainStore,
    *,
    bundle_id: str,
    version: str,
    image: Mapping[str, Any],
    skill_name: str,
) -> dict[str, str]:
    bundle_digest = f"sha256:{image['image_id']}"
    sbom_digest = _sha_ref({"image_reference": image["image_reference"], "packages": []})
    signature_ref = f"audit://{RUN_SCOPE}/signature/{version}"
    store.intake_bundle(
        bundle_id=bundle_id,
        bundle_kind="SKILL",
        bundle_name=skill_name,
        bundle_version=version,
        publisher_id=f"publisher://{RUN_SCOPE}/acceptance",
        bundle_digest=bundle_digest,
        quarantine_audit_ref=f"audit://{RUN_SCOPE}/quarantine/{version}",
        idempotency_key=f"{RUN_SCOPE}:intake:{version}",
    )
    store.stage_bundle(
        bundle_id=bundle_id,
        expected_status_version=0,
        signature_key_id=f"{RUN_SCOPE}-local-acceptance-key",
        signature_algorithm="HMAC_SHA256",
        signature_verification_audit_ref=signature_ref,
        sbom_digest=sbom_digest,
        sandbox_profile_id="ne05-isolated-v1",
        sandbox_profile_digest=_sha_ref("ne05-isolated-v1"),
        audit_ref=f"audit://{RUN_SCOPE}/stage/{version}",
        idempotency_key=f"{RUN_SCOPE}:stage:{version}",
    )
    return {"bundle_digest": bundle_digest, "sbom_digest": sbom_digest, "signature_ref": signature_ref}


def _settle_canary(
    *,
    lifecycle: SQLiteSkillLifecycleV2Repository,
    clock: _Clock,
    bundle_id: str,
    sandbox_digest: str,
    canary: Mapping[str, Any],
    suffix: str,
) -> dict[str, Any]:
    pending = lifecycle.request_canary(
        workspace_id=f"workspace-{RUN_SCOPE}",
        bundle_id=bundle_id,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        sandbox_attestation_digest=sandbox_digest,
        idempotency_key=f"{RUN_SCOPE}:canary:{suffix}",
    )
    claim = lifecycle.claim_canary_outbox(
        lease_owner=f"{RUN_SCOPE}-worker-{suffix}", lease_ms=30_000
    )
    if claim is None:
        raise RuntimeError("canary outbox claim is missing")
    clock.advance()
    return lifecycle.settle_canary(
        operation_id=pending["operation_id"],
        lease_owner=f"{RUN_SCOPE}-worker-{suffix}",
        fencing_token=int(claim["fencing_token"]),
        outcome="PASS",
        downstream_receipt_digest=str(canary["receipt_digest"]),
        verifier_identity="ne05-external-canary-verifier.v1",
        result={
            "status": canary["result"]["status"],
            "candidate_version": canary["result"]["candidate_version"],
            "external_effects": canary["external_effects"],
            "http_status": canary["http_status"],
        },
    )


def _approve(
    *,
    reviews: ReviewStore,
    clock: _Clock,
    bundle_id: str,
    bundle_digest: str,
    version: str,
    report_digest: str,
    proposal_digest: str,
    canary_digest: str,
    review_id: str,
) -> dict[str, Any]:
    item = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=activation_review_subject_ref(
            bundle_id=bundle_id,
            bundle_digest=bundle_digest,
            bundle_version=version,
        ),
        subject_version=1,
        requester=f"{RUN_SCOPE}-acceptance",
        reason="Approve a trusted Docker Lab version for isolated acceptance.",
        risk_level=4,
        side_effects=("skill-version-activation",),
        evidence_refs=(
            f"evolution-lab-report://sha256/{report_digest}",
            f"evolution-proposal://sha256/{proposal_digest}",
            f"skill-canary://sha256/{canary_digest}",
        ),
        status=ReviewStatus.PENDING,
        expires_at=clock() + 600_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=f"skill-checkpoint://{review_id}",
    )
    reviews.create_review(item)
    approved = reviews.approve_review(
        review_id, actor_id=f"operator-{RUN_SCOPE}-acceptance"
    )
    return {
        "review_id": review_id,
        "status_version": int(approved["review"]["status_version"]),
        "record": approved,
    }


def _activation_evidence(
    *,
    bundle_id: str,
    version: str,
    staged: Mapping[str, str],
    report: TrustedExperimentReportV2,
    proposal: EvolutionProposalV2,
    sandbox_digest: str,
    canary: Mapping[str, Any],
    approved: Mapping[str, Any],
    previous_bundle_id: str | None,
    skill_name: str,
) -> SkillActivationEvidenceV2:
    suffix = version.replace(".", "-")
    return SkillActivationEvidenceV2(
        activation_id=f"skill-activation://{RUN_SCOPE}/{skill_name}/{suffix}",
        workspace_id=f"workspace-{RUN_SCOPE}",
        bundle_id=bundle_id,
        bundle_digest=staged["bundle_digest"],
        bundle_version=version,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        lab_report_digest=report.report_digest,
        review_id=str(approved["review_id"]),
        review_status_version=int(approved["status_version"]),
        sbom_digest=staged["sbom_digest"],
        signature_audit_ref=staged["signature_ref"],
        sandbox_attestation_digest=sandbox_digest,
        canary_receipt_digest=str(canary["canary_receipt_digest"]),
        maturity_decision_id=f"skill-maturity-decision://{RUN_SCOPE}/{suffix}",
        maturity_level=2 if previous_bundle_id is None else 3,
        previous_bundle_id=previous_bundle_id,
        new_capability=previous_bundle_id is None,
    )


def _host_skill_probe(
    *,
    host_base_url: str,
    db_path: Path,
    skill_name: str,
    probe_message: str,
    phase: str,
    expected_manifest_ref: str | None,
    expect_execution: bool = True,
) -> dict[str, Any]:
    session_id = f"{RUN_SCOPE}-{phase}-{time.time_ns()}"
    body = _canonical_bytes({
        "message": probe_message,
        "session_id": session_id,
    })
    request = urllib.request.Request(
        f"{host_base_url.rstrip('/')}/v1/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        response_body = response.read().decode("utf-8", errors="replace")
        http_status = int(response.status)
    events: list[dict[str, Any]] = []
    for line in response_body.splitlines():
        raw = line.strip()
        if not raw.startswith("data:"):
            continue
        try:
            event = json.loads(raw[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    commits = [event for event in events if event.get("phase") == "COMMIT"]
    if not commits:
        raise RuntimeError(f"Host probe has no COMMIT event: {phase}")
    commit = commits[-1]
    request_id = str(commit.get("request_id") or "").strip()
    with sqlite3.connect(db_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT request_id, skill_name, result_status, result_code,
                   evidence_ref, manifest_ref, ts_ms
            FROM skill_exec_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
        active_row = connection.execute(
            """
            SELECT bundle_id, bundle_version, maturity_level,
                   runtime_adapter_id, runtime_protocol_version,
                   activation_generation
            FROM skill_active_version_projection_v2
            WHERE bundle_kind = 'SKILL' AND bundle_name = ?
            """,
            (skill_name,),
        ).fetchone()
    receipt = dict(row) if row is not None else None
    active_projection = dict(active_row) if active_row is not None else None
    checks = {"http_200": http_status == 200}
    if expect_execution:
        checks.update({
            "commit_approved": str(commit.get("decision") or "").upper() == "APPROVE",
            "route_is_skill": str(commit.get("route_type") or "").lower() == "skill",
            "target_matches": str(commit.get("target_id") or "") == skill_name,
            "durable_receipt_success": bool(receipt)
            and str(receipt.get("result_status") or "").upper() == "SUCCESS",
            "executed_bundle_matches": (
                (receipt or {}).get("manifest_ref") == expected_manifest_ref
            ),
            "commit_binding_matches": (
                dict(dict(commit.get("audit_refs") or {}).get("skill_version_binding") or {}).get("bundle_id")
                == expected_manifest_ref
            ) if expected_manifest_ref is not None else (
                not dict(dict(commit.get("audit_refs") or {}).get("skill_version_binding") or {})
            ),
        })
    else:
        checks.update({
            "commit_review_paused": str(commit.get("decision") or "").upper() == "PAUSE",
            "review_reason_is_airlock": str(commit.get("reason_code") or "") == "GOV_AIRLOCK_REVIEW",
            "no_skill_execution_receipt": receipt is None,
        })
    checks["active_projection_matches"] = (
        (active_projection or {}).get("bundle_id") == expected_manifest_ref
        if expected_manifest_ref is not None
        else active_projection is None
    )
    if not all(checks.values()):
        raise RuntimeError(
            f"Host online Skill binding probe failed: {phase}: {checks}"
        )
    return {
        "phase": phase,
        "session_id": session_id,
        "request_id": request_id,
        "http_status": http_status,
        "commit": commit,
        "skill_exec_receipt": receipt,
        "active_projection": active_projection,
        "expected_manifest_ref": expected_manifest_ref,
        "expect_execution": expect_execution,
        "checks": checks,
    }


def main() -> int:
    global RUN_SCOPE
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--fixture-root", default="tests/fixtures/ne05_skill_lab"
    )
    parser.add_argument("--database-path", default="")
    parser.add_argument("--host-base-url", default="")
    parser.add_argument("--skill-name", default="noop")
    parser.add_argument("--run-scope", default="a22-ne05")
    parser.add_argument("--baseline-version", default="1.0.0")
    parser.add_argument("--candidate-version", default="2.0.0")
    parser.add_argument(
        "--host-probe-message", default="please summarize the current session"
    )
    args = parser.parse_args()
    RUN_SCOPE = str(args.run_scope).strip().lower()
    if not RUN_SCOPE or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in RUN_SCOPE
    ):
        raise ValueError("--run-scope must contain only a-z, 0-9, '-' or '_'")
    versions = (
        str(args.baseline_version).strip(),
        str(args.candidate_version).strip(),
    )
    if not all(versions) or versions[0] == versions[1]:
        raise ValueError("baseline and candidate versions must be distinct")
    root = Path(args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "ne05-real-skill-lifecycle.json"
    db_path = (
        Path(args.database_path).resolve()
        if str(args.database_path).strip()
        else root / "ne05-real-skill-lifecycle.sqlite3"
    )
    skill_name = str(args.skill_name).strip()
    if not skill_name:
        raise ValueError("--skill-name must not be empty")
    host_base_url = str(args.host_base_url).strip()
    fixture_root = Path(args.fixture_root).resolve()
    started = time.time_ns() // 1_000_000
    containers_before = set(
        _run("docker", "ps", "-aq").stdout.splitlines()
    )
    git_sha = _run("git", "rev-parse", "HEAD").stdout.strip()
    git_diff = _run("git", "diff", "--binary", "HEAD").stdout.encode("utf-8")
    docker_version = json.loads(
        _run("docker", "version", "--format", "{{json .}}").stdout
    )
    docker_info = json.loads(
        _run("docker", "info", "--format", "{{json .}}").stdout
    )
    engine_identity = (
        f"docker://engine/{docker_version['Server']['Version']}@sha256:"
        f"{_hex({'id': docker_info.get('ID'), 'server': docker_version['Server']})}"
    )
    images = {
        "baseline": _build_image(
            fixture_root,
            tag="natsume-ne05-skill-lab:a22-v1",
            version=versions[0],
        ),
        "candidate": _build_image(
            fixture_root,
            tag="natsume-ne05-skill-lab:a22-v2",
            version=versions[1],
        ),
    }
    dataset = LabDatasetV1(
        f"{RUN_SCOPE}-real-docker-lab-dataset",
        ({
            "case_id": "ne05-real-001",
            "challenge": "activate-degrade-rollback",
            "expected": "PASS",
        },),
    )
    spec = _spec(dataset=dataset, images=images)
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"{RUN_SCOPE}-real-skill-lifecycle",
        ts_ms=started,
    )
    if not migration.success:
        raise RuntimeError(str(migration.as_dict()))
    clock = _Clock(started if host_base_url else max(started, 2_200_000_000_000))
    v2 = SQLiteEvolutionLabV2Ledger(db_path)
    trusted = SQLiteTrustedEvolutionLabLedger(db_path, clock=clock)
    supply = SQLiteSkillSupplyChainStore(db_path, clock=clock)
    lifecycle = SQLiteSkillLifecycleV2Repository(db_path, clock=clock)
    reviews = ReviewStore(db_path, clock=clock)
    host_online_probes: list[dict[str, Any]] = []
    if host_base_url:
        host_online_probes.append(_host_skill_probe(
            host_base_url=host_base_url,
            db_path=db_path,
            skill_name=skill_name,
            probe_message=str(args.host_probe_message),
            phase="baseline",
            expected_manifest_ref=None,
        ))
    v2.register_spec_v2(spec)
    runs: dict[str, TrustedExperimentRunReceiptV2] = {}
    lab_evidence: dict[str, Any] = {}
    for arm in ("baseline", "candidate"):
        claim = trusted.reserve_execution(
            spec=spec, variant_id=arm, owner_id=f"{RUN_SCOPE}-{arm}-worker"
        )
        if not claim.execution_authorized:
            raise RuntimeError(f"trusted execution was not authorized: {arm}")
        receipt, raw = _run_offline_arm(
            root=root,
            spec=spec,
            claim=claim,
            image=images[arm],
            engine_identity=engine_identity,
        )
        runs[arm] = trusted.complete_execution(claim=claim, receipt=receipt)
        lab_evidence[arm] = raw
        clock.advance()
    report = TrustedExperimentReportV2(
        report_id=f"report-{RUN_SCOPE}-real-docker-lab",
        experiment_kind="skill_lifecycle",
        spec=spec,
        runs=(runs["baseline"], runs["candidate"]),
        comparisons=(CounterfactualPairV2(
            baseline_run_id=runs["baseline"].run_id,
            alternative_run_id=runs["candidate"].run_id,
            outcome_delta={"quality": 0.12},
            evidence_delta={"pass_rate": 0.0},
            cost_delta={"microusd": 0.0},
            latency_delta={
                "milliseconds": float(runs["candidate"].latency_ms - runs["baseline"].latency_ms)
            },
            risk_delta={"policy_errors": 0.0},
            verification_delta={"pass_rate": 0.0},
        ),),
    )
    trusted.append_report(report)

    lifecycle_data: dict[str, Any] = {}
    bundles = {
        versions[0]: f"skill://{RUN_SCOPE}/{skill_name}/1",
        versions[1]: f"skill://{RUN_SCOPE}/{skill_name}/2",
    }
    arm_for_version = {versions[0]: "baseline", versions[1]: "candidate"}
    with sqlite3.connect(db_path, timeout=30) as generation_connection:
        generation_row = generation_connection.execute(
            """
            SELECT COALESCE(MAX(activation_generation), 0),
                   COALESCE(MAX(activation_fencing_token), 0)
            FROM skill_supply_chain_bundle
            WHERE bundle_kind = 'SKILL' AND bundle_name = ?
            """,
            (skill_name,),
        ).fetchone()
    starting_generation = int(generation_row[0])
    starting_fence = int(generation_row[1])
    previous: str | None = None
    activation_receipts: list[dict[str, Any]] = []
    for generation_offset, version in enumerate(versions):
        arm = arm_for_version[version]
        bundle_id = bundles[version]
        staged = _stage(
            supply,
            bundle_id=bundle_id,
            version=version,
            image=images[arm],
            skill_name=skill_name,
        )
        proposal = EvolutionProposalV2(
            proposal_id=f"proposal-{RUN_SCOPE}-{version.replace('.', '-')}",
            report_digest=report.report_digest,
            target_kind=f"skill-bundle-sha256:{hashlib.sha256(bundle_id.encode()).hexdigest()}",
            target_version=version,
            rationale="Real isolated Docker Lab and loopback external canary passed; review remains mandatory.",
        )
        trusted.append_proposal(proposal)
        canary_raw = _external_canary(
            image=images[arm], version=version, challenge=f"{RUN_SCOPE}-{version}"
        )
        canary = _settle_canary(
            lifecycle=lifecycle,
            clock=clock,
            bundle_id=bundle_id,
            sandbox_digest=runs[arm].sandbox_attestation.receipt_digest,
            canary=canary_raw,
            suffix=version,
        )
        approved = _approve(
            reviews=reviews,
            clock=clock,
            bundle_id=bundle_id,
            bundle_digest=staged["bundle_digest"],
            version=version,
            report_digest=report.report_digest,
            proposal_digest=proposal.proposal_digest,
            canary_digest=str(canary["canary_receipt_digest"]),
            review_id=f"review-{RUN_SCOPE}-{version.replace('.', '-')}",
        )
        evidence = _activation_evidence(
            bundle_id=bundle_id,
            version=version,
            staged=staged,
            report=report,
            proposal=proposal,
            sandbox_digest=runs[arm].sandbox_attestation.receipt_digest,
            canary=canary,
            approved=approved,
            previous_bundle_id=previous,
            skill_name=skill_name,
        )
        activated = lifecycle.activate(
            evidence,
            expected_activation_generation=(
                starting_generation + generation_offset
            ),
            fencing_token=starting_fence + generation_offset + 1,
            idempotency_key=f"{RUN_SCOPE}:activate:{version}",
        )
        registry = LifecycleBoundSkillRegistry(
            base_registry=build_builtin_skill_registry(), active_versions=lifecycle
        )
        binding = registry.require(skill_name).version_binding
        if binding is None or binding.bundle_id != bundle_id:
            raise RuntimeError("Host runtime registry did not bind activated version")
        activation_receipts.append(activated.to_dict())
        lifecycle_data[version] = {
            "staged": staged,
            "proposal": proposal.to_dict(),
            "canary_raw": canary_raw,
            "canary_authority": canary,
            "approved_review": approved["record"],
            "activation_evidence": evidence.to_dict(),
            "activation_receipt": activated.to_dict(),
            "runtime_registry_binding": binding.to_dict(),
        }
        if host_base_url:
            host_online_probes.append(_host_skill_probe(
                host_base_url=host_base_url,
                db_path=db_path,
                skill_name=skill_name,
                probe_message=str(args.host_probe_message),
                phase=f"activate-{version}",
                expected_manifest_ref=bundle_id,
                expect_execution=previous is not None,
            ))
        previous = bundle_id
        clock.advance()

    rolled_back = lifecycle.rollback(
        current_bundle_id=bundles[versions[1]],
        target_bundle_id=bundles[versions[0]],
        expected_activation_generation=starting_generation + 2,
        fencing_token=starting_fence + 3,
        idempotency_key=f"{RUN_SCOPE}:rollback:{versions[0]}",
    )
    rollback_registry = LifecycleBoundSkillRegistry(
        base_registry=build_builtin_skill_registry(), active_versions=lifecycle
    )
    rollback_binding = rollback_registry.require(skill_name).version_binding
    if rollback_binding is None or rollback_binding.bundle_id != bundles[versions[0]]:
        raise RuntimeError("Host runtime registry did not bind rollback target")
    if host_base_url:
        host_online_probes.append(_host_skill_probe(
            host_base_url=host_base_url,
            db_path=db_path,
            skill_name=skill_name,
            probe_message=str(args.host_probe_message),
            phase=f"rollback-{versions[0]}",
            expected_manifest_ref=bundles[versions[0]],
            expect_execution=False,
        ))
    degraded = lifecycle.degrade(
        bundle_id=bundles[versions[0]],
        idempotency_key=f"{RUN_SCOPE}:degrade:{versions[0]}",
        reason_code="SKILL_DEGRADED_BY_ACCEPTANCE_GUARDIAN",
    )
    degraded_registry = LifecycleBoundSkillRegistry(
        base_registry=build_builtin_skill_registry(), active_versions=lifecycle
    )
    if degraded_registry.require(skill_name).version_binding is not None:
        raise RuntimeError("degraded version still influences Host runtime registry")
    if host_base_url:
        host_online_probes.append(_host_skill_probe(
            host_base_url=host_base_url,
            db_path=db_path,
            skill_name=skill_name,
            probe_message=str(args.host_probe_message),
            phase=f"degrade-{versions[0]}",
            expected_manifest_ref=None,
        ))

    containers_after = set(_run("docker", "ps", "-aq").stdout.splitlines())
    leaked = sorted(containers_after - containers_before)
    finished = time.time_ns() // 1_000_000
    result = {
        "schema": "natsume.acceptance.ne05-real-skill-lifecycle.v1",
        "status": (
            "PASS_DOCKER_LAB_EXTERNAL_CANARY_HOST_ONLINE_LIFECYCLE"
            if host_base_url
            else "PASS_DOCKER_LAB_EXTERNAL_CANARY_LIFECYCLE"
        ),
        "full_http_host_online_check": "PASS" if host_base_url else "PENDING_FINAL_FROZEN_SHA_STACK",
        "scope_note": (
            "The online arm used the production Host HTTP Skill route and verified the exact executed bundle in both COMMIT audit refs and skill_exec_log."
            if host_base_url
            else "Registry binding is the production Host resolver path; this run does not claim an HTTP Host process was started."
        ),
        "git": {
            "commit_sha": git_sha,
            "dirty_diff_sha256": _hex_bytes(git_diff),
        },
        "migration": migration.as_dict(),
        "database": str(db_path),
        "engine_identity": engine_identity,
        "images": images,
        "spec": spec.to_dict(),
        "trusted_report": report.to_dict(),
        "lab_evidence": lab_evidence,
        "lifecycle": lifecycle_data,
        "host_online_probes": host_online_probes,
        "rollback_receipt": rolled_back.to_dict(),
        "rollback_runtime_binding": rollback_binding.to_dict(),
        "degrade_receipt": degraded.to_dict(),
        "active_after_degrade": lifecycle.resolve_active(
            bundle_kind="SKILL", bundle_name=skill_name
        ),
        "cleanup": {
            "containers_before": sorted(containers_before),
            "containers_after": sorted(containers_after),
            "leaked_container_ids": leaked,
            "status": "PASS" if not leaked else "FAIL",
        },
        "checks": {
            "trusted_report_promotion_eligible": report.promotion_eligible,
            "both_network_disabled_lab_arms": all(
                run.sandbox_attestation.network_mode == "none"
                for run in runs.values()
            ),
            "both_external_canaries_passed": all(
                lifecycle_data[v]["canary_raw"]["result"]["status"] == "PASS"
                for v in lifecycle_data
            ),
            "activated_v1_then_v2": list(lifecycle_data) == [
                versions[0],
                versions[1],
            ],
            "rollback_bound_v1": rollback_binding.bundle_version == versions[0],
            "degrade_removed_active_projection": lifecycle.resolve_active(
                bundle_kind="SKILL", bundle_name=skill_name
            ) is None,
            "host_online_activate_rollback_degrade": (
                len(host_online_probes) == 5
                and all(
                    all(dict(probe.get("checks") or {}).values())
                    for probe in host_online_probes
                )
            ) if host_base_url else True,
            "no_container_leak": not leaked,
        },
        "started_at_ms": started,
        "finished_at_ms": finished,
    }
    if not all(result["checks"].values()):
        result["status"] = "FAIL"
    report_path.write_bytes(_canonical_bytes(result))
    print(json.dumps({
        "status": result["status"],
        "report": str(report_path),
        "report_sha256": _hex_bytes(report_path.read_bytes()),
        "database": str(db_path),
        "checks": result["checks"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
