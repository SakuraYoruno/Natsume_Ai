import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Database, Pause, Play, RefreshCw, Search, Trash2 } from 'lucide-react'
import { fetchErrors } from '../api/obs.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { formatAlertTitle, humanizeToken } from '../utils/digitalLife.js'

function formatTime(tsMs) {
  if (!tsMs) return ''
  return new Date(tsMs).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
  })
}

const LEVEL_STYLES = {
  ERROR: { bg: 'bg-red-50', text: 'text-red-700', badge: 'bg-red-100 text-red-700' },
  WARNING: { bg: 'bg-orange-50', text: 'text-orange-600', badge: 'bg-orange-100 text-orange-700' },
  INFO: { bg: '', text: 'text-gray-700', badge: 'bg-blue-100 text-blue-700' },
  DEBUG: { bg: '', text: 'text-gray-500', badge: 'bg-gray-100 text-gray-500' },
}

function classifyLogLevel(entry) {
  const reasonCode = String(entry?.reason_code || '').toUpperCase()
  if (reasonCode.includes('BLOCK') || reasonCode.includes('FAIL')) return 'ERROR'
  if (reasonCode.includes('DEGRADE') || reasonCode.includes('TIMEOUT')) return 'WARNING'
  return 'INFO'
}

function LogLine({ entry, highlight }) {
  const level = classifyLogLevel(entry)
  const style = LEVEL_STYLES[level] || LEVEL_STYLES.INFO
  const text = `[${entry.stage ? humanizeToken(entry.stage) : '—'}] ${entry.reason_code ? formatAlertTitle(entry.reason_code) : '—'} | ${entry.detail || ''}`

  if (highlight) {
    const escaped = highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(`(${escaped})`, 'gi')
    const parts = text.split(regex)

    return (
      <div className={`flex gap-2 border-b border-gray-100 px-3 py-1 font-mono text-xs ${style.bg}`}>
        <span className="w-20 shrink-0 text-gray-400">{formatTime(entry.ts_ms)}</span>
        <span className={`shrink-0 rounded px-1.5 py-0 text-[10px] font-semibold ${style.badge}`}>{level}</span>
        <span className={`${style.text} break-all`}>
          {parts.map((part, index) => (
            index % 2 === 1 ? (
              <mark key={`${entry.ts_ms}-${index}`} className="rounded bg-yellow-200 px-0.5 text-yellow-900">
                {part}
              </mark>
            ) : (
              <React.Fragment key={`${entry.ts_ms}-${index}`}>{part}</React.Fragment>
            )
          ))}
        </span>
      </div>
    )
  }

  return (
    <div className={`flex gap-2 border-b border-gray-100 px-3 py-1 font-mono text-xs ${style.bg}`}>
      <span className="w-20 shrink-0 text-gray-400">{formatTime(entry.ts_ms)}</span>
      <span className={`shrink-0 rounded px-1.5 py-0 text-[10px] font-semibold ${style.badge}`}>{level}</span>
      <span className={`${style.text} break-all`}>{text}</span>
    </div>
  )
}

export default function LogStream() {
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [paused, setPaused] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [levelFilter, setLevelFilter] = useState('ALL')
  const [autoScroll, setAutoScroll] = useState(true)
  const logEndRef = useRef(null)

  const load = useCallback(async () => {
    if (paused) return
    try {
      setLoading(true)
      const response = await fetchErrors({ window_minutes: 60, limit: 100 })
      const entries = response?.recent_errors || response?.errors || []
      setLogs((prev) => {
        const existing = new Set(prev.map((item) => `${item.ts_ms}-${item.reason_code}-${item.stage}`))
        const next = entries.filter((item) => !existing.has(`${item.ts_ms}-${item.reason_code}-${item.stage}`))
        return [...prev, ...next].slice(-500)
      })
    } catch {
    } finally {
      setLoading(false)
    }
  }, [paused])

  useEffect(() => {
    load()
    const timer = setInterval(load, Math.min(pollingInterval, 15) * 1000)
    return () => clearInterval(timer)
  }, [load, pollingInterval])

  useEffect(() => {
    if (autoScroll) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  const filtered = useMemo(() => logs.filter((entry) => {
    if (keyword && !JSON.stringify(entry).toLowerCase().includes(keyword.toLowerCase())) return false
    if (levelFilter === 'ERROR' && classifyLogLevel(entry) !== 'ERROR') return false
    if (levelFilter === 'WARNING' && classifyLogLevel(entry) !== 'WARNING') return false
    return true
  }), [keyword, levelFilter, logs])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">生理日志流</h1>
        <p className="text-sm text-gray-500">实时观察生命体审计日志、异常事件和神经链路故障明细。</p>
      </div>

      <PageHero
        icon={Database}
        eyebrow="实时生理日志观察窗"
        title={paused ? '生理日志流当前已暂停' : '生理日志流正在实时更新'}
        description="支持关键词高亮、级别过滤、自动滚动和快速清空，适合盯运行时异常与神经链路抖动。"
        gradientClass="from-slate-50 via-white to-cyan-50"
        metaItems={[
          `当前显示：${filtered.length} 条`,
          `缓存总量：${logs.length} 条`,
          `拉取周期：${Math.min(pollingInterval, 15)}s`,
          autoScroll ? '自动滚动：开启' : '自动滚动：关闭',
        ]}
      />

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <div className="flex min-h-[32rem] flex-col">
        <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className="w-44 rounded border border-gray-300 py-1.5 pl-7 pr-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary-200"
                placeholder="搜索…"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </div>
            <select className="text-xs border rounded px-2 py-1.5" value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)}>
              <option value="ALL">全部级别</option>
              <option value="ERROR">ERROR</option>
              <option value="WARNING">WARNING</option>
            </select>
            <button className={`btn-secondary text-xs ${paused ? 'bg-orange-50 text-orange-600' : ''}`} onClick={() => setPaused((value) => !value)}>
              {paused ? <><Play size={12} /> 继续</> : <><Pause size={12} /> 暂停</>}
            </button>
            <button className={`btn-secondary text-xs ${autoScroll ? 'bg-blue-50 text-blue-600' : ''}`} onClick={() => setAutoScroll((value) => !value)}>
              <ArrowDown size={12} /> {autoScroll ? '跟随' : '固定'}
            </button>
            <button className="btn-secondary text-xs" onClick={() => setLogs([])}>
              <Trash2 size={12} />
            </button>
            <button className="btn-secondary text-xs" onClick={() => load()} disabled={loading}>
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>

          <div className="flex items-center gap-4 px-1 text-xs text-gray-500">
            <span><Database size={11} className="inline" /> {filtered.length}/{logs.length} 条</span>
            <span className={paused ? 'font-medium text-orange-500' : 'text-green-500'}>
              {paused ? '⏸ 已暂停' : `● 实时：${Math.min(pollingInterval, 15)}s`}
            </span>
            {keyword ? <span className="text-yellow-600">搜索: "{keyword}"</span> : null}
          </div>
        </div>

        <div className="card flex-1 overflow-y-auto border bg-gray-900/[0.02]">
          {filtered.length === 0 && !loading ? (
            <EmptyStateCard title="暂无日志" hint="等待审计事件写入后会实时显示。" className="py-12" />
          ) : null}
          {filtered.map((entry, index) => (
            <LogLine key={`${entry.ts_ms}-${index}`} entry={entry} highlight={keyword} />
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  )
}
