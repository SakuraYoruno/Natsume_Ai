import argparse
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from config import Settings
from core.context_pack import build_context_pack
from llm.dify_client import DifyChatflowClient
from llm.reply_validator import ReplyQA, validate_and_sanitize_reply
from state.hard_state import HardStateLedger
from bond.bond_summary import BondSummaryProvider
from memory.episodic import EpisodicMemory
from memory.world_canon import WorldCanonStore


class ChatRequest(BaseModel):
    user_text_cn: str
    debug: bool = False


class ChatResponse(BaseModel):
    narrative_ja: str
    intent: list
    quality: dict
    qa: dict
    request_id: str
    ts_utc: str
    debug: Optional[dict] = None


def create_app() -> FastAPI:
    """
    Host_Brain V2（最小闭环）：
    - Host 侧：事实主权 / RAG 主权 / 审计落盘（SQLite）
    - Dify：只生成「日语叙事 + Intent 草案（JSON）」；不得执行、不得自称执行
    """
    settings = Settings.from_env()
    ledger = HardStateLedger(settings.SQLITE_PATH)
    dify = DifyChatflowClient(settings.DIFY_API_URL, settings.DIFY_API_KEY, settings.DIFY_USER_ID)

    bond_provider = BondSummaryProvider(
        neo4j_uri=settings.NEO4J_URI,
        neo4j_user=settings.NEO4J_USER,
        neo4j_password=settings.NEO4J_PASSWORD,
        summary_query=settings.NEO4J_BOND_SUMMARY_QUERY,
        connect_timeout_s=settings.NEO4J_CONNECT_TIMEOUT_S,
    )

    episodic = EpisodicMemory(settings.CHROMA_DIR, settings.CHROMA_EPISODIC_COLLECTION)
    canon = WorldCanonStore(settings.CHROMA_DIR, settings.CHROMA_CANON_COLLECTION)

    app = FastAPI(title="Project Natsume Host_Brain V2", version="2.0.0")

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        user_text_cn = (req.user_text_cn or "").strip()
        if not user_text_cn:
            raise ValueError("user_text_cn 为空")

        request_id = ledger.new_request_id()
        ts_utc = datetime.now(timezone.utc).isoformat()

        # Host 读取四库（按可用性降级）
        hard_state = ledger.read_hard_state_snapshot(limit_keys=settings.HARD_STATE_KEYS_LIMIT)
        bond_summary = bond_provider.get_summary_safe()
        recent_dialogue = ledger.read_recent_dialogue(limit_turns=settings.RECENT_DIALOGUE_TURNS)

        # Host 侧召回：World Canon + Episodic（RAG 主权归 Host）
        world_hits = canon.query(user_text_cn, top_k=settings.WORLD_CANON_TOP_K)
        episodic_hits = episodic.query(user_text_cn, top_k=settings.EPISODIC_TOP_K)

        # Host 打包 context_pack_json（Host 负责压缩与裁剪）
        context_pack = build_context_pack(
            request_id=request_id,
            user_text_cn=user_text_cn,
            hard_state=hard_state,
            bond_summary=bond_summary,
            recent_dialogue=recent_dialogue,
            world_canon_hits=world_hits,
            episodic_hits=episodic_hits,
            max_chars=settings.CONTEXT_PACK_MAX_CHARS,
        )
        context_pack_json = json.dumps(context_pack, ensure_ascii=False)

        # Dify（Ego）：只生成 JSON（narrative_ja + intent + quality）
        dify_raw = dify.chat(user_query=user_text_cn, context_pack_json=context_pack_json)

        # Host 审计：JSON 修复 + 伪执行宣告检测 + 最小兜底
        qa = ReplyQA.from_reply(dify_raw.get("answer", ""))
        reply_obj = validate_and_sanitize_reply(
            raw_answer_text=dify_raw.get("answer", ""),
            qa=qa,
            fallback_style="apology_minimal",
        )

        # Host 落盘：Hard Ledger（chat_history append-only）
        ledger.append_chat_history(
            request_id=request_id,
            user_text_cn=user_text_cn,
            context_pack_json=context_pack_json,
            dify_raw_json=json.dumps(dify_raw, ensure_ascii=False),
            assistant_reply_json=json.dumps(reply_obj, ensure_ascii=False),
            qa_json=json.dumps(qa.to_dict(), ensure_ascii=False),
        )

        return ChatResponse(
            narrative_ja=reply_obj["narrative_ja"],
            intent=reply_obj.get("intent", []) or [],
            quality=reply_obj.get("quality", {}) or {},
            qa=qa.to_dict(),
            request_id=request_id,
            ts_utc=ts_utc,
            debug=context_pack if req.debug else None,
        )

    return app


