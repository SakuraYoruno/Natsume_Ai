"""
微信 4.x 数据库解密 - 最终版
策略:
1. 先检查 DLL 中是否有 sqlcipher/WCDB 特征
2. 在 shared_andromeda.dll 内存空间附近搜索 key
3. 使用 verify_key 验证 (PyWxDump 的标准验证)
"""
import os
import sys
import sqlite3
import hmac
import hashlib
import ctypes
import ctypes.wintypes as wintypes
from Cryptodome.Cipher import AES

# 输出编码修复
sys.stdout.reconfigure(encoding='utf-8')

SQLITE_FILE_HEADER = b"SQLite format 3\x00"
KEY_SIZE = 32

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

def verify_key_for_db(key_bytes, db_path):
    """验证 key 是否能解密指定数据库"""
    with open(db_path, "rb") as f:
        blist = f.read(4096)
    if len(blist) < 4096:
        return False
    salt = blist[:16]
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    byteHmac = hashlib.pbkdf2_hmac("sha1", key_bytes, salt, 64000, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac("sha1", byteHmac, mac_salt, 2, KEY_SIZE)
    hash_mac = hmac.new(mac_key, blist[16:4064], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')
    return hash_mac.digest() == blist[4064:4084]

def decrypt_db(key_bytes, db_path, out_path):
    """解密数据库文件"""
    with open(db_path, "rb") as f:
        blist = f.read()
    salt = blist[:16]
    byteHmac = hashlib.pbkdf2_hmac("sha1", key_bytes, salt, 64000, KEY_SIZE)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as deFile:
        deFile.write(SQLITE_FILE_HEADER)
        for i in range(0, len(blist), 4096):
            tblist = blist[i:i + 4096] if i > 0 else blist[16:i + 4096]
            deFile.write(AES.new(byteHmac, AES.MODE_CBC, tblist[-48:-32]).decrypt(tblist[:-48]))
            deFile.write(tblist[-48:])

# ======= Step 0: 确认加密方式 =======
print("=" * 60)
print("Step 0: Verify encryption format")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
contact_db = os.path.join(DB_BASE, "contact", "contact.db")

with open(contact_db, "rb") as f:
    page1 = f.read(4096)

# 检查 SQLCipher 特征
reserve = page1[-48:]
pad = reserve[36:]
print(f"Reserve area padding: {pad.hex()}")
zero12 = b'\x00' * 12
print(f"Padding is zeros: {pad == zero12}")

# 即使 padding 不全为 0，我们仍然尝试标准 SQLCipher 方式
# 有些版本的 SQLCipher 会使用不同的 reserve 大小

# ======= Step 1: 先搜索 DLL 中的加密特征字符串 =======
print("\n" + "=" * 60)
print("Step 1: Search DLL for encryption signatures")
print("=" * 60)

dll_path = r"C:\Program Files\Tencent\Weixin\4.1.8.28\shared_andromeda.dll"
if os.path.exists(dll_path):
    with open(dll_path, "rb") as f:
        dll_content = f.read()

    for term in [b"sqlcipher", b"SQLCipher", b"PRAGMA key", b"sqlite3_key",
                 b"wcdb", b"WCDB", b"sqliterk", b"openssl", b"OPENSSL",
                 b"cipher_page_size", b"kdf_iter", b"hmac_use"]:
        pos = dll_content.find(term)
        if pos >= 0:
            ctx = dll_content[max(0,pos-10):pos+len(term)+30]
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
            print(f"  Found '{term.decode()}' at offset {pos}: {printable}")

    # 搜索 WCDB 特有的 key 派生相关字符串
    for term in [b"cipher_default_kdf_iter", b"cipher_hmac_algorithm",
                 b"cipher_kdf_algorithm", b"cipher_plaintext_header_size",
                 b"cipher_use_hmac"]:
        pos = dll_content.find(term)
        if pos >= 0:
            print(f"  Found WCDB param: '{term.decode()}' at offset {pos}")

    del dll_content  # 释放内存

# ======= Step 2: 从 Weixin.exe 进程获取 key =======
print("\n" + "=" * 60)
print("Step 2: Search Weixin.exe memory for key")
print("=" * 60)

from pywxdump.wx_core.utils.ctypes_utils import get_process_list, get_memory_maps, get_process_exe_path

# 获取 Weixin.exe 的 PID
weixin_pids = [(pid, name) for pid, name in get_process_list() if name == "Weixin.exe"]
print(f"Weixin.exe processes: {weixin_pids}")

if not weixin_pids:
    print("Weixin.exe not found!")
    sys.exit(1)

pid = weixin_pids[0][0]
print(f"Using PID: {pid}")

# 获取内存映射，找到 shared_andromeda.dll 的地址范围
memory_maps = get_memory_maps(pid)
andromeda_start = 0x7FFFFFFFFFFFFFFF
andromeda_end = 0

for module in memory_maps:
    if module.FileName and 'shared_andromeda.dll' in module.FileName:
        s = module.BaseAddress
        e = module.BaseAddress + module.RegionSize
        andromeda_start = min(s, andromeda_start)
        andromeda_end = max(e, andromeda_end)

print(f"shared_andromeda.dll range: 0x{andromeda_start:x} - 0x{andromeda_end:x}")
print(f"Size: {(andromeda_end - andromeda_start) / 1024 / 1024:.1f} MB")

# 打开进程
hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
if not hProcess:
    print(f"Failed to open process: {ctypes.get_last_error()}")
    sys.exit(1)

# 方法1: 搜索 "iphone"/"android"/"ipad" 字符串（PyWxDump 原始方法，但在 andromeda 范围内）
print("\nMethod A: Search for phone type strings near andromeda.dll...")

from pywxdump.wx_core.utils.ctypes_utils import search_memory

for search_str in [b"iphone\x00", b"android\x00", b"ipad\x00"]:
    addrs = search_memory(hProcess, search_str, max_num=10,
                         start_address=andromeda_start, end_address=andromeda_end)
    if addrs:
        print(f"  Found '{search_str[:-1].decode()}' at: {['0x{:x}'.format(a) for a in addrs]}")

# 方法2: 搜索进程全部内存中的 phone type strings
print("\nMethod B: Search entire process memory for phone type strings...")

for search_str in [b"iphone\x00", b"android\x00", b"ipad\x00"]:
    addrs = search_memory(hProcess, search_str, max_num=10)
    if addrs:
        print(f"  Found '{search_str[:-1].decode()}' at: {['0x{:x}'.format(a) for a in addrs]}")

        # 对于找到的每个地址，在其附近搜索 key
        addr_len = 8  # 64位进程
        ReadProcessMemory = kernel32.ReadProcessMemory
        void_p = ctypes.c_void_p

        for base_addr in addrs[:2]:
            print(f"  Searching key near 0x{base_addr:x}...")
            tested = 0
            for offset in range(0, 2000, addr_len):
                addr = base_addr - offset
                # 读取地址指针
                array = ctypes.create_string_buffer(addr_len)
                if ReadProcessMemory(hProcess, void_p(addr), array, addr_len, 0) == 0:
                    continue

                key_addr = int.from_bytes(bytes(array), byteorder='little')

                # 验证地址是否合理
                if key_addr < 0x10000 or key_addr > 0x7FFFFFFFFFFF:
                    continue

                # 读取 32 字节 key
                key_buf = ctypes.create_string_buffer(32)
                if ReadProcessMemory(hProcess, void_p(key_addr), key_buf, 32, 0) == 0:
                    continue

                key_bytes = bytes(key_buf)
                if key_bytes == b'\x00' * 32:
                    continue

                tested += 1

                # 验证
                if verify_key_for_db(key_bytes, contact_db):
                    print(f"  >>> FOUND KEY at base-{offset}: {key_bytes.hex()}")

                    # 保存并退出搜索
                    kernel32.CloseHandle(hProcess)

                    # 解密所有数据库
                    print("\n" + "=" * 60)
                    print("Step 3: Decrypt all databases!")
                    print("=" * 60)

                    out_dir = r"C:\Users\v_wenloliu\Documents\xwechat_files\decrypted"
                    all_dbs = [
                        ("message_0.db", os.path.join(DB_BASE, "message", "message_0.db")),
                        ("contact.db", os.path.join(DB_BASE, "contact", "contact.db")),
                        ("session.db", os.path.join(DB_BASE, "session", "session.db")),
                        ("head_image.db", os.path.join(DB_BASE, "head_image", "head_image.db")),
                        ("emoticon.db", os.path.join(DB_BASE, "emoticon", "emoticon.db")),
                        ("sns.db", os.path.join(DB_BASE, "sns", "sns.db")),
                        ("general.db", os.path.join(DB_BASE, "general", "general.db")),
                    ]

                    for db_name, db_p in all_dbs:
                        out_path = os.path.join(out_dir, f"de_{db_name}")
                        if verify_key_for_db(key_bytes, db_p):
                            decrypt_db(key_bytes, db_p, out_path)
                            try:
                                conn = sqlite3.connect(out_path)
                                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                                print(f"  {db_name}: OK, tables={[t[0] for t in tables]}")
                                conn.close()
                            except Exception as e:
                                print(f"  {db_name}: decrypt ok but read error: {e}")
                        else:
                            print(f"  {db_name}: key mismatch")

                    sys.exit(0)

            print(f"    Tested {tested} candidates, no match")

# 方法3: 搜索 wxid 字符串附近
print("\nMethod C: Search near wxid string...")
wxid = b"wxid_6ofkq4h94pxe22"
addrs = search_memory(hProcess, wxid, max_num=10)
if addrs:
    print(f"  Found wxid at: {['0x{:x}'.format(a) for a in addrs[:5]]}")

kernel32.CloseHandle(hProcess)

print("\n[INFO] If all methods failed, WeChat 4.x likely uses WCDB (a modified SQLCipher)")
print("WCDB may use different KDF iterations or cipher parameters")
