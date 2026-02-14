import sqlite3
import requests
import datetime
import os

# 引入配置
from config import DB_PATH, DIFY_API_KEY, DIFY_BASE_URL
from core.services import history, memory

# ================= 配置区 =================
USE_LOCAL_OLLAMA = False

# 🟢 [核心修改] 动态 Prompt 模板
# 我们不再写死性格，而是留出 {persona_desc} 占位符
DIARY_TEMPLATE = """
你是夏目蓝（Natsume Ai）。
请根据你今天与 Master 的对话记录，以及你此刻的心情状态，写一篇【私人日记】。

【当前状态】
- 心情: {mood}/100
- 精力: {energy}/100
- 信任: {trust}/100
- **当前人格模式**: 【{persona}】
- **人格表现指南**: {persona_desc}

【写作要求】
1. 必须使用第一人称“私（わたし）”。
2. **语气必须严格符合上述的【当前人格模式】**。
   - 如果是"病娇"，要写出占有欲。
   - 如果是"疲惫"，要写得简短、想睡觉。
   - 如果是"毒舌"，可以抱怨Master是个笨蛋。
3. 提炼出今天最有感触的一个瞬间，不要流水账。
4. 字数 200 字以内，不要标题。
"""


# ================= 辅助函数 =================

def get_current_state_sync():
    """同步读取当前的生物状态 (Mood/Trust/Energy)"""
    # 既然是脚本运行，直接读 SQLite 最快，不需要 async
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT mood, trust, energy FROM character_state LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0], row[1], row[2]
    except Exception:
        pass
    # 默认值
    return 60.0, 60.0, 80.0


def resolve_persona_desc(mood, trust, energy):
    """
    🧮 计算人格，并返回给 LLM 看的【指导语】
    """
    # 1. 生理极限
    if energy < 15:
        return "Exhausted_Soft", "极度疲惫、慵懒、撒娇、想睡觉，句子很短，不想思考。"

    # 2. 防御机制
    if trust < 30:
        return "Distant_Polite", "冷淡、客气、使用敬语、保持距离，像对待陌生人。"

    # 3. 情绪光谱
    if mood < 35:
        if trust > 70:
            return "Clingy_Depressed", "委屈、粘人、缺乏安全感、自我厌恶，需要安慰。"
        else:
            return "Cold_Toxic", "尖酸刻薄、嘲讽、不耐烦、毒舌，攻击性强。"

    elif 35 <= mood <= 75:
        return "Gentle_Intellectual", "知性、优雅、温柔、从容，像一位成熟的姐姐或老师。"

    else:  # Mood > 75 (亢奋)
        if trust > 80:
            return "Obsessive_Devoted", "病娇、狂热、独占欲强、甜腻、危险的爱意。"
        else:
            return "Cheerful_Teasing", "元气、活泼、喜欢开玩笑、捉弄Master。"


# ================= 核心逻辑 =================

def generate_diary_content(logs):
    if not logs: return None

    print("🧠 [Hippocampus] 正在读取当前状态...")

    # 1. 获取各项数值
    mood, trust, energy = get_current_state_sync()

    # 2. 计算今晚的人格
    persona_name, persona_desc = resolve_persona_desc(mood, trust, energy)
    print(f"   - 此时此刻的状态: Mood={mood:.1f} | Persona={persona_name}")

    # 3. 渲染动态 Prompt
    final_prompt = DIARY_TEMPLATE.format(
        mood=mood,
        energy=energy,
        trust=trust,
        persona=persona_name,
        persona_desc=persona_desc
    )

    prompt_content = f"{final_prompt}\n\n【今日对话素材】\n{logs}\n\n【夏目蓝的日记】"

    # --- 调用 Dify (推荐) ---
    try:
        payload = {
            "inputs": {},
            "query": prompt_content,
            "response_mode": "blocking",
            "user": "System_Hippocampus",
        }
        headers = {"Authorization": f"Bearer {DIFY_API_KEY}"}
        resp = requests.post(DIFY_BASE_URL, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            return resp.json().get('answer', '')
        else:
            print(f"❌ Dify 调用失败: {resp.text}")
    except Exception as e:
        print(f"❌ Dify 连接错误: {e}")

    return None


def consolidate_memory():
    """海马体回放核心逻辑"""
    print("🌙 [Hippocampus] 开始夜间记忆固化...")

    # 1. 获取流水账
    logs = history.get_today_logs()
    if not logs:
        print("   - 今天无事发生。")
        return

    # 2. 生成动态日记
    diary_content = generate_diary_content(logs)
    if not diary_content:
        return

    # 3. 预览
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n======== 📅 夏目蓝的日记 ({today_str}) ========")
    print(diary_content.strip())
    print("==========================================\n")

    # 4. 存入 ChromaDB
    if memory.collection:
        try:
            unique_id = f"diary_{today_str}"
            memory.collection.add(
                documents=[diary_content],
                metadatas=[{
                    "translation": "（夏目蓝的私人日记）",
                    "tag": "Diary",
                    "date": today_str,
                    "memory_type": "episodic.py"  # 思想钢印
                }],
                ids=[unique_id]
            )
            print(f"✅ 日记已刻入灵魂 (Type: Episodic)")
            history.clear_old_logs()
        except Exception as e:
            print(f"❌ 向量存储失败: {e}")
            backup_file(diary_content, today_str)
    else:
        print("❌ Memory 模块未连接，备份至本地...")
        backup_file(diary_content, today_str)


def backup_file(content, date_str):
    save_dir = os.path.join(os.path.dirname(DB_PATH), "diaries_backup")
    if not os.path.exists(save_dir): os.makedirs(save_dir)
    with open(os.path.join(save_dir, f"{date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    consolidate_memory()