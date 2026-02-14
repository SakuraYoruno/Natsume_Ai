import requests
import os
import time
from config import TTS_API_URL, TEMP_AUDIO_DIR, TTS_REF_AUDIO_PATH, TTS_PROMPT_TEXT, TTS_PROMPT_LANG, TTS_TARGET_LANG
from colorama import Fore, Style


class NatsumeMouth:
    def __init__(self):
        # 已经在 config.py 中设为 http://127.0.0.1:9880/tts
        self.api_url = TTS_API_URL

    def speak(self, text):
        if not text:
            return None

        print(f"{Fore.YELLOW}👄 正在通过笔记本调用 TTS 合成...{Style.RESET_ALL}")

        # 构造绝对路径文件名
        filename = f"reply_{int(time.time())}.wav"
        output_path = os.path.join(TEMP_AUDIO_DIR, filename)

        # 【核心修复】将 text_language 改为 text_lang 以匹配 api_v2.py 的预期
        payload = {
            "text": text,
            "text_lang": TTS_TARGET_LANG if TTS_TARGET_LANG else "ja",
            "ref_audio_path": TTS_REF_AUDIO_PATH,
            "prompt_text": TTS_PROMPT_TEXT,
            "prompt_lang": TTS_PROMPT_LANG if TTS_PROMPT_LANG else "ja",
            "top_k": 5,
            "top_p": 1,
            "temperature": 1,
            "media_type": "wav"
        }

        try:
            # 向 http://127.0.0.1:9880/tts 发起请求
            response = requests.post(self.api_url, json=payload, timeout=60)

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)

                # 再次确认文件是否生成成功
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    print(f"✅ 语音合成成功: {output_path}")
                    return output_path
                return None
            else:
                # 这里会打印出导致 400 错误的具体参数问题
                print(f"{Fore.RED}❌ TTS 接口报错 ({response.status_code}): {response.text}{Style.RESET_ALL}")
                return None
        except Exception as e:
            print(f"{Fore.RED}❌ 嘴巴连接异常: {e}{Style.RESET_ALL}")
            return None