import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Clock3, Download, ExternalLink, RefreshCw, Search } from 'lucide-react'
import { exportSessionsCsv, fetchSessions } from '../api/obs.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { compactText, DASH, formatAlertTitle, formatDateTime } from '../utils/digitalLife.js'
import { sanitizeRecentRequests } from '../utils/sessionList.js'

const PAGE_SIZE = 20

const WINDOW_OPTIONS = [
  { label: '1h', value: 60, timeRange: '1h' },
  { label: '6h', value: 360, timeRange: '6h' },
  { label: '24h', value: 1440, timeRange: '24h' },
  { label: '7d', value: 10080, timeRange: '7d' },
]

function getTimeRange(minutes) {
  return WINDOW_OPTIONS.find((option) => option.value === minutes)?.timeRange || '24h'
}

function getWindowLabel(minutes) {
  return WINDOW_OPTIONS.find((option) => option.value === minutes)?.label || `${minutes}m`
}

function normalizeStatus(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'ok' || value === 'success') return 'healthy'
  if (value === 'rejected') return 'degraded'
  if (value === 'error' || value === 'fail') return 'error'
  return 'unknown'
}

function getStatusTone(status) {
  switch (normalizeStatus(status)) {
    case 'healthy':
      return {
        ring: 'bg-emerald-400',
        border: 'border-emerald-200',
        bg: 'bg-emerald-50/80',
        badge: 'bg-emerald-100 text-emerald-700 border-emerald-200',
      }
    case 'degraded':
      return {
        ring: 'bg-amber-400',
        border: 'border-amber-200',
        bg: 'bg-amber-50/80',
        badge: 'bg-amber-100 text-amber-700 border-amber-200',
      }
    case 'error':
      return {
        ring: 'bg-rose-400',
        border: 'border-rose-200',
        bg: 'bg-rose-50/80',
        badge: 'bg-rose-100 text-rose-700 border-rose-200',
      }
    default:
      return {
        ring: 'bg-slate-400',
        border: 'border-slate-200',
        bg: 'bg-white',
        badge: 'bg-slate-100 text-slate-700 border-slate-200',
      }
  }
}

function FlowSection({ title, hint, children }) {
  return (
    <section className="card relative overflow-hidden p-4">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_32%)]" />
      <div className="relative">
        <div className="mb-2 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        </div>
        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mb-4 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        {children}
      </div>
    </section>
  )
}

