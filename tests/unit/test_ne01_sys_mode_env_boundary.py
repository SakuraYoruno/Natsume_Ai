from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_consumers_do_not_read_or_write_host_sys_mode_environment() -> None:
    for relative in (
        "host_brain/app/chat_generation_payload.py",
        "host_brain/app/llm/routing_matrix.py",
        "host_brain/lifecycle_runtime/runtime_snapshot.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert 'os.environ.get("HOST_SYS_MODE")' not in source
        assert '_os.environ.get("HOST_SYS_MODE")' not in source
        assert 'os.environ["HOST_SYS_MODE"] =' not in source
