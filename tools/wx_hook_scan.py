"""
微信 4.x Hook sqlite3_key - 使用纯 Python + Windows API
策略:
1. 不使用 frida（微信有反 Hook 保护）
2. 使用 pymem 读取进程内存
3. 在 Weixin.dll 中搜索 SQLCipher 的 sqlcipher_codec_ctx_set_pass 函数
4. 设置硬件断点或 INT3 断点来捕获函数调用
5. 从寄存器/栈中提取 key 参数

实际采用的更可行方案:
- 搜索 Weixin.dll 中的 PRAGMA format string 引用
- 通过交叉引用定位调用函数
- 在函数入口设置 breakpoint
- 通过 debug API 读取参数
"""
import sys
import os
import time
import struct
import ctypes
import ctypes.wintypes as wintypes
import pymem
import pymem.process
import hmac
import hashlib
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')

KEY_SIZE = 32
SQLITE_FILE_HEADER = b"SQLite format 3\x00"

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
ReadProcessMemory = kernel32.ReadProcessMemory
void_p = ctypes.c_void_p

# ========= Step 1: 用 pymem 打开进程 =========
print("=" * 60)
print("Step 1: Open Weixin.exe and find Weixin.dll")
print("=" * 60)

pm = pymem.Pymem("Weixin.exe")
pid = pm.process_id
print(f"Attached to PID: {pid}")

# 找到 Weixin.dll 的基址和大小
weixin_dll = None
for mod in pm.list_modules():
    if mod.name == "Weixin.dll":
        weixin_dll = mod
        break

if not weixin_dll:
    print("ERROR: Weixin.dll not found!")
    sys.exit(1)

dll_base = weixin_dll.lpBaseOfDll
dll_size = weixin_dll.SizeOfImage
print(f"Weixin.dll: base=0x{dll_base:x}, size={dll_size} ({dll_size/1024/1024:.1f} MB)")

# ========= Step 2: 搜索关键字符串 =========
print("\n" + "=" * 60)
print("Step 2: Search for key strings in Weixin.dll")
print("=" * 60)

# 分块读取 DLL 内存
CHUNK = 50 * 1024 * 1024  # 50MB
dll_data = bytearray()
offset = 0
while offset < dll_size:
    read_size = min(CHUNK, dll_size - offset)
    try:
        chunk = pm.read_bytes(dll_base + offset, read_size)
        dll_data.extend(chunk)
    except Exception as e:
        print(f"  Read error at offset {offset}: {e}")
        dll_data.extend(b'\x00' * read_size)
    offset += read_size

print(f"Read {len(dll_data)} bytes from Weixin.dll")

# 搜索关键字符串
key_strings = {
    "PRAGMA cipher_compatibility = %d;": None,
    "PRAGMA cipher_page_size = %d;": None,
    "PRAGMA cipher_use_hmac = %d;": None,
    "PRAGMA cipher_default_kdf_iter = %d;": None,
    "PRAGMA cipher_kdf_algorithm = %s;": None,
    "PRAGMA cipher_hmac_algorithm = %s;": None,
    "not an error": None,
    "database is locked": None,
    "sqlcipher_export": None,
}

for search_str, _ in key_strings.items():
    search_bytes = search_str.encode('utf-8')
    pos = dll_data.find(search_bytes)
    if pos >= 0:
        key_strings[search_str] = dll_base + pos
        print(f"  '{search_str}' at 0x{dll_base + pos:x} (offset +0x{pos:x})")

# ========= Step 3: 找到引用 "PRAGMA cipher_compatibility" 的代码 =========
print("\n" + "=" * 60)
print("Step 3: Find code references to PRAGMA strings")
print("=" * 60)

# 在 x86-64 中，字符串引用通常通过 LEA 指令:
# 48 8D xx [4 bytes RIP-relative offset]
# 或者通过 MOV 指令加载地址

