"""Fixed-worktree runner support for the real A15 Code benefit cohort.

The acceptance adapter keeps model output process-local, applies one bounded
full-file replacement in a disposable detached worktree and persists only
model, diff, hidden-test, static-check and cleanup evidence.  Both arms come
from the common Code PlanningSearch domain; this module has no production
promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from host_brain.contracts.planning_search.code_benefit_cohort import (
    CodeArmBenefitEvidenceV1,
    CodeCohortPairedEvidenceV1,
)
from host_brain.model_runtime.live_prompts import (
    CODE_BENEFIT_EXECUTION_PROMPT_ID,
    CODE_BENEFIT_GENERATION_PARAMETERS,
)
from host_brain.orchestration.goal_planning import GoalPlanningRequest, GoalPlanningService
from host_brain.persistence.code_benchmark_ledger_repository import (
    SQLiteCodeHistoricalBenchmarkLedger,
)
from host_brain.planning_search import BenchmarkArm, DomainBenchmarkWorkload
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
    AttestedExternalResearchRun,
)
from host_brain.planning_search.code_benchmark_experiment import (
    ControlledCodeBenchmarkExperimentService,
)
from host_brain.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkPathPolicy,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeBenchmarkWorkspace,
    CodeHistoricalBenchmarkSandbox,
    PrivateBareBenchmarkSource,
)
from host_brain.planning_search.code_benchmark_verification_contracts import (
    CodeBenchmarkControlledVerificationReceipt,
    CodeBenchmarkVerificationCommand,
    CodeBenchmarkVerificationPlan,
)
from host_brain.planning_search.code_benchmark_verifier import (
    ControlledCodeBenchmarkVerifier,
)
from host_brain.planning_search.code_domain import CodePlanningDomain
from host_brain.planning_search.code_domain_models import (
    CODE_DOMAIN,
    CodePatchSpec,
    CodeTaskSnapshot,
)
from host_brain.planning_search.contracts import MCTSMode, PlanningGoal, SearchBudget


FIXED_CODE_COHORT_SCHEMA = "natsume.code-benefit-fixed-cohort.v1"
CODE_EXECUTION_PROFILE_ID = "a15-code-full-file-json-v2"
CODE_BENCHMARK_SCENE_TYPE = "CODE_BENCHMARK"
DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS = 30_000
CODE_BENCHMARK_REQUEST_DEADLINE_MS = 90_000
CODE_HIDDEN_VERIFIER_PROFILE_DIGEST = hashlib.sha256(
    b"natsume.a15.code-hidden-pytest.v1"
).hexdigest()
CODE_STATIC_VERIFIER_PROFILE_DIGEST = hashlib.sha256(
    b"natsume.a15.code-static-pytest.v1"
).hexdigest()

_MODEL_SYSTEM = """
你是隔离 Code benchmark 的单文件修复器。只依据调用方给出的当前源码、任务目标和本轮 PlanningSearch 选中的需求证据修改代码。
输出只能是一个 JSON 对象，且只能包含 content 字段；content 必须是目标 Python 文件的完整 UTF-8 源码。
文件必须只定义 solve(value)，不得读取文件、网络、环境变量，不得使用 eval、exec、open 或动态导入。
不要输出 Markdown、补丁、解释、路径或额外字段。不能确定时也必须返回语法有效的最小实现。
""".strip()


class CodeBenefitExperimentError(RuntimeError):
    """The fixed Code queue or a paired real execution failed closed."""


@dataclass(frozen=True, slots=True)
class FixedCodeCaseV1:
    input_value: Any
    expected_value: Any


@dataclass(frozen=True, slots=True)
class FixedCodeWorkloadV1:
    workload_id: str
    module_name: str
    objective: str
    baseline_evidence: str
    selected_evidence: str
    cases: tuple[FixedCodeCaseV1, ...]

    @property
    def relative_module_path(self) -> str:
        return f"src/{self.module_name}.py"


@dataclass(frozen=True, slots=True)
class FixedCodeCohortV1:
    config_path: Path
    config_digest: str
    cohort_id: str
    cohort_version: str
    planning_algorithm_version: str
    source_epoch: str
    workloads: tuple[FixedCodeWorkloadV1, ...]


@dataclass(frozen=True, slots=True)
class CodeBenchmarkRuntimeRootsV1:
    source_repository: Path
    source_commit: str
    private_bare_root: Path
    worktree_root: Path
    source_ref: str


@dataclass(frozen=True, slots=True)
class PreparedCodeWorkloadV1:
    definition: FixedCodeWorkloadV1
    snapshot: CodeTaskSnapshot
    path_policy: CodeBenchmarkPathPolicy
    verification_plan: CodeBenchmarkVerificationPlan
    execution_budget: CodeBenchmarkExecutionBudget
    workload: DomainBenchmarkWorkload
    registration: CodeBenchmarkWorkloadRegistration
    run_spec: CodeBenchmarkRunSpec
    baseline_evidence_text: str
    selected_evidence_text: str
    original_source: str
    selected_mcts_used: bool


@dataclass(frozen=True, slots=True)
class CodeArmModelOutputV1:
    role: str
    run: AttestedExternalResearchRun
    replacement_source: str
    parsed: bool


class _PreparedPatchExecutor:
    def __init__(
        self,
        *,
        relative_path: str,
        original_source: str,
        replacement_source: str,
        parsed: bool,
    ) -> None:
        self._relative_path = relative_path
        self._original_source = original_source
        self._replacement_source = replacement_source
        self._parsed = parsed

    def execute(
        self,
        *,
        workspace: CodeBenchmarkWorkspace,
        arm: BenchmarkArm,
    ) -> Mapping[str, int]:
        del arm
        replacement = self._replacement_source
        if not self._parsed:
            replacement = self._original_source.rstrip() + "\n# invalid model output\n"
        workspace.write_text(self._relative_path, replacement)
        return {}


def load_fixed_code_cohort(config_path: str | Path) -> FixedCodeCohortV1:
    path = Path(config_path).expanduser().resolve()
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CodeBenefitExperimentError("fixed Code cohort cannot be loaded") from exc
    if not isinstance(payload, dict) or payload.get("schema") != FIXED_CODE_COHORT_SCHEMA:
        raise CodeBenefitExperimentError("fixed Code cohort schema drifted")
    raw_workloads = payload.get("workloads")
    if not isinstance(raw_workloads, list) or len(raw_workloads) != 20:
        raise CodeBenefitExperimentError("fixed Code cohort requires exactly 20 workloads")
    workloads = tuple(_workload_from_dict(item) for item in raw_workloads)
    ids = tuple(item.workload_id for item in workloads)
    modules = tuple(item.module_name for item in workloads)
    if ids != tuple(sorted(ids)) or len(set(ids)) != 20 or len(set(modules)) != 20:
        raise CodeBenefitExperimentError("fixed Code workload identities must be unique and sorted")
    return FixedCodeCohortV1(
        config_path=path,
        config_digest=hashlib.sha256(raw_bytes).hexdigest(),
        cohort_id=_text(payload.get("cohort_id"), "cohort_id", 160),
        cohort_version=_text(payload.get("cohort_version"), "cohort_version", 80),
        planning_algorithm_version=_text(
            payload.get("planning_algorithm_version"),
            "planning_algorithm_version",
            120,
        ),
        source_epoch=_text(payload.get("source_epoch"), "source_epoch", 80),
        workloads=workloads,
    )


def create_fixed_code_source_repository(
    *,
    cohort: FixedCodeCohortV1,
    runtime_root: str | Path,
) -> CodeBenchmarkRuntimeRootsV1:
    root = Path(runtime_root).expanduser().resolve()
    if root.exists():
        raise CodeBenefitExperimentError("Code benchmark runtime root already exists")
    source = root / "source"
    private_bares = root / "private-bares"
    worktrees = root / "worktrees"
    source.mkdir(parents=True)
    private_bares.mkdir()
    worktrees.mkdir()
    (source / "src").mkdir()
    (source / "tests_hidden").mkdir()
    (source / "tests_static").mkdir()
    (source / "src" / "__init__.py").write_text("", encoding="utf-8")
    (source / "tests_hidden" / "test_tasks.py").write_text(
        _hidden_test_source(cohort),
        encoding="utf-8",
        newline="",
    )
    (source / "tests_static" / "test_tasks_static.py").write_text(
        _static_test_source(cohort),
        encoding="utf-8",
        newline="",
    )
    for workload in cohort.workloads:
        (source / workload.relative_module_path).write_text(
            _initial_source(),
            encoding="utf-8",
            newline="",
        )
    _git(source, "init")
    _git(source, "config", "user.email", "a15-code@example.invalid")
    _git(source, "config", "user.name", "A15 Code Cohort")
    _git(source, "config", "core.autocrlf", "false")
    _git(source, "add", ".")
    _git(
        source,
        "commit",
        "-m",
        "冻结 A15 Code 20 题源码",
        env={
            "GIT_AUTHOR_DATE": cohort.source_epoch,
            "GIT_COMMITTER_DATE": cohort.source_epoch,
        },
    )
    if _git(source, "status", "--porcelain"):
        raise CodeBenefitExperimentError("generated Code source repository is not clean")
    commit = _git(source, "rev-parse", "HEAD").lower()
    return CodeBenchmarkRuntimeRootsV1(
        source_repository=source,
        source_commit=commit,
        private_bare_root=private_bares,
        worktree_root=worktrees,
        source_ref=f"snapshot://a15/code/{cohort.config_digest}",
    )


def prepare_code_workload(
    *,
    cohort: FixedCodeCohortV1,
    definition: FixedCodeWorkloadV1,
    runtime: CodeBenchmarkRuntimeRootsV1,
    run_namespace: str,
) -> PreparedCodeWorkloadV1:
    original_source = (runtime.source_repository / definition.relative_module_path).read_text(
        encoding="utf-8"
    )
    artifact_ref = f"artifact://a15/code/{definition.module_name}"
    test_ref = f"test://a15/code/{definition.module_name}"
    snapshot = CodeTaskSnapshot(
        task_ref=f"task://a15/code/{definition.workload_id}",
        workspace_snapshot_ref=runtime.source_ref,
        workspace_digest=hashlib.sha256(runtime.source_commit.encode("ascii")).hexdigest(),
        objective_ref=f"objective://a15/code/{definition.workload_id}",
        failing_test_ids=(test_ref,),
        target_artifact_digests={
            artifact_ref: hashlib.sha256(original_source.encode("utf-8")).hexdigest()
        },
        allowed_path_refs=(f"pathref://a15/code/{definition.module_name}",),
        forbidden_path_refs=(),
        max_changed_files=1,
        verification_profile_ref=f"verification://a15/code/{definition.module_name}",
        patch_catalog=(
            CodePatchSpec(
                patch_id="risky",
                patch_ref=f"patch://a15/code/{definition.module_name}/risky",
                affected_artifact_refs=(artifact_ref,),
                test_ids=(test_ref,),
                estimated_pass_probability=0.45,
                estimated_risk=0.65,
                estimated_cost=0.10,
            ),
            CodePatchSpec(
                patch_id="safe",
                patch_ref=f"patch://a15/code/{definition.module_name}/safe",
                affected_artifact_refs=(artifact_ref,),
                test_ids=(test_ref,),
                estimated_pass_probability=0.95,
                estimated_risk=0.10,
                estimated_cost=0.20,
            ),
        ),
        source_refs=(f"evidence://a15/code/{definition.module_name}",),
        virtual_model_ready=True,
    )
    baseline_result = _search(snapshot=snapshot, definition=definition, mode=MCTSMode.NEVER)
    selected_result = _search(snapshot=snapshot, definition=definition, mode=MCTSMode.AUTO)
    selected_ids = tuple(item.candidate_id for item in selected_result.selected_path)
    if not selected_result.mcts_used or "code.patch.safe" not in selected_ids:
        raise CodeBenefitExperimentError("Code search did not select the safe L3 patch path")
    baseline_ids = tuple(item.candidate_id for item in baseline_result.selected_path)
    if "code.patch.safe" in baseline_ids:
        raise CodeBenefitExperimentError("Code baseline unexpectedly used the L3 safe patch")
    baseline = BenchmarkArm.from_search_result(
        f"baseline-{definition.module_name}", baseline_result
    )
    selected = BenchmarkArm.from_search_result(
        f"selected-{definition.module_name}", selected_result
    )
    path_policy = CodeBenchmarkPathPolicy(
        allowed_paths=(definition.relative_module_path,),
        max_changed_files=1,
    )
    verification_plan = CodeBenchmarkVerificationPlan(
        commands=(
            CodeBenchmarkVerificationCommand(
                command_id="hidden",
                argv=(
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    f"tests_hidden/test_tasks.py::test_{definition.module_name}",
                ),
                timeout_seconds=30,
            ),
            CodeBenchmarkVerificationCommand(
                command_id="static",
                argv=(
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    f"tests_static/test_tasks_static.py::test_{definition.module_name}_static",
                ),
                timeout_seconds=30,
            ),
        )
    )
    execution_budget = CodeBenchmarkExecutionBudget(
        max_model_calls=2,
        max_provider_attempts=2,
        max_tool_calls=0,
        max_test_commands=4,
        max_wall_time_ms=120_000,
        max_token_count=3_200,
        max_model_cost_micros=1_000_000,
    )
    workload = DomainBenchmarkWorkload(
        workload_id=definition.workload_id,
        domain=CODE_DOMAIN,
        workload_ref=f"benchmark://a15/code/{definition.workload_id}",
        baseline_ref=f"benchmark://a15/code/{definition.workload_id}/baseline",
        verifier_refs=(verification_plan.plan_ref,),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": runtime.source_ref,
            "source_commit": runtime.source_commit,
            "path_policy_digest": path_policy.digest,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=_search_budget(),
        cohort_key=f"{cohort.cohort_id}:{cohort.cohort_version}",
    )
    registration = CodeBenchmarkWorkloadRegistration(
        workload=workload,
        execution_budget=execution_budget,
        controlled_verification_plan=verification_plan,
    )
    run_id = f"code-{run_namespace}-{definition.module_name}"[:120]
    return PreparedCodeWorkloadV1(
        definition=definition,
        snapshot=snapshot,
        path_policy=path_policy,
        verification_plan=verification_plan,
        execution_budget=execution_budget,
        workload=workload,
        registration=registration,
        run_spec=CodeBenchmarkRunSpec(
            run_id=run_id,
            registration=registration,
            baseline=baseline,
            selected=selected,
        ),
        baseline_evidence_text=definition.baseline_evidence,
        selected_evidence_text=definition.selected_evidence,
        original_source=original_source,
        selected_mcts_used=True,
    )


async def execute_prepared_code_workload(
    *,
    prepared: PreparedCodeWorkloadV1,
    runtime: CodeBenchmarkRuntimeRootsV1,
    ledger: SQLiteCodeHistoricalBenchmarkLedger,
    external_adapter: AttestedExternalResearchAdapter,
    arm_order: Sequence[str],
    execution_namespace: str,
    model_timeout_ms: int = DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS,
) -> CodeCohortPairedEvidenceV1:
    order = tuple(str(item or "").strip().upper() for item in arm_order)
    if len(order) != 2 or set(order) != {"BASELINE", "SELECTED"}:
        raise CodeBenefitExperimentError("Code arm order is invalid")
    stored_registration = ledger.register_workload(prepared.registration)
    stored_spec = ledger.preregister_run(
        CodeBenchmarkRunSpec(
            run_id=prepared.run_spec.run_id,
            registration=stored_registration,
            baseline=prepared.run_spec.baseline,
            selected=prepared.run_spec.selected,
        )
    )
    if ledger.get_completed(stored_spec.run_id) is not None:
        raise CodeBenefitExperimentError(
            "terminal Code replay has no private model output; use a new run namespace"
        )
    plans = {
        "BASELINE": (
            prepared.run_spec.baseline,
            prepared.baseline_evidence_text,
        ),
        "SELECTED": (
            prepared.run_spec.selected,
            prepared.selected_evidence_text,
        ),
    }
    model_outputs: dict[str, CodeArmModelOutputV1] = {}
    for role in order:
        arm, evidence_text = plans[role]
        execution_id = (
            f"a15-code-{execution_namespace}-{prepared.definition.module_name}-{role.lower()}"
        )[:160]
        run = await external_adapter.execute(
            execution_id=execution_id,
            arm_id=arm.arm_id,
            prompt_id=CODE_BENEFIT_EXECUTION_PROMPT_ID,
            messages=_execution_messages(
                prepared=prepared,
                arm=arm,
                evidence_text=evidence_text,
            ),
            deadline_ms=CODE_BENCHMARK_REQUEST_DEADLINE_MS,
            cognitive_tier="RESEARCH",
            scene_type=CODE_BENCHMARK_SCENE_TYPE,
        )
        replacement, parsed = _parse_model_source(run.output_text)
        model_outputs[role] = CodeArmModelOutputV1(
            role=role,
            run=run,
            replacement_source=replacement,
            parsed=parsed,
        )
    sandbox = CodeHistoricalBenchmarkSandbox(
        source=PrivateBareBenchmarkSource(
            source_repository=runtime.source_repository,
            private_bare_root=runtime.private_bare_root,
            worktree_root=runtime.worktree_root,
            protected_roots=(runtime.source_repository,),
            source_ref=runtime.source_ref,
            source_commit=runtime.source_commit,
        )
    )
    verifier = ControlledCodeBenchmarkVerifier(
        plan=prepared.verification_plan,
        execution_budget=prepared.execution_budget,
    )
    receipt = ControlledCodeBenchmarkExperimentService(
        sandbox=sandbox,
        ledger=ledger,
    ).run_and_record(
        registration=prepared.registration,
        spec=stored_spec,
        path_policy=prepared.path_policy,
        baseline_executor=_executor(prepared, model_outputs["BASELINE"]),
        selected_executor=_executor(prepared, model_outputs["SELECTED"]),
        verifier=verifier,
        arm_order=order,
    )
    terminal_digest = _digest_json(receipt.to_dict())
    return CodeCohortPairedEvidenceV1(
        workload_id=prepared.definition.workload_id,
        run_id=receipt.spec.run_id,
        terminal_receipt_digest=terminal_digest,
        terminal_receipt=receipt,
        baseline=_benefit_evidence(
            role="BASELINE",
            receipt=receipt,
            model_output=model_outputs["BASELINE"],
            execution_profile_digest=code_execution_profile_digest(
                model_timeout_ms=model_timeout_ms
            ),
        ),
        selected=_benefit_evidence(
            role="SELECTED",
            receipt=receipt,
            model_output=model_outputs["SELECTED"],
            execution_profile_digest=code_execution_profile_digest(
                model_timeout_ms=model_timeout_ms
            ),
        ),
        owner_randomized_run=True,
        independent_fresh_worktrees=True,
    )


def sanitized_code_member_summary(
    evidence: CodeCohortPairedEvidenceV1,
    *,
    arm_order: Sequence[str],
) -> dict[str, Any]:
    return {
        "workload_id": evidence.workload_id,
        "run_id": evidence.run_id,
        "terminal_receipt_digest": evidence.terminal_receipt_digest,
        "arm_order": list(arm_order),
        "baseline": _arm_summary(evidence.baseline),
        "selected": _arm_summary(evidence.selected),
    }


def code_execution_profile_digest(
    *,
    model_timeout_ms: int = DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS,
) -> str:
    normalized_timeout_ms = int(model_timeout_ms)
    if not 10_000 <= normalized_timeout_ms <= CODE_BENCHMARK_REQUEST_DEADLINE_MS:
        raise CodeBenefitExperimentError("Code model timeout is outside the frozen range")
    return _digest_json(
        {
            "profile_id": CODE_EXECUTION_PROFILE_ID,
            "prompt_id": CODE_BENEFIT_EXECUTION_PROMPT_ID,
            "scene_type": CODE_BENCHMARK_SCENE_TYPE,
            "model_timeout_ms": normalized_timeout_ms,
            "request_deadline_ms": CODE_BENCHMARK_REQUEST_DEADLINE_MS,
            "generation_parameters": dict(CODE_BENEFIT_GENERATION_PARAMETERS),
            "system": _MODEL_SYSTEM,
            "output": {"content": "complete Python source"},
            "cache_policy": "NONE",
        }
    )


def _executor(
    prepared: PreparedCodeWorkloadV1,
    output: CodeArmModelOutputV1,
) -> _PreparedPatchExecutor:
    return _PreparedPatchExecutor(
        relative_path=prepared.definition.relative_module_path,
        original_source=prepared.original_source,
        replacement_source=output.replacement_source,
        parsed=output.parsed,
    )


def _benefit_evidence(
    *,
    role: str,
    receipt: Any,
    model_output: CodeArmModelOutputV1,
    execution_profile_digest: str,
) -> CodeArmBenefitEvidenceV1:
    arm_evidence = (
        receipt.run.baseline_evidence if role == "BASELINE" else receipt.run.selected_evidence
    )
    controlled: CodeBenchmarkControlledVerificationReceipt | None = (
        arm_evidence.verifier.controlled_receipt
    )
    if controlled is None:
        raise CodeBenefitExperimentError("Code arm lacks controlled verification evidence")
    by_id = {item.command_id: item for item in controlled.commands}
    if set(by_id) != {"hidden", "static"}:
        raise CodeBenefitExperimentError("Code verifier command identity drifted")
    hidden_digest = _digest_json(
        {
            "run_id": receipt.spec.run_id,
            "arm_id": arm_evidence.arm_id,
            "command": by_id["hidden"].to_dict(),
        }
    )
    static_digest = _digest_json(
        {
            "run_id": receipt.spec.run_id,
            "arm_id": arm_evidence.arm_id,
            "command": by_id["static"].to_dict(),
        }
    )
    run = model_output.run
    return CodeArmBenefitEvidenceV1(
        run_id=receipt.spec.run_id,
        arm_role=role,
        arm_id=arm_evidence.arm_id,
        model_execution_id=run.execution_id,
        model_attempt_ids=run.model_receipt.attempt_ids,
        model_output_digest=run.attestation.response_digest,
        execution_profile_digest=execution_profile_digest,
        hidden_verifier_execution_id=(
            f"hidden-{receipt.spec.run_id}-{role.lower()}"
        )[:160],
        hidden_verifier_profile_digest=CODE_HIDDEN_VERIFIER_PROFILE_DIGEST,
        hidden_test_receipt_ref=f"code-hidden-test://sha256/{hidden_digest}",
        hidden_test_receipt_digest=hidden_digest,
        hidden_test_passed=by_id["hidden"].status == "PASS",
        static_verifier_execution_id=(
            f"static-{receipt.spec.run_id}-{role.lower()}"
        )[:160],
        static_verifier_profile_digest=CODE_STATIC_VERIFIER_PROFILE_DIGEST,
        static_check_receipt_ref=f"code-static-check://sha256/{static_digest}",
        static_check_receipt_digest=static_digest,
        static_check_passed=by_id["static"].status == "PASS",
        diff_risk_score=0.10,
        verification_cost_micros=0,
        evidence_class="ATTESTED_EXTERNAL",
        attestation=run.attestation,
    )


def _arm_summary(evidence: CodeArmBenefitEvidenceV1) -> dict[str, Any]:
    attestation = evidence.attestation
    return {
        "model_execution_id": evidence.model_execution_id,
        "provider": None if attestation is None else attestation.provider,
        "model": None if attestation is None else attestation.model,
        "tokens": None if attestation is None else attestation.total_tokens,
        "cost_micros": evidence.total_cost_micros,
        "latency_ms": None if attestation is None else attestation.total_latency_ms,
        "hidden_test_passed": evidence.hidden_test_passed,
        "static_check_passed": evidence.static_check_passed,
        "diff_risk_score": evidence.diff_risk_score,
        "benefit_eligible": evidence.benefit_eligible,
    }


def _execution_messages(
    *,
    prepared: PreparedCodeWorkloadV1,
    arm: BenchmarkArm,
    evidence_text: str,
) -> tuple[dict[str, str], ...]:
    payload = {
        "objective": prepared.definition.objective,
        "selected_candidate_path": list(arm.selected_path_candidate_ids),
        "selected_requirement_evidence": evidence_text,
        "target_file": prepared.definition.relative_module_path,
        "current_source": prepared.original_source,
    }
    return (
        {"role": "system", "content": _MODEL_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )


def _parse_model_source(output_text: str) -> tuple[str, bool]:
    try:
        payload = json.loads(str(output_text or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", False
    if not isinstance(payload, dict) or set(payload) != {"content"}:
        return "", False
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return "", False
    encoded = content.encode("utf-8")
    if len(encoded) > 64_000 or "\x00" in content:
        return "", False
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized, True


def _search(
    *,
    snapshot: CodeTaskSnapshot,
    definition: FixedCodeWorkloadV1,
    mode: MCTSMode,
):  # noqa: ANN202
    request = GoalPlanningRequest(
        request_id=f"{definition.workload_id}-{mode.value}",
        goal=PlanningGoal(
            goal_id=f"goal-{definition.workload_id}-{mode.value}",
            domain=CODE_DOMAIN,
            objective=definition.objective,
            success_criteria=("select one bounded patch and verify it",),
            frozen_context_refs=(
                snapshot.task_ref,
                snapshot.workspace_snapshot_ref,
                snapshot.verification_profile_ref,
            ),
        ),
        state=snapshot.to_virtual_state(),
        budget=_search_budget(),
        context={},
        mcts_mode=mode,
    )
    return GoalPlanningService(
        ports=CodePlanningDomain(snapshot).planning_ports()
    ).search(request).result


def _search_budget() -> SearchBudget:
    return SearchBudget(
        max_candidates=32,
        max_expansions=16,
        max_transitions=16,
        max_rollouts=8,
        max_depth=3,
        branching_factor=3,
    )


def _initial_source() -> str:
    return "def solve(value):\n    return value\n"


def _hidden_test_source(cohort: FixedCodeCohortV1) -> str:
    lines = ["from copy import deepcopy", "", ""]
    for workload in cohort.workloads:
        lines.append(f"def test_{workload.module_name}():")
        lines.append(f"    from src import {workload.module_name} as target")
        for case in workload.cases:
            lines.append(f"    value = {case.input_value!r}")
            lines.append("    before = deepcopy(value)")
            lines.append(f"    assert target.solve(value) == {case.expected_value!r}")
            lines.append("    assert value == before")
        lines.extend(("", ""))
    return "\n".join(lines).rstrip() + "\n"


def _static_test_source(cohort: FixedCodeCohortV1) -> str:
    lines = [
        "import ast",
        "from pathlib import Path",
        "",
        "FORBIDDEN_CALLS = {'eval', 'exec', 'open', 'compile', '__import__'}",
        "",
        "def _assert_safe(module_name):",
        "    source = Path('src', module_name + '.py').read_text(encoding='utf-8')",
        "    tree = ast.parse(source)",
        "    assert not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)) for node in ast.walk(tree))",
        "    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]",
        "    assert [node.name for node in functions] == ['solve']",
        "    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS for node in ast.walk(tree))",
        "",
        "",
    ]
    for workload in cohort.workloads:
        lines.extend(
            (
                f"def test_{workload.module_name}_static():",
                f"    _assert_safe('{workload.module_name}')",
                "",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _workload_from_dict(value: Any) -> FixedCodeWorkloadV1:
    if not isinstance(value, dict):
        raise CodeBenefitExperimentError("fixed Code workload must be an object")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) < 3:
        raise CodeBenefitExperimentError("each Code workload needs at least three hidden cases")
    cases: list[FixedCodeCaseV1] = []
    for raw in raw_cases:
        if not isinstance(raw, dict) or set(raw) != {"input", "expected"}:
            raise CodeBenefitExperimentError("Code hidden case schema drifted")
        cases.append(
            FixedCodeCaseV1(
                input_value=_json_copy(raw["input"]),
                expected_value=_json_copy(raw["expected"]),
            )
        )
    module = _text(value.get("module_name"), "module_name", 80).lower()
    if not module.startswith("task_") or not module.replace("_", "").isalnum():
        raise CodeBenefitExperimentError("Code module_name is invalid")
    return FixedCodeWorkloadV1(
        workload_id=_text(value.get("workload_id"), "workload_id", 160),
        module_name=module,
        objective=_text(value.get("objective"), "objective", 2_000),
        baseline_evidence=_text(
            value.get("baseline_evidence"), "baseline_evidence", 2_000
        ),
        selected_evidence=_text(
            value.get("selected_evidence"), "selected_evidence", 2_000
        ),
        cases=tuple(cases),
    )


def _git(path: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    process_env = None
    if env is not None:
        import os

        process_env = dict(os.environ)
        process_env.update(env)
    completed = subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
        env=process_env,
    )
    return completed.stdout.strip()


def _text(value: Any, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise CodeBenefitExperimentError(f"{field} must be non-empty and bounded")
    return result


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise CodeBenefitExperimentError("Code case must be bounded JSON") from exc


def _digest_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CODE_EXECUTION_PROFILE_ID",
    "CODE_BENCHMARK_SCENE_TYPE",
    "CODE_BENCHMARK_REQUEST_DEADLINE_MS",
    "DEFAULT_CODE_BENCHMARK_MODEL_TIMEOUT_MS",
    "CODE_HIDDEN_VERIFIER_PROFILE_DIGEST",
    "CODE_STATIC_VERIFIER_PROFILE_DIGEST",
    "FIXED_CODE_COHORT_SCHEMA",
    "CodeBenchmarkRuntimeRootsV1",
    "CodeBenefitExperimentError",
    "FixedCodeCohortV1",
    "FixedCodeWorkloadV1",
    "PreparedCodeWorkloadV1",
    "code_execution_profile_digest",
    "create_fixed_code_source_repository",
    "execute_prepared_code_workload",
    "load_fixed_code_cohort",
    "prepare_code_workload",
    "sanitized_code_member_summary",
]
