from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_edge_startup_uses_embedded_component_health_without_fake_ports() -> None:
    source = (ROOT / "scripts" / "runtime" / "start_edge.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "Get-EmbeddedEdgeComponent" in source
    assert 'ComponentName "asr"' in source
    assert 'ComponentName "tts"' in source
    assert "$null -ne $standaloneAsrPid" in source
    assert "$null -ne $standaloneTtsPid" in source
    assert (
        "absence of 17801 is not evidence that embedded ASR crashed"
        in source
    )
