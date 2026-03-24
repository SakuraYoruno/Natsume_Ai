"""
微信 4.x - 从 cipher_compatibility 函数反编译提取加密参数
已知:
- 函数入口: 0x7ffb762bd010 (Weixin.dll + 0x29ed010)
- 函数引用了 "PRAGMA cipher_compatibility = %d;" 字符串
- 这个函数负责初始化数据库加密

目标:
1. 分析函数中引用的所有 PRAGMA 字符串
2. 提取 KDF iteration count, HMAC algo, cipher page size 等参数
3. 找到 key 的来源
"""
import sys
import os
import struct
import pymem
import pymem.process
import hmac
import hashlib
import sqlite3
from Cryptodome.Cipher import AES

sys.stdout.reconfigure(encoding='utf-8')

KEY_SIZE = 32
SQLITE_FILE_HEADER = b"SQLite format 3\x00"

pm = pymem.Pymem("Weixin.exe")
pid = pm.process_id
print(f"PID: {pid}")

# 找到 Weixin.dll
for mod in pm.list_modules():
    if mod.name == "Weixin.dll":
        dll_base = mod.lpBaseOfDll
        dll_size = mod.SizeOfImage
        break

print(f"Weixin.dll: 0x{dll_base:x}, size={dll_size}")

# ========= Step 1: 读取 cipher_compatibility 函数 =========
print("\n" + "=" * 60)
print("Step 1: Disassemble cipher_compatibility function")
print("=" * 60)

func_addr = 0x7ffb762bd010  # 从上一次扫描的结果
func_rva = func_addr - dll_base

# 读取函数 2KB
func_data = pm.read_bytes(func_addr, 2048)

# 找到这个函数中所有的 LEA [rip+imm32] 指令，提取引用的字符串
print(f"Analyzing function at 0x{func_addr:x} (RVA 0x{func_rva:x}):")

lea_refs = []
for i in range(len(func_data) - 7):
    # 48 8D xx 05 [imm32] (LEA r64, [rip+imm32] where mod=00, rm=101)
    # 4C 8D xx 05 [imm32]
    if func_data[i] in (0x48, 0x4C) and func_data[i+1] == 0x8D:
        modrm = func_data[i+2]
        mod_field = (modrm >> 6) & 0x03
        rm_field = modrm & 0x07

        if mod_field == 0x00 and rm_field == 0x05:
            imm32 = struct.unpack('<i', func_data[i+3:i+7])[0]
            current_ip = func_addr + i + 7  # 指令末尾
            target = current_ip + imm32

            # 尝试读取目标地址的字符串
            try:
                raw = pm.read_bytes(target, 200)
                null_pos = raw.find(b'\x00')
                if null_pos > 0 and null_pos < 200:
                    text = raw[:null_pos].decode('utf-8', errors='replace')
                    if len(text) > 2 and text.isprintable():
                        reg_num = (modrm >> 3) & 0x07
                        reg_names_48 = ['rax','rcx','rdx','rbx','rsp','rbp','rsi','rdi']
                        reg_names_4c = ['r8','r9','r10','r11','r12','r13','r14','r15']
                        reg = reg_names_4c[reg_num] if func_data[i] == 0x4C else reg_names_48[reg_num]

                        lea_refs.append({
                            'offset': i,
                            'addr': func_addr + i,
                            'target': target,
                            'reg': reg,
                            'string': text,
                        })
            except:
                pass

# 显示所有字符串引用
print(f"\nFound {len(lea_refs)} string references in function:")
for ref in lea_refs:
    print(f"  +0x{ref['offset']:03x} (0x{ref['addr']:x}): LEA {ref['reg']}, [{ref['string'][:80]}]")

# ========= Step 2: 找到函数中的 CALL 指令 =========
print(f"\nCALL instructions in function:")
for i in range(len(func_data) - 5):
    if func_data[i] == 0xE8:  # CALL rel32
        imm32 = struct.unpack('<i', func_data[i+1:i+5])[0]
        target = func_addr + i + 5 + imm32
        print(f"  +0x{i:03x}: CALL 0x{target:x}")

# ========= Step 3: 提取加密参数 =========
print("\n" + "=" * 60)
print("Step 2: Extract cipher parameters from function")
print("=" * 60)

# 从字符串引用中提取参数
for ref in lea_refs:
    s = ref['string']
    if 'PRAGMA cipher_compatibility' in s:
        print(f"  cipher_compatibility: format='{s}'")
    elif 'PRAGMA cipher_page_size' in s:
        print(f"  cipher_page_size: format='{s}'")
    elif 'PRAGMA cipher_use_hmac' in s:
        print(f"  cipher_use_hmac: format='{s}'")
    elif 'PRAGMA cipher_default_kdf_iter' in s:
        print(f"  cipher_default_kdf_iter: format='{s}'")
    elif 'PRAGMA cipher_kdf_algorithm' in s:
        print(f"  cipher_kdf_algorithm: format='{s}'")
    elif 'PRAGMA cipher_hmac_algorithm' in s:
        print(f"  cipher_hmac_algorithm: format='{s}'")
    elif 'PRAGMA cipher_plaintext_header_size' in s:
        print(f"  cipher_plaintext_header_size: format='{s}'")
    elif 'PRAGMA cipher_salt' in s:
        print(f"  cipher_salt: format='{s}'")
    elif 'PRAGMA key' in s:
        print(f"  >>> KEY FORMAT: '{s}'")

