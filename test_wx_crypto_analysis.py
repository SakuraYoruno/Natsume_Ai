"""
微信 4.x 数据库加密格式深度分析
发现: padding 不为零，说明不是标准 SQLCipher
需要确定:
1. 是否是 WCDB (腾讯自研的 SQLCipher 变体)
2. 加密参数差异 (KDF iter, HMAC algo, page size, reserve size)
3. 实际的 key 格式
"""
import os
import sys
import sqlite3
import struct

sys.stdout.reconfigure(encoding='utf-8')

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"

# ======= 分析 1: 多个数据库的头部结构对比 =======
print("=" * 60)
print("Analysis 1: Compare first pages of multiple DBs")
print("=" * 60)

dbs = {
    "contact.db": os.path.join(DB_BASE, "contact", "contact.db"),
    "session.db": os.path.join(DB_BASE, "session", "session.db"),
    "message_0.db": os.path.join(DB_BASE, "message", "message_0.db"),
    "emoticon.db": os.path.join(DB_BASE, "emoticon", "emoticon.db"),
}

for name, path in dbs.items():
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        page1 = f.read(4096)

    print(f"\n--- {name} ({size} bytes, {size // 4096} pages) ---")

    # 分析 reserve area (标准 SQLCipher: 最后 48 字节 = 16 IV + 20 HMAC + 12 pad)
    for reserve_size in [48, 36, 32, 64]:
        reserve = page1[-reserve_size:]
        rest = page1[16:-reserve_size] if reserve_size < 4096 else b""
        print(f"  Last {reserve_size} bytes: {reserve.hex()}")

    # 只显示最后 48 字节的详细分析
    r48 = page1[-48:]
    print(f"  [Analysis of last 48 bytes]")
    print(f"    Bytes [-48:-32] (possible IV):   {r48[:16].hex()}")
    print(f"    Bytes [-32:-12] (possible HMAC): {r48[16:36].hex()}")
    print(f"    Bytes [-12:  ] (possible pad):   {r48[36:].hex()}")

    # 检查 page 2 的结构
    with open(path, "rb") as f:
        f.seek(4096)
        page2 = f.read(4096)

    if len(page2) == 4096:
        r48_p2 = page2[-48:]
        print(f"  Page 2 last 48: IV={r48_p2[:16].hex()} HMAC={r48_p2[16:36].hex()} PAD={r48_p2[36:].hex()}")

    # Salt (第一页前16字节)
    print(f"  Salt (first 16 bytes): {page1[:16].hex()}")

# ======= 分析 2: 检查是否可能是不同的 reserve_size =======
print("\n" + "=" * 60)
print("Analysis 2: Try different reserve sizes and page sizes")
print("=" * 60)

# WCDB 可能用不同的 reserve size
# SQLCipher 默认: reserve = 48 (IV 16 + HMAC 20 + padding 12)
# WCDB 可能: reserve = 32 (IV 16 + HMAC 16) 或其他

path = dbs["contact.db"]
with open(path, "rb") as f:
    data = f.read()

file_size = len(data)
print(f"\nFile size: {file_size}")

for page_size in [1024, 2048, 4096, 8192, 16384]:
    if file_size % page_size == 0:
        pages = file_size // page_size
        print(f"\n  Page size {page_size}: {pages} pages")

        # 比较不同页的结尾部分
        if pages >= 2:
            p1 = data[0:page_size]
            p2 = data[page_size:page_size*2]

            # 找到每页末尾的零字节模式
            for reserve in [16, 20, 32, 36, 48, 64]:
                p1_end = p1[-reserve:]
                p2_end = p2[-reserve:]

                # 检查是否有规律
                p1_has_zeros = p1_end[-12:] == b'\x00' * 12
                p2_has_zeros = p2_end[-12:] == b'\x00' * 12

                if p1_has_zeros or p2_has_zeros:
                    print(f"    reserve={reserve}: p1_zeros={p1_has_zeros}, p2_zeros={p2_has_zeros}")

# ======= 分析 3: 尝试 WCDB/SQLCipher 不同参数 =======
print("\n" + "=" * 60)
print("Analysis 3: Check for plaintext header")
print("=" * 60)

# WCDB 支持 cipher_plaintext_header_size, 这意味着头部可能部分是明文
# 但微信 4.x 的头部不是 "SQLite format 3\x00"

# 检查前几个字节是否有规律
for name, path in dbs.items():
    with open(path, "rb") as f:
        header = f.read(100)

    # 检查第一个字节是否可能是 page_size 编码
    # SQLite 文件头 offset 16-17 是 page_size
    print(f"\n  {name}:")
    print(f"    First 4 bytes: {header[:4].hex()}")
    print(f"    Bytes 16-18: {header[16:18].hex()}")

# ======= 分析 4: 检查 DLL 中的加密配置 =======
print("\n" + "=" * 60)
print("Analysis 4: Deep scan shared_andromeda.dll for crypto config")
print("=" * 60)

dll_path = r"C:\Program Files\Tencent\Weixin\4.1.8.28\shared_andromeda.dll"
with open(dll_path, "rb") as f:
    dll = f.read()

# 搜索更多加密相关字符串
search_terms = [
    b"sqlite3", b"SQLITE", b"cipher", b"Cipher",
    b"encrypt", b"decrypt", b"ENCRYPT", b"DECRYPT",
    b"AES", b"aes", b"CBC", b"CTR", b"GCM",
    b"PBKDF2", b"pbkdf2", b"HMAC", b"hmac",
    b"SHA1", b"sha1", b"SHA256", b"sha256", b"SHA512", b"sha512",
    b"kdf_iter", b"page_size", b"cipher_",
    b"PRAGMA", b"pragma",
    b"sqliterk", b"repair",
    b"64000",  # 标准 SQLCipher KDF iterations
    b"256000", # WCDB 可能的 KDF iterations
    b"4000",   # 另一个可能的迭代次数
]

found_terms = {}
for term in search_terms:
    positions = []
    start = 0
    while len(positions) < 3:
        pos = dll.find(term, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1
    if positions:
        found_terms[term.decode('ascii', errors='replace')] = positions

for term, positions in sorted(found_terms.items()):
    print(f"  '{term}': {len(positions)} hit(s) at {positions}")
    # 显示上下文
    for p in positions[:1]:
        ctx_start = max(0, p - 20)
        ctx_end = min(len(dll), p + len(term) + 40)
        ctx = dll[ctx_start:ctx_end]
        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
        print(f"    Context: {printable}")

# ======= 分析 5: 检查其他 DLL =======
print("\n" + "=" * 60)
print("Analysis 5: Check other Weixin DLLs")
print("=" * 60)

dll_dir = r"C:\Program Files\Tencent\Weixin\4.1.8.28"
for dll_name in os.listdir(dll_dir):
    if dll_name.endswith(".dll"):
        dll_p = os.path.join(dll_dir, dll_name)
        dll_size = os.path.getsize(dll_p)

        with open(dll_p, "rb") as f:
            content = f.read()

        for term in [b"sqlcipher", b"SQLCipher", b"WCDB", b"wcdb", b"sqlite3_key", b"cipher_page"]:
            if term in content:
                print(f"  {dll_name} ({dll_size/1024:.0f}KB): contains '{term.decode()}'")
                break

del dll
