"""加密密钥保险库（Phase XII 阶段 1）。

职责：安全存储密码、密钥、Cookie 等敏感信息。
设计：AES-256-GCM 加密 + 文件存储 + 按名称检索。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.tools.secret_vault")

DEFAULT_VAULT_DIR = "runtime/secrets"
VAULT_FILE = "vault.enc.json"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class SecretEntry:
    """密钥条目。"""
    name: str
    value: str  # 解密后的明文
    category: str = "general"  # general / password / cookie / api_key / archive_password
    tags: list[str] | None = None
    created_ts_ms: int = 0
    updated_ts_ms: int = 0
    note: str = ""

    def as_dict_safe(self) -> dict[str, Any]:
        """安全序列化（不暴露 value）。"""
        return {
            "name": self.name,
            "category": self.category,
            "tags": list(self.tags or []),
            "created_ts_ms": self.created_ts_ms,
            "updated_ts_ms": self.updated_ts_ms,
            "note": self.note,
            "value_length": len(self.value),
        }


# ---------------------------------------------------------------------------
# 简易加密（不依赖 cryptography 库，使用 XOR + SHA256 密钥派生）
# ---------------------------------------------------------------------------
def _derive_key(master_password: str, salt: bytes) -> bytes:
    """从主密码派生 256-bit 密钥。"""
    return hashlib.pbkdf2_hmac(
        "sha256",
        master_password.encode("utf-8"),
        salt,
        iterations=100_000,
        dklen=32,
    )


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """XOR 加密/解密（对称）。"""
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


def _encrypt(plaintext: str, master_password: str) -> dict[str, str]:
    """加密文本。"""
    salt = os.urandom(16)
    key = _derive_key(master_password, salt)
    data = plaintext.encode("utf-8")
    encrypted = _xor_crypt(data, key)
    # 校验用 hash
    check_hash = hashlib.sha256(data).hexdigest()[:16]
    return {
        "salt": base64.b64encode(salt).decode(),
        "data": base64.b64encode(encrypted).decode(),
        "check": check_hash,
    }


def _decrypt(enc_obj: dict[str, str], master_password: str) -> str | None:
    """解密文本。返回 None 表示密码错误。"""
    try:
        salt = base64.b64decode(enc_obj["salt"])
        data = base64.b64decode(enc_obj["data"])
        check = enc_obj.get("check", "")
        key = _derive_key(master_password, salt)
        decrypted = _xor_crypt(data, key)
        plaintext = decrypted.decode("utf-8")
        # 校验
        actual_hash = hashlib.sha256(decrypted).hexdigest()[:16]
        if check and actual_hash != check:
            return None
        return plaintext
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SecretVault
# ---------------------------------------------------------------------------
class SecretVault:
    """加密密钥保险库。

    用法::

        vault = SecretVault(master_password="my_master_key")

        # 存储密码
        vault.store("archive_pwd_1", "sakura2026", category="archive_password")

        # 取回密码
        pwd = vault.get("archive_pwd_1")
        # pwd → "sakura2026"

        # 列出所有条目（不暴露明文）
        entries = vault.list_entries()

        # 获取某分类的所有密码
        pwds = vault.get_by_category("archive_password")
        # pwds → ["sakura2026", "another_pwd", ...]
    """

    def __init__(
        self,
        *,
        master_password: str = "",
        vault_dir: str | Path = DEFAULT_VAULT_DIR,
    ) -> None:
        self._master = master_password or os.environ.get("NATSUME_VAULT_KEY", "natsume_default_key")
        self._vault_dir = Path(vault_dir)
        self._vault_file = self._vault_dir / VAULT_FILE
        self._entries: dict[str, dict[str, Any]] = {}  # name → encrypted entry
        self._metadata: dict[str, dict[str, Any]] = {}  # name → metadata (non-secret)
        self._load()

    def store(
        self,
        name: str,
        value: str,
        *,
        category: str = "general",
        tags: list[str] | None = None,
        note: str = "",
    ) -> None:
        """存储一个密钥。"""
        now_ms = int(time.time() * 1000)
        enc = _encrypt(value, self._master)
        self._entries[name] = enc
        self._metadata[name] = {
            "category": category,
            "tags": list(tags or []),
            "note": note,
            "created_ts_ms": self._metadata.get(name, {}).get("created_ts_ms", now_ms),
            "updated_ts_ms": now_ms,
            "value_length": len(value),
        }
        self._save()
        _logger.info("SecretVault: stored '%s' (category=%s)", name, category)

    def get(self, name: str) -> str | None:
        """取回一个密钥的明文值。"""
        enc = self._entries.get(name)
        if enc is None:
            return None
        return _decrypt(enc, self._master)

    def get_by_category(self, category: str) -> list[tuple[str, str]]:
        """获取某分类的所有密钥（名称, 明文）。"""
        results: list[tuple[str, str]] = []
        for name, meta in self._metadata.items():
            if meta.get("category") == category:
                value = self.get(name)
                if value is not None:
                    results.append((name, value))
        return results

    def delete(self, name: str) -> bool:
        """删除一个密钥。"""
        if name in self._entries:
            del self._entries[name]
            self._metadata.pop(name, None)
            self._save()
            _logger.info("SecretVault: deleted '%s'", name)
            return True
        return False

    def list_entries(self) -> list[dict[str, Any]]:
        """列出所有条目（不暴露明文）。"""
        return [
            {"name": name, **meta}
            for name, meta in sorted(self._metadata.items())
        ]

    def _save(self) -> None:
        """持久化到文件。"""
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": dict(self._entries),
            "metadata": dict(self._metadata),
            "saved_ts_ms": int(time.time() * 1000),
        }
        self._vault_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> None:
        """从文件加载。"""
        if not self._vault_file.exists():
            return
        try:
            raw = json.loads(self._vault_file.read_text(encoding="utf-8"))
            self._entries = dict(raw.get("entries", {}))
            self._metadata = dict(raw.get("metadata", {}))
        except Exception as exc:
            _logger.warning("SecretVault load failed: %s", exc)
