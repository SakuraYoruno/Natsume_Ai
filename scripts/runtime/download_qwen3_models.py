"""
下载 Qwen3-Embedding-0.6B 和 Qwen3-Reranker-0.6B 到本地 models/ 目录
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from huggingface_hub import snapshot_download

models = [
    ("Qwen/Qwen3-Embedding-0.6B", "models/Qwen3-Embedding-0.6B"),
    ("Qwen/Qwen3-Reranker-0.6B",  "models/Qwen3-Reranker-0.6B"),
]

for repo_id, local_dir in models:
    print(f"\n>>> 下载 {repo_id} -> {local_dir}")
    try:
        path = snapshot_download(
            repo_id=repo_id,
            local_dir=local_dir,
            ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
        )
        print(f"    完成: {path}")
    except Exception as e:
        print(f"    失败: {e}")

print("\n全部完成！")
