import sys
# 检查 sentence-transformers 是否已安装
try:
    import sentence_transformers
    print("sentence-transformers:", sentence_transformers.__version__)
except ImportError as e:
    print("sentence-transformers 未安装:", e)

# 检查 torch
try:
    import torch
    print("torch:", torch.__version__)
    print("CUDA available:", torch.cuda.is_available())
except ImportError as e:
    print("torch 未安装:", e)

# 检查本地已缓存的模型
import pathlib, os
cache_dirs = [
    pathlib.Path.home() / ".cache" / "huggingface" / "hub",
    pathlib.Path.home() / ".cache" / "torch" / "sentence_transformers",
    pathlib.Path("models"),
]
for d in cache_dirs:
    if d.exists():
        print(f"\n[缓存目录] {d}")
        for item in sorted(d.iterdir()):
            print(f"  {item.name}")
    else:
        print(f"\n[不存在] {d}")
