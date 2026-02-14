# core/lifecycle.py
import time
import sys
import threading
import queue
from colorama import Fore, Style

from core.nerve_client import NerveCenter
from senses.ears import NatsumeEars
from actuators.mouth import NatsumeMouth
from actuators.avatar_ctrl import AvatarController
from actuators.speaker import AsyncSpeaker

input_queue = queue.Queue()

def ear_worker(ears, mute_event: threading.Event):
    print(f"{Fore.CYAN}👂 听觉神经线程已启动 (支持随时打断)...{Style.RESET_ALL}")
    while True:
        try:
            # ✅ 播放期间：不要录音、不要转写、不要入队
            if mute_event.is_set():
                time.sleep(0.05)
                continue

            user_text = ears.listen()
            if user_text and len(user_text.strip()) >= 2:
                input_queue.put(user_text)

            time.sleep(0.01)
        except Exception as e:
            print(f"❌ 耳朵线程出错: {e}")
            time.sleep(1)

def start_life_cycle():
    print(f"{Fore.CYAN}>>> 夏目蓝 V5.7 躯体系统 (Edge/Async) 初始化...{Style.RESET_ALL}")

    try:
        avatar = AvatarController()
        ears = NatsumeEars()
        mouth = NatsumeMouth()
        nerve = NerveCenter()

        # ✅ 新增：静音事件（播放时 set，结束 clear）
        mute_event = threading.Event()

        # ✅ speaker 接收 mute_event
        speaker = AsyncSpeaker(avatar, mute_event=mute_event)

    except Exception as e:
        print(f"{Fore.RED}❌ 初始化失败: {e}{Style.RESET_ALL}")
        return

    # ✅ ear_worker 也接收 mute_event
    t = threading.Thread(target=ear_worker, args=(ears, mute_event), daemon=True)
    t.start()

    print(f"\n{Fore.GREEN}✨ 躯体已苏醒，全双工模式就绪 (Host 5080)...{Style.RESET_ALL}")

    try:
        while True:
            try:
                user_text = input_queue.get(timeout=1)
                print(f"{Fore.YELLOW}Master: {user_text}{Style.RESET_ALL}")

                # 插话打断：只在“正在说话”时触发
                if speaker.is_busy:
                    print(f"{Fore.RED}⚡ 检测到插话，强行打断当前语音...{Style.RESET_ALL}")
                    speaker.stop()
                    avatar.trigger_hotkey("HK_Shy")

                avatar.start_thinking()
                ai_text = nerve.send_sensation(user_text)

                if ai_text:
                    print(f"{Fore.MAGENTA}🧠 中枢指令已达，准备合成...{Style.RESET_ALL}")

                    avatar.express_emotion(
                        mood=nerve.last_mood,
                        energy=nerve.last_energy,
                        context_tag=nerve.last_context
                    )

                    audio_path = mouth.speak(ai_text)

                    if audio_path:
                        if not input_queue.empty():
                            print(f"{Fore.RED}⚡ 合成期间检测到新指令，跳过播放。{Style.RESET_ALL}")
                        else:
                            speaker.play(audio_path)
                    else:
                        print(f"{Fore.RED}⚠️ TTS 合成失败{Style.RESET_ALL}")

            except queue.Empty:
                continue

    except KeyboardInterrupt:
        print(f"\n{Fore.RED}💤 夏目蓝已休眠。{Style.RESET_ALL}")
        speaker.stop()
