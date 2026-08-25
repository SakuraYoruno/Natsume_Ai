"""Edge 运行时的组合、启动与关闭辅助逻辑。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from natsume_contracts.edge.v1 import GatewayTransportStoreFactory

from edge_client.actuators.speech import SpeechPlaybackQueue
from edge_client.actuators.tts_engine import (
    AudioPlaybackDevice,
    SoVITSModelManager,
    TtsConfig,
    make_audio_playback_starter,
    make_sovits_synthesizer,
)
from edge_client.actuators.fanout_avatar_driver import FanoutAvatarDriver
from edge_client.actuators.vts_driver import ReconnectConfig, VTubeStudioDriver, VtsConfig
from edge_client.actuators.vrm_bridge_driver import VrmBridgeConfig, VrmBridgeDriver
from edge_client.config import (
    EdgeConfig,
    _env_get,
    _normalize_env_lookup,
    build_edge_runtime_statuses,
)
from edge_client.rpc.event_reporter import EdgeEventCache, EdgeEventReporter, build_http_transport
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import VoiceRpcContract
from edge_client.runtime.tribe_composition import (
    EdgeTribeComposition,
    compose_edge_tribe,
)
from edge_client.runtime.voice_pipeline import EdgeVoicePipeline
from edge_client.senses.asr import AsrConfig, AsrService, AudioStreamCapture, DeviceRecoveryConfig


LOGGER = logging.getLogger("Natsume.edge.runtime.bootstrap")
_BACKGROUND_STARTUP_COMPONENTS = frozenset({"tts", "vts", "asr"})


@dataclass
class EdgeRuntime:
    """聚合 RPC 网关所需的 Edge 组件，并统一其生命周期所有权。"""

    config: EdgeConfig
    voice_rpc: VoiceRpcContract
    speech_queue: SpeechPlaybackQueue
    event_reporter: EdgeEventReporter
    voice_pipeline: EdgeVoicePipeline
    resource_gate: VoiceResourceGate | None
    asr_service: AsrService | None
    tts_model: SoVITSModelManager | None
    tts_playback_device: AudioPlaybackDevice | None
    vts_driver: VTubeStudioDriver | VrmBridgeDriver | None
    tribe_composition: EdgeTribeComposition
    gateway_transport_store_factory: GatewayTransportStoreFactory | None = None
    startup_threads: list[threading.Thread] = field(default_factory=list, repr=False)

    @property
    def asr(self) -> AsrService | None:
        return self.asr_service

    @property
    def tts(self) -> SoVITSModelManager | None:
        return self.tts_model

    @property
    def vts(self) -> VTubeStudioDriver | VrmBridgeDriver | None:
        return self.vts_driver

    @property
    def rpc_client(self) -> VoiceRpcContract:
        return self.voice_rpc

    @property
    def tribe_bridge(self):
        return self.tribe_composition.bridge

    @property
    def tribe_registry(self):
        return self.tribe_composition.registry

    def service_statuses(self) -> dict[str, dict[str, Any]]:
        return build_edge_runtime_statuses(self.config)

    def redacted_config_snapshot(self) -> dict[str, Any]:
        # 健康/诊断视图只暴露“密钥是否配置”和其引用，不返回 HMAC 原值，避免
        # 配置快照成为 RPC 认证秘密的泄露面。
        row = self.config.as_dict()
        row["hmac_shared_key_ref"] = self.config.hmac_shared_key_ref
        row["hmac_shared_key_set"] = bool(self.config.hmac_shared_key)
        return row

    def close(self) -> dict[str, Any]:
        """Best-effort graceful shutdown of runtime-owned resources."""

        # 关闭收据按资源分别记录：某个设备停不下来不应阻止其余资源回收，同时也
        # 不能把“已尽力请求停止”误报为所有物理副作用都已终止。
        outcome = {
            "asr_stopped": True,
            "tts_playback_stopped": True,
            "tts_unloaded": True,
            "vts_disconnected": True,
            "gateway_stopped": True,
            "errors": [],
        }

        if self.asr_service is not None:
            # 先停输入采集，防止关闭中继续生成新语音事件；随后才释放播放、模型和
            # 形象连接，保持不再接收新工作后再撤销执行依赖的顺序。
            try:
                stop_result = self.asr_service.stop_session()
                outcome["asr_stopped"] = bool(stop_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["asr_stopped"] = False
                outcome["errors"].append(f"asr stop failed: {exc}")

        if self.tts_playback_device is not None:
            try:
                stop_result = self.tts_playback_device.stop()
                outcome["tts_playback_stopped"] = bool(stop_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["tts_playback_stopped"] = False
                outcome["errors"].append(f"tts playback stop failed: {exc}")

        if self.tts_model is not None:
            try:
                self.tts_model.unload()
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["tts_unloaded"] = False
                outcome["errors"].append(f"tts unload failed: {exc}")

        if self.vts_driver is not None:
            try:
                disconnect_result = self.vts_driver.disconnect()
                outcome["vts_disconnected"] = bool(disconnect_result.success)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                outcome["vts_disconnected"] = False
                outcome["errors"].append(f"vts disconnect failed: {exc}")

        gateway_instance = getattr(self, "_gateway_instance", None)
        if gateway_instance is not None:
            try:
                gateway_instance.stop()
            except Exception as exc:  # pragma: no cover
                outcome["gateway_stopped"] = False
                outcome["errors"].append(f"gateway stop failed: {exc}")

        return outcome


def build_edge_runtime(
    config: EdgeConfig,
    *,
    gateway_transport_store_factory: GatewayTransportStoreFactory | None = None,
) -> EdgeRuntime:
    """从已校验的 Edge 配置构造运行时组件。"""

    # 这里是设备依赖的唯一装配点：配置仅决定可用组件，具体 RPC handler 不自行
    # 创建模型或驱动，从而使资源门控、健康状态和关闭顺序可被统一管理。
    env_lookup = _normalize_env_lookup(os.environ)
    tribe_composition = compose_edge_tribe()
    # 资源门在所有实时组件之间共享，必须在构建 ASR/TTS/RPC 前确定；单个处理器
    # 不自行创建门控实例，避免对同一 GPU/队列给出相互矛盾的准入判断。
    resource_gate = (
        VoiceResourceGate(
            vram_soft_limit_ratio=_parse_float(
                _env_get(env_lookup, "EDGE_RESOURCE_GATE_VRAM_SOFT_LIMIT_RATIO"),
                default=0.90,
                min_value=0.1,
                max_value=1.0,
            ),
        )
        if config.resource_gate_enabled
        else None
    )
    voice_rpc = VoiceRpcContract(
        resource_gate=resource_gate,
    )

    tts_cfg = TtsConfig(
        backend=_as_text(_env_get(env_lookup, "EDGE_TTS_BACKEND"), "gpt_sovits"),
        model_path=_as_text(_env_get(env_lookup, "EDGE_TTS_MODEL_PATH"), "models/gpt_sovits"),
        device=_as_text(_env_get(env_lookup, "EDGE_TTS_DEVICE"), "cuda"),
        sample_rate=_parse_int(
            _env_get(env_lookup, "EDGE_TTS_SAMPLE_RATE"),
            default=32000,
            min_value=8000,
            max_value=96000,
        ),
        max_text_length=_parse_int(
            _env_get(env_lookup, "EDGE_TTS_MAX_TEXT_LENGTH"),
            default=500,
            min_value=1,
            max_value=20000,
        ),
        synth_timeout_s=_parse_float(
            _env_get(env_lookup, "EDGE_TTS_SYNTH_TIMEOUT_S"),
            default=30.0,
            min_value=0.1,
            max_value=600.0,
        ),
        ref_audio_path=_as_optional_text(_env_get(env_lookup, "EDGE_TTS_REF_AUDIO_PATH")),
        ref_text=_as_optional_text(_env_get(env_lookup, "EDGE_TTS_REF_TEXT")),
        voice_bank_path=_as_optional_text(_env_get(env_lookup, "EDGE_TTS_VOICE_BANK_PATH")),
        language=_as_text(_env_get(env_lookup, "EDGE_TTS_LANGUAGE"), "zh"),
    )
    tts_playback_device_index = _parse_optional_int(
        _env_get(env_lookup, "EDGE_TTS_PLAYBACK_DEVICE_INDEX"),
        min_value=0,
        max_value=4096,
    )
    tts_model = SoVITSModelManager(config=tts_cfg) if config.tts_enabled else None
    playback_device = (
        AudioPlaybackDevice(
            sample_rate=tts_cfg.sample_rate,
            device_index=tts_playback_device_index,
        )
        if config.tts_enabled
        else None
    )
    synthesizer = make_sovits_synthesizer(tts_model) if tts_model is not None else None
    playback_starter = (
        make_audio_playback_starter(playback_device)
        if playback_device is not None
        else None
    )
    speech_queue = SpeechPlaybackQueue(
        rpc=voice_rpc,
        resource_gate=resource_gate,
        synthesizer=synthesizer,
        playback_starter=playback_starter,
    )

    avatar_backend = str(getattr(config, "avatar_backend", "vts") or "vts").strip().lower()
    if avatar_backend == "vrm":
        primary_vrm_driver = VrmBridgeDriver(
            config=VrmBridgeConfig(
                host=str(getattr(config, "vrm_bridge_host", "127.0.0.1") or "127.0.0.1"),
                port=int(getattr(config, "vrm_bridge_port", 18770) or 18770),
                api_timeout_s=float(getattr(config, "vrm_bridge_timeout_s", 1.5) or 1.5),
                model_path=getattr(config, "vrm_model_path", None),
            )
        )
        mirror_drivers: list[Any] = []
        if _parse_bool(_env_get(env_lookup, "EDGE_VRM_MIRROR_VTS_ENABLED"), default=True):
            mirror_vts_cfg = VtsConfig(
                host=_as_text(_env_get(env_lookup, "EDGE_VTS_HOST"), "localhost"),
                port=_parse_int(
                    _env_get(env_lookup, "EDGE_VTS_PORT"),
                    default=8001,
                    min_value=1,
                    max_value=65535,
                ),
                plugin_name=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_NAME"), "NatsumeAi"),
                plugin_developer=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_DEVELOPER"), "Natsume"),
                token_path=_as_text(_env_get(env_lookup, "EDGE_VTS_TOKEN_PATH"), ".vts_token"),
                api_timeout_s=_parse_float(
                    _env_get(env_lookup, "EDGE_VTS_MIRROR_API_TIMEOUT_S"),
                    default=0.6,
                    min_value=0.1,
                    max_value=30.0,
                ),
                mouth_param_candidates=_parse_csv_tuple(
                    _env_get(env_lookup, "EDGE_VTS_MOUTH_PARAM_CANDIDATES"),
                    default=("MouthOpen", "ParamMouthOpenY"),
                ),
                mouth_volume_param_candidates=_parse_csv_tuple(
                    _env_get(env_lookup, "EDGE_VTS_MOUTH_VOLUME_PARAM_CANDIDATES"),
                    default=("VoiceVolumePlusMouthOpen", "VoiceVolume"),
                ),
                voice_silence_param_candidates=_parse_csv_tuple(
                    _env_get(env_lookup, "EDGE_VTS_VOICE_SILENCE_PARAM_CANDIDATES"),
                    default=("VoiceSilence",),
                ),
                reconnect=ReconnectConfig(
                    max_attempts=1,
                    base_delay_s=0.2,
                    max_delay_s=0.6,
                    auto_reconnect=False,
                ),
            )
            mirror_drivers.append(VTubeStudioDriver(config=mirror_vts_cfg))
        vts_driver = FanoutAvatarDriver(primary=primary_vrm_driver, mirrors=mirror_drivers)
    else:
        vts_cfg = VtsConfig(
            host=_as_text(_env_get(env_lookup, "EDGE_VTS_HOST"), "localhost"),
            port=_parse_int(
                _env_get(env_lookup, "EDGE_VTS_PORT"),
                default=8001,
                min_value=1,
                max_value=65535,
            ),
            plugin_name=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_NAME"), "NatsumeAi"),
            plugin_developer=_as_text(_env_get(env_lookup, "EDGE_VTS_PLUGIN_DEVELOPER"), "Natsume"),
            token_path=_as_text(_env_get(env_lookup, "EDGE_VTS_TOKEN_PATH"), ".vts_token"),
            api_timeout_s=_parse_float(
                _env_get(env_lookup, "EDGE_VTS_API_TIMEOUT_S"),
                default=5.0,
                min_value=0.1,
                max_value=300.0,
            ),
            mouth_param_candidates=_parse_csv_tuple(
                _env_get(env_lookup, "EDGE_VTS_MOUTH_PARAM_CANDIDATES"),
                default=("MouthOpen", "ParamMouthOpenY"),
            ),
            mouth_volume_param_candidates=_parse_csv_tuple(
                _env_get(env_lookup, "EDGE_VTS_MOUTH_VOLUME_PARAM_CANDIDATES"),
                default=("VoiceVolumePlusMouthOpen", "VoiceVolume"),
            ),
            voice_silence_param_candidates=_parse_csv_tuple(
                _env_get(env_lookup, "EDGE_VTS_VOICE_SILENCE_PARAM_CANDIDATES"),
                default=("VoiceSilence",),
            ),
            reconnect=ReconnectConfig(
                max_attempts=_parse_int(
                    _env_get(env_lookup, "EDGE_VTS_RECONNECT_MAX_ATTEMPTS"),
                    default=10,
                    min_value=1,
                    max_value=1000,
                ),
                base_delay_s=_parse_float(
                    _env_get(env_lookup, "EDGE_VTS_RECONNECT_BASE_DELAY_S"),
                    default=1.0,
                    min_value=0.1,
                    max_value=300.0,
                ),
                max_delay_s=_parse_float(
                    _env_get(env_lookup, "EDGE_VTS_RECONNECT_MAX_DELAY_S"),
                    default=16.0,
                    min_value=0.1,
                    max_value=3600.0,
                ),
                auto_reconnect=_parse_bool(
                    _env_get(env_lookup, "EDGE_VTS_RECONNECT_AUTO"),
                    default=True,
                ),
            ),
        )
        vts_driver = VTubeStudioDriver(config=vts_cfg)
    event_report_host_url = _as_optional_text(_env_get(env_lookup, "EDGE_EVENT_REPORT_HOST_URL"))
    event_reporter = EdgeEventReporter(
        transport_fn=(
            build_http_transport(
                host_url=event_report_host_url,
                timeout_s=_parse_float(
                    _env_get(env_lookup, "EDGE_EVENT_REPORT_TIMEOUT_S"),
                    default=3.0,
                    min_value=0.1,
                    max_value=300.0,
                ),
            )
            if event_report_host_url
            else None
        ),
        cache=EdgeEventCache(
            max_events=_parse_int(
                _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_CACHE_EVENTS"),
                default=1000,
                min_value=1,
                max_value=1_000_000,
            ),
            max_bytes=_parse_int(
                _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_CACHE_BYTES"),
                default=10 * 1024 * 1024,
                min_value=1024,
                max_value=1024 * 1024 * 1024,
            ),
        ),
        max_retry=_parse_int(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_RETRY_ATTEMPTS"),
            default=5,
            min_value=1,
            max_value=1000,
        ),
        base_delay_s=_parse_float(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_BASE_RETRY_DELAY_S"),
            default=1.0,
            min_value=0.0,
            max_value=3600.0,
        ),
        max_delay_s=_parse_float(
            _env_get(env_lookup, "EDGE_EVENT_REPORT_MAX_RETRY_DELAY_S"),
            default=30.0,
            min_value=0.0,
            max_value=3600.0,
        ),
    )
    voice_pipeline = EdgeVoicePipeline(
        voice_rpc=voice_rpc,
        speech_queue=speech_queue,
        event_reporter=event_reporter,
        vts_driver=vts_driver,
        resource_gate=resource_gate,
        tribe_bridge=tribe_composition.bridge,
    )
    if playback_device is not None:
        # 设备回调只把“播放已结束”的事实交回队列状态机；不在音频线程中启动下一
        # 个请求或执行 Host I/O，避免回调重入改变播放顺序。
        playback_device.set_on_playback_finished(lambda: voice_pipeline.complete_playback())
    asr_cfg = AsrConfig(
        model_size=_as_text(_env_get(env_lookup, "EDGE_ASR_MODEL_SIZE"), "large-v3"),
        device=_as_text(_env_get(env_lookup, "EDGE_ASR_DEVICE"), "cuda"),
        compute_type=_as_text(_env_get(env_lookup, "EDGE_ASR_COMPUTE_TYPE"), "float16"),
        beam_size=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_BEAM_SIZE"),
            default=5,
            min_value=1,
            max_value=64,
        ),
        sample_rate=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_SAMPLE_RATE"),
            default=16000,
            min_value=8000,
            max_value=96000,
        ),
        channels=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_CHANNELS"),
            default=1,
            min_value=1,
            max_value=8,
        ),
        chunk_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_CHUNK_DURATION_S"),
            default=0.5,
            min_value=0.05,
            max_value=10.0,
        ),
        vad_threshold=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_VAD_THRESHOLD"),
            default=0.5,
            min_value=0.0,
            max_value=1.0,
        ),
        silence_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_SILENCE_DURATION_S"),
            default=0.8,
            min_value=0.05,
            max_value=10.0,
        ),
        min_speech_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_MIN_SPEECH_DURATION_S"),
            default=0.3,
            min_value=0.05,
            max_value=10.0,
        ),
        max_speech_duration_s=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_MAX_SPEECH_DURATION_S"),
            default=30.0,
            min_value=1.0,
            max_value=600.0,
        ),
        precheck_min_peak=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_PRECHECK_MIN_PEAK"),
            default=0.01,
            min_value=0.0,
            max_value=1.0,
        ),
        precheck_min_rms=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_PRECHECK_MIN_RMS"),
            default=0.002,
            min_value=0.0,
            max_value=1.0,
        ),
        use_vad_filter=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_USE_VAD_FILTER"),
            default=True,
        ),
        vad_retry_without_filter_on_empty=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_VAD_RETRY_WITHOUT_FILTER_ON_EMPTY"),
            default=True,
        ),
        vad_retry_min_peak=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_VAD_RETRY_MIN_PEAK"),
            default=0.015,
            min_value=0.0,
            max_value=1.0,
        ),
        language=_as_text(_env_get(env_lookup, "EDGE_ASR_LANGUAGE"), "zh"),
        initial_prompt=_as_optional_text(_env_get(env_lookup, "EDGE_ASR_INITIAL_PROMPT")),
        barge_in_on_speech_enabled=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_BARGE_IN_ON_SPEECH_ENABLED"),
            default=True,
        ),
        barge_in_min_active_chunks=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_BARGE_IN_MIN_ACTIVE_CHUNKS"),
            default=2,
            min_value=1,
            max_value=20,
        ),
        barge_in_cooldown_ms=_parse_int(
            _env_get(env_lookup, "EDGE_ASR_BARGE_IN_COOLDOWN_MS"),
            default=1200,
            min_value=0,
            max_value=60000,
        ),
        barge_in_min_peak=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_BARGE_IN_MIN_PEAK"),
            default=0.035,
            min_value=0.0,
            max_value=1.0,
        ),
        barge_in_min_rms=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_BARGE_IN_MIN_RMS"),
            default=0.006,
            min_value=0.0,
            max_value=1.0,
        ),
        playback_echo_suppression_enabled=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_PLAYBACK_ECHO_SUPPRESSION_ENABLED"),
            default=True,
        ),
        speaker_verification_enabled=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_SPEAKER_VERIFICATION_ENABLED"),
            default=False,
        ),
        speaker_verification_fail_open=_parse_bool(
            _env_get(env_lookup, "EDGE_ASR_SPEAKER_VERIFICATION_FAIL_OPEN"),
            default=True,
        ),
        speaker_verification_min_confidence=_parse_float(
            _env_get(env_lookup, "EDGE_ASR_SPEAKER_VERIFICATION_MIN_CONFIDENCE"),
            default=0.68,
            min_value=0.0,
            max_value=1.0,
        ),
        speaker_profile_path=_as_optional_text(_env_get(env_lookup, "EDGE_ASR_SPEAKER_PROFILE_PATH")),
        recovery=DeviceRecoveryConfig(
            max_attempts=_parse_int(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_MAX_ATTEMPTS"),
                default=5,
                min_value=1,
                max_value=100,
            ),
            base_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_BASE_DELAY_S"),
                default=1.0,
                min_value=0.1,
                max_value=120.0,
            ),
            max_delay_s=_parse_float(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_MAX_DELAY_S"),
                default=8.0,
                min_value=0.1,
                max_value=600.0,
            ),
            auto_recover=_parse_bool(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_AUTO"),
                default=True,
            ),
            try_alternate_devices=_parse_bool(
                _env_get(env_lookup, "EDGE_ASR_RECOVERY_TRY_ALTERNATE_DEVICES"),
                default=True,
            ),
        ),
    )
    asr_audio_capture = AudioStreamCapture(
        sample_rate=asr_cfg.sample_rate,
        channels=asr_cfg.channels,
        chunk_duration_s=asr_cfg.chunk_duration_s,
        device_index=_parse_optional_int(
            _env_get(env_lookup, "EDGE_ASR_DEVICE_INDEX"),
            min_value=0,
            max_value=4096,
        ),
    )
    asr_service = (
        AsrService(
            rpc=voice_rpc,
            config=asr_cfg,
            audio_capture=asr_audio_capture,
        )
        if config.asr_enabled
        else None
    )
    if asr_service is not None:
        asr_service.set_rpc_event_callback(voice_pipeline.on_asr_rpc_result)
        asr_service.set_playback_state_provider(lambda: voice_pipeline.speech_queue.active_playback)
        asr_service.set_barge_in_callback(
            lambda request_id, payload, ts_ms: voice_pipeline.barge_in(
                request_id=request_id,
                payload=payload,
                ts_ms=ts_ms,
            )
        )

    return EdgeRuntime(
        config=config,
        voice_rpc=voice_rpc,
        speech_queue=speech_queue,
        event_reporter=event_reporter,
        voice_pipeline=voice_pipeline,
        resource_gate=resource_gate,
        asr_service=asr_service,
        tts_model=tts_model,
        tts_playback_device=playback_device,
        vts_driver=vts_driver,
        tribe_composition=tribe_composition,
        gateway_transport_store_factory=gateway_transport_store_factory,
    )


def bootstrap(
    config: EdgeConfig,
    *,
    gateway_transport_store_factory: GatewayTransportStoreFactory | None = None,
) -> EdgeRuntime:
    """M45 entrypoint alias for runtime assembly."""

    runtime = build_edge_runtime(
        config,
        gateway_transport_store_factory=gateway_transport_store_factory,
    )

    # 将 EdgeConfig 存入 runtime，供 fastapi_app.py 的 wx handler 读取
    runtime._edge_config = config  # type: ignore[attr-defined]

    # 初始化 WxDbService（若配置了解密输出目录）
    if config.wx_decrypt_output_dir:
        try:
            from edge_client.services.wx_db_service import WxDbService
            wx_svc = WxDbService(
                config.wx_decrypt_output_dir,
                poll_watermark_file=None,  # 水位线由 Gateway 的 WxDbWatcher 管理
            )
            runtime._wx_db_service = wx_svc  # type: ignore[attr-defined]
            LOGGER.info("WxDbService 已初始化: %s", config.wx_decrypt_output_dir)
        except Exception as exc:
            LOGGER.warning("WxDbService 初始化失败（非致命）: %s", exc)

    # T-127：初始化 EdgeTribeRegistry，将 vts_driver 注入到 SomaticAgent
    # SomaticAgent 接收来自 host_brain LimbicAgent 的下行情绪指令后，
    # 通过 vts_driver.set_expression() 驱动 VTubeStudio 表情，实现身心联动闭环
    try:
        _tribe_registry = runtime.tribe_registry
        somatic = _tribe_registry.somatic
        if hasattr(somatic, "set_vts_driver"):
            somatic.set_vts_driver(runtime.vts_driver)
        LOGGER.info("EdgeTribeRegistry 已初始化，SomaticAgent vts_driver 注入完成")
    except Exception as exc:
        LOGGER.warning("EdgeTribeRegistry 初始化失败（非致命）: %s", exc)

    # 面部捕捉感知轮询：每 5s 读取 VTS 面捕参数，推断用户表情并上报给 host_brain
    # SomaticAgent.poll_user_face_capture() → publish_emotion_inference_updated
    # → host_brain LimbicAgent/MirrorNeuronAgent 感知用户情绪状态
    try:
        import threading as _threading  # noqa: PLC0415

        _face_capture_interval_s = float(
            _env_get(dict(os.environ), "EDGE_FACE_CAPTURE_POLL_INTERVAL_S") or 5.0
        )
        _face_capture_enabled = _parse_bool(
            _env_get(dict(os.environ), "EDGE_FACE_CAPTURE_ENABLED"),
            default=True,
        )

        if _face_capture_enabled and runtime.vts_driver is not None:
            def _face_capture_poll_loop() -> None:
                """面部捕捉感知轮询循环，在后台线程中持续运行。"""
                import time as _time  # noqa: PLC0415
                _somatic = _tribe_registry.somatic

                while True:
                    try:
                        if _somatic is not None and hasattr(_somatic, "poll_user_face_capture"):
                            _somatic.poll_user_face_capture()
                    except Exception as _exc:
                        LOGGER.debug("面部捕捉轮询异常（非致命）: %s", _exc)
                    _time.sleep(_face_capture_interval_s)

            _face_poll_thread = _threading.Thread(
                target=_face_capture_poll_loop,
                name="edge-face-capture-poll",
                daemon=True,
            )
            _face_poll_thread.start()
            LOGGER.info(
                "面部捕捉感知轮询已启动: interval=%.1fs thread=%s",
                _face_capture_interval_s, _face_poll_thread.name,
            )
        else:
            LOGGER.info(
                "面部捕捉感知轮询已跳过: enabled=%s vts_driver=%s",
                _face_capture_enabled, runtime.vts_driver is not None,
            )
    except Exception as exc:
        LOGGER.warning("面部捕捉感知轮询启动失败（非致命）: %s", exc)

    # 启动 Unified Message Gateway（若已启用）
    if config.gateway_enabled:
        try:
            from edge_client.gateway.bootstrap import GatewayConfig, start_gateway

            gateway_host_url = _as_optional_text(_env_get(dict(os.environ), "EDGE_EVENT_REPORT_HOST_URL"))
            if not gateway_host_url:
                gateway_host_url = f"http://{config.rpc_host}:18080"
                LOGGER.warning(
                    "Unified Message Gateway host_url not set by EDGE_EVENT_REPORT_HOST_URL; fallback=%s",
                    gateway_host_url,
                )
            watermark_file = str(
                (Path.cwd() / "runtime" / "edge" / "wx_gateway_watermarks.json").resolve()
            )

            gw_config = GatewayConfig(
                host_url=gateway_host_url,
                hmac_key=config.hmac_shared_key,
                bind_host=config.rpc_host,
                enabled_platforms=config.gateway_platforms,
                wx_db_storage_dir=config.wx_db_storage_dir or "",
                wx_decrypt_output_dir=config.wx_decrypt_output_dir or "",
                wx_key_cache_path=config.wx_key_cache_path,
                wx_decrypt_refresh_interval_s=config.wx_decrypt_refresh_interval_s,
                wx_self_wxid=config.wx_self_wxid,
                wx_watermark_file=watermark_file,
                wx_poll_interval_s=config.wx_poll_interval_s,
                gateway_port=config.gateway_port,
                qq_onebot_api_base=config.qq_onebot_api_base,
                qq_self_id=config.qq_self_id,
                feishu_app_id=config.feishu_app_id,
                feishu_app_secret=config.feishu_app_secret,
                feishu_encrypt_key=config.feishu_encrypt_key,
                feishu_verification_token=config.feishu_verification_token,
                wecom_corp_id=config.wecom_corp_id,
                wecom_corp_secret=config.wecom_corp_secret,
                wecom_agent_id=config.wecom_agent_id,
                wecom_token=config.wecom_token,
                wecom_encoding_aes_key=config.wecom_encoding_aes_key,
                # LINE Messaging API
                line_channel_access_token=config.line_channel_access_token,
                line_channel_secret=config.line_channel_secret,
            )
            gw_instance = start_gateway(
                gw_config,
                tribe_bridge=runtime.tribe_bridge,
                transport_store_factory=runtime.gateway_transport_store_factory,
            )
            runtime._gateway_instance = gw_instance  # type: ignore[attr-defined]
            LOGGER.info("Unified Message Gateway 已启动，端口: %d", config.gateway_port)
        except Exception as exc:
            LOGGER.warning("Unified Message Gateway 启动失败（非致命）: %s", exc)

    return runtime


async def startup(runtime: EdgeRuntime) -> None:
    """Best-effort runtime startup.

    Heavy edge peripherals such as TTS / VTS / ASR can take several seconds to
    load models, connect devices, or probe local drivers.  If we await them
    inline inside FastAPI lifespan, the RPC health endpoint never starts
    listening until all of them finish.  That makes the edge service look dead
    even though the gateway thread has already started.

    To keep RPC responsive, schedule those heavy startups in background daemon
    threads and let the health endpoint reflect their eventual readiness.
    """

    # 重型外围设备放入 daemon 线程，HTTP 健康端点可先启动并反映其最终就绪状态；
    # 这不是宣布设备可用，失败仍会在各组件健康信息中显式呈现。
    runtime.startup_threads.clear()
    for name, component in _iter_lifecycle_components(runtime):
        if name in _BACKGROUND_STARTUP_COMPONENTS:
            thread = threading.Thread(
                target=_invoke_lifecycle_sync,
                args=(component, "startup"),
                kwargs={"name": name},
                name=f"edge-{name}-startup",
                daemon=True,
            )
            runtime.startup_threads.append(thread)
            thread.start()
            LOGGER.info(
                "edge lifecycle startup scheduled in background for component=%s thread=%s",
                name,
                thread.name,
            )
            continue
        await _invoke_lifecycle(component, "startup", name=name)


async def shutdown(runtime: EdgeRuntime) -> None:
    """尽力关闭运行时；单个组件失败会记录并继续其余清理。"""

    # 按启动依赖的反序关闭，先阻止后台启动线程继续触及将被释放的设备资源。
    for thread in list(getattr(runtime, "startup_threads", []) or []):
        if thread.is_alive():
            thread.join(timeout=0.5)
    runtime.startup_threads.clear()

    components = _iter_lifecycle_components(runtime)
    for name, component in reversed(components):
        await _invoke_lifecycle(component, "shutdown", name=name)

    if callable(getattr(runtime, "close", None)):
        try:
            runtime.close()
        except Exception as exc:  # pragma: no cover - defensive guard
            LOGGER.exception("runtime close failed: %s", exc)


def _iter_lifecycle_components(runtime: EdgeRuntime) -> list[tuple[str, Any]]:
    ordered_names = (
        "tts",
        "vts",
        "voice_pipeline",
        "asr",
        "resource_gate",
        "rpc_client",
    )
    rows: list[tuple[str, Any]] = []
    for name in ordered_names:
        component = getattr(runtime, name, None)
        if component is None:
            continue
        rows.append((name, component))
    return rows


async def _invoke_lifecycle(component: Any, method_name: str, *, name: str) -> None:
    method = getattr(component, method_name, None)
    if not callable(method):
        return
    try:
        outcome = method()
        if inspect.isawaitable(outcome):
            await outcome
    except Exception as exc:  # pragma: no cover - defensive lifecycle guard
        LOGGER.exception(
            "edge lifecycle %s failed for component=%s: %s",
            method_name,
            name,
            exc,
        )


def _invoke_lifecycle_sync(component: Any, method_name: str, *, name: str) -> None:
    method = getattr(component, method_name, None)
    if not callable(method):
        return
    try:
        outcome = method()
        if inspect.isawaitable(outcome):
            asyncio.run(outcome)
    except Exception as exc:  # pragma: no cover - defensive lifecycle guard
        LOGGER.exception(
            "edge lifecycle %s failed for component=%s: %s",
            method_name,
            name,
            exc,
        )


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _parse_csv_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    text = _as_optional_text(value)
    if text is None:
        return tuple(default)
    parsed = tuple(part.strip() for part in text.split(",") if part.strip())
    return parsed or tuple(default)


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)


def _parse_optional_int(value: Any, *, min_value: int, max_value: int) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _parse_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    if value is None or str(value).strip() == "":
        return float(default)
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return float(default)
    return min(max(parsed, min_value), max_value)
