import sys
import os
from colorama import init, Fore, Style

# 1. 路径修正
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# 2. 自动初始化 Colorama (Windows 必备)
init(autoreset=True)

try:
    # 3. 加载环境 (DLL)
    from utils.env_setup import init_nvidia_environment

    init_nvidia_environment()

    # 4. 启动生命周期
    from core.lifecycle import start_life_cycle

except ImportError as e:
    print(f"{Fore.RED}❌ 启动失败：依赖缺失 - {e}{Style.RESET_ALL}")
    sys.exit(1)

if __name__ == "__main__":
    start_life_cycle()