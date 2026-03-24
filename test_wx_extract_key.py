"""
从内存中提取完整的 WCDB PRAGMA key 语句
"""
import os
import sys
import ctypes
import ctypes.wintypes as wintypes
import re
import hmac
import hashlib
import sqlite3
from Cryptodome.Cipher import AES

sys.stdout.reconfigure(encoding='utf-8')

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ReadProcessMemory = kernel32.ReadProcessMemory
void_p = ctypes.c_void_p
KEY_SIZE = 32
SQLITE_FILE_HEADER = b"SQLite format 3\x00"

from pywxdump.wx_core.utils.ctypes_utils import get_process_list
from pywxdump.wx_core.utils import search_memory

pid = [(p, n) for p, n in get_process_list() if n == "Weixin.exe"][0][0]
hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
print(f"PID: {pid}")

# ======= 搜索 PRAGMA key =======
print("=" * 60)
print("Search for PRAGMA key / raw key statements")
print("=" * 60)

# WCDB 用 PRAGMA key = 'xxx' 来设置密钥
# 但可能不是 PRAGMA key，而是 sqlite3_key() API 调用
# key 可能以 hex string 或 raw bytes 形式存在

# 搜索 "key =" 或 "KEY ="
for pattern in [b"PRAGMA key", b"pragma key", b"sqlite3_key", b"x'", b"ATTACH"]:
    addrs = search_memory(hProcess, pattern, max_num=30)
    if addrs:
        unique = set()
        for addr in addrs:
            buf = ctypes.create_string_buffer(300)
            if ReadProcessMemory(hProcess, void_p(addr), buf, 300, 0):
                data = bytes(buf)
                end = data.find(b'\x00')
                if end < 0:
                    end = 200
                stmt = data[:end].decode('utf-8', errors='replace')[:200]
                if stmt not in unique:
                    unique.add(stmt)

        print(f"\n'{pattern.decode()}' - {len(unique)} unique statements:")
        for s in sorted(unique):
            print(f"  {s}")

# ======= 搜索 cipher_salt 附近完整的配置块 =======
print("\n" + "=" * 60)
print("Full PRAGMA block near cipher_salt")
print("=" * 60)

salt_addrs = search_memory(hProcess, b"PRAGMA cipher_salt = 'x", max_num=10)
print(f"Found {len(salt_addrs)} PRAGMA cipher_salt with value")

for addr in salt_addrs:
    # 读取这个地址前后 2KB 范围
    buf = ctypes.create_string_buffer(4000)
    read_start = max(0, addr - 2000)
    if ReadProcessMemory(hProcess, void_p(read_start), buf, 4000, 0):
        data = bytes(buf)
        text = data.decode('utf-8', errors='replace')

        # 找到所有 PRAGMA 语句
        pragmas = re.findall(r"PRAGMA\s+[\w_]+(?:\s*=\s*[^;\"]*)?", text)
        if pragmas:
            print(f"\n  All PRAGMAs near 0x{addr:x}:")
            for p in pragmas:
                p = p.strip()
                if p:
                    print(f"    {p}")

        # 找到所有看起来像 hex key 的字符串
        hex_keys = re.findall(r"x'([0-9a-fA-F]{32,})'", text)
        if hex_keys:
            print(f"\n  Hex values found:")
            for h in hex_keys:
                print(f"    x'{h}' ({len(h)} chars = {len(h)//2} bytes)")

# ======= 搜索 "1200" 附近的 key 模式 =======
print("\n" + "=" * 60)
print("Search D291E19C and surrounding key data")
print("=" * 60)

for pattern in [b"D291E19C", b"d291e19c"]:
    addrs = search_memory(hProcess, pattern, max_num=20)
    if addrs:
        print(f"\nFound '{pattern.decode()}' at {len(addrs)} locations")
        for addr in addrs[:5]:
            # 读取前后 300 字节
            buf = ctypes.create_string_buffer(600)
            read_start = max(0, addr - 200)
            if ReadProcessMemory(hProcess, void_p(read_start), buf, 600, 0):
                data = bytes(buf)

                # hex dump
                print(f"\n  Hex dump around 0x{addr:x}:")
                for i in range(180, min(400, len(data)), 16):
                    line = data[i:i+16]
                    hex_str = " ".join(f"{b:02x}" for b in line)
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                    abs_addr = read_start + i
                    print(f"    0x{abs_addr:x}: {hex_str:<48s}  {ascii_str}")

                # 提取连续 hex 字符
                text = data.decode('utf-8', errors='replace')
                hex_matches = re.findall(r'[0-9A-Fa-f]{40,}', text)
                if hex_matches:
                    print(f"\n  Hex strings found:")
                    for h in hex_matches:
                        print(f"    {h} ({len(h)} chars)")

# ======= 读取之前的 "bizchat.db" 结构体更大范围 =======
print("\n" + "=" * 60)
print("Read bizchat.db structure with larger range")
print("=" * 60)

bizchat_addrs = search_memory(hProcess, b"bizchat\\bizchat.db\x00", max_num=5)
if bizchat_addrs:
    for addr in bizchat_addrs[:1]:
        buf = ctypes.create_string_buffer(800)
        if ReadProcessMemory(hProcess, void_p(addr), buf, 800, 0):
            data = bytes(buf)

            for i in range(0, min(500, len(data)), 16):
                line = data[i:i+16]
                hex_str = " ".join(f"{b:02x}" for b in line)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                abs_addr = addr + i
                print(f"  0x{abs_addr:x}: {hex_str:<48s}  {ascii_str}")

kernel32.CloseHandle(hProcess)
