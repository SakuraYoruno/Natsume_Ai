import React, { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  ChevronRight,
  Menu,
  PauseCircle,
  RefreshCw,
  X,
} from 'lucide-react'
import ForgeRiskActionBar from '../../features/operations/components/ForgeRiskActionBar.jsx'
import useAppStore from '../../store/useAppStore.js'
import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import { useCapabilityGapStatsQuery } from '../../features/observability/diagnosticQueries.js'
import { preloadRoute } from '../../routes/routeLoaders.js'
import { NAV_SECTIONS as MANIFEST_NAV_SECTIONS } from '../routeManifest.jsx'
import { buildDigitalLifeAlerts } from '../../utils/runtimeHealth.js'

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

function findActiveNav(pathname) {
  const path = pathname || '/cockpit'
  for (const section of NAV_SECTIONS) {
    const activeItem = section.items.find((item) => path === item.to || path.startsWith(`${item.to}/`))
    if (activeItem) {
      return { section, item: activeItem }
    }
  }
  return { section: NAV_SECTIONS[0], item: NAV_SECTIONS[0]?.items[0] }
}

function NavPill({ to, icon: Icon, label, onClick, badge = null, compact = false, block = false }) {
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
        `${block ? 'flex w-full justify-start' : 'inline-flex shrink-0'} min-w-0 items-center gap-1.5 whitespace-nowrap border font-medium transition-all ${
          compact ? 'rounded-lg px-2 py-1 text-xs' : 'rounded-xl px-3 py-1.5 text-sm'
        } ${
          isActive
            ? 'border-primary-200 bg-gradient-to-r from-primary-50 to-cyan-50 text-primary shadow-sm'
            : 'border-transparent text-gray-600 hover:border-primary-200 hover:bg-primary-50/65 hover:text-gray-800'
        }`
      }
    >
      <Icon size={15} className="shrink-0" />
      <span className="truncate">{label}</span>
      {badge ? <NavBadge value={badge.value} severity={badge.severity} /> : null}
    </NavLink>
  )
}

