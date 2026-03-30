#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微信端到端测试脚本。

分阶段测试完整的接收→发送流程，帮助定位发送失败的具体原因。

用法：
    python tools/_test_wechat_e2e.py                    # 运行所有阶段
    python tools/_test_wechat_e2e.py --stage env        # 只测试环境
    python tools/_test_wechat_e2e.py --stage db         # 只测试数据库
    python tools/_test_wechat_e2e.py --stage recv       # 只测试接收
    python tools/_test_wechat_e2e.py --stage send       # 只测试发送
    python tools/_test_wechat_e2e.py --stage diag       # 窗口控件诊断
    python tools/_test_wechat_e2e.py --stage send --to wxid_xxx --msg "你好"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 修复 Windows GBK 终端编码
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936', 'gb2312'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("wx_e2e_test")

# ─────────────────────────────────────────────
# 配置读取
# ─────────────────────────────────────────────

def _get_config() -> dict:
    return {
        "decrypt_output_dir": os.environ.get("WECHAT_DECRYPT_OUTPUT_DIR", "").strip(),
        "db_storage_dir": os.environ.get("WECHAT_DB_STORAGE_DIR", "").strip(),
        "self_wxid": os.environ.get("WECHAT_SELF_WXID", "").strip(),
        "poll_interval_s": float(os.environ.get("WECHAT_POLL_INTERVAL_S", "5")),
    }


# ─────────────────────────────────────────────
# 阶段 1：环境检查
# ─────────────────────────────────────────────

def stage_env() -> bool:
    print("\n" + "=" * 60)
    print("【阶段 1】环境检查")
    print("=" * 60)

    ok = True

    # Python 版本
    print(f"  Python: {sys.version}")

    # 关键依赖
    deps = {
        "pywinauto": False,
        "win32clipboard": False,
        "watchdog": False,
        "dotenv": False,
    }
    for dep in list(deps.keys()):
        try:
            __import__(dep if dep != "dotenv" else "dotenv")
            deps[dep] = True
            print(f"  ✅ {dep}")
        except ImportError:
            print(f"  ❌ {dep} 未安装")
            ok = False

    # 配置检查
    cfg = _get_config()
    print(f"\n  WECHAT_DECRYPT_OUTPUT_DIR = {cfg['decrypt_output_dir'] or '(未配置)'}")
    print(f"  WECHAT_DB_STORAGE_DIR     = {cfg['db_storage_dir'] or '(未配置)'}")
    print(f"  WECHAT_SELF_WXID          = {cfg['self_wxid'] or '(未配置)'}")

    if not cfg["decrypt_output_dir"]:
        print("  ❌ WECHAT_DECRYPT_OUTPUT_DIR 未配置，请在 .env 中填写")
        ok = False
    elif not Path(cfg["decrypt_output_dir"]).exists():
        print(f"  ❌ 解密输出目录不存在: {cfg['decrypt_output_dir']}")
        ok = False
    else:
        db_files = list(Path(cfg["decrypt_output_dir"]).glob("de_*.db"))
        print(f"  ✅ 解密输出目录存在，找到 {len(db_files)} 个 de_*.db 文件")
        for f in db_files[:8]:
            size_kb = f.stat().st_size // 1024
            print(f"     - {f.name} ({size_kb} KB)")

    # 微信进程检查
    print("\n  检查微信进程...")
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5
        )
        if "Weixin.exe" in result.stdout:
            print("  ✅ 微信进程正在运行 (Weixin.exe)")
        else:
            print("  ❌ 微信进程未运行，请先启动微信并登录")
            ok = False
    except Exception as e:
        print(f"  ⚠️  无法检查进程: {e}")

    return ok


# ─────────────────────────────────────────────
# 阶段 2：数据库连接测试
# ─────────────────────────────────────────────

