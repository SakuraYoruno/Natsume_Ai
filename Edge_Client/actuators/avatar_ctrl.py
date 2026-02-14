import asyncio
import threading
import os
import random
import time
from colorama import Fore, Style
import pyvts


class AvatarController:
    def __init__(self):
        self.current_state = "IDLE"
        self.connected = False
        self.last_mood = 50

        # 1. 定义 VTS 连接信息
        self.plugin_info = {
            "plugin_name": "Natsume Brain",
            "developer": "Master",
            "authentication_token_path": "./vts_token.txt"
        }

        # 2. 初始化 VTS 库
        self.api = pyvts.vts(plugin_info=self.plugin_info)

        # 3. ⚡ [核心映射表] 
        # 左边是代码逻辑用的名字，右边是 VTube Studio 里设置的热键名(Name)
        self.behavior_map = {
            # --- 表情类 ---
            "EXP_LOVE": "HK_Love",  # 对应 VTS 里的 HK_Love (小键盘9)
            "EXP_STAR": "HK_Star",  # 对应 VTS 里的 HK_Star (小键盘8) - 假设你有
            "EXP_SHY": "HK_Shy",  # 对应 VTS 里的 HK_Shy (小键盘4)
            "EXP_ANGRY": "HK_Angry",  # 对应 VTS 里的 HK_Angry (小键盘1)
            "EXP_DARK": "HK_Despise",  # 对应 VTS 里的 HK_Despise (鄙视/黑化)
            "EXP_DEAD": "HK_Dead",  # 对应 VTS 里的 HK_Dead (死机)

            # --- 形态类 ---
            "FORM_CHIBI": "HK_Chibi",  # 变Q版
            "FORM_NORMAL": "HK_Normal",  # 变回正常 (如果没有专门的键，通常是再按一次变Q)

            # --- 道具类 ---
            "PROP_GAMEPAD": "HK_Game",  # 拿手柄
            "PROP_PHONE": "HK_Phone",  # 玩手机

            # --- 常用表情 (补充) ---
            "EXP_HAPPY": "HK_Happy"  # 开心
        }

        # 4. 启动后台连接线程
        threading.Thread(target=self._connect_vts, daemon=True).start()

    def _connect_vts(self):
        """后台连接保活线程"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_connect())
        except Exception as e:
            print(f"{Fore.YELLOW}⚠️ VTS 连接失败: {e}{Style.RESET_ALL}")
        finally:
            loop.close()

    async def _async_connect(self):
        """异步连接逻辑"""
        await self.api.connect()
        await self.api.request_authenticate_token()
        await self.api.request_authenticate()
        self.connected = True
        print(f"{Fore.GREEN}✅ 夏目蓝躯体连接成功 (VTube Studio){Style.RESET_ALL}")

    def trigger_hotkey(self, hotkey_name):
        """
        [关键修复] 根据热键名称触发动作
        逻辑：先获取所有热键列表 -> 找到名字匹配的 ID -> 发送触发指令
        """
        if not self.connected:
            return

        async def _send():
            try:
                # 1. 向 VTS 请求当前所有热键的列表
                response = await self.api.request(self.api.vts_request.requestHotKeyList())
                hotkey_list = response.get('data', {}).get('availableHotkeys', [])

                target_id = None
                # 2. 遍历列表，查找名字匹配的 ID
                for hk in hotkey_list:
                    if hk['name'] == hotkey_name:
                        target_id = hk['hotkeyID']
                        break

                # 3. 如果找到了 ID，发送触发指令
                if target_id:
                    await self.api.request(self.api.vts_request.requestHotKey(target_id))
                    print(f"⚡ [VTS] 触发动作成功: {hotkey_name}")
                else:
                    print(f"⚠️ [VTS] 未找到热键名: {hotkey_name} (请检查 VTS 设置)")
            except Exception as e:
                print(f"❌ [VTS] 热键触发异常: {e}")

        # 在同步环境中运行异步代码
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_send())
            loop.close()
        except:
            pass

    def express_emotion(self, mood, energy, context_tag=None):
        """
        全自动表情管理系统 (V5.6 Final Logic)
        """
        self.last_mood = mood
        print(f"🎭 [Avatar] 表情同步: Mood={mood:.1f}, Energy={energy:.1f}, Context={context_tag}")

        # --- 0. 形态复位逻辑 (新增) ---
        # 如果精力恢复了 (>20) 且当前记录显示是 Q 版，则触发变回正常
        # 这是一个“状态锁”，防止重复触发
        if energy >= 20 and getattr(self, 'is_chibi', False):
             print("🔋 精力恢复，变回正常形态。")
             self.trigger_hotkey(self.behavior_map["FORM_NORMAL"])
             self.is_chibi = False # 解锁

        # --- 1. 生理极限 ---
        # 如果精力过低，强制变 Q 版并保持，不做其他表情
        if energy < 20:
            # 只有当她还不是 Q 版的时候，才触发一次变身
            if not getattr(self, 'is_chibi', False):
                print("💤 精力耗尽，切换 Q 版模式。")
                self.trigger_hotkey(self.behavior_map["FORM_CHIBI"])
                self.is_chibi = True # 上锁
            return

        # --- 2. 上下文场景 ---
        if context_tag == "GAME":
            self.trigger_hotkey(self.behavior_map["PROP_GAMEPAD"])
            return

        # --- 3. 情绪分支 (正常形态下的表情) ---
        if mood >= 90:
            self.trigger_hotkey(self.behavior_map["EXP_LOVE"])
        elif mood >= 75:
            self.trigger_hotkey(self.behavior_map["EXP_HAPPY"])
        elif mood <= 10:
            self.trigger_hotkey(self.behavior_map["EXP_DEAD"])
        elif mood <= 30:
            self.trigger_hotkey(self.behavior_map["EXP_ANGRY"])
        elif mood <= 45:
            self.trigger_hotkey(self.behavior_map["EXP_DARK"])
        else:
            # 平淡期随机动作
            import random
            if random.random() < 0.15:
                self.trigger_hotkey(self.behavior_map["EXP_SHY"])
            elif random.random() < 0.1:
                self.trigger_hotkey(self.behavior_map["PROP_PHONE"])

    # --- 保持口型同步的辅助函数 ---
    def start_talking(self):
        self.set_state("SPEAKING")
        if self.last_mood > 80:
            self.trigger_hotkey(self.behavior_map["EXP_HAPPY"])

    def stop_talking(self):
        self.set_state("IDLE")

    def start_listening(self):
        self.set_state("LISTENING")

    def start_thinking(self):
        self.set_state("THINKING")

    def set_state(self, state):
        self.current_state = state