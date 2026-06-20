import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  History,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  XCircle,
  Zap,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import { runAgentLoopStream } from '../features/execution/api.js'
import PageHero from '../shared/ui/PageHero.jsx'
import { useAgentLoopConsoleQuery } from '../features/execution/agentLoopQueries.js'
import {
  buildAgentLoopConsoleViewModel,
  buildAgentLoopRouteErrorEvent,
  buildAgentLoopRunRequest,
  buildAgentLoopRunSummary,
  getAgentLoopPhaseClass,
  isAgentLoopRunReady,
  isAgentLoopDoneEvent,
} from '../features/execution/agentLoopModel.js'
import { formatTime } from '../shared/formatters.js'
import { consumeSseBuffer } from '../shared/streaming/sse.js'
import useAppStore from '../store/useAppStore.js'

function PhaseBadge({ phase }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${getAgentLoopPhaseClass(phase)}`}>
      {phase || 'UNKNOWN'}
    </span>
  )
}

export default function AgentLoop() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [goal, setGoal] = useState('')
  const [maxRounds, setMaxRounds] = useState(5)
  const [roundTimeout, setRoundTimeout] = useState(30)
  const [totalTimeout, setTotalTimeout] = useState(180)
  const [useMock, setUseMock] = useState(false)
  const [useEdgeRpc, setUseEdgeRpc] = useState(false)
  const [imageConsent, setImageConsent] = useState(false)
  const [running, setRunning] = useState(false)
  const [events, setEvents] = useState([])
  const [loopResult, setLoopResult] = useState(null)
  const abortRef = useRef(null)
  const eventsEndRef = useRef(null)
  const {
    agentInfo,
    infoError,
    uiAssertionData,
    agentLoopStats,
    loopHistory,
    statsError,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useAgentLoopConsoleQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const runSummary = useMemo(() => buildAgentLoopRunSummary(loopResult), [loopResult])
  const {
    loadState: agentLoopLoadState,
    heroTitle,
    heroMetaItems,
    sampleSize,
    metricCards,
  } = useMemo(() => buildAgentLoopConsoleViewModel({
    agentInfo,
    agentLoopStats,
    uiAssertionData,
    loopHistory,
    loading,
    refreshing,
    error,
    infoError,
    statsError,
    stale,
  }), [
    agentInfo,
    agentLoopStats,
    error,
    infoError,
    loading,
    loopHistory,
    refreshing,
    stale,
    statsError,
    uiAssertionData,
  ])

  const handleRun = useCallback(async () => {
    const request = buildAgentLoopRunRequest({
      goal,
      maxRounds,
      roundTimeout,
      totalTimeout,
      useMock,
      useEdgeRpc,
      imageConsent,
    })
    if (!request) return

    setRunning(true)
    setEvents([])
    setLoopResult(null)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const response = await runAgentLoopStream(
        request,
        { signal: controller.signal }
      )

      if (!response.body) {
        throw new Error('响应缺少事件流')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const { events: nextEvents, remainder } = consumeSseBuffer(buffer)
        buffer = remainder

        for (const event of nextEvents) {
          const normalizedEvent = { ...event, _ts: Date.now() }
          setEvents((previous) => [...previous, normalizedEvent])
          if (isAgentLoopDoneEvent(event)) {
            setLoopResult(event)
          }
        }
      }

      await refetch()
    } catch (error) {
      if (error.name !== 'AbortError') {
        setEvents((previous) => [
          ...previous,
          buildAgentLoopRouteErrorEvent(error),
        ])
      }
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }, [goal, imageConsent, maxRounds, refetch, roundTimeout, totalTimeout, useEdgeRpc, useMock])

  const handleStop = useCallback(() => {
    abortRef.current?.abort()
    setRunning(false)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900">
            <Bot size={20} className="text-indigo-600" />
            Agent Loop
          </h1>
          <p className="text-sm text-gray-500">执行视觉自动化回路，实时观察轮次、阶段和最终结果。</p>
        </div>
        <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Zap}
        eyebrow="自动化执行回路"
        title={heroTitle}
        description="适合做端到端页面动作验证、视觉观测和自动执行链复验。"
        gradientClass="from-indigo-50 via-white to-sky-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={agentLoopLoadState}
            sampleSize={sampleSize}
            sampleLabel="执行样本"
            updatedAt={lastUpdatedAt}
            source="agent info + loop stats + history"
            definition="Agent Loop 页把控制台配置、最近执行与断言守卫摘要统一交给 query 层，页面本身只保留运行控制与 SSE 事件流。"
          />
        )}
      />

      {infoError ? <NoticeBanner variant="warning" title="Agent 配置加载失败">{infoError}</NoticeBanner> : null}
      {statsError ? <NoticeBanner variant="error" title="统计加载失败">{statsError}</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="Agent Loop 数据加载失败">{error.message}</NoticeBanner> : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard
            key={card.key}
            label={card.label}
            value={card.value}
            hint={card.hint}
            tone={card.tone}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card space-y-4 p-4">
            <div className="text-sm font-semibold text-gray-800">执行参数</div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">执行目标</label>
              <textarea
                className="w-full resize-none rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-300"
                rows={4}
                placeholder="例如：打开设置页，切换到数字生命体总览，确认运行摘要卡可见。"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                disabled={running}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">最大轮次：{maxRounds}</label>
              <input type="range" min={1} max={20} step={1} value={maxRounds} onChange={(event) => setMaxRounds(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">单轮超时：{roundTimeout}s</label>
              <input type="range" min={5} max={120} step={5} value={roundTimeout} onChange={(event) => setRoundTimeout(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">总超时：{totalTimeout}s</label>
              <input type="range" min={10} max={600} step={10} value={totalTimeout} onChange={(event) => setTotalTimeout(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
            </div>

            <div className="space-y-2">
              {[
                { label: '使用兜底模式', value: useMock, set: setUseMock },
                { label: '使用 edge RPC', value: useEdgeRpc, set: setUseEdgeRpc },
                { label: '允许上传截图', value: imageConsent, set: setImageConsent },
              ].map((item) => (
                <label key={item.label} className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox"
                    checked={item.value}
                    onChange={(event) => item.set(event.target.checked)}
                    disabled={running}
                    className="accent-indigo-500"
                  />
                  {item.label}
                </label>
              ))}
            </div>

            <div className="flex gap-2">
              <button
                className="flex flex-1 items-center justify-center gap-1.5 rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handleRun}
                disabled={running || !isAgentLoopRunReady(goal)}
              >
                {running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
                {running ? '执行中' : '启动'}
              </button>
              {running ? (
                <button className="rounded border border-red-300 px-3 py-2 text-red-600 hover:bg-red-50" onClick={handleStop}>
                  <Square size={14} />
                </button>
              ) : null}
            </div>
          </div>

          {runSummary ? (
            <div className={`card border p-4 ${runSummary.success ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}>
              <div className="mb-2 flex items-center gap-2">
                {runSummary.success ? <CheckCircle2 size={16} className="text-green-600" /> : <XCircle size={16} className="text-red-500" />}
                <span className={`text-sm font-semibold ${runSummary.success ? 'text-green-700' : 'text-red-600'}`}>
                  {runSummary.success ? '目标已达成' : '目标未达成'}
                </span>
              </div>
              <div className="space-y-1 text-xs text-gray-600">
                <div>总轮次：{runSummary.rounds}</div>
                <div>总耗时：{runSummary.latency === '—' ? '—' : `${runSummary.latency}ms`}</div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="space-y-4">
          <div className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold text-gray-800">实时事件流</div>
              <span className="text-xs text-gray-400">{events.length} 条</span>
            </div>
            {events.length > 0 ? (
              <div className="max-h-[420px] space-y-2 overflow-y-auto">
                {events.map((event, index) => (
                  <div key={`${event._ts || index}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3">
                    <div className="flex items-center gap-2">
                      <PhaseBadge phase={event.phase} />
                      <span className="text-xs text-gray-400">{formatTime(event._ts)}</span>
                      {event.round != null ? <span className="text-xs text-gray-500">第 {event.round} 轮</span> : null}
                    </div>
                    <div className="mt-2 text-sm text-gray-700">
                      {event.summary || event.detail || event.error || event.raw || '无附加说明'}
                    </div>
                  </div>
                ))}
                <div ref={eventsEndRef} />
              </div>
            ) : (
              <EmptyStateCard
                title="等待执行"
                hint="启动 Agent Loop 后，这里会展示完整轮次事件。"
                className="p-8"
              />
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className="card p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
                <History size={14} />
                最近执行
              </div>
              {loopHistory.length > 0 ? (
                <div className="space-y-2">
                  {loopHistory.map((item) => (
                    <div key={`${item.request_id}-${item.ts_ms}`} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-sm font-medium text-gray-800">{item.goal || '未记录目标'}</div>
                        {item.goal_achieved == null ? null : item.goal_achieved ? (
                          <CheckCircle2 size={14} className="text-green-600" />
                        ) : (
                          <XCircle size={14} className="text-red-500" />
                        )}
                      </div>
                      <div className="mt-1 space-y-1 text-xs text-gray-500">
                        <div>状态：{item.final_status || '—'}</div>
                        <div>轮次：{item.total_rounds ?? '—'} · 耗时：{item.total_latency_ms != null ? `${item.total_latency_ms}ms` : '—'}</div>
                        <div>模式：{item.client_mode || '—'} · 时间：{formatTime(item.ts_ms)}</div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyStateCard title="暂无历史执行" hint="等执行完成后，这里会显示近 24 小时记录。" className="p-6" />
              )}
            </div>

            <div className="card p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
                <ShieldAlert size={14} />
                断言守卫
              </div>
              {uiAssertionData ? (
                <div className="space-y-3 text-sm">
                  <div className="rounded-lg bg-gray-50 px-3 py-3">
                    <div className="text-xs text-gray-400">总断言</div>
                    <div className="mt-1 text-lg font-semibold text-gray-900">{uiAssertionData.total_assertions ?? 0}</div>
                  </div>
                  <div className="rounded-lg bg-gray-50 px-3 py-3">
                    <div className="text-xs text-gray-400">敏感拦截</div>
                    <div className="mt-1 text-lg font-semibold text-orange-600">{uiAssertionData.sensitive_blocked ?? 0}</div>
                  </div>
                  <div className="rounded-lg bg-gray-50 px-3 py-3">
                    <div className="text-xs text-gray-400">前置 / 后置失败</div>
                    <div className="mt-1 text-lg font-semibold text-rose-600">
                      {uiAssertionData.precondition_failed ?? 0} / {uiAssertionData.postcondition_failed ?? 0}
                    </div>
                  </div>
                </div>
              ) : (
                <EmptyStateCard title="暂无断言样本" hint="等待自动化执行后再查看守卫统计。" className="p-6" />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
