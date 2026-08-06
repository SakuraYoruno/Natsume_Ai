"""Attested hidden quality verifier for real Research benefit cohorts.

The paired Research ledger deliberately stores no answer text. A fresh
process-local arm output may therefore be judged exactly once before it is
discarded. This module sends that transient answer, frozen source facts and
private coverage points through a second canonical model attempt. Only
digests, usage and the bounded score enter durable cohort evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchQualityVerifierReceiptV1,
)
from host_brain.model_runtime.live_prompts import RESEARCH_QUALITY_VERIFIER_PROMPT_ID
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
)
from host_brain.planning_search.research_paired_execution import (
    ResearchTransientArmOutputV1,
)


RESEARCH_QUALITY_RUBRIC_V1 = """
按以下确定性规则评估候选研究回答：
1. 只根据给出的冻结来源事实和期望覆盖点判断，不使用外部知识。
2. quality_score 在 0 到 1 之间，主要表示期望覆盖点被准确、完整、无矛盾地回答的比例；空泛提及不算覆盖。
3. 引用必须与对应结论相关；有效但无关的引用不能提高分数。
4. 严重政策错误仅统计危险、越权或明确违反题目约束的结论。
5. 严重冲突错误仅统计与冻结来源事实直接相反、或在冲突未解决时武断选边的结论。
6. 不因文风、长度或回答来自 baseline/search 而加减分。
""".strip()

_QUALITY_SYSTEM_TEMPLATE = """
你是独立的隐藏 Research 质量验证器。你看不到候选属于哪只实验 arm，也不得推断或偏袒搜索策略。
严格使用提供的冻结来源事实、期望覆盖点和统一评分规则。只输出一个 JSON 对象，且只能包含：
quality_score（0 到 1 的数字）、severe_policy_errors（非负整数）、severe_conflict_errors（非负整数）。
不要输出理由、Markdown 或额外字段。
""".strip()


class ResearchQualityVerifierError(RuntimeError):
    """A transient answer cannot produce trustworthy quality evidence."""


def research_quality_rubric_digest() -> str:
    return _digest(
        {"schema": "natsume.research-quality-rubric.v1", "rubric": RESEARCH_QUALITY_RUBRIC_V1}
    )


def research_quality_profile_digest() -> str:
    return _digest(
        {
            "schema": "natsume.research-quality-profile.v1",
            "prompt_id": RESEARCH_QUALITY_VERIFIER_PROMPT_ID,
            "system_template": _QUALITY_SYSTEM_TEMPLATE,
            "output_fields": [
                "quality_score",
                "severe_policy_errors",
                "severe_conflict_errors",
            ],
            "rubric_digest": research_quality_rubric_digest(),
        }
    )


class AttestedResearchQualityVerifier:
    """Create one external, hidden and response-bound quality receipt."""

    def __init__(self, *, external_adapter: AttestedExternalResearchAdapter) -> None:
        if not isinstance(external_adapter, AttestedExternalResearchAdapter):
            raise TypeError("external_adapter must be AttestedExternalResearchAdapter")
        self._external_adapter = external_adapter

    async def verify(
        self,
        *,
        run_id: str,
        arm_output: ResearchTransientArmOutputV1,
        objective: str,
        expected_points: Sequence[str],
        frozen_source_facts: Sequence[str],
        deadline_ms: int = 30_000,
    ) -> ResearchQualityVerifierReceiptV1:
        normalized_run_id = _text(run_id, field="run_id", limit=255)
        if not isinstance(arm_output, ResearchTransientArmOutputV1):
            raise TypeError("arm_output must be ResearchTransientArmOutputV1")
        normalized_objective = _text(objective, field="objective", limit=4_000)
        points = _texts(expected_points, field="expected_points", max_items=12, item_limit=1_000)
        sources = _texts(
            frozen_source_facts,
            field="frozen_source_facts",
            max_items=12,
            item_limit=8_000,
        )
        if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int) or deadline_ms <= 0:
            raise ResearchQualityVerifierError("deadline_ms must be a positive integer")

        candidate = _candidate_payload(arm_output.run.output_text)
        arm_execution_id = arm_output.run.execution_id
        evaluated_response_digest = arm_output.run.attestation.response_digest
        verifier_execution_id = _verifier_execution_id(
            run_id=normalized_run_id,
            arm_execution_id=arm_execution_id,
            response_digest=evaluated_response_digest,
        )
        quality_run = await self._external_adapter.execute(
            execution_id=verifier_execution_id,
            arm_id=f"quality-{_digest({'execution_id': arm_execution_id})[:24]}",
            prompt_id=RESEARCH_QUALITY_VERIFIER_PROMPT_ID,
            messages=_quality_messages(
                objective=normalized_objective,
                expected_points=points,
                frozen_source_facts=sources,
                candidate=candidate,
            ),
            deadline_ms=deadline_ms,
            cognitive_tier="RESEARCH",
        )
        score, policy_errors, conflict_errors = _parse_quality_output(
            quality_run.output_text
        )
        return ResearchQualityVerifierReceiptV1(
            run_id=normalized_run_id,
            arm_role=arm_output.role,
            arm_execution_id=arm_execution_id,
            evaluated_response_digest=evaluated_response_digest,
            verifier_execution_id=quality_run.execution_id,
            verifier_attempt_ids=quality_run.model_receipt.attempt_ids,
            verifier_profile_digest=research_quality_profile_digest(),
            rubric_digest=research_quality_rubric_digest(),
            verifier_result_digest=quality_run.attestation.response_digest,
            quality_score=score,
            severe_policy_errors=policy_errors,
            severe_conflict_errors=conflict_errors,
            evidence_class="ATTESTED_EXTERNAL",
            hidden_verifier=True,
            verifier_prompt_digest=quality_run.model_receipt.prompt_digest,
            attestation_ref=quality_run.attestation.attestation_ref,
            attestation=quality_run.attestation,
        )


def _quality_messages(
    *,
    objective: str,
    expected_points: tuple[str, ...],
    frozen_source_facts: tuple[str, ...],
    candidate: dict[str, Any],
) -> tuple[dict[str, str], ...]:
    # Arm role is intentionally absent. The same profile and rubric judge both
    # candidates; only their actual answer and citation proposals differ.
    user_payload = {
        "objective": objective,
        "expected_points": list(expected_points),
        "frozen_source_facts": list(frozen_source_facts),
        "candidate_answer": candidate["answer"],
        "candidate_citations": candidate["citations"],
        "rubric": RESEARCH_QUALITY_RUBRIC_V1,
    }
    return (
        {"role": "system", "content": _QUALITY_SYSTEM_TEMPLATE},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )


def _candidate_payload(output_text: str) -> dict[str, Any]:
    try:
        value = json.loads(str(output_text or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchQualityVerifierError("candidate Research output is not JSON") from exc
    if not isinstance(value, dict) or set(value) != {"answer", "citations"}:
        raise ResearchQualityVerifierError("candidate Research output shape drifted")
    if not isinstance(value["answer"], str) or not isinstance(value["citations"], list):
        raise ResearchQualityVerifierError("candidate Research output fields are invalid")
    return {"answer": value["answer"], "citations": value["citations"]}


def _parse_quality_output(output_text: str) -> tuple[float, int, int]:
    try:
        value = json.loads(str(output_text or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchQualityVerifierError("quality verifier output is not JSON") from exc
    expected = {"quality_score", "severe_policy_errors", "severe_conflict_errors"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ResearchQualityVerifierError("quality verifier output shape drifted")
    score = value["quality_score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ResearchQualityVerifierError("quality_score must be numeric")
    normalized_score = float(score)
    if not math.isfinite(normalized_score) or not 0.0 <= normalized_score <= 1.0:
        raise ResearchQualityVerifierError("quality_score must be within [0, 1]")
    errors: list[int] = []
    for field in ("severe_policy_errors", "severe_conflict_errors"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ResearchQualityVerifierError(f"{field} must be a non-negative integer")
        errors.append(item)
    return normalized_score, errors[0], errors[1]


def _verifier_execution_id(
    *, run_id: str, arm_execution_id: str, response_digest: str
) -> str:
    digest = _digest(
        {
            "schema": "natsume.research-quality-execution.v1",
            "run_id": run_id,
            "arm_execution_id": arm_execution_id,
            "response_digest": response_digest,
            "profile_digest": research_quality_profile_digest(),
            "rubric_digest": research_quality_rubric_digest(),
        }
    )
    return f"research-quality-{digest[:40]}"


def _texts(
    value: Sequence[str],
    *,
    field: str,
    max_items: int,
    item_limit: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ResearchQualityVerifierError(f"{field} must be an explicit sequence")
    items = tuple(_text(item, field=f"{field}[]", limit=item_limit) for item in value)
    if not items or len(items) > max_items:
        raise ResearchQualityVerifierError(f"{field} must contain 1 through {max_items} items")
    return items


def _text(value: Any, *, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ResearchQualityVerifierError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AttestedResearchQualityVerifier",
    "RESEARCH_QUALITY_RUBRIC_V1",
    "ResearchQualityVerifierError",
    "research_quality_profile_digest",
    "research_quality_rubric_digest",
]
