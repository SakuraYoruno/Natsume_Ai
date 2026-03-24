"""
提取微信 4.x 的完整 WCDB PRAGMA 配置和 key
"""
import os
import sys
import ctypes
import ctypes.wintypes as wintypes

sys.stdout.reconfigure(encoding='utf-8')

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ReadProcessMemory = kernel32.ReadProcessMemory
void_p = ctypes.c_void_p

from pywxdump.wx_core.utils.ctypes_utils import get_process_list
from pywxdump.wx_core.utils import search_memory

pid = [(p, n) for p, n in get_process_list() if n == "Weixin.exe"][0][0]
hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
print(f"PID: {pid}")

# ======= 找到所有 PRAGMA cipher 语句 =======
print("=" * 60)
print("Search ALL PRAGMA cipher statements in memory")
print("=" * 60)

for pattern in [b"PRAGMA cipher", b"PRAGMA key", b"PRAGMA kdf"]:
    addrs = search_memory(hProcess, pattern, max_num=50)
    print(f"\n'{pattern.decode()}' found {len(addrs)} times")

    for addr in addrs[:20]:
        buf = ctypes.create_string_buffer(500)
        if ReadProcessMemory(hProcess, void_p(addr), buf, 500, 0):
            data = bytes(buf)
            # 找到完整的语句（以 \x00 或 ; 结束）
            end = data.find(b'\x00')
            if end > 0:
                stmt = data[:end].decode('utf-8', errors='replace')
                print(f"  0x{addr:x}: {stmt[:200]}")

# ======= 详细读取 cipher_salt PRAGMA 附近 =======
print("\n" + "=" * 60)
print("Detailed read around PRAGMA cipher_salt")
print("=" * 60)

salt_addrs = search_memory(hProcess, b"PRAGMA cipher_salt", max_num=20)
for addr in salt_addrs[:10]:
    buf = ctypes.create_string_buffer(2000)
    read_addr = max(0, addr - 200)
    if ReadProcessMemory(hProcess, void_p(read_addr), buf, 2000, 0):
        data = bytes(buf)

        # 找到所有以 PRAGMA 开头的语句
        import re
        text = data.decode('utf-8', errors='replace')
        pragmas = re.findall(r'PRAGMA\s+\w+[^;]*', text)
        for p in pragmas:
            print(f"  {p[:200]}")

# ======= 搜索 "nt_key" 附近的完整结构 =======
print("\n" + "=" * 60)
print("Search 'nt_key' (probably 'client_key' or 'encrypt_key')")
print("=" * 60)

for pattern in [b"nt_key", b"encrypt_key", b"client_key", b"db_key", b"cipher_key"]:
    addrs = search_memory(hProcess, pattern, max_num=20)
    if addrs:
        print(f"\n'{pattern.decode()}' found {len(addrs)} times")
        for addr in addrs[:5]:
            buf = ctypes.create_string_buffer(300)
            read_addr = max(0, addr - 100)
            if ReadProcessMemory(hProcess, void_p(read_addr), buf, 300, 0):
                data = bytes(buf)
                for i in range(0, min(300, len(data)), 16):
                    line = data[i:i+16]
                    hex_str = " ".join(f"{b:02x}" for b in line)
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                    abs_addr = read_addr + i
                    print(f"    0x{abs_addr:x}: {hex_str:<48s}  {ascii_str}")

# ======= 搜索完整的 hex key 字符串 =======
print("\n" + "=" * 60)
print("Search for 64-char hex key strings (32 bytes)")
print("=" * 60)

# 从之前发现的 "D291E19C" 搜索完整的 key
for prefix in [b"D291E19C", b"d291e19c"]:
    addrs = search_memory(hProcess, prefix, max_num=20)
    if addrs:
        print(f"\nFound '{prefix.decode()}' at {len(addrs)} locations")
        for addr in addrs[:5]:
            buf = ctypes.create_string_buffer(200)
            if ReadProcessMemory(hProcess, void_p(addr), buf, 200, 0):
                data = bytes(buf)
                end = data.find(b'\x00')
                if end < 0:
                    end = 200
                hex_str = data[:end].decode('utf-8', errors='replace')
                print(f"  0x{addr:x}: {hex_str[:100]}")

# ======= 搜索 bizchat 数据库结构附近的完整数据 =======
print("\n" + "=" * 60)
print("Search for database config structures")
print("=" * 60)

# 从之前发现的模式: path + \x00 + metadata + "1200" + hex_key + ":503:1" + "nt_key"
# 搜索所有数据库名附近的这种模式

db_names = [b"bizchat.db", b"contact.db", b"session.db", b"message_0.db", b"general.db"]
for db_name in db_names:
    addrs = search_memory(hProcess, db_name, max_num=5)
    if addrs:
        for addr in addrs[:1]:
            buf = ctypes.create_string_buffer(500)
            if ReadProcessMemory(hProcess, void_p(addr), buf, 500, 0):
                data = bytes(buf)

                print(f"\n{db_name.decode()} at 0x{addr:x}:")

                # hex dump 前 300 字节
                for i in range(0, min(300, len(data)), 16):
                    line = data[i:i+16]
                    hex_str = " ".join(f"{b:02x}" for b in line)
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                    print(f"  +{i:3d}: {hex_str:<48s}  {ascii_str}")

kernel32.CloseHandle(hProcess)
