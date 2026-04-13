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
import { fetchCapabilityGapStats, fetchErrors, fetchServicesHealth } from '../api/obs.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { classNames, compactText, formatAlertTitle, formatDateTime, formatServiceStatus, humanizeToken } from '../utils/digitalLife.js'
import { buildDigitalLifeAlerts } from '../utils/runtimeHealth.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const SEVERITY_RANK = {
  critical: 0,
  warning: 1,
  info: 2,
  resolved: 3,
}

const FORGE_REASON_CODES = new Set([
  'FORGE_RUNTIME_CRITICAL_PROPOSALS',
  'FORGE_RUNTIME_UNHEALTHY_PROPOSALS',
])

const SEVERITY_META = {
  critical: {
    icon: XCircle,
    badge: 'text-red-700 bg-red-50 border-red-200',
    card: 'border-red-200 bg-red-50/70',
    accent: 'bg-red-500',
    label: '严重',
  },
  warning: {
    icon: AlertTriangle,
    badge: 'text-orange-700 bg-orange-50 border-orange-200',
    card: 'border-orange-200 bg-orange-50/70',
    accent: 'bg-orange-400',
    label: '警告',
  },
  info: {
    icon: Bell,
    badge: 'text-blue-700 bg-blue-50 border-blue-200',
    card: 'border-blue-200 bg-blue-50/70',
    accent: 'bg-blue-400',
    label: '提示',
  },
  resolved: {
    icon: BellOff,
    badge: 'text-green-700 bg-green-50 border-green-200',
    card: 'border-green-200 bg-green-50/70',
    accent: 'bg-green-500',
    label: '已恢复',
  },
}

function normalizeAlertSeverity(alert) {
  if (alert?.severity) return String(alert.severity).toLowerCase()
  const reasonCode = String(alert?.reason_code || '').toUpperCase()
  if (reasonCode.includes('TIMEOUT') || reasonCode.includes('BLOCK') || reasonCode.includes('DOWN')) {
    return 'critical'
  }
  return 'warning'
}

function buildAlertTitle(alert) {
  return (
    alert?.title
    || (alert?.reason_code ? formatAlertTitle(alert.reason_code) : '')
    || (alert?.event_type ? humanizeToken(alert.event_type) : '')
    || '未分类告警'
  )
}

function buildAlertDetail(alert) {
  if (alert?.detail) return compactText(alert.detail, 180)
  if (alert?.stage) return `阶段：${humanizeToken(alert.stage)}`
  return '暂无更多细节。'
}

