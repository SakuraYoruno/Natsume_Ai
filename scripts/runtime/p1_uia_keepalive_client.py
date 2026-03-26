#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
受众：CODEX
标题：P1 方案三 — Minimal Accessibility Client (UIA 保活客户端)

一个最小化的 COM 客户端，通过持续轮询 + 订阅 UIA 事件的组合策略，
保持微信 UIA 树的活跃状态。

原理：
  与方案一（纯事件监听）和方案二（外部屏幕阅读器）不同，本方案
  采用更激进的"主动戳探 + 被动监听"双模式：

  1. 主动模式：定期对微信窗口执行 FindAll(TreeScope_Subtree) 深度遍历，
     强制 Qt 的 UIA Provider 响应并加载完整控件树
  2. 被动模式：注册 StructureChanged 事件监听，维持 Accessibility 客户端身份
  3. 缓存模式：利用 IUIAutomation.CreateCacheRequest() 预请求控件属性，
     让 UIA 框架保持对微信进程的持久连接
  4. 跨进程激活：通过 SetWinEventHook 监听 EVENT_OBJECT_CREATE 等 WinEvent，
     在操作系统层面注册为辅助技术客户端

  这四个策略叠加，模拟了一个完整的辅助技术应用的行为。

用法：
  # 启动保活客户端（前台运行，Ctrl+C 退出）
  python p1_uia_keepalive_client.py

  # 在另一个终端运行验证
  python verify_uia_recovery.py

依赖：
  pip install comtypes
