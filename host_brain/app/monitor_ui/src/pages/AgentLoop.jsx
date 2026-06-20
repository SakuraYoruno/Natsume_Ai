import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  RefreshCw,
  Zap,
} from 'lucide-react'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import { runAgentLoopStream } from '../features/execution/api.js'
import PageHero from '../shared/ui/PageHero.jsx'
import {
  AgentLoopAssertionPanel,
  AgentLoopEventsPanel,
  AgentLoopHistoryPanel,
  AgentLoopRunControls,
  AgentLoopRunSummaryCard,
} from '../features/execution/components/AgentLoopConsoleSections.jsx'
import { useAgentLoopConsoleQuery } from '../features/execution/agentLoopQueries.js'
import {
  buildAgentLoopConsoleViewModel,
  buildAgentLoopRouteErrorEvent,
  buildAgentLoopRunRequest,
  buildAgentLoopRunSummary,
  isAgentLoopDoneEvent,
} from '../features/execution/agentLoopModel.js'
import { consumeSseBuffer } from '../shared/streaming/sse.js'
import useAppStore from '../store/useAppStore.js'

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
          <AgentLoopRunControls
            goal={goal}
            setGoal={setGoal}
            maxRounds={maxRounds}
            setMaxRounds={setMaxRounds}
            roundTimeout={roundTimeout}
            setRoundTimeout={setRoundTimeout}
            totalTimeout={totalTimeout}
            setTotalTimeout={setTotalTimeout}
            useMock={useMock}
            setUseMock={setUseMock}
            useEdgeRpc={useEdgeRpc}
            setUseEdgeRpc={setUseEdgeRpc}
            imageConsent={imageConsent}
            setImageConsent={setImageConsent}
            running={running}
            onRun={handleRun}
            onStop={handleStop}
          />

          <AgentLoopRunSummaryCard runSummary={runSummary} />
        </div>

        <div className="space-y-4">
          <AgentLoopEventsPanel events={events} eventsEndRef={eventsEndRef} />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <AgentLoopHistoryPanel loopHistory={loopHistory} />
            <AgentLoopAssertionPanel uiAssertionData={uiAssertionData} />
          </div>
        </div>
      </div>
    </div>
  )
}
