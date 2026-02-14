import schedule
import time
import sys
import sqlite3

from services import hippocampus
from config import DB_PATH

# ================= 核心任务逻辑 =================

def run_metabolism():
    """
    📉 [遗忘机制] 记忆代谢 (The Forgetting Curve)
    每晚将所有记忆的权重降低 5% (乘以 0.95)，模拟人类的自然遗忘。
    只有经常被 recall 的记忆（access_count增加）权重才会回升。
    """
    print("📉 [Metabolism] 正在执行全脑记忆代谢...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 1. 权重衰减：所有 current_weight > 0.1 的记忆，权重 * 0.95
        # 这样很久没用的记忆会慢慢沉入潜意识（不再被检索到）
        cursor.execute("""
                       UPDATE memory_metabolism
                       SET current_weight = current_weight * 0.95
                       WHERE current_weight > 0.1
                       """)

        # 2. (可选) 清理幽灵数据：如果权重低至 0.01，且非重要记忆，可以考虑标记归档
        # cursor.execute("DELETE FROM memory_metabolism WHERE current_weight < 0.01")

        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"   ✅ 代谢完成。{affected_rows} 条记忆的信号已根据遗忘曲线衰减。")

    except Exception as e:
        print(f"❌ [Metabolism] 代谢失败: {e}")


def nightly_routine():
    """深夜维护主程序"""
    print(f"\n🔔 [Clock] 深夜时刻 ({time.strftime('%H:%M')}) - 夏目蓝开始进入 REM 睡眠...")

    # 1. 海马体回放 (写日记 + 存入 Chroma)
    try:
        hippocampus.consolidate_memory()
    except Exception as e:
        print(f"❌ [Hippocampus] 回放失败: {e}")

    # 2. 全局代谢 (遗忘旧记忆)
    try:
        run_metabolism()
    except Exception as e:
        print(f"❌ [Metabolism] 任务异常: {e}")

    print("💤 [Clock] 夜间维护结束。等待明天的唤醒...\n")


# ================= 启动入口 =================

def start_daemon():
    print("⏰ [Daemon] V5.7 时间层守护进程已启动...")

    # 设定每天凌晨 03:00 执行
    schedule.every().day.at("03:00").do(nightly_routine)

    # 显示下次执行时间
    next_run = schedule.next_run()
    print(f"   - 下次执行时间: {next_run}")
    print("   - 状态: 监听中 (每 60s 扫描一次)")

    # 测试模式：如果带参数 --test，立即运行一次
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        print("🧪 [Test Mode] 立即触发夜间维护...")
        nightly_routine()

    # 守护循环
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次，非常省资源
    except KeyboardInterrupt:
        print("\n🛑 [Daemon] 守护进程已停止。")


if __name__ == "__main__":
    start_daemon()