"""

import sys
import io
import os
import time
import json
import ctypes
import ctypes.wintypes
import threading
from ctypes import windll, wintypes, WINFUNCTYPE, POINTER, c_int, c_uint
from datetime import datetime

# ── 编码兼容 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 常量 ──
WECHAT_CLASS_NAMES = [
    "WeChatMainWndForPC",
    "Qt51514QWindowIcon",
]
WECHAT_WINDOW_TITLE = "微信"
TARGET_CLASSES = [
    "mmui::ChatSingleWindow",
    "mmui::ChatTextItemView",
    "mmui::ChatMsgListView",
    "mmui::SessionListView",
    "mmui::SearchBar",
]
CRITICAL_TARGET_CLASSES = [
    "mmui::ChatSingleWindow",
    "mmui::ChatTextItemView",
]

# WinEvent 常量
EVENT_MIN = 0x00000001
EVENT_MAX = 0x7FFFFFFF
EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_STATECHANGE = 0x800A
EVENT_OBJECT_NAMECHANGE = 0x800C
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

# 主动戳探间隔（秒）
PROBE_INTERVAL = 10
# 初始戳探的激进期（前 N 次使用更短间隔）
AGGRESSIVE_PROBES = 6
AGGRESSIVE_INTERVAL = 3


def find_wechat_hwnd():
    """查找微信主窗口句柄"""
    for class_name in WECHAT_CLASS_NAMES:
        hwnd = windll.user32.FindWindowW(class_name, None)
        if hwnd:
            return hwnd, class_name

    hwnd = windll.user32.FindWindowW(None, WECHAT_WINDOW_TITLE)
    if hwnd:
        buf = (wintypes.WCHAR * 256)()
        windll.user32.GetClassNameW(hwnd, buf, 256)
        return hwnd, buf.value

    return None, None


def get_wechat_pid(hwnd):
    """获取微信进程 PID"""
    pid = wintypes.DWORD()
    windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


class UIAKeepaliveClient:
    """UIA 保活客户端核心类"""

    def __init__(self, hwnd, matched_class):
        self.hwnd = hwnd
        self.matched_class = matched_class
        self.pid = get_wechat_pid(hwnd)
        self.uia = None
        self.root = None
        self.cache_request = None
        self.event_handlers = []
        self.winevent_hooks = []
        self.stats = {
            "probes": 0,
            "max_children": 0,
            "targets_found": {},
            "structure_events": 0,
            "winevent_count": 0,
            "first_recovery_time": None,
        }
        self._stop = False
        self._lock = threading.Lock()

    def initialize_com(self):
        """初始化 COM 和 UIA"""
        import comtypes
        import comtypes.client

        self.uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=comtypes.gen.UIAutomationClient.IUIAutomation
        )
        print("[OK] IUIAutomation 创建成功")

        self.root = self.uia.ElementFromHandle(self.hwnd)
        print("[OK] 微信根元素获取成功")

        # 创建缓存请求（策略 3）
        try:
            self.cache_request = self.uia.CreateCacheRequest()
            # 添加常用属性到缓存
            self.cache_request.AddProperty(30005)  # UIA_NamePropertyId
            self.cache_request.AddProperty(30003)  # UIA_ClassNamePropertyId
            self.cache_request.AddProperty(30010)  # UIA_IsEnabledPropertyId
            # TreeScope = Subtree
            self.cache_request.TreeScope = 7  # TreeScope_Element | TreeScope_Children | TreeScope_Descendants
            print("[OK] CacheRequest 创建成功")
        except Exception as e:
            print(f"[WARN] CacheRequest 创建失败（非阻塞）: {e}")
            self.cache_request = None

    def register_uia_events(self):
        """注册 UIA 事件监听（策略 2）"""
        import comtypes
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationStructureChangedEventHandler,
            TreeScope_Subtree,
        )

        client = self  # 闭包引用

        class StructHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]

            def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
                with client._lock:
                    client.stats["structure_events"] += 1
                return 0

        try:
            handler = StructHandler()
            self.uia.AddStructureChangedEventHandler(
                self.root,
                TreeScope_Subtree,
                None,
                handler
            )
            self.event_handlers.append(("structure", handler))
            print("[OK] StructureChanged 事件已注册")
        except Exception as e:
            print(f"[WARN] StructureChanged 事件注册失败: {e}")

    def setup_winevent_hook(self):
        """设置 WinEvent 钩子（策略 4）"""
        # WinEventProc 回调类型
        WINEVENTPROC = WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,  # hWinEventHook
            ctypes.wintypes.DWORD,   # event
            ctypes.wintypes.HWND,    # hwnd
            ctypes.c_long,           # idObject
            ctypes.c_long,           # idChild
            ctypes.wintypes.DWORD,   # idEventThread
            ctypes.wintypes.DWORD,   # dwmsEventTime
        )

        client = self  # 闭包

        @WINEVENTPROC
        def winevent_callback(hHook, event, hwnd, idObject, idChild, threadId, time):
            with client._lock:
                client.stats["winevent_count"] += 1

        self._winevent_callback = winevent_callback  # 防止 GC 回收

        # 只监听微信进程的事件
        events_to_hook = [
            (EVENT_OBJECT_CREATE, EVENT_OBJECT_CREATE),
            (EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW),
            (EVENT_OBJECT_FOCUS, EVENT_OBJECT_FOCUS),
            (EVENT_OBJECT_STATECHANGE, EVENT_OBJECT_NAMECHANGE),
        ]

        for event_min, event_max in events_to_hook:
            try:
                hook = windll.user32.SetWinEventHook(
                    event_min,
                    event_max,
                    None,
                    winevent_callback,
                    self.pid,  # 只监听微信进程
                    0,  # 所有线程
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
                if hook:
                    self.winevent_hooks.append(hook)
            except Exception:
                pass

        if self.winevent_hooks:
            print(f"[OK] WinEvent 钩子已注册 ({len(self.winevent_hooks)} 个)")
        else:
            print("[WARN] WinEvent 钩子注册失败（非阻塞）")

    def probe_tree(self, deep=True):
        """
        主动戳探 UIA 树（策略 1）
        返回 (child_count, found_targets)
        """
        try:
            condition = self.uia.CreateTrueCondition()

            # 直接子元素
            children = self.root.FindAll(1, condition)
            child_count = children.Length

            found_targets = {}

            if deep and child_count > 2:
                # 深度搜索目标控件
                all_elems = self.root.FindAll(4, condition)  # TreeScope_Descendants
                total = all_elems.Length
                for i in range(min(total, 5000)):
                    elem = all_elems.GetElement(i)
                    cn = elem.CurrentClassName or ""
                    if cn in TARGET_CLASSES and cn not in found_targets:
                        name = (elem.CurrentName or "")[:60]
                        found_targets[cn] = name

            # 带缓存的请求（策略 3 加强）
            if self.cache_request and child_count <= 2:
                try:
                    cached_root = self.root.BuildUpdatedCache(self.cache_request)
                    # 尝试从缓存获取子元素
                    cached_children = cached_root.GetCachedChildren()
                    if cached_children:
                        cache_count = cached_children.Length
                        if cache_count > child_count:
                            child_count = cache_count
                except Exception:
                    pass  # 缓存失败不阻塞

            with self._lock:
                self.stats["probes"] += 1
                if child_count > self.stats["max_children"]:
                    self.stats["max_children"] = child_count
                if found_targets:
                    self.stats["targets_found"].update(found_targets)
                    if not self.stats["first_recovery_time"]:
                        has_critical = any(
                            tc in found_targets for tc in CRITICAL_TARGET_CLASSES
                        )
                        if has_critical:
                            self.stats["first_recovery_time"] = datetime.now().isoformat()

            return child_count, found_targets

        except Exception as e:
            return -1, {}

    def pump_messages(self, duration=0.1):
        """处理 Windows 消息泵（必须在主线程调用，WinEvent 需要消息循环）"""
        import ctypes
        from ctypes.wintypes import MSG

        msg = MSG()
        end_time = time.time() + duration
        while time.time() < end_time:
            result = windll.user32.PeekMessageW(
                ctypes.byref(msg), None, 0, 0, 1  # PM_REMOVE
            )
            if result:
                windll.user32.TranslateMessage(ctypes.byref(msg))
                windll.user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

    def run(self):
        """主运行循环"""
        print(f"\n{'=' * 60}")
        print(f"  UIA 保活客户端运行中")
        print(f"  微信 PID: {self.pid}")
        print(f"  前 {AGGRESSIVE_PROBES} 次戳探间隔: {AGGRESSIVE_INTERVAL}s")
        print(f"  之后戳探间隔: {PROBE_INTERVAL}s")
        print(f"  按 Ctrl+C 退出")
        print(f"{'=' * 60}\n")

        probe_num = 0
        baseline, _ = self.probe_tree(deep=False)
        print(f"[INFO] 基线子元素数: {baseline}\n")

        try:
            while not self._stop:
                probe_num += 1

                # 激进期使用更短间隔
                if probe_num <= AGGRESSIVE_PROBES:
                    interval = AGGRESSIVE_INTERVAL
                    deep = True
                else:
                    interval = PROBE_INTERVAL
                    deep = (probe_num % 3 == 0)  # 每 3 次做一次深度搜索

                # 主动戳探
                count, targets = self.probe_tree(deep=deep)

                # 判定状态
                has_critical = any(
                    tc in targets for tc in CRITICAL_TARGET_CLASSES
                )

                if has_critical:
                    icon = "[OK]"
                elif count > 2:
                    icon = "[PARTIAL]"
                elif count > baseline:
                    icon = "[IMPROVING]"
                else:
                    icon = "[WAIT]"

                with self._lock:
                    se = self.stats["structure_events"]
                    we = self.stats["winevent_count"]

                print(
                    f"  {icon} 戳探 #{probe_num}: "
                    f"子元素={count}, "
                    f"目标={list(targets.keys()) or '无'}, "
                    f"UIA事件={se}, WinEvent={we}"
                )

                if has_critical and probe_num >= AGGRESSIVE_PROBES:
                    print(f"\n[OK] 关键控件已找到！保活客户端继续运行维持状态 ...")
                    print(f"     现在可以运行 verify_uia_recovery.py 进行完整验证")

                # 消息泵 + 等待
                wait_start = time.time()
                while time.time() - wait_start < interval and not self._stop:
                    self.pump_messages(0.5)
                    time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n\n[INFO] 收到 Ctrl+C，正在退出 ...")
            self._stop = True

    def cleanup(self):
        """清理资源"""
        # 移除 WinEvent 钩子
        for hook in self.winevent_hooks:
            try:
                windll.user32.UnhookWinEvent(hook)
            except Exception:
                pass
        if self.winevent_hooks:
            print(f"[OK] 已移除 {len(self.winevent_hooks)} 个 WinEvent 钩子")

        # 移除 UIA 事件
        for etype, handler in self.event_handlers:
            try:
                if etype == "structure":
                    self.uia.RemoveStructureChangedEventHandler(self.root, handler)
            except Exception:
                pass
        if self.event_handlers:
            print(f"[OK] 已移除 {len(self.event_handlers)} 个 UIA 事件处理器")

    def get_report(self):
        """生成最终报告"""
        with self._lock:
            final_count, final_targets = self.probe_tree(deep=True)
            has_critical = any(
                tc in final_targets for tc in CRITICAL_TARGET_CLASSES
            )

            return {
                "strategy": "p1_uia_keepalive_client",
                "timestamp": datetime.now().isoformat(),
                "wechat_hwnd": f"0x{self.hwnd:08X}",
                "wechat_class": self.matched_class,
                "wechat_pid": self.pid,
                "final_children": final_count,
                "final_targets": list(final_targets.keys()),
                "has_critical_targets": has_critical,
                "overall_result": (
                    "success" if has_critical else
                    "partial" if final_count > 2 else
                    "failed"
                ),
                "stats": {
                    "total_probes": self.stats["probes"],
                    "max_children_observed": self.stats["max_children"],
                    "all_targets_found": list(self.stats["targets_found"].keys()),
                    "structure_events": self.stats["structure_events"],
                    "winevent_count": self.stats["winevent_count"],
                    "first_recovery_time": self.stats["first_recovery_time"],
                },
            }


def main():
    print("=" * 60)
    print("  P1 方案三：Minimal Accessibility Client (UIA 保活)")
    print(f"  时间: {datetime.now().isoformat()}")
    print("=" * 60)

    # ── 查找微信 ──
    hwnd, matched_class = find_wechat_hwnd()
    if not hwnd:
        print("[FAIL] 未找到微信窗口")
        sys.exit(1)
    print(f"[OK] 微信窗口: class='{matched_class}', hwnd=0x{hwnd:08X}")

    # ── 创建客户端 ──
    client = UIAKeepaliveClient(hwnd, matched_class)

    try:
        # 初始化
        print("\n[INFO] 初始化 UIA 保活客户端 ...")
        client.initialize_com()
        client.register_uia_events()
        client.setup_winevent_hook()

        # 运行
        client.run()

    except Exception as e:
        print(f"\n[FAIL] 运行异常: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 生成报告
        report = client.get_report()
        report_path = "p1_keepalive_status.json"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n[INFO] 报告已保存: {report_path}")
        except Exception as e:
            print(f"[WARN] 报告保存失败: {e}")

        # 输出最终状态
        overall = report.get("overall_result", "unknown")
        print(f"\n[INFO] 最终结果: {overall}")
        print(f"       最大子元素数: {report['stats']['max_children_observed']}")
        print(f"       找到的目标控件: {report['stats']['all_targets_found'] or '无'}")
        print(f"       总戳探次数: {report['stats']['total_probes']}")
        print(f"       UIA 结构事件: {report['stats']['structure_events']}")
        print(f"       WinEvent 总数: {report['stats']['winevent_count']}")

        # 清理
        client.cleanup()
        print("\n[END] P1 方案三保活客户端已退出")


if __name__ == "__main__":
    main()
