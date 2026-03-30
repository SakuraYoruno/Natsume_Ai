"""调试脚本：搜索联系人后截图，查看下拉列表坐标"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ctypes
import time

ctypes.windll.user32.SetProcessDPIAware()

import win32gui, win32con, win32api, win32clipboard

def find_wechat_hwnd():
    result = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd): return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title == '微信' and 'Qt' in cls:
            rect = win32gui.GetWindowRect(hwnd)
            w, h = rect[2]-rect[0], rect[3]-rect[1]
            if w > 200 and h > 200:
                result.append((hwnd, rect))
        return True
    win32gui.EnumWindows(cb, None)
    return result[0] if result else (None, None)

def bring_to_front(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    time.sleep(0.2)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
    fg = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
    ctypes.windll.user32.AttachThreadInput(cur, fg, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.AttachThreadInput(cur, fg, False)
    time.sleep(0.3)

def set_clipboard(text):
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    win32clipboard.CloseClipboard()

def key_down(vk): win32api.keybd_event(vk, 0, 0, 0)
def key_up(vk): win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
def hotkey(mod, key):
    key_down(mod); key_down(key); key_up(key); key_up(mod); time.sleep(0.05)
def press(vk):
    key_down(vk); key_up(vk); time.sleep(0.05)

def screenshot(path):
    import mss
    from mss.tools import to_png
    hwnd, rect = find_wechat_hwnd()
    l, t, r, b = rect
    with mss.mss() as sct:
        img = sct.grab({'left': l, 'top': t, 'width': r-l, 'height': b-t})
        to_png(img.rgb, img.size, output=path)
    print(f"截图: {path}")

hwnd, rect = find_wechat_hwnd()
l, t, r, b = rect
win_w, win_h = r-l, b-t
print(f"窗口: {win_w}x{win_h} at ({l},{t})")

bring_to_front(hwnd)

# 搜索
print("Ctrl+F 触发搜索...")
hotkey(win32con.VK_CONTROL, ord('F'))
time.sleep(0.6)

print("输入联系人...")
set_clipboard("文件传输助手")
hotkey(win32con.VK_CONTROL, ord('V'))
time.sleep(1.0)  # 等待下拉列表出现

# 截图查看下拉列表
screenshot("tools/_search_dropdown.png")
print("截图完成，查看 tools/_search_dropdown.png")
print(f"窗口左上角屏幕坐标: ({l}, {t})")
print(f"窗口尺寸: {win_w}x{win_h}")
print("请查看截图，找到联系人所在行的Y坐标（相对窗口）")

# 按ESC关闭搜索
press(win32con.VK_ESCAPE)
