import re

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update INSIGHT_GROUPS to add more vibrant colors and gradients
groups_code = """
const INSIGHT_GROUPS = [
  { id: 'global', title: '全局与错误', icon: AlertTriangle, desc: 'Governor决策、全局错误追踪、热力图、主链路节点、失败事件', color: 'text-red-600', bg: 'bg-gradient-to-br from-red-50 to-red-100 border border-red-100', iconBg: 'bg-red-100' },
  { id: 'memory', title: '子系统与记忆', icon: Database, desc: '子系统健康、Memory统计、EpisodicStore、ContextCompressor', color: 'text-teal-700', bg: 'bg-gradient-to-br from-teal-50 to-teal-100 border border-teal-100', iconBg: 'bg-teal-100' },
  { id: 'llm', title: 'Agent与LLM', icon: Bot, desc: 'Agent Loop、LLM成本、路由决策、Prompt构建、RecallPack', color: 'text-indigo-700', bg: 'bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-100', iconBg: 'bg-indigo-100' },
  { id: 'safety', title: '安全与护栏', icon: ShieldCheck, desc: 'QA违规、权限拒绝、宪法校验、UI断言安全', color: 'text-emerald-700', bg: 'bg-gradient-to-br from-emerald-50 to-emerald-100 border border-emerald-100', iconBg: 'bg-emerald-100' },
  { id: 'state', title: '状态与规范', icon: GitMerge, desc: '事实仲裁、Canon提案', color: 'text-cyan-700', bg: 'bg-gradient-to-br from-cyan-50 to-cyan-100 border border-cyan-100', iconBg: 'bg-cyan-100' },
  { id: 'vts', title: 'VTS通道', icon: Tv, desc: 'VTS通道统计', color: 'text-pink-700', bg: 'bg-gradient-to-br from-pink-50 to-pink-100 border border-pink-100', iconBg: 'bg-pink-100' },
]
"""
content = re.sub(r"const INSIGHT_GROUPS = \[\n.*?\n\]", groups_code.strip(), content, flags=re.DOTALL)

# 2. Update the card rendering in the return block
old_card_render = """
          {INSIGHT_GROUPS.map(g => (
            <div key={g.id} className="card p-5 cursor-pointer hover:shadow-md transition-shadow" onClick={() => navigate(`/insights/${g.id}`)}>
              <div className="flex items-center gap-3 mb-2">
                <div className={`p-2 rounded-lg ${g.bg} ${g.color}`}>
                  <g.icon size={20} />
                </div>
                <h3 className="text-lg font-semibold text-gray-800">{g.title}</h3>
              </div>
              <p className="text-sm text-gray-500">{g.desc}</p>
            </div>
          ))}
"""

new_card_render = """
          {INSIGHT_GROUPS.map(g => (
            <div 
              key={g.id} 
              className={`rounded-xl p-6 cursor-pointer transition-all duration-300 hover:-translate-y-1 hover:shadow-lg ${g.bg}`} 
              onClick={() => navigate(`/insights/${g.id}`)}
            >
              <div className="flex items-start justify-between mb-4">
                <div className={`p-3 rounded-xl ${g.iconBg} ${g.color} shadow-sm`}>
                  <g.icon size={24} strokeWidth={2.5} />
                </div>
                <div className={`text-xs font-medium px-2.5 py-1 rounded-full ${g.iconBg} ${g.color} bg-opacity-50`}>
                  查看详情
                </div>
              </div>
              <h3 className="text-xl font-bold text-gray-800 mb-2 tracking-tight">{g.title}</h3>
              <p className="text-sm text-gray-600 leading-relaxed line-clamp-2">{g.desc}</p>
            </div>
          ))}
"""
content = content.replace(old_card_render.strip(), new_card_render.strip())

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'w', encoding='utf-8') as f:
    f.write(content)

print("Style refactoring complete.")