function SessionCard({ item, onOpen }) {
  const tone = getStatusTone(item.status)
  const preview = compactText(item.input_preview || '暂无输入摘要', 180)
  const latency = item.total_latency_ms != null ? `${item.total_latency_ms} ms` : DASH
  const reason = item.reason_code ? formatAlertTitle(item.reason_code) : '暂无显式原因'

  return (
    <button
      className={`group w-full rounded-2xl border ${tone.border} ${tone.bg} p-4 text-left shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-md`}
      onClick={() => onOpen(item.request_id)}
    >
      <div className="flex items-start gap-3">
        <span className={`mt-1 h-3.5 w-3.5 shrink-0 rounded-full ${tone.ring} ring-4 ring-white/70`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-gray-900">{preview}</div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                <span className="font-mono">{item.request_id?.slice(0, 18) || DASH}</span>
                <span>{item.session_id || DASH}</span>
                <span>{formatDateTime(item.ts_ms)}</span>
              </div>
            </div>
            <StatusBadge status={normalizeStatus(item.status)} label={item.status || 'UNKNOWN'} />
          </div>

          <div className="mt-3 text-sm leading-6 text-gray-700">{preview}</div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className={`inline-flex items-center rounded-full border px-2.5 py-1 font-medium ${tone.badge}`}>
              神经链路回放
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              耗时 {latency}
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              {reason}
            </span>
          </div>
        </div>
        <ExternalLink size={14} className="mt-1 text-gray-300 transition-colors group-hover:text-gray-500" />
      </div>
    </button>
  )
}

function SessionSkeleton() {
  return (
    <div className="grid gap-3 xl:grid-cols-2" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-2xl border border-gray-200 bg-white/85 p-4">
          <div className="h-3 w-3/4 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-3 h-3 w-1/2 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-4 h-12 w-full animate-pulse rounded-xl bg-gray-100" />
          <div className="mt-3 flex gap-2">
            <div className="h-6 w-24 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-20 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-28 animate-pulse rounded-full bg-gray-100" />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Sessions() {
  const navigate = useNavigate()
  const setSelectedRequestId = useAppStore((state) => state.setSelectedRequestId)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)

  const [sessions, setSessions] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [exporting, setExporting] = useState(false)
  const [suppressedCount, setSuppressedCount] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [windowMinutes, setWindowMinutes] = useState(1440)

  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const load = useCallback(async (targetPage = page) => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetchSessions({
        page: targetPage,
        page_size: PAGE_SIZE,
        keyword: keyword || undefined,
        status: status || undefined,
        time_range: getTimeRange(windowMinutes),
      })
      const rawItems = response.items || []
      const sanitizedItems = sanitizeRecentRequests(rawItems, { keyword })
      setSessions(sanitizedItems)
      setSuppressedCount(Math.max(0, rawItems.length - sanitizedItems.length))
      setTotal(response.total || 0)
      setPage(response.page || targetPage)
    } catch (loadError) {
      setError(loadError.message || '会话流加载失败')
      setSuppressedCount(0)
    } finally {
      setLoading(false)
    }
  }, [keyword, page, status, windowMinutes])

  useEffect(() => {
    load(page)
    if (!pollingEnabled) return undefined
    const timer = setInterval(() => load(page), pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, page, pollingEnabled, pollingInterval])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const summary = useMemo(() => {
    const healthyCount = sessions.filter((item) => normalizeStatus(item.status) === 'healthy').length
    const degradedCount = sessions.filter((item) => normalizeStatus(item.status) === 'degraded').length
    const errorCount = sessions.filter((item) => normalizeStatus(item.status) === 'error').length
    const latencyValues = sessions
      .map((item) => Number(item.total_latency_ms))
      .filter((value) => Number.isFinite(value) && value > 0)

    const avgLatency = latencyValues.length > 0
      ? Math.round(latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length)
      : null

    return { healthyCount, degradedCount, errorCount, avgLatency }
  }, [sessions])

  const pulseNodes = useMemo(() => ([
    { label: '当前页', value: `${sessions.length}/${PAGE_SIZE}`, tone: 'primary', active: true },
    { label: '顺流', value: String(summary.healthyCount), tone: 'teal', active: summary.healthyCount > 0 },
    { label: '回撤', value: String(summary.degradedCount), tone: 'amber', active: summary.degradedCount > 0 },
    { label: '异常', value: String(summary.errorCount), tone: 'rose', active: summary.errorCount > 0 },
    { label: '平均耗时', value: summary.avgLatency != null ? `${summary.avgLatency}ms` : '--', tone: 'violet', active: summary.avgLatency != null },
    { label: '时间窗', value: getWindowLabel(windowMinutes), tone: 'gray', active: true },
  ]), [sessions.length, summary, windowMinutes])

  const handleSearch = useCallback(() => {
    setPage(1)
    load(1)
  }, [load])

  const handleExport = useCallback(async () => {
    setExporting(true)
    try {
      await exportSessionsCsv({
        window_minutes: windowMinutes,
        keyword: keyword || undefined,
        status: status || undefined,
      })
      toast.success('CSV 导出已开始')
    } catch (exportError) {
      toast.error(`导出失败：${exportError.message}`)
    } finally {
      setExporting(false)
    }
  }, [keyword, status, windowMinutes])

  const openSessionTrace = useCallback((requestId) => {
    if (!requestId) return
    setSelectedRequestId(requestId)
    navigate(`/trace/${requestId}`)
  }, [navigate, setSelectedRequestId])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">神经会话沉积</h1>
          <p className="text-sm text-gray-500">把最近请求看成生命回声流，先看节律，再进入单条链路。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={handleExport} disabled={exporting}>
            <Download size={14} className={exporting ? 'animate-pulse' : ''} />
            {exporting ? '导出中' : '导出 CSV'}
          </button>
          <button className="btn-secondary" onClick={() => load(page)} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      <PageHero
        icon={Clock3}
        eyebrow="神经会话回声面板"
        title={`最近 ${getWindowLabel(windowMinutes)} 共 ${total} 条请求`}
        description={
          keyword || status
            ? `当前过滤生效${keyword ? `，关键词“${keyword}”` : ''}${status ? `，状态 ${status}` : ''}。`
            : '可按关键词、状态和时间窗口筛选请求，再进入神经链路查看完整传导路径。'
        }
        gradientClass="from-sky-50 via-white to-indigo-50"
        metaItems={[
          `当前页：${page} / ${totalPages}`,
          `顺流：${summary.healthyCount}`,
          `回撤：${summary.degradedCount}`,
          `异常：${summary.errorCount}`,
        ]}
      />

      <LivingPulseStrip
        title="会话脉冲"
        subtitle="把本页请求聚成一条可读脉冲流，识别是平稳推进还是出现回撤与异常。"
        nodes={pulseNodes}
      />

      {error ? (
        <NoticeBanner variant="error" title="会话流加载失败">
          {error}
        </NoticeBanner>
      ) : null}

      {!error && suppressedCount > 0 ? (
        <NoticeBanner variant="info" title="已隐藏噪声记录">
          当前页已自动隐藏 {suppressedCount} 条测试会话、纯问号占位消息或不可读摘要记录。
        </NoticeBanner>
      ) : null}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <FlowSection title="体内会话体征" hint="将会话质量、异常密度和耗时放入同一体征层。">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="当前页样本" value={sessions.length} hint="用于快速浏览" tone="text-gray-900" />
          <MetricCard label="顺流" value={summary.healthyCount} hint="当前页" tone="text-green-600" />
          <MetricCard label="回撤/降级" value={summary.degradedCount} hint="当前页" tone="text-orange-500" />
          <MetricCard
            label="平均耗时"
            value={summary.avgLatency != null ? `${summary.avgLatency} ms` : DASH}
            hint="当前页样本"
            tone="text-blue-600"
          />
        </div>
      </FlowSection>

      <FlowSection title="会话筛选与检索" hint="先在体内筛查，再下钻到单条神经链路。">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_180px_160px_auto]">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-xl border border-gray-200 py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
              placeholder="搜索 request_id、session_id 或输入摘要"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleSearch()
              }}
            />
          </div>

          <select
            className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value)
              setPage(1)
            }}
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="rejected">拒绝</option>
            <option value="error">异常</option>
          </select>

          <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
            时间窗：{getWindowLabel(windowMinutes)}
          </div>

          <button className="btn-primary justify-center" onClick={handleSearch}>
            搜索
          </button>
        </div>
      </FlowSection>

      <FlowSection title="会话沉积流" hint={`共 ${total} 条，单页 ${PAGE_SIZE} 条。`}>
        {loading ? <SessionSkeleton /> : null}

        {!loading && !error && sessions.length === 0 ? (
          <EmptyStateCard
            title="暂无会话沉积"
            hint="可以调整筛选条件，或等待新的对话请求进入系统。"
            className="p-6"
          />
        ) : null}

        {!loading && !error && sessions.length > 0 ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {sessions.map((item) => (
              <SessionCard key={item.request_id} item={item} onOpen={openSessionTrace} />
            ))}
          </div>
        ) : null}
      </FlowSection>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500">
        <span>当前页 {page} / {totalPages}</span>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary px-3 py-1.5 text-xs"
            disabled={page <= 1}
            onClick={() => {
              const nextPage = Math.max(1, page - 1)
              setPage(nextPage)
              load(nextPage)
            }}
          >
            上一页
          </button>
          <span className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            className="btn-secondary px-3 py-1.5 text-xs"
            disabled={page >= totalPages}
            onClick={() => {
              const nextPage = Math.min(totalPages, page + 1)
              setPage(nextPage)
              load(nextPage)
            }}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  )
}
