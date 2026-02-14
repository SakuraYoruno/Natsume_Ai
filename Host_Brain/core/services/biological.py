import sqlite3
import time
import asyncio
import torch
from transformers import pipeline

from config import DB_PATH

# ================= 配置参数 =================
BASE_ENERGY_COST = 0.5  # 每次对话基础消耗
RECOVERY_RATE_PER_MIN = 1.0  # 每分钟回血量
MAX_ENERGY = 100.0
MIN_ENERGY = 0.0

# ================= 🧠 杏仁核 (Amygdala) 初始化 =================
print("🧠 [Bio] 正在加载情感分析神经元 (BERT)...")
try:
    # 检测 GPU (RTX 5080)
    device = 0 if torch.cuda.is_available() else -1
    device_name = "CUDA (RTX 5080)" if device == 0 else "CPU"

    # 加载多语言情感分析模型 (支持中日英)
    # 第一次运行会自动下载约 300MB 模型文件
    sentiment_analyzer = pipeline(
        "sentiment-analysis",
        model="lxyuan/distilbert-base-multilingual-cased-sentiments-student",
        device=device,
        top_k=None  # 返回所有标签的概率
    )
    print(f"✅ [Bio] 杏仁核已激活，运行于: {device_name}")
    HAS_MODEL = True
except Exception as e:
    print(f"⚠️ [Bio] 模型加载失败，情感感知将失效: {e}")
    HAS_MODEL = False


def analyze_sentiment_bert(text):
    """
    使用 BERT 模型进行深度情感计算
    返回: (mood_delta, trust_delta)
    """
    if not text or not HAS_MODEL: return 0, 0

    try:
        # 模型推理
        results = sentiment_analyzer(text)[0]
        # results 结构: [{'label': 'positive', 'score': 0.95}, ...]

        # 将结果转换为字典
        scores = {item['label']: item['score'] for item in results}

        pos_score = scores.get('positive', 0.0)
        neg_score = scores.get('negative', 0.0)

        mood_delta = 0
        trust_delta = 0

        # === 情感计算公式 ===

        # 1. 正面反馈 (喜欢/夸奖)
        if pos_score > 0.6:
            # 强度 * 系数 (最高 +10)
            mood_delta = pos_score * 10
            trust_delta = pos_score * 2

        # 2. 负面反馈 (辱骂/攻击)
        elif neg_score > 0.5:
            # 负面情绪杀伤力通常更大 (x15)
            mood_delta = -(neg_score * 15)
            trust_delta = -(neg_score * 5)

            # 暴击判定：如果极度确信是恶意 (>0.9)
            if neg_score > 0.9:
                mood_delta -= 5

                # 3. 忽略微弱情绪噪音
        if abs(mood_delta) < 1.0:
            return 0, 0

        return mood_delta, trust_delta

    except Exception as e:
        print(f"⚠️ [Bio] BERT 推理出错: {e}")
        return 0, 0


async def get_state():
    """
    [Async Interface] 获取当前状态
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 🟢 [修正] 表名改为 character_state，且强制读取 id=1
        cursor.execute("SELECT mood, trust, energy, last_interaction_time FROM character_state WHERE id=1")
        row = cursor.fetchone()

        now = time.time()

        if not row:
            # 🚑 紧急自我修复
            print("⚠️ [Bio] 状态为空，正在初始化默认生命体征...")
            mood, trust, energy, last_time = 60.0, 60.0, 100.0, now
            cursor.execute(
                "INSERT INTO character_state (id, mood, trust, energy, last_interaction_time) VALUES (1, ?, ?, ?, ?)",
                (mood, trust, energy, last_time))
            conn.commit()
        else:
            mood, trust, energy, last_time = row
            if last_time is None: last_time = now

        # === 自然恢复逻辑 ===
        elapsed_minutes = (now - last_time) / 60.0

        if elapsed_minutes > 5 and energy < MAX_ENERGY:
            recovery = elapsed_minutes * RECOVERY_RATE_PER_MIN
            old_energy = energy
            energy = min(MAX_ENERGY, energy + recovery)

            cursor.execute("UPDATE character_state SET energy=?, last_interaction_time=? WHERE id=1", (energy, now))
            conn.commit()
            print(f"🛌 [Bio] 经过 {elapsed_minutes:.1f} 分钟休息，精力恢复: {old_energy:.1f} -> {energy:.1f}")

        conn.close()
        return mood, trust, energy

    except Exception as e:
        print(f"⚠️ [Bio] 读取状态失败: {e}")
        return 60.0, 60.0, 100.0


# 🟢 [修改] 增加 user_text 参数
async def update_state(current_mood, current_trust, current_energy, user_text=""):
    """
    [Async Interface] 代谢更新：根据用户说的话，计算心情和体力变化
    """
    try:
        # 1. 深度情感感知 (BERT)
        mood_delta, trust_delta = analyze_sentiment_bert(user_text)

        if abs(mood_delta) > 0.1:
            print(f"   ⚡️ [Amygdala] 情感刺激: Mood {mood_delta:+.2f} | Trust {trust_delta:+.2f}")

        # 2. 计算新心情
        new_mood = current_mood + mood_delta
        # 情绪重力 (慢慢回归 60)
        new_mood += (60 - new_mood) * 0.1
        new_mood = max(0, min(100, new_mood))

        # 3. 计算新信任度
        new_trust = max(0, min(100, current_trust + trust_delta))

        # 4. 计算新精力 (心情越差消耗越大)
        cost_factor = 0.5 if new_mood > 70 else (2.0 if new_mood < 30 else 1.0)
        energy_drop = BASE_ENERGY_COST * cost_factor
        new_energy = max(MIN_ENERGY, current_energy - energy_drop)

        # 5. 写入数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
                       UPDATE character_state
                       SET mood=?,
                           trust=?,
                           energy=?,
                           last_interaction_time=?
                       WHERE id = 1
                       """, (new_mood, new_trust, new_energy, time.time()))

        conn.commit()
        conn.close()
        return new_mood, new_energy

    except Exception as e:
        print(f"❌ [Bio] 更新状态失败: {e}")
        return current_mood, current_energy


async def log_interaction(user_text, ai_text):
    """
    [Async Interface] 记录对话历史
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (user_text, ai_text) VALUES (?, ?)", (user_text, ai_text))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ [History] 记录失败: {e}")


# 测试用
if __name__ == "__main__":
    async def test():
        print("🧪 测试生物模块 (BERT版)...")
        m, t, e = await get_state()
        print(f"初始状态: Mood={m}, Energy={e}")

        test_input = "笨蛋蓝，什么都做不好！"
        print(f"🗣️ 模拟输入: {test_input}")

        m_new, e_new = await update_state(m, t, e, user_text=test_input)
        print(f"更新后: Mood={m_new:.1f}, Energy={e_new:.1f}")


    asyncio.run(test())