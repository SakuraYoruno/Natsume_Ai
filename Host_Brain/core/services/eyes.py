import psutil
import time
import threading

from config import GAME_PROCESSES
from core.services import hardware  # 同级引用，或通过root引用均可

# 全局模式标志
SYS_MODE = "normal"


class OmniEye:
    def __init__(self):
        self.running = True
        self.game_not_found_count = 0
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        global SYS_MODE
        print("👁️ [Eyes] 视觉监控已启动...")

        while self.running:
            game_active = False
            # 快速扫描进程
            try:
                # 转换为集合加速查找
                current_procs = {p.info['name'].lower() for p in psutil.process_iter(['name'])}
                for target_game in GAME_PROCESSES:
                    if target_game.lower() in current_procs:
                        game_active = True
                        break
            except:
                pass

            # 状态机
            if game_active:
                self.game_not_found_count = 0
                if SYS_MODE == "normal":
                    print("🎮 [Eyes] 进入游戏模式。Host 算力释放。")
                    SYS_MODE = "gaming"
                    hardware.enter_gaming_mode()
            else:
                if SYS_MODE == "gaming":
                    # 防抖：60秒无游戏才切回
                    self.game_not_found_count += 1
                    if self.game_not_found_count > 12:  # 12*5s = 60s
                        print("🏠 [Eyes] 退出游戏模式。Host 大脑重启。")
                        SYS_MODE = "normal"
                        hardware.exit_gaming_mode()
                        self.game_not_found_count = 0

            time.sleep(5)


eye_module = OmniEye()


def get_sys_mode(): return SYS_MODE