"""尝试从 key_info_data 中提取密钥并解密微信 4.x 数据库"""
import os
import sqlite3
import hmac
import hashlib
from Cryptodome.Cipher import AES

SQLITE_FILE_HEADER = b"SQLite format 3\x00"
KEY_SIZE = 32
DEFAULT_PAGESIZE = 4096

def try_decrypt_with_key(key_hex, db_path, out_path):
    """使用 PyWxDump 的解密逻辑尝试解密"""
    password = bytes.fromhex(key_hex)

    with open(db_path, "rb") as f:
        blist = f.read()

    salt = blist[:16]
    mac_salt = bytes([(salt[i] ^ 58) for i in range(16)])
    byteHmac = hashlib.pbkdf2_hmac("sha1", password, salt, 64000, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac("sha1", byteHmac, mac_salt, 2, KEY_SIZE)

    hash_mac = hmac.new(mac_key, blist[16:4064], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')

    first_page_hmac = blist[4064:4084]  # 20 bytes HMAC
    calculated_hmac = hash_mac.digest()

    if calculated_hmac == first_page_hmac:
        print(f"  [OK] HMAC pass! Key correct!")
        # 执行解密
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as deFile:
            deFile.write(SQLITE_FILE_HEADER)
            for i in range(0, len(blist), 4096):
                tblist = blist[i:i + 4096] if i > 0 else blist[16:i + 4096]
                deFile.write(AES.new(byteHmac, AES.MODE_CBC, tblist[-48:-32]).decrypt(tblist[:-48]))
                deFile.write(tblist[-48:])
        return True
    else:
        return False

# ====== 从 key_info_data 中提取各种可能的 key 并尝试 ======
key_db_path = r"C:\Users\v_wenloliu\Documents\xwechat_files\all_users\login\wxid_6ofkq4h94pxe22\key_info.db"
conn = sqlite3.connect(f"file:{key_db_path}?mode=ro", uri=True)
cursor = conn.cursor()
cursor.execute("SELECT key_info_md5, key_info_data FROM LoginKeyInfoTable")
all_records = cursor.fetchall()
conn.close()

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"

# 测试数据库
test_dbs = [
    ("message_0.db", os.path.join(DB_BASE, "message", "message_0.db")),
    ("contact.db", os.path.join(DB_BASE, "contact", "contact.db")),
    ("session.db", os.path.join(DB_BASE, "session", "session.db")),
]

# 分析 key_info_data 的结构
# 所有记录的 field 1 (168 字节) 的公共前缀
print("分析 key_info_data 结构...")
blobs = []
for md5, data in all_records:
    # field 1 starts at offset 3 (0a a801 = field 1, length 168)
    # payload starts at offset 3
    blob = data[3:3+168]
    blobs.append(blob)

# 查看公共字节
print(f"\n记录数: {len(blobs)}")
print(f"每条 blob 长度: {len(blobs[0])}")

# 找出每条记录中哪些字节不同
common = bytearray(blobs[0])
diff_positions = set()
for b in blobs[1:]:
    for i in range(len(b)):
        if b[i] != common[i]:
            diff_positions.add(i)

print(f"\n不同位置 ({len(diff_positions)} 个): {sorted(diff_positions)}")
print(f"相同位置 ({168 - len(diff_positions)} 个)")

# 显示结构
print("\n第一条记录的 blob 结构分析:")
blob = blobs[0]
print(f"  [0:1]   = {blob[0:1].hex()} (标志?)")
print(f"  [1:5]   = {blob[1:5].hex()} (固定: 0bb51739)")
print(f"  [5:9]   = {blob[5:9].hex()} (版本?)")
print(f"  [9:13]  = {blob[9:13].hex()} (固定)")
print(f"  [13:29] = {blob[13:29].hex()} (16字节 - 变化部分, 可能是key前半)")
print(f"  [29:31] = {blob[29:31].hex()} (固定: 0000)")
print(f"  [31:33] = {blob[31:33].hex()} (006e)")
print(f"  [33:34] = {blob[33:34].hex()} (b0)")
print(f"  [34:36] = {blob[34:36].hex()}")
print(f"  [36:68] = {blob[36:68].hex()} (32字节 - 不变部分!)")
print(f"  [68:100]= {blob[68:100].hex()} (32字节 - 变化部分)")
print(f"  [100:132]={blob[100:132].hex()} (32字节 - 变化部分)")
print(f"  [132:164]={blob[132:164].hex()} (32字节 - 变化部分)")
print(f"  [164:168]={blob[164:168].hex()} (尾部)")

# 标记哪些位置是变化的
print("\n变化位置标记 (. = 相同, X = 不同):")
marks = ""
for i in range(168):
    if i in diff_positions:
        marks += "X"
    else:
        marks += "."
# 每 32 字符一行
for i in range(0, len(marks), 32):
    print(f"  [{i:3d}-{min(i+31, len(marks)-1):3d}]: {marks[i:i+32]}")
    print(f"           {blob[i:i+32].hex()}")

# 提取可能的 key 候选
print("\n\n" + "=" * 60)
print("尝试各种 key 候选解密...")
print("=" * 60)

# 策略: 每条记录中变化的部分可能就是每个 db 对应的 key
# 不变部分可能是公共 key 或账户信息
msg_db_path = test_dbs[0][1]  # message_0.db

key_candidates = []

for idx, blob in enumerate(blobs):
    # 尝试 blob 中不同的 32 字节窗口
    for offset in [13, 36, 68, 100, 132]:
        if offset + 32 <= len(blob):
            candidate = blob[offset:offset+32]
            key_hex = candidate.hex()
            key_candidates.append((idx, offset, key_hex))

# 也尝试固定的不变部分
fixed_part = blobs[0][36:68]
key_candidates.append((-1, 36, fixed_part.hex()))

# 去重
seen = set()
unique_candidates = []
for idx, offset, key_hex in key_candidates:
    if key_hex not in seen:
        seen.add(key_hex)
        unique_candidates.append((idx, offset, key_hex))

print(f"共 {len(unique_candidates)} 个不重复的 key 候选")

for db_name, db_path in test_dbs[:1]:  # 先只测 message_0.db
    print(f"\n--- 测试: {db_name} ---")
    out_dir = r"C:\Users\v_wenloliu\Documents\xwechat_files\decrypted"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"de_{db_name}")

    found = False
    for rec_idx, offset, key_hex in unique_candidates:
        result = try_decrypt_with_key(key_hex, db_path, out_path)
        if result:
            print(f"  [SUCCESS] rec_idx={rec_idx}, offset={offset}, key={key_hex}")

            # 验证解密后的文件
            try:
                conn2 = sqlite3.connect(out_path)
                tables = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                print(f"  解密后的表: {[t[0] for t in tables]}")
                for t in tables[:3]:
                    count = conn2.execute(f"SELECT COUNT(*) FROM '{t[0]}'").fetchone()[0]
                    print(f"    表 {t[0]}: {count} 行")
                conn2.close()
            except Exception as e:
                print(f"  解密文件读取失败: {e}")
            found = True
            break

    if not found:
        print(f"  [FAIL] All key candidates failed")

# ====== 额外: 尝试直接从内存搜索 key ======
print("\n\n" + "=" * 60)
print("额外: 使用 PyWxDump 的内存搜索功能")
print("=" * 60)

try:
    from pywxdump.wx_core.wx_info import BiasAddr, get_wx_info
    from pywxdump.wx_core.utils import WX_OFFS

    # 尝试手动构造 bias 来搜索 Weixin.exe 进程
    print(f"WX_OFFS 中的版本: {list(WX_OFFS.keys())[:10]}...")
    print(f"是否有 4.x 版本: {[v for v in WX_OFFS.keys() if v.startswith('4.') or v.startswith('98.')]}")
except Exception as e:
    print(f"导入失败: {e}")
    import traceback
    traceback.print_exc()
