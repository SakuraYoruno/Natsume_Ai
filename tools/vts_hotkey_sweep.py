from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge_client.actuators.vts_driver import (  # noqa: E402
    ReconnectConfig,
    VTubeStudioDriver,
    VtsConfig,
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            os.environ.setdefault(key, value)


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in str(raw or "").split(",") if item.strip())


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _config_from_env() -> VtsConfig:
    return VtsConfig(
        host=str(os.environ.get("EDGE_VTS_HOST") or "localhost").strip() or "localhost",
        port=_int_env("EDGE_VTS_PORT", 8001),
        plugin_name=str(os.environ.get("EDGE_VTS_PLUGIN_NAME") or "NatsumeAi").strip() or "NatsumeAi",
        plugin_developer=str(os.environ.get("EDGE_VTS_PLUGIN_DEVELOPER") or "Natsume").strip() or "Natsume",
        token_path=str(os.environ.get("EDGE_VTS_TOKEN_PATH") or ".vts_token").strip() or ".vts_token",
        api_timeout_s=_float_env("EDGE_VTS_API_TIMEOUT_S", 5.0),
        mouth_param_candidates=_csv_env("EDGE_VTS_MOUTH_PARAM_CANDIDATES", "MouthOpen,ParamMouthOpenY"),
        mouth_volume_param_candidates=_csv_env(
            "EDGE_VTS_MOUTH_VOLUME_PARAM_CANDIDATES",
            "VoiceVolumePlusMouthOpen,VoiceVolume",
        ),
        voice_silence_param_candidates=_csv_env("EDGE_VTS_VOICE_SILENCE_PARAM_CANDIDATES", "VoiceSilence"),
        reconnect=ReconnectConfig(
            max_attempts=_int_env("EDGE_VTS_RECONNECT_MAX_ATTEMPTS", 10),
            base_delay_s=_float_env("EDGE_VTS_RECONNECT_BASE_DELAY_S", 1.0),
            max_delay_s=_float_env("EDGE_VTS_RECONNECT_MAX_DELAY_S", 16.0),
            auto_reconnect=_bool_env("EDGE_VTS_RECONNECT_AUTO", True),
        ),
    )


def _emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List or sweep VTube Studio hotkeys in the current model.")
    parser.add_argument("--list", action="store_true", help="Only list current model hotkeys.")
    parser.add_argument("--dwell-s", type=float, default=1.2, help="Seconds to wait after each hotkey.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max hotkeys to trigger.")
    parser.add_argument("--start", type=int, default=0, help="Zero-based index to start from.")
    parser.add_argument("--reset-final", action="store_true", help="Try reset_to_neutral after the sweep.")
    args = parser.parse_args(argv)

    _load_dotenv(ROOT / ".env")
    driver = VTubeStudioDriver(_config_from_env())
    connect = driver.connect()
    _emit({"event": "connect", "result": connect.as_dict()})
    if not connect.success:
        return 2
    try:
        health = driver.health()
        hotkeys = list(health.get("hotkey_catalog") or [])
        if not hotkeys:
            hotkeys = list(getattr(driver, "_hotkey_catalog", []) or [])
        _emit({"event": "hotkey_catalog", "count": len(hotkeys), "hotkeys": hotkeys})
        if args.list:
            return 0

        start = max(0, int(args.start))
        selected = hotkeys[start:]
        if args.limit and args.limit > 0:
            selected = selected[: int(args.limit)]
        dwell_s = max(0.15, float(args.dwell_s))
        results: list[dict[str, Any]] = []
        for offset, hotkey in enumerate(selected):
            index = start + offset
            hotkey_id = str(hotkey.get("id") or "").strip()
            name = str(hotkey.get("name") or "").strip()
            action = str(hotkey.get("action") or "").strip()
            if not hotkey_id:
                continue
            _emit({"event": "trigger_start", "index": index, "id": hotkey_id, "name": name, "action": action})
            started = time.time()
            result = driver.trigger_hotkey(hotkey_id)
            elapsed_ms = int(round((time.time() - started) * 1000))
            row = {
                "index": index,
                "id": hotkey_id,
                "name": name,
                "action": action,
                "success": bool(result.success),
                "reason_code": result.reason_code,
                "detail": result.detail,
                "elapsed_ms": elapsed_ms,
            }
            results.append(row)
            _emit({"event": "trigger_result", **row})
            time.sleep(dwell_s)

        if args.reset_final:
            reset = driver.reset_to_neutral()
            _emit({"event": "reset_final", "result": reset.as_dict()})
        failures = [row for row in results if not row["success"]]
        _emit({"event": "summary", "tested": len(results), "success": len(results) - len(failures), "failed": len(failures), "failures": failures})
        return 0 if not failures else 1
    finally:
        driver.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
