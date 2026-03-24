"""
提取微信 4.x 真实的 raw key
关键发现: 内存中有 "1200" + raw_key + ":503:1" 的模式
1200 是 kdf_iter, raw_key 是实际密钥
"""
import os
import sys
import ctypes
import ctypes.wintypes as wintypes
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

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"

# ======= Step 1: 找到 "1200" 模式并提取 raw bytes =======
print("=" * 60)
print("Step 1: Find '1200' pattern and extract raw key")
print("=" * 60)

# 搜索 b"1200" 后跟非 ASCII 的模式（1200 是 kdf_iter）
addrs_1200 = search_memory(hProcess, b"1200", max_num=100)
print(f"Found '1200' at {len(addrs_1200)} locations")

key_candidates = []

for addr in addrs_1200:
    # 读取 "1200" 前后 200 字节
    buf = ctypes.create_string_buffer(400)
    read_start = max(0, addr - 100)
    if ReadProcessMemory(hProcess, void_p(read_start), buf, 400, 0):
        data = bytes(buf)

        # 找到 "1200" 的位置
        pos = data.find(b"1200")
        if pos < 0:
            continue

        # 检查 "1200" 后面是否跟着看起来像 key 的数据
        # 从之前的分析：c1 40 ef bf bd 31 32 30 30 ef bf bd [key bytes]
        # 但实际二进制中 ef bf bd 是 UTF-8 编码问题
        # 让我检查 "1200" 后面紧跟的字节

        after_1200 = data[pos+4:pos+100]

        # 查看是否 ":503:1" 在后面 (这标记了 key 的结束)
        end_marker_pos = after_1200.find(b":503:")
        if end_marker_pos < 0:
            end_marker_pos = after_1200.find(b":524:")  # 另一个变体

        if end_marker_pos > 0:
            # 找到了！1200 和 :503: 之间的数据可能是 key
            raw_key_area = after_1200[:end_marker_pos]

            print(f"\n  Found pattern at 0x{addr:x}")
            print(f"  '1200' at offset {pos}, end marker at +{end_marker_pos}")
            print(f"  Raw bytes between markers ({len(raw_key_area)} bytes): {raw_key_area.hex()}")
            print(f"  ASCII: {''.join(chr(b) if 32 <= b < 127 else '.' for b in raw_key_area)}")

            # 也看看 "1200" 之前的数据
            before_area = data[max(0, pos-50):pos]
            print(f"  50 bytes before '1200': {before_area.hex()}")
            print(f"  ASCII: {''.join(chr(b) if 32 <= b < 127 else '.' for b in before_area)}")

            # 提取可能的 hex 字符串形式的 key
            # "D291E19CED0FE09D2F072D96B67848F68A98" 是 ASCII hex
            import re
            hex_match = re.findall(rb'[0-9A-Fa-f]+', raw_key_area)
            full_hex = b''.join(hex_match).decode('ascii')
            print(f"  Extracted hex chars: {full_hex} ({len(full_hex)} chars)")

            if len(full_hex) >= 32:
                key_candidates.append(full_hex)

            # 也收集 "1200" 前面的 hex 数据
            hex_before = re.findall(rb'[0-9A-Fa-f]+', before_area)
            if hex_before:
                full_before = b''.join(hex_before).decode('ascii')
                print(f"  Hex before '1200': {full_before} ({len(full_before)} chars)")

                # 合并前后的 hex
                combined = full_before + full_hex
                print(f"  Combined hex: {combined} ({len(combined)} chars)")
                if len(combined) >= 64:
                    key_candidates.append(combined[-64:])  # 取最后 64 个 hex chars
                    key_candidates.append(combined[:64])    # 取前 64 个 hex chars

# ======= Step 2: 用提取的 key 尝试不同 WCDB 参数解密 =======
print("\n" + "=" * 60)
print("Step 2: Try decryption with extracted keys")
print("=" * 60)

contact_db = os.path.join(DB_BASE, "contact", "contact.db")
session_db = os.path.join(DB_BASE, "session", "session.db")

