"""Production failure drill runner for cold-start/dependency/rollback and orchestration scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from host_brain.audit.drill import (
    S_DRILL_SCENARIO,
    S_DRILL_SUITE,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    build_drill_result,
    normalize_reason_code_chain,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_NODE_MISSING,
    ORCH_PARAM_DRIFT,
    RELEASE_ROLLBACK_TARGET_MISSING,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SKILL_TOKEN_MISSING,
)
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.integrations.llm_gateway import (
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
    LlmGatewayContract,
)
from host_brain.release.release_manifest import (
    STATUS_FAILED as RELEASE_STATUS_FAILED,
    build_release_checklist,
    validate_release_bundle,
)
from tools.replay.request_replay import build_request_replay_report


SCENARIO_COLD_START_NO_KEY = "cold_start_no_key"
SCENARIO_PROVIDER_DOWN = "provider_down"
SCENARIO_ROLLBACK_CONFLICT = "rollback_conflict"
SCENARIO_MODEL_PROFILE_MISSING = "model_profile_missing"
SCENARIO_SELECTOR_NO_CANDIDATE = "selector_no_candidate"
SCENARIO_PARAM_DRIFT = "param_drift"
SCENARIO_APPROVAL_TOKEN_MISSING = "approval_token_missing"

DRILL_SCENARIO_INVALID = "DRILL_SCENARIO_INVALID"


def run_drill_scenario_from_fixture(
    *,
    root: str | Path,
    fixture_path: str | Path,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    scenario = _load_json(repo_root / str(fixture_path))
    return run_drill_scenario(
        root=repo_root,
        scenario=scenario,
        ts_ms=ts_ms,
    )


def run_drill_scenario(
    *,
    root: str | Path,
    scenario: dict[str, Any],
    ts_ms: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else int(scenario.get("ts_ms") or 0) or 0)
    if now_value <= 0:
        now_value = 1_800_100_000_000

    scenario_id = str(scenario.get("scenario_id") or "").strip() or "drill.unknown"
    request_id = str(scenario.get("request_id") or "").strip() or f"req_{scenario_id.replace('.', '_')}"
    scenario_kind = str(scenario.get("scenario_kind") or "").strip().lower()

    ledger = SQLiteLedger(db_path=":memory:")
    try:
        if scenario_kind == SCENARIO_COLD_START_NO_KEY:
            return _run_cold_start_no_key(
                root=repo_root,
                scenario=scenario,
                scenario_id=scenario_id,
                request_id=request_id,
                ledger=ledger,
                ts_ms=now_value,
            )
        if scenario_kind == SCENARIO_PROVIDER_DOWN:
            return _run_provider_down(
                root=repo_root,
                scenario=scenario,
                scenario_id=scenario_id,
                request_id=request_id,
                ledger=ledger,
                ts_ms=now_value,
            )
        if scenario_kind == SCENARIO_ROLLBACK_CONFLICT:
            return _run_rollback_conflict(
                root=repo_root,
                scenario=scenario,
                scenario_id=scenario_id,
                request_id=request_id,
                ledger=ledger,
                ts_ms=now_value,
            )
        if scenario_kind in {
            SCENARIO_MODEL_PROFILE_MISSING,
            SCENARIO_SELECTOR_NO_CANDIDATE,
            SCENARIO_PARAM_DRIFT,
            SCENARIO_APPROVAL_TOKEN_MISSING,
        }:
            return _run_orchestration_case(
                scenario=scenario,
                scenario_id=scenario_id,
                request_id=request_id,
                ledger=ledger,
                ts_ms=now_value,
            )

        replay = _build_replay_with_reject(
            ledger=ledger,
            request_id=request_id,
            reason_code=DRILL_SCENARIO_INVALID,
            stage=S_DRILL_SCENARIO,
            ts_ms=now_value,
        )
        chain = normalize_reason_code_chain([DRILL_SCENARIO_INVALID], replay["summary"]["reason_code_chain"])
        return build_drill_result(
            scenario_id=scenario_id,
            status=STATUS_FAILED,
            stage=S_DRILL_SCENARIO,
            reason_code=DRILL_SCENARIO_INVALID,
            reason_code_chain=chain,
            replay_ref=f"request::{request_id}",
            detail=f"unsupported scenario_kind: {scenario_kind}",
            payload={"scenario": dict(scenario)},
            replay_report=replay,
            ts_ms=now_value,
        )
    finally:
        ledger.close()


def run_drill_suite(
    *,
    root: str | Path,
    scenario_fixtures: Iterable[str | Path],
    ts_ms: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    base_ts = int(ts_ms if ts_ms is not None else 1_800_100_000_000)
    results: list[dict[str, Any]] = []

    for index, fixture in enumerate(scenario_fixtures):
        row = run_drill_scenario_from_fixture(
            root=repo_root,
            fixture_path=fixture,
            ts_ms=base_ts + index,
        )
        results.append(row)

    status = STATUS_OK
    if any(row["status"] == STATUS_FAILED for row in results):
        status = STATUS_FAILED
    elif any(row["status"] == STATUS_DEGRADED for row in results):
        status = STATUS_DEGRADED

    return {
        "stage": S_DRILL_SUITE,
        "status": status,
        "reason_code": _first_reason(results),
        "summary": {
            "scenario_total": len(results),
            "ok_count": sum(1 for row in results if row["status"] == STATUS_OK),
            "degraded_count": sum(1 for row in results if row["status"] == STATUS_DEGRADED),
            "failed_count": sum(1 for row in results if row["status"] == STATUS_FAILED),
        },
        "scenarios": results,
    }


def _run_cold_start_no_key(
    *,
    root: Path,
    scenario: dict[str, Any],
    scenario_id: str,
    request_id: str,
    ledger: SQLiteLedger,
    ts_ms: int,
) -> dict[str, Any]:
    prompt = _load_prompt(root=root, scenario=scenario)
    model_profile_id = str(scenario.get("model_profile_id") or "mp.openai.v1")
    gateway = LlmGatewayContract(
        ledger=ledger,
        provider_name=PROVIDER_OPENAI,
        api_key=None,
        available=bool(scenario.get("available", True)),
    )
    result = gateway.generate(
        request_id=request_id,
        model_profile_id=model_profile_id,
        prompt_payload=prompt,
        ts_ms=ts_ms,
    )
    reason_code = str(result.reason_code or SERVICE_UNAVAILABLE_COLD_START)
    replay = _build_replay_with_reject(
        ledger=ledger,
        request_id=request_id,
        reason_code=reason_code,
        stage=result.stage,
        ts_ms=ts_ms,
    )
    chain = normalize_reason_code_chain(
        [reason_code],
        [result.output.get("reason_code")],
        replay["summary"]["reason_code_chain"],
    )
    row = build_drill_result(
        scenario_id=scenario_id,
        status=result.status,
        stage=result.stage,
        reason_code=reason_code,
        reason_code_chain=chain,
        replay_ref=f"request::{request_id}",
        detail=result.detail,
        payload=result.output,
        replay_report=replay,
        ts_ms=ts_ms,
    )
    row["model_profile_id"] = model_profile_id
    return row


def _run_provider_down(
    *,
    root: Path,
    scenario: dict[str, Any],
    scenario_id: str,
    request_id: str,
    ledger: SQLiteLedger,
    ts_ms: int,
) -> dict[str, Any]:
    prompt = _load_prompt(root=root, scenario=scenario)
    provider_name = str(scenario.get("provider_name") or PROVIDER_MOCK).strip().lower()
    model_profile_id = str(scenario.get("model_profile_id") or "mp.mock.v1")
    api_key = str(scenario.get("api_key") or "").strip() or None
    gateway = LlmGatewayContract(
        ledger=ledger,
        provider_name=provider_name,
        api_key=api_key,
        available=False,
    )
    result = gateway.generate(
        request_id=request_id,
        model_profile_id=model_profile_id,
        prompt_payload=prompt,
        ts_ms=ts_ms,
    )
    reason_code = str(result.reason_code or SERVICE_UNAVAILABLE_DEPENDENCY)
    replay = _build_replay_with_reject(
        ledger=ledger,
        request_id=request_id,
        reason_code=reason_code,
        stage=result.stage,
        ts_ms=ts_ms,
    )
    chain = normalize_reason_code_chain(
        [reason_code],
        [result.output.get("reason_code")],
        replay["summary"]["reason_code_chain"],
    )
    row = build_drill_result(
        scenario_id=scenario_id,
        status=result.status,
        stage=result.stage,
        reason_code=reason_code,
        reason_code_chain=chain,
        replay_ref=f"request::{request_id}",
        detail=result.detail,
        payload=result.output,
        replay_report=replay,
        ts_ms=ts_ms,
    )
    row["model_profile_id"] = model_profile_id
    return row


def _run_orchestration_case(
    *,
    scenario: dict[str, Any],
    scenario_id: str,
    request_id: str,
    ledger: SQLiteLedger,
    ts_ms: int,
) -> dict[str, Any]:
    graph_config = dict(scenario.get("graph") or {})
    payload = dict(scenario.get("payload") or {})

    dependency_statuses = dict(scenario.get("dependency_statuses") or {})
    model_profile_id = str(graph_config.get("model_profile_id") or "").strip() or None

    runtime = GraphRuntimeContract(ledger=ledger)
    result = runtime.run_graph(
        request_id=request_id,
        graph_config=graph_config,
        payload=payload,
        dependency_statuses=dependency_statuses,
        ts_ms=ts_ms,
    )
    reason_code = str(result.reason_code or _default_reason_for_scenario(scenario_kind=str(scenario.get("scenario_kind") or "")))
    replay = _build_replay_with_reject(
        ledger=ledger,
        request_id=request_id,
        reason_code=reason_code,
        stage=result.stage,
        ts_ms=ts_ms,
    )
    chain = normalize_reason_code_chain(
        [reason_code],
        [result.reason_code],
        list((result.output or {}).get("reason_code_chain") or []),
        replay["summary"]["reason_code_chain"],
    )
    detail = str(result.detail or "orchestration drill executed")
    row = build_drill_result(
        scenario_id=scenario_id,
        status=result.status,
        stage=result.stage,
        reason_code=reason_code,
        reason_code_chain=chain,
        replay_ref=f"request::{request_id}",
        detail=detail,
        payload=dict(result.output or {}),
        replay_report=replay,
        ts_ms=ts_ms,
    )
    row["model_profile_id"] = model_profile_id or result.output.get("model_profile_id")
    return row


def _default_reason_for_scenario(*, scenario_kind: str) -> str:
    normalized = str(scenario_kind or "").strip().lower()
    if normalized == SCENARIO_PARAM_DRIFT:
        return ORCH_PARAM_DRIFT
    if normalized == SCENARIO_APPROVAL_TOKEN_MISSING:
        return SKILL_TOKEN_MISSING
    return ORCH_NODE_MISSING


def _run_rollback_conflict(
    *,
    root: Path,
    scenario: dict[str, Any],
    scenario_id: str,
    request_id: str,
    ledger: SQLiteLedger,
    ts_ms: int,
) -> dict[str, Any]:
    release_specs = _load_json(root / str(scenario.get("release_specs_ref")))
    rollback_mapping = _load_json(root / str(scenario.get("rollback_mapping_ref")))
    milestone = str(scenario.get("milestone") or "M3").strip().upper()
    spec_row = _pick_spec_by_milestone(specs=release_specs.get("specs") or [], milestone=milestone)
    if spec_row is None:
        replay = _build_replay_with_reject(
            ledger=ledger,
            request_id=request_id,
            reason_code=DRILL_SCENARIO_INVALID,
            stage=S_DRILL_SCENARIO,
            ts_ms=ts_ms,
        )
        chain = normalize_reason_code_chain([DRILL_SCENARIO_INVALID], replay["summary"]["reason_code_chain"])
        return build_drill_result(
            scenario_id=scenario_id,
            status=STATUS_FAILED,
            stage=S_DRILL_SCENARIO,
            reason_code=DRILL_SCENARIO_INVALID,
            reason_code_chain=chain,
            replay_ref=f"request::{request_id}",
            detail=f"milestone not found in release specs: {milestone}",
            payload={"milestone": milestone},
            replay_report=replay,
            ts_ms=ts_ms,
        )

    checklist = build_release_checklist(
        root=root,
        milestone=spec_row["milestone"],
        release_id=spec_row["release_id"],
        assets=spec_row["assets"],
        required_services=spec_row["required_services"],
        config_snapshot_refs=scenario.get("config_snapshot_refs") or [],
        ts_ms=ts_ms,
    )
    known_release_ids = set(str(item) for item in scenario.get("known_release_ids") or [])
    validation = validate_release_bundle(
        root=root,
        checklist=checklist,
        rollback_mapping=rollback_mapping,
        known_release_ids=known_release_ids,
    )
    reason_code = str(validation.get("reason_code") or RELEASE_ROLLBACK_TARGET_MISSING)
    issues = validation.get("issues") or []
    detail = str(issues[0]["detail"]) if issues else "rollback drill completed without issues"
    ledger.log_error(
        request_id=request_id,
        stage=str(validation.get("stage") or "S_RELEASE_CHECKLIST"),
        reason_code=reason_code,
        detail=detail,
    )
    replay = _build_replay_with_reject(
        ledger=ledger,
        request_id=request_id,
        reason_code=reason_code,
        stage=str(validation.get("stage") or "S_RELEASE_CHECKLIST"),
        ts_ms=ts_ms,
    )
    chain = normalize_reason_code_chain(
        [reason_code],
        [row.get("reason_code") for row in issues],
        replay["summary"]["reason_code_chain"],
    )
    status = STATUS_FAILED if validation.get("status") == RELEASE_STATUS_FAILED else STATUS_OK
    return build_drill_result(
        scenario_id=scenario_id,
        status=status,
        stage=str(validation.get("stage") or "S_RELEASE_CHECKLIST"),
        reason_code=reason_code,
        reason_code_chain=chain,
        replay_ref=f"request::{request_id}",
        detail=detail,
        payload=validation,
        replay_report=replay,
        ts_ms=ts_ms,
    )


def _build_replay_with_reject(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    reason_code: str,
    stage: str,
    ts_ms: int,
) -> dict[str, Any]:
    _log_minimal_intent(ledger=ledger, request_id=request_id, ts_ms=ts_ms)
    ledger.log_governor(
        {
            "request_id": request_id,
            "thread_id": f"thread_{request_id}",
            "turn_id": f"turn_{request_id}",
            "intent_id": f"intent_{request_id}",
            "decision": "REJECT",
            "reason_code": reason_code,
            "rule_trace": [],
            "tokens": {},
            "snapshot_refs": {"governance_snapshot": "drill"},
            "stage": stage,
            "ts_ms": ts_ms + 1,
        }
    )
    return build_request_replay_report(ledger=ledger, request_id=request_id)


def _log_minimal_intent(*, ledger: SQLiteLedger, request_id: str, ts_ms: int) -> None:
    ledger.log_intent(
        {
            "request_id": request_id,
            "thread_id": f"thread_{request_id}",
            "turn_id": f"turn_{request_id}",
            "intent_id": f"intent_{request_id}",
            "session_id": f"session_{request_id}",
            "seq": 1,
            "ts_ms": ts_ms,
            "nonce": f"nonce_{request_id}",
            "parse_stage": "LENIENT_OK",
        }
    )


def _load_prompt(*, root: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    prompt_ref = str(scenario.get("prompt_payload_ref") or "tests/fixtures/llm_gateway/good/prompt_payload.json")
    return _load_json(root / prompt_ref)


def _pick_spec_by_milestone(*, specs: list[dict[str, Any]], milestone: str) -> dict[str, Any] | None:
    for row in specs:
        if str(row.get("milestone") or "").strip().upper() == milestone:
            return dict(row)
    return None


def _first_reason(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        reason_code = str(row.get("reason_code") or "").strip()
        if reason_code:
            return reason_code
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if len(args) < 2:
        print(
            json.dumps(
                {
                    "stage": S_DRILL_SUITE,
                    "status": STATUS_FAILED,
                    "reason_code": DRILL_SCENARIO_INVALID,
                    "detail": "usage: production_drill.py <repo_root> <fixture_a> [fixture_b ...]",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    root = args[0]
    suite = run_drill_suite(root=root, scenario_fixtures=args[1:])
    print(json.dumps(suite, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
