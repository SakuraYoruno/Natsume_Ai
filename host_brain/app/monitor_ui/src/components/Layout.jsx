import React, { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  MessageSquare,
  GitBranch,
  BarChart2,
  Lightbulb,
  BookOpen,
  Heart,
  Wrench,
  Clock,
  Bot,
  RefreshCw,
  PauseCircle,
  Radio,
  Package,
  SendHorizontal,
  Bell,
  ScrollText,
  Smile,
  Database,
  Settings,
  Zap,
  FileText,
  Wand2,
  Menu,
  X,
  MoonStar,
  Sparkles,
  Brain,
} from 'lucide-react'
import useAppStore from '../store/useAppStore.js'

const NAV_SECTIONS = [
  {
    title: 'Life',
    items: [
      { to: '/digital-life', icon: Zap, label: '总览' },
      { to: '/emotion', icon: Smile, label: '情绪' },
      { to: '/bond', icon: Heart, label: 'Bond' },
      { to: '/memory', icon: Database, label: '记忆' },
      { to: '/runtime-mode', icon: Zap, label: '模式' },
      { to: '/inner-world', icon: MoonStar, label: '内心世界' },
      { to: '/response-policy', icon: Sparkles, label: '回应策略' },
      { to: '/self-model', icon: Brain, label: '自我模型' },
    ],
  },
  {
    title: 'Operate',
    items: [
      { to: '/prompt-config', icon: FileText, label: 'Prompts' },
      { to: '/persona-config', icon: Wand2, label: 'Persona' },
      { to: '/system', icon: Settings, label: '系统底座' },
      { to: '/canon', icon: BookOpen, label: 'Canon' },
    ],
  },
  {
    title: 'Observe',
    items: [
      { to: '/overview', icon: LayoutDashboard, label: 'Ops' },
      { to: '/sessions', icon: MessageSquare, label: 'Sessions' },
      { to: '/trace', icon: GitBranch, label: 'Trace' },
      { to: '/metrics', icon: BarChart2, label: 'Metrics' },
      { to: '/insights', icon: Lightbulb, label: 'Insights' },
      { to: '/chat-debug', icon: SendHorizontal, label: 'Chat' },
      { to: '/alerts', icon: Bell, label: 'Alerts' },
      { to: '/logs', icon: ScrollText, label: 'Logs' },
    ],
  },
  {
    title: 'Infra',
    items: [
      { to: '/gateway', icon: Radio, label: 'Gateway' },
      { to: '/skills', icon: Package, label: 'Skills' },
      { to: '/tools', icon: Wrench, label: 'Tools' },
      { to: '/automation', icon: Clock, label: 'Auto' },
      { to: '/agent', icon: Bot, label: 'Agent' },
    ],
  },
]

const INTERVAL_OPTIONS = [15, 30, 60]

function NavPill({ to, icon: Icon, label, onClick }) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
          isActive
            ? 'bg-primary-50 text-primary'
            : 'text-gray-600 hover:bg-gray-100 hover:text-gray-800'
        }`
      }
    >
      <Icon size={15} />
      {label}
    </NavLink>
  )
}

export default function Layout() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const setPollingEnabled = useAppStore((s) => s.setPollingEnabled)
  const setPollingInterval = useAppStore((s) => s.setPollingInterval)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setMobileMenuOpen(false)
  }, [location.pathname])

  return (
    <div className="h-full flex flex-col bg-gray-50">
      <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/95 shadow-sm backdrop-blur">
        <div className="w-full mx-auto px-4 sm:px-6 py-3 space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <button
                className="lg:hidden p-1.5 rounded text-gray-500 hover:bg-gray-100 transition-colors"
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                aria-label="切换导航菜单"
              >
                {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
              </button>
              <div>
                <div className="text-primary font-bold text-lg tracking-tight">Natsume AI</div>
                <div className="hidden sm:block text-xs text-gray-400">数字生命控制台</div>
              </div>
            </div>

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
                {pollingEnabled ? <RefreshCw size={12} className="animate-spin" /> : <PauseCircle size={12} />}
                {pollingEnabled ? '轮询中' : '已暂停'}
              </button>
              <select
                className="text-xs border border-gray-300 rounded px-1.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary-200 text-gray-600"
                value={pollingInterval}
                onChange={(e) => setPollingInterval(Number(e.target.value))}
                title="自动刷新间隔"
              >
                {INTERVAL_OPTIONS.map((seconds) => (
                  <option key={seconds} value={seconds}>{seconds}s</option>
                ))}
              </select>
            </div>
          </div>

          <nav className="hidden lg:flex flex-wrap gap-3" role="navigation" aria-label="主导航">
            {NAV_SECTIONS.map((section) => (
              <div key={section.title} className="rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2">
                <div className="px-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                  {section.title}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {section.items.map((item) => (
                    <NavPill key={item.to} {...item} />
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </div>
      </header>

      {mobileMenuOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-black/30 lg:hidden" onClick={() => setMobileMenuOpen(false)} />
          <nav
            className="fixed top-[72px] left-0 bottom-0 z-50 w-72 bg-white border-r border-gray-200 shadow-xl overflow-y-auto lg:hidden"
            role="navigation"
            aria-label="移动端导航"
          >
            <div className="p-3 space-y-3">
              {NAV_SECTIONS.map((section) => (
                <div key={section.title} className="rounded-xl border border-gray-200 bg-gray-50 p-2">
                  <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                    {section.title}
                  </div>
                  <div className="space-y-1">
                    {section.items.map((item) => (
                      <NavPill key={item.to} {...item} onClick={() => setMobileMenuOpen(false)} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </nav>
        </>
      )}

      <main className="flex-1 w-full min-w-0 mx-auto px-4 sm:px-6 lg:px-8 py-6 flex flex-col">
        <Outlet />
      </main>
    </div>
  )
}