def run_cli() -> None:
    """
    交互式自检入口（Edge 断开时用来压测叙事质量）：
    - 输入中文
    - 输出：Host 审计后的 JSON（含日语叙事 + intent 草案）
    """
    settings = Settings.from_env()
    ledger = HardStateLedger(settings.SQLITE_PATH)
    dify = DifyChatflowClient(settings.DIFY_API_URL, settings.DIFY_API_KEY, settings.DIFY_USER_ID)

    bond_provider = BondSummaryProvider(
        neo4j_uri=settings.NEO4J_URI,
        neo4j_user=settings.NEO4J_USER,
        neo4j_password=settings.NEO4J_PASSWORD,
        summary_query=settings.NEO4J_BOND_SUMMARY_QUERY,
        connect_timeout_s=settings.NEO4J_CONNECT_TIMEOUT_S,
    )

    episodic = EpisodicMemory(settings.CHROMA_DIR, settings.CHROMA_EPISODIC_COLLECTION)
    canon = WorldCanonStore(settings.CHROMA_DIR, settings.CHROMA_CANON_COLLECTION)

    print("Host_Brain V2 CLI：输入中文，输出经过 Host 审计后的日语 JSON。输入 /exit 退出。")

    while True:
        try:
            user_text_cn = input("\nCN> ").strip()
        except EOFError:
            break

        if not user_text_cn:
            continue
        if user_text_cn.lower() in ("/exit", "exit", "quit"):
            break

        request_id = ledger.new_request_id()
        hard_state = ledger.read_hard_state_snapshot(limit_keys=settings.HARD_STATE_KEYS_LIMIT)
        bond_summary = bond_provider.get_summary_safe()
        recent_dialogue = ledger.read_recent_dialogue(limit_turns=settings.RECENT_DIALOGUE_TURNS)

        world_hits = canon.query(user_text_cn, top_k=settings.WORLD_CANON_TOP_K)
        episodic_hits = episodic.query(user_text_cn, top_k=settings.EPISODIC_TOP_K)

        context_pack = build_context_pack(
            request_id=request_id,
            user_text_cn=user_text_cn,
            hard_state=hard_state,
            bond_summary=bond_summary,
            recent_dialogue=recent_dialogue,
            world_canon_hits=world_hits,
            episodic_hits=episodic_hits,
            max_chars=settings.CONTEXT_PACK_MAX_CHARS,
        )
        context_pack_json = json.dumps(context_pack, ensure_ascii=False)
        dify_raw = dify.chat(user_query=user_text_cn, context_pack_json=context_pack_json)

        qa = ReplyQA.from_reply(dify_raw.get("answer", ""))
        reply_obj = validate_and_sanitize_reply(
            raw_answer_text=dify_raw.get("answer", ""),
            qa=qa,
            fallback_style="apology_minimal",
        )

        ledger.append_chat_history(
            request_id=request_id,
            user_text_cn=user_text_cn,
            context_pack_json=context_pack_json,
            dify_raw_json=json.dumps(dify_raw, ensure_ascii=False),
            assistant_reply_json=json.dumps(reply_obj, ensure_ascii=False),
            qa_json=json.dumps(qa.to_dict(), ensure_ascii=False),
        )

        print("\n输出（JSON，含 narrative_ja + intent）:")
        print(json.dumps(reply_obj, ensure_ascii=False, indent=2))
        print("\nQA（Host 审计结果）:")
        print(json.dumps(qa.to_dict(), ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="运行交互式 CLI，而不是启动 HTTP 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8787, type=int)
    args = ap.parse_args()

    if args.cli:
        run_cli()
        return

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
