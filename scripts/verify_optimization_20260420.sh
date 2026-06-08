#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# OPTIMIZATION-PLAN-20260420 / §17：一键自检脚本
# ─────────────────────────────────────────────────────
# 覆盖 OPT-1 / OPT-2 / OPT-3 / OPT-5 / OPT-6 / OPT-8 / OPT-10
# 默认在 CI 与本地开发机都可运行。
# 任意步骤失败直接 exit 1。
#
# 用法：
#     bash scripts/verify_optimization_20260420.sh
#     PYTHON=D:/Natsume_Ai_repo/.venv/Scripts/python.exe bash scripts/verify_optimization_20260420.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"

echo "════════════════════════════════════════════════════════════════════"
echo " OPTIMIZATION_PLAN_20260420 自检（repo=$ROOT）"
echo "════════════════════════════════════════════════════════════════════"

# --- OPT-1：.idea IDE 个人文件不得出现在 git index ---
echo ""
echo "[OPT-1] 检查 git index 是否污染了 .idea 下 IDE 个人文件..."
idea_tracked="$(cd "$ROOT" && git ls-files | grep -E '\.idea/' || true)"
if [ -n "$idea_tracked" ]; then
  echo "  ✗ 以下 IDE 个人文件被纳入 git index，应从 .gitignore 与 index 中清理："
  echo "$idea_tracked" | sed 's/^/    /'
  exit 1
fi
echo "  [ok] git index clean of .idea personal files"

# --- OPT-2：pytest.ini 已规范化 ---
echo ""
echo "[OPT-2] 检查 pytest.ini markers / testpaths 规范..."
for key in "testpaths" "strict-markers" "markers"; do
  if ! grep -q "$key" "$ROOT/pytest.ini"; then
    echo "  ✗ pytest.ini 缺少关键项：$key"
    exit 1
  fi
done
echo "  [ok] pytest.ini contains testpaths / strict-markers / markers"

# --- OPT-3：soft_fail 基建可导入且暴露 API ---
echo ""
echo "[OPT-3] 检查 host_brain.core.soft_fail 契约..."
PYTHONIOENCODING=utf-8 "$PYTHON" - <<'PY'
from host_brain.core.soft_fail import soft_fail, snapshot_soft_failures, reset_soft_failures
reset_soft_failures()
import logging
logging.getLogger('host_brain.soft_fail').setLevel(logging.CRITICAL)
with soft_fail('verify_opt3', fallback='DEFAULT') as box:
    raise RuntimeError('forced')
assert box[0] == 'DEFAULT', 'fallback not applied'
snap = snapshot_soft_failures()
assert 'verify_opt3' in snap, f'stage not recorded in snapshot: {snap!r}'
assert snap['verify_opt3'].get('RuntimeError', 0) >= 1
reset_soft_failures()
print('[OK] soft_fail contract passed')
PY

# --- OPT-5：旧 payload builder 已退役，Surface 主链入口必须存在 ---
echo ""
echo "[OPT-5] 检查 Surface reply 主链入口存在..."
if [ ! -f "$ROOT/host_brain/app/surface_reply_runtime.py" ]; then
  echo "  ✗ surface_reply_runtime.py 不存在"
  exit 1
fi
if [ ! -f "$ROOT/host_brain/persona/surface_main_chain.py" ]; then
  echo "  ✗ surface_main_chain.py 不存在"
  exit 1
fi
echo "  [ok] surface_reply_runtime.py and surface_main_chain.py exist"

# --- OPT-6：prompt lint 脚本可运行且当前仓库通过 ---
echo ""
echo "[OPT-6] 运行 scripts/lint_prompts.py..."
"$PYTHON" "$ROOT/scripts/lint_prompts.py" > /tmp/opt6_lint.out 2>&1
lint_exit=$?
if [ "$lint_exit" -ne 0 ]; then
  echo "  ✗ prompt lint 失败（退出码 $lint_exit）："
  cat /tmp/opt6_lint.out
  exit 1
fi
echo "  [ok] scripts/lint_prompts.py passed"

# --- OPT-8：ChatChannel Protocol 可导入 ---
echo ""
echo "[OPT-8] 检查 edge_client.channels.base ChatChannel Protocol..."
PYTHONIOENCODING=utf-8 "$PYTHON" - <<'PY'
from edge_client.channels.base import (
    ChatChannel, ChannelCapabilities, InboundEvent, OutboundResponse,
    ChannelHealth, adapt_response_to_capabilities,
)
assert hasattr(ChatChannel, 'capabilities')
print('[OK] ChatChannel Protocol + helpers importable')
PY

# --- OPT-10：shared 模块体积 lint 规则已注册 ---
echo ""
echo "[OPT-10] 运行 shared 模块 lint 契约..."
(
  cd "$ROOT"
  "$PYTHON" -m pytest tests/contract/test_ci_gate_contract.py::TestSharedModuleLint \
    -q --no-header
) > /tmp/opt10_lint.out 2>&1 || {
  echo "  ✗ OPT-10 lint 契约失败："
  cat /tmp/opt10_lint.out
  exit 1
}
echo "  [ok] shared module lint passed"

# --- 综合：契约 + 单测快速冒烟 ---
echo ""
echo "[冒烟] 运行 OPT-6 / OPT-8 契约与单测..."
(
  cd "$ROOT"
  "$PYTHON" -m pytest \
    tests/contract/test_prompt_lint_contract.py \
    tests/unit/test_chat_channel_protocol.py \
    tests/contract/test_ci_gate_contract.py \
    -q --no-header
) > /tmp/opt_smoke.out 2>&1 || {
  echo "  ✗ 冒烟测试失败："
  cat /tmp/opt_smoke.out
  exit 1
}
grep -E "passed|failed" /tmp/opt_smoke.out | tail -1 | sed 's/^/  /'

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo " ALL OPTIMIZATIONS VERIFIED [ok]"
echo "════════════════════════════════════════════════════════════════════"
