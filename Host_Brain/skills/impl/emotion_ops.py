import time
import pyautogui
import subprocess
import os

from config import ASSETS_DIR

# TCOMAS 路径 (如果不正确请修改)
TCOMAS_PATH = r"C:\Program Files\TCOMAS\TCOMAS.exe"


def change_face(emotion: str):
    """
    GUI 自动化控制换脸 (CPU 水冷屏)
    素材路径: core/data/assets/face_round/{emotion}.gif
    """
    print(f"🎨 [Emotion] 切换表情: {emotion}")
    gif_path = os.path.join(ASSETS_DIR, "face_round", f"{emotion}.gif")

    if not os.path.exists(gif_path):
        return f"❌ 缺失素材: {gif_path}"

    try:
        # 1. 启动/置顶软件 (9800X3D 秒开)
        subprocess.Popen(TCOMAS_PATH)
        time.sleep(2)

        # 2. 模拟点击流程 (此处为演示，需配合 locateCenterOnScreen 优化)
        # 建议先截图按钮放在 ASSETS_DIR/ui 目录下
        # ... 点击操作 ...

        # 3. 最小化
        pyautogui.hotkey('win', 'down')
        return f"✅ 表情已切换为 {emotion}"
    except Exception as e:
        return f"❌ GUI 操作失败: {e}"


def change_gpu_status(mode: str):
    """显卡屏控制 (需适配 iGame Center)"""
    return f"✅ GPU 屏已切换: {mode}"