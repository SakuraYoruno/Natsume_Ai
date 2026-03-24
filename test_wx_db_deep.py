"""深入分析 key_info.db 和数据库加密格式"""
import os
import sqlite3
import binascii

# ======= Part 1: 深入分析 key_info.db =======
print("=" * 60)
print("Part 1: key_info.db 详细分析")
print("=" * 60)

key_db_path = r"C:\Users\v_wenloliu\Documents\xwechat_files\all_users\login\wxid_6ofkq4h94pxe22\key_info.db"
conn = sqlite3.connect(f"file:{key_db_path}?mode=ro", uri=True)
cursor = conn.cursor()

cursor.execute("SELECT * FROM LoginKeyInfoTable")
rows = cursor.fetchall()

print(f"\n总记录数: {len(rows)}")
for i, row in enumerate(rows):
    user_name_md5, key_md5, key_info_md5, key_info_data = row
    print(f"\n--- 记录 {i+1} ---")
    print(f"  user_name_md5: {user_name_md5}")
    print(f"  key_md5: {key_md5}")
    print(f"  key_info_md5: {key_info_md5}")
    if key_info_data:
        print(f"  key_info_data length: {len(key_info_data)} bytes")
        print(f"  key_info_data hex: {key_info_data.hex()}")
        # 尝试解析前几个字节
        print(f"  key_info_data[:4]: {key_info_data[:4].hex()} (可能是长度或版本)")
        if len(key_info_data) >= 36:
            print(f"  key_info_data[4:36]: {key_info_data[4:36].hex()} (可能是32字节key)")
    else:
        print(f"  key_info_data: None")

conn.close()

# ======= Part 2: 分析加密数据库头部特征 =======
print("\n" + "=" * 60)
print("Part 2: 加密数据库头部分析")
print("=" * 60)

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
msg_db = os.path.join(DB_BASE, "message", "message_0.db")

with open(msg_db, "rb") as f:
    data = f.read(4096)  # 读取第一页

print(f"\n文件大小: {os.path.getsize(msg_db)} bytes")
print(f"第一页前64字节 hex:")
for offset in range(0, 64, 16):
    line = data[offset:offset+16]
    hex_str = " ".join(f"{b:02x}" for b in line)
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in line)
    print(f"  {offset:04x}: {hex_str}  {ascii_str}")

# 检查是否是 SQLCipher (前16字节是salt)
print(f"\n前16字节(可能的salt): {data[:16].hex()}")
print(f"第17-32字节: {data[16:32].hex()}")

# SQLCipher 默认 page_size=4096, 第一页的 salt 占16字节
# 检查文件大小是否是 4096 的倍数
file_size = os.path.getsize(msg_db)
print(f"\n文件大小 % 4096 = {file_size % 4096}")
print(f"文件大小 % 1024 = {file_size % 1024}")

# ======= Part 3: 检查 kvdb 文件 =======
print("\n" + "=" * 60)
print("Part 3: kvdb 文件分析")
print("=" * 60)

kvdb_path = os.path.join(DB_BASE, "message", "message_0.kvdb")
if os.path.exists(kvdb_path):
    size = os.path.getsize(kvdb_path)
    with open(kvdb_path, "rb") as f:
        header = f.read(64)
    print(f"message_0.kvdb 大小: {size} bytes")
    print(f"Header: {header[:16].hex()}")
    is_sqlite = header[:6] == b"SQLite"
    print(f"是否明文SQLite: {is_sqlite}")
    if is_sqlite:
        try:
            conn2 = sqlite3.connect(f"file:{kvdb_path}?mode=ro", uri=True)
            tables = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            print(f"Tables: {[t[0] for t in tables]}")
            conn2.close()
        except Exception as e:
            print(f"Open error: {e}")

# ======= Part 4: 尝试用 pysqlcipher3 或 sqlcipher 解密 =======
print("\n" + "=" * 60)
print("Part 4: 检查可用的解密工具")
print("=" * 60)

try:
    from pywxdump.wx_core.wx_decrypt import decrypt as wx_decrypt
    print("pywxdump.wx_core.wx_decrypt 可用")
except ImportError as e:
    print(f"pywxdump decrypt 不可用: {e}")

try:
    import pysqlcipher3
    print("pysqlcipher3 可用")
except ImportError:
    print("pysqlcipher3 不可用")

try:
    from Cryptodome.Cipher import AES
    print("pycryptodomex (AES) 可用")
except ImportError:
    print("pycryptodomex 不可用")
