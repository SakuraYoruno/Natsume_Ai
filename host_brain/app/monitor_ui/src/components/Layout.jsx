import React, { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  BarChart2,
  Bell,
  BookOpen,
  Bot,
  Brain,
  Clock,
  Database,
  FileText,
  GitBranch,
  Heart,
  LayoutDashboard,
  Lightbulb,
  Menu,
  MessageSquare,
  MoonStar,
  Package,
  PauseCircle,
  Radio,
  RefreshCw,
  ScrollText,
  SendHorizontal,
  Settings,
  Smile,
  Sparkles,
  Wand2,
  Wrench,
  X,
  Zap,
} from 'lucide-react'
import useAppStore from '../store/useAppStore.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { buildDigitalLifeAlerts } from '../utils/runtimeHealth.js'

const NAV_SECTIONS = [
  {
    title: '数字生命',
    items: [
      { to: '/digital-life', icon: Zap, label: '总览' },
      { to: '/emotion', icon: Smile, label: '情绪' },
      { to: '/bond', icon: Heart, label: '关系' },
      { to: '/memory', icon: Database, label: '记忆' },
      { to: '/runtime-mode', icon: Zap, label: '存在态' },
      { to: '/inner-world', icon: MoonStar, label: '内在世界' },
      { to: '/response-policy', icon: Sparkles, label: '回复策略' },
      { to: '/self-model', icon: Brain, label: '自我模型' },
    ],
  },
  {
    title: '运营',
    items: [
      { to: '/prompt-config', icon: FileText, label: '提示词' },
      { to: '/persona-config', icon: Wand2, label: '人格文案' },
      { to: '/system', icon: Settings, label: '系统底座' },
      { to: '/canon', icon: BookOpen, label: '设定管理' },
    ],
  },
  {
    title: '观测',
    items: [
      { to: '/overview', icon: LayoutDashboard, label: '运行总览' },
      { to: '/sessions', icon: MessageSquare, label: '会话' },
      { to: '/trace', icon: GitBranch, label: '链路' },
      { to: '/metrics', icon: BarChart2, label: '指标' },
      { to: '/insights', icon: Lightbulb, label: '洞察' },
      { to: '/chat-debug', icon: SendHorizontal, label: 'Chat 调试' },
      { to: '/alerts', icon: Bell, label: '告警' },
      { to: '/logs', icon: ScrollText, label: '日志' },
    ],
  },
  {
    title: '基础设施',
    items: [
      { to: '/gateway', icon: Radio, label: '网关' },
      { to: '/skills', icon: Package, label: '技能' },
      { to: '/tools', icon: Wrench, label: '工具' },
      { to: '/automation', icon: Clock, label: '自动化' },
      { to: '/agent', icon: Bot, label: 'Agent Loop' },
    ],
  },
]

const INTERVAL_OPTIONS = [15, 30, 60]

const BADGE_STYLES = {
  critical: 'border-red-200 bg-red-50 text-red-700',
  warning: 'border-orange-200 bg-orange-50 text-orange-700',
  info: 'border-blue-200 bg-blue-50 text-blue-700',
}

function NavBadge({ value, severity = 'info' }) {
  if (!value) return null
  return (
    <span className={`inline-flex min-w-5 items-center justify-center rounded-full border px-1.5 py-0.5 text-[11px] font-semibold ${BADGE_STYLES[severity] || BADGE_STYLES.info}`}>
      {value}
    </span>
  )
}

function NavPill({ to, icon: Icon, label, onClick, badge = null }) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
          isActive ? 'bg-primary-50 text-primary' : 'text-gray-600 hover:bg-gray-100 hover:text-gray-800'
        }`
      }
    >
      <Icon size={15} />
      {label}
      {badge ? <NavBadge value={badge.value} severity={badge.severity} /> : null}
    </NavLink>
  )
}

export default function Layout() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const setPollingEnabled = useAppStore((state) => state.setPollingEnabled)
  const setPollingInterval = useAppStore((state) => state.setPollingInterval)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const location = useLocation()
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats })

  useEffect(() => {
    setMobileMenuOpen(false)
  }, [location.pathname])

  const runtimeAlertBadge = runtimeAlerts.length > 0
    ? { value: runtimeAlerts.length, severity: runtimeAlerts[0]?.severity || 'info' }
    : null

  const sectionBadges = {
    数字生命: runtimeAlertBadge,
  }

  const itemBadges = {
    '/alerts': runtimeAlertBadge,
  }

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/95 shadow-sm backdrop-blur">
        <div className="mx-auto w-full space-y-3 px-4 py-3 sm:px-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <button
                className="rounded p-1.5 text-gray-500 transition-colors hover:bg-gray-100 lg:hidden"
                onClick={() => setMobileMenuOpen((value) => !value)}
                aria-label="切换导航菜单"
              >
                {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
              </button>
              <div>
                <div className="text-lg font-bold tracking-tight text-primary">Natsume AI</div>
                <div className="hidden text-xs text-gray-400 sm:block">数字生命控制台</div>
              </div>
            </div>

            <div className="flex items-center gap-2 text-sm">
              <button
                className={`flex items-center gap-1.5 rounded border px-2.5 py-1.5 text-xs font-medium transition-colors ${
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
                className="rounded border border-gray-300 px-1.5 py-1.5 text-xs text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary-200"
                value={pollingInterval}
                onChange={(event) => setPollingInterval(Number(event.target.value))}
                title="自动刷新间隔"
              >
                {INTERVAL_OPTIONS.map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {seconds}s
                  </option>
                ))}
              </select>
            </div>
          </div>

          <nav className="hidden flex-wrap gap-3 lg:flex" role="navigation" aria-label="主导航">
            {NAV_SECTIONS.map((section) => (
              <div key={section.title} className="rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2">
                <div className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                  <span>{section.title}</span>
                  {sectionBadges[section.title] ? (
                    <NavBadge
                      value={sectionBadges[section.title].value}
                      severity={sectionBadges[section.title].severity}
                    />
                  ) : null}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {section.items.map((item) => (
                    <NavPill key={item.to} {...item} badge={itemBadges[item.to]} />
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </div>
      </header>

      {mobileMenuOpen ? (
        <>
          <div className="fixed inset-0 z-40 bg-black/30 lg:hidden" onClick={() => setMobileMenuOpen(false)} />
          <nav
            className="fixed bottom-0 left-0 top-[72px] z-50 w-72 overflow-y-auto border-r border-gray-200 bg-white shadow-xl lg:hidden"
            role="navigation"
            aria-label="移动端导航"
          >
            <div className="space-y-3 p-3">
              {NAV_SECTIONS.map((section) => (
                <div key={section.title} className="rounded-xl border border-gray-200 bg-gray-50 p-2">
                  <div className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                    <span>{section.title}</span>
                    {sectionBadges[section.title] ? (
                      <NavBadge
                        value={sectionBadges[section.title].value}
                        severity={sectionBadges[section.title].severity}
                      />
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    {section.items.map((item) => (
                      <NavPill
                        key={item.to}
                        {...item}
                        badge={itemBadges[item.to]}
                        onClick={() => setMobileMenuOpen(false)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </nav>
        </>
      ) : null}

      <main className="mx-auto flex w-full min-w-0 flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
