from __future__ import annotations

import os

from edge_client.senses import asr


def test_asr_cuda_dll_dirs_register_valid_paths_once(
    monkeypatch,
    tmp_path,
) -> None:
    first = tmp_path / "torch-lib"
    second = tmp_path / "ctranslate2"
    first.mkdir()
    second.mkdir()
    handles: list[object] = []
    registered: list[str] = []

    monkeypatch.setenv(
        "EDGE_ASR_CUDA_DLL_DIRS",
        os.pathsep.join((str(first), str(second), str(tmp_path / "missing"))),
    )
    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_HANDLES", handles)
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_PATHS", set())

    def add_dll_directory(path: str) -> object:
        registered.append(path)
        return object()

    monkeypatch.setattr(asr.os, "add_dll_directory", add_dll_directory, raising=False)

    asr._ensure_asr_cuda_dll_dirs()
    asr._ensure_asr_cuda_dll_dirs()

    assert registered == [str(first.resolve()), str(second.resolve())]
    assert len(handles) == 2
    path_parts = os.environ["PATH"].split(os.pathsep)
    assert path_parts[:2] == [str(second.resolve()), str(first.resolve())]
    assert path_parts.count(str(first.resolve())) == 1
    assert path_parts.count(str(second.resolve())) == 1


def test_asr_cuda_dll_dirs_empty_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_ASR_CUDA_DLL_DIRS", raising=False)
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_PATHS", set())
    monkeypatch.setattr(
        asr.os,
        "add_dll_directory",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected call")),
        raising=False,
    )

    asr._ensure_asr_cuda_dll_dirs()

    assert asr._ASR_DLL_DIRECTORY_PATHS == set()


def test_asr_cuda_dll_dirs_ignore_empty_path_segments(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "EDGE_ASR_CUDA_DLL_DIRS",
        os.pathsep.join(("", " ", "")),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(asr, "_ASR_DLL_DIRECTORY_PATHS", set())
    monkeypatch.setattr(
        asr.os,
        "add_dll_directory",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected call")),
        raising=False,
    )

    asr._ensure_asr_cuda_dll_dirs()

    assert asr._ASR_DLL_DIRECTORY_PATHS == set()
