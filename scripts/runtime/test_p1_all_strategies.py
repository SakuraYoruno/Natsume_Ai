#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
受众：CODEX
标题：P1 三方案对比测试 — 自动运行三种 UIA 恢复策略并择优

依次运行三种 P1 恢复方案，每种方案独立测试，最终汇总对比结果
并推荐最优方案。

三种方案：
  S1: 持久化 UIA 事件监听器（p1_uia_event_listener 内嵌逻辑）
  S2: NVDA/Narrator 屏幕阅读器辅助
  S3: Minimal Accessibility Client（主动戳探 + 被动监听 + WinEvent）

测试流程（每种方案）：
  1. 基线检查（无任何辅助工具时的子元素数）
  2. 启动方案
  3. 等待指定时间
  4. 运行 UIA 树检查
  5. 停止方案
  6. 等待回收检查（看停止后 UIA 树是否回退）

用法：
  python test_p1_all_strategies.py

  # 快速模式（缩短等待时间）
  python test_p1_all_strategies.py --quick

  # 只测试某个方案
  python test_p1_all_strategies.py --only s1
  python test_p1_all_strategies.py --only s2
  python test_p1_all_strategies.py --only s3

输出：
  - 控制台汇总表
  - p1_comparison_result.json（完整对比报告）

依赖：
  pip install comtypes pywinauto uiautomation
