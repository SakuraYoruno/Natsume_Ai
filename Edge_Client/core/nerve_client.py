import requests
import json
import time
from config import NERVE_URL


class NerveCenter:
    def __init__(self):
        print(f"🔗 [Nerve]正在连接中枢神经: {NERVE_URL} ...")
        # 用于缓存最新的身体状态
        self.last_mood = 50
        self.last_energy = 100
        self.last_context = "NORMAL"

    def send_sensation(self, text):
        """
        将听到的文本发送给 Host (大脑)
        并等待返回指令 (回复文本 + 状态)
        """
        if not text: return None

        print(f"👂 [Ear] 听到: {text}")

        payload = {
            "text": text,  # 核心感知
            "source": "edge_mic",  # 来源标识
            "timestamp": time.time()
        }

        try:
            # 发送给 Bridge Server
            resp = requests.post(NERVE_URL, json=payload, timeout=30)

            if resp.status_code == 200:
                data = resp.json()
                response_text = data.get("response", "")
                state = data.get("state", {})

                # 捕获并保存状态/上下文 (供 lifecycle 调用)
                self.last_mood = state.get("mood", 50)
                self.last_energy = state.get("energy", 100)
                self.last_context = data.get("context", "NORMAL")

                print(f"🧠 [Brain] 指令: {response_text}")
                if state:
                    # 打印一下当前的上下文状态，方便调试
                    ctx_str = f" | Context: {self.last_context}" if self.last_context != "NORMAL" else ""
                    print(f"   (Mood: {self.last_mood:.1f} | Energy: {self.last_energy:.1f}{ctx_str})")

                return response_text
            else:
                print(f"❌ [Nerve] 中枢拒绝连接: {resp.status_code}")
                return None

        except Exception as e:
            print(f"❌ [Nerve] 神经信号中断: {e}")
            return None