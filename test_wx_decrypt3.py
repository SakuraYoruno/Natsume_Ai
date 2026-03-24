"""
微信 4.x 数据库解密 - 优化版
策略: 先检查 PyWxDump 是否有获取 key 的方法
然后尝试从 key_info.db 中 protobuf 解密获取 key
"""
import os
import sqlite3
import hmac
import hashlib
from Cryptodome.Cipher import AES

# ===== 方法1: 检查 pywxdump 有没有针对新版的 key 获取 =====
print("=" * 60)
print("Method 1: Check pywxdump for key extraction")
print("=" * 60)

import pywxdump
pywxdump_dir = os.path.dirname(pywxdump.__file__)

# 检查 wx_info.py 中的方法
wx_info_path = os.path.join(pywxdump_dir, "wx_core", "wx_info.py")
print(f"wx_info.py path: {wx_info_path}")

# 查看其中的函数列表
import importlib
from pywxdump.wx_core import wx_info
methods = [m for m in dir(wx_info) if not m.startswith("_")]
print(f"wx_info methods: {methods}")

# 检查 get_wx_key 或类似方法
for name in methods:
    if "key" in name.lower() or "decrypt" in name.lower() or "info" in name.lower():
        print(f"  - {name}")

# ===== 方法2: 使用 pywxdump 的 get_wx_info 来获取 key =====
print("\n" + "=" * 60)
print("Method 2: Try get_wx_info with custom process name")
print("=" * 60)

from pywxdump.wx_core.utils import WX_OFFS
print(f"WX_OFFS has {len(WX_OFFS)} versions")
print(f"Latest versions: {list(WX_OFFS.keys())[-5:]}")

# ===== 方法3: 检查是否微信 4.x 改了加密方式 (不是 SQLCipher) =====
print("\n" + "=" * 60)
print("Method 3: Check if WeChat 4.x uses different encryption")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
db_path = os.path.join(DB_BASE, "contact", "contact.db")

with open(db_path, "rb") as f:
    page1 = f.read(4096)
    page2 = f.read(4096)

print(f"Page 1 last 48 bytes (reserve area):")
reserve1 = page1[-48:]
print(f"  IV  (16 bytes): {reserve1[:16].hex()}")
print(f"  HMAC(20 bytes): {reserve1[16:36].hex()}")
print(f"  Pad (12 bytes): {reserve1[36:].hex()}")

print(f"\nPage 2 last 48 bytes (reserve area):")
reserve2 = page2[-48:]
print(f"  IV  (16 bytes): {reserve2[:16].hex()}")
print(f"  HMAC(20 bytes): {reserve2[16:36].hex()}")
print(f"  Pad (12 bytes): {reserve2[36:].hex()}")

# 检查 padding 是否为零 (SQLCipher 特征)
if reserve1[36:] == b'\x00' * 12:
    print("\n  >> Page 1 padding is all zeros - standard SQLCipher format!")
else:
    print(f"\n  >> Page 1 padding is NOT zeros: {reserve1[36:].hex()}")
    print("  >> This might NOT be standard SQLCipher!")

# ===== 方法4: 查看 shared_andromeda.dll 是否有 sqlcipher 相关符号 =====
print("\n" + "=" * 60)
print("Method 4: Check Weixin DLLs for sqlcipher")
print("=" * 60)

dll_dir = r"C:\Program Files\Tencent\Weixin\4.1.8.28"
if os.path.exists(dll_dir):
    dlls = [f for f in os.listdir(dll_dir) if f.endswith(".dll")]
    print(f"DLLs in Weixin dir: {dlls}")

    # 搜索 sqlcipher 字符串
    for dll_name in ["shared_andromeda.dll"]:
        dll_path = os.path.join(dll_dir, dll_name)
        if os.path.exists(dll_path):
            size = os.path.getsize(dll_path)
            print(f"\n{dll_name}: {size / 1024 / 1024:.1f} MB")

            # 搜索关键字符串
            search_terms = [b"sqlcipher", b"SQLCipher", b"PRAGMA key", b"pragma key",
                          b"sqlite3_key", b"HMAC", b"pbkdf2", b"AES-256",
                          b"wcdb", b"WCDB", b"sqliterk", b"SQLiteRepairKit"]

            with open(dll_path, "rb") as f:
                content = f.read()

            for term in search_terms:
                positions = []
                start = 0
                while True:
                    pos = content.find(term, start)
                    if pos == -1:
                        break
                    positions.append(pos)
                    start = pos + 1
                    if len(positions) > 5:
                        break

                if positions:
                    print(f"  Found '{term.decode()}' at {len(positions)} position(s): {positions[:3]}")
                    # 显示周围上下文
                    for p in positions[:1]:
                        ctx_start = max(0, p - 20)
                        ctx_end = min(len(content), p + len(term) + 40)
                        ctx = content[ctx_start:ctx_end]
                        # 只显示可打印字符
                        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
                        print(f"    Context: ...{printable}...")

# ===== 方法5: 检查 pywxdump 的 get_wx_db_key_v4 或类似方法 =====
print("\n" + "=" * 60)
print("Method 5: Search pywxdump source for v4/new version support")
print("=" * 60)

import glob
for py_file in glob.glob(os.path.join(pywxdump_dir, "**", "*.py"), recursive=True):
    with open(py_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    for keyword in ["Weixin.exe", "xwechat", "andromeda", "4096", "key_info", "LoginKeyInfo"]:
        if keyword in content:
            # 找到关键行
            for i, line in enumerate(content.split("\n")):
                if keyword in line:
                    rel_path = os.path.relpath(py_file, pywxdump_dir)
                    print(f"  {rel_path}:{i+1}: {line.strip()[:120]}")