for name, str_addr in key_strings.items():
    if str_addr is None:
        continue
    if "cipher_compatibility" not in name and "cipher_page_size" not in name:
        continue

    print(f"\n  Searching xrefs to '{name}' (0x{str_addr:x})...")

    # 搜索 LEA reg, [rip + offset] 形式的引用
    # LEA: 48 8D xx [imm32] 或 4C 8D xx [imm32]
    # 其中 imm32 = target - (current_ip + 7)

    xref_count = 0
    for code_offset in range(0, len(dll_data) - 7, 1):
        # 检查是否是 LEA 指令 (48 8D 或 4C 8D)
        if dll_data[code_offset] in (0x48, 0x4C) and dll_data[code_offset + 1] == 0x8D:
            mod_rm = dll_data[code_offset + 2]
            # 检查 mod_rm 的 mod 字段是 00 且 rm 字段是 101 (RIP-relative)
            mod_field = (mod_rm >> 6) & 0x03
            rm_field = mod_rm & 0x07

            if mod_field == 0x00 and rm_field == 0x05:
                # RIP-relative: 目标地址 = 当前指令地址 + 7 + imm32
                imm32 = struct.unpack('<i', dll_data[code_offset + 3:code_offset + 7])[0]
                current_addr = dll_base + code_offset
                target_addr = current_addr + 7 + imm32

                if target_addr == str_addr:
                    xref_count += 1
                    print(f"    XREF at 0x{current_addr:x}: LEA to '{name}'")

                    # 往前找函数入口 (搜索 push rbp 或 sub rsp 模式)
                    func_start = None
                    for back in range(code_offset, max(0, code_offset - 500), -1):
                        # 常见函数入口:
                        # 55 (push rbp)
                        # 48 89 5C 24 xx (mov [rsp+xx], rbx)
                        # 48 83 EC xx (sub rsp, xx)
                        # 40 55 (push rbp with REX)
                        # CC (int3, function boundary)
                        if dll_data[back] == 0xCC:  # padding between functions
                            func_start = dll_base + back + 1
                            break
                        if back > 0 and dll_data[back-1] == 0xC3:  # ret before this func
                            func_start = dll_base + back
                            break

                    if func_start:
                        print(f"    Likely function start: 0x{func_start:x}")
                        # 读取函数开头几个字节
                        func_offset = func_start - dll_base
                        func_bytes = dll_data[func_offset:func_offset+16]
                        hex_str = " ".join(f"{b:02x}" for b in func_bytes)
                        print(f"    Function prologue: {hex_str}")

                    if xref_count >= 3:
                        break

# ========= Step 4: 搜索 "PRAGMA key" 变体 (可能不是字符串形式) =========
print("\n" + "=" * 60)
print("Step 4: Search for key setting patterns")
print("=" * 60)

# SQLCipher 的 key 设置通常在 sqlite3_key_v2 -> sqlcipher_codec_ctx_set_pass
# 该函数的签名: int sqlcipher_codec_ctx_set_pass(codec_ctx*, const void* key, int keyLen, int forCtx)
# forCtx = 0 (用户 key), 1 (rekey)

# 搜索 "sqlcipher_export" 的 xrefs —— 这个函数在 sqlite3_key 附近注册
export_str_addr = key_strings.get("sqlcipher_export")
if export_str_addr:
    print(f"\n  Searching xrefs to 'sqlcipher_export' (0x{export_str_addr:x})...")

    for code_offset in range(0, len(dll_data) - 7, 1):
        if dll_data[code_offset] in (0x48, 0x4C) and dll_data[code_offset + 1] == 0x8D:
            mod_rm = dll_data[code_offset + 2]
            if (mod_rm >> 6) == 0 and (mod_rm & 0x07) == 0x05:
                imm32 = struct.unpack('<i', dll_data[code_offset + 3:code_offset + 7])[0]
                current_addr = dll_base + code_offset
                target_addr = current_addr + 7 + imm32

                if target_addr == export_str_addr:
                    print(f"    XREF at 0x{current_addr:x}")

                    # 打印这个位置前后的反汇编
                    ctx_start = max(0, code_offset - 100)
                    ctx_end = min(len(dll_data), code_offset + 100)
                    for i in range(ctx_start, ctx_end, 16):
                        line = dll_data[i:i+16]
                        hex_str = " ".join(f"{b:02x}" for b in line)
                        addr = dll_base + i
                        print(f"      0x{addr:x}: {hex_str}")

# ========= Step 5: 最直接的方案 - 搜索密钥模式 =========
print("\n" + "=" * 60)
print("Step 5: Search heap for database key pattern")
print("=" * 60)

# 从之前的分析中我们知道 WCDB 使用 PRAGMA cipher_salt 设置 salt
# 密钥可能以不同形式存储在堆中
# 搜索策略: 找到已知的 db salt 值，然后在其附近搜索 key

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
contact_db = os.path.join(DB_BASE, "contact", "contact.db")

with open(contact_db, "rb") as f:
    page1 = f.read(4096)

db_salt = page1[:16]
print(f"contact.db salt: {db_salt.hex()}")

# 在进程堆中搜索这个 salt
print("Searching for DB salt in process memory...")
salt_addrs = pymem.pattern.pattern_scan_all(pm.process_handle, db_salt, return_multiple=True)
print(f"Found salt at {len(salt_addrs)} locations")

for addr in salt_addrs[:5]:
    print(f"  Salt at 0x{addr:x}")

    # 读取 salt 前后 200 字节
    try:
        context = pm.read_bytes(max(0, addr - 100), 300)

        # 分析上下文 - 寻找可能的 key
        for i in range(0, len(context) - 32, 8):
            candidate = context[i:i+32]
            if candidate == b'\x00' * 32 or candidate == b'\xff' * 32:
                continue
            if candidate == db_salt + db_salt:  # 避免匹配 salt 本身
                continue

            # 简单启发式: key 的熵应该比较高
            unique_bytes = len(set(candidate))
            if unique_bytes > 20:  # 至少 20 种不同的字节值
                ctx_addr = addr - 100 + i
                print(f"    High-entropy 32 bytes at 0x{ctx_addr:x}: {candidate.hex()}")
    except:
        pass

pm.close_process()
print("\nDone.")
