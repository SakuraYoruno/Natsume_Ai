from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ResearchCitationSpanV1,
    ResearchCorpusBindingV1,
)
from host_brain.planning_search.research_benchmark_corpus import (
    FrozenResearchCorpusManifest,
    ResearchCorpusManifestEntry,
)
from host_brain.planning_search.research_benchmark_verifier import (
    RESEARCH_CITATION_VERIFIER_ID,
    ResearchCitationVerificationError,
    ResearchCitationVerifier,
)
from host_brain.contracts.planning_search.research_domain_models import (
    ResearchSourceDescriptor,
    ResearchTaskSnapshot,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture(tmp_path: Path):  # noqa: ANN201
    content = "第一份来源：Natsume 的发布日期是 2026-07-18。\n".encode("utf-8")
    target = tmp_path / "corpus" / "primary.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    source = ResearchSourceDescriptor(
        source_id="primary",
        source_ref="artifact://research/d20/primary",
        content_digest=_digest(content),
        authority_tier="primary",
        relevance_score=0.9,
        estimated_read_cost=0.1,
    )
    snapshot = ResearchTaskSnapshot(
        task_ref="artifact://research-workloads/d20/verifier",
        corpus_ref="artifact://research-corpus/d20/verifier",
        objective="Verify UTF-8 citation evidence from a frozen corpus.",
        sources=(source,),
        constraints={"network": "disabled", "production_influence": False},
    )
    manifest = FrozenResearchCorpusManifest(
        corpus_ref=snapshot.corpus_ref,
        entries=(
            ResearchCorpusManifestEntry(
                source_id=source.source_id,
                source_ref=source.source_ref,
                relative_path="corpus/primary.txt",
                content_digest=source.content_digest,
                byte_count=len(content),
                authority_tier=source.authority_tier,
            ),
        ),
    )
    binding = ResearchCorpusBindingV1.from_snapshot(
        snapshot,
        manifest_digest=manifest.manifest_digest,
    )
    verifier = ResearchCitationVerifier(
        repository_root=tmp_path,
        manifest=manifest,
        snapshot=snapshot,
        binding=binding,
    )
    return verifier, target, content, binding


def test_d20b_verifier_reopens_exact_bound_corpus_and_validates_utf8_span(tmp_path: Path) -> None:
    verifier, target, content, binding = _fixture(tmp_path)
    cited = "发布日期是 2026-07-18".encode("utf-8")
    start = content.index(cited)
    citation = ResearchCitationSpanV1(
        source_id="primary",
        start_byte=start,
        end_byte=start + len(cited),
        span_digest=_digest(cited),
    )

    receipt = verifier.verify(execution_id="research-execution-1", citations=(citation,))

    assert receipt.execution_id == "research-execution-1"
    assert receipt.citation_count == 1
    assert receipt.cited_span_count == 1
    assert receipt.citation_coverage == 1.0
    assert receipt.citation_validity == 1.0
    assert len(receipt.evidence_digest) == 64
    assert verifier.binding_digest == binding.binding_digest
    serialized = repr(receipt.to_dict())
    assert RESEARCH_CITATION_VERIFIER_ID not in serialized
    assert str(target) not in serialized
    assert "第一份来源" not in serialized


def test_d20b_verifier_fails_closed_for_drift_after_construction(tmp_path: Path) -> None:
    verifier, target, content, _binding = _fixture(tmp_path)
    cited = "发布日期".encode("utf-8")
    start = content.index(cited)
    citation = ResearchCitationSpanV1(
        source_id="primary",
        start_byte=start,
        end_byte=start + len(cited),
        span_digest=_digest(cited),
    )
    target.write_bytes(content.replace(b"2026-07-18", b"2026-07-19"))

    with pytest.raises(ResearchCitationVerificationError, match="cannot be reopened"):
        verifier.verify(execution_id="research-execution-2", citations=(citation,))


def test_d20b_verifier_rejects_a_span_that_splits_a_utf8_character(tmp_path: Path) -> None:
    verifier, _target, content, _binding = _fixture(tmp_path)
    character = "发".encode("utf-8")
    start = content.index(character) + 1
    citation = ResearchCitationSpanV1(
        source_id="primary",
        start_byte=start,
        end_byte=start + 1,
        span_digest=_digest(content[start : start + 1]),
    )

    with pytest.raises(ResearchCitationVerificationError, match="UTF-8"):
        verifier.verify(execution_id="research-execution-3", citations=(citation,))


def test_d20b_verifier_rejects_a_snapshot_manifest_binding_mismatch(tmp_path: Path) -> None:
    _verifier, _target, _content, binding = _fixture(tmp_path)
    source = ResearchSourceDescriptor(
        source_id="primary",
        source_ref="artifact://research/d20/primary",
        content_digest="f" * 64,
        authority_tier="primary",
        relevance_score=0.9,
        estimated_read_cost=0.1,
    )
    snapshot = ResearchTaskSnapshot(
        task_ref="artifact://research-workloads/d20/verifier",
        corpus_ref="artifact://research-corpus/d20/verifier",
        objective="Different immutable source metadata.",
        sources=(source,),
        constraints={"network": "disabled", "production_influence": False},
    )
    manifest = FrozenResearchCorpusManifest(
        corpus_ref=snapshot.corpus_ref,
        entries=(
            ResearchCorpusManifestEntry(
                source_id="primary",
                source_ref="artifact://research/d20/primary",
                relative_path="unused.txt",
                content_digest="f" * 64,
                byte_count=0,
                authority_tier="primary",
            ),
        ),
    )

    with pytest.raises(ResearchCitationVerificationError, match="do not match exactly"):
        ResearchCitationVerifier(
            repository_root=tmp_path,
            manifest=manifest,
            snapshot=snapshot,
            binding=binding,
        )


def test_d20b_verifier_rejects_manifest_sources_outside_the_snapshot_binding(
    tmp_path: Path,
) -> None:
    _verifier, _target, _content, binding = _fixture(tmp_path)
    source = ResearchSourceDescriptor(
        source_id="primary",
        source_ref="artifact://research/d20/primary",
        content_digest="a" * 64,
        authority_tier="primary",
        relevance_score=0.9,
        estimated_read_cost=0.1,
    )
    snapshot = ResearchTaskSnapshot(
        task_ref="artifact://research-workloads/d20/verifier",
        corpus_ref="artifact://research-corpus/d20/verifier",
        objective="Check manifest source metadata independently.",
        sources=(source,),
        constraints={"network": "disabled", "production_influence": False},
    )
    manifest = FrozenResearchCorpusManifest(
        corpus_ref=snapshot.corpus_ref,
        entries=(
            ResearchCorpusManifestEntry(
                source_id="primary",
                source_ref="artifact://research/d20/changed-primary",
                relative_path="unused.txt",
                content_digest="a" * 64,
                byte_count=0,
                authority_tier="primary",
            ),
        ),
    )
    matching_snapshot_binding = ResearchCorpusBindingV1.from_snapshot(
        snapshot,
        manifest_digest=manifest.manifest_digest,
    )

    with pytest.raises(ResearchCitationVerificationError, match="manifest sources"):
        ResearchCitationVerifier(
            repository_root=tmp_path,
            manifest=manifest,
            snapshot=snapshot,
            binding=matching_snapshot_binding,
        )
