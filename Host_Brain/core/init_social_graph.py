from neo4j import GraphDatabase

try:
    from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
except ImportError:
    print("❌ 错误：无法加载 config.py。请确认配置文件存在且包含 Neo4j 配置。")
    exit()

# --- 2. Cypher 语句定义 ---

# 清空数据库 (重置世界)
CLEAR_DB_QUERY = "MATCH (n) DETACH DELETE n"

# 创建核心节点与关系 (创世纪)
# 这里我们直接定义：
# Character: 夏目蓝
# Player: Master
# Relationship: BOND_WITH (类型: 共犯者, 等级: 5)
INIT_DATA_QUERY = """
CREATE (n:Character {
    name: 'Natsume_Ai', 
    display_name: '夏目蓝',
    description: '萝莉姐姐',
    created_at: datetime()
})
CREATE (p:Player {
    name: 'Master_Local',
    display_name: 'Master',
    created_at: datetime()
})
CREATE (n)-[r:BOND_WITH {
    type: 'Accomplice',    
    level: 5,              
    trust_base: 20.0,
    established_at: datetime()
}]->(p)
RETURN n, r, p
"""

# 验证查询
VERIFY_QUERY = """
MATCH (n:Character)-[r:BOND_WITH]->(p:Player)
RETURN n.display_name AS char_name, r.type AS relation, r.level AS level, p.display_name AS player_name
"""


# --- 3. 执行逻辑 ---

def init_social_world():
    print(f"🔌 [Neo4j] 正在连接图数据库: {NEO4J_URI} ...")

    driver = None
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print("✅ 连接成功！准备执行创世程序。")

        with driver.session() as session:
            # Step 1: 清理旧世界
            print("🧹 正在清理旧的关系数据...")
            session.run(CLEAR_DB_QUERY)

            # Step 2: 注入新契约
            print("🕸️ 正在编织【共犯者】契约...")
            session.run(INIT_DATA_QUERY)

            # Step 3: 验证
            print("🔍 正在验证写入结果...")
            result = session.run(VERIFY_QUERY).single()

            if result:
                char = result["char_name"]
                rel = result["relation"]
                lv = result["level"]
                player = result["player_name"]

                print("\n" + "=" * 40)
                print(f"🎉 契约已缔结 (V5.6 Social Graph Ready)")
                print(f"   角色: {char}")
                print(f"   对象: {player}")
                print(f"   关系: {rel}")
                print(f"   等级: Lv.{lv}")
                print("=" * 40 + "\n")
            else:
                print("❌ 警告：数据写入似乎失败，未查到关系。")

    except Exception as e:
        print(f"\n❌ [Error] Neo4j 初始化失败: {e}")
        print("请检查：")
        print("1. Neo4j Desktop 是否已启动？")
        print("2. 密码是否正确？")

    finally:
        if driver:
            driver.close()
            print("🔌 连接已断开。")


if __name__ == "__main__":
    # 添加一个确认步骤，防止误删数据
    print("⚠️  警告：此操作将清空 Neo4j 数据库中的所有现有节点并重置为初始状态。")
    confirm = input("确认执行吗？(输入 y 继续): ")

    if confirm.lower() == 'y':
        init_social_world()
    else:
        print("操作已取消。")