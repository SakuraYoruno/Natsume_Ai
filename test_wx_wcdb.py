"""
微信 4.x WCDB 数据库解密 - 从内存中的 db_path 附近提取 key
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

from pywxdump.wx_core.utils.ctypes_utils import get_process_list, get_memory_maps
from pywxdump.wx_core.utils import search_memory

# 获取 PID
weixin_pids = [(pid, name) for pid, name in get_process_list() if name == "Weixin.exe"]
pid = weixin_pids[0][0]
print(f"PID: {pid}")

hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)

# ======= Step 1: 读取 db_path 附近的完整数据结构 =======
print("=" * 60)
print("Step 1: Read data near db_path in memory")
print("=" * 60)

db_pattern = b"db_storage"
db_addrs = search_memory(hProcess, db_pattern, max_num=50)
print(f"Found {len(db_addrs)} db_storage references")

# 读取更大范围的上下文
unique_contexts = set()
for addr in db_addrs[:10]:
    # 读取 addr 前后各 256 字节
    buf_size = 512
    buf = ctypes.create_string_buffer(buf_size)
    read_addr = max(0, addr - 256)
    if ReadProcessMemory(hProcess, void_p(read_addr), buf, buf_size, 0):
        data = bytes(buf)

        # 查找 hex 字符串模式 (可能是 key)
        import re
        # 查找连续的 hex 字符 (至少 32 位)
        text_part = "".join(chr(b) if 32 <= b < 127 else "\x00" for b in data)
        hex_matches = re.findall(r'[0-9A-Fa-f]{32,}', text_part)

        if hex_matches:
            for match in hex_matches:
                if match not in unique_contexts:
                    unique_contexts.add(match)
                    print(f"\n  At 0x{read_addr:x}: Found hex string ({len(match)} chars): {match[:80]}...")

# ======= Step 2: 搜索特定 db 路径附近的数据，寻找 key 结构 =======
print("\n" + "=" * 60)
print("Step 2: Deep search near specific db paths")
print("=" * 60)

# 搜索 contact.db 路径
contact_pattern = b"contact.db"
contact_addrs = search_memory(hProcess, contact_pattern, max_num=20)
print(f"contact.db occurrences: {len(contact_addrs)}")

for addr in contact_addrs[:5]:
    # 读取 -500 到 +200 范围
    buf_size = 700
    buf = ctypes.create_string_buffer(buf_size)
    read_addr = max(0, addr - 500)
    if ReadProcessMemory(hProcess, void_p(read_addr), buf, buf_size, 0):
        data = bytes(buf)

        # 显示数据库路径前面的字节
        # 找到 contact.db 在 data 中的位置
        pos = data.find(b"contact.db")
        if pos >= 0:
            # 路径前面 200 字节
            before = data[max(0, pos-200):pos]
            print(f"\n  At 0x{addr:x}:")
            print(f"  200 bytes before 'contact.db':")

            # hex dump
            for i in range(0, min(200, len(before)), 16):
                line = before[i:i+16]
                hex_str = " ".join(f"{b:02x}" for b in line)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                offset_label = pos - len(before) + i
                print(f"    {offset_label:+5d}: {hex_str:<48s}  {ascii_str}")

# ======= Step 3: 搜索 "1200" 模式 (之前看到的) 及周围的 key 数据 =======
print("\n" + "=" * 60)
print("Step 3: Search for WCDB cipher configuration in memory")
print("=" * 60)

# 搜索 "PRAGMA cipher" 或 "cipher_compatibility"
for pattern in [b"cipher_compatibility", b"PRAGMA cipher", b"cipher_default_kdf"]:
    addrs = search_memory(hProcess, pattern, max_num=5)
    if addrs:
        print(f"\n  Found '{pattern.decode()}' at {len(addrs)} locations")
        for a in addrs[:2]:
            buf = ctypes.create_string_buffer(200)
            if ReadProcessMemory(hProcess, void_p(a), buf, 200, 0):
                data = bytes(buf)
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:150])
                print(f"    0x{a:x}: {printable}")

# ======= Step 4: 搜索 key 的直接形式 =======
print("\n" + "=" * 60)
print("Step 4: Search for raw key bytes near wxid")
print("=" * 60)

# wxid 附近可能有数据库 key
wxid_addrs = search_memory(hProcess, b"wxid_6ofkq4h94pxe22", max_num=50)
print(f"wxid occurrences: {len(wxid_addrs)}")

# 找到 db_storage 路径结构体，读取其中可能的 key 字段
# 从之前的输出看到: path + \x00 + "S.L" + ... + "1200" + ... + "D291E19C..."
# 这个 "1200" 可能是配置参数, "D291E19C..." 可能是 key

# 重新仔细读取第一个 db_storage 引用附近的数据
if db_addrs:
    addr = db_addrs[0]  # 第一个引用
    buf_size = 1024
    buf = ctypes.create_string_buffer(buf_size)
    read_addr = max(0, addr - 200)

    if ReadProcessMemory(hProcess, void_p(read_addr), buf, buf_size, 0):
        data = bytes(buf)
        pos = data.find(b"db_storage")

        print(f"\nDetailed hex dump around first db_storage reference:")
        start = max(0, pos - 50)
        end = min(len(data), pos + 300)

        for i in range(start, end, 16):
            line = data[i:i+16]
            hex_str = " ".join(f"{b:02x}" for b in line)
            ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
            abs_addr = read_addr + i
            print(f"    0x{abs_addr:x}: {hex_str:<48s}  {ascii_str}")

# ======= Step 5: 尝试 WCDB 不同参数的 verify =======
print("\n" + "=" * 60)
print("Step 5: Try WCDB parameters for verify_key")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
contact_db = os.path.join(DB_BASE, "contact", "contact.db")

with open(contact_db, "rb") as f:
    page1 = f.read(4096)

salt = page1[:16]

def verify_wcdb(key_bytes, data, kdf_iter, hmac_algo, reserve_size=48):
    """尝试不同 WCDB 参数验证"""
    try:
        salt = data[:16]
        mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])

        if hmac_algo == "sha1":
            hash_algo = "sha1"
            hmac_size = 20
        elif hmac_algo == "sha256":
            hash_algo = "sha256"
            hmac_size = 32
        elif hmac_algo == "sha512":
            hash_algo = "sha512"
            hmac_size = 64
        else:
            return False

        byteKey = hashlib.pbkdf2_hmac(hash_algo, key_bytes, salt, kdf_iter, KEY_SIZE)
        mac_key = hashlib.pbkdf2_hmac(hash_algo, byteKey, mac_salt, 2, KEY_SIZE)

        # 计算 HMAC
        data_end = 4096 - reserve_size
        hmac_start = data_end
        hmac_data = data[16:data_end]

        hash_mac = hmac.new(mac_key, hmac_data, getattr(hashlib, hash_algo))
        hash_mac.update(b'\x01\x00\x00\x00')

        expected = data[hmac_start + 16:hmac_start + 16 + hmac_size]  # After IV

        return hash_mac.digest() == expected
    except:
        return False

# 从 key_info.db 获取所有 protobuf 中的候选 key
key_db_path = r"C:\Users\v_wenloliu\Documents\xwechat_files\all_users\login\wxid_6ofkq4h94pxe22\key_info.db"
conn = sqlite3.connect(f"file:{key_db_path}?mode=ro", uri=True)
cursor = conn.cursor()
cursor.execute("SELECT key_info_data FROM LoginKeyInfoTable")
all_key_data = cursor.fetchall()
conn.close()

# 提取 protobuf 中的各段数据
candidates = set()
for (raw,) in all_key_data:
    blob = raw[3:3+168]  # field 1 payload
    # 尝试不同的 32 字节窗口
    for offset in range(0, len(blob) - 32, 4):
        candidate = blob[offset:offset+32]
        candidates.add(candidate)

print(f"Candidates from key_info.db: {len(candidates)}")

# 尝试不同的 WCDB 参数组合
tested = 0
for kdf_iter in [4000, 64000, 256000]:
    for hmac_algo in ["sha1", "sha256", "sha512"]:
        for reserve in [48, 32, 64]:
            for candidate in list(candidates)[:5]:  # 先测试几个
                tested += 1
                if verify_wcdb(candidate, page1, kdf_iter, hmac_algo, reserve):
                    print(f"  FOUND! kdf_iter={kdf_iter}, hmac={hmac_algo}, reserve={reserve}")
                    print(f"  Key: {candidate.hex()}")
                    break

print(f"Tested {tested} combinations, no match from key_info.db protobuf data")

# 也尝试 hex 字符串作为 key
hex_key_candidates = list(unique_contexts) if unique_contexts else []
print(f"\nHex string candidates: {len(hex_key_candidates)}")
for hex_str in hex_key_candidates:
    # 取前 64 字符 (32 bytes)
    if len(hex_str) >= 64:
        try:
            key_bytes = bytes.fromhex(hex_str[:64])
            for kdf_iter in [4000, 64000, 256000]:
                for hmac_algo in ["sha1", "sha256"]:
                    if verify_wcdb(key_bytes, page1, kdf_iter, hmac_algo, 48):
                        print(f"  FOUND with hex key! kdf={kdf_iter} hmac={hmac_algo}")
                        print(f"  Key: {hex_str[:64]}")
        except:
            pass

kernel32.CloseHandle(hProcess)
print("\nDone.")
