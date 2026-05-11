"""
验证真实运行环境配置是否正确加载
执行：python tools/verify_env.py
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(".")
ENV_FILE = ROOT / ".env"
SECRET_STORE_FILE = ROOT / "configs" / "secrets" / "secret_store.runtime.local.json"
KEY_PROFILES_FILE = ROOT / "configs" / "secrets" / "key_profiles.runtime.json"

SEP = "=" * 60


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k:
            values[k] = v
    return values


def mask(val: str) -> str:
    if not val:
        return "(空)"
    if len(val) <= 8:
        return "***"
    return val[:4] + "..." + val[-4:]


def _is_real_secret(val: str) -> bool:
    """判断密钥值是否为真实填写（非占位符、非空）"""
    if not val:
        return False
    placeholders = ("sk-<", "<replace", "neo4j:", "<")
    return not any(val.startswith(p) for p in placeholders)


print(SEP)
print("  Natsume AI 真实运行环境配置验证")
print(SEP)

# ── 1. 检查文件存在性 ──────────────────────────────────────────
print()
print("【1】配置文件存在性检查")
files = {
    ".env": ENV_FILE,
    "secret_store.runtime.local.json": SECRET_STORE_FILE,
    "key_profiles.runtime.json": KEY_PROFILES_FILE,
}
all_exist = True
for name, path in files.items():
    exists = path.exists()
    icon = "OK" if exists else "MISSING"
    print(f"  [{icon}] {name} -> {path}")
    if not exists:
        all_exist = False

if not all_exist:
    print()
    print("  [ERROR] 缺少必要配置文件，请检查上方输出")
    sys.exit(1)

# ── 2. 加载配置 ────────────────────────────────────────────────
env_values = load_env_file(ENV_FILE)
secret_store = json.loads(SECRET_STORE_FILE.read_text(encoding="utf-8-sig"))
key_profiles_data = json.loads(KEY_PROFILES_FILE.read_text(encoding="utf-8-sig"))

print()
print("【2】配置文件内容统计")
print(f"  .env 变量数: {len(env_values)}")
print(f"  secret_store 条目数: {len(secret_store)}")
print(f"  key_profiles 节点数: {len(key_profiles_data.get('profiles', []))}")
print(f"  host_node_id: {key_profiles_data.get('host_node_id')}")
print(f"  edge_node_id: {key_profiles_data.get('edge_node_id')}")
print(f"  current_epoch: {key_profiles_data.get('current_epoch')}")

# ── 3. 关键配置项检查 ──────────────────────────────────────────
print()
print("【3】关键配置项")
KEY_CHECKS = [
    ("Natsume_ENV", False),
    ("HOST_LLM_PROVIDER", False),
    ("HOST_APP_HOST", False),
    ("HOST_APP_PORT", False),
    ("RETR_EMBED_PROVIDER", False),
    ("RETR_EMBED_MODEL_ID", False),
    ("RETR_EMBED_DIM", False),
    ("CHROMA_ENABLED", False),
    ("BOND_NEO4J_ENABLED", False),
    ("OPENAI_API_KEY", True),
    ("GEMINI_API_KEY", True),
    ("DEEPSEEK_API_KEY", True),
    ("OPENAI_API_KEY_REF", False),
]
for key, is_secret in KEY_CHECKS:
    val = env_values.get(key, "")
    display = mask(val) if is_secret else (repr(val) if val else "(空)")
    print(f"  {key} = {display}")

# ── 4. 密钥存储状态 ────────────────────────────────────────────
print()
print("【4】secret_store 密钥状态")
filled_count = 0
for ref, val in secret_store.items():
    is_filled = _is_real_secret(val)
    icon = "OK" if is_filled else "EMPTY"
    display = mask(val) if is_filled else "(待填写)"
    print(f"  [{icon}] {ref} = {display}")
    if is_filled:
        filled_count += 1

print(f"\n  已填写密钥: {filled_count}/{len(secret_store)}")

# ── 5. host_brain 配置加载测试 ─────────────────────────────────
print()
print("【5】host_brain 配置加载测试")
from host_brain.app.config import load_host_config

result = load_host_config(
    env=env_values,
    key_profiles=key_profiles_data.get("profiles", []),
    secret_store=secret_store,
    node_id=key_profiles_data.get("host_node_id", "natsu-node-01"),
    current_epoch=int(key_profiles_data.get("current_epoch", 0)),
)
icon = "OK" if result.success else "FAIL"
print(f"  [{icon}] load_host_config: success={result.success}")
if result.config:
    print(f"  llm_provider: {result.config.llm_provider}")
    print(f"  key_profile_status: {result.config.key_profile_status}")
    print(f"  openai_api_key: {'已配置 ' + mask(result.config.openai_api_key) if result.config.openai_api_key else '未配置'}")
    print(f"  gemini_api_key: {'已配置 ' + mask(result.config.gemini_api_key) if result.config.gemini_api_key else '未配置'}")
    print(f"  deepseek_api_key: {'已配置 ' + mask(result.config.deepseek_api_key) if result.config.deepseek_api_key else '未配置'}")
    print(f"  cold_start_allowed: {result.config.cold_start_allowed}")
    print(f"  enable_retrieval: {result.config.enable_retrieval}")
if result.issues:
    print(f"  issues ({len(result.issues)}):")
    for issue in result.issues:
        print(f"    - [{issue.reason_code}] {issue.detail}")

# ── 6. 检索配置加载测试 ────────────────────────────────────────
print()
print("【6】表层召回配置加载测试")
from host_brain.retrieval.recall_fusion import load_recall_runtime_config

recall_result = load_recall_runtime_config(env=env_values)

icon = "OK" if recall_result.success else "FAIL"
print(f"  [{icon}] RecallFusion: success={recall_result.success}", end="")
if recall_result.success and recall_result.config:
    print(f", default_route={recall_result.config.default_route}", end="")
    print(f", routes={list(recall_result.config.routes.keys())}", end="")
if not recall_result.success:
    print(f", reason={recall_result.reason_code}", end="")
print()

# ── 7. 汇总 ───────────────────────────────────────────────────
print()
print(SEP)
print("  汇总")
print(SEP)

has_any_key = bool(
    env_values.get("OPENAI_API_KEY") or
    env_values.get("GEMINI_API_KEY") or
    env_values.get("DEEPSEEK_API_KEY") or
    any(_is_real_secret(v) for v in secret_store.values())
)

print(f"  配置文件: 全部存在")
print(f"  host 配置加载: {'成功' if result.success else '失败'}")
print(f"  API 密钥: {'已填写' if has_any_key else '待填写（请在 .env 或 secret_store.runtime.local.json 中填入）'}")
print()
if not has_any_key:
    print("  ★ 下一步：请在以下任一位置填入真实 API 密钥：")
    print("    方式 A（推荐）：编辑 .env，在 OPENAI_API_KEY= 后填入密钥")
    print("    方式 B：编辑 configs/secrets/secret_store.runtime.local.json，")
    print("            在 sec://openai/natsu-node-01 对应的值中填入密钥")
    print()
    print("  填写完成后，重新运行：")
    print("    python tools/verify_env.py")
    print("    python -m host_brain")
else:
    print("  ★ 密钥已配置，可以运行：")
    print("    python -m host_brain")
print(SEP)
