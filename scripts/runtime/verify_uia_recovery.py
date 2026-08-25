#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
受众：CODEX
标题：微信 4.x UIA 树恢复验证脚本

验证 P0 方案（UIA 客户端冒充）是否能让微信暴露完整的控件树。
包含三种 Python 实现，任一成功即说明方案可行。

用法：
  pip install comtypes pywinauto uiautomation
  python verify_uia_recovery.py

输出：
  - 每种方案的子元素数量
  - 是否找到 mmui::ChatSingleWindow / mmui::ChatTextItemView
  - 最终汇总结论

前置条件：
  - 微信已启动（PC 端 4.x 版本）
  - 有至少一个打开的聊天窗口（用于验证 ChatSingleWindow）

关联文档：微信4.x_UIA树恢复方案_可操作指南.md
"""
import sys
import io
import time
import json
import platform
from datetime import datetime
from ctypes import windll, wintypes

# ──────────────────────────────────────────────
# 编码兼容：强制 stdout/stderr 使用 UTF-8
# 解决 RUN 节点默认控制台编码下 emoji 输出触发 UnicodeEncodeError
# ──────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
# 微信不同版本/环境可能使用不同的窗口类名，按优先级排列
WECHAT_CLASS_NAMES = [
    "WeChatMainWndForPC",     # 微信 3.x 及部分 4.x 早期版本
    "Qt51514QWindowIcon",     # 微信 4.x（QT 重写版本，真机实测确认）
]
WECHAT_WINDOW_TITLE = "微信"  # 兜底：按窗口标题搜索
WAIT_AFTER_ATTACH = 3  # 附着后等待秒数
MAX_DISPLAY_CHILDREN = 30  # 最多显示的子元素数
TARGET_CLASSES = [
    "mmui::ChatSingleWindow",
    "mmui::ChatTextItemView",
    "mmui::ChatMsgListView",
    "mmui::SessionListView",
    "mmui::SearchBar",
]
# 成功判定的核心控件（必须至少找到其中一个才算 UI 树真正恢复）
CRITICAL_TARGET_CLASSES = [
    "mmui::ChatSingleWindow",
    "mmui::ChatTextItemView",
]


def find_wechat_hwnd():
    """查找微信主窗口句柄，支持多类名 + 标题兜底"""
    # 策略 1：按已知类名逐个尝试
    for class_name in WECHAT_CLASS_NAMES:
        hwnd = windll.user32.FindWindowW(class_name, None)
        if hwnd:
            print(f"[OK] 找到微信窗口: class_name='{class_name}', hwnd=0x{hwnd:08X} ({hwnd})")
            return hwnd, class_name

    # 策略 2：按窗口标题"微信"兜底搜索
    print(f"[INFO] 已知类名均未命中，尝试按标题'{WECHAT_WINDOW_TITLE}'搜索...")
    hwnd = windll.user32.FindWindowW(None, WECHAT_WINDOW_TITLE)
    if hwnd:
        # 获取实际类名
        buf = (wintypes.WCHAR * 256)()
        windll.user32.GetClassNameW(hwnd, buf, 256)
        actual_class = buf.value
        print(f"[OK] 按标题找到微信窗口: class_name='{actual_class}', hwnd=0x{hwnd:08X} ({hwnd})")
        return hwnd, actual_class

    # 全部失败
    print("[FAIL] 未找到微信窗口，请确保微信已启动")
    print(f"   已尝试类名: {WECHAT_CLASS_NAMES}")
    print(f"   已尝试标题: '{WECHAT_WINDOW_TITLE}'")
    sys.exit(1)


def deep_search_targets(root_func, label=""):
    """深度搜索目标控件类名"""
    found = {}
    try:
        descendants = root_func()
        total = 0
        for elem in descendants:
            total += 1
            cn = getattr(elem, 'CurrentClassName', None) or getattr(elem, 'ClassName', None) or ""
            if cn in TARGET_CLASSES and cn not in found:
                name = getattr(elem, 'CurrentName', None) or getattr(elem, 'Name', None) or ""
                found[cn] = name[:80]
                print(f"    [FOUND] {cn}: {name[:60]}")
            if total >= 5000:  # 安全上限
                break
        print(f"    深度遍历共 {total} 个后代元素")
    except Exception as e:
        print(f"    [WARN] 深度搜索异常: {e}")
    return found


def evaluate_success(child_count, found_targets):
    """
    双条件成功判定（升级版）：
    - 条件 1（核心）：必须在后代中找到 mmui::ChatSingleWindow 或 mmui::ChatTextItemView
    - 条件 2（辅助）：直接子元素数量 > 2
    两个条件都满足才算真正成功。仅满足 child_count > 2 视为 partial（可能是登录态等非聊天界面）。
    """
    has_critical = any(tc in found_targets for tc in CRITICAL_TARGET_CLASSES)
    has_many_children = child_count > 2

    if has_critical and has_many_children:
        return "success"
    elif has_many_children and not has_critical:
        return "partial"  # 有子元素但没找到聊天控件，可能不在聊天界面
    else:
        return "failed"


# ──────────────────────────────────────────────
# 方案 A: comtypes 直接调用 COM
# ──────────────────────────────────────────────
def test_comtypes(hwnd):
    """P0-A: comtypes 直接调用 IUIAutomation COM 接口"""
    print("\n" + "=" * 50)
    print("[A] comtypes (COM 直调)")
    print("=" * 50)

    try:
        import comtypes
        import comtypes.client
    except ImportError:
        print("  [WARN] comtypes 未安装: pip install comtypes")
        return {"method": "comtypes", "status": "not_installed", "child_count": 0}

    try:
        # 确保 UIAutomationClient 类型库已加载到 comtypes.gen
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

        # 创建 CUIAutomation 实例
        UIA = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=IUIAutomation
        )

        # 通过句柄附着 —— 关键步骤！触发 UIA Provider 加载
        print(f"  附着窗口 hwnd=0x{hwnd:08X} ...")
        root = UIA.ElementFromHandle(hwnd)

        print(f"  等待 {WAIT_AFTER_ATTACH}s 让 UI 树加载...")
        time.sleep(WAIT_AFTER_ATTACH)

        # 获取直接子元素
        condition = UIA.CreateTrueCondition()
        children = root.FindAll(1, condition)  # TreeScope_Children = 1
        count = children.Length

        print(f"  直接子元素数量: {count}")

        # 显示子元素
        for i in range(min(count, MAX_DISPLAY_CHILDREN)):
            elem = children.GetElement(i)
            cn = elem.CurrentClassName or ""
            name = (elem.CurrentName or "")[:60]
            print(f"    [{i:02d}] ClassName: {cn}, Name: {name}")

        result = {
            "method": "comtypes",
            "child_count": count,
            "found_targets": {},
        }

        if count > 2:
            print(f"\n  [INFO] 检测到 {count} 个直接子元素，开始深度搜索目标控件...")

            def get_descendants():
                all_elems = root.FindAll(4, condition)  # TreeScope_Descendants = 4
                for i in range(all_elems.Length):
                    yield all_elems.GetElement(i)

            found = deep_search_targets(get_descendants, "comtypes")
            result["found_targets"] = found

            for tc in TARGET_CLASSES:
                if tc not in found:
                    print(f"    [WARN] 未找到 {tc}（可能需要打开相应面板）")

            # 双条件判定
            result["status"] = evaluate_success(count, found)
            if result["status"] == "success":
                print(f"\n  [OK] UI 树已恢复且找到关键聊天控件！")
            elif result["status"] == "partial":
                print(f"\n  [PARTIAL] 有 {count} 个子元素，但未找到关键聊天控件")
                print(f"           可能原因：微信不在聊天主界面（登录态/设置页等）")
        else:
            print(f"\n  [FAIL] UI 树未恢复（仅 {count} 个元素）")
            result["status"] = "failed"

        # 保持引用存活（不释放 root 和 UIA）
        result["_refs"] = (UIA, root)
        return result

    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        import traceback
        traceback.print_exc()
        return {"method": "comtypes", "status": "error", "child_count": 0, "error": str(e)}


# ──────────────────────────────────────────────
# 方案 B: pywinauto
# ──────────────────────────────────────────────
def test_pywinauto(hwnd):
    """P0-B: pywinauto UIA backend"""
    print("\n" + "=" * 50)
    print("[B] pywinauto (UIA backend)")
    print("=" * 50)

    try:
        from pywinauto import Desktop
    except ImportError:
        print("  [WARN] pywinauto 未安装: pip install pywinauto")
        return {"method": "pywinauto", "status": "not_installed", "child_count": 0}

    try:
        desktop = Desktop(backend="uia")

        # 连接微信窗口 —— 尝试多种类名
        print(f"  连接微信窗口...")
        wechat = None
        matched_class = None
        for class_name in WECHAT_CLASS_NAMES:
            try:
                wechat = desktop.window(class_name=class_name)
                # 触发实际查找（pywinauto 是延迟绑定的）
                _ = wechat.element_info
                matched_class = class_name
                print(f"  [OK] pywinauto 通过 class_name='{class_name}' 连接成功")
                break
            except Exception:
                continue

        if wechat is None or matched_class is None:
            # 按标题兜底
            try:
                wechat = desktop.window(title=WECHAT_WINDOW_TITLE)
                _ = wechat.element_info
                matched_class = wechat.element_info.class_name
                print(f"  [OK] pywinauto 通过 title='{WECHAT_WINDOW_TITLE}' 连接成功 (class={matched_class})")
            except Exception as e:
                print(f"  [FAIL] pywinauto 无法连接微信窗口: {e}")
                return {"method": "pywinauto", "status": "error", "child_count": 0, "error": str(e)}

        print(f"  等待 {WAIT_AFTER_ATTACH}s ...")
        time.sleep(WAIT_AFTER_ATTACH)

        children = wechat.children()
        count = len(children)

        print(f"  直接子元素数量: {count}")

        for i, child in enumerate(children[:MAX_DISPLAY_CHILDREN]):
            info = child.element_info
            cn = info.class_name or ""
            name = (info.name or "")[:60]
            print(f"    [{i:02d}] ClassName: {cn}, Name: {name}")

        result = {
            "method": "pywinauto",
            "child_count": count,
            "found_targets": {},
        }

        if count > 2:
            print(f"\n  [INFO] 检测到 {count} 个直接子元素，搜索目标控件...")
            for tc in TARGET_CLASSES:
                try:
                    ctrl = wechat.child_window(class_name=tc)
                    ctrl_info = ctrl.element_info
                    result["found_targets"][tc] = (ctrl_info.name or "")[:80]
                    print(f"    [FOUND] {tc}: {ctrl_info.name[:60] if ctrl_info.name else ''}")
                except Exception:
                    print(f"    [WARN] 未找到 {tc}")

            # 双条件判定
            result["status"] = evaluate_success(count, result["found_targets"])
            if result["status"] == "success":
                print(f"\n  [OK] UI 树已恢复且找到关键聊天控件！")
            elif result["status"] == "partial":
                print(f"\n  [PARTIAL] 有 {count} 个子元素，但未找到关键聊天控件")
                print(f"           可能原因：微信不在聊天主界面（登录态/设置页等）")
        else:
            print(f"\n  [FAIL] UI 树未恢复（仅 {count} 个元素）")
            result["status"] = "failed"

        result["_refs"] = (desktop, wechat)
        return result

    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        import traceback
        traceback.print_exc()
        return {"method": "pywinauto", "status": "error", "child_count": 0, "error": str(e)}


# ──────────────────────────────────────────────
# 方案 C: uiautomation 库
# ──────────────────────────────────────────────
def test_uiautomation(hwnd):
    """P0-C: uiautomation 库"""
    print("\n" + "=" * 50)
    print("[C] uiautomation 库")
    print("=" * 50)

    try:
        import uiautomation as auto
    except ImportError:
        print("  [WARN] uiautomation 未安装: pip install uiautomation")
        return {"method": "uiautomation", "status": "not_installed", "child_count": 0}

    try:
        print(f"  通过句柄获取控件...")
        wechat = auto.ControlFromHandle(hwnd)

        print(f"  等待 {WAIT_AFTER_ATTACH}s ...")
        time.sleep(WAIT_AFTER_ATTACH)

        children = wechat.GetChildren()
        count = len(children)

        print(f"  直接子元素数量: {count}")

        for i, child in enumerate(children[:MAX_DISPLAY_CHILDREN]):
            cn = child.ClassName or ""
            name = (child.Name or "")[:60]
            print(f"    [{i:02d}] ClassName: {cn}, Name: {name}")

        result = {
            "method": "uiautomation",
            "child_count": count,
            "found_targets": {},
        }

        if count > 2:
            print(f"\n  [INFO] 检测到 {count} 个直接子元素，深度搜索目标控件...")
            for tc in TARGET_CLASSES:
                try:
                    found_ctrl = wechat.Control(ClassName=tc, searchDepth=10)
                    if found_ctrl.Exists(maxSearchSeconds=3):
                        result["found_targets"][tc] = (found_ctrl.Name or "")[:80]
                        print(f"    [FOUND] {tc}: {found_ctrl.Name[:60] if found_ctrl.Name else ''}")
                    else:
                        print(f"    [WARN] 未找到 {tc}")
                except Exception:
                    print(f"    [WARN] 未找到 {tc}")

            # 双条件判定
            result["status"] = evaluate_success(count, result["found_targets"])
            if result["status"] == "success":
                print(f"\n  [OK] UI 树已恢复且找到关键聊天控件！")
            elif result["status"] == "partial":
                print(f"\n  [PARTIAL] 有 {count} 个子元素，但未找到关键聊天控件")
                print(f"           可能原因：微信不在聊天主界面（登录态/设置页等）")
        else:
            print(f"\n  [FAIL] UI 树未恢复（仅 {count} 个元素）")
            result["status"] = "failed"

        result["_refs"] = (wechat,)
        return result

    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        import traceback
        traceback.print_exc()
        return {"method": "uiautomation", "status": "error", "child_count": 0, "error": str(e)}


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  微信 4.x UIA 树恢复验证 -- P0 方案")
    print(f"  时间: {datetime.now().isoformat()}")
    print(f"  系统: {platform.platform()}")
    print(f"  Python: {platform.python_version()}")
    print("=" * 60)

    hwnd, matched_class = find_wechat_hwnd()
    print(f"  使用窗口: class_name='{matched_class}', hwnd=0x{hwnd:08X}")

    # ── 基线检查（附着前） ──
    print("\n--- 基线检查（附着前）---")
    try:
        import uiautomation as auto
        baseline_ctrl = auto.ControlFromHandle(hwnd)
        baseline_children = baseline_ctrl.GetChildren()
        baseline_count = len(baseline_children)
        print(f"  基线子元素数: {baseline_count}")
        # 释放基线引用
        del baseline_ctrl, baseline_children
        time.sleep(1)
    except ImportError:
        print("  [WARN] uiautomation 未安装，跳过基线检查")
        baseline_count = -1
    except Exception as e:
        print(f"  [WARN] 基线检查异常: {e}")
        baseline_count = -1

    # ── 逐个测试三种方案 ──
    results = []

    r1 = test_comtypes(hwnd)
    results.append(r1)

    r2 = test_pywinauto(hwnd)
    results.append(r2)

    r3 = test_uiautomation(hwnd)
    results.append(r3)

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("  验证结果汇总")
    print("=" * 60)
    print(f"\n  基线子元素数（附着前）: {baseline_count}")
    print(f"  匹配窗口类名: {matched_class}")
    print()

    any_success = False
    any_partial = False
    for r in results:
        method = r["method"]
        status = r["status"]
        count = r["child_count"]
        targets = r.get("found_targets", {})

        if status == "success":
            icon = "[OK]"
            any_success = True
        elif status == "partial":
            icon = "[PARTIAL]"
            any_partial = True
        elif status == "not_installed":
            icon = "[SKIP]"
        elif status == "error":
            icon = "[ERROR]"
        else:
            icon = "[FAIL]"

        print(f"  {icon} {method:15s} -- 子元素数: {count:3d}  状态: {status}")
        if targets:
            for tc in targets:
                print(f"      找到: {tc}")

    print()

    if any_success:
        print("=== P0 方案验证成功！===")
        print("  至少一种方法恢复了 UI 树并找到了关键聊天控件！")
        print("  下一步：修改 wxautox4_bridge.py 或对应桥接层，集成持久化微信客户端")
    elif any_partial:
        print("=== P0 方案部分成功 ===")
        print("  至少一种方法恢复了部分 UI 树（有子元素），但未找到关键聊天控件。")
        print("  可能原因：微信不在聊天主界面（登录态/设置页等）")
        print("  建议：确保微信处于聊天主界面后重新运行验证")
    else:
        all_not_installed = all(r["status"] == "not_installed" for r in results)
        if all_not_installed:
            print("=== 所有库均未安装！===")
            print("  请先安装：pip install comtypes pywinauto uiautomation")
        else:
            print("=== P0 方案验证失败 ===")
            print("  所有方法均未能恢复 UI 树。")
            print("  下一步：尝试 P1 方案（持久化无障碍服务）")
            print("    1. 运行 minimal_uia_listener.py 在后台保持连接")
            print("    2. 或安装 NVDA 屏幕阅读器并保持运行")
            print("    3. 然后重新运行本脚本")

    # ── 输出 JSON 结果（供自动化使用）──
    json_results = []
    for r in results:
        jr = {k: v for k, v in r.items() if not k.startswith("_")}
        json_results.append(jr)

    report = {
        "timestamp": datetime.now().isoformat(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "wechat_hwnd": f"0x{hwnd:08X}",
        "wechat_class_name": matched_class,
        "baseline_child_count": baseline_count,
        "results": json_results,
        "any_success": any_success,
        "any_partial": any_partial,
    }

    report_path = "uia_recovery_result.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] JSON 结果已保存: {report_path}")
    except Exception as e:
        print(f"\n[WARN] JSON 保存失败: {e}")

    # 保持所有引用存活（不退出脚本），让用户手动验证
    if any_success or any_partial:
        print("\n[INFO] 保持脚本运行中（维持 UIA 客户端连接）...")
        print("   现在可以用 Inspect.exe 或 Accessibility Insights 查看微信控件树")
        print("   按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n[END] 验证脚本已退出")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
