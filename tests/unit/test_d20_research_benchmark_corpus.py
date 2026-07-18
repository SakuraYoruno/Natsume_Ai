from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from host_brain.planning_search.research_benchmark_corpus import (
    FrozenResearchCorpusLoader,
    FrozenResearchCorpusManifest,
    ResearchBenchmarkCorpusError,
    ResearchCorpusManifestEntry,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _entry(
    *,
    source_id: str = "primary-notice",
    source_ref: str = "source://research/d20/primary-notice",
    relative_path: str = "fixtures/primary-notice.txt",
    content: bytes = b"Primary notice: the release date is 2026-07-18.\n",
    authority_tier: str = "primary",
) -> ResearchCorpusManifestEntry:
    return ResearchCorpusManifestEntry(
        source_id=source_id,
        source_ref=source_ref,
        relative_path=relative_path,
        content_digest=_digest(content),
        byte_count=len(content),
        authority_tier=authority_tier,
    )


def _loader(tmp_path: Path, *, entry: ResearchCorpusManifestEntry | None = None):  # noqa: ANN201
    selected = entry or _entry()
    target = tmp_path.joinpath(*Path(selected.relative_path).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    content_by_path = {
        "fixtures/primary-notice.txt": b"Primary notice: the release date is 2026-07-18.\n",
        "fixtures/independent-report.txt": b"Independent report: the release date is 2026-07-18.\n",
    }
    target.write_bytes(content_by_path.get(selected.relative_path, b""))
    manifest = FrozenResearchCorpusManifest(
        corpus_ref="corpus://research/d20/frozen-fixture",
        entries=(selected,),
    )
    return FrozenResearchCorpusLoader(repository_root=tmp_path, manifest=manifest), target


def _directory_link_or_skip(*, link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        symlink_error = exc
    if os.name == "nt":
        junction = subprocess.run(
            ("cmd", "/d", "/c", "mklink", "/J", str(link), str(target)),
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
        )
        if junction.returncode == 0:
            return
    pytest.skip(
        "directory symlink/junction unavailable on this test host: "
        f"{symlink_error}; junction stderr={junction.stderr if os.name == 'nt' else ''}"
    )


def test_loader_reads_manifest_listed_source_and_receipt_hides_local_locator(tmp_path: Path) -> None:
    loader, target = _loader(tmp_path)

    document = loader.load("primary-notice")

    manifest_receipt = loader.public_manifest_receipt()
    assert len(manifest_receipt["manifest_digest"]) == 64
    assert manifest_receipt["sources"][0]["source_id"] == "primary-notice"
    assert document.content == b"Primary notice: the release date is 2026-07-18.\n"
    assert document.to_public_receipt() == {
        "source_id": "primary-notice",
        "source_ref": "source://research/d20/primary-notice",
        "content_digest": _digest(document.content),
        "byte_count": len(document.content),
        "authority_tier": "primary",
    }
    serialized = repr(loader.public_manifest_receipt()) + repr(document.to_public_receipt())
    assert str(tmp_path) not in serialized
    assert str(target) not in serialized
    assert "relative_path" not in serialized
    assert "fixtures/primary-notice.txt" not in serialized
    assert "Primary notice" not in serialized


@pytest.mark.parametrize(
    "relative_path",
    (
        "/outside.txt",
        "C:/outside.txt",
        "../outside.txt",
        "fixtures/../outside.txt",
        r"fixtures\\outside.txt",
        "fixtures/outside.txt:private",
        "fixtures/./outside.txt",
    ),
)
def test_manifest_rejects_absolute_and_traversal_repository_paths(relative_path: str) -> None:
    with pytest.raises(ResearchBenchmarkCorpusError, match="relative_path"):
        _entry(relative_path=relative_path)


def test_loader_rejects_unlisted_source(tmp_path: Path) -> None:
    loader, _target = _loader(tmp_path)

    with pytest.raises(ResearchBenchmarkCorpusError, match="not listed"):
        loader.load("unlisted-source")


def test_loader_rejects_same_size_digest_drift(tmp_path: Path) -> None:
    loader, target = _loader(tmp_path)
    target.write_bytes(b"Primary notice: the release date is 2026-07-19.\n")

    with pytest.raises(ResearchBenchmarkCorpusError, match="digest drifted"):
        loader.load("primary-notice")


def test_loader_rejects_byte_count_drift_before_read(tmp_path: Path) -> None:
    loader, target = _loader(tmp_path)
    target.write_bytes(b"short\n")

    with pytest.raises(ResearchBenchmarkCorpusError, match="size or type drifted"):
        loader.load("primary-notice")


def test_loader_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-research-source.txt"
    content = b"outside primary source\n"
    outside.write_bytes(content)
    link = tmp_path / "fixtures" / "primary-notice.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable on this host: {exc}")
    entry = _entry(content=content)
    manifest = FrozenResearchCorpusManifest(
        corpus_ref="corpus://research/d20/frozen-fixture",
        entries=(entry,),
    )
    loader = FrozenResearchCorpusLoader(repository_root=tmp_path, manifest=manifest)

    with pytest.raises(ResearchBenchmarkCorpusError, match="forbidden link"):
        loader.load("primary-notice")


def test_manifest_rejects_file_scheme_and_ambiguous_mappings() -> None:
    with pytest.raises(ResearchBenchmarkCorpusError, match="file locator"):
        _entry(source_ref="file://C:/private/research.txt")

    first = _entry()
    duplicate_ref = _entry(
        source_id="independent-report",
        relative_path="fixtures/independent-report.txt",
    )
    with pytest.raises(ResearchBenchmarkCorpusError, match="source_ref values must be unique"):
        FrozenResearchCorpusManifest(
            corpus_ref="corpus://research/d20/frozen-fixture",
            entries=(first, duplicate_ref),
        )

    case_alias = _entry(
        source_id="independent-report",
        source_ref="source://research/d20/independent-report",
        relative_path="fixtures/PRIMARY-notice.txt",
        authority_tier="independent",
    )
    with pytest.raises(ResearchBenchmarkCorpusError, match="case aliases"):
        FrozenResearchCorpusManifest(
            corpus_ref="corpus://research/d20/frozen-fixture",
            entries=(first, case_alias),
        )


def test_loader_rejects_a_symlinked_parent_component(tmp_path: Path) -> None:
    outside_directory = tmp_path.parent / "outside-research-directory"
    outside_directory.mkdir(exist_ok=True)
    content = b"Primary notice: the release date is 2026-07-18.\n"
    (outside_directory / "primary-notice.txt").write_bytes(content)
    link = tmp_path / "fixtures"
    _directory_link_or_skip(link=link, target=outside_directory)
    entry = _entry(content=content)
    manifest = FrozenResearchCorpusManifest(
        corpus_ref="corpus://research/d20/frozen-fixture",
        entries=(entry,),
    )
    loader = FrozenResearchCorpusLoader(repository_root=tmp_path, manifest=manifest)

    with pytest.raises(ResearchBenchmarkCorpusError, match="forbidden link"):
        loader.load("primary-notice")


def test_loader_load_all_returns_verified_documents(tmp_path: Path) -> None:
    content = b"expected source\n"
    entry = _entry(content=content)
    target = tmp_path / "fixtures" / "primary-notice.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    loader = FrozenResearchCorpusLoader(
        repository_root=tmp_path,
        manifest=FrozenResearchCorpusManifest(
            corpus_ref="corpus://research/d20/frozen-fixture",
            entries=(entry,),
        ),
    )

    assert loader.load_all()[0].content == content
