from neo4j import AsyncGraphDatabase
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

print("   ├─ [Social] Connecting Neo4j (Async)...")

# 🟢 初始化异步驱动实例
driver = None
try:
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except Exception as e:
    print(f"⚠️ [Social] 驱动初始化失败: {e}")


async def get_bond():
    """
    [Async] 获取身份锚点
    不再阻塞主线程，网络卡顿不影响心跳
    """
    if not driver: return "Stranger", "Level 0"

    query = "MATCH (n:Character {name: 'Natsume_Ai'})-[r:BOND_WITH]->(p:Player) RETURN r.type, r.level"

    try:
        # 🟢 异步 Session 上下文
        async with driver.session() as session:
            # 发送查询
            result = await session.run(query)
            # 等待获取单条记录
            record = await result.single()

            if record:
                return record[0], f"Level {record[1]}"

    except Exception as e:
        print(f"⚠️ [Social] 异步查询失败: {e}")
        # 这里可以选择添加重连逻辑，或者保持静默降级

    return "Stranger", "Level 0"


async def close():
    """关闭驱动连接池 (供系统关闭时调用)"""
    if driver:
        await driver.close()
        print("   └─ [Social] Neo4j connection closed.")