"""Fail-closed Evolution Lab runner boundary.

The runner accepts only a dataset reader, restricted model port, verifier and
the append-only Lab repository.  It deliberately has no production writer,
CapabilityBroker, TokenService, Edge actuator or activation port.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol

from .contracts import ExperimentRunReceiptV1, ExperimentSpecV1


class EvolutionLabRunnerError(RuntimeError):
    """Lab execution failed closed before production influence."""


class DatasetReader(Protocol):
    def read(self, *, dataset_digest: str) -> Mapping[str, Any]: ...


class RestrictedModelPort(Protocol):
    def run(
        self,
        *,
        variant: str,
        dataset: Mapping[str, Any],
        seed: int,
        budget: Mapping[str, int],
    ) -> Mapping[str, Any]: ...


class LabVerifier(Protocol):
    def verify(
        self,
        *,
        variant: str,
        dataset: Mapping[str, Any],
        model_output: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ExperimentRepository(Protocol):
    def append_run(self, receipt: ExperimentRunReceiptV1) -> str: ...


_FORBIDDEN_PORT_NAMES = frozenset(
    {
        "write",
        "activate",
        "capability_broker",
        "token_service",
        "edge_actuator",
        "production_writer",
        "activation_port",
    }
)


@dataclass(frozen=True, slots=True)
class EvolutionLabPortSet:
    dataset_reader: DatasetReader
    model: RestrictedModelPort
    verifier: LabVerifier
    repository: ExperimentRepository

    def validate(self) -> None:
        for name, port in (
            ("dataset_reader", self.dataset_reader),
            ("model", self.model),
            ("verifier", self.verifier),
            ("repository", self.repository),
        ):
            if port is None:
                raise EvolutionLabRunnerError(f"{name} port is required")
            leaked = sorted(
                forbidden
                for forbidden in _FORBIDDEN_PORT_NAMES
                if hasattr(port, forbidden)
            )
            if leaked:
                # 端口按能力表面而非类型名做负向检查：任何可写、激活、令牌或 Edge
                # 入口都会让实验环境越过“只产生证据”的权限边界，因此直接拒绝组合。
                raise EvolutionLabRunnerError(
                    f"{name} exposes forbidden production authority: {','.join(leaked)}"
                )


class EvolutionLabRunner:
    """Execute one isolated variant and persist only its evidence receipt."""

    def __init__(self, *, ports: EvolutionLabPortSet) -> None:
        if not isinstance(ports, EvolutionLabPortSet):
            raise TypeError("ports must be EvolutionLabPortSet")
        ports.validate()
        self._ports = ports

    def run_variant(self, *, spec: ExperimentSpecV1, variant: str) -> ExperimentRunReceiptV1:
        if not isinstance(spec, ExperimentSpecV1):
            raise TypeError("spec must be ExperimentSpecV1")
        normalized_variant = str(variant or "").strip()
        if normalized_variant not in spec.variants:
            # 只能运行由不可变 ExperimentSpec 列出的变体，调用方不能临时注入一个
            # 未被预算、数据集摘要或评审规则覆盖的模型配置。
            raise EvolutionLabRunnerError("variant is not registered in ExperimentSpec")
        dataset = self._ports.dataset_reader.read(dataset_digest=spec.dataset_digest)
        if not isinstance(dataset, Mapping):
            raise EvolutionLabRunnerError("DatasetReader must return a mapping")
        model_output = self._ports.model.run(
            variant=normalized_variant,
            dataset=dict(dataset),
            seed=spec.seed,
            budget=dict(spec.budget),
        )
        if not isinstance(model_output, Mapping):
            raise EvolutionLabRunnerError("RestrictedModelPort must return a mapping")
        verification = self._ports.verifier.verify(
            variant=normalized_variant,
            dataset=dict(dataset),
            model_output=dict(model_output),
        )
        if not isinstance(verification, Mapping):
            raise EvolutionLabRunnerError("LabVerifier must return a mapping")
        evidence_digest = str(verification.get("evidence_digest") or "").strip().lower()
        if len(evidence_digest) != 64 or any(ch not in "0123456789abcdef" for ch in evidence_digest):
            # verifier 未提供合规摘要时，基于本次模型输出和验证结果重新计算稳定摘要；
            # 它仅用于证据可追溯，不把原始实验内容提升为生产可消费的工件。
            evidence_digest = hashlib.sha256(
                _canonical({"variant": normalized_variant, "model": model_output, "verification": verification}).encode("utf-8")
            ).hexdigest()
        status = str(verification.get("status") or "INCOMPLETE").strip().upper()
        if status not in {"PASS", "FAIL", "INCOMPLETE"}:
            raise EvolutionLabRunnerError("LabVerifier returned unsupported status")
        run_id = f"{spec.experiment_id}:{normalized_variant}:{spec.seed}"
        receipt = ExperimentRunReceiptV1(
            run_id=run_id,
            spec_digest=spec.spec_digest,
            variant=normalized_variant,
            status=status,
            evidence_digest=evidence_digest,
            outcome=dict(verification.get("outcome") or {}),
            cost=dict(verification.get("cost") or model_output.get("cost") or {}),
            risk=dict(verification.get("risk") or {}),
            production_side_effects=False,
        )
        # 仓储只追加 receipt；PASS 表示实验结论，不是发布许可。任何生产提升必须在
        # 实验边界之外经由独立的治理、签名与激活流程重新授权。
        self._ports.repository.append_run(receipt)
        return receipt


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "DatasetReader",
    "EvolutionLabPortSet",
    "EvolutionLabRunner",
    "EvolutionLabRunnerError",
    "ExperimentRepository",
    "LabVerifier",
    "RestrictedModelPort",
]
