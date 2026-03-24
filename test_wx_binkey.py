"""
微信 4.x - 直接搜索原始二进制 key 模式
发现: 内存中存在 protobuf 编码的配置结构:
  ... [hex_key_part1] 0xC1 0x40 [1byte] "1200" [1byte] [hex_key_part2] [1byte] ":503:1" ...
ef bf bd 实际上是单个字节被 protobuf varint 或自定义编码包裹的

让我直接搜索 0xC140 + byte + "1200" 的模式
"""
import os
import sys
import ctypes
import ctypes.wintypes as wintypes
import hmac
import hashlib
import sqlite3
import struct
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

pid = [(p, n) for p, n in get_process_list() if n == "Weixin.exe"][0][0]
hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
print(f"PID: {pid}")

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"

# ======= Step 1: 搜索 binary pattern =======
print("=" * 60)
print("Step 1: Search for binary key pattern")
print("=" * 60)

# 从之前的 hex dump:
# c1 40 ef bf bd 31 32 30 30 ef bf bd 44 32 39 31 ...
# 这里 ef bf bd 是 UTF-8 的 replacement char, 说明原始字节是 > 0x7F 的单字节
# protobuf 编码: c1 40 是 field tag, 后面可能是 length-delimited 或 varint

# 但关键是："1200" 是 ASCII 字符串形式的数字
# "D291E19CED0FE09D2F072D96B67848F68A98" 也是 ASCII hex 字符串

# 让我搜索 "1200" 然后检查前面是否有 c1 40
pattern = b"\xc1\x40"
addrs = search_memory(hProcess, pattern, max_num=200)
print(f"Found 0xC140 pattern at {len(addrs)} locations")

found_structures = []
for addr in addrs:
    buf = ctypes.create_string_buffer(300)
    if ReadProcessMemory(hProcess, void_p(addr), buf, 300, 0):
        data = bytes(buf)

        # 检查格式: c1 40 [1-3 bytes] "1200" [1-3 bytes] [hex ascii key] [1-3 bytes] ":503:" or ":524:"
        # 在 UTF-8 中 ef bf bd 取代了单个高字节
        # 但在内存中，这些应该是原始的单字节

        # 寻找 "1200" 在后面的位置
        pos_1200 = data.find(b"1200", 0, 20)
        if pos_1200 >= 0:
            # 找到了！读取更大范围
            buf2 = ctypes.create_string_buffer(500)
            read_start = max(0, addr - 100)
            if ReadProcessMemory(hProcess, void_p(read_start), buf2, 500, 0):
                full_data = bytes(buf2)
                c140_pos = full_data.find(b"\xc1\x40")

                if c140_pos >= 0:
                    # 从 c1 40 开始解析
                    struct_data = full_data[c140_pos:]

                    print(f"\n  Structure at 0x{addr:x}:")
                    # hex dump
                    for i in range(0, min(120, len(struct_data)), 16):
                        line = struct_data[i:i+16]
                        hex_str = " ".join(f"{b:02x}" for b in line)
                        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
                        print(f"    +{i:3d}: {hex_str:<48s}  {ascii_str}")

                    # 提取 key: 在 "1200" 后面，到 ":503:" 或 ":524:" 之前
                    pos_1200_in_struct = struct_data.find(b"1200")
                    end_markers = [struct_data.find(b":503:", pos_1200_in_struct),
                                   struct_data.find(b":524:", pos_1200_in_struct)]
                    end_markers = [e for e in end_markers if e >= 0]

                    if pos_1200_in_struct >= 0 and end_markers:
                        end_pos = min(end_markers)
                        # 从 "1200" + 4 到 end_pos
                        key_area_start = pos_1200_in_struct + 4
                        key_area = struct_data[key_area_start:end_pos]

                        print(f"    Key area raw ({len(key_area)} bytes): {key_area.hex()}")

                        # 过滤掉非 hex-ascii 字节
                        hex_chars = bytes([b for b in key_area if (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102)])
                        hex_str = hex_chars.decode('ascii')
                        print(f"    Key hex string: {hex_str} ({len(hex_str)} chars)")

                        # 也获取 "1200" 前面的 hex key 部分
                        before_start = max(0, c140_pos - 100)
                        before_data = full_data[before_start:c140_pos]

                        # 找到前面的连续 hex ascii 字符
                        hex_before = bytearray()
                        for b in reversed(before_data):
                            if (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102):
                                hex_before.insert(0, b)
                            else:
                                if len(hex_before) >= 8:
                                    break
                                hex_before.clear()

                        if hex_before:
                            hex_before_str = hex_before.decode('ascii')
                            print(f"    Hex before c140: {hex_before_str} ({len(hex_before_str)} chars)")

                            # 合并
                            full_key_hex = hex_before_str + hex_str
                            print(f"    Full key: {full_key_hex} ({len(full_key_hex)} chars)")

                            if len(full_key_hex) >= 64:
                                found_structures.append({
                                    'addr': addr,
                                    'key_hex': full_key_hex,
                                    'kdf_iter': 1200,
                                })

