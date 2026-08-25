from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge_client.actuators.vts_driver import (  # noqa: E402
    ReconnectConfig,
    VTubeStudioDriver,
    VtsConfig,
    _discover_current_model_mouth_bindings,
    _set_vts_parameters,
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


def _connect_driver() -> VTubeStudioDriver:
    _load_dotenv(ROOT / ".env")
    driver = VTubeStudioDriver(_config_from_env())
    result = driver.connect()
    print(json.dumps({"connect": result.as_dict()}, ensure_ascii=False, indent=2))
    if not result.success:
        raise SystemExit(2)
    return driver


def _candidate_params(driver: VTubeStudioDriver, extra: list[str]) -> list[str]:
    health = driver.health()
    mouth = dict(health.get("mouth_parameters") or {})
    candidates = [
        *list(mouth.get("model_open_inputs") or []),
        *list(mouth.get("mouth") or []),
        *list(mouth.get("volume") or []),
        "MouthOpen",
        "VoiceVolume",
        "VoiceVolumePlusMouthOpen",
        "VoiceA",
        "VoiceI",
        "VoiceU",
        "VoiceE",
        "VoiceO",
        *extra,
    ]
    available = set(str(item) for item in (mouth.get("available") or []) if str(item).strip())
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        param = str(item or "").strip()
        if not param or param in seen:
            continue
        if available and param not in available:
            continue
        seen.add(param)
        out.append(param)
    return out


def _print_bindings(driver: VTubeStudioDriver) -> None:
    print(json.dumps({"model_binding": _discover_current_model_mouth_bindings()}, ensure_ascii=False, indent=2))
    health = driver.health()
    print(
        json.dumps(
            {"mouth_parameters": dict(health.get("mouth_parameters") or {})},
            ensure_ascii=False,
            indent=2,
        )
    )


def _hold_params(
    driver: VTubeStudioDriver,
    *,
    params: list[str],
    value: float,
    hold_s: float,
    fps: float,
) -> None:
    interval = max(0.01, 1.0 / max(1.0, fps))
    deadline = time.monotonic() + max(0.1, hold_s)
    sent = 0
    payload = {param: value for param in params}
    if "VoiceSilence" in params:
        payload["VoiceSilence"] = max(0.0, min(1.0, 1.0 - value))
    while time.monotonic() < deadline:
        _set_vts_parameters(vts=driver._vts, parameter_values=payload, timeout_s=driver._config.api_timeout_s)
        sent += 1
        time.sleep(interval)
    _set_vts_parameters(
        vts=driver._vts,
        parameter_values={param: 0.0 for param in params},
        timeout_s=driver._config.api_timeout_s,
    )
    print(json.dumps({"held": params, "value": value, "hold_s": hold_s, "fps": fps, "frames_sent": sent}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", action="store_true")
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--sequence", action="store_true")
    parser.add_argument("--params", default="")
    parser.add_argument("--value", type=float, default=1.0)
    parser.add_argument("--hold-s", type=float, default=3.0)
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args(argv)

    driver = _connect_driver()
    try:
        if args.bindings or (not args.hold and not args.sequence):
            _print_bindings(driver)
        extra = [item.strip() for item in str(args.params or "").split(",") if item.strip()]
        params = _candidate_params(driver, extra)
        if args.hold:
            selected = extra or params[:4]
            print(json.dumps({"hold_params": selected}, ensure_ascii=False))
            _hold_params(driver, params=selected, value=args.value, hold_s=args.hold_s, fps=args.fps)
        if args.sequence:
            for param in params:
                print(json.dumps({"probe_param": param, "watch_now": True}, ensure_ascii=False))
                _hold_params(driver, params=[param], value=args.value, hold_s=args.hold_s, fps=args.fps)
                time.sleep(0.6)
    finally:
        driver.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
