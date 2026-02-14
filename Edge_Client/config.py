# config.py
import os

# 获取当前 config.py 文件所在的绝对路径 (D:\Edge_Client)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== 大脑设置 (Host: 5080主机) ====================
BRAIN_IP = "192.168.1.100"
HOST_PORT = 5080
USER_ID = "Master"
# 神经信号地址 (即 Bridge Server 接口)
NERVE_URL = f"http://{BRAIN_IP}:{HOST_PORT}/hear"

# ==================== 躯体设置 (Laptop: 笔记本) ====================
# 锁定 /tts 子路径以避开 404
TTS_API_URL = "http://127.0.0.1:9880/tts"

# 建议使用正斜杠防止转义问题
TTS_REF_AUDIO_PATH = "D:/DataSet/Natsume_Ai_Sliced/fem_aix_10526.ogg_0000124480_0000220800.wav"
TTS_PROMPT_TEXT = "その感情が何であるのかわからなかった"
TTS_PROMPT_LANG = "ja"
TTS_TARGET_LANG = "ja"

# 语音识别 (Whisper) 设置
WHISPER_MODEL_SIZE = "large-v3"
DEVICE = "cuda"

# 唤醒词设置
WAKE_WORDS = ["Ai", "AI", "爱", "愛", "蓝"]
WAKE_WORD = "Ai"

# 【关键修复】使用绝对路径，解决之前提示 TEMP_AUDIO_DIR 缺失或找不到的问题
TEMP_AUDIO_DIR = os.path.join(BASE_DIR, "temp_audio")

if not os.path.exists(TEMP_AUDIO_DIR):
    os.makedirs(TEMP_AUDIO_DIR)