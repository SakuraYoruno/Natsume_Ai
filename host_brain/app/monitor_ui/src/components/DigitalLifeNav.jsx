import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  Activity,
  Brain,
  Bell,
  Database,
  Heart,
  MoonStar,
  Orbit,
  Sparkles,
  Wand2,
  Waves,
} from 'lucide-react'

const ITEMS = [
  { to: '/digital-life', label: '总览', icon: Sparkles },
  { to: '/life-timeline', label: '生命时间轴', icon: Activity },
  { to: '/pathologies', label: '病灶中心', icon: Bell },
  { to: '/emotion', label: '情绪', icon: Waves },
  { to: '/bond', label: '关系', icon: Heart },
  { to: '/memory', label: '记忆', icon: Database },
  { to: '/runtime-mode', label: '存在态', icon: Orbit },
  { to: '/inner-world', label: '内在世界', icon: MoonStar },
  { to: '/response-policy', label: '回复策略', icon: Sparkles },
  { to: '/self-model', label: '自我模型', icon: Brain },
  { to: '/persona-config', label: '人格文案', icon: Wand2 },
]

export default function DigitalLifeNav() {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-3 shadow-sm">
      <div className="mb-2 text-xs font-medium tracking-wide text-gray-400">
        数字生命体导航
      </div>
      <div className="flex flex-wrap gap-2">
        {ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                isActive
                  ? 'border-primary-200 bg-primary-50 text-primary'
                  : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100 hover:text-gray-800'
              }`
            }
          >
            <Icon size={13} />
            {label}
          </NavLink>
        ))}
      </div>
    </div>
  )
}
