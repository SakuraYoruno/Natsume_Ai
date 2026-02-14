# actuators/speaker.py
import os
import threading
import queue
import winsound
from colorama import Fore, Style
from core.runtime_flags import AUDIO_PLAYING

class AsyncSpeaker:
    def __init__(self, avatar_controller, mute_event=None):
        self.queue = queue.Queue()
        self.avatar = avatar_controller
        self.is_busy = False
        self.mute_event = mute_event  # ✅ 新增：播放期间静音“耳朵”的开关

        self._thread = threading.Thread(target=self._playback_worker, daemon=True)
        self._thread.start()

    def play(self, audio_path):
        if audio_path and os.path.exists(audio_path):
            self.queue.put(audio_path)
            print(f"{Fore.CYAN}>>> 音频已入列: {os.path.basename(audio_path)}{Style.RESET_ALL}")

    def stop(self):
        # 1) 清队列
        with self.queue.mutex:
            self.queue.queue.clear()

        # 2) 立刻停声
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
            print(f"{Fore.RED}🛑 [Speaker] 播放已强行打断 (Winsound){Style.RESET_ALL}")
        except Exception as e:
            print(f"❌ 打断失败: {e}")

        # ✅ 3) 立刻解除“静音耳朵”状态（否则 ear_worker 可能一直不听）
        if self.mute_event:
            self.mute_event.clear()

        # 4) 立刻重置状态（闭嘴）
        self.is_busy = False
        self.avatar.stop_talking()

    def _playback_worker(self):
        while True:
            try:
                audio_path = self.queue.get(timeout=1)

                if audio_path:
                    self.is_busy = True

                    # ✅ 播放前：静音耳朵
                    if self.mute_event:
                        self.mute_event.set()

                    self.avatar.start_talking()
                    print(f"{Fore.GREEN}▶️ 夏目蓝正在说话: {os.path.basename(audio_path)}{Style.RESET_ALL}")

                    try:
                        winsound.PlaySound(audio_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                    except Exception as e:
                        print(f"❌ 播放底层错误: {e}")

                    # 播放结束：闭嘴 + 解锁
                    self.avatar.stop_talking()
                    self.is_busy = False

                    # ✅ 播放后：解除静音耳朵
                    if self.mute_event:
                        self.mute_event.clear()

                    # 清理文件
                    try:
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                    except:
                        pass

                self.queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"❌ [Speaker] 播放线程异常: {e}")
                self.is_busy = False
                if self.mute_event:
                    self.mute_event.clear()
