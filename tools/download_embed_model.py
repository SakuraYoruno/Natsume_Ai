"""
下载本地嵌入模型到 models/ 目录
模型：paraphrase-multilingual-MiniLM-L12-v2（dim=384，支持日中英）
"""
from __future__ import annotations
import pathlib, sys

ROOT = pathlib.Path(".").resolve()
MODEL_DIR = ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

print(f"[模型下载] {MODEL_ID}")
print(f"[保存路径] {MODEL_DIR}")
print()

if MODEL_DIR.exists() and any(MODEL_DIR.iterdir()):
    print("[跳过] 模型已存在")
    # 验证模型可加载
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(str(MODEL_DIR), device="cpu")
        vec = model.encode("テスト", normalize_embeddings=True)
        print(f"[OK] 模型加载成功，向量维度: {len(vec)}")
    except Exception as e:
        print(f"[WARN] 模型加载失败: {e}")
    sys.exit(0)

MODEL_DIR.mkdir(parents=True, exist_ok=True)

try:
    from sentence_transformers import SentenceTransformer
    print("[下载中] 请稍候（约 470MB）...")
    model = SentenceTransformer(
        model_name_or_path=MODEL_ID,
        device="cpu",
        cache_folder=str(ROOT / "models"),
    )
    # 保存到本地目录
    model.save(str(MODEL_DIR))
    print(f"[OK] 模型已保存到: {MODEL_DIR}")

    # 验证
    vec = model.encode("テスト", normalize_embeddings=True)
    print(f"[OK] 向量维度: {len(vec)}")
    print(f"[OK] 示例向量（前5维）: {list(vec[:5])}")

except Exception as e:
    print(f"[FAIL] 下载失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
