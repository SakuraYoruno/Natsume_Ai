import re

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# Update the card rendering in the return block to make them square
old_card_render = """
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

new_card_render = """
          {INSIGHT_GROUPS.map(g => (
            <div 
              key={g.id} 
              className={`aspect-square rounded-2xl p-6 cursor-pointer transition-all duration-300 hover:-translate-y-1 hover:shadow-xl flex flex-col justify-between ${g.bg}`} 
              onClick={() => navigate(`/insights/${g.id}`)}
            >
              <div className="flex items-start justify-between">
                <div className={`p-4 rounded-2xl ${g.iconBg} ${g.color} shadow-sm`}>
                  <g.icon size={32} strokeWidth={2} />
                </div>
                <div className={`text-xs font-medium px-3 py-1.5 rounded-full ${g.iconBg} ${g.color} bg-opacity-50 flex items-center gap-1`}>
                  <span>进入</span>
                  <ArrowLeft size={12} className="rotate-180" />
                </div>
              </div>
              
              <div className="mt-auto">
                <h3 className="text-2xl font-bold text-gray-800 mb-3 tracking-tight">{g.title}</h3>
                <p className="text-sm text-gray-600 leading-relaxed line-clamp-3">{g.desc}</p>
              </div>
            </div>
          ))}
"""
content = content.replace(old_card_render.strip(), new_card_render.strip())

with open('host_brain/app/monitor_ui/src/pages/Insights.jsx', 'w', encoding='utf-8') as f:
    f.write(content)

print("Square style refactoring complete.")
