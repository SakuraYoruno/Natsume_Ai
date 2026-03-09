"""直接调用 chat_handler 内部逻辑，捕获详细异常"""
import sys
import io
import os
import traceback
import asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 加载 .env
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

# 检查 Gemini API key
print("GEMINI_API_KEY set:", bool(os.environ.get("GEMINI_API_KEY")))
print("GEMINI_API_KEY_REF:", os.environ.get("GEMINI_API_KEY_REF"))

# 检查 secret store
from host_brain.app.config import AppConfig
cfg = AppConfig()
print("\nAppConfig:")
print("  llm_provider:", cfg.llm_provider)
print("  gemini_api_key set:", bool(cfg.gemini_api_key))
print("  llm_api_key set:", bool(cfg.llm_api_key))

# 检查 resolve_host_llm_runtime
from host_brain.app.graph_config_builder import resolve_host_llm_runtime
runtime = resolve_host_llm_runtime()
print("\nresolve_host_llm_runtime:")
for k, v in runtime.items():
    if k == "llm_api_key":
        print(f"  {k}: {'SET' if v else 'EMPTY'}")
    elif k == "provider_fn":
        print(f"  {k}: {type(v).__name__}")
    else:
        print(f"  {k}: {v}")
