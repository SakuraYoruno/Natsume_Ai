"""Acceptance criteria judgement contract and deterministic gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping

from host_brain.agent_runtime.contract_errors import (
    ACCEPTANCE_JUDGEMENT_INVALID,
    CONTRACT_FIELD_INVALID,
    ContractViolation,
    require_version,
)


ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION = "Natsume.agent.acceptance_judgement.v1"

ACCEPTANCE_CRITERION_FAILED = "ACCEPTANCE_CRITERION_FAILED"
ACCEPTANCE_EVIDENCE_REQUIRED = "ACCEPTANCE_EVIDENCE_REQUIRED"
ACCEPTANCE_EXECUTION_NOT_SUCCESSFUL = "ACCEPTANCE_EXECUTION_NOT_SUCCESSFUL"
ACCEPTANCE_REQUIRED_NOT_ACCEPTED = "ACCEPTANCE_REQUIRED_NOT_ACCEPTED"
ACCEPTANCE_REQUIRES_REVIEW = "ACCEPTANCE_REQUIRES_REVIEW"

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


class AcceptanceDecision(StrEnum):
    ACCEPT = "ACCEPT"
    RETRY = "RETRY"
    SPLIT = "SPLIT"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class AcceptanceCriterionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AcceptanceCriterionVerdict:
    criterion_id: str
    criterion_text: str
    verdict: AcceptanceCriterionStatus
    evidence_refs: tuple[str, ...]
    deterministic: bool = True
    reason_code: str | None = None
    critic_verdict: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AcceptanceCriterionVerdict":
        data = dict(payload or {})
        verdict = _enum_value(
            AcceptanceCriterionStatus,
            data.get("verdict"),
            field_name="criterion.verdict",
        )
        deterministic = data.get("deterministic", True)
        if type(deterministic) is not bool:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "criterion.deterministic must be a boolean")
        evidence_refs = _text_tuple(data.get("evidence_refs"), field_name="criterion.evidence_refs")
        item = cls(
            criterion_id=_required_text(data.get("criterion_id"), field_name="criterion_id"),
            criterion_text=_required_text(data.get("criterion_text"), field_name="criterion_text"),
            verdict=verdict,
            evidence_refs=evidence_refs,
            deterministic=deterministic,
            reason_code=_optional_text(data.get("reason_code"), field_name="reason_code"),
            critic_verdict=_optional_text(data.get("critic_verdict"), field_name="critic_verdict"),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.criterion_id or not self.criterion_text:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "criterion_id and criterion_text are required",
            )
        if not self.evidence_refs:
            raise ContractViolation(
                ACCEPTANCE_EVIDENCE_REQUIRED,
                "each acceptance criterion verdict requires evidence_refs",
            )
        if any(not _DURABLE_REFERENCE_PATTERN.fullmatch(reference) for reference in self.evidence_refs):
            raise ContractViolation(
                ACCEPTANCE_EVIDENCE_REQUIRED,
                "acceptance criterion evidence_refs must be bounded durable references",
            )
        if self.verdict is AcceptanceCriterionStatus.PASS and self.reason_code:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "PASS criterion verdict cannot carry a failure reason_code",
            )
        if self.verdict is not AcceptanceCriterionStatus.PASS and not self.reason_code:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "non-PASS criterion verdict requires a stable reason_code",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "criterion_text": self.criterion_text,
            "verdict": self.verdict.value,
            "evidence_refs": list(self.evidence_refs),
            "deterministic": self.deterministic,
            "reason_code": self.reason_code,
            "critic_verdict": self.critic_verdict,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceJudgement:
    judgement_id: str
    work_order_id: str
    decision: AcceptanceDecision
    criteria: tuple[AcceptanceCriterionVerdict, ...]
    task_graph_id: str | None = None
    task_node_id: str | None = None
    attempt_id: str | None = None
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    critic_candidate: dict[str, Any] | None = None
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    schema_version: str = ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AcceptanceJudgement":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION,
            contract_name="AcceptanceJudgement",
        )
        criteria_raw = data.get("criteria")
        if not isinstance(criteria_raw, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "criteria must be a list")
        evidence_refs = _text_tuple(data.get("evidence_refs"), field_name="evidence_refs")
        critic_candidate = data.get("critic_candidate")
        if critic_candidate is not None and not isinstance(critic_candidate, Mapping):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "critic_candidate must be an object")
        judgement = cls(
            judgement_id=_required_text(data.get("judgement_id"), field_name="judgement_id"),
            work_order_id=_required_text(data.get("work_order_id"), field_name="work_order_id"),
            decision=_enum_value(AcceptanceDecision, data.get("decision"), field_name="decision"),
            criteria=tuple(
                AcceptanceCriterionVerdict.from_mapping(item)
                for item in criteria_raw
                if isinstance(item, Mapping)
            ),
            task_graph_id=_optional_text(data.get("task_graph_id"), field_name="task_graph_id"),
            task_node_id=_optional_text(data.get("task_node_id"), field_name="task_node_id"),
            attempt_id=_optional_text(data.get("attempt_id"), field_name="attempt_id"),
            reason_code=_optional_text(data.get("reason_code"), field_name="reason_code"),
            evidence_refs=evidence_refs,
            critic_candidate=dict(critic_candidate) if isinstance(critic_candidate, Mapping) else None,
            started_at_ms=_optional_int(data.get("started_at_ms"), field_name="started_at_ms"),
            finished_at_ms=_optional_int(data.get("finished_at_ms"), field_name="finished_at_ms"),
        )
        if len(judgement.criteria) != len(criteria_raw):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "criteria must contain only objects")
        judgement.validate()
        return judgement

    def validate(self) -> None:
        if self.schema_version != ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "AcceptanceJudgement schema version is invalid",
            )
        if not self.judgement_id or not self.work_order_id:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "judgement_id and work_order_id are required",
            )
        if bool(self.task_graph_id) != bool(self.task_node_id):
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "task_graph_id and task_node_id must be set together",
            )
        if self.criteria and not self.evidence_refs:
            raise ContractViolation(
                ACCEPTANCE_EVIDENCE_REQUIRED,
                "AcceptanceJudgement requires evidence_refs when criteria exist",
            )
        if any(not _DURABLE_REFERENCE_PATTERN.fullmatch(reference) for reference in self.evidence_refs):
            raise ContractViolation(
                ACCEPTANCE_EVIDENCE_REQUIRED,
                "AcceptanceJudgement evidence_refs must be bounded durable references",
            )
        if self.decision is AcceptanceDecision.ACCEPT:
            if self.reason_code:
                raise ContractViolation(
                    ACCEPTANCE_JUDGEMENT_INVALID,
                    "ACCEPT judgement cannot carry a failure reason_code",
                )
            if any(item.verdict is not AcceptanceCriterionStatus.PASS for item in self.criteria):
                raise ContractViolation(
                    ACCEPTANCE_REQUIRED_NOT_ACCEPTED,
                    "ACCEPT judgement requires every criterion verdict to PASS",
                )
        elif not self.reason_code:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "non-ACCEPT judgement requires a stable reason_code",
            )
        if (
            self.started_at_ms is not None
            and self.finished_at_ms is not None
            and self.finished_at_ms < self.started_at_ms
        ):
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "finished_at_ms cannot precede started_at_ms",
            )

    @property
    def allows_accepted_transition(self) -> bool:
        return self.decision is AcceptanceDecision.ACCEPT and all(
            item.verdict is AcceptanceCriterionStatus.PASS for item in self.criteria
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "judgement_id": self.judgement_id,
            "work_order_id": self.work_order_id,
            "task_graph_id": self.task_graph_id,
            "task_node_id": self.task_node_id,
            "attempt_id": self.attempt_id,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "criteria": [item.to_dict() for item in self.criteria],
            "critic_candidate": dict(self.critic_candidate) if self.critic_candidate else None,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
        }


def judge_acceptance_result(
    result: Mapping[str, Any],
    *,
    work_order_id: str,
    acceptance_criteria: tuple[str, ...] | list[str],
    critic_candidate: Mapping[str, Any] | None = None,
    task_graph_id: str | None = None,
    task_node_id: str | None = None,
    attempt_id: str | None = None,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
) -> AcceptanceJudgement:
    criteria_texts = tuple(str(item or "").strip() for item in acceptance_criteria if str(item or "").strip())
    judgement_id = _judgement_id(work_order_id=work_order_id, result=result, criteria=criteria_texts)
    base_evidence = _base_evidence_refs(judgement_id, result)
    verdicts = tuple(
        _judge_criterion(
            index=index,
            text=text,
            result=result,
            judgement_id=judgement_id,
            base_evidence=base_evidence,
        )
        for index, text in enumerate(criteria_texts, start=1)
    )
    decision, reason = _final_decision(verdicts, result=result, critic_candidate=critic_candidate)
    evidence_refs = tuple(
        dict.fromkeys(
            ref
            for verdict in verdicts
            for ref in verdict.evidence_refs
        )
    )
    if not evidence_refs:
        evidence_refs = (
            f"acceptance://{judgement_id}/result/{_digest({'work_order_id': work_order_id, 'criteria': []})}",
        )
    judgement = AcceptanceJudgement(
        judgement_id=judgement_id,
        work_order_id=str(work_order_id or "").strip(),
        decision=decision,
        criteria=verdicts,
        task_graph_id=str(task_graph_id or "").strip() or None,
        task_node_id=str(task_node_id or "").strip() or None,
        attempt_id=str(attempt_id or "").strip() or None,
        reason_code=reason,
        evidence_refs=evidence_refs,
        critic_candidate=dict(critic_candidate) if isinstance(critic_candidate, Mapping) else None,
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
    )
    judgement.validate()
    return judgement


class AcceptanceGate:
    @staticmethod
    def validate_node_transition(
        judgement: Mapping[str, Any] | AcceptanceJudgement | None,
        *,
        target_status: str,
        graph_id: str,
        node_id: str,
        root_work_order_id: str,
        node_acceptance_criteria: tuple[str, ...],
        outbox_evidence_refs: tuple[str, ...] = (),
    ) -> AcceptanceJudgement | None:
        if target_status != "ACCEPTED":
            return None
        if judgement is None:
            raise ContractViolation(
                ACCEPTANCE_REQUIRED_NOT_ACCEPTED,
                "ACCEPTED transition requires an AcceptanceJudgement",
            )
        accepted = judgement if isinstance(judgement, AcceptanceJudgement) else AcceptanceJudgement.from_mapping(judgement)
        if accepted.work_order_id != root_work_order_id:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "AcceptanceJudgement.work_order_id must match TaskGraph.root_work_order_id",
            )
        if accepted.task_graph_id != graph_id or accepted.task_node_id != node_id:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "AcceptanceJudgement task graph/node identity must match the state transition",
            )
        if not accepted.allows_accepted_transition:
            raise ContractViolation(
                ACCEPTANCE_REQUIRED_NOT_ACCEPTED,
                "ACCEPTED transition requires ACCEPT judgement and PASS criterion verdicts",
            )
        expected = tuple(str(item or "").strip() for item in node_acceptance_criteria if str(item or "").strip())
        actual = tuple(item.criterion_text for item in accepted.criteria)
        if expected and actual != expected:
            raise ContractViolation(
                ACCEPTANCE_JUDGEMENT_INVALID,
                "AcceptanceJudgement criteria must exactly match TaskNode.acceptance_criteria",
            )
        missing = sorted(set(accepted.evidence_refs) - set(outbox_evidence_refs))
        if missing:
            raise ContractViolation(
                ACCEPTANCE_EVIDENCE_REQUIRED,
                "outbox evidence_refs must include AcceptanceJudgement evidence refs",
            )
        return accepted


def _judge_criterion(
    *,
    index: int,
    text: str,
    result: Mapping[str, Any],
    judgement_id: str,
    base_evidence: tuple[str, ...],
) -> AcceptanceCriterionVerdict:
    criterion_id = f"criterion-{index}"
    evidence_refs = (
        *base_evidence,
        f"acceptance://{judgement_id}/criterion/{index}/{_digest({'criterion': text, 'result_status': result.get('status')})}",
    )
    status = str(result.get("status") or "").strip().upper()
    if status != "EXECUTED":
        return AcceptanceCriterionVerdict(
            criterion_id=criterion_id,
            criterion_text=text,
            verdict=AcceptanceCriterionStatus.FAIL,
            evidence_refs=evidence_refs,
            deterministic=True,
            reason_code=ACCEPTANCE_EXECUTION_NOT_SUCCESSFUL,
        )
    metadata = dict(result.get("metadata") or {})
    returncode = metadata.get("returncode")
    if _mentions_exit_zero(text):
        if returncode == 0:
            return AcceptanceCriterionVerdict(
                criterion_id=criterion_id,
                criterion_text=text,
                verdict=AcceptanceCriterionStatus.PASS,
                evidence_refs=evidence_refs,
                deterministic=True,
            )
        return AcceptanceCriterionVerdict(
            criterion_id=criterion_id,
            criterion_text=text,
            verdict=AcceptanceCriterionStatus.FAIL if isinstance(returncode, int) else AcceptanceCriterionStatus.UNKNOWN,
            evidence_refs=evidence_refs,
            deterministic=True,
            reason_code=ACCEPTANCE_CRITERION_FAILED if isinstance(returncode, int) else ACCEPTANCE_REQUIRES_REVIEW,
        )
    if _criterion_text_supported_by_output(text, result):
        return AcceptanceCriterionVerdict(
            criterion_id=criterion_id,
            criterion_text=text,
            verdict=AcceptanceCriterionStatus.PASS,
            evidence_refs=evidence_refs,
            deterministic=True,
        )
    return AcceptanceCriterionVerdict(
        criterion_id=criterion_id,
        criterion_text=text,
        verdict=AcceptanceCriterionStatus.UNKNOWN,
        evidence_refs=evidence_refs,
        deterministic=True,
        reason_code=ACCEPTANCE_REQUIRES_REVIEW,
    )


def _final_decision(
    verdicts: tuple[AcceptanceCriterionVerdict, ...],
    *,
    result: Mapping[str, Any],
    critic_candidate: Mapping[str, Any] | None,
) -> tuple[AcceptanceDecision, str | None]:
    if any(item.verdict is AcceptanceCriterionStatus.FAIL for item in verdicts):
        return AcceptanceDecision.FAIL, ACCEPTANCE_CRITERION_FAILED
    if verdicts and all(item.verdict is AcceptanceCriterionStatus.PASS for item in verdicts):
        return AcceptanceDecision.ACCEPT, None
    status = str(result.get("status") or "").strip().upper()
    if status in {"FAILED", "DEGRADED"}:
        return AcceptanceDecision.RETRY, ACCEPTANCE_EXECUTION_NOT_SUCCESSFUL
    critic_decision = _critic_decision(critic_candidate)
    if critic_decision in {AcceptanceDecision.RETRY, AcceptanceDecision.SPLIT, AcceptanceDecision.REVIEW}:
        return critic_decision, ACCEPTANCE_REQUIRES_REVIEW
    return AcceptanceDecision.REVIEW, ACCEPTANCE_REQUIRES_REVIEW


def _mentions_exit_zero(text: str) -> bool:
    normalized = text.lower()
    return (
        "exit code" in normalized
        and ("zero" in normalized or "0" in normalized)
    )


def _criterion_text_supported_by_output(text: str, result: Mapping[str, Any]) -> bool:
    needle = _normalize_text(text)
    if not needle or len(needle) < 6:
        return False
    haystack = _normalize_text(
        " ".join(
            str(result.get(key) or "")
            for key in ("summary", "result_text", "detail")
        )
    )
    return needle in haystack


def _critic_decision(candidate: Mapping[str, Any] | None) -> AcceptanceDecision | None:
    if not isinstance(candidate, Mapping):
        return None
    raw = str(candidate.get("decision") or "").strip().upper()
    if not raw:
        return None
    try:
        return AcceptanceDecision(raw)
    except ValueError:
        return None


def _base_evidence_refs(judgement_id: str, result: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = [f"acceptance://{judgement_id}/result/{_digest({'status': result.get('status')})}"]
    verification = result.get("verification")
    if isinstance(verification, Mapping):
        refs.extend(str(item).strip() for item in list(verification.get("evidence_refs") or []) if str(item).strip())
    return tuple(dict.fromkeys(refs))


def _judgement_id(*, work_order_id: str, result: Mapping[str, Any], criteria: tuple[str, ...]) -> str:
    return "accept_" + _digest(
        {
            "work_order_id": work_order_id,
            "status": result.get("status"),
            "criteria": list(criteria),
        }
    )[:24]


def _digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} is required")
    return text


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a string")
    text = value.strip()
    return text or None


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be an integer")
    if value < 0:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be non-negative")
    return value


def _text_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must be a list")
    refs = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(refs) != len(value):
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"{field_name} must contain only non-empty strings")
    return refs


def _enum_value(enum_type: type[StrEnum], value: Any, *, field_name: str) -> StrEnum:
    try:
        return enum_type(str(value or "").strip().upper())
    except ValueError as exc:
        raise ContractViolation(CONTRACT_FIELD_INVALID, f"unsupported {field_name}: {value}") from exc


__all__ = [
    "ACCEPTANCE_CRITERION_FAILED",
    "ACCEPTANCE_EVIDENCE_REQUIRED",
    "ACCEPTANCE_EXECUTION_NOT_SUCCESSFUL",
    "ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION",
    "ACCEPTANCE_REQUIRED_NOT_ACCEPTED",
    "ACCEPTANCE_REQUIRES_REVIEW",
    "AcceptanceCriterionStatus",
    "AcceptanceCriterionVerdict",
    "AcceptanceDecision",
    "AcceptanceGate",
    "AcceptanceJudgement",
    "judge_acceptance_result",
]
