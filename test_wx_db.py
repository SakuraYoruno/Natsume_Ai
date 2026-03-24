"""微信 4.x 数据库验证脚本 - 检测是否加密"""
import os
import sqlite3

DB_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\wxid_6ofkq4h94pxe22_e4a7\db_storage"
LOGIN_BASE = r"C:\Users\v_wenloliu\Documents\xwechat_files\all_users\login\wxid_6ofkq4h94pxe22"

db_files = {
    "message_0.db": os.path.join(DB_BASE, "message", "message_0.db"),
    "contact.db": os.path.join(DB_BASE, "contact", "contact.db"),
    "session.db": os.path.join(DB_BASE, "session", "session.db"),
    "head_image.db": os.path.join(DB_BASE, "head_image", "head_image.db"),
    "emoticon.db": os.path.join(DB_BASE, "emoticon", "emoticon.db"),
    "favorite.db": os.path.join(DB_BASE, "favorite", "favorite.db"),
    "sns.db": os.path.join(DB_BASE, "sns", "sns.db"),
    "general.db": os.path.join(DB_BASE, "general", "general.db"),
    "key_info.db": os.path.join(LOGIN_BASE, "key_info.db"),
}

for name, path in db_files.items():
    print(f"\n{'='*50}")
    print(f"=== {name} ===")

    if not os.path.exists(path):
        print(f"  文件不存在: {path}")
        continue

    size = os.path.getsize(path)
    print(f"  大小: {size} bytes")

    with open(path, "rb") as f:
        header = f.read(32)

    print(f"  Header hex: {header[:16].hex()}")
    print(f"  Header ascii: {repr(header[:16])}")

    is_sqlite = header[:6] == b"SQLite"
    print(f"  是否明文 SQLite: {is_sqlite}")

    if is_sqlite:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # 获取所有表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"  表列表: {tables}")

            # 每个表查看前2条记录的列名
            for table in tables[:5]:  # 最多看5个表
                try:
                    cursor.execute(f"PRAGMA table_info('{table}')")
                    cols = [(row[1], row[2]) for row in cursor.fetchall()]
                    print(f"  表 [{table}] 列: {cols}")

                    cursor.execute(f"SELECT COUNT(*) FROM '{table}'")
                    count = cursor.fetchone()[0]
                    print(f"  表 [{table}] 行数: {count}")

                    if count > 0:
                        cursor.execute(f"SELECT * FROM '{table}' LIMIT 1")
                        row = cursor.fetchone()
                        # 显示每列的值（截断长内容）
                        col_names = [desc[0] for desc in cursor.description]
                        for cn, val in zip(col_names, row):
                            if isinstance(val, bytes):
                                print(f"    {cn}: <bytes len={len(val)}>")
                            elif isinstance(val, str) and len(val) > 100:
                                print(f"    {cn}: {val[:100]}...")
                            else:
                                print(f"    {cn}: {val}")
                except Exception as e:
                    print(f"  表 [{table}] 读取错误: {e}")

            conn.close()
        except Exception as e:
            print(f"  打开错误: {e}")
    else:
        # 检查是否是 SQLCipher 加密
        if header[:4] == b"\x00\x00\x00\x00":
            print("  可能是 SQLCipher 加密数据库")
        else:
            print(f"  未知格式, 前4字节: {header[:4].hex()}")
