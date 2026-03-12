import React from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard,
  MessageSquare,
  GitBranch,
  BarChart2,
  Lightbulb,
  BookOpen,
  RefreshCw,
  PauseCircle,
} from 'lucide-react'
import useAppStore from '../store/useAppStore.js'

const NAV_ITEMS = [
  { to: '/overview', icon: LayoutDashboard, label: 'Overview' },
  { to: '/sessions',  icon: MessageSquare,  label: 'Sessions'  },
  { to: '/trace',     icon: GitBranch,      label: 'Trace'     },
  { to: '/metrics',   icon: BarChart2,      label: 'Metrics'   },
  { to: '/insights',  icon: Lightbulb,      label: 'Insights'  },
  { to: '/canon',     icon: BookOpen,       label: 'Canon'     },
]

const INTERVAL_OPTIONS = [15, 30, 60]

export default function Layout() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const setPollingEnabled  = useAppStore((s) => s.setPollingEnabled)
  const setPollingInterval = useAppStore((s) => s.setPollingInterval)

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

          {/* 轮询控制 */}
          <div className="flex items-center gap-2 text-sm">
            <button
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded border transition-colors text-xs font-medium ${
                pollingEnabled
                  ? 'border-green-300 bg-green-50 text-green-700 hover:bg-green-100'
                  : 'border-gray-300 bg-gray-50 text-gray-500 hover:bg-gray-100'
              }`}
              onClick={() => setPollingEnabled(!pollingEnabled)}
              title={pollingEnabled ? '点击暂停自动刷新' : '点击开启自动刷新'}
            >
              {pollingEnabled
                ? <RefreshCw size={12} className="animate-spin" />
                : <PauseCircle size={12} />
              }
              {pollingEnabled ? '轮询中' : '已暂停'}
            </button>
            <select
              className="text-xs border border-gray-300 rounded px-1.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary-200 text-gray-600"
              value={pollingInterval}
              onChange={(e) => setPollingInterval(Number(e.target.value))}
              title="自动刷新间隔"
            >
              {INTERVAL_OPTIONS.map((s) => (
                <option key={s} value={s}>{s}s</option>
              ))}
            </select>
          </div>
        </div>
      </header>

      {/* 页面内容 */}
      <main className="flex-1 max-w-screen-2xl mx-auto w-full px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