def stage_db() -> bool:
    print("\n" + "=" * 60)
    print("【阶段 2】数据库连接测试")
    print("=" * 60)

    cfg = _get_config()
    if not cfg["decrypt_output_dir"]:
        print("  ❌ WECHAT_DECRYPT_OUTPUT_DIR 未配置，跳过")
        return False

    try:
        from edge_client.services.wx_db_service import WxDbService
        svc = WxDbService(cfg["decrypt_output_dir"])
        stats = svc.stats()
        print(f"  数据库状态: {json.dumps(stats, ensure_ascii=False, indent=4)}")

        # 测试联系人查询
        contacts = svc.get_contacts()
        print(f"\n  联系人列表（前5条）:")
        for c in contacts[:5]:
            print(f"    - {c.wxid} | 昵称: {c.nickname} | 备注: {c.remark} | 群: {c.is_group}")

        if not contacts:
            print("  [WARN] 联系人列表为空，可能解密未完成或目录配置有误")
            return False

        print(f"\n  [OK] 数据库连接正常，共 {len(contacts)} 条联系人（已截断显示）")
        return True

    except Exception as e:
        print(f"  ❌ 数据库连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────
# 阶段 3：接收消息测试（监听 10 秒）
# ─────────────────────────────────────────────

def stage_recv(duration_s: float = 10.0) -> bool:
    print("\n" + "=" * 60)
    print(f"【阶段 3】接收消息测试（监听 {duration_s:.0f} 秒，请在微信发一条消息）")
    print("=" * 60)

    cfg = _get_config()
    if not cfg["decrypt_output_dir"]:
        print("  ❌ WECHAT_DECRYPT_OUTPUT_DIR 未配置，跳过")
        return False

    received: list = []

    try:
        from edge_client.services.wx_db_service import WxDbService
        from edge_client.services.wx_watcher import WxDbWatcher

        svc = WxDbService(cfg["decrypt_output_dir"])

        def on_msgs(msgs):
            for m in msgs:
                received.append(m)
                print(f"  📨 收到消息: from={m.from_wxid} conv={m.conversation_id} "
                      f"type={m.msg_type} content={m.content[:60]!r}")

        watcher = WxDbWatcher(svc, on_msgs, poll_interval_s=cfg["poll_interval_s"])
        mode = watcher.start()
        print(f"  监听模式: {mode}，等待 {duration_s:.0f} 秒...")
        print("  ⏳ 请现在在微信给自己或任意联系人发一条消息...")

        time.sleep(duration_s)
        watcher.stop()

        if received:
            print(f"\n  ✅ 成功接收到 {len(received)} 条消息")
            return True
        else:
            print(f"\n  ⚠️  {duration_s:.0f} 秒内未收到新消息（可能没有新消息，或解密目录未更新）")
            return True  # 不算失败，可能只是没有新消息

    except Exception as e:
        print(f"  ❌ 接收测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────
# 阶段 4：发送消息测试
# ─────────────────────────────────────────────

def stage_send(to_conversation: str, message: str) -> bool:
    print("\n" + "=" * 60)
    print(f"【阶段 4】发送消息测试")
    print(f"  目标会话: {to_conversation}")
    print(f"  消息内容: {message!r}")
    print("=" * 60)

    cfg = _get_config()
    if not cfg["decrypt_output_dir"]:
        print("  ❌ WECHAT_DECRYPT_OUTPUT_DIR 未配置，跳过")
        return False

    try:
        from edge_client.services.wx_db_service import WxDbService
        from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter
        from edge_client.gateway.models import MsgType, Platform, UnifiedReply

        svc = WxDbService(cfg["decrypt_output_dir"])

        # 先查一下联系人是否存在
        contact = svc.get_contact(to_conversation)
        if contact:
            print(f"  联系人信息: wxid={contact.wxid} 昵称={contact.nickname} 备注={contact.remark}")
        else:
            print(f"  ⚠️  联系人数据库中未找到 {to_conversation}，将尝试直接搜索")

        adapter = WeChatAdapter(svc, self_wxid=cfg["self_wxid"] or None)

        reply = UnifiedReply(
            platform=Platform.WECHAT,
            conversation_id=to_conversation,
            msg_type=MsgType.TEXT,
            content=message,
        )

        print("\n  开始发送...")
        t0 = time.time()
        success = adapter.send_reply(reply)
        elapsed = time.time() - t0

        health = adapter.health()
        last = health.get("last_outbound", {})
        print(f"\n  发送结果: {'✅ 成功' if success else '❌ 失败'}")
        print(f"  耗时: {elapsed:.2f}s")
        print(f"  详情: {last.get('detail', '')}")
        print(f"  统计: {health.get('stats', {})}")

        return success

    except Exception as e:
        print(f"  ❌ 发送测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


# ─────────────────────────────────────────────
# 阶段 5：窗口控件诊断
# ─────────────────────────────────────────────

def stage_diag() -> bool:
    print("\n" + "=" * 60)
    print("【阶段 5】微信窗口控件诊断")
    print("=" * 60)

    try:
        from pywinauto import Application, Desktop
    except ImportError:
        print("  ❌ pywinauto 未安装")
        return False

    # 连接微信
    app = None
    for method, kwargs in [
        ("path", {"path": "Weixin.exe"}),
        ("title", {"title_re": "微信"}),
    ]:
        try:
            app = Application(backend="uia").connect(**kwargs)
            print(f"  ✅ 连接微信成功 (方式: {method})")
            break
        except Exception as e:
            print(f"  ⚠️  连接方式 {method} 失败: {e}")

    if app is None:
        print("  ❌ 无法连接微信，请确认微信已启动")
        return False

    try:
        wnd = app.top_window()
        print(f"\n  窗口标题: {wnd.window_text()!r}")
        print(f"  窗口可见: {wnd.is_visible()}")
        rect = wnd.rectangle()
        print(f"  窗口位置: left={rect.left} top={rect.top} right={rect.right} bottom={rect.bottom}")
        print(f"  窗口尺寸: {rect.right - rect.left} x {rect.bottom - rect.top}")
    except Exception as e:
        print(f"  ⚠️  获取窗口信息失败: {e}")

    # 打印所有 Edit 控件
    print("\n  ── Edit 控件列表 ──")
    try:
        edits = list(wnd.descendants(control_type="Edit"))
        print(f"  共找到 {len(edits)} 个 Edit 控件:")
        for i, ctrl in enumerate(edits):
            try:
                name = ctrl.window_text()
                auto_id = getattr(ctrl.element_info, "automation_id", "")
                rect = ctrl.rectangle()
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                print(f"    [{i}] name={name!r:30s} auto_id={auto_id!r:20s} size={w}x{h}")
            except Exception as e:
                print(f"    [{i}] 获取失败: {e}")
    except Exception as e:
        print(f"  ❌ 获取 Edit 控件失败: {e}")

    # 打印所有 Document 控件
    print("\n  ── Document 控件列表 ──")
    try:
        docs = list(wnd.descendants(control_type="Document"))
        print(f"  共找到 {len(docs)} 个 Document 控件:")
        for i, ctrl in enumerate(docs):
            try:
                name = ctrl.window_text()
                auto_id = getattr(ctrl.element_info, "automation_id", "")
                rect = ctrl.rectangle()
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                print(f"    [{i}] name={name!r:30s} auto_id={auto_id!r:20s} size={w}x{h}")
            except Exception as e:
                print(f"    [{i}] 获取失败: {e}")
    except Exception as e:
        print(f"  ❌ 获取 Document 控件失败: {e}")

    # 控件数量统计
    print("\n  ── 控件数量统计 ──")
    for ct in ("Edit", "Document", "List", "ListItem", "Button", "Pane", "Text", "TabItem"):
        try:
            count = len(list(wnd.descendants(control_type=ct)))
            print(f"    {ct:12s}: {count}")
        except Exception:
            print(f"    {ct:12s}: 获取失败")

    return True


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="微信端到端测试")
    parser.add_argument(
        "--stage",
        choices=["env", "db", "recv", "send", "diag", "all"],
        default="all",
        help="要运行的测试阶段",
    )
    parser.add_argument("--to", default="filehelper", help="发送目标 conversation_id（默认: filehelper 文件传输助手）")
    parser.add_argument("--msg", default="[测试消息] Natsume AI 发送测试 ✅", help="发送的消息内容")
    parser.add_argument("--recv-duration", type=float, default=10.0, help="接收监听时长（秒）")
    args = parser.parse_args()

    results: dict[str, bool] = {}

    if args.stage in ("env", "all"):
        results["env"] = stage_env()

    if args.stage in ("db", "all"):
        results["db"] = stage_db()

    if args.stage in ("recv", "all"):
        results["recv"] = stage_recv(args.recv_duration)

    if args.stage in ("send", "all"):
        results["send"] = stage_send(args.to, args.msg)

    if args.stage in ("diag", "all"):
        results["diag"] = stage_diag()

    # 汇总
    print("\n" + "=" * 60)
    print("【测试汇总】")
    print("=" * 60)
    all_ok = True
    for stage, ok in results.items():
        status = "✅ 通过" if ok else "❌ 失败"
        print(f"  {stage:8s}: {status}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("  🎉 所有测试通过！")
    else:
        print("  ⚠️  部分测试失败，请根据上方日志排查问题。")
    print()


if __name__ == "__main__":
    main()
