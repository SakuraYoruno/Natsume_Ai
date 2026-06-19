import React, { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Menu,
  PauseCircle,
  RefreshCw,
  X,
} from 'lucide-react'
import ForgeRiskActionBar from './ForgeRiskActionBar.jsx'
import useAppStore from '../store/useAppStore.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useCapabilityGapStatsQuery } from '../features/observability/diagnosticQueries.js'
import { preloadRoute } from '../routes/routeLoaders.js'
import { NAV_SECTIONS as MANIFEST_NAV_SECTIONS } from '../app/routeManifest.jsx'
import { buildDigitalLifeAlerts } from '../utils/runtimeHealth.js'

const NAV_SECTIONS = MANIFEST_NAV_SECTIONS

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

function NavPill({ to, icon: Icon, label, onClick, badge = null, compact = false }) {
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
        `inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border font-medium transition-all ${
          compact ? 'rounded-lg px-2 py-1 text-xs' : 'rounded-xl px-3 py-1.5 text-sm'
        } ${
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

function DesktopNav({ sectionBadges, itemBadges }) {
  return (
    <nav
      className="hidden min-w-0 flex-wrap items-center gap-2 rounded-xl border border-primary-100/70 bg-white/76 px-2 py-1.5 shadow-sm backdrop-blur-sm lg:flex"
      role="navigation"
      aria-label="主导航"
    >
      {NAV_SECTIONS.map((section) => (
        <React.Fragment key={section.title}>
          <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-primary-100/80 bg-primary-50/70 px-2 py-1 text-[11px] font-semibold text-primary-700">
            {section.title}
            {sectionBadges[section.title] ? (
              <NavBadge
                value={sectionBadges[section.title].value}
                severity={sectionBadges[section.title].severity}
              />
            ) : null}
          </span>
          {section.items.map((item) => (
            <NavPill key={item.to} {...item} badge={itemBadges[item.to]} compact />
          ))}
        </React.Fragment>
      ))}
    </nav>
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
    'Life Systems': runtimeAlertBadge,
  }

  const itemBadges = {
    '/observability/alerts': runtimeAlertBadge,
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-x-hidden">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-slate-50 via-white to-cyan-50/45" />
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary-100 to-transparent" />
      </div>
      <header className="sticky top-0 z-50 border-b border-primary-100/70 bg-gradient-to-r from-primary-50/80 via-white/88 to-emerald-50/70 shadow-sm backdrop-blur-xl">
        <div className="mx-auto w-full space-y-2 px-4 py-2 sm:px-6">
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

          <DesktopNav sectionBadges={sectionBadges} itemBadges={itemBadges} />
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
