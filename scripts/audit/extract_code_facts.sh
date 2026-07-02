#!/bin/bash
# scripts/audit/extract_code_facts.sh
# 提取代码事实基线，输出到 stdout
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "=== CODE FACTS BASELINE $(date +%Y-%m-%d) ==="
echo ""
echo "## Host Brain 总览"
echo "- Total .py files: $(find host_brain -name '*.py' | wc -l)"
echo ""

echo "## 子目录规模"
for dir in app persona persona/surface persona/surface/scoring pentest pentest/agent pentest/adversarial pentest/swarm pentest/online pentest/tools pentest/data pentest/report pentest/utils runtime tribe memory memory/life_memory governor skills/impl; do
  if [ -d "host_brain/$dir" ]; then
    files=$(find "host_brain/$dir" -maxdepth 1 -name '*.py' | wc -l)
    lines=$(find "host_brain/$dir" -maxdepth 1 -name '*.py' -exec cat {} + 2>/dev/null | wc -l)
    echo "- $dir: $files files, $lines lines"
  fi
done
echo ""

echo "## Pentest agent/ 完整文件清单 (按行数降序)"
echo '```'
wc -l host_brain/pentest/agent/*.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "## Persona surface/ + scoring/ 完整文件清单 (按行数降序)"
echo '```'
wc -l host_brain/persona/surface/*.py host_brain/persona/surface/scoring/*.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "## Pentest 根目录关键大文件 (top 20 by lines)"
echo '```'
wc -l host_brain/pentest/*.py 2>/dev/null | sort -rn | head -20
echo '```'
echo ""

echo "## Tribe agent 文件规模 (按行数降序)"
echo '```'
wc -l host_brain/tribe/*_agent.py host_brain/tribe/tribe_state.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "## 路由注册 (router_registry.py include_router 调用)"
echo '```'
grep -n "include_router\|\.include_router" host_brain/app/router_registry.py | head -40
echo '```'
echo ""

echo "## Digital Life 端点"
echo '```'
grep -n "@router\.\(get\|post\|put\|delete\)\|async def \|def " host_brain/app/digital_life_routes.py 2>/dev/null | head -40
echo '```'
echo ""

echo "## Memory life_memory/ 子包文件"
echo '```'
wc -l host_brain/memory/life_memory/*.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "## Runtime 文件"
echo '```'
wc -l host_brain/runtime/*.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "## Governor 文件"
echo '```'
wc -l host_brain/governor/*.py 2>/dev/null | sort -rn
echo '```'
echo ""

echo "=== END BASELINE ==="