def try_wcdb_decrypt(key_hex, db_path, kdf_iter, hmac_algo="sha1"):
    """尝试用 WCDB 参数解密"""
    try:
        key_bytes = bytes.fromhex(key_hex[:64])  # 取前 32 bytes
    except:
        return False

    with open(db_path, "rb") as f:
        blist = f.read(4096)

    if len(blist) < 4096:
        return False

    salt = blist[:16]
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])

    hash_func = "sha1" if hmac_algo == "sha1" else hmac_algo

    byteKey = hashlib.pbkdf2_hmac(hash_func, key_bytes, salt, kdf_iter, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac(hash_func, byteKey, mac_salt, 2, KEY_SIZE)

    hash_mac = hmac.new(mac_key, blist[16:4064], getattr(hashlib, hash_func))
    hash_mac.update(b'\x01\x00\x00\x00')

    return hash_mac.digest() == blist[4064:4084]

print(f"Key candidates: {len(key_candidates)}")

# 去重
unique_keys = list(set(key_candidates))
print(f"Unique keys: {len(unique_keys)}")

for key_hex in unique_keys:
    print(f"\n  Testing key: {key_hex[:64]}...")

    for kdf_iter in [1200, 4000, 64000, 256000, 1]:
        for hmac_algo in ["sha1", "sha256"]:
            for db in [contact_db, session_db]:
                db_name = os.path.basename(db)
                if try_wcdb_decrypt(key_hex, db, kdf_iter, hmac_algo):
                    print(f"  >>> MATCH! kdf={kdf_iter}, hmac={hmac_algo}, db={db_name}")
                    print(f"  >>> Key: {key_hex[:64]}")

# ======= Step 3: 也尝试直接用 raw bytes (非 hex string) 作为 key =======
print("\n" + "=" * 60)
print("Step 3: Try raw bytes as key")
print("=" * 60)

# 重新读取 "1200" 后的原始字节
for addr in addrs_1200:
    buf = ctypes.create_string_buffer(200)
    if ReadProcessMemory(hProcess, void_p(addr), buf, 200, 0):
        data = bytes(buf)
        if data[:4] == b"1200":
            after = data[4:]
            # 尝试后续的每个 32 字节窗口
            for offset in [0, 1, 2, 3, 4]:
                raw_key = after[offset:offset+32]
                if len(raw_key) < 32:
                    continue
                if raw_key == b'\x00' * 32:
                    continue

                for kdf_iter in [1200, 4000, 64000, 1]:
                    for hmac_algo in ["sha1", "sha256"]:
                        if try_wcdb_decrypt(raw_key.hex(), contact_db, kdf_iter, hmac_algo):
                            print(f"  >>> MATCH with raw bytes! offset={offset}, kdf={kdf_iter}, hmac={hmac_algo}")
                            print(f"  >>> Key: {raw_key.hex()}")

# ======= Step 4: 读取 PRAGMA cipher_salt 附近的完整 key =======
print("\n" + "=" * 60)
print("Step 4: Search for raw key via PRAGMA cipher_salt context")
print("=" * 60)

# 找到 session.db 的 salt 在内存中
session_salt = b"91538d165c454e492add2e25e4b0e7e5"  # 从之前的 PRAGMA cipher_salt
salt_addrs = search_memory(hProcess, session_salt, max_num=10)
print(f"session.db salt found at {len(salt_addrs)} locations")

for addr in salt_addrs[:3]:
    # 在 salt 附近应该有 key
    buf = ctypes.create_string_buffer(2000)
    read_start = max(0, addr - 500)
    if ReadProcessMemory(hProcess, void_p(read_start), buf, 2000, 0):
        data = bytes(buf)

        # 查找 "key" 或 "KEY" 字符串
        for needle in [b"key", b"KEY", b"Key"]:
            pos = 0
            while True:
                pos = data.find(needle, pos)
                if pos < 0:
                    break
                # 显示周围上下文
                ctx_start = max(0, pos - 50)
                ctx_end = min(len(data), pos + 100)
                ctx = data[ctx_start:ctx_end]
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
                abs_addr = read_start + pos
                print(f"  '{needle.decode()}' at 0x{abs_addr:x}: ...{printable[:150]}...")
                pos += 1

kernel32.CloseHandle(hProcess)
print("\nDone.")
