import torch
import aiohttp
import asyncio
from sentence_transformers import CrossEncoder

from config import RERANKER_MODEL_PATH, DIFY_API_KEY, DIFY_BASE_URL

print("   ├─ [Cognitive] Loading Reranker (Arbitrator)...")

# ✅ 确认 5080 被正确调用
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"      ⚙️ Inference Device: {device.upper()} (RTX 5080 Mode)")

rerank_model = None
try:
    # 5080 Blackwell 架构在此处的推理延迟应低于 100ms
    rerank_model = CrossEncoder(RERANKER_MODEL_PATH, device=device, max_length=512)
except Exception as e:
    print(f"⚠️ [Cognitive] Rerank 模型加载失败: {e}")

# 🟢 [V5.7 优化] 持久化 Session 避免频繁握手
_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

def think(query, candidates):
    """
    重排仲裁 - 利用 5080 GPU 加速
    """
    if not candidates: return "（......）"
    if not rerank_model: return candidates[0]

    try:
        # 将记忆列表与 Query 配对进行打分
        pairs = [[query, doc] for doc in candidates]
        scores = rerank_model.predict(pairs)
        best_idx = scores.argmax()
        return candidates[best_idx]
    except Exception as e:
        print(f"⚠️ [Rerank] 异常: {e}")
        return candidates[0]

async def async_speak(user_text, context_data):
    """
    大脑生成 (Dify) - 异步非阻塞版
    """
    fallback_response = context_data.get('current_memory', "（......）")

    payload = {
        "inputs": context_data,
        "query": user_text,
        "response_mode": "blocking",
        "user": "Master"
    }
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}"}

    try:
        session = await get_session()
        # 🟢 挂起主线程，不阻塞 5080 的其他异步任务
        async with session.post(DIFY_BASE_URL, json=payload, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                answer = data.get('answer', fallback_response)
                # 过滤 Dify 产生的空回复
                return answer if answer.strip() else fallback_response
            else:
                error_text = await resp.text()
                print(f"❌ [Dify] API Error: {error_text}")
                return fallback_response

    except asyncio.TimeoutError:
        print(f"❌ [Dify] Timeout: 思考超时")
        return fallback_response
    except Exception as e:
        print(f"❌ [Dify] Conn Error: {e}")
        return fallback_response

# 🔴 [新增] 优雅关闭接口
async def close_cognitive():
    global _session
    if _session:
        await _session.close()