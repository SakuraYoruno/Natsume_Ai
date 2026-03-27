import React, { useEffect, useState, useCallback, useRef } from 'react'
import { RefreshCw, Search, Filter, Pause, Play, ArrowDown, Trash2, Database } from 'lucide-react'
import { fetchErrors } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

function formatTime(tsMs) {
  if (!tsMs) return ''
  return new Date(tsMs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })
}

const LEVEL_STYLES = {
  ERROR:   { bg: 'bg-red-50',    text: 'text-red-700',    badge: 'bg-red-100 text-red-700' },
  WARNING: { bg: 'bg-orange-50', text: 'text-orange-600', badge: 'bg-orange-100 text-orange-700' },
  INFO:    { bg: '',             text: 'text-gray-700',   badge: 'bg-blue-100 text-blue-700' },
  DEBUG:   { bg: '',             text: 'text-gray-500',   badge: 'bg-gray-100 text-gray-500' },
}

function LogLine({ entry, highlight }) {
  const level = (entry.reason_code?.includes('BLOCK') || entry.reason_code?.includes('FAIL'))
    ? 'ERROR' : (entry.reason_code?.includes('DEGRADE') || entry.reason_code?.includes('TIMEOUT'))
    ? 'WARNING' : 'INFO'
  const style = LEVEL_STYLES[level] || LEVEL_STYLES.INFO

  // 高亮关键词
  let text = `[${entry.stage || '—'}] ${entry.reason_code || '—'} | ${entry.detail || ''}`
  if (highlight) {
    const regex = new RegExp(`(${highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
    const parts = text.split(regex)
    return (
      <div className={`flex gap-2 px-3 py-1 font-mono text-xs ${style.bg} border-b border-gray-100`}>
        <span className="text-gray-400 shrink-0 w-20">{formatTime(entry.ts_ms)}</span>
        <span className={`shrink-0 px-1.5 py-0 rounded text-[10px] font-semibold ${style.badge}`}>{level}</span>
        <span className={`${style.text} break-all`}>
          {parts.map((p, i) => regex.test(p) ? <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">{p}</mark> : p)}
        </span>
      </div>
    )
  }

  return (
    <div className={`flex gap-2 px-3 py-1 font-mono text-xs ${style.bg} border-b border-gray-100`}>
      <span className="text-gray-400 shrink-0 w-20">{formatTime(entry.ts_ms)}</span>
      <span className={`shrink-0 px-1.5 py-0 rounded text-[10px] font-semibold ${style.badge}`}>{level}</span>
      <span className={`${style.text} break-all`}>{text}</span>
    </div>
  )
}

export default function LogStream() {
  const pollingInterval = useAppStore(s => s.pollingInterval)
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [paused, setPaused] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [levelFilter, setLevelFilter] = useState('ALL')
  const [autoScroll, setAutoScroll] = useState(true)
  const logEndRef = useRef(null)
  const lastTsRef = useRef(0)

  const load = useCallback(async () => {
    if (paused) return
    try {
      setLoading(true)
      const res = await fetchErrors({ window_minutes: 60, limit: 200 })
      const entries = res?.recent_errors || res?.errors || []
      // 追加新日志（去重）
      setLogs(prev => {
        const existingTs = new Set(prev.map(e => e.ts_ms))
        const newEntries = entries.filter(e => !existingTs.has(e.ts_ms))
        const merged = [...prev, ...newEntries].slice(-500) // 保留最近 500 条
        return merged
      })
    } catch {} finally { setLoading(false) }
  }, [paused])

  useEffect(() => {
    load()
    const timer = setInterval(load, Math.min(pollingInterval, 15) * 1000)
    return () => clearInterval(timer)
  }, [load, pollingInterval])

  useEffect(() => {
    if (autoScroll) logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, autoScroll])

  const filtered = logs.filter(e => {
    if (keyword && !JSON.stringify(e).toLowerCase().includes(keyword.toLowerCase())) return false
    if (levelFilter === 'ERROR' && !(e.reason_code?.includes('BLOCK') || e.reason_code?.includes('FAIL'))) return false
    if (levelFilter === 'WARNING' && !(e.reason_code?.includes('DEGRADE') || e.reason_code?.includes('TIMEOUT'))) return false
    return true
  })

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">日志流</h1>
          <p className="text-xs text-gray-500">审计日志实时展示 · 关键词高亮 · 级别过滤</p>
        </div>
        <div className="flex items-center gap-2">
          {/* 搜索 */}
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="pl-7 pr-2 py-1.5 text-xs border rounded w-40 focus:outline-none focus:ring-1 focus:ring-primary-200"
              placeholder="搜索…" value={keyword} onChange={e => setKeyword(e.target.value)} />
          </div>
          {/* 级别过滤 */}
          <select className="text-xs border rounded px-2 py-1.5" value={levelFilter} onChange={e => setLevelFilter(e.target.value)}>
            <option value="ALL">全部级别</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
          </select>
          {/* 暂停/继续 */}
          <button className={`btn-secondary text-xs ${paused ? 'bg-orange-50 text-orange-600' : ''}`} onClick={() => setPaused(!paused)}>
            {paused ? <><Play size={12} /> 继续</> : <><Pause size={12} /> 暂停</>}
          </button>
          {/* 自动滚动 */}
          <button className={`btn-secondary text-xs ${autoScroll ? 'bg-blue-50 text-blue-600' : ''}`} onClick={() => setAutoScroll(!autoScroll)}>
            <ArrowDown size={12} /> {autoScroll ? '跟踪' : '固定'}
          </button>
          {/* 清空 */}
          <button className="btn-secondary text-xs" onClick={() => setLogs([])}>
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* 统计条 */}
      <div className="flex items-center gap-4 text-xs text-gray-500 mb-2 px-1">
        <span><Database size={11} className="inline" /> {filtered.length}/{logs.length} 条</span>
        <span className={paused ? 'text-orange-500 font-medium' : 'text-green-500'}>
          {paused ? '⏸ 已暂停' : `● 实时（${Math.min(pollingInterval, 15)}s）`}
        </span>
        {keyword && <span className="text-yellow-600">搜索: "{keyword}"</span>}
      </div>

      {/* 日志面板 */}
      <div className="flex-1 overflow-y-auto card bg-gray-900/[0.02] border">
        {filtered.length === 0 && !loading && (
          <div className="text-center text-gray-400 py-12 text-xs">暂无日志</div>
        )}
        {filtered.map((e, i) => <LogLine key={`${e.ts_ms}-${i}`} entry={e} highlight={keyword} />)}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}
