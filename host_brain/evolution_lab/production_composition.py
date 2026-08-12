"""可参与晋升判定的 Evolution Lab 运行的类型化应用内组合。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from host_brain.evolution_lab.docker_executor import (
    TrustedDockerSandboxExecutor,
)
from host_brain.evolution_lab.golden_experiments import (
    GoldenExperimentService,
)
from host_brain.evolution_lab.golden_verifier import (
    GoldenExperimentVerifier,
)
from host_brain.evolution_lab.redacted_trace import (
    TrustedCompositeDatasetReader,
    TrustedTraceRedactionService,
)
from host_brain.evolution_lab.trusted_runner import (
    TrustedEvolutionLabPorts,
    TrustedEvolutionLabRunner,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_redacted_dataset_repository import (
    SQLiteTrustedRedactedDatasetReader,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)


@dataclass(frozen=True, slots=True)
class TrustedEvolutionLabComposition:
    """封闭的 Lab 图：不暴露生产写入器或激活端口。"""

    dataset_reader: TrustedCompositeDatasetReader
    redacted_dataset_reader: SQLiteTrustedRedactedDatasetReader
    trace_redaction: TrustedTraceRedactionService
    executor: TrustedDockerSandboxExecutor
    verifier: GoldenExperimentVerifier
    spec_registry: SQLiteEvolutionLabV2Ledger
    evidence_ledger: SQLiteTrustedEvolutionLabLedger
    runner: TrustedEvolutionLabRunner
    golden_experiments: GoldenExperimentService

    def close(self) -> None:
        # Repositories use short-lived SQLite connections per operation.
        return None


def build_trusted_evolution_lab_composition(
    *,
    db_path: str | Path,
    runtime_root: str | Path,
    image_reference: str,
) -> TrustedEvolutionLabComposition:
    """仅在中央迁移器完成 0122/0138-0140 后构造。"""

    # Lab 只能读取已脱敏数据、调用受信沙箱并写入实验收据。将这些端口在此显式
    # 固定，避免实验代码经依赖注入意外取得生产策略激活或业务写入权限。
    spec_registry = SQLiteEvolutionLabV2Ledger(db_path)
    evidence_ledger = SQLiteTrustedEvolutionLabLedger(db_path)
    redacted_dataset_reader = SQLiteTrustedRedactedDatasetReader(db_path)
    dataset_reader = TrustedCompositeDatasetReader(
        redacted_reader=redacted_dataset_reader,
    )
    trace_redaction = TrustedTraceRedactionService(
        ledger=evidence_ledger,
        reader=redacted_dataset_reader,
    )
    executor = TrustedDockerSandboxExecutor(
        runtime_root=runtime_root,
        image_reference=image_reference,
    )
    verifier = GoldenExperimentVerifier(dataset_reader=dataset_reader)
    runner = TrustedEvolutionLabRunner(
        ports=TrustedEvolutionLabPorts(
            dataset_reader=dataset_reader,
            executor=executor,
            verifier=verifier,
            spec_registry=spec_registry,
            operation_ledger=evidence_ledger,
        )
    )
    golden = GoldenExperimentService(
        runner=runner,
        executor=executor,
        verifier=verifier,
        ledger=evidence_ledger,
    )
    return TrustedEvolutionLabComposition(
        dataset_reader=dataset_reader,
        redacted_dataset_reader=redacted_dataset_reader,
        trace_redaction=trace_redaction,
        executor=executor,
        verifier=verifier,
        spec_registry=spec_registry,
        evidence_ledger=evidence_ledger,
        runner=runner,
        golden_experiments=golden,
    )


__all__ = [
    "TrustedEvolutionLabComposition",
    "build_trusted_evolution_lab_composition",
]
