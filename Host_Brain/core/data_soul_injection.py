import os
import re
import time
import torch
import chromadb
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from chromadb.utils import embedding_functions  # 🟢 [新增] 用于强制对齐模型

# ================= 配置区 =================
JP_PATH = r"D:\Natsume_Brain\data\raw_ast_jp"
CN_PATH = r"D:\Natsume_Brain\data\raw_ast_cn"

DB_PATH = r"D:\Natsume_Brain\data\chroma_db"
COLLECTION_NAME = "natsume_persona_core"
MODEL_PATH = r"D:\AI_Models\multilingual-minilm"

# 🚀 RTX 5080 性能优化：可以处理更大的批次
BATCH_SIZE = 4096
TARGET_NAMES = ["藍", "夏目蓝", "夏目藍", "蓝", "夏目"]


# ================= 核心逻辑 =================

def read_file_safe(path):
    encodings = ['utf-8', 'utf-16', 'shift_jis', 'gb18030', 'gbk']
    for enc in encodings:
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except:
            continue
    return None


def extract_dialogues(content):
    dialogues = []
    pattern = re.compile(r'name\s*=\s*\{"([^"]+)"\},\s*[\r\n\t]+\s*"([^"]+)"', re.DOTALL)
    matches = pattern.findall(content)
    for name, text in matches:
        clean_text = text.replace(r'\rt2', '').replace('「', '').replace('」', '')
        clean_text = re.sub(r'<[^>]+>', '', clean_text).strip()
        if clean_text:
            dialogues.append({"name": name.strip(), "text": clean_text})
    return dialogues


def start_import():
    # 1. 硬件握手 (RTX 5080 检测)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        print(f"🚀 RTX 5080 已就绪: {torch.cuda.get_device_name(0)}")
    else:
        print("❌ 警告: 未检测到显卡，请检查 CUDA 13.0 环境")

    # 2. 准备嵌入函数 (这是解决冲突的关键)
    # 💡 必须在初始化集合时就锁死 embedding_function
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=MODEL_PATH,
        device=device
    )

    # 3. 加载计算模型
    print(f"📥 正在加载 Embedding 模型: {os.path.basename(MODEL_PATH)}")
    model = SentenceTransformer(MODEL_PATH, device=device)
    model.eval()

    # 4. 初始化数据库
    client = chromadb.PersistentClient(path=DB_PATH)

    # 🔥 [V5.7 强制清理] 彻底删除旧集合，消除 persisted: default 冲突
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"🧹 已清空旧集合 '{COLLECTION_NAME}'，准备注入全新灵魂...")
    except:
        pass

    # 🔥 [核心对齐] 创建集合时显式传入 ef，确保元数据标记为 sentence_transformer
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef  # 这一行解决了 image_3f9ac5 里的 conflict 报错
    )

    batch_docs, batch_metas, batch_ids = [], [], []
    total_count = 0

    files = [f for f in os.listdir(JP_PATH) if f.endswith('.ast')]
    print(f"📂 发现 {len(files)} 个脚本文件，开始双轨解析...")

    for file_name in tqdm(files, desc="注入灵魂"):
        jp_file = os.path.join(JP_PATH, file_name)
        cn_file = os.path.join(CN_PATH, file_name)

        if not os.path.exists(cn_file): continue

        jp_content = read_file_safe(jp_file)
        cn_content = read_file_safe(cn_file)
        if not jp_content or not cn_content: continue

        jp_dialogues = extract_dialogues(jp_content)
        cn_dialogues = extract_dialogues(cn_content)

        limit = min(len(jp_dialogues), len(cn_dialogues))

        for i in range(limit):
            jp_item = jp_dialogues[i]
            cn_item = cn_dialogues[i]

            is_target = any(target in jp_item['name'] for target in TARGET_NAMES)

            if is_target:
                doc_text = jp_item['text']  # 向量化使用日文 (灵魂)

                meta = {
                    "translation": cn_item['text'],  # Dify 语义理解使用中文
                    "source_file": file_name,
                    "character": jp_item['name'],
                    "memory_type": "canon", # 🟢 [新增] 思想钢印：这是“出厂设置”
                    "initial_weight": 1.0,
                    "current_weight": 1.0,
                    "last_access_time": time.time(),
                    "tag": "General"
                }

                batch_docs.append(doc_text)
                batch_metas.append(meta)
                batch_ids.append(f"{file_name}_{i}")
                total_count += 1

            if len(batch_docs) >= BATCH_SIZE:
                # 显卡批量加速
                embeddings = model.encode(batch_docs, batch_size=BATCH_SIZE, convert_to_tensor=False)
                collection.add(
                    embeddings=embeddings.tolist(),
                    documents=batch_docs,
                    metadatas=batch_metas,
                    ids=batch_ids
                )
                batch_docs, batch_metas, batch_ids = [], [], []

    # 处理收尾
    if batch_docs:
        embeddings = model.encode(batch_docs, batch_size=BATCH_SIZE, convert_to_tensor=False)
        collection.add(
            embeddings=embeddings.tolist(),
            documents=batch_docs,
            metadatas=batch_metas,
            ids=batch_ids
        )

    print(f"\n✨ 灵魂注入完成！")
    print(f"✅ 共存入核心记忆: {total_count} 条")
    print(f"💾 数据库已原生适配 RTX 5080 (Blackwell 架构)")


if __name__ == "__main__":
    start_import()