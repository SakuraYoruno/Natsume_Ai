from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any

from config import HOST_IP, MCP_PORT
from skills.catalog import SKILL_CATALOG
from skills.loader import load_and_execute

app = FastAPI(title="Natsume V6.1 MCP")

class ExecuteReq(BaseModel):
    skill: str
    params: Dict[str, Any] = {}

@app.get("/manifest")
def get_manifest():
    """生成 Dify 工具定义"""
    tools = []
    for name, meta in SKILL_CATALOG.items():
        tools.append({
            "name": name,
            "description": meta["desc"],
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string"} for k, v in meta.get("params", {}).items()}
            }
        })
    return {"tools": tools}

@app.post("/execute")
def execute(req: ExecuteReq):
    print(f"📥 [MCP] 收到指令: {req.skill} -> {req.params}")
    result = load_and_execute(req.skill, **req.params)
    return {"result": str(result)}

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 MCP Server 运行在: http://{HOST_IP}:{MCP_PORT}")
    uvicorn.run(app, host=HOST_IP, port=MCP_PORT)