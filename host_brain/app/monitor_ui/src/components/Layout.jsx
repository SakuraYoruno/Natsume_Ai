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
import ForgeRiskActionBar from './ForgeRiskActionBar.jsx'
import useAppStore from '../store/useAppStore.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useCapabilityGapStatsQuery } from '../queries/diagnosticQueries.js'
import { preloadRoute } from '../routes/routeLoaders.js'
import { buildDigitalLifeAlerts } from '../utils/runtimeHealth.js'

const NAV_SECTIONS = [
  {
    title: '数字生命体',
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
    title: '器官级观测',
    items: [
      { to: '/overview', icon: LayoutDashboard, label: '生命体征' },
      { to: '/sessions', icon: MessageSquare, label: '神经会话' },
      { to: '/trace', icon: GitBranch, label: '神经链路' },
      { to: '/metrics', icon: BarChart2, label: '代谢指标' },
      { to: '/insights', icon: Lightbulb, label: '皮层洞察' },
      { to: '/chat-debug', icon: SendHorizontal, label: '神经调试' },
      { to: '/alerts', icon: Bell, label: '免疫告警' },
      { to: '/logs', icon: ScrollText, label: '生理日志' },
    ],
  },
  {
    title: '外周执行',
    items: [
      { to: '/gateway', icon: Radio, label: '感官网关' },
      { to: '/skills', icon: Package, label: '行为技能' },
      { to: '/tools', icon: Wrench, label: '执行工具' },
      { to: '/automation', icon: Clock, label: '节律调度' },
      { to: '/agent', icon: Bot, label: '反射回路' },
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
  const handlePrefetch = () => {
    void preloadRoute(to)
  }

  return (
    <NavLink
      to={to}
      onClick={onClick}
      onMouseEnter={handlePrefetch}
      onFocus={handlePrefetch}
      onTouchStart={handlePrefetch}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-sm font-medium transition-all ${
          isActive
            ? 'border-primary-200 bg-gradient-to-r from-primary-50 to-cyan-50 text-primary shadow-sm'
            : 'border-transparent text-gray-600 hover:border-primary-200 hover:bg-primary-50/65 hover:text-gray-800'
        }`
      }
    >
      <Icon size={15} />
      {label}
      {badge ? <NavBadge value={badge.value} severity={badge.severity} /> : null}
    </NavLink>
  )
}

function SectionFlowRail({ sections }) {
  return (
    <div className="lifeline-rail hidden h-11 items-center px-3 lg:flex">
      <div className="lifeline-wave absolute inset-0 opacity-80" />
      <div className="relative flex w-full items-center justify-between gap-2">
        {sections.map((section, index) => (
          <NavLink
            key={`flow-${section.title}`}
            to={section.items?.[0]?.to || '/digital-life'}
            className="group inline-flex min-w-0 items-center gap-2 rounded-lg px-2 py-1 transition-colors hover:bg-primary-50/60"
          >
            <span className="lifeline-node shrink-0" style={{ animationDelay: `${index * 0.35}s` }} />
            <span className="truncate text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-600 group-hover:text-gray-800">
              {section.title}
            </span>
          </NavLink>
        ))}
      </div>
    </div>
  )
}

export default function Layout() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const bumpPollingTick = useAppStore((state) => state.bumpPollingTick)
  const setPollingEnabled = useAppStore((state) => state.setPollingEnabled)
  const setPollingInterval = useAppStore((state) => state.setPollingInterval)
  const setPageVisible = useAppStore((state) => state.setPageVisible)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const location = useLocation()
  const {
    statsData: capabilityGapData,
  } = useCapabilityGapStatsQuery({
    windowMinutes: 1440,
    pollingEnabled: pollingEnabled && pageVisible,
    pollingTick,
    pollingInterval,
  })
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled: pollingEnabled && pageVisible,
    pollingInterval,
    windowMinutes: 60,
  })
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats, capabilityGapData })

  useEffect(() => {
    setMobileMenuOpen(false)
  }, [location.pathname])

  useEffect(() => {
    const syncPageVisibility = () => {
      const visible = typeof document === 'undefined' ? true : document.visibilityState !== 'hidden'
      setPageVisible(visible)
      if (visible && pollingEnabled) {
        bumpPollingTick()
      }
    }
    syncPageVisibility()
    document.addEventListener('visibilitychange', syncPageVisibility)
    return () => {
      document.removeEventListener('visibilitychange', syncPageVisibility)
    }
  }, [bumpPollingTick, pollingEnabled, setPageVisible])

  useEffect(() => {
    if (!pollingEnabled || !pageVisible) {
      return undefined
    }
    const timer = setInterval(() => {
      bumpPollingTick()
    }, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [bumpPollingTick, pageVisible, pollingEnabled, pollingInterval])

  const runtimeAlertBadge = runtimeAlerts.length > 0
    ? { value: runtimeAlerts.length, severity: runtimeAlerts[0]?.severity || 'info' }
    : null

  const sectionBadges = {
    数字生命体: runtimeAlertBadge,
  }

  const itemBadges = {
    '/alerts': runtimeAlertBadge,
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-x-hidden">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-36 top-20 h-80 w-80 rounded-full bg-primary-100/35 blur-3xl animate-pulse" />
        <div className="absolute right-[-6%] top-1/4 h-[28rem] w-[28rem] rounded-full bg-emerald-100/30 blur-3xl animate-pulse" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.08),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(245,158,11,0.06),_transparent_30%)]" />
      </div>
      <header className="sticky top-0 z-50 border-b border-primary-100/70 bg-gradient-to-r from-primary-50/80 via-white/88 to-emerald-50/70 shadow-sm backdrop-blur-xl">
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
                <div className="hidden text-xs text-gray-400 sm:block">数字生命体控制台</div>
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

          <ForgeRiskActionBar
            capabilityGapData={capabilityGapData}
            pollingEnabled={pollingEnabled}
            pollingInterval={pollingInterval}
            pollingTick={pollingTick}
          />

          <nav className="hidden flex-wrap gap-3 lg:flex" role="navigation" aria-label="主导航">
            {NAV_SECTIONS.map((section) => (
              <div key={section.title} className="rounded-2xl border border-primary-100/70 bg-gradient-to-br from-white/86 to-primary-50/55 px-2.5 py-2 shadow-sm backdrop-blur-sm">
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

          <SectionFlowRail sections={NAV_SECTIONS} />
        </div>
      </header>

      {mobileMenuOpen ? (
        <>
          <div className="fixed inset-0 z-40 bg-black/30 lg:hidden" onClick={() => setMobileMenuOpen(false)} />
          <nav
            className="fixed bottom-0 left-0 top-[72px] z-50 w-72 overflow-y-auto border-r border-primary-100/70 bg-slate-50/94 shadow-xl backdrop-blur lg:hidden"
            role="navigation"
            aria-label="移动端导航"
          >
            <div className="space-y-3 p-3">
              {NAV_SECTIONS.map((section) => (
                <div key={section.title} className="rounded-xl border border-primary-100/70 bg-gradient-to-br from-white/86 to-primary-50/55 p-2">
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

      <main className="relative z-10 mx-auto flex w-full max-w-[1760px] min-w-0 flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
