import sqlite3
import os
import time

from config import DB_PATH

def init_db():
    print(f"🧬 [Bio-Init] 正在构建生理数据库: {DB_PATH}")

    # 确保 data 目录存在
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ================= 1. 核心状态表 (Character State) =================
    # 表名改为 character_state，与 biological.py 保持一致
    # last_interaction_time 字段，用于计算体力自然恢复
    c.execute('''
              CREATE TABLE IF NOT EXISTS character_state
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  mood
                  REAL
                  DEFAULT
                  60.0,  -- 心情 (0-100)
                  trust
                  REAL
                  DEFAULT
                  60.0,  -- 信任 (0-100)
                  energy
                  REAL
                  DEFAULT
                  100.0, -- 精力 (0-100)
                  last_interaction_time
                  REAL,  -- 上次互动时间戳 (Unix Timestamp)
                  updated_at
                  DATETIME
                  DEFAULT
                  CURRENT_TIMESTAMP
              )
              ''')

    # ================= 2. 记忆代谢表 (Memory Metabolism) =================
    # 用于记录哪些记忆很久没被访问了 (Shadow Table)
    c.execute('''
              CREATE TABLE IF NOT EXISTS memory_metabolism
              (
                  memory_id
                  TEXT
                  PRIMARY
                  KEY, -- 对应 ChromaDB 的 ID
                  current_weight
                  REAL
                  DEFAULT
                  1.0,
                  last_access_time
                  REAL,
                  access_count
                  INTEGER
                  DEFAULT
                  0
              )
              ''')

    # ================= 3. 梦境缓冲区 (Dream Buffer) =================
    # 权重 < 0.1 的记忆会被移到这里
    c.execute('''
              CREATE TABLE IF NOT EXISTS dream_buffer
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  memory_id
                  TEXT,
                  content
                  TEXT,
                  source_file
                  TEXT,
                  enter_dream_time
                  DATETIME
                  DEFAULT
                  CURRENT_TIMESTAMP,
                  status
                  TEXT
                  DEFAULT
                  'PENDING' -- PENDING / DELETED / RESTORED
              )
              ''')

    # ================= 4. 对话历史表 (Chat History) =================
    # 🟢 [新增] 之前的 hippocampus.py 和 history.py 都需要这个表
    c.execute('''
              CREATE TABLE IF NOT EXISTS chat_history
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  user_text
                  TEXT,
                  ai_text
                  TEXT,
                  timestamp
                  DATETIME
                  DEFAULT
                  CURRENT_TIMESTAMP
              )
              ''')

    # ================= 5. 日志流水账表 (Daily Logs) =================
    # 🟢 [新增] 专门给 scheduler_daemon.py 用的临时表
    c.execute('''
              CREATE TABLE IF NOT EXISTS daily_logs
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  timestamp
                  REAL,
                  role
                  TEXT, -- 'User' or 'Ai'
                  content
                  TEXT
              )
              ''')

    # 6. 初始化默认状态 (如果表是空的)
    # 我们只需要一行数据，id=1，作为夏目蓝的“本体”
    c.execute("SELECT count(*) FROM character_state")
    if c.fetchone()[0] == 0:
        print("🌱 注入初始生命体征 (Mood:60, Trust:60, Energy:100)...")
        now = time.time()
        c.execute('''
                  INSERT INTO character_state (id, mood, trust, energy, last_interaction_time)
                  VALUES (1, 60.0, 60.0, 100.0, ?)
                  ''', (now,))

    conn.commit()
    conn.close()
    print("✅ [Bio-Init] 生理机能数据库构建完成。")


if __name__ == "__main__":
    init_db()