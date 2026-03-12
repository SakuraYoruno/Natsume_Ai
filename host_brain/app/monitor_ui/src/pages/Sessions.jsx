import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, RefreshCw } from 'lucide-react'
import StatusBadge from '../components/StatusBadge.jsx'
import { fetchSessions } from '../api/obs.js'

const PAGE_SIZE = 20

/** 将 ts_ms 格式化为可读时间 */
function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

/** 将后端 status 字段映射为 StatusBadge 可识别的值 */
function normalizeStatus(status) {
  const s = (status ?? '').toLowerCase()
  if (s === 'ok' || s === 'success') return 'healthy'
  if (s === 'rejected') return 'degraded'
  if (s === 'error' || s === 'fail') return 'error'
  return 'unknown'
}

export default function Sessions() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // 过滤条件
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [timeRange, setTimeRange] = useState('24h')

  const load = useCallback(async (pageNum) => {
    const p = pageNum ?? page
    setLoading(true)
    setError(null)
    try {
      const res = await fetchSessions({
        page: p,
        page_size: PAGE_SIZE,
        keyword: keyword || undefined,
        status: status || undefined,
        time_range: timeRange,
      })
      setSessions(res.items ?? [])
      setTotal(res.total ?? 0)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [page, keyword, status, timeRange])

  useEffect(() => { load() }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const handleSearch = () => {
    setPage(1)
    load(1)
  }

  return (
    <div className="space-y-4">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">会话列表</h1>
        <button className="btn-secondary" onClick={() => load()} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新
        </button>
      </div>

      {/* 过滤栏 */}
      <div className="card p-3 flex flex-wrap gap-3 items-center">
        <div className="relative flex-1 min-w-48">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-300 rounded
                       focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary"
            placeholder="搜索 request_id…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          />
        </div>
        <select
          className="text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary-200"
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1) }}
        >
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="rejected">拒绝</option>
          <option value="error">错误</option>
        </select>
        <select
          className="text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary-200"
          value={timeRange}
          onChange={(e) => { setTimeRange(e.target.value); setPage(1) }}
        >
          <option value="1h">最近 1h</option>
          <option value="6h">最近 6h</option>
          <option value="24h">最近 24h</option>
          <option value="7d">最近 7d</option>
        </select>
        <button className="btn-primary text-sm" onClick={handleSearch}>搜索</button>
      </div>

      {/* 表格 */}
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {['Request ID', '时间', '状态', '耗时', '拒绝码'].map((h) => (
                <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-gray-400">
                <RefreshCw size={16} className="animate-spin inline mr-2" />加载中…
              </td></tr>
            )}
            {error && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-red-500">{error}</td></tr>
            )}
            {!loading && !error && sessions.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-gray-400">暂无数据</td></tr>
            )}
            {!loading && sessions.map((s) => (
              <tr
                key={s.request_id}
                className="hover:bg-gray-50 cursor-pointer transition-colors"
                onClick={() => navigate(`/trace/${s.request_id}`)}
              >
                <td className="px-4 py-2.5 font-mono text-xs text-gray-600" title={s.request_id}>
                  {s.request_id?.slice(0, 16)}…
                </td>
                <td className="px-4 py-2.5 text-gray-600 text-xs whitespace-nowrap">
                  {formatTime(s.ts_ms)}
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={normalizeStatus(s.status)} />
                </td>
                <td className="px-4 py-2.5 text-gray-600">
                  {s.total_latency_ms != null ? `${s.total_latency_ms}ms` : '—'}
                </td>
                <td className="px-4 py-2.5 text-gray-500 text-xs">
                  {s.reason_code ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      <div className="flex items-center justify-between text-sm text-gray-500">
        <span>共 {total} 条</span>
        <div className="flex items-center gap-1">
          <button
            className="btn-secondary px-2 py-1 text-xs"
            disabled={page <= 1}
            onClick={() => { const p = page - 1; setPage(p); load(p) }}
          >
            上一页
          </button>
          <span className="px-2">{page} / {totalPages}</span>
          <button
            className="btn-secondary px-2 py-1 text-xs"
            disabled={page >= totalPages}
            onClick={() => { const p = page + 1; setPage(p); load(p) }}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  )
}
