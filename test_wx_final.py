"""
微信 4.x - Weixin.dll SQLCipher 分析 + 内存搜索 key
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

SQLITE_FILE_HEADER = b"SQLite format 3\x00"
KEY_SIZE = 32

# ======= Step 1: 分析 Weixin.dll 中的 SQLCipher 参数 =======
print("=" * 60)
print("Step 1: Analyze Weixin.dll for SQLCipher parameters")
print("=" * 60)

dll_path = r"C:\Program Files\Tencent\Weixin\4.1.8.28\Weixin.dll"
dll_size = os.path.getsize(dll_path)
print(f"Weixin.dll size: {dll_size / 1024 / 1024:.1f} MB")

# 由于文件巨大，分块搜索
CHUNK_SIZE = 50 * 1024 * 1024  # 50MB 块

search_terms = [
    b"sqlcipher", b"SQLCipher", b"WCDB", b"wcdb",
    b"sqlite3_key", b"cipher_page_size", b"kdf_iter",
    b"cipher_default_kdf_iter", b"cipher_hmac_algorithm",
    b"cipher_use_hmac", b"cipher_plaintext_header_size",
    b"PRAGMA key", b"PRAGMA cipher",
    b"cipher_kdf_algorithm",
]

with open(dll_path, "rb") as f:
    offset = 0
    while True:
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break

        for term in search_terms:
            start = 0
            while True:
                pos = chunk.find(term, start)
                if pos == -1:
                    break
                abs_pos = offset + pos
                # 取上下文
                ctx_start = max(0, pos - 30)
                ctx_end = min(len(chunk), pos + len(term) + 50)
                ctx = chunk[ctx_start:ctx_end]
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
                print(f"  '{term.decode()}' at offset {abs_pos}: ...{printable}...")
                start = pos + 1
                if start - (pos - len(term)) > 3:
                    break  # 最多打印 3 个匹配

        offset += len(chunk)

# ======= Step 2: 在 Weixin.dll 内存空间搜索 key =======
print("\n" + "=" * 60)
print("Step 2: Search key in Weixin.dll memory space")
print("=" * 60)

from pywxdump.wx_core.utils.ctypes_utils import get_process_list, get_memory_maps
from pywxdump.wx_core.utils import search_memory

# 获取 PID
weixin_pids = [(pid, name) for pid, name in get_process_list() if name == "Weixin.exe"]
pid = weixin_pids[0][0]
print(f"Weixin.exe PID: {pid}")

# 找到 Weixin.dll 的内存范围
memory_maps = get_memory_maps(pid)
weixin_dll_start = 0x7FFFFFFFFFFFFFFF
weixin_dll_end = 0

for module in memory_maps:
    if module.FileName and 'Weixin.dll' in module.FileName:
        s = module.BaseAddress
        e = module.BaseAddress + module.RegionSize
        weixin_dll_start = min(s, weixin_dll_start)
        weixin_dll_end = max(e, weixin_dll_end)

if weixin_dll_start < weixin_dll_end:
    print(f"Weixin.dll range: 0x{weixin_dll_start:x} - 0x{weixin_dll_end:x}")
    print(f"Size: {(weixin_dll_end - weixin_dll_start) / 1024 / 1024:.1f} MB")
else:
    print("Weixin.dll NOT found in memory maps!")
    # 打印所有唯一 DLL
    seen = set()
    for m in memory_maps:
        if m.FileName and m.FileName not in seen:
            seen.add(m.FileName)
            if 'weixin' in m.FileName.lower() or 'Weixin' in m.FileName:
                print(f"  {m.FileName}: base=0x{m.BaseAddress:x} size={m.RegionSize}")

# 打开进程
hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)

# 搜索 "iphone"/"android"/"ipad" 字符串
# 先在 Weixin.dll 范围内搜索
print("\nSearching phone type strings in Weixin.dll...")
for search_str in [b"iphone\x00", b"android\x00", b"ipad\x00"]:
    if weixin_dll_start < weixin_dll_end:
        addrs = search_memory(hProcess, search_str, max_num=10,
                             start_address=weixin_dll_start, end_address=weixin_dll_end)
        if addrs:
            print(f"  Found '{search_str[:-1].decode()}' in Weixin.dll at: {['0x{:x}'.format(a) for a in addrs]}")

# 全进程搜索
print("\nSearching phone type strings in entire process...")
for search_str in [b"iphone\x00", b"android\x00", b"ipad\x00"]:
    addrs = search_memory(hProcess, search_str, max_num=10)
    if addrs:
        print(f"  Found '{search_str[:-1].decode()}' at: {['0x{:x}'.format(a) for a in addrs]}")

# ======= Step 3: 尝试 PyWxDump 的验证函数 + 原始搜索方式 =======
print("\n" + "=" * 60)
print("Step 3: Use PyWxDump's key search near phone type strings")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"

# 尝试所有数据库作为验证目标
test_dbs = [
    os.path.join(DB_BASE, "contact", "contact.db"),
    os.path.join(DB_BASE, "session", "session.db"),
    os.path.join(DB_BASE, "message", "message_0.db"),
]

from pywxdump.wx_core.utils import verify_key as pywxdump_verify_key

for search_str in [b"iphone\x00", b"android\x00", b"ipad\x00"]:
    addrs = search_memory(hProcess, search_str, max_num=10)
    if not addrs:
        continue

    print(f"\nSearching key near '{search_str[:-1].decode()}' ({len(addrs)} hits)...")

    for base_addr in addrs:
        addr_len = 8  # 64-bit

        for offset in range(0, 4000, addr_len):
            addr = base_addr - offset

            # 读取指针
            array = ctypes.create_string_buffer(addr_len)
            if ReadProcessMemory(hProcess, void_p(addr), array, addr_len, 0) == 0:
                continue

            key_addr = int.from_bytes(bytes(array), byteorder='little')
            if key_addr < 0x10000 or key_addr > 0x7FFFFFFFFFFF:
                continue

            # 读取 32 字节
            key_buf = ctypes.create_string_buffer(32)
            if ReadProcessMemory(hProcess, void_p(key_addr), key_buf, 32, 0) == 0:
                continue

            key_bytes = bytes(key_buf)
            if key_bytes == b'\x00' * 32 or key_bytes == b'\xff' * 32:
                continue

            # 对每个测试数据库验证
            for db_path in test_dbs:
                if pywxdump_verify_key(key_bytes, db_path):
                    print(f"  >>> FOUND KEY! db={os.path.basename(db_path)} offset={offset}: {key_bytes.hex()}")
                    # 保存 key 并继续

                    # 尝试解密
                    out_dir = r"C:\Users\v_wenloliu\Documents\xwechat_files\decrypted"
                    os.makedirs(out_dir, exist_ok=True)

                    all_dbs = {
                        "contact.db": os.path.join(DB_BASE, "contact", "contact.db"),
                        "session.db": os.path.join(DB_BASE, "session", "session.db"),
                        "message_0.db": os.path.join(DB_BASE, "message", "message_0.db"),
                        "general.db": os.path.join(DB_BASE, "general", "general.db"),
                        "sns.db": os.path.join(DB_BASE, "sns", "sns.db"),
                    }

                    for db_name, db_p in all_dbs.items():
                        out_path = os.path.join(out_dir, f"de_{db_name}")
                        if pywxdump_verify_key(key_bytes, db_p):
                            # 解密
                            with open(db_p, "rb") as f:
                                blist = f.read()
                            salt = blist[:16]
                            byteHmac = hashlib.pbkdf2_hmac("sha1", key_bytes, salt, 64000, KEY_SIZE)
                            with open(out_path, "wb") as deFile:
                                deFile.write(SQLITE_FILE_HEADER)
                                for i in range(0, len(blist), 4096):
                                    tblist = blist[i:i + 4096] if i > 0 else blist[16:i + 4096]
                                    deFile.write(AES.new(byteHmac, AES.MODE_CBC, tblist[-48:-32]).decrypt(tblist[:-48]))
                                    deFile.write(tblist[-48:])

                            conn = sqlite3.connect(out_path)
                            try:
                                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                                print(f"    {db_name}: OK tables={[t[0] for t in tables]}")
                            except Exception as e:
                                print(f"    {db_name}: decrypt error: {e}")
                            conn.close()
                        else:
                            print(f"    {db_name}: key mismatch")

                    kernel32.CloseHandle(hProcess)
                    sys.exit(0)

print("\nPhone type string search completed. Key not found via this method.")

# ======= Step 4: 尝试直接搜索 key 指纹 =======
print("\n" + "=" * 60)
print("Step 4: Alternative - search for key pattern in heap")
print("=" * 60)

# 搜索 wxid 字符串
wxid = b"wxid_6ofkq4h94pxe22"
wxid_addrs = search_memory(hProcess, wxid, max_num=20)
print(f"wxid occurrences: {len(wxid_addrs)}")
if wxid_addrs:
    print(f"  First 5: {['0x{:x}'.format(a) for a in wxid_addrs[:5]]}")

# 搜索数据库路径
db_pattern = b"db_storage"
db_addrs = search_memory(hProcess, db_pattern, max_num=20)
print(f"db_storage occurrences: {len(db_addrs)}")
if db_addrs:
    for addr in db_addrs[:3]:
        # 读取周围 200 字节
        buf = ctypes.create_string_buffer(200)
        if ReadProcessMemory(hProcess, void_p(max(0, addr - 50)), buf, 200, 0):
            data = bytes(buf)
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
            print(f"  0x{addr:x}: {printable[:150]}")

kernel32.CloseHandle(hProcess)