function SectionTab({ section, active = false, badge = null }) {
  const target = section.items[0]?.to || '/cockpit'
  const handlePrefetch = () => {
    void preloadRoute(target)
  }

  return (
    <NavLink
      to={target}
      onMouseEnter={handlePrefetch}
      onFocus={handlePrefetch}
      onTouchStart={handlePrefetch}
      aria-label={`进入 ${section.title}`}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold transition-all ${
        active
          ? 'border-primary-200 bg-primary-50 text-primary shadow-sm'
          : 'border-transparent text-gray-500 hover:border-primary-100 hover:bg-white/80 hover:text-gray-800'
      }`}
    >
      <span>{section.title}</span>
      {badge ? <NavBadge value={badge.value} severity={badge.severity} /> : null}
    </NavLink>
  )
}

function DesktopNav({ activeNav, sectionBadges, itemBadges }) {
  const currentSection = activeNav?.section || NAV_SECTIONS[0]
  const currentItem = activeNav?.item || currentSection?.items?.[0]

  return (
    <nav
      className="hidden min-w-0 overflow-hidden rounded-xl border border-primary-100/70 bg-white/76 shadow-sm backdrop-blur-sm lg:block"
      role="navigation"
      aria-label="主导航"
    >
      <div className="flex min-w-0 items-center justify-between gap-3 border-b border-primary-100/70 px-2 py-1.5">
        <div className="flex min-w-0 items-center gap-1.5 overflow-x-auto">
          {NAV_SECTIONS.map((section) => (
            <SectionTab
              key={section.title}
              section={section}
              active={currentSection?.title === section.title}
              badge={sectionBadges[section.title]}
            />
          ))}
        </div>
        <div className="hidden shrink-0 items-center gap-1.5 rounded-lg border border-primary-100/70 bg-primary-50/55 px-2 py-1 text-xs text-primary-700 xl:flex">
          <span className="font-semibold">{currentSection?.title}</span>
          <ChevronRight size={13} />
          <span className="max-w-36 truncate text-gray-600">{currentItem?.label}</span>
        </div>
      </div>
      <div
        className="flex min-w-0 items-center gap-2 overflow-x-auto px-2 py-1.5"
        aria-label={`${currentSection?.title || '当前领域'} 子导航`}
      >
        <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-primary-100/80 bg-primary-50/70 px-2 py-1 text-[11px] font-semibold text-primary-700">
          {currentSection?.title}
          {sectionBadges[currentSection?.title] ? (
            <NavBadge
              value={sectionBadges[currentSection.title].value}
              severity={sectionBadges[currentSection.title].severity}
            />
          ) : null}
        </span>
        {(currentSection?.items || []).map((item) => (
          <NavPill key={item.to} {...item} badge={itemBadges[item.to]} compact />
        ))}
      </div>
    </nav>
  )
}

function MobileCurrentRoute({ activeNav, runtimeAlertBadge, onOpenMenu }) {
  if (!activeNav?.section || !activeNav?.item) return null
  const { section, item } = activeNav
  const Icon = item.icon
  return (
    <button
      type="button"
      className="flex w-full min-w-0 items-center justify-between gap-3 rounded-xl border border-primary-100/70 bg-white/78 px-3 py-2 text-left shadow-sm backdrop-blur-sm lg:hidden"
      onClick={onOpenMenu}
      aria-label={`打开导航菜单：${section.title} / ${item.label}`}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-primary-100 bg-primary-50 text-primary">
          <Icon size={16} />
        </span>
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            {section.title}
            {section.title === 'Life Systems' && runtimeAlertBadge ? (
              <NavBadge value={runtimeAlertBadge.value} severity={runtimeAlertBadge.severity} />
            ) : null}
          </span>
          <span className="block truncate text-sm font-semibold text-gray-800">{item.label}</span>
        </span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-xs font-medium text-gray-500">
        菜单
        <ChevronRight size={14} />
      </span>
    </button>
  )
}

function MobileNavDrawer({
  activeNav,
  itemBadges,
  mobileMenuOpen,
  onClose,
  sectionBadges,
}) {
  if (!mobileMenuOpen) return null
  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-[60] bg-slate-950/30 backdrop-blur-[1px] lg:hidden"
        onClick={onClose}
        aria-label="关闭导航遮罩"
      />
      <nav
        className="fixed inset-y-0 left-0 z-[70] flex w-[min(22rem,calc(100vw-2rem))] flex-col border-r border-primary-100/70 bg-slate-50 shadow-2xl lg:hidden"
        role="navigation"
        aria-label="移动端导航"
      >
        <div className="border-b border-primary-100/70 bg-white/88 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-base font-bold tracking-tight text-primary">Natsume AI</div>
              <div className="truncate text-xs text-gray-500">
                {activeNav?.section?.title || 'Console'} / {activeNav?.item?.label || '导航'}
              </div>
            </div>
            <button
              type="button"
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-gray-50 text-gray-600 transition-colors hover:bg-gray-100"
              onClick={onClose}
              aria-label="关闭导航菜单"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          {NAV_SECTIONS.map((section) => {
            const isCurrentSection = activeNav?.section?.title === section.title
            return (
              <section
                key={section.title}
                className={`border-b border-primary-100/70 py-2 last:border-b-0 ${
                  isCurrentSection ? 'rounded-xl border border-primary-100 bg-white/76 px-2 shadow-sm' : 'px-2'
                }`}
              >
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                  <span>{section.title}</span>
                  {sectionBadges[section.title] ? (
                    <NavBadge
                      value={sectionBadges[section.title].value}
                      severity={sectionBadges[section.title].severity}
                    />
                  ) : null}
                </div>
                <div className="grid grid-cols-1 gap-1">
                  {section.items.map((item) => (
                    <NavPill
                      key={item.to}
                      {...item}
                      badge={itemBadges[item.to]}
                      block
                      compact={false}
                      onClick={onClose}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </nav>
    </>
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
  const activeNav = findActiveNav(location.pathname)
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
  }, [location.pathname, location.search])

  useEffect(() => {
    if (!mobileMenuOpen) {
      return undefined
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setMobileMenuOpen(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [mobileMenuOpen])

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
                type="button"
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white/75 text-gray-500 transition-colors hover:bg-gray-100 lg:hidden"
                onClick={() => setMobileMenuOpen((value) => !value)}
                aria-label={mobileMenuOpen ? '关闭主导航菜单' : '打开主导航菜单'}
              >
                {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
              </button>
              <div className="min-w-0">
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

          <MobileCurrentRoute
            activeNav={activeNav}
            runtimeAlertBadge={runtimeAlertBadge}
            onOpenMenu={() => setMobileMenuOpen(true)}
          />

          <DesktopNav activeNav={activeNav} sectionBadges={sectionBadges} itemBadges={itemBadges} />
        </div>
      </header>

      <MobileNavDrawer
        activeNav={activeNav}
        itemBadges={itemBadges}
        mobileMenuOpen={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        sectionBadges={sectionBadges}
      />

      <main className="relative z-10 mx-auto flex w-full max-w-[1760px] min-w-0 flex-1 flex-col px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
