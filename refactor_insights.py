import re

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = "{error && <div className=\"card p-4 text-red-500 text-sm\">加载失败：{error}</div>}"
end_marker = "    </div>\n  )\n}\n\n// ===== TODO-47: 路由决策统计子组件 ====="

start_idx = content.find(start_marker) + len(start_marker)
end_idx = content.find(end_marker)

sections_content = content[start_idx:end_idx]

def extract_section(marker):
    s_idx = sections_content.find(marker)
    if s_idx == -1: return ""
    next_idx = sections_content.find("{/* =====", s_idx + 10)
    if next_idx == -1:
        return sections_content[s_idx:].strip()
    return sections_content[s_idx:next_idx].strip()

sec_gov = extract_section("{/* ===== P1: Governor 决策漏斗 ===== */}")
sec_err = extract_section("{/* ===== P1: 全局错误追踪 ===== */}")
sec_subsys = extract_section("{/* ===== P2: 子系统健康矩阵 ===== */}")
sec_agent = extract_section("{/* ===== P3: Agent Loop 统计 ===== */}")
sec_vts = extract_section("{/* ===== TODO-38: VTS 通道统计 ===== */}")
sec_qa = extract_section("{/* ===== TODO-40: QA 违规统计 ===== */}")
sec_llm = extract_section("{/* ===== TODO-41: LLM 调用成本统计 ===== */}")
sec_memory = extract_section("{/* ===== TODO-32: Memory 子系统统计 ===== */}")

new_return_content = f"""
      {{isRoot ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-6">
          {{INSIGHT_GROUPS.map(g => (
            <div key={{g.id}} className="card p-5 cursor-pointer hover:shadow-md transition-shadow" onClick={{() => navigate(`/insights/${{g.id}}`)}}>
              <div className="flex items-center gap-3 mb-2">
                <div className={{`p-2 rounded-lg ${{g.bg}} ${{g.color}}`}}>
                  <g.icon size={{20}} />
                </div>
                <h3 className="text-lg font-semibold text-gray-800">{{g.title}}</h3>
              </div>
              <p className="text-sm text-gray-500">{{g.desc}}</p>
            </div>
          ))}}
        </div>
      ) : (
        <div className="space-y-8 mt-6">
          <button className="btn-secondary" onClick={{() => navigate('/insights')}}>
            <ArrowLeft size={{16}} /> 返回分组
          </button>

          {{currentGroup === 'global' && (
            <>
              {sec_gov}
              {sec_err}
              <ErrorHeatmapSection windowMinutes={{windowMinutes}} />
              <ChainStatsSection windowMinutes={{windowMinutes}} />
              <FailureStatsSection windowMinutes={{windowMinutes}} />
            </>
          )}}

          {{currentGroup === 'memory' && (
            <>
              {sec_subsys}
              {sec_memory}
              <EpisodicStoreStatsSection windowMinutes={{windowMinutes}} />
              <ContextCompressorStatsSection windowMinutes={{windowMinutes}} />
            </>
          )}}

          {{currentGroup === 'llm' && (
            <>
              {sec_agent}
              {sec_llm}
              <RouteStatsSection windowMinutes={{windowMinutes}} />
              <PromptBuilderStatsSection windowMinutes={{windowMinutes}} />
              <RecallPackSection windowMinutes={{windowMinutes}} />
            </>
          )}}

          {{currentGroup === 'safety' && (
            <>
              {sec_qa}
              <PermissionStatsSection windowMinutes={{windowMinutes}} />
              <MemoryGuardStatsSection windowMinutes={{windowMinutes}} />
              <UIAssertionSection windowMinutes={{windowMinutes}} />
            </>
          )}}

          {{currentGroup === 'state' && (
            <>
              <StateArbiterStatsSection windowMinutes={{windowMinutes}} />
              <CanonProposalStatsSection windowMinutes={{windowMinutes}} />
            </>
          )}}

          {{currentGroup === 'vts' && (
            <>
              {sec_vts}
            </>
          )}}
        </div>
      )}}
"""

new_content = content[:start_idx] + new_return_content + "\n" + content[end_idx:]

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Refactoring complete.")
