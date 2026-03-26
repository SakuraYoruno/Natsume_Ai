#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
受众：CODEX
标题：P1 方案一 — 持久化 UIA 事件监听器

在运行 verify_uia_recovery.py 之前，先启动本脚本。
本脚本会创建一个持久化的 UIA 事件订阅，监听微信窗口的
StructureChanged / FocusChanged / PropertyChanged 事件，
迫使 Qt 的 QWindowsUiaAccessibility 插件保持活跃并暴露完整控件树。

原理：
  微信 4.x 使用 Qt 的"按需暴露"策略——只有当外部有活跃的
  Accessibility 客户端在监听事件时，Qt 的 UIA Provider 才会
  完整加载控件树。短时的 UIA 查询（如 Inspect.exe 快速扫描）
  可能不足以触发完整加载。

  本脚本通过 comtypes 直接调用 IUIAutomation::AddStructureChangedEventHandler
  等 COM 接口，建立一个长时间驻留的事件处理器，模拟屏幕阅读器
  的持久化存在。

用法：
  # 终端 1：启动监听器（后台驻留）
  python p1_uia_event_listener.py

  # 终端 2：等待 5-10 秒后运行验证脚本
  python verify_uia_recovery.py

  # 验证完成后，回到终端 1 按 Ctrl+C 退出

依赖：
  pip install comtypes
