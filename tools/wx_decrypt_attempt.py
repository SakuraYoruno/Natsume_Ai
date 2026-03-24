"""
微信 4.x - 交叉匹配所有 key 和 DB
1. 从内存提取所有 x'...' key (它们是 ASCII hex 文本)
2. 读取所有 DB 的文件 salt (二进制前 16 字节)
3. 比较 key 最后 16 字节的 hex decode 与 DB salt
4. 用正确匹配的 key 尝试解密
"""
import sys
import os
import hmac
import hashlib
import sqlite3
import struct
import pymem
from Cryptodome.Cipher import AES

sys.stdout.reconfigure(encoding='utf-8')

KEY_SIZE = 32
SQLITE_FILE_HEADER = b"SQLite format 3\x00"
DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
OUT_DIR = r"C:\Users\v_wenloliu\Documents\xwechat_files\decrypted"

# ========= Step 1: 提取所有 key =========
print("=" * 60)
print("Step 1: Extract all keys from memory")
print("=" * 60)

pm = pymem.Pymem("Weixin.exe")

# 搜索 x' 模式
x_prime_addrs = pymem.pattern.pattern_scan_all(pm.process_handle, b"x'", return_multiple=True)
print(f"Found x' at {len(x_prime_addrs)} locations")

all_keys = {}  # hex_string -> [addrs]
for addr in x_prime_addrs:
    try:
        raw = pm.read_bytes(addr + 2, 200)
        end = raw.find(b"'")
        if end <= 0:
            continue

        content = raw[:end]

        # 这些字节是 ASCII hex 字符（每个字节是 '0'-'9', 'a'-'f', 'A'-'F'）
        # 验证所有字节都是 hex ASCII
        all_hex = all(
            (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102)
            for b in content
        )

        if all_hex and len(content) == 96:  # 96 ASCII chars = 48 bytes
            text = content.decode('ascii')
            if text not in all_keys:
                all_keys[text] = []
            all_keys[text].append(addr)
    except:
        pass

print(f"Found {len(all_keys)} unique 96-char keys")

# 解析每个 key: 96 ASCII hex chars -> 48 bytes -> 32 key + 16 salt
key_data = {}  # {ascii_key: (enc_key_bytes, salt_bytes)}
for ascii_key in all_keys:
    raw_48 = bytes.fromhex(ascii_key)
    enc_key = raw_48[:32]
    key_salt = raw_48[32:]
    key_data[ascii_key] = (enc_key, key_salt)
    print(f"  Key: {enc_key.hex()}")
    print(f"  Salt in key: {key_salt.hex()}")
    print()

# ========= Step 2: 读取所有 DB 的 salt =========
print("=" * 60)
print("Step 2: Read all DB salts")
print("=" * 60)

db_salts = {}  # {db_name: salt_bytes}
for root, dirs, files in os.walk(DB_BASE):
    for f in files:
        if f.endswith('.db'):
            db_path = os.path.join(root, f)
            try:
                with open(db_path, "rb") as fp:
                    salt = fp.read(16)
                db_salts[f] = (salt, db_path)
                print(f"  {f}: salt={salt.hex()}")
            except:
                pass

# ========= Step 3: 交叉匹配 =========
print("\n" + "=" * 60)
print("Step 3: Cross-match keys to DBs")
print("=" * 60)

matches = {}
for ascii_key, (enc_key, key_salt) in key_data.items():
    for db_name, (db_salt, db_path) in db_salts.items():
        if key_salt == db_salt:
            print(f"  MATCH: key salt {key_salt.hex()} == {db_name} salt")
            matches[db_name] = (enc_key, db_path)

print(f"\nMatched {len(matches)} DBs")

# ========= Step 4: 尝试解密 =========
print("\n" + "=" * 60)
print("Step 4: Decrypt matched DBs")
print("=" * 60)

