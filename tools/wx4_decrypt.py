"""
微信 4.x 数据库解密工具
======================
用于从 Weixin.exe 进程内存中提取数据库密钥并解密所有聊天数据库。

原理:
  微信 4.x (Windows) 使用 WCDB (基于 SQLCipher 4.1.0) 加密所有数据库。
  加密参数:
    - kdf_iter = 0 (raw key 模式, 不做 PBKDF2 派生)
    - hmac_algorithm = SHA512
    - reserve_size = 80 (16 bytes IV + 64 bytes HMAC-SHA512)
    - page_size = 4096
  密钥格式:
    - 内存中以 PRAGMA key = x'[64 hex enc_key][32 hex salt]' 形式存储
    - 96 个 hex 字符 = 48 字节: 前 32 字节是 AES-256 key, 后 16 字节是 salt
    - salt 与数据库文件的前 16 字节匹配

使用方法（CLI）:
  1. 确保微信已启动并登录
  2. 运行此脚本: python wx4_decrypt.py
  3. 解密后的数据库存放在 decrypted 子目录中

使用方法（库导入）:
  from tools.wx4_decrypt import decrypt_all_databases, load_cached_keys, save_key_cache

依赖:
  pip install pymem pycryptodomex

作者: Natsume_Ai project
日期: 2026-03-24
"""
from __future__ import annotations

import json
import os
import sqlite3
import logging
from pathlib import Path
from Cryptodome.Cipher import AES

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ===== 常量 =====
SQLITE_FILE_HEADER = b"SQLite format 3\x00"
RESERVE_SIZE = 80  # 16 IV + 64 SHA512 HMAC
PAGE_SIZE = 4096

# 微信数据目录 (默认路径)
DEFAULT_WECHAT_DATA_DIR = os.path.expanduser(r"~\Documents\xwechat_files")


# ===== 密钥缓存（避免每次重启都扫描进程内存）=====

def load_cached_keys(cache_path: str | Path) -> dict[str, str]:
    """从缓存文件加载密钥。

    缓存格式：JSON，{salt_hex: enc_key_hex}

    Args:
        cache_path: 缓存文件路径

    Returns:
        {salt_hex: enc_key_bytes} 字典，文件不存在或解析失败时返回空字典
    """
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            raw: dict[str, str] = json.load(f)
        # 将 hex 字符串还原为 bytes
        return {salt_hex: bytes.fromhex(key_hex) for salt_hex, key_hex in raw.items()}
    except Exception as exc:
        logger.warning("密钥缓存加载失败: %s", exc)
        return {}


