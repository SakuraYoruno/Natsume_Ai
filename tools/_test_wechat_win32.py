"""微信 4.x Win32 坐标点击发送测试脚本（DPI感知版）。

微信 4.x 使用 GPU 渲染，UIA 控件树只有 2 个节点，
必须改用 Win32 坐标点击方案。

关键：必须 SetProcessDPIAware() 才能获取正确的逻辑坐标。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ctypes
import time

# 必须在最开始设置 DPI 感知，否则坐标会被缩放
ctypes.windll.user32.SetProcessDPIAware()

try:
    import win32gui
    import win32con
    import win32api
    import win32clipboard
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("[ERROR] pywin32 未安装，请运行: pip install pywin32")
    sys.exit(1)


def find_wechat_hwnd():
    """找到微信主窗口句柄（DPI感知坐标）。"""
    result = []

    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title == '微信' and 'Qt' in cls:
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w > 200 and h > 200:
                result.append((hwnd, rect))
                print(f"  找到微信窗口: hwnd={hwnd} size={w}x{h} rect={rect}")
        return True

    win32gui.EnumWindows(enum_callback, None)
    if result:
        return result[0]
    return None, None


def bring_to_front(hwnd):
    """将窗口置于前台（使用 AttachThreadInput 方式，更可靠）。"""
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.15)

    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
    fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)

    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
    time.sleep(0.25)

    fg = win32gui.GetWindowText(ctypes.windll.user32.GetForegroundWindow())
    print(f"  当前前台窗口: {fg!r}")
    return fg == '微信'


def set_clipboard(text: str):
    """设置剪贴板文本。"""
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    win32clipboard.CloseClipboard()


def key_down(vk):
    win32api.keybd_event(vk, 0, 0, 0)

def key_up(vk):
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

def hotkey(mod, key):
    key_down(mod); key_down(key)
    key_up(key); key_up(mod)
    time.sleep(0.05)

def press(vk):
    key_down(vk); key_up(vk)
    time.sleep(0.05)

def click_pos(screen_x, screen_y):
    """在屏幕绝对坐标点击。"""
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, screen_x, screen_y, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, screen_x, screen_y, 0, 0)
    time.sleep(0.08)

def click_in_window(hwnd, rel_x, rel_y):
    """在窗口内相对坐标点击（逻辑坐标）。"""
    rect = win32gui.GetWindowRect(hwnd)
    screen_x = rect[0] + rel_x
    screen_y = rect[1] + rel_y
    print(f"    点击: 窗口相对({rel_x},{rel_y}) → 屏幕({screen_x},{screen_y})")
    click_pos(screen_x, screen_y)


def test_send(contact_name: str, msg: str):
    """测试发送消息。

    Args:
        contact_name: 联系人名称（用于搜索），如 '文件传输助手'
        msg: 要发送的消息
    """
    print(f"\n=== 查找微信窗口 ===")
    hwnd, rect = find_wechat_hwnd()
    if hwnd is None:
        print("[ERROR] 未找到微信窗口，请确保微信已打开")
        return False

    l, t, r, b = rect
    win_w = r - l
    win_h = b - t
    print(f"窗口尺寸(逻辑坐标): {win_w}x{win_h}")

    print(f"\n=== 置前微信窗口 ===")
    bring_to_front(hwnd)

    # -------------------------------------------------------
    # 步骤1：按 Ctrl+F 触发搜索（比坐标点击更可靠）
    # 微信 4.x 的 Ctrl+F 会打开联系人/会话搜索框
    # -------------------------------------------------------
    print(f"\n步骤1: Ctrl+F 触发搜索")
    hotkey(win32con.VK_CONTROL, ord('F'))
    time.sleep(0.5)

    # 步骤2：粘贴联系人名称
    print(f"步骤2: 输入联系人 {contact_name!r}")
    set_clipboard(contact_name)
    hotkey(win32con.VK_CONTROL, ord('V'))
    time.sleep(0.8)  # 等待搜索结果

    # 步骤3：按↓跳过第一项"搜索网络结果"，选中联系人后按 Enter
    # 微信4.x搜索下拉第一项是"搜索网络结果"（会跳内置浏览器），联系人在第二项
    print(f"步骤3: 按↓跳过'搜索网络结果'，选中联系人后按 Enter")
    press(win32con.VK_DOWN)   # 跳过第一项"搜索网络结果"
    time.sleep(0.15)
    press(win32con.VK_DOWN)   # 选中联系人（第二项）
    time.sleep(0.15)
    press(win32con.VK_RETURN)
    time.sleep(0.5)

    # 步骤4：点击输入框（底部中央）
    # 微信输入框在窗口底部，约 y = win_h - 80（相对窗口）
    input_rel_x = win_w // 2
    input_rel_y = win_h - 80
    print(f"步骤4: 点击输入框 (相对坐标: {input_rel_x}, {input_rel_y})")
    click_in_window(hwnd, input_rel_x, input_rel_y)
    time.sleep(0.2)

    # 步骤5：粘贴消息并发送
    print(f"步骤5: 粘贴消息并发送: {msg!r}")
    set_clipboard(msg)
    hotkey(win32con.VK_CONTROL, ord('V'))
    time.sleep(0.2)
    press(win32con.VK_RETURN)
    time.sleep(0.2)

    print(f"\n[OK] 发送完成，请检查微信 {contact_name!r} 是否收到消息")
    return True


def take_screenshot(output_path: str = 'tools/_wechat_screenshot.png'):
    """截取微信窗口截图。"""
    try:
        import mss
        from mss.tools import to_png
    except ImportError:
        print("[ERROR] 请安装 mss: pip install mss")
        return

    hwnd, rect = find_wechat_hwnd()
    if hwnd is None:
        print("[ERROR] 未找到微信窗口")
        return

    l, t, r, b = rect
    w, h = r - l, b - t
    with mss.mss() as sct:
        mon = {'left': l, 'top': t, 'width': w, 'height': h}
        img = sct.grab(mon)
        to_png(img.rgb, img.size, output=output_path)
        print(f"截图保存到: {output_path}  ({w}x{h})")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="微信 4.x Win32 发送测试")
    parser.add_argument("--contact", default="文件传输助手", help="联系人名称")
    parser.add_argument("--msg", default=f"Natsume AI Win32测试 {int(time.time())}", help="消息内容")
    parser.add_argument("--screenshot", action="store_true", help="截图后退出")
    args = parser.parse_args()

    if args.screenshot:
        take_screenshot()
    else:
        test_send(args.contact, args.msg)
