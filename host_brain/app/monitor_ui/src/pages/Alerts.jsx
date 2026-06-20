import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  Bell,
  BellOff,
  Filter,
  RefreshCw,
  ShieldAlert,
  Siren,
  Wrench,
  XCircle,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useAlertsCenterQuery } from '../features/observability/alertsQueries.js'
import useAppStore from '../store/useAppStore.js'
import {
  buildAlertCardView,
  buildAlertsCenterView,
  buildAlertsFilterSearch,
  getAlertsFilterCodeFromSearch,
  getAlertReasonBreakdownBarWidth,
} from '../features/observability/alertsModel.js'
import { classNames, formatAlertTitle, formatDateTime, humanizeToken } from '../utils/digitalLife.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const SEVERITY_ICONS = {
  critical: XCircle,
  warning: AlertTriangle,
  info: Bell,
  resolved: BellOff,
}

function AlertCard({ alert }) {
  const {
    severity,
    badgeClass,
    cardClass,
    severityLabel,
    title,
    detail,
  } = buildAlertCardView(alert)
  const Icon = SEVERITY_ICONS[severity] || SEVERITY_ICONS.info

  return (
    <div className={classNames('card border p-4 transition-colors', cardClass)}>
      <div className="flex items-start gap-3">
        <div className={classNames('mt-0.5 rounded-xl border p-2', badgeClass)}>
          <Icon size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-semibold text-gray-800">{title}</span>
              <span className={classNames('rounded-full border px-2 py-0.5 text-[11px] font-medium', badgeClass)}>
                {severityLabel}
              </span>
            </div>
            <span className="shrink-0 text-xs text-gray-400">{formatDateTime(alert.ts_ms)}</span>
          </div>

          <div className="mt-2 text-sm text-gray-600">{detail}</div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
            {alert?.source ? (
              <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5">
                来源：{humanizeToken(alert.source)}
              </span>
            ) : null}
            {alert?.reason_code ? (
              <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 font-mono">
                {alert.reason_code}
              </span>
            ) : null}
            {alert?.request_id ? (
              <a href={`#/observability/trace/${alert.request_id}`} className="text-primary hover:underline">
                打开 Trace
              </a>
            ) : null}
            {!alert?.request_id && alert?.to ? (
              <a href={`#${alert.to}`} className="text-primary hover:underline">
                打开相关页面
              </a>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Alerts() {
  const location = useLocation()
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(360)
  const [filterCode, setFilterCode] = useState('')

  const applyFilterCode = useCallback((nextCode) => {
    const next = String(nextCode || '').trim()
    setFilterCode(next)
    navigate(
      {
        pathname: '/observability/alerts',
        search: buildAlertsFilterSearch(location.search, next),
      },
      { replace: true }
    )
  }, [location.search, navigate])

  const {
    runtimeSnapshot,
    promptStats,
    reload: reloadRuntime,
    lastUpdatedAt: runtimeUpdatedAt,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })
  const {
    errorRes,
    healthRes,
    capabilityGapData,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useAlertsCenterQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    setFilterCode(getAlertsFilterCodeFromSearch(location.search))
  }, [location.search])

  const {
    serviceAlerts,
    allAlerts,
    reasonBreakdown,
    filteredAlerts,
    criticalCount,
    warningCount,
    infoCount,
    isForgeRiskFiltered,
    immuneOrganModel,
    heroContent,
    heroMetaItems,
    sampleMeta,
  } = useMemo(() => buildAlertsCenterView({
    runtimeSnapshot,
    promptStats,
    capabilityGapData,
    errorRes,
    healthRes,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    runtimeUpdatedAt,
    stale,
    filterCode,
    windowMinutes,
  }), [
    capabilityGapData,
    error,
    errorRes,
    filterCode,
    healthRes,
    lastUpdatedAt,
    loading,
    promptStats,
    refreshing,
    runtimeSnapshot,
    runtimeUpdatedAt,
    stale,
    windowMinutes,
  ])

  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([refetch(), reloadRuntime()])
  }, [refetch, reloadRuntime])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">免疫告警中心</h1>
          <p className="text-sm text-gray-500">统一收敛生命体运行时、器官异常和错误日志告警。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={handleRefresh} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            {loading || refreshing ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      <PageHero
        icon={Siren}
        eyebrow="数字生命体免疫告警总览"
        title={heroContent.title}
        description={heroContent.description}
        gradientClass="from-rose-50 via-white to-orange-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            windowMinutes={sampleMeta.windowMinutes}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {error ? (
        <NoticeBanner variant="error" title="告警数据加载失败">
          {error.message}
        </NoticeBanner>
      ) : null}

      {isForgeRiskFiltered ? (
        <NoticeBanner variant="info" title="Forge Risk Drill-down">
          <span className="mr-2">{`filter=${filterCode}`}</span>
          <a href="#/observability/insights/global?focus=forge-risk" className="font-medium text-primary hover:underline">
            打开 Forge 全局洞察
          </a>
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={immuneOrganModel} />

      <DigitalLifeRuntimeCard
        runtimeSnapshot={runtimeSnapshot}
        promptStats={promptStats}
        capabilityGapData={capabilityGapData}
        compact
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <MetricCard icon={Bell} label="总告警" value={filteredAlerts.length} hint="当前过滤结果" tone="text-gray-900" />
        <MetricCard icon={ShieldAlert} label="严重" value={criticalCount} hint="优先处理" tone="text-red-600" />
        <MetricCard icon={AlertTriangle} label="警告" value={warningCount} hint="建议跟进" tone="text-orange-500" />
        <MetricCard icon={BellOff} label="提示" value={infoCount} hint="信息性事件" tone="text-blue-600" />
        <MetricCard icon={Wrench} label="器官异常" value={serviceAlerts.length} hint="来自 health probe" tone="text-purple-600" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Filter size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">触发类型</h2>
          </div>

          {reasonBreakdown.length > 0 ? (
            <div className="mt-4 space-y-2">
              {reasonBreakdown.map(([code, count]) => {
                const active = code === filterCode
                return (
                  <button
                    key={code}
                    type="button"
                    className={classNames(
                      'w-full rounded-xl border px-3 py-2 text-left transition-colors',
                      active ? 'border-primary bg-primary-50' : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                    )}
                    onClick={() => applyFilterCode(active ? '' : code)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className={classNames('text-sm font-medium', active ? 'text-primary' : 'text-gray-700')}>
                        {formatAlertTitle(code)}
                      </span>
                      <span className="text-xs text-gray-400">{count}</span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: getAlertReasonBreakdownBarWidth(count, allAlerts.length) }}
                      />
                    </div>
                    <div className="mt-2 truncate font-mono text-[11px] text-gray-400">{code}</div>
                  </button>
                )
              })}
            </div>
          ) : (
            <EmptyStateCard title="暂无类型分布" hint="有告警进入后，这里会显示主要触发类型。" className="mt-4 p-5" />
          )}

          {filterCode ? (
            <button className="btn-secondary mt-4 w-full justify-center" onClick={() => applyFilterCode('')}>
              清除过滤：{formatAlertTitle(filterCode)}
            </button>
          ) : null}
        </div>

        <div className="space-y-3">
          {filteredAlerts.length > 0 ? (
            filteredAlerts.map((item, index) => (
              <AlertCard key={`${item.reason_code || 'alert'}-${item.ts_ms || index}-${index}`} alert={item} />
            ))
          ) : (
            <EmptyStateCard
              title="当前没有待看的告警"
              hint="系统在当前窗口内保持稳定；如果你刚切换了过滤条件，也可能是该类型暂无命中。"
              icon={BellOff}
            />
          )}
        </div>
      </div>
    </div>
  )
}