def save_key_cache(keys: dict[str, bytes], cache_path: str | Path) -> bool:
    """将密钥保存到缓存文件。

    Args:
        keys: {salt_hex: enc_key_bytes} 字典
        cache_path: 缓存文件路径

    Returns:
        True 表示保存成功
    """
    cache_path = Path(cache_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # 将 bytes 转为 hex 字符串存储
        raw = {salt_hex: key_bytes.hex() for salt_hex, key_bytes in keys.items()}
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        logger.info("密钥缓存已保存: %s (%d 个密钥)", cache_path, len(keys))
        return True
    except Exception as exc:
        logger.warning("密钥缓存保存失败: %s", exc)
        return False



    """查找微信数据目录"""
    base = DEFAULT_WECHAT_DATA_DIR
    if not os.path.exists(base):
        raise FileNotFoundError(f"微信数据目录不存在: {base}")

    # 查找 wxid_xxx 目录
    for entry in os.listdir(base):
        full = os.path.join(base, entry)
        if os.path.isdir(full) and "wxid_" in entry:
            db_storage = os.path.join(full, "db_storage")
            if os.path.exists(db_storage):
                return db_storage

    raise FileNotFoundError(f"未找到微信 db_storage 目录")


def extract_keys_from_memory():
    """从 Weixin.exe 进程内存中提取所有数据库密钥

    Returns:
        dict: {salt_hex: enc_key_bytes} 映射
    """
    import pymem
    import pymem.pattern

    logger.info("正在打开 Weixin.exe 进程...")
    try:
        pm = pymem.Pymem("Weixin.exe")
    except pymem.exception.ProcessNotFound:
        raise RuntimeError("未找到 Weixin.exe 进程，请确保微信已启动并登录")

    logger.info(f"已附加到 PID: {pm.process_id}")

    # 搜索 x' 模式 (PRAGMA key 的 hex 值前缀)
    logger.info("正在搜索进程内存中的密钥...")
    x_prime_addrs = pymem.pattern.pattern_scan_all(
        pm.process_handle, b"x'", return_multiple=True
    )
    logger.info(f"找到 {len(x_prime_addrs)} 个 x' 模式")

    keys = {}  # {salt_hex: enc_key_bytes}

    for addr in x_prime_addrs:
        try:
            raw = pm.read_bytes(addr + 2, 200)
            end = raw.find(b"'")
            if end <= 0:
                continue

            content = raw[:end]

            # 验证: 必须是 96 个 ASCII hex 字符
            if len(content) != 96:
                continue

            all_hex = all(
                (48 <= b <= 57) or (65 <= b <= 70) or (97 <= b <= 102)
                for b in content
            )
            if not all_hex:
                continue

            # 解码: 96 hex chars -> 48 bytes -> 32 key + 16 salt
            hex_text = content.decode('ascii')
            raw_48 = bytes.fromhex(hex_text)
            enc_key = raw_48[:32]
            salt = raw_48[32:]
            salt_hex = salt.hex()

            if salt_hex not in keys:
                keys[salt_hex] = enc_key

        except Exception:
            pass

    pm.close_process()
    logger.info(f"提取到 {len(keys)} 个唯一密钥")
    return keys


def decrypt_database(enc_key: bytes, db_path: str, out_path: str) -> bool:
    """解密单个 SQLCipher 4 数据库 (raw key 模式)

    Args:
        enc_key: 32 字节 AES-256 加密密钥
        db_path: 加密数据库文件路径
        out_path: 解密输出路径

    Returns:
        bool: 解密是否成功
    """
    with open(db_path, "rb") as f:
        file_data = f.read()

    if len(file_data) < PAGE_SIZE:
        logger.warning(f"文件太小: {db_path}")
        return False

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "wb") as out:
        out.write(SQLITE_FILE_HEADER)

        total_pages = len(file_data) // PAGE_SIZE
        for page_num in range(total_pages):
            offset = page_num * PAGE_SIZE
            page = file_data[offset:offset + PAGE_SIZE]

            data_size = PAGE_SIZE - RESERVE_SIZE
            reserve = page[-RESERVE_SIZE:]
            iv = reserve[:16]

            if page_num == 0:
                # 第一页: 前 16 字节是 salt, 不参与加密内容
                encrypted = page[16:data_size]
            else:
                encrypted = page[:data_size]

            cipher = AES.new(enc_key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted)

            out.write(decrypted)
            out.write(reserve)

    # 验证解密结果
    try:
        conn = sqlite3.connect(out_path)
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        conn.close()
        return True
    except Exception:
        # 清理失败的输出文件
        os.remove(out_path)
        return False