# ========= Step 4: 搜索更大范围的函数调用链 =========
# cipher_compatibility 函数本身可能是被更高层函数调用的
# 搜索调用此函数的代码

print(f"\nSearching for callers of function 0x{func_addr:x}...")

# 读取 dll_data 的 .text section
# 搜索 CALL [func_addr]
# CALL rel32: E8 [imm32], target = current + 5 + imm32
# 所以 imm32 = func_addr - (caller + 5)

# 分块搜索
CHUNK = 50 * 1024 * 1024
callers = []
for chunk_start in range(0, dll_size, CHUNK):
    read_size = min(CHUNK, dll_size - chunk_start)
    try:
        data = pm.read_bytes(dll_base + chunk_start, read_size)
    except:
        continue

    for i in range(len(data) - 5):
        if data[i] == 0xE8:
            imm32 = struct.unpack('<i', data[i+1:i+5])[0]
            caller_addr = dll_base + chunk_start + i
            target = caller_addr + 5 + imm32

            if target == func_addr:
                callers.append(caller_addr)
                if len(callers) <= 10:
                    print(f"  Caller at 0x{caller_addr:x}")

print(f"Total callers: {len(callers)}")

# ========= Step 5: 分析 salt 附近的结构来推断 key =========
print("\n" + "=" * 60)
print("Step 3: Analyze salt context for key")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
contact_db = os.path.join(DB_BASE, "contact", "contact.db")

with open(contact_db, "rb") as f:
    page1 = f.read(4096)

db_salt_hex = page1[:16].hex()  # 原始 hex
print(f"contact.db salt (raw hex): {db_salt_hex}")

# 搜索 salt 的 ASCII hex 表示
salt_hex_str = db_salt_hex.encode('ascii')  # b"b5ce90e6..."
salt_addrs = pymem.pattern.pattern_scan_all(pm.process_handle, page1[:16], return_multiple=True)

# 也搜索 ASCII 版本
print(f"\nSearching for salt ASCII hex string in memory...")
ascii_salt = b"3ce1dd7c74908fd48d745e1e5de17282"  # 从之前发现的大小写混合
ascii_addrs = pymem.pattern.pattern_scan_all(pm.process_handle, ascii_salt, return_multiple=True)
print(f"Found ASCII salt at {len(ascii_addrs)} locations")

for addr in ascii_addrs[:3]:
    # 读取完整结构: 往前 200 字节，往后 200 字节
    start = max(0, addr - 200)
    try:
        context = pm.read_bytes(start, 600)
        offset_in_ctx = addr - start

        print(f"\n  ASCII salt at 0x{addr:x}:")

        # hex dump
        for row_start in range(0, min(400, len(context)), 32):
            line = context[row_start:row_start+32]
            hex_str = " ".join(f"{b:02x}" for b in line)
            ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
            abs_addr = start + row_start
            marker = " <-- SALT" if row_start <= offset_in_ctx < row_start + 32 else ""
            print(f"    0x{abs_addr:x}: {ascii_str}{marker}")

        # 寻找 key 模式: key 可能在 salt 前面或后面
        # 从之前的分析: 结构是 protobuf 格式
        # [prefix] [salt_hex_string] [key_hex_string] [suffix]

        # 在 salt 后面找连续的 hex 字符
        after_salt = context[offset_in_ctx + len(ascii_salt):]
        hex_after = bytearray()
        skip_non_hex = 0
        for b in after_salt:
            if (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102):  # 0-9, A-F, a-f
                hex_after.append(b)
            else:
                if len(hex_after) >= 10:
                    break
                if skip_non_hex > 5:
                    break
                skip_non_hex += 1

        if hex_after:
            hex_str = bytes(hex_after).decode('ascii')
            print(f"    Hex after salt: {hex_str} ({len(hex_str)} chars)")

        # 在 salt 前面找连续的 hex 字符
        before_salt = context[:offset_in_ctx]
        hex_before = bytearray()
        for b in reversed(before_salt):
            if (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102):
                hex_before.insert(0, b)
            else:
                if len(hex_before) >= 10:
                    break
                hex_before.clear()

        if hex_before:
            hex_str = bytes(hex_before).decode('ascii')
            print(f"    Hex before salt: {hex_str} ({len(hex_str)} chars)")

    except Exception as e:
        print(f"  Error reading: {e}")

pm.close_process()
print("\nDone.")
