import subprocess
import psutil
import requests
import time
import torch
import gc

from config import OLLAMA_API, MAIN_MODEL

def enter_gaming_mode():
    """[焦土战术] 彻底清空 5080 显存"""
    print("\n🚨 [Hardware] 监测到游戏启动！执行显存净化程序...")

    # 1. 物理处决 Ollama
    for proc in psutil.process_iter(['name']):
        if "ollama" in proc.info['name'].lower():
            try:
                proc.kill()
            except:
                pass

    # 2. 清理 Python 显存
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()

    print("✅ [Hardware] 显存已归零，全力渲染游戏。")


def exit_gaming_mode():
    """[光速复活] 9100 PRO 专属复活逻辑"""
    print("\n🌱 [Hardware] 游戏结束，正在唤醒主脑...")

    # 1. 启动服务 (PCIe 5.0 硬盘启动极快)
    # 先检查是否已存活
    try:
        if requests.get(OLLAMA_API, timeout=0.5).status_code == 200:
            print("   -> Ollama 已在运行。")
    except:
        # 启动
        subprocess.Popen(["ollama", "serve"], creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL)

        # 2. 暴力轮询端口 (0.1s 间隔)
        for _ in range(30):
            try:
                if requests.get(OLLAMA_API).status_code == 200: break
            except:
                time.sleep(0.1)

    # 3. 预热模型 (Abliterated版)
    try:
        print(f"🔥 [Hardware] 预热模型: {MAIN_MODEL}")
        requests.post(f"{OLLAMA_API}/api/generate",
                      json={"model": MAIN_MODEL, "keep_alive": -1}, timeout=1)
    except:
        pass
    print("✅ [Hardware] 主脑已就绪。")