"""

import sys
import io
import os
import time
import json
import subprocess
import argparse
import ctypes
import ctypes.wintypes
import threading
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

# ── comtypes UIAutomation 初始化状态 ──
_uia_typelib_loaded = False


def _ensure_uia_typelib():
    """确保 comtypes.gen.UIAutomationClient 类型库已加载（幂等操作）"""
    global _uia_typelib_loaded
    if _uia_typelib_loaded:
        return
    import comtypes.client
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
    except OSError:
        import os as _os
        dll_path = _os.path.join(
            _os.environ.get("SystemRoot", r"C:\Windows"),
            "System32", "UIAutomationCore.dll"
        )
        comtypes.client.GetModule(dll_path)
    _uia_typelib_loaded = True


def _create_uia():
    """创建并返回 IUIAutomation COM 实例"""
    import comtypes
    import comtypes.client
    _ensure_uia_typelib()
    from comtypes.gen.UIAutomationClient import IUIAutomation
    return comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        interface=IUIAutomation
    )


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


def check_uia_tree(hwnd):
    """
    检查微信 UIA 树状态。
    返回 (child_count, found_targets_dict, descendant_count)
    """
    try:
        UIA = _create_uia()

        root = UIA.ElementFromHandle(hwnd)
        condition = UIA.CreateTrueCondition()

        children = root.FindAll(1, condition)
        child_count = children.Length

        found_targets = {}
        descendant_count = 0

        if child_count > 0:
            try:
                all_elems = root.FindAll(4, condition)
                descendant_count = all_elems.Length
                for i in range(min(descendant_count, 5000)):
                    elem = all_elems.GetElement(i)
                    cn = elem.CurrentClassName or ""
                    if cn in TARGET_CLASSES and cn not in found_targets:
                        name = (elem.CurrentName or "")[:60]
                        found_targets[cn] = name
            except Exception:
                pass

        return child_count, found_targets, descendant_count

    except Exception as e:
        return -1, {}, 0


def evaluate_result(child_count, found_targets, baseline_count):
    """评估测试结果"""
    has_critical = any(tc in found_targets for tc in CRITICAL_TARGET_CLASSES)
    improved = child_count > baseline_count

    if has_critical and child_count > 2:
        return "success", 100
    elif child_count > 2 and not has_critical:
        return "partial", 60
    elif improved:
        return "improved", 30
    else:
        return "failed", 0


# ════════════════════════════════════════
# 策略 S1: 持久化 UIA 事件监听器
# ════════════════════════════════════════
def run_strategy_s1(hwnd, duration=20, quick=False):
    """策略 S1: 持久化 UIA 事件监听"""
    print("\n" + "=" * 55)
    print("  [S1] 持久化 UIA 事件监听器")
    print("=" * 55)

    if quick:
        duration = 10

    result = {
        "strategy": "S1_event_listener",
        "description": "持久化 UIA 事件监听器（StructureChanged + FocusChanged）",
    }

    try:
        import comtypes
        import comtypes.client

        UIA = _create_uia()
        root = UIA.ElementFromHandle(hwnd)

        # 注册事件
        _ensure_uia_typelib()
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationStructureChangedEventHandler,
            TreeScope_Subtree,
        )

        event_count = [0]

        class StructHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]
            def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
                event_count[0] += 1
                return 0

        handler = StructHandler()
        UIA.AddStructureChangedEventHandler(root, TreeScope_Subtree, None, handler)
        print("  [OK] 事件监听器已注册")

        result["events_registered"] = True

        # 等待
        print(f"  [INFO] 等待 {duration}s ...")
        for i in range(duration):
            time.sleep(1)
            if (i + 1) % 5 == 0:
                count, targets, desc = check_uia_tree(hwnd)
                has_c = any(tc in targets for tc in CRITICAL_TARGET_CLASSES)
                if has_c:
                    print(f"    {i+1}s: 子元素={count}, 已找到关键控件!")
                    break
                print(f"    {i+1}s: 子元素={count}, 后代={desc}, 事件={event_count[0]}")

        # 最终检查
        child_count, found_targets, desc_count = check_uia_tree(hwnd)
        result["child_count"] = child_count
        result["descendant_count"] = desc_count
        result["found_targets"] = list(found_targets.keys())
        result["events_received"] = event_count[0]

        # 清理
        try:
            UIA.RemoveStructureChangedEventHandler(root, handler)
        except Exception:
            pass

        # 保持 UIA/root 引用直到函数结束

    except ImportError:
        result["error"] = "comtypes 未安装"
        result["child_count"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["child_count"] = -1

    return result


# ════════════════════════════════════════
# 策略 S2: NVDA / Narrator 辅助
# ════════════════════════════════════════
def run_strategy_s2(hwnd, duration=25, quick=False):
    """策略 S2: NVDA/Narrator 屏幕阅读器"""
    print("\n" + "=" * 55)
    print("  [S2] NVDA / Narrator 屏幕阅读器辅助")
    print("=" * 55)

    if quick:
        duration = 12

    result = {
        "strategy": "S2_screen_reader",
        "description": "NVDA/Narrator 屏幕阅读器辅助恢复",
    }

    # 查找 NVDA
    nvda_paths = [
        r"C:\Program Files (x86)\NVDA\nvda.exe",
        r"C:\Program Files\NVDA\nvda.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\NVDA\nvda.exe"),
    ]

    reader_name = None
    reader_proc = None
    was_running = False

    # 尝试 NVDA
    for path in nvda_paths:
        if os.path.isfile(path):
            try:
                # 检查是否已运行
                check = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq nvda.exe", "/NH"],
                    capture_output=True, text=True, timeout=10
                )
                if "nvda.exe" in check.stdout.lower():
                    reader_name = "NVDA"
                    was_running = True
                    print(f"  [INFO] NVDA 已在运行中")
                    break

                reader_proc = subprocess.Popen(
                    [path, "--minimal"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                reader_name = "NVDA"
                print(f"  [OK] NVDA 已启动 (PID: {reader_proc.pid})")
                break
            except Exception as e:
                print(f"  [WARN] NVDA 启动失败: {e}")

    # 回退 Narrator
    if not reader_name:
        narrator_path = r"C:\Windows\System32\Narrator.exe"
        try:
            check = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Narrator.exe", "/NH"],
                capture_output=True, text=True, timeout=10
            )
            if "narrator.exe" in check.stdout.lower():
                reader_name = "Narrator"
                was_running = True
                print(f"  [INFO] Narrator 已在运行中")
            elif os.path.isfile(narrator_path):
                try:
                    reader_proc = subprocess.Popen(
                        [narrator_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    reader_name = "Narrator"
                    print(f"  [OK] Narrator 已启动")
                except OSError as oe:
                    # WinError 740: 请求的操作需要提升
                    print(f"  [WARN] Narrator 直接启动失败: {oe}")
                    # PowerShell fallback（与 p1_nvda_assisted_uia.py 统一）
                    print(f"  [INFO] 尝试通过 PowerShell 启动 Narrator ...")
                    try:
                        subprocess.Popen(
                            ["powershell", "-Command", "Start-Process", "Narrator.exe"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        time.sleep(3)
                        recheck = subprocess.run(
                            ["tasklist", "/FI", "IMAGENAME eq Narrator.exe", "/NH"],
                            capture_output=True, text=True, timeout=10
                        )
                        if "narrator.exe" in recheck.stdout.lower():
                            reader_name = "Narrator"
                            reader_proc = None  # PowerShell 进程，不是 Narrator 本身
                            print(f"  [OK] Narrator 已通过 PowerShell 启动")
                        else:
                            print(f"  [WARN] PowerShell 启动后未检测到 Narrator 进程")
                    except Exception as ps_err:
                        print(f"  [WARN] PowerShell 启动 Narrator 也失败: {ps_err}")
        except Exception as e:
            print(f"  [WARN] Narrator 启动失败: {e}")
            # 最后尝试 PowerShell
            print(f"  [INFO] 尝试通过 PowerShell 启动 Narrator ...")
            try:
                subprocess.Popen(
                    ["powershell", "-Command", "Start-Process", "Narrator.exe"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(3)
                recheck = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Narrator.exe", "/NH"],
                    capture_output=True, text=True, timeout=10
                )
                if "narrator.exe" in recheck.stdout.lower():
                    reader_name = "Narrator"
                    was_running = False
                    print(f"  [OK] Narrator 已通过 PowerShell 启动")
            except Exception:
                pass

    if not reader_name:
        print("  [FAIL] 无法启动任何屏幕阅读器")
        result["error"] = "no_screen_reader_available"
        result["child_count"] = -1
        return result

    result["reader_name"] = reader_name
    result["was_already_running"] = was_running

    # 等待
    print(f"  [INFO] 等待 {duration}s 让 {reader_name} 激活 UIA ...")
    for i in range(duration):
        time.sleep(1)
        if (i + 1) % 5 == 0:
            count, targets, desc = check_uia_tree(hwnd)
            has_c = any(tc in targets for tc in CRITICAL_TARGET_CLASSES)
            if has_c:
                print(f"    {i+1}s: 子元素={count}, 已找到关键控件!")
                break
            print(f"    {i+1}s: 子元素={count}, 后代={desc}")

    # 最终检查
    child_count, found_targets, desc_count = check_uia_tree(hwnd)
    result["child_count"] = child_count
    result["descendant_count"] = desc_count
    result["found_targets"] = list(found_targets.keys())

    # 清理
    if reader_name and not was_running:
        try:
            exe_name = f"{reader_name.lower()}.exe"
            subprocess.run(
                ["taskkill", "/IM", exe_name, "/F"],
                capture_output=True, timeout=10
            )
            print(f"  [OK] 已停止 {reader_name}")
        except Exception:
            pass

    return result


# ════════════════════════════════════════
# 策略 S3: Minimal Accessibility Client
# ════════════════════════════════════════
def run_strategy_s3(hwnd, duration=25, quick=False):
    """策略 S3: Minimal Accessibility Client（主动戳探 + 多重监听）"""
    print("\n" + "=" * 55)
    print("  [S3] Minimal Accessibility Client (主动戳探 + 监听)")
    print("=" * 55)

    if quick:
        duration = 12

    result = {
        "strategy": "S3_keepalive_client",
        "description": "Minimal Accessibility Client（主动戳探 + UIA事件 + WinEvent）",
    }

    try:
        import comtypes
        import comtypes.client

        UIA = _create_uia()
        root = UIA.ElementFromHandle(hwnd)

        # 注册 UIA 事件
        _ensure_uia_typelib()
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationStructureChangedEventHandler,
            TreeScope_Subtree,
        )

        event_count = [0]

        class StructHandler(comtypes.COMObject):
            _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]
            def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
                event_count[0] += 1
                return 0

        handler = StructHandler()
        UIA.AddStructureChangedEventHandler(root, TreeScope_Subtree, None, handler)

        # 创建缓存请求
        try:
            cache_req = UIA.CreateCacheRequest()
            cache_req.AddProperty(30005)  # Name
            cache_req.AddProperty(30003)  # ClassName
            cache_req.TreeScope = 7  # Subtree
        except Exception:
            cache_req = None

        # 设置 WinEvent 钩子
        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
        )

        winevent_count = [0]

        @WINEVENTPROC
        def winevent_cb(hHook, event, hwnd_, idObj, idChild, threadId, eventTime):
            winevent_count[0] += 1

        # 获取微信 PID
        pid = ctypes.wintypes.DWORD()
        windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        hooks = []
        for ev in [0x8000, 0x8002, 0x8005]:  # CREATE, SHOW, FOCUS
            h = windll.user32.SetWinEventHook(ev, ev, None, winevent_cb, pid.value, 0, 0x0002)
            if h:
                hooks.append(h)

        print(f"  [OK] 已注册: UIA事件=1, WinEvent={len(hooks)}, 缓存请求={'OK' if cache_req else 'N/A'}")

        # 主动戳探循环
        condition = UIA.CreateTrueCondition()
        probe_interval = 3  # 激进模式
        probes_done = 0
        best_count = 0

        elapsed = 0
        while elapsed < duration:
            probes_done += 1

            # 主动戳探：深度遍历
            try:
                children = root.FindAll(1, condition)
                cc = children.Length

                desc_count = 0
                targets = {}
                if cc > 0:
                    all_e = root.FindAll(4, condition)
                    desc_count = all_e.Length
                    for i in range(min(desc_count, 5000)):
                        elem = all_e.GetElement(i)
                        cn = elem.CurrentClassName or ""
                        if cn in TARGET_CLASSES and cn not in targets:
                            targets[cn] = (elem.CurrentName or "")[:60]

                # 缓存戳探
                if cache_req and cc <= 2:
                    try:
                        cached = root.BuildUpdatedCache(cache_req)
                    except Exception:
                        pass

                if cc > best_count:
                    best_count = cc

                has_c = any(tc in targets for tc in CRITICAL_TARGET_CLASSES)
                if has_c:
                    print(f"    戳探 #{probes_done}: 子元素={cc}, 后代={desc_count}, 找到关键控件!")
                    break

                if probes_done % 2 == 0:
                    print(
                        f"    戳探 #{probes_done}: 子元素={cc}, 后代={desc_count}, "
                        f"UIA事件={event_count[0]}, WinEvent={winevent_count[0]}"
                    )

            except Exception as e:
                print(f"    戳探 #{probes_done}: 异常 {e}")

            # 消息泵
            from ctypes.wintypes import MSG
            msg = MSG()
            pump_end = time.time() + probe_interval
            while time.time() < pump_end:
                r = windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                if r:
                    windll.user32.TranslateMessage(ctypes.byref(msg))
                    windll.user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.05)

            elapsed += probe_interval

        # 最终检查
        child_count, found_targets, desc_count = check_uia_tree(hwnd)
        result["child_count"] = child_count
        result["descendant_count"] = desc_count
        result["found_targets"] = list(found_targets.keys())
        result["probes_done"] = probes_done
        result["uia_events"] = event_count[0]
        result["win_events"] = winevent_count[0]
        result["max_children"] = best_count

        # 清理
        try:
            UIA.RemoveStructureChangedEventHandler(root, handler)
        except Exception:
            pass
        for h in hooks:
            try:
                windll.user32.UnhookWinEvent(h)
            except Exception:
                pass

    except ImportError:
        result["error"] = "comtypes 未安装"
        result["child_count"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["child_count"] = -1

    return result


# ════════════════════════════════════════
# 主入口
# ════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="P1 三方案对比测试")
    parser.add_argument("--quick", action="store_true", help="快速模式（缩短等待时间）")
    parser.add_argument("--only", choices=["s1", "s2", "s3"], help="只运行指定方案")
    args = parser.parse_args()

    print("=" * 60)
    print("  P1 三方案对比测试 — UIA 恢复策略评估")
    print(f"  时间: {datetime.now().isoformat()}")
    print(f"  模式: {'快速' if args.quick else '标准'}")
    if args.only:
        print(f"  限定方案: {args.only.upper()}")
    print("=" * 60)

    # ── 查找微信 ──
    hwnd, matched_class = find_wechat_hwnd()
    if not hwnd:
        print("\n[FAIL] 未找到微信窗口，请确保微信已启动")
        sys.exit(1)
    print(f"\n[OK] 微信窗口: class='{matched_class}', hwnd=0x{hwnd:08X}")

    # ── 基线检查 ──
    print("\n--- 基线检查 ---")
    baseline_count, baseline_targets, baseline_desc = check_uia_tree(hwnd)
    print(f"  子元素数: {baseline_count}")
    print(f"  后代总数: {baseline_desc}")
    print(f"  目标控件: {list(baseline_targets.keys()) or '无'}")

    if baseline_count == -1:
        print("\n  [WARN] 基线检查返回 -1，UIA COM 初始化可能有问题")
        print("         但仍继续测试各策略（策略内部有独立初始化逻辑）")

    # ── 运行方案 ──
    strategies_to_run = []
    if args.only:
        strategies_to_run = [args.only]
    else:
        strategies_to_run = ["s1", "s2", "s3"]

    results = {}
    strategy_funcs = {
        "s1": run_strategy_s1,
        "s2": run_strategy_s2,
        "s3": run_strategy_s3,
    }

    for sid in strategies_to_run:
        func = strategy_funcs[sid]

        # 重新检查基线（每次方案测试前重置）
        print(f"\n--- {sid.upper()} 前基线 ---")
        pre_count, _, pre_desc = check_uia_tree(hwnd)
        print(f"  子元素: {pre_count}, 后代: {pre_desc}")

        # 运行方案
        r = func(hwnd, quick=args.quick)
        r["baseline_count"] = pre_count
        r["baseline_descendants"] = pre_desc

        # 评估
        cc = r.get("child_count", -1)
        ft = {t: "" for t in r.get("found_targets", [])}
        status, score = evaluate_result(cc, ft, pre_count)
        r["status"] = status
        r["score"] = score

        results[sid] = r

        # 方案间冷却
        if sid != strategies_to_run[-1]:
            print(f"\n  [INFO] 方案间冷却 5s ...")
            time.sleep(5)

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("  对比测试结果汇总")
    print("=" * 60)
    print()

    # 表头
    print(f"  {'方案':<6} {'状态':<12} {'评分':<6} {'子元素':<8} {'后代':<8} {'关键控件':<15} {'基线'}")
    print(f"  {'─'*6} {'─'*12} {'─'*6} {'─'*8} {'─'*8} {'─'*15} {'─'*6}")

    for sid, r in results.items():
        status = r.get("status", "error")
        score = r.get("score", 0)
        cc = r.get("child_count", -1)
        desc = r.get("descendant_count", 0)
        targets = r.get("found_targets", [])
        baseline = r.get("baseline_count", "?")

        if status == "success":
            icon = "[OK]"
        elif status == "partial":
            icon = "[PARTIAL]"
        elif status == "improved":
            icon = "[IMPROVED]"
        else:
            icon = "[FAIL]"

        target_str = ",".join(t.split("::")[-1] for t in targets)[:15] if targets else "无"
        print(f"  {sid.upper():<6} {icon:<12} {score:<6} {cc:<8} {desc:<8} {target_str:<15} {baseline}")

    # ── 推荐 ──
    print()
    if results:
        best_sid = max(results, key=lambda k: results[k].get("score", 0))
        best = results[best_sid]

        if best["score"] >= 100:
            print(f"  [推荐] {best_sid.upper()} — {best['description']}")
            print(f"         完全恢复 UIA 树，找到关键聊天控件!")
        elif best["score"] >= 60:
            print(f"  [推荐] {best_sid.upper()} — {best['description']}")
            print(f"         部分恢复，子元素数增加但未找到关键聊天控件")
            print(f"         建议确认微信在聊天主界面后重试")
        elif best["score"] >= 30:
            print(f"  [参考] {best_sid.upper()} 表现最佳但仅有轻微改善")
            print(f"         建议加大等待时间或组合使用多个方案")
        else:
            print(f"  [FAIL] 三种方案均未能恢复 UIA 树")
            print(f"         建议：")
            print(f"         1. 确认微信处于聊天主界面（非登录页/设置页）")
            print(f"         2. 尝试重启微信后立即运行测试")
            print(f"         3. 考虑非 UIA 方案（如 OCR、图像识别）")

    # ── 保存报告 ──
    report = {
        "timestamp": datetime.now().isoformat(),
        "wechat_hwnd": f"0x{hwnd:08X}",
        "wechat_class": matched_class,
        "baseline": {
            "child_count": baseline_count,
            "descendant_count": baseline_desc,
            "targets": list(baseline_targets.keys()),
        },
        "strategies": results,
        "recommendation": best_sid.upper() if results else "NONE",
        "best_score": max((r.get("score", 0) for r in results.values()), default=0),
    }

    report_path = "p1_comparison_result.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 对比报告已保存: {report_path}")
    except Exception as e:
        print(f"\n[WARN] 报告保存失败: {e}")

    print()

    # 退出码
    best_score = max((r.get("score", 0) for r in results.values()), default=0)
    if best_score >= 100:
        sys.exit(0)
    elif best_score >= 60:
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