"""

import sys
import io
import time
import ctypes
import json
from ctypes import windll, wintypes
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
POLL_INTERVAL = 30  # 每 N 秒做一次心跳检查
EVENT_SETTLE_WAIT = 5  # 注册事件后等待多久再做首次检查


def find_wechat_hwnd():
    """查找微信主窗口句柄"""
    for class_name in WECHAT_CLASS_NAMES:
        hwnd = windll.user32.FindWindowW(class_name, None)
        if hwnd:
            print(f"[OK] 找到微信窗口: class_name='{class_name}', hwnd=0x{hwnd:08X}")
            return hwnd, class_name

    hwnd = windll.user32.FindWindowW(None, WECHAT_WINDOW_TITLE)
    if hwnd:
        buf = (wintypes.WCHAR * 256)()
        windll.user32.GetClassNameW(hwnd, buf, 256)
        actual_class = buf.value
        print(f"[OK] 按标题找到微信窗口: class_name='{actual_class}', hwnd=0x{hwnd:08X}")
        return hwnd, actual_class

    print("[FAIL] 未找到微信窗口")
    return None, None


def count_children_comtypes(uia, hwnd):
    """用 comtypes 计数直接子元素"""
    try:
        root = uia.ElementFromHandle(hwnd)
        condition = uia.CreateTrueCondition()
        children = root.FindAll(1, condition)  # TreeScope_Children = 1
        return children.Length
    except Exception as e:
        print(f"  [WARN] 子元素计数失败: {e}")
        return -1


def main():
    print("=" * 60)
    print("  P1 方案一：持久化 UIA 事件监听器")
    print(f"  时间: {datetime.now().isoformat()}")
    print("=" * 60)

    # ── 查找微信窗口 ──
    hwnd, matched_class = find_wechat_hwnd()
    if not hwnd:
        print("[FAIL] 无法启动监听器：未找到微信窗口")
        sys.exit(1)

    # ── 初始化 COM ──
    try:
        import comtypes
        import comtypes.client
        from comtypes import COMError

        # 确保 COM 初始化为 MTA（多线程模式，适合事件处理）
        # comtypes 默认会初始化 COM，但我们需要确保在正确的线程模式
        print("\n[INFO] 初始化 COM / IUIAutomation ...")

        UIA = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=comtypes.gen.UIAutomationClient.IUIAutomation
        )
        print("[OK] IUIAutomation 实例创建成功")

    except ImportError:
        print("[FAIL] comtypes 未安装: pip install comtypes")
        sys.exit(1)
    except Exception as e:
        print(f"[FAIL] COM 初始化失败: {e}")
        sys.exit(1)

    # ── 获取微信根元素 ──
    try:
        wechat_element = UIA.ElementFromHandle(hwnd)
        print(f"[OK] 获取微信 UIA 元素成功")
    except Exception as e:
        print(f"[FAIL] ElementFromHandle 失败: {e}")
        sys.exit(1)

    # ── 基线检查 ──
    baseline = count_children_comtypes(UIA, hwnd)
    print(f"\n[INFO] 基线子元素数: {baseline}")

    # ── 注册 UIA 事件处理器 ──
    print("\n[INFO] 注册 UIA 事件监听器 ...")

    event_count = {"structure": 0, "focus": 0, "property": 0}
    handlers_registered = []

    # --- 方式 1: StructureChangedEventHandler ---
    try:
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationStructureChangedEventHandler,
            TreeScope_Subtree,
        )

        class StructureChangedHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]

            def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
                event_count["structure"] += 1
                if event_count["structure"] <= 5:
                    print(f"  [EVENT] StructureChanged #{event_count['structure']} (type={changeType})")
                return 0

        handler = StructureChangedHandler()
        UIA.AddStructureChangedEventHandler(
            wechat_element,
            TreeScope_Subtree,  # 4 = TreeScope_Subtree
            None,  # cacheRequest
            handler
        )
        handlers_registered.append("StructureChanged")
        print("  [OK] StructureChangedEventHandler 已注册")

    except Exception as e:
        print(f"  [WARN] StructureChanged 事件注册失败: {e}")

    # --- 方式 2: FocusChangedEventHandler ---
    try:
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationFocusChangedEventHandler,
        )

        class FocusChangedHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationFocusChangedEventHandler]

            def HandleFocusChangedEvent(self, sender):
                event_count["focus"] += 1
                if event_count["focus"] <= 5:
                    try:
                        cn = sender.CurrentClassName or ""
                        name = (sender.CurrentName or "")[:40]
                        print(f"  [EVENT] FocusChanged #{event_count['focus']}: {cn} / {name}")
                    except Exception:
                        print(f"  [EVENT] FocusChanged #{event_count['focus']}")
                return 0

        focus_handler = FocusChangedHandler()
        UIA.AddFocusChangedEventHandler(
            None,  # cacheRequest
            focus_handler
        )
        handlers_registered.append("FocusChanged")
        print("  [OK] FocusChangedEventHandler 已注册")

    except Exception as e:
        print(f"  [WARN] FocusChanged 事件注册失败: {e}")

    # --- 方式 3: PropertyChangedEventHandler ---
    try:
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationPropertyChangedEventHandler,
            TreeScope_Subtree,
        )

        # UIA_NamePropertyId = 30005
        # UIA_IsEnabledPropertyId = 30010
        # UIA_BoundingRectanglePropertyId = 30001
        property_ids = (ctypes.c_int * 3)(30005, 30010, 30001)

        class PropertyChangedHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationPropertyChangedEventHandler]

            def HandlePropertyChangedEvent(self, sender, propertyId, newValue):
                event_count["property"] += 1
                if event_count["property"] <= 5:
                    print(f"  [EVENT] PropertyChanged #{event_count['property']} (prop={propertyId})")
                return 0

        prop_handler = PropertyChangedHandler()
        UIA.AddPropertyChangedEventHandler(
            wechat_element,
            TreeScope_Subtree,
            None,  # cacheRequest
            prop_handler,
            property_ids
        )
        handlers_registered.append("PropertyChanged")
        print("  [OK] PropertyChangedEventHandler 已注册")

    except Exception as e:
        print(f"  [WARN] PropertyChanged 事件注册失败: {e}")

    if not handlers_registered:
        print("\n[FAIL] 所有事件处理器注册失败，无法建立持久化监听")
        sys.exit(1)

    print(f"\n[OK] 已注册 {len(handlers_registered)} 个事件处理器: {', '.join(handlers_registered)}")

    # ── 等待事件沉淀 ──
    print(f"\n[INFO] 等待 {EVENT_SETTLE_WAIT}s 让事件沉淀 ...")
    time.sleep(EVENT_SETTLE_WAIT)

    # ── 事件注册后首次检查 ──
    after_count = count_children_comtypes(UIA, hwnd)
    print(f"\n[INFO] 事件注册后子元素数: {after_count} (基线: {baseline})")

    if after_count > baseline and after_count > 2:
        print("[OK] 事件监听器似乎促进了 UI 树展开!")
    elif after_count > 2:
        print("[INFO] 子元素数 > 2，可能已有效果")
    else:
        print("[INFO] 子元素数未明显增加，继续保持监听 ...")
        print("       建议等待 10-30 秒后运行 verify_uia_recovery.py 验证")

    # ── 输出状态报告 ──
    report = {
        "strategy": "p1_uia_event_listener",
        "timestamp": datetime.now().isoformat(),
        "wechat_hwnd": f"0x{hwnd:08X}",
        "wechat_class": matched_class,
        "handlers_registered": handlers_registered,
        "baseline_children": baseline,
        "after_register_children": after_count,
        "events_received": dict(event_count),
    }

    report_path = "p1_event_listener_status.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 状态报告已保存: {report_path}")
    except Exception as e:
        print(f"[WARN] 状态报告保存失败: {e}")

    # ── 持久化运行（心跳） ──
    print(f"\n{'=' * 60}")
    print(f"  事件监听器已就绪，保持运行中 ...")
    print(f"  每 {POLL_INTERVAL}s 输出心跳")
    print(f"  现在可以在另一个终端运行 verify_uia_recovery.py")
    print(f"  按 Ctrl+C 退出")
    print(f"{'=' * 60}")

    try:
        heartbeat = 0
        while True:
            time.sleep(POLL_INTERVAL)
            heartbeat += 1
            current_count = count_children_comtypes(UIA, hwnd)
            total_events = sum(event_count.values())
            print(
                f"  [HEARTBEAT #{heartbeat}] "
                f"子元素数: {current_count}, "
                f"累计事件: {total_events} "
                f"(结构:{event_count['structure']}, "
                f"焦点:{event_count['focus']}, "
                f"属性:{event_count['property']})"
            )
    except KeyboardInterrupt:
        print("\n\n[INFO] 收到 Ctrl+C，正在清理 ...")

        # 尝试移除事件处理器
        try:
            if "StructureChanged" in handlers_registered:
                UIA.RemoveStructureChangedEventHandler(wechat_element, handler)
            if "FocusChanged" in handlers_registered:
                UIA.RemoveFocusChangedEventHandler(focus_handler)
            if "PropertyChanged" in handlers_registered:
                UIA.RemovePropertyChangedEventHandler(wechat_element, prop_handler)
            print("[OK] 事件处理器已清理")
        except Exception as e:
            print(f"[WARN] 清理事件处理器时异常: {e}")

        # 最终子元素数
        final_count = count_children_comtypes(UIA, hwnd)
        print(f"[INFO] 退出时子元素数: {final_count}")
        print("[END] P1 方案一监听器已退出")


if __name__ == "__main__":
    main()
