#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
受众：CODEX
标题：P1 方案二 — NVDA/Narrator 屏幕阅读器辅助 UIA 恢复

启动一个轻量级的屏幕阅读器（优先 NVDA，回退 Narrator），
让其在后台运行一段时间，然后运行 verify_uia_recovery.py
验证微信 UIA 树是否恢复。

原理：
  屏幕阅读器（如 NVDA、Narrator）在启动时会注册为持久化的
  Accessibility 客户端，这会触发所有窗口（包括 Qt 应用）的
  UIA Provider 完整加载控件树。

  之前在 RUN 节点短时拉起 Narrator 未成功，可能原因：
  1. Narrator 启动到完全初始化需要时间
  2. 交互式计划任务环境下 Narrator 可能未绑定正确会话
  3. 需要给 Qt 应用更长时间响应 Accessibility 客户端的存在

  本脚本改进方案：
  - 尝试 NVDA（更强的 UIA 支持）
  - 如果没有 NVDA，回退到 Narrator
  - 启动后等待更长时间（可配置，默认 15 秒）
  - 在屏幕阅读器运行期间持续检测微信 UIA 树状态
  - 支持 --keep 模式保持屏幕阅读器运行

用法：
  # 基本用法（自动启动、检测、停止）
  python p1_nvda_assisted_uia.py

  # 保持屏幕阅读器运行（适合后续手动验证）
  python p1_nvda_assisted_uia.py --keep

  # 自定义等待时间（秒）
  python p1_nvda_assisted_uia.py --wait 30

  # 指定使用 Narrator（跳过 NVDA 检测）
  python p1_nvda_assisted_uia.py --narrator

