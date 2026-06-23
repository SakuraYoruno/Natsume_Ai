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
import { LIFE_COPY, NAV_LABELS } from '../../../shared/content/terminology.js'

const ICONS = {
  cockpit: Sparkles,
  lifeTimeline: Activity,
  pathologies: Bell,
  emotion: Waves,
  relationship: Heart,
  memory: Database,
  lifeRuntime: Activity,
  runtimeMode: Orbit,
  innerWorld: MoonStar,
  responsePolicy: Wand2,
  selfModel: Brain,
}

const ITEMS = LIFE_COPY.navItems.map((item) => ({
  ...item,
  label: NAV_LABELS[item.labelKey] || item.labelKey,
  icon: ICONS[item.labelKey] || Sparkles,
}))

export default function DigitalLifeNav() {
  return (
    <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">
          {LIFE_COPY.lifeNavTitle}
        </div>
        <div className="text-xs text-gray-400">{LIFE_COPY.lifeNavHint}</div>
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