function AlertCard({ alert }) {
  const severity = normalizeAlertSeverity(alert)
  const meta = SEVERITY_META[severity] || SEVERITY_META.info
  const Icon = meta.icon
  const title = buildAlertTitle(alert)
  const detail = buildAlertDetail(alert)

  return (
    <div className={classNames('card border p-4 transition-colors', meta.card)}>
      <div className="flex items-start gap-3">
        <div className={classNames('mt-0.5 rounded-xl border p-2', meta.badge)}>
          <Icon size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-semibold text-gray-800">{title}</span>
              <span className={classNames('rounded-full border px-2 py-0.5 text-[11px] font-medium', meta.badge)}>
                {meta.label}
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
              <a href={`#/trace/${alert.request_id}`} className="text-primary hover:underline">
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

  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [serviceAlerts, setServiceAlerts] = useState([])
  const [capabilityGapData, setCapabilityGapData] = useState(null)
  const [filterCode, setFilterCode] = useState('')

  const applyFilterCode = useCallback((nextCode) => {
    const next = String(nextCode || '').trim()
    setFilterCode(next)

    const params = new URLSearchParams(location.search)
    if (next) {
      params.set('reason_code', next)
    } else {
      params.delete('reason_code')
    }
    const nextSearch = params.toString()
    navigate(
      {
        pathname: '/alerts',
        search: nextSearch ? `?${nextSearch}` : '',
      },
      { replace: true }
    )
  }, [location.search, navigate])

  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [errorRes, healthRes, capabilityGapRes] = await Promise.all([
        fetchErrors({ window_minutes: windowMinutes, limit: 100 }),
        fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
        fetchCapabilityGapStats({
          limit_recent: 20,
          limit_hotspots: 8,
          limit_candidates: 8,
          min_candidate_detected: 3,
          min_candidate_sessions: 2,
          min_candidate_failure_rate: 0.35,
          min_candidate_failed: 2,
        }).catch(() => null),
      ])

      const errorAlerts = (errorRes?.recent_errors || errorRes?.errors || []).map((item) => ({
        ...item,
        title: item.title || (item.reason_code ? formatAlertTitle(item.reason_code) : ''),
        severity: normalizeAlertSeverity(item),
      }))

      const runtimeServiceAlerts = (healthRes?.services || [])
        .filter((service) => ['DOWN', 'DEGRADED'].includes(String(service.status || '').toUpperCase()))
        .map((service) => ({
          reason_code: `SERVICE_${String(service.status || '').toUpperCase()}`,
          title: `${service.display_name || service.service_id} ${formatServiceStatus(service.status)}`,
          detail: compactText(
            service.status_detail || `当前状态：${formatServiceStatus(service.status)}`,
            180
          ),
          ts_ms: Date.now(),
          severity: String(service.status || '').toUpperCase() === 'DOWN' ? 'critical' : 'warning',
          source: 'service-health',
          to: '/overview',
        }))

      setAlerts(errorAlerts)
      setServiceAlerts(runtimeServiceAlerts)
      setCapabilityGapData(capabilityGapRes)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return undefined
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const reasonCode = String(params.get('reason_code') || '').trim()
    setFilterCode(reasonCode)
  }, [location.search])

  const runtimeAlerts = useMemo(
    () => buildDigitalLifeAlerts(runtimeSnapshot, { promptStats, capabilityGapData }),
    [runtimeSnapshot, promptStats, capabilityGapData]
  )

  const allAlerts = useMemo(
    () => [...runtimeAlerts, ...serviceAlerts, ...alerts].sort((left, right) => {
      const severityDelta = (SEVERITY_RANK[normalizeAlertSeverity(left)] ?? 99)
        - (SEVERITY_RANK[normalizeAlertSeverity(right)] ?? 99)
      if (severityDelta !== 0) return severityDelta
      return Number(right.ts_ms || 0) - Number(left.ts_ms || 0)
    }),
    [alerts, runtimeAlerts, serviceAlerts]
  )

  const reasonBreakdown = useMemo(() => {
    const counter = new Map()
    allAlerts.forEach((item) => {
      const key = String(item.reason_code || 'UNKNOWN')
      counter.set(key, (counter.get(key) || 0) + 1)
    })
    return Array.from(counter.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, 8)
  }, [allAlerts])

  const filteredAlerts = useMemo(() => {
    if (!filterCode) return allAlerts
    return allAlerts.filter((item) => String(item.reason_code || '').toLowerCase() === filterCode.toLowerCase())
  }, [allAlerts, filterCode])

  const criticalCount = filteredAlerts.filter((item) => normalizeAlertSeverity(item) === 'critical').length
  const warningCount = filteredAlerts.filter((item) => normalizeAlertSeverity(item) === 'warning').length
  const infoCount = filteredAlerts.filter((item) => normalizeAlertSeverity(item) === 'info').length
  const isForgeRiskFiltered = FORGE_REASON_CODES.has(String(filterCode || '').toUpperCase())

  const heroTitle = criticalCount > 0
    ? `${criticalCount} 条严重告警待处理`
    : filteredAlerts.length > 0
      ? `当前窗口共有 ${filteredAlerts.length} 条告警`
      : '当前窗口无告警'

  const heroDescription = filterCode
    ? `已按 ${formatAlertTitle(filterCode)} 过滤，方便集中处理同类问题。`
    : filteredAlerts.length > 0
      ? '已聚合数字生命体运行时告警、器官异常和错误事件，便于按严重度快速收敛。'
      : '当前时间窗口内未发现运行时异常、器官离线或高优先级错误。'

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">免疫告警中心</h1>
          <p className="text-sm text-gray-500">统一收敛生命体运行时、器官异常和错误日志告警。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      <PageHero
        icon={Siren}
        eyebrow="数字生命体免疫告警总览"
        title={heroTitle}
        description={heroDescription}
        gradientClass="from-rose-50 via-white to-orange-50"
        metaItems={[
          `时间窗口：${windowMinutes} 分钟`,
          `运行时告警：${runtimeAlerts.length}`,
          `器官异常：${serviceAlerts.length}`,
          `错误事件：${alerts.length}`,
        ]}
      />

      {error ? (
        <NoticeBanner variant="error" title="告警数据加载失败">
          {error}
        </NoticeBanner>
      ) : null}

      {isForgeRiskFiltered ? (
        <NoticeBanner variant="info" title="Forge Risk Drill-down">
          <span className="mr-2">{`filter=${filterCode}`}</span>
          <a href="#/insights/global?focus=forge-risk" className="font-medium text-primary hover:underline">
            打开 Forge 全局洞察
          </a>
        </NoticeBanner>
      ) : null}

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
                        style={{ width: `${Math.min(100, (count / Math.max(allAlerts.length, 1)) * 100)}%` }}
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
