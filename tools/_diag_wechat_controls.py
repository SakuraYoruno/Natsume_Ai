"""微信窗口控件完整诊断脚本（微信 4.x 新版）。"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import time

try:
    from pywinauto import Application, Desktop
except ImportError:
    print("pywinauto 未安装")
    sys.exit(1)

# 连接微信主窗口
print("=== 连接微信 ===")
app = None
main_wnd = None

# 方式1: 通过 Desktop 找最大的微信窗口
try:
    desktop = Desktop(backend="uia")
    windows = list(desktop.windows())
    print(f"桌面窗口数: {len(windows)}")
    for w in windows:
        try:
            title = w.window_text()
            cls = getattr(w.element_info, 'class_name', '')
            rect = w.rectangle()
            w_size = rect.right - rect.left
            h_size = rect.bottom - rect.top
            if w_size > 100 and h_size > 100:
                print(f"  [{w_size}x{h_size}] title={title!r} class={cls!r}")
                # 精确匹配微信聊天窗口（排除企业微信）
                is_wechat = (
                    title == '微信'
                    or (('Weixin' in cls or 'Qt5151' in cls) and '企业微信' not in title and title in ('微信', 'WeChat'))
                )
                if is_wechat:
                    main_wnd = w
                    print(f"    *** 选中此窗口 ***")
        except Exception as e:
            pass
except Exception as e:
    print(f"Desktop 枚举失败: {e}")

if main_wnd is None:
    print("\n尝试通过 Application 连接...")
    try:
        app = Application(backend="uia").connect(path="Weixin.exe")
        windows = list(app.windows())
        print(f"进程窗口数: {len(windows)}")
        for w in windows:
            try:
                title = w.window_text()
                rect = w.rectangle()
                w_size = rect.right - rect.left
                h_size = rect.bottom - rect.top
                print(f"  [{w_size}x{h_size}] title={title!r}")
                if w_size > 200 and h_size > 200:
                    main_wnd = w
                    print(f"    *** 选中此窗口 ***")
            except Exception:
                pass
    except Exception as e:
        print(f"Application 连接失败: {e}")

if main_wnd is None:
    print("无法找到微信主窗口，退出")
    sys.exit(1)

print(f"\n=== 主窗口信息 ===")
try:
    print(f"标题: {main_wnd.window_text()!r}")
    rect = main_wnd.rectangle()
    print(f"尺寸: {rect.right-rect.left}x{rect.bottom-rect.top}")
    print(f"类名: {getattr(main_wnd.element_info, 'class_name', 'N/A')!r}")
except Exception as e:
    print(f"获取窗口信息失败: {e}")

# 打印所有控件（按类型分组）
print("\n=== 所有控件（按类型）===")
for ct in ("Edit", "Document", "Button", "List", "ListItem", "Pane", "Text", "Custom"):
    try:
        items = list(main_wnd.descendants(control_type=ct))
        if not items:
            continue
        print(f"\n  [{ct}] 共 {len(items)} 个:")
        for i, ctrl in enumerate(items[:20]):
            try:
                name = ctrl.window_text() or ''
                auto_id = getattr(ctrl.element_info, 'automation_id', '') or ''
                class_name = getattr(ctrl.element_info, 'class_name', '') or ''
                rect = ctrl.rectangle()
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                print(f"    [{i}] name={name!r:35s} auto_id={auto_id!r:20s} class={class_name!r:20s} size={w}x{h}")
            except Exception as e:
                print(f"    [{i}] 获取失败: {e}")
    except Exception as e:
        print(f"  [{ct}] 获取失败: {e}")

# 特别打印前50个控件（不分类型）
print("\n=== 前50个控件（全部）===")
try:
    all_items = list(main_wnd.descendants())
    print(f"总控件数: {len(all_items)}")
    for i, ctrl in enumerate(all_items[:50]):
        try:
            name = ctrl.window_text() or ''
            ct = str(getattr(ctrl.element_info, 'control_type', '') or '')
            auto_id = getattr(ctrl.element_info, 'automation_id', '') or ''
            print(f"  [{i:3d}] {ct:15s} name={name!r:35s} auto_id={auto_id!r}")
        except Exception as e:
            print(f"  [{i:3d}] 获取失败: {e}")
except Exception as e:
    print(f"获取所有控件失败: {e}")

print("\n完成。")