依赖：
  pip install comtypes
  可选：安装 NVDA (https://www.nvaccess.org/)
"""

import sys
import io
import os
import time
import json
import subprocess
import signal
import argparse
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
]

# NVDA 常见安装路径
NVDA_PATHS = [
    r"C:\Program Files (x86)\NVDA\nvda.exe",
    r"C:\Program Files\NVDA\nvda.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\NVDA\nvda.exe"),
]

# Narrator 路径
NARRATOR_PATH = r"C:\Windows\System32\Narrator.exe"


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
    """检查微信 UIA 树状态，返回 (child_count, found_targets)"""
    try:
        import comtypes
        import comtypes.client

        # 确保类型库已生成
        try:
            comtypes.client.GetModule("UIAutomationCore.dll")
        except OSError:
            import os as _os
            dll_path = _os.path.join(
                _os.environ.get("SystemRoot", r"C:\Windows"),
                "System32", "UIAutomationCore.dll"
            )
            comtypes.client.GetModule(dll_path)

        from comtypes.gen.UIAutomationClient import IUIAutomation

        UIA = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=IUIAutomation
        )

        root = UIA.ElementFromHandle(hwnd)
        condition = UIA.CreateTrueCondition()

        # 直接子元素
        children = root.FindAll(1, condition)  # TreeScope_Children = 1
        child_count = children.Length

        # 深度搜索目标控件
        found_targets = {}
        if child_count > 2:
            all_elems = root.FindAll(4, condition)  # TreeScope_Descendants = 4
            for i in range(min(all_elems.Length, 5000)):
                elem = all_elems.GetElement(i)
                cn = elem.CurrentClassName or ""
                if cn in TARGET_CLASSES and cn not in found_targets:
                    name = (elem.CurrentName or "")[:60]
                    found_targets[cn] = name

        return child_count, found_targets

    except Exception as e:
        print(f"  [WARN] UIA 树检查异常: {e}")
        return -1, {}


def find_nvda():
    """查找 NVDA 安装路径"""
    for path in NVDA_PATHS:
        if os.path.isfile(path):
            return path

    # 尝试在 PATH 中查找
    try:
        result = subprocess.run(
            ["where", "nvda.exe"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            path = result.stdout.strip().splitlines()[0]
            if os.path.isfile(path):
                return path
    except Exception:
        pass

    return None


def is_process_running(name):
    """检查进程是否正在运行"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        return name.lower() in result.stdout.lower()
    except Exception:
        return False


def start_screen_reader(use_narrator=False):
    """
    启动屏幕阅读器。
    返回 (reader_name, process_or_None, was_already_running)
    """
    # --- 尝试 NVDA ---
    if not use_narrator:
        nvda_path = find_nvda()
        if nvda_path:
            # 检查是否已在运行
            if is_process_running("nvda.exe"):
                print("[INFO] NVDA 已经在运行中")
                return "NVDA", None, True

            print(f"[INFO] 启动 NVDA: {nvda_path}")
            try:
                # NVDA 支持 --minimal 最小化模式
                proc = subprocess.Popen(
                    [nvda_path, "--minimal"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[OK] NVDA 已启动 (PID: {proc.pid})")
                return "NVDA", proc, False
            except Exception as e:
                print(f"[WARN] NVDA 启动失败: {e}")
        else:
            print("[INFO] 未找到 NVDA 安装，尝试 Narrator ...")

    # --- 回退到 Narrator ---
    if is_process_running("Narrator.exe"):
        print("[INFO] Narrator 已经在运行中")
        return "Narrator", None, True

    if os.path.isfile(NARRATOR_PATH):
        print(f"[INFO] 启动 Narrator: {NARRATOR_PATH}")
        try:
            proc = subprocess.Popen(
                [NARRATOR_PATH],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[OK] Narrator 已启动 (PID: {proc.pid})")
            return "Narrator", proc, False
        except Exception as e:
            print(f"[WARN] Narrator 启动失败: {e}")

    # --- 尝试通过 PowerShell 启动 Narrator ---
    print("[INFO] 尝试通过 PowerShell 启动 Narrator ...")
    try:
        proc = subprocess.Popen(
            ["powershell", "-Command", "Start-Process", "Narrator.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        if is_process_running("Narrator.exe"):
            print("[OK] Narrator 已通过 PowerShell 启动")
            return "Narrator", None, False  # proc 是 powershell 进程，不是 narrator
        else:
            print("[WARN] Narrator 启动后未检测到进程")
    except Exception as e:
        print(f"[WARN] PowerShell 启动 Narrator 失败: {e}")

    return None, None, False


def stop_screen_reader(reader_name, proc, was_already_running):
    """停止屏幕阅读器"""
    if was_already_running:
        print(f"[INFO] {reader_name} 是预先运行的，不自动停止")
        return

    if reader_name == "NVDA":
        print("[INFO] 停止 NVDA ...")
        try:
            subprocess.run(
                ["taskkill", "/IM", "nvda.exe", "/F"],
                capture_output=True, timeout=10
            )
            print("[OK] NVDA 已停止")
        except Exception as e:
            print(f"[WARN] 停止 NVDA 失败: {e}")

    elif reader_name == "Narrator":
        print("[INFO] 停止 Narrator ...")
        try:
            subprocess.run(
                ["taskkill", "/IM", "Narrator.exe", "/F"],
                capture_output=True, timeout=10
            )
            print("[OK] Narrator 已停止")
        except Exception as e:
            print(f"[WARN] 停止 Narrator 失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="P1 方案二：NVDA/Narrator 辅助 UIA 恢复")
    parser.add_argument("--keep", action="store_true", help="保持屏幕阅读器运行不自动停止")
    parser.add_argument("--wait", type=int, default=15, help="启动后等待秒数（默认 15）")
    parser.add_argument("--narrator", action="store_true", help="跳过 NVDA，直接使用 Narrator")
    parser.add_argument("--checks", type=int, default=5, help="检查次数（默认 5）")
    parser.add_argument("--interval", type=int, default=5, help="每次检查间隔秒数（默认 5）")
    args = parser.parse_args()

    print("=" * 60)
    print("  P1 方案二：NVDA/Narrator 屏幕阅读器辅助 UIA 恢复")
    print(f"  时间: {datetime.now().isoformat()}")
    print("=" * 60)

    # ── 查找微信窗口 ──
    hwnd, matched_class = find_wechat_hwnd()
    if not hwnd:
        print("[FAIL] 未找到微信窗口")
        sys.exit(1)
    print(f"[OK] 微信窗口: class='{matched_class}', hwnd=0x{hwnd:08X}")

    # ── 基线检查 ──
    baseline_count, baseline_targets = check_uia_tree(hwnd)
    print(f"\n[INFO] 基线检查: 子元素数={baseline_count}, 目标控件={list(baseline_targets.keys())}")

    # ── 启动屏幕阅读器 ──
    print()
    reader_name, proc, was_running = start_screen_reader(use_narrator=args.narrator)

    if not reader_name:
        print("\n[FAIL] 无法启动任何屏幕阅读器 (NVDA / Narrator)")
        print("  建议：")
        print("  1. 安装 NVDA: https://www.nvaccess.org/download/")
        print("  2. 或确保 Narrator 可用（Windows 设置 > 辅助功能 > 讲述人）")
        sys.exit(1)

    # ── 等待初始化 ──
    print(f"\n[INFO] 等待 {args.wait}s 让 {reader_name} 完成初始化 ...")
    for i in range(args.wait):
        time.sleep(1)
        if (i + 1) % 5 == 0:
            print(f"  ... {i + 1}/{args.wait}s")

    # ── 多轮检查 ──
    print(f"\n[INFO] 开始 {args.checks} 轮 UIA 树检查（间隔 {args.interval}s）...\n")

    check_results = []
    best_count = baseline_count
    best_targets = {}

    for check_num in range(1, args.checks + 1):
        child_count, found_targets = check_uia_tree(hwnd)

        status = "improved" if child_count > baseline_count else (
            "same" if child_count == baseline_count else "degraded"
        )

        has_critical = any(
            tc in found_targets
            for tc in ["mmui::ChatSingleWindow", "mmui::ChatTextItemView"]
        )

        result = {
            "check": check_num,
            "child_count": child_count,
            "found_targets": list(found_targets.keys()),
            "has_critical_targets": has_critical,
            "status": status,
        }
        check_results.append(result)

        if child_count > best_count:
            best_count = child_count
            best_targets = found_targets

        # 输出
        status_icon = "[OK]" if has_critical else (
            "[PARTIAL]" if child_count > 2 else "[WAIT]"
        )
        print(
            f"  {status_icon} 检查 #{check_num}: "
            f"子元素={child_count} ({status}), "
            f"目标控件={list(found_targets.keys()) or '无'}"
        )

        # 如果已经找到关键控件，提前结束
        if has_critical:
            print(f"\n[OK] 已找到关键聊天控件! 提前结束检查。")
            break

        if check_num < args.checks:
            time.sleep(args.interval)

    # ── 结论 ──
    print(f"\n{'=' * 60}")
    print(f"  检查结束")
    print(f"{'=' * 60}")

    final_count, final_targets = check_uia_tree(hwnd)
    final_has_critical = any(
        tc in final_targets
        for tc in ["mmui::ChatSingleWindow", "mmui::ChatTextItemView"]
    )

    if final_has_critical:
        overall = "success"
        print(f"\n[OK] P1 方案二成功! {reader_name} 恢复了完整 UIA 树!")
        print(f"     子元素数: {baseline_count} -> {final_count}")
        print(f"     找到关键控件: {list(final_targets.keys())}")
    elif final_count > baseline_count and final_count > 2:
        overall = "partial"
        print(f"\n[PARTIAL] 部分恢复。子元素数增加但未找到关键聊天控件。")
        print(f"          子元素数: {baseline_count} -> {final_count}")
        print(f"          可能原因: 微信不在聊天主界面")
    else:
        overall = "failed"
        print(f"\n[FAIL] P1 方案二未恢复 UIA 树。")
        print(f"       子元素数: {baseline_count} -> {final_count}")

    # ── 保存报告 ──
    report = {
        "strategy": "p1_nvda_assisted_uia",
        "timestamp": datetime.now().isoformat(),
        "screen_reader": reader_name,
        "was_already_running": was_running,
        "wechat_hwnd": f"0x{hwnd:08X}",
        "wechat_class": matched_class,
        "wait_seconds": args.wait,
        "baseline_children": baseline_count,
        "final_children": final_count,
        "final_targets": list(final_targets.keys()),
        "overall_result": overall,
        "check_results": check_results,
    }

    report_path = "p1_nvda_assisted_status.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 报告已保存: {report_path}")
    except Exception as e:
        print(f"[WARN] 报告保存失败: {e}")

    # ── 停止屏幕阅读器 ──
    if not args.keep:
        print()
        stop_screen_reader(reader_name, proc, was_running)
        print("[INFO] 如需保持运行，使用 --keep 参数")
    else:
        print(f"\n[INFO] --keep 模式: {reader_name} 将保持运行")
        print(f"       手动停止: taskkill /IM {reader_name.lower()}.exe /F")

    # ── 退出码 ──
    if overall == "success":
        sys.exit(0)
    elif overall == "partial":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
