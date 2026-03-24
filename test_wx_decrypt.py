"""解析 key_info_data protobuf 并尝试解密微信 4.x 数据库"""
import os
import sqlite3
import struct
import hashlib

# ======= Part 1: 用 blackboxprotobuf 解析 key_info_data =======
print("=" * 60)
print("Part 1: Protobuf 解析 key_info_data")
print("=" * 60)

key_db_path = r"C:\Users\v_wenloliu\Documents\xwechat_files\all_users\login\wxid_6ofkq4h94pxe22\key_info.db"
conn = sqlite3.connect(f"file:{key_db_path}?mode=ro", uri=True)
cursor = conn.cursor()
cursor.execute("SELECT key_info_data FROM LoginKeyInfoTable LIMIT 1")
raw_data = cursor.fetchone()[0]
conn.close()

print(f"raw_data length: {len(raw_data)}")
print(f"raw_data hex: {raw_data.hex()}")

try:
    import blackboxprotobuf
    message, typedef = blackboxprotobuf.decode_message(raw_data)
    print(f"\nProtobuf 解析结果:")
    print(f"  message: {message}")
    print(f"  typedef: {typedef}")
except Exception as e:
    print(f"blackboxprotobuf 解析失败: {e}")

# 手动解析 protobuf wire format
print("\n--- 手动解析 protobuf wire format ---")
pos = 0
data = raw_data
while pos < len(data):
    if pos >= len(data):
        break
    byte = data[pos]
    field_number = byte >> 3
    wire_type = byte & 0x07
    pos += 1

    # 处理 varint field_number > 15
    if byte & 0x80:
        next_byte = data[pos]
        field_number = ((next_byte & 0x7F) << 4) | ((byte >> 3) & 0x0F)
        pos += 1

    if wire_type == 0:  # Varint
        val = 0
        shift = 0
        while pos < len(data):
            b = data[pos]
            val |= (b & 0x7F) << shift
            pos += 1
            shift += 7
            if not (b & 0x80):
                break
        print(f"  field={field_number} wire=varint value={val}")
    elif wire_type == 2:  # Length-delimited
        length = 0
        shift = 0
        while pos < len(data):
            b = data[pos]
            length |= (b & 0x7F) << shift
            pos += 1
            shift += 7
            if not (b & 0x80):
                break
        payload = data[pos:pos+length]
        pos += length
        print(f"  field={field_number} wire=bytes length={length}")
        print(f"    hex: {payload.hex()}")
        if length <= 64:
            try:
                print(f"    str: {payload.decode('utf-8', errors='replace')}")
            except:
                pass
    elif wire_type == 5:  # 32-bit
        val = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        print(f"  field={field_number} wire=fixed32 value={val}")
    elif wire_type == 1:  # 64-bit
        val = struct.unpack('<Q', data[pos:pos+8])[0]
        pos += 8
        print(f"  field={field_number} wire=fixed64 value={val}")
    else:
        print(f"  field={field_number} wire={wire_type} UNKNOWN - stopping")
        break

# ======= Part 2: 尝试 PyWxDump 的解密函数 =======
print("\n" + "=" * 60)
print("Part 2: 搜索 PyWxDump 中的解密逻辑")
print("=" * 60)

# 查看 pywxdump 的解密相关模块
import pywxdump
pywxdump_path = os.path.dirname(pywxdump.__file__)
print(f"pywxdump 安装路径: {pywxdump_path}")

# 查找所有 decrypt 相关文件
for root, dirs, files in os.walk(pywxdump_path):
    for f in files:
        if "decrypt" in f.lower() or "cipher" in f.lower() or "crypto" in f.lower():
            full = os.path.join(root, f)
            print(f"  找到: {full}")

# ======= Part 3: 尝试 SQLCipher 解密 =======
print("\n" + "=" * 60)
print("Part 3: 尝试 SQLCipher 解密")
print("=" * 60)

# 从 key_info_data 中提取各种可能的 key
# 格式分析: 0aa80100 开头看起来像 protobuf 的 field 1, length-delimited
# 0a = field 1, wire type 2 (length-delimited)
# a80100 = varint 168 (0x80 0x01 表示 128+40=168...  let me recalculate)
# a8 01 = 0b_0101000 0b_0000001 = 168... wait
# Let me check: 0xa8 = 1010_1000, low 7 bits = 0010_1000 = 40, has more
# 0x01 = 0000_0001, low 7 bits = 0000_001 = 1
# value = 40 | (1 << 7) = 40 + 128 = 168
# So field 1 has a 168-byte sub-message

# 尝试从 protobuf 中找到可能的 32 字节 key
# 看 bytes 从 offset 12 开始的不同部分
key_candidates = []

# 从 blackboxprotobuf 解析中查找
try:
    import blackboxprotobuf

    # 获取所有记录的 key_info_data
    conn = sqlite3.connect(f"file:{key_db_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT key_info_md5, key_info_data FROM LoginKeyInfoTable")
    all_records = cursor.fetchall()
    conn.close()

    for key_info_md5, kid in all_records:
        msg, td = blackboxprotobuf.decode_message(kid)
        # 从解析结果中提取所有 bytes 类型的字段
        def extract_bytes_fields(obj, prefix=""):
            results = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    extract_bytes_fields(v, f"{prefix}.{k}")
            elif isinstance(obj, bytes):
                results.append((prefix, obj))
                if len(obj) == 32:
                    print(f"  发现32字节字段 {prefix}: {obj.hex()}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_bytes_fields(item, f"{prefix}[{i}]")
            return results

        print(f"\n--- key_info_md5: {key_info_md5} ---")
        print(f"  Protobuf 结构: {msg}")
        extract_bytes_fields(msg)
        break  # 只看第一条

except Exception as e:
    print(f"解析失败: {e}")
    import traceback
    traceback.print_exc()
