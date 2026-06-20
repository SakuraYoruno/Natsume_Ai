import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Database, Pause, Play, RefreshCw, Search, Trash2 } from 'lucide-react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useErrorLogBatchQuery } from '../features/observability/logQueries.js'
import {
  buildLogLineView,
  buildLogLiveStatusLabel,
  buildLogStreamViewModel,
  clampLogPollingInterval,
  filterLogEntries,
  getLogBatchEntries,
  mergeLogEntries,
} from '../features/observability/logStreamModel.js'
import useAppStore from '../store/useAppStore.js'

function LogLine({ entry, highlight }) {
  const { level, style, timeLabel, parts } = buildLogLineView(entry, highlight)

  return (
    <div className={`flex gap-2 border-b border-gray-100 px-3 py-1 font-mono text-xs ${style.bg}`}>
      <span className="w-20 shrink-0 text-gray-400">{timeLabel}</span>
      <span className={`shrink-0 rounded px-1.5 py-0 text-[10px] font-semibold ${style.badge}`}>{level}</span>
      <span className={`${style.text} break-all`}>
        {parts.map((part, index) => (
          part.highlighted ? (
            <mark key={`${entry.ts_ms}-${index}`} className="rounded bg-yellow-200 px-0.5 text-yellow-900">
              {part.text}
            </mark>
          ) : (
            <React.Fragment key={`${entry.ts_ms}-${index}`}>{part.text}</React.Fragment>
          )
        ))}
      </span>
    </div>
  )
}

export default function LogStream() {
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [logs, setLogs] = useState([])
  const [paused, setPaused] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [levelFilter, setLevelFilter] = useState('ALL')
  const [autoScroll, setAutoScroll] = useState(true)
  const logEndRef = useRef(null)
  const {
    logBatch,
    batchError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useErrorLogBatchQuery({
    windowMinutes: 60,
    limit: 100,
    pollingEnabled: pollingEnabled && !paused,
    pollingTick,
    pollingInterval: clampLogPollingInterval(pollingInterval),
  })

  useEffect(() => {
    const entries = getLogBatchEntries(logBatch)
    if (!entries.length) return
    setLogs((prev) => mergeLogEntries(prev, entries))
  }, [lastUpdatedAt, logBatch])

  useEffect(() => {
    if (autoScroll) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  const filtered = useMemo(
    () => filterLogEntries(logs, { keyword, levelFilter }),
    [keyword, levelFilter, logs],
  )
  const { heroMetaItems, sampleMeta, noticeView } = buildLogStreamViewModel({
    logBatch,
    logs,
    filteredCount: filtered.length,
    batchError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    pollingInterval,
    autoScroll,
    windowMinutes: 60,
  })

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

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      {noticeView.batchError ? (
        <NoticeBanner variant={noticeView.batchErrorVariant} title="日志批次加载异常">
          {noticeView.batchError}
        </NoticeBanner>
      ) : null}

      {noticeView.noSample ? (
        <NoticeBanner variant="info" title="当前暂无生理日志">
          系统当前还没有可观测的错误或异常日志写入。
        </NoticeBanner>
      ) : null}

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
            <button className="btn-secondary text-xs" onClick={() => refetch()} disabled={loading || refreshing}>
              <RefreshCw size={12} className={loading || refreshing ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>

          <div className="flex items-center gap-4 px-1 text-xs text-gray-500">
            <span><Database size={11} className="inline" /> {filtered.length}/{logs.length} 条</span>
            <span className={paused ? 'font-medium text-orange-500' : 'text-green-500'}>
              {buildLogLiveStatusLabel({ paused, pollingInterval })}
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
