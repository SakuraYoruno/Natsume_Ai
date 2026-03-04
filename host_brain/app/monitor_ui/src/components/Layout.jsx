import React from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard,
  MessageSquare,
  GitBranch,
  BarChart2,
} from 'lucide-react'

const NAV_ITEMS = [
  { to: '/overview', icon: LayoutDashboard, label: 'Overview' },
  { to: '/sessions',  icon: MessageSquare,  label: 'Sessions'  },
  { to: '/trace',     icon: GitBranch,      label: 'Trace'     },
  { to: '/metrics',   icon: BarChart2,      label: 'Metrics'   },
]

export default function Layout() {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      {/* 顶部导航栏 */}
      <header className="sticky top-0 z-50 bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-screen-2xl mx-auto px-6 h-14 flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <span className="text-primary font-bold text-lg tracking-tight">Natsume AI</span>
            <span className="text-gray-400 text-sm">监控面板</span>
          </div>

          {/* 导航 */}
          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors duration-150 ` +
                  (isActive
                    ? 'bg-primary-50 text-primary'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-800')
                }
              >
                <Icon size={15} />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      {/* 页面内容 */}
      <main className="flex-1 max-w-screen-2xl mx-auto w-full px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
