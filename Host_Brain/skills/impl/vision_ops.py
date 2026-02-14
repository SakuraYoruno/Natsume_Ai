import sys
import os
import time

from skills.registry import register_skill
from config import ASSETS_DIR


@register_skill("屏幕分析")
def capture_and_analyze(prompt="描述屏幕内容", target="host"):
    """
    V6.1 视觉实现
    此处演示懒加载：只有 Dify 调用这个工具时，才会加载 CV 库
    """
    print("👀 [Vision] 正在唤醒视觉神经 (Lazy Loading)...")

    # === 动态加载重型库 ===
    import mss
    # import torch # 如果要用本地模型，在这里 import

    # 1. 截图
    timestamp = int(time.time())
    save_path = os.path.join(ASSETS_DIR, f"screen_{timestamp}.png")

    with mss.mss() as sct:
        # 截取第一个显示器
        sct.shot(mon=1, output=save_path)

    print(f"📸 截图已保存: {save_path}")

    # 2. 分析 (此处模拟 VLM 返回，实际可接 Ollama Vision)
    # 真正的 9100 PRO 速度在这里体现：加载模型 -> 推理 -> 释放

    return f"✅ 已截取屏幕。模拟分析结果: 画面看起来是一个代码编辑器，用户正在写 Python。"