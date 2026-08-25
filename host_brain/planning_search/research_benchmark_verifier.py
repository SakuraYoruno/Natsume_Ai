"""Independent D20b citation verification over a frozen Research corpus.

The executor may propose citation byte ranges, but it must not be able to
declare those ranges valid.  This verifier owns a separate loader
construction for every verification, rebinds the public planning snapshot to
the corpus manifest, and validates each UTF-8 byte span against freshly read
source bytes.  It intentionally has no model, browser, TaskGraph, provider,
or production-promotion dependency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from host_brain.core.runtime_helpers import canonical_json_sha256
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ResearchCitationSpanV1,
    ResearchCorpusBindingV1,
    ResearchVerifierReceiptV1,
)
from host_brain.contracts.planning_search.research_domain_models import ResearchTaskSnapshot
from host_brain.planning_search.research_benchmark_corpus import (
    FrozenResearchCorpusLoader,
    FrozenResearchCorpusManifest,
    ResearchBenchmarkCorpusError,
    ResearchCorpusDocument,
)


RESEARCH_CITATION_VERIFIER_ID = "FRESH_FROZEN_CORPUS_UTF8_SPAN_V1"
"""Stable identity recorded in evidence digests for the D20b verifier."""


class ResearchCitationVerificationError(ValueError):
    """The independent Research citation verifier rejected submitted evidence."""


class ResearchCitationVerifier:
    """Verify citations by reopening a snapshot-bound frozen corpus.

    The constructor receives the trusted experiment root, manifest and frozen
    planning snapshot rather than an executor-owned loader or source map.  A
    new :class:`FrozenResearchCorpusLoader` is constructed inside every
    :meth:`verify` invocation, so a caller cannot pass a cached executor
    document and have it treated as independent verification.
    """

    def __init__(
        self,
        *,
        repository_root: Path,
        manifest: FrozenResearchCorpusManifest,
        snapshot: ResearchTaskSnapshot,
        binding: ResearchCorpusBindingV1,
    ) -> None:
        if not isinstance(manifest, FrozenResearchCorpusManifest):
            raise TypeError("manifest must be FrozenResearchCorpusManifest")
        if not isinstance(snapshot, ResearchTaskSnapshot):
            raise TypeError("snapshot must be ResearchTaskSnapshot")
        if not isinstance(binding, ResearchCorpusBindingV1):
            raise TypeError("binding must be ResearchCorpusBindingV1")
        self._repository_root = Path(repository_root)
        self._manifest = manifest
        self._snapshot = snapshot
        self._binding = binding
        self._assert_exact_binding()

    @property
    def binding_digest(self) -> str:
        """Return the immutable corpus binding verified by this instance."""

        return self._binding.binding_digest

    def verify(
        self,
        *,
        execution_id: str,
        citations: Sequence[ResearchCitationSpanV1],
    ) -> ResearchVerifierReceiptV1:
        """Return a byte-accurate citation receipt or fail closed.

        ``citations`` is only a proposal.  Each source ID is resolved through
        a fresh manifest-bound loader, and the span digest plus UTF-8
        boundaries are computed from reopened bytes.  No source body, local
        path, or model output is retained in the resulting evidence digest.
        """

        normalized_execution_id = _execution_id(execution_id)
        normalized_citations = _citations(citations)
        loader = self._fresh_loader()
        documents: dict[str, ResearchCorpusDocument] = {}
        for citation in normalized_citations:
            document = documents.get(citation.source_id)
            if document is None:
                try:
                    document = loader.load(citation.source_id)
                except ResearchBenchmarkCorpusError as exc:
                    raise ResearchCitationVerificationError(
                        "citation source cannot be reopened from the frozen corpus"
                    ) from exc
                self._assert_document_matches_binding(document)
                documents[citation.source_id] = document
            _verify_span(document=document, citation=citation)

        citation_count = len(normalized_citations)
        public_documents = [
            documents[source_id].to_public_receipt() for source_id in sorted(documents)
        ]
        evidence_digest = canonical_json_sha256(
            {
                "schema": "natsume.research.citation-verifier.v1",
                "verifier_id": RESEARCH_CITATION_VERIFIER_ID,
                "execution_id": normalized_execution_id,
                "corpus_binding_digest": self._binding.binding_digest,
                "documents": public_documents,
                "citations": [citation.to_dict() for citation in normalized_citations],
            },
            allow_nan=True,
        )
        score = 1.0 if citation_count else 0.0
        return ResearchVerifierReceiptV1(
            execution_id=normalized_execution_id,
            citation_count=citation_count,
            cited_span_count=citation_count,
            citation_coverage=score,
            citation_validity=score,
            evidence_digest=evidence_digest,
        )

    def _fresh_loader(self) -> FrozenResearchCorpusLoader:
        """Build a verifier-owned loader instead of accepting executor state."""

        try:
            return FrozenResearchCorpusLoader(
                repository_root=self._repository_root,
                manifest=self._manifest,
            )
        except ResearchBenchmarkCorpusError as exc:
            raise ResearchCitationVerificationError(
                "frozen corpus verifier cannot construct its independent loader"
            ) from exc

    def _assert_exact_binding(self) -> None:
        expected = ResearchCorpusBindingV1.from_snapshot(
            self._snapshot,
            manifest_digest=self._manifest.manifest_digest,
        )
        if expected.to_dict() != self._binding.to_dict():
            raise ResearchCitationVerificationError(
                "planning snapshot, corpus manifest and binding do not match exactly"
            )
        manifest_sources = tuple(
            (
                entry.source_id,
                entry.source_ref,
                entry.content_digest,
                entry.authority_tier,
            )
            for entry in self._manifest.entries
        )
        binding_sources = tuple(
            zip(
                self._binding.source_ids,
                self._binding.source_refs,
                self._binding.source_digests,
                self._binding.authority_tiers,
                strict=True,
            )
        )
        if manifest_sources != binding_sources:
            raise ResearchCitationVerificationError(
                "corpus manifest sources do not match the verified binding"
            )

    def _assert_document_matches_binding(self, document: ResearchCorpusDocument) -> None:
        try:
            index = self._binding.source_ids.index(document.source_id)
        except ValueError as exc:
            raise ResearchCitationVerificationError(
                "citation source is outside the verified corpus binding"
            ) from exc
        expected = (
            self._binding.source_refs[index],
            self._binding.source_digests[index],
            self._binding.authority_tiers[index],
        )
        observed = (
            document.source_ref,
            document.content_digest,
            document.authority_tier,
        )
        if observed != expected:
            raise ResearchCitationVerificationError(
                "reopened citation source drifted from the verified corpus binding"
            )


def _execution_id(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise ResearchCitationVerificationError("execution_id must be non-empty and bounded")
    return result


def _citations(value: Sequence[ResearchCitationSpanV1]) -> tuple[ResearchCitationSpanV1, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ResearchCitationVerificationError("citations must be an explicit sequence")
    result = tuple(value or ())
    if len(result) > 128:
        raise ResearchCitationVerificationError("citations exceed the D20b verifier limit")
    if not all(isinstance(item, ResearchCitationSpanV1) for item in result):
        raise ResearchCitationVerificationError("citations must contain ResearchCitationSpanV1 values")
    identities = {
        (item.source_id, item.start_byte, item.end_byte, item.span_digest)
        for item in result
    }
    if len(identities) != len(result):
        raise ResearchCitationVerificationError("duplicate citation spans are not valid evidence")
    return tuple(
        sorted(
            result,
            key=lambda item: (item.source_id, item.start_byte, item.end_byte, item.span_digest),
        )
    )


def _verify_span(*, document: ResearchCorpusDocument, citation: ResearchCitationSpanV1) -> None:
    content = document.content
    if citation.end_byte > len(content):
        raise ResearchCitationVerificationError("citation byte range exceeds the reopened source")
    try:
        content.decode("utf-8")
        content[: citation.start_byte].decode("utf-8")
        content[citation.start_byte : citation.end_byte].decode("utf-8")
        content[citation.end_byte :].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchCitationVerificationError(
            "citation offsets do not delimit UTF-8 byte boundaries"
        ) from exc
    observed_digest = hashlib.sha256(
        content[citation.start_byte : citation.end_byte]
    ).hexdigest()
    if observed_digest != citation.span_digest:
        raise ResearchCitationVerificationError("citation span digest does not match reopened bytes")


__all__ = [
    "RESEARCH_CITATION_VERIFIER_ID",
    "ResearchCitationVerificationError",
    "ResearchCitationVerifier",
]