# ======= Step 2: 尝试解密 =======
print("\n" + "=" * 60)
print(f"Step 2: Try decryption with {len(found_structures)} found keys")
print("=" * 60)

contact_db = os.path.join(DB_BASE, "contact", "contact.db")
session_db = os.path.join(DB_BASE, "session", "session.db")
message_db = os.path.join(DB_BASE, "message", "message_0.db")

with open(contact_db, "rb") as f:
    contact_page1 = f.read(4096)

def try_decrypt(key_hex, page1_data, kdf_iter, hmac_algo="sha1"):
    """尝试解密验证"""
    if len(key_hex) < 64:
        return False
    try:
        key_bytes = bytes.fromhex(key_hex[:64])
    except:
        return False

    salt = page1_data[:16]
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])

    byteKey = hashlib.pbkdf2_hmac(hmac_algo, key_bytes, salt, kdf_iter, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac(hmac_algo, byteKey, mac_salt, 2, KEY_SIZE)

    hash_mac = hmac.new(mac_key, page1_data[16:4064], getattr(hashlib, hmac_algo))
    hash_mac.update(b'\x01\x00\x00\x00')

    return hash_mac.digest() == page1_data[4064:4084]

seen_keys = set()
for struct_info in found_structures:
    key_hex = struct_info['key_hex']

    # 尝试不同的截取方式（前64、后64、中间64等）
    variants = set()
    if len(key_hex) >= 64:
        variants.add(key_hex[:64])
        variants.add(key_hex[-64:])
        # 中间各种偏移
        for start in range(0, len(key_hex) - 63, 2):
            variants.add(key_hex[start:start+64])

    for v in variants:
        if v in seen_keys:
            continue
        seen_keys.add(v)

        for kdf in [1200, 4000, 64000, 256000, 1, 2]:
            for algo in ["sha1", "sha256", "sha512"]:
                if try_decrypt(v, contact_page1, kdf, algo):
                    print(f"  MATCH! key={v}, kdf={kdf}, hmac={algo}")

                    # 解密并验证
                    key_bytes = bytes.fromhex(v)
                    salt = contact_page1[:16]
                    byteHmac = hashlib.pbkdf2_hmac(algo, key_bytes, salt, kdf, KEY_SIZE)

                    out_dir = r"C:\Users\v_wenloliu\Documents\xwechat_files\decrypted"
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, "de_contact.db")

                    with open(contact_db, "rb") as f:
                        blist = f.read()

                    with open(out_path, "wb") as deFile:
                        deFile.write(SQLITE_FILE_HEADER)
                        for i in range(0, len(blist), 4096):
                            tblist = blist[i:i + 4096] if i > 0 else blist[16:i + 4096]
                            deFile.write(AES.new(byteHmac, AES.MODE_CBC, tblist[-48:-32]).decrypt(tblist[:-48]))
                            deFile.write(tblist[-48:])

                    conn = sqlite3.connect(out_path)
                    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    print(f"  Decrypted tables: {[t[0] for t in tables]}")
                    conn.close()

                    kernel32.CloseHandle(hProcess)
                    sys.exit(0)

print(f"\nTested {len(seen_keys)} unique key variants - none matched")
print("\nNote: The key might not be stored as ASCII hex in memory")
print("WeChat 4.x might use a completely different key derivation mechanism")

kernel32.CloseHandle(hProcess)
