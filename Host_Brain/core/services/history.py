import sqlite3
import time

from config import DB_PATH


def init_history_db():
    """初始化今日对话临时表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
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
    conn.commit()
    conn.close()


def append_log(role, content):
    """写入一条对话"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO daily_logs (timestamp, role, content) VALUES (?, ?, ?)",
                       (time.time(), role, content))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ [History] 写入失败: {e}")


def get_today_logs():
    """获取过去 24 小时的所有对话"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 获取一天前的时间戳
        one_day_ago = time.time() - 86400
        cursor.execute("SELECT role, content FROM daily_logs WHERE timestamp > ? ORDER BY timestamp ASC",
                       (one_day_ago,))
        rows = cursor.fetchall()
        conn.close()

        # 格式化为文本块
        log_text = ""
        for role, content in rows:
            log_text += f"[{role}]: {content}\n"
        return log_text
    except:
        return ""


def clear_old_logs():
    """清理 48 小时前的旧日志（因为已经总结成日记了，不需要留着流水账）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        two_days_ago = time.time() - 172800
        cursor.execute("DELETE FROM daily_logs WHERE timestamp < ?", (two_days_ago,))
        conn.commit()
        conn.close()
        print("🧹 [History] 已清理过期流水账")
    except:
        pass


# 初始化
init_history_db()