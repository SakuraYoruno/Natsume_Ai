import importlib
import requests

from config import EDGE_WCF_API
from skills.catalog import SKILL_CATALOG


def load_and_execute(skill_name: str, **kwargs):
    """
    V6.1 核心加载器
    负责路由：本地 Lazy Load 或 远程 Edge 转发
    """
    if skill_name not in SKILL_CATALOG:
        return f"❌ 技能 '{skill_name}' 未注册"

    meta = SKILL_CATALOG[skill_name]
    target = kwargs.get("target", "host").lower()

    # === 路由 A: 转发给 Edge (3070 Ti) ===
    if target == "edge":
        print(f"📡 [Router] 转发技能 '{skill_name}' -> Edge ({EDGE_WCF_API})")
        try:
            resp = requests.post(f"{EDGE_WCF_API}/execute_local", json={
                "skill_name": skill_name,
                "params": kwargs
            }, timeout=3)
            if resp.status_code == 200:
                return f"Edge回应: {resp.json().get('result')}"
            else:
                return f"Edge错误: {resp.status_code}"
        except Exception as e:
            return f"❌ Edge 连接失败 (Host->Edge): {e}"

    # === 路由 B: Host 本地执行 (懒加载) ===
    module_name = meta["module"]
    func_name = meta["func"]

    print(f"⚡ [Loader] 动态加载本地模块: skills.impl.{module_name}")
    try:
        # 动态导入 skills.impl.xxx
        module = importlib.import_module(f"skills.impl.{module_name}")
        func = getattr(module, func_name)
        return func(**kwargs)
    except Exception as e:
        return f"❌ 本地执行报错: {e}"