def decrypt_all_databases(db_storage_dir: str, out_dir: str, *, key_cache_path: str | Path | None = None) -> dict:
    """解密所有微信数据库

    Args:
        db_storage_dir: db_storage 目录路径
        out_dir: 解密输出目录
        key_cache_path: 密钥缓存文件路径（None 表示不使用缓存）
                        微信不重新登录密钥不变，首次提取后缓存可避免重复扫描进程内存

    Returns:
        dict: {db_name: {"status": "ok"/"fail", "tables": [...], "path": out_path}}
    """
    # Step 1: 优先从缓存加载密钥，缓存不存在时从内存提取
    keys: dict[str, bytes] = {}
    if key_cache_path is not None:
        keys = load_cached_keys(key_cache_path)
        if keys:
            logger.info("从缓存加载到 %d 个密钥: %s", len(keys), key_cache_path)

    if not keys:
        logger.info("缓存为空或未配置，从进程内存提取密钥...")
        keys = extract_keys_from_memory()
        # 提取成功后写入缓存
        if keys and key_cache_path is not None:
            save_key_cache(keys, key_cache_path)

    # Step 2: 收集所有数据库文件及其 salt
    db_files = {}
    for root, _, files in os.walk(db_storage_dir):
        for f in files:
            if f.endswith('.db'):
                path = os.path.join(root, f)
                try:
                    with open(path, "rb") as fp:
                        salt = fp.read(16)
                    db_files[f] = {"path": path, "salt": salt}
                except Exception:
                    pass

    logger.info(f"发现 {len(db_files)} 个数据库文件")

    # Step 3: 匹配密钥并解密
    results = {}
    os.makedirs(out_dir, exist_ok=True)

    for db_name, db_info in db_files.items():
        salt_hex = db_info["salt"].hex()

        if salt_hex not in keys:
            logger.warning(f"  {db_name}: 未找到匹配的密钥 (salt={salt_hex})")
            results[db_name] = {"status": "no_key"}
            continue

        enc_key = keys[salt_hex]
        out_path = os.path.join(out_dir, f"de_{db_name}")

        logger.info(f"  正在解密 {db_name}...")
        if decrypt_database(enc_key, db_info["path"], out_path):
            # 获取表信息
            try:
                conn = sqlite3.connect(out_path)
                tables = [t[0] for t in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]
                total_rows = 0
                for t in tables:
                    try:
                        count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                        total_rows += count
                    except:
                        pass
                conn.close()

                logger.info(f"  ✓ {db_name}: {len(tables)} 个表, {total_rows} 行数据")
                results[db_name] = {
                    "status": "ok",
                    "tables": tables,
                    "total_rows": total_rows,
                    "path": out_path,
                }
            except Exception as e:
                logger.info(f"  ✓ {db_name}: 已解密 (FTS 表需要自定义 tokenizer)")
                results[db_name] = {
                    "status": "ok_partial",
                    "path": out_path,
                    "note": str(e),
                }
        else:
            logger.error(f"  ✗ {db_name}: 解密失败")
            results[db_name] = {"status": "fail"}

    return results


def main():
    """主函数（CLI 入口）"""
    import argparse
    parser = argparse.ArgumentParser(description="微信 4.x 数据库解密工具")
    parser.add_argument(
        "--db-storage", default=None,
        help="微信 db_storage 目录路径（默认自动查找）",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="解密输出目录（默认为 db_storage 同级的 decrypted 目录）",
    )
    parser.add_argument(
        "--cache", default=None,
        help="密钥缓存文件路径（微信不重新登录密钥不变，缓存后无需重复扫描进程内存）",
    )
    args = parser.parse_args()

    try:
        # 查找数据目录
        db_storage = args.db_storage or find_wechat_data_dir()
        logger.info(f"数据目录: {db_storage}")

        # 输出目录
        out_dir = args.out_dir or os.path.join(os.path.dirname(db_storage), "decrypted")

        # 解密
        results = decrypt_all_databases(db_storage, out_dir, key_cache_path=args.cache)

        # 统计
        ok = sum(1 for r in results.values() if r["status"] in ("ok", "ok_partial"))
        fail = sum(1 for r in results.values() if r["status"] == "fail")
        no_key = sum(1 for r in results.values() if r["status"] == "no_key")

        logger.info(f"\n解密完成: 成功 {ok}, 失败 {fail}, 无密钥 {no_key}")
        logger.info(f"输出目录: {out_dir}")

        return results

    except Exception as e:
        logger.error(f"错误: {e}")
        raise


if __name__ == "__main__":
    main()
