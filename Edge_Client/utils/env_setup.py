import os
import sys


def init_nvidia_environment():
    """初始化 NVIDIA 库路径和 HF 镜像"""
    # 1. 强制设置 HF 镜像 (原代码逻辑)
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    # 2. 显卡库路径
    # 动态获取当前 Python 环境目录
    base_path = os.path.dirname(sys.executable)

    # 自动推导库路径
    paths_to_add = [
        os.path.join(base_path, r"Lib\site-packages\nvidia\cublas\bin"),
        os.path.join(base_path, r"Lib\site-packages\nvidia\cudnn\bin")
    ]

    added_count = 0
    for p in paths_to_add:
        if os.path.exists(p):
            os.add_dll_directory(p)
            os.environ['PATH'] = p + os.pathsep + os.environ['PATH']
            added_count += 1

    if added_count > 0:
        print(f"   [Env] 已加载 {added_count} 个 NVIDIA DLL 路径")
    else:
        print(f"   [Env] ⚠️ 未找到 NVIDIA DLL，可能导致 Whisper 报错")