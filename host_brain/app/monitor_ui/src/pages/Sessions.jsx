import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Clock3, Download, ExternalLink, RefreshCw, Search } from 'lucide-react'
import { exportSessionsCsv, fetchSessions } from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import { SkeletonTable } from '../components/Skeleton.jsx'
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
      setError(loadError.message)
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

    return {
      healthyCount,
      degradedCount,
      errorCount,
      avgLatency,
    }
  }, [sessions])

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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">会话列表</h1>
          <p className="text-sm text-gray-500">查看最近请求、输入摘要、状态分布和 Trace 跳转入口。</p>
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

      <DigitalLifeNav />

      <PageHero
        icon={Clock3}
        eyebrow="会话观测面板"
        title={`最近 ${getWindowLabel(windowMinutes)} 共 ${total} 条请求`}
        description={
          keyword || status
            ? `当前过滤条件已生效${keyword ? `，关键词“${keyword}”` : ''}${status ? `，状态 ${status}` : ''}。`
            : '可按关键词、状态和时间窗口筛选请求，再直接跳转到 Trace 查看完整链路。'
        }
        gradientClass="from-sky-50 via-white to-indigo-50"
        metaItems={[
          `当前页：${page} / ${totalPages}`,
          `成功：${summary.healthyCount}`,
          `拒绝：${summary.degradedCount}`,
          `异常：${summary.errorCount}`,
        ]}
      />

      {error ? (
        <NoticeBanner variant="error" title="会话列表加载失败">
          {error}
        </NoticeBanner>
      ) : null}

      {!error && suppressedCount > 0 ? (
        <NoticeBanner variant="info" title="已隐藏噪声记录">
          当前页已自动隐藏 {suppressedCount} 条测试会话、纯问号占位消息或无可读输入摘要的记录。
        </NoticeBanner>
      ) : null}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前页样本" value={sessions.length} hint="用于快速浏览" tone="text-gray-900" />
        <MetricCard label="成功" value={summary.healthyCount} hint="当前页" tone="text-green-600" />
        <MetricCard label="拒绝/降级" value={summary.degradedCount} hint="当前页" tone="text-orange-500" />
        <MetricCard
          label="平均耗时"
          value={summary.avgLatency != null ? `${summary.avgLatency} ms` : DASH}
          hint="当前页样本"
          tone="text-blue-600"
        />
      </div>

      <div className="card p-4">
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
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr className="border-b border-gray-200">
                <th className="px-4 py-3 text-left">Request</th>
                <th className="px-4 py-3 text-left">会话</th>
                <th className="px-4 py-3 text-left">输入摘要</th>
                <th className="px-4 py-3 text-left">时间</th>
                <th className="px-4 py-3 text-left">状态</th>
                <th className="px-4 py-3 text-left">耗时</th>
                <th className="px-4 py-3 text-left">原因</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-0">
                    <SkeletonTable rows={6} cols={7} />
                  </td>
                </tr>
              ) : null}

              {!loading && !error && sessions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8">
                    <EmptyStateCard
                      title="暂无会话数据"
                      hint="可以调整筛选条件，或者等待新的对话请求进入系统。"
                      className="p-6"
                    />
                  </td>
                </tr>
              ) : null}

              {!loading && !error && sessions.map((item) => (
                <tr
                  key={item.request_id}
                  className="cursor-pointer transition-colors hover:bg-primary-50/40"
                  onClick={() => {
                    setSelectedRequestId(item.request_id)
                    navigate(`/trace/${item.request_id}`)
                  }}
                >
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-1 font-mono text-xs text-gray-700">
                      <span>{item.request_id?.slice(0, 18) || DASH}</span>
                      <ExternalLink size={12} className="text-gray-300" />
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-gray-500">
                    <div className="max-w-[180px] truncate" title={item.session_id || ''}>
                      {item.session_id || DASH}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-sm text-gray-700">
                    <div className="max-w-[360px]" title={item.input_preview || ''}>
                      {item.input_preview ? compactText(item.input_preview, 88) : DASH}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-gray-500">
                    {formatDateTime(item.ts_ms)}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <StatusBadge status={normalizeStatus(item.status)} />
                  </td>
                  <td className="px-4 py-3 align-top text-sm text-gray-600">
                    {item.total_latency_ms != null ? `${item.total_latency_ms} ms` : DASH}
                  </td>
                  <td className="px-4 py-3 align-top text-xs text-gray-500">
                    {item.reason_code ? formatAlertTitle(item.reason_code) : DASH}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500">
        <span>共 {total} 条，单页 {PAGE_SIZE} 条。</span>
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
