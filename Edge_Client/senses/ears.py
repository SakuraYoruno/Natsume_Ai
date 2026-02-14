# senses/ears.py
from faster_whisper import WhisperModel
import speech_recognition as sr
from config import WHISPER_MODEL_SIZE, DEVICE
from colorama import Fore, Style
from core.runtime_flags import AUDIO_PLAYING

import os
import time
import tempfile


class NatsumeEars:
    def __init__(self):
        print(f"{Fore.CYAN}👂 正在加载听觉模型 ({WHISPER_MODEL_SIZE})...{Style.RESET_ALL}")

        # Whisper 推理模型
        # 注：3070Ti + large-v3 用 float16 OK；若遇到不稳定可改为 "int8_float16"
        self.model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=DEVICE,
            compute_type="float16"
        )

        # SpeechRecognition 录音前端
        self.recognizer = sr.Recognizer()

        # 关键：不要把 energy_threshold 锁死得太低，否则更容易拾到外放/风扇
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True

        # 可选：更稳一点的动态阈值调节速度（默认也行）
        # self.recognizer.dynamic_energy_adjustment_damping = 0.15
        # self.recognizer.dynamic_energy_ratio = 1.5

        self.mic = sr.Microphone()

        # ✅ 启动时做一次“较长”环境校准，后续不再每次都 1s 校准
        with self.mic as source:
            print(f"{Fore.CYAN}🔧 正在进行一次性环境噪声校准...{Style.RESET_ALL}")
            self.recognizer.adjust_for_ambient_noise(source, duration=1.0)

        self._last_recalibrate_ts = time.time()
        print("👂 听觉系统就绪")

    def _maybe_recalibrate(self, source):
        """
        轻量校准：每隔一段时间做一次短校准，适配环境变化
        """
        now = time.time()
        # 每 60 秒做一次 0.15s 的快速校准（你可以按环境调成 30/120 秒）
        if now - self._last_recalibrate_ts >= 60:
            try:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.15)
            except:
                pass
            self._last_recalibrate_ts = now

    def listen(self):
        """录音并转文字（阻塞）"""
        with self.mic as source:

            # ✅ 播放期间不录音（两种策略二选一）
            # 策略A：直接跳过本轮（推荐，不卡线程）
            if AUDIO_PLAYING.is_set():
                time.sleep(0.05)
                return None

            # 策略B：等待播放结束再继续（会让 ear_worker 更“粘”）
            # AUDIO_PLAYING.wait()
            print(f"{Fore.MAGENTA}🎤 请说话...{Style.RESET_ALL}")

            # ✅ 不再每次 1s 校准；按时间做“短校准”
            self._maybe_recalibrate(source)

            try:
                # listen 参数建议：
                # - timeout: 没说话多久放弃
                # - phrase_time_limit: 单次最长语音，太长会拖慢对话
                audio_data = self.recognizer.listen(
                    source,
                    timeout=10,
                    phrase_time_limit=12
                )

                # ✅ 用临时文件，避免固定文件名冲突 & 确保清理
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                        tmp.write(audio_data.get_wav_data())
                        tmp_path = tmp.name

                    # Whisper 转写
                    segments, _ = self.model.transcribe(
                        tmp_path,
                        beam_size=5,
                        vad_filter=True,
                        vad_parameters=dict(
                            # 这几个参数比原来的更“抗外放/噪声”
                            min_silence_duration_ms=400,
                            speech_pad_ms=150
                        ),
                        initial_prompt="这是一段普通话对话，夏目蓝正在倾听。"
                    )

                    # 拼接文本
                    text_parts = []
                    for seg in segments:
                        if seg.text:
                            text_parts.append(seg.text)
                    text = "".join(text_parts).strip()

                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass

                # 过滤
                if not text:
                    return None

                # 常见“幻听/尾噪”过滤（按你的场景可继续加关键词）
                bad_phrases = ("谢谢观看", "字幕", "订阅", "点赞", "关注")
                if any(p in text for p in bad_phrases):
                    return None

                # 过短过滤（避免一些“啊/嗯/噪声”触发）
                if len(text.strip()) < 2:
                    return None

                print(f"👂 听到: {text}")
                return text

            except sr.WaitTimeoutError:
                return None
            except Exception as e:
                print(f"{Fore.RED}❌ 听觉错误: {e}{Style.RESET_ALL}")
                return None
