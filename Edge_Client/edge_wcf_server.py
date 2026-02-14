from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any

# 在这里做映射Edge要实现的技能
from senses.eyes import NatsumeEyes

app = FastAPI(title="Edge WCF (3070Ti)")

class ExecReq(BaseModel):
    skill_name: str
    params: Dict[str, Any] = {}

# 懒加载：需要时才初始化
_eyes = None
def get_eyes():
    global _eyes
    if _eyes is None:
        _eyes = NatsumeEyes()
    return _eyes

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/execute_local")
def execute_local(req: ExecReq):
    name = req.skill_name
    params = req.params or {}

    # 示例：给 Host 的 “analyze_screen / vision” 做一个 Edge 侧实现
    if name in ("analyze_screen", "edge_see"):
        objs = get_eyes().see()
        return {"result": {"objects": objs}}

    return {"result": f"❌ Edge 未实现技能: {name}"}