def try_decrypt_sqlcipher(enc_key, db_path, out_path):
    """尝试各种 SQLCipher 参数解密"""
    with open(db_path, "rb") as f:
        file_data = f.read()

    page1 = file_data[:4096]
    salt = page1[:16]

    configs = [
        # kdf_iter, hash_algo, reserve_size, hmac_size
        (256000, "sha512", 80, 64),   # SQLCipher 4
        (64000, "sha1", 48, 20),       # SQLCipher 3
        (4000, "sha1", 48, 20),        # WCDB possible
        (256000, "sha256", 64, 32),    # SQLCipher 4 with SHA256
        (64000, "sha256", 64, 32),     # Custom
        (1, "sha1", 48, 20),           # raw key iter=1
        (1, "sha512", 80, 64),         # raw key iter=1 sha512
        (0, "sha1", 48, 20),           # direct
        (0, "sha512", 80, 64),         # direct sha512
        # 没有 HMAC 的模式
        (256000, "sha512", 0, 0),      # no reserve
        (256000, "sha512", 16, 0),     # IV only
        (64000, "sha1", 0, 0),
        (64000, "sha1", 16, 0),
        (1, "sha1", 0, 0),
        (1, "sha1", 16, 0),
        (0, "sha1", 0, 0),
        (0, "sha1", 16, 0),
    ]

    for kdf_iter, hash_algo, reserve_size, hmac_size in configs:
        try:
            if kdf_iter == 0:
                derived = enc_key
            else:
                derived = hashlib.pbkdf2_hmac(hash_algo, enc_key, salt, kdf_iter, KEY_SIZE)

            # 尝试解密第一页
            data_end = 4096 - reserve_size
            encrypted = page1[16:data_end]  # 跳过 salt

            if reserve_size >= 16:
                iv = page1[data_end:data_end + 16]
            elif reserve_size == 0:
                iv = b'\x00' * 16  # 或者用 salt
            else:
                continue

            if len(encrypted) % 16 != 0:
                continue

            cipher = AES.new(derived, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted)

            # 检查解密结果是否合理
            # SQLite 第一页 offset 16 开始的结构:
            # offset 0-1: page_size (big-endian uint16)
            # offset 2: file format write version (1 or 2)
            # offset 3: file format read version (1 or 2)
            # offset 4: reserved space at end of each page

            if len(decrypted) >= 5:
                page_size = struct.unpack('>H', decrypted[0:2])[0]
                write_ver = decrypted[2]
                read_ver = decrypted[3]
                reserved = decrypted[4]

                if page_size in [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 1]:
                    if write_ver in [1, 2] and read_ver in [1, 2]:
                        actual_ps = 65536 if page_size == 1 else page_size
                        print(f"\n  Possible match! kdf={kdf_iter}, algo={hash_algo}, reserve={reserve_size}")
                        print(f"  page_size={actual_ps}, write_ver={write_ver}, read_ver={read_ver}, reserved={reserved}")
                        print(f"  First 32 decrypted bytes: {decrypted[:32].hex()}")

                        # 写出完整文件
                        os.makedirs(os.path.dirname(out_path), exist_ok=True)
                        with open(out_path, "wb") as out:
                            out.write(SQLITE_FILE_HEADER)
                            out.write(decrypted)
                            if reserve_size > 0:
                                out.write(page1[data_end:])

                            for pn in range(1, len(file_data) // 4096):
                                page = file_data[pn * 4096:(pn + 1) * 4096]
                                enc = page[:4096 - reserve_size]
                                piv = page[4096 - reserve_size:4096 - reserve_size + 16] if reserve_size >= 16 else b'\x00' * 16
                                c = AES.new(derived, AES.MODE_CBC, piv)
                                dec = c.decrypt(enc)
                                out.write(dec)
                                if reserve_size > 0:
                                    out.write(page[4096 - reserve_size:])

                        try:
                            conn = sqlite3.connect(out_path)
                            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                            table_names = [t[0] for t in tables]
                            print(f"  >>> SUCCESS! Tables: {table_names}")
                            for t in table_names[:5]:
                                count = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
                                print(f"      {t}: {count} rows")
                            conn.close()
                            return True
                        except Exception as e:
                            print(f"  Verify failed: {e}")
        except Exception:
            pass

    return False

os.makedirs(OUT_DIR, exist_ok=True)

for db_name, (enc_key, db_path) in matches.items():
    print(f"\n--- Decrypting {db_name} ---")
    print(f"  Key: {enc_key.hex()}")
    out_path = os.path.join(OUT_DIR, f"de_{db_name}")

    if try_decrypt_sqlcipher(enc_key, db_path, out_path):
        print(f"  {db_name}: DECRYPTED!")
    else:
        print(f"  {db_name}: No match found")

# 如果没有匹配的 key，也尝试所有 key 对所有 DB
if not matches:
    print("\nNo direct salt matches. Trying all keys against all DBs...")
    for ascii_key, (enc_key, key_salt) in key_data.items():
        for db_name, (db_salt, db_path) in list(db_salts.items())[:3]:
            out_path = os.path.join(OUT_DIR, f"de_{db_name}")
            print(f"\n  Trying key {enc_key.hex()[:16]}... on {db_name}")
            if try_decrypt_sqlcipher(enc_key, db_path, out_path):
                print(f"  MATCHED!")

pm.close_process()
print("\nDone.")
