from __future__ import annotations

import argparse
import json
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m edge_client.actuators.tts_subprocess_helper",
        description="Fresh-process GPT-SoVITS synth helper for edge fallback.",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--sample-rate", type=int, default=32000)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--text", required=True)
    parser.add_argument("--text-language", default="zh")
    parser.add_argument("--ref-audio-path", default="")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--voice-bank-path", default="")
    parser.add_argument("--emotion-tag", default="")
    parser.add_argument("--voice-profile", default="")
    parser.add_argument("--emotion-intensity", type=float, default=None)
    parser.add_argument("--speed-factor", type=float, default=None)
    parser.add_argument("--energy-level", type=float, default=None)
    parser.add_argument("--pitch-semitones", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # 子进程以仓库根目录作为导入锚点，避免由调用方工作目录决定加载到哪份 edge 代码；
    # 该 helper 只承载一次降级合成，生命周期由父端进程边界隔离。
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.chdir(repo_root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import numpy as np

    from edge_client.actuators.tts_engine import SoVITSModelManager, TtsConfig

    cfg = TtsConfig(
        model_path=str(args.model_path),
        device=str(args.device),
        sample_rate=int(args.sample_rate),
        ref_audio_path=str(args.ref_audio_path or "").strip() or None,
        ref_text=str(args.ref_text or "").strip() or None,
        voice_bank_path=str(args.voice_bank_path or "").strip() or None,
        language=str(args.language or "zh"),
    )
    mgr = SoVITSModelManager(cfg)
    try:
        # 所有情绪/韵律参数仍通过 TTS 引擎的受控入口归一化，helper 不直接操作模型，
        # 以维持常驻运行时与一次性回退路径相同的语义和安全界限。
        result = mgr.synthesize(
            str(args.text),
            language=str(args.text_language or "zh"),
            emotion_tag=str(args.emotion_tag or "").strip() or None,
            voice_profile=str(args.voice_profile or "").strip() or None,
            emotion_intensity=args.emotion_intensity,
            speed_factor=args.speed_factor,
            energy_level=args.energy_level,
            pitch_semitones=args.pitch_semitones,
        )
    finally:
        # 一次性辅助进程无状态复用价值；显式关闭可及时释放 GPU/原生句柄，
        # 即使合成失败也不会把资源泄漏到下一次回退请求。
        # The helper is intentionally one-shot.  Explicit cleanup also makes
        # this safe if a future backend uses a child process or native handle.
        mgr.shutdown()
    if not result.success or result.audio_data is None:
        print(
            json.dumps(
                {
                    "success": False,
                    "reason_code": result.reason_code,
                    "detail": result.detail,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    np.savez_compressed(
        str(args.output),
        audio=np.asarray(result.audio_data, dtype=np.float32),
        sample_rate=np.int32(result.sample_rate),
    )
    print(
        json.dumps(
            {
                "success": True,
                "detail": result.detail,
                "sample_rate": int(result.sample_rate),
                "duration_s": float(result.duration_s),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
