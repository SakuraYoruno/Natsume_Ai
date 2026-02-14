import chromadb
from chromadb.utils import embedding_functions
import sqlite3
import sys
import os
import torch

try:
    from config import DB_PATH, EMBEDDING_MODEL_PATH

    # 自动兼容不同的变量命名方式
    try:
        from config import CHROMA_DB_PATH
    except ImportError:
        from config import CHROMA_PERSIST_DIRECTORY as CHROMA_DB_PATH
    try:
        from config import COLLECTION_NAME
    except ImportError:
        COLLECTION_NAME = "natsume_persona_core"
except ImportError as e:
    print(f"❌ [Memory] 核心配置加载失败: {e}")
    sys.exit(1)

# --- 2. 初始化 Chroma (RTX 5080 兼容版) ---
print("   ├─ [Memory] Loading ChromaDB (Soul)...")
collection = None

try:
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    # 🟢 自动识别 5080 显卡
    device_target = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"      ⚙️ Memory Device: {device_target.upper()} (RTX 5080 Enabled)")

    # 定义嵌入函数
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL_PATH,
        device=device_target
    )

    # 🔥 核心修复逻辑：强行握手，解决 "Embedding function conflict"
    try:
        collection = chroma_client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=emb_fn
        )
    except Exception as e:
        if "Embedding function conflict" in str(e):
            print(f"      ⚠️ 检测到持久化模型冲突，正在强制对齐为 {os.path.basename(EMBEDDING_MODEL_PATH)}...")
            # 解决方案：使用 get_or_create 重新激活当前 emb_fn 配置
            collection = chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=emb_fn
            )
        else:
            raise e

    if collection:
        print(f"      ✅ Memory Linked. Count: {collection.count()}")

except Exception as e:
    print(f"❌ [Memory] Chroma 加载失败: {e}")
    collection = None


# --- 3. 核心功能逻辑 (代谢权重与双语召回) ---

def get_metabolism_weights(memory_ids):
    """从 SQLite 获取记忆的当前权重 (遗忘系数)"""
    weights = {}
    if not memory_ids: return weights
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 批量查询
        placeholders = ','.join('?' * len(memory_ids))
        cursor.execute(f"SELECT memory_id, current_weight FROM memory_metabolism WHERE memory_id IN ({placeholders})",
                       memory_ids)
        for mid, w in cursor.fetchall():
            weights[mid] = w
        conn.close()
    except Exception as e:
        # 即使 SQLite 暂时查不到，也允许系统以默认权重运行
        pass

    # 没查到的默认权重为 1.0 (新记忆)
    return {mid: weights.get(mid, 1.0) for mid in memory_ids}


def recall(query, mood):
    """[V5.7] 双轨召回 + 5080 加速 + 代谢重排"""
    # 🛡️ 关键保护：如果加载失败，返回空列表，防止 handle_request 报错
    if collection is None:
        return []

    try:
        # 心情较低时屏蔽亲密记忆
        where_filter = {"tag": {"$ne": "Intimate"}} if mood < 20 else None

        # 1. 向量初步召回 (5080 毫秒级推理)
        res = collection.query(
            query_texts=[query],
            n_results=10,
            where=where_filter
        )

        if not res or not res.get('documents') or not res['documents'][0]:
            return []

        docs = res['documents'][0]  # 文档内容 (台词原文 或 日记正文)
        metadatas = res['metadatas'][0]  # 元数据 (包含 translation, memory_type 等)
        ids = res['ids'][0]  # 记忆 ID
        distances = res['distances'][0]  # 原始距离

        # 2. 获取代谢权重 (遗忘曲线)
        weights = get_metabolism_weights(ids)

        # 3. 灵魂逻辑：加权重排 & 格式化
        weighted_results = []
        for i, doc_id in enumerate(ids):
            # 获取该记忆的生命力权重 (0.0 ~ 1.0)
            weight = weights.get(doc_id, 1.0)

            # 🛑 遗忘过滤：权重太低说明已经被大脑“尘封”，不进行提取
            if weight < 0.2:
                continue

            # 计算最终召回分：(相关性) * (记忆权重)
            base_score = max(0, 1.0 - distances[i])
            final_score = base_score * weight

            # 🛠️ 数据提取
            jp_text = docs[i]
            # 兼容处理：防止 metadata 为空
            meta = metadatas[i] if (metadatas and i < len(metadatas)) else {}
            cn_text = meta.get('translation', '（翻译缺失）') if meta else '（翻译缺失）'

            # 🟢 [核心修改] 读取记忆类型标签 (思想钢印)
            # 如果没有 memory_type 字段，默认视为 "canon" (原典台词)
            mem_type = meta.get('memory_type', 'canon')

            # 🟢 [核心修改] 差异化格式输出
            if mem_type == 'canon':
                # 情况 A: 游戏台词 -> 强调【原文】和【意译】，用于模仿语气
                content_str = f"[类型:经典语录] [原文:{jp_text}] [意译:{cn_text}]"
            else:
                # 情况 B: 昨晚写的日记 -> 强调【内容】，用于回想事实
                # 日记的 jp_text 就是日记正文，通常不需要意译
                content_str = f"[类型:珍贵回忆] [内容:{jp_text}]"

            weighted_results.append({
                "content": content_str,
                "score": final_score
            })

        # 4. 按加权后的分数进行降序排列
        weighted_results.sort(key=lambda x: x["score"], reverse=True)

        # 5. 返回前 5 个最相关的“鲜活”记忆
        return [item["content"] for item in weighted_results[:5]]

    except Exception as e:
        print(f"⚠️ [Memory] 检索过程异常: {e}")
        return []
