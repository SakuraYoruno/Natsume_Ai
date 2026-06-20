import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import {
  ChatDebugMainColumn,
  ChatTraceSidebar,
} from '../features/observability/components/chatDebug/ChatDebugConsoleSections.jsx'
import { sendChatDebugStream } from '../features/observability/chatDebugApi.js'
import {
  buildChatDebugTraceView,
} from '../features/observability/chatDebugModel.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useTraceSnapshotQuery } from '../features/observability/traceQueries.js'
import { consumeSseBuffer } from '../shared/streaming/sse.js'
import useAppStore from '../store/useAppStore.js'

export default function ChatDebug() {
  const setSelectedRequestId = useAppStore((state) => state.setSelectedRequestId)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [message, setMessage] = useState('')
  const [chatHistory, setChatHistory] = useState([])
  const [sseEvents, setSseEvents] = useState([])
  const [streaming, setStreaming] = useState(false)
  const [currentResponse, setCurrentResponse] = useState('')
  const [requestId, setRequestId] = useState(null)
  const [error, setError] = useState(null)
  const [stats, setStats] = useState(null)
  const abortRef = useRef(null)
  const chatEndRef = useRef(null)
  const eventsEndRef = useRef(null)
  const {
    traceSnapshot,
    traceError,
    loading: traceLoading,
    refreshing: traceRefreshing,
    stale: traceStale,
    lastUpdatedAt: traceUpdatedAt,
    refetch: refetchTraceSnapshot,
  } = useTraceSnapshotQuery({
    requestId,
    enabled: Boolean(requestId),
    pollingEnabled: Boolean(requestId) && !streaming && pollingEnabled,
    pollingInterval,
  })

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory, currentResponse])

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [sseEvents])

  const {
    chainEvents,
    latestEvent,
    traceLatestNode,
    traceIssueCount,
    traceNodeCount,
    traceBusy,
    traceInputPreview,
    traceOutputPreview,
    traceLoadState,
    heroMetaItems,
  } = useMemo(() => buildChatDebugTraceView({
    sseEvents,
    chatHistory,
    currentResponse,
    traceSnapshot,
    traceError,
    traceLoading,
    traceRefreshing,
    traceStale,
    requestId,
  }), [
    chatHistory,
    currentResponse,
    requestId,
    sseEvents,
    traceError,
    traceLoading,
    traceRefreshing,
    traceSnapshot,
    traceStale,
  ])

  useEffect(() => {
    if (requestId && !streaming) {
      void refetchTraceSnapshot()
    }
  }, [refetchTraceSnapshot, requestId, streaming])

  const sendMessage = useCallback(async () => {
    if (!message.trim() || streaming) return

    const userText = message.trim()
    setMessage('')
    setError(null)
    setChatHistory((previous) => [...previous, { role: 'user', content: userText }])
    setSseEvents([])
    setCurrentResponse('')
    setRequestId(null)
    setStats(null)
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    const startTime = Date.now()
    let eventCount = 0

    try {
      const response = await sendChatDebugStream(
        {
          message: userText,
          session_id: `debug-${Date.now()}`,
        },
        { signal: controller.signal }
      )

      if (!response.body) {
        throw new Error('响应缺少事件流')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const { events, remainder } = consumeSseBuffer(buffer)
        buffer = remainder

        for (const event of events) {
          eventCount += 1
          const normalizedEvent = { ...event, ts_ms: event.ts_ms || Date.now() }
          setSseEvents((previous) => [...previous, normalizedEvent])

          if (event.request_id) {
            setRequestId(event.request_id)
            setSelectedRequestId(event.request_id)
          }

          if (event.token) fullText += event.token
          if (event.content) fullText = event.content
          if (event.text) fullText = event.text
          if (event.rendered_ja) fullText = event.rendered_ja
          if (event.narrative_zh) fullText = event.narrative_zh

          setCurrentResponse(fullText)
        }
      }

      setStats({
        elapsed: Date.now() - startTime,
        eventCount,
      })
      setChatHistory((previous) => [
        ...previous,
        { role: 'assistant', content: fullText || '（本次请求没有返回可展示文本）' },
      ])
      setCurrentResponse('')
    } catch (requestError) {
      if (requestError.name !== 'AbortError') {
        const messageText = requestError.message || '请求失败'
        setError(messageText)
        setChatHistory((previous) => [...previous, { role: 'error', content: `请求失败：${messageText}` }])
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [message, setSelectedRequestId, streaming])

  const stopStream = useCallback(() => {
    abortRef.current?.abort()
    setStreaming(false)
  }, [])

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-4">
      <ChatDebugMainColumn
        stats={stats}
        streaming={streaming}
        heroMetaItems={heroMetaItems}
        runtimeCard={(
          <DigitalLifeRuntimeCard
            runtimeSnapshot={runtimeSnapshot}
            promptStats={promptStats}
            compact
          />
        )}
        error={error}
        chatHistory={chatHistory}
        currentResponse={currentResponse}
        chatEndRef={chatEndRef}
        message={message}
        setMessage={setMessage}
        onSend={sendMessage}
        onStop={stopStream}
      />

      <ChatTraceSidebar
        requestId={requestId}
        traceBusy={traceBusy}
        traceLoadState={traceLoadState}
        traceError={traceError}
        traceUpdatedAt={traceUpdatedAt}
        chainEvents={chainEvents}
        traceSnapshot={traceSnapshot}
        traceNodeCount={traceNodeCount}
        sseEvents={sseEvents}
        traceIssueCount={traceIssueCount}
        traceLatestNode={traceLatestNode}
        latestEvent={latestEvent}
        traceInputPreview={traceInputPreview}
        traceOutputPreview={traceOutputPreview}
        eventsEndRef={eventsEndRef}
        onRefreshTrace={() => void refetchTraceSnapshot()}
      />
    </div>
  )
}
