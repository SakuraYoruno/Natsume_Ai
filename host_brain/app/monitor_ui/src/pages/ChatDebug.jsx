import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  GitBranch,
  Loader2,
  Send,
  Sparkles,
  User,
  Zap,
} from 'lucide-react'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import { getTraceNodeDisplayName } from '../components/trace/traceNodeText.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { fetchTraceNodes } from '../api/obs.js'
import {
  formatAlertTitle,
  formatDecision,
  formatEventPhase,
  formatSceneType,
  humanizeToken,
} from '../utils/digitalLife.js'

function parseSseBlock(block) {
  const data = block
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n')

  if (!data) return null

  try {
    return JSON.parse(data)
  } catch (_) {
    return { raw: data }
  }
}

function consumeSseBuffer(buffer) {
  const chunks = buffer.split('\n\n')
  const remainder = chunks.pop() ?? ''
  const events = chunks.map(parseSseBlock).filter(Boolean)
  return { events, remainder }
}

function PhaseBadge({ phase }) {
  const styles = {
    PRESENCE: 'bg-blue-100 text-blue-700',
    COMMIT: 'bg-green-100 text-green-700',
    REJECT: 'bg-red-100 text-red-700',
    CHUNK: 'bg-purple-100 text-purple-700',
    NODE: 'bg-orange-100 text-orange-700',
  }

  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${styles[phase] || 'bg-gray-100 text-gray-600'}`}>
      {formatEventPhase(phase)}
    </span>
  )
}

function EventRow({ event, index }) {
  const ts = event.ts_ms ? new Date(event.ts_ms).toLocaleTimeString('zh-CN') : `#${index + 1}`
  const phase = String(event.phase || event.event || '?').toUpperCase()

  return (
    <div className={`rounded-lg px-2 py-2 text-xs ${index % 2 === 0 ? 'bg-gray-50' : 'bg-white'}`}>
      <div className="flex items-center gap-2">
        <span className="w-20 shrink-0 text-gray-400">{ts}</span>
        <PhaseBadge phase={phase} />
        {event.node_id ? <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-600">{event.node_id}</span> : null}
      </div>
      <div className="mt-1 break-words text-gray-600">
        {event.event ? <span className="font-medium text-gray-800">{humanizeToken(event.event)} </span> : null}
        {event.scene_type ? <span>场景：{formatSceneType(event.scene_type)} </span> : null}
        {event.decision ? <span>决策：{formatDecision(event.decision)} </span> : null}
        {event.reason_code ? <span>原因：{formatAlertTitle(event.reason_code)} </span> : null}
        {event.content ? <span>{event.content.slice(0, 120)}</span> : null}
        {event.token ? <span>{event.token}</span> : null}
        {event.raw ? <span>{event.raw}</span> : null}
      </div>
    </div>
  )
}

function truncateDebugText(value, maxLength = 160) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

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
  const [traceSnapshot, setTraceSnapshot] = useState(null)
  const [traceLoading, setTraceLoading] = useState(false)
  const [traceError, setTraceError] = useState(null)
  const abortRef = useRef(null)
  const chatEndRef = useRef(null)
  const eventsEndRef = useRef(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory, currentResponse])

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [sseEvents])

  const chainEvents = useMemo(
    () => sseEvents.filter((event) => event.node_id || String(event.phase || '').toUpperCase() === 'NODE'),
    [sseEvents]
  )
  const latestEvent = sseEvents[sseEvents.length - 1] ?? null
  const traceSummary = traceSnapshot?.summary ?? {}
  const traceLatestNode = useMemo(() => {
    const nodes = Array.isArray(traceSnapshot?.nodes) ? [...traceSnapshot.nodes] : []
    if (nodes.length === 0) return null
    nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
    return nodes[nodes.length - 1]
  }, [traceSnapshot])
  const traceIssueCount = useMemo(() => (
    Array.isArray(traceSnapshot?.nodes)
      ? traceSnapshot.nodes.filter((node) => ['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'].includes(String(node?.status || '').toUpperCase())).length
      : 0
  ), [traceSnapshot])
  const traceInputPreview = truncateDebugText(traceSummary.input_text || chatHistory.filter((item) => item.role === 'user').slice(-1)[0]?.content, 180)
  const traceOutputPreview = truncateDebugText(
    traceSummary.output_text
    || traceSummary.failure_event
    || currentResponse
    || chatHistory.filter((item) => item.role === 'assistant').slice(-1)[0]?.content,
    180
  )

  const loadTraceSnapshot = useCallback(async () => {
    if (!requestId) {
      setTraceSnapshot(null)
      setTraceError(null)
      setTraceLoading(false)
      return
    }

    setTraceLoading(true)
    try {
      const data = await fetchTraceNodes(requestId)
      setTraceSnapshot(data)
      setTraceError(null)
    } catch (fetchError) {
      const messageText = fetchError.message || 'Trace 摘要拉取失败'
      setTraceSnapshot(null)
      setTraceError(messageText.includes('404') ? '链路摘要尚未落盘，流结束后会自动补拉。' : messageText)
    } finally {
      setTraceLoading(false)
    }
  }, [requestId])

  useEffect(() => {
    if (!requestId) {
      setTraceSnapshot(null)
      setTraceError(null)
      setTraceLoading(false)
      return
    }
    void loadTraceSnapshot()
  }, [requestId, loadTraceSnapshot])

  useEffect(() => {
    if (requestId && !streaming) {
      void loadTraceSnapshot()
    }
  }, [requestId, streaming, loadTraceSnapshot])

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
    setTraceSnapshot(null)
    setTraceError(null)
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    const startTime = Date.now()
    let eventCount = 0

    try {
      const response = await fetch('/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userText,
          session_id: `debug-${Date.now()}`,
        }),
        signal: controller.signal,
      })

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`)
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
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">Chat 调试</h1>
            <p className="text-xs text-gray-500">直接发消息，实时看事件流、请求 ID 和 Trace 入口。</p>
          </div>
          {stats ? (
            <div className="flex items-center gap-3 text-xs text-gray-500">
              <span><Clock size={11} className="mr-1 inline" />{stats.elapsed}ms</span>
              <span><Zap size={11} className="mr-1 inline" />{stats.eventCount} 事件</span>
            </div>
          ) : null}
        </div>

        <DigitalLifeNav />

        <div className="mt-3">
          <PageHero
            icon={Sparkles}
            eyebrow="对话实时调试"
            title={streaming ? '当前正在流式调试' : '手动发起一次对话调试'}
            description="适合联调 SSE 事件流、回复生成、链路节点推进以及请求到 Trace 的跳转闭环。"
            gradientClass="from-pink-50 via-white to-blue-50"
            metaItems={[
              `历史消息：${chatHistory.length}`,
              `SSE 事件：${sseEvents.length}`,
              traceSnapshot?.status ? `Trace：${traceSnapshot.status}` : `链路节点：${chainEvents.length}`,
              requestId ? '已有 Request ID' : '等待新请求',
            ]}
          />
        </div>

        <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

        {error ? (
          <div className="mt-3">
            <NoticeBanner variant="error" title="请求失败">{error}</NoticeBanner>
          </div>
        ) : null}

        <div className="card mt-3 flex-1 space-y-3 overflow-y-auto p-4">
          {chatHistory.length === 0 ? (
            <EmptyStateCard
              title="发一条消息开始调试"
              hint="右侧会实时展示 SSE 事件、链路节点和 Trace 跳转入口。"
              icon={Bot}
              className="py-12"
            />
          ) : null}

          {chatHistory.map((item, index) => (
            <div key={`${item.role}-${index}`} className={`flex gap-2 ${item.role === 'user' ? 'justify-end' : ''}`}>
              {item.role !== 'user' ? (
                <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${item.role === 'error' ? 'bg-red-100' : 'bg-primary-100'}`}>
                  {item.role === 'error'
                    ? <AlertTriangle size={14} className="text-red-500" />
                    : <Bot size={14} className="text-primary" />}
                </div>
              ) : null}

              <div
                className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                  item.role === 'user'
                    ? 'bg-primary text-white'
                    : item.role === 'error'
                      ? 'border border-red-200 bg-red-50 text-red-700'
                      : 'bg-gray-100 text-gray-800'
                }`}
              >
                <pre className="whitespace-pre-wrap font-sans">{item.content}</pre>
              </div>

              {item.role === 'user' ? (
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-200">
                  <User size={14} className="text-gray-600" />
                </div>
              ) : null}
            </div>
          ))}

          {currentResponse ? (
            <div className="flex gap-2">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-100">
                <Loader2 size={14} className="animate-spin text-primary" />
              </div>
              <div className="rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-800">
                <pre className="whitespace-pre-wrap font-sans">{currentResponse}<span className="animate-pulse">▌</span></pre>
              </div>
            </div>
          ) : null}
          <div ref={chatEndRef} />
        </div>

        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
            placeholder="输入消息，按 Enter 发送"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                sendMessage()
              }
            }}
            disabled={streaming}
          />
          {streaming ? (
            <button className="btn-secondary px-4" onClick={stopStream}>
              <AlertTriangle size={14} />
              停止
            </button>
          ) : (
            <button className="btn-primary px-4" onClick={sendMessage} disabled={!message.trim()}>
              <Send size={14} />
              发送
            </button>
          )}
        </div>
      </div>

      <div className="flex w-96 shrink-0 flex-col gap-3">
        <div className="card p-4">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-semibold text-gray-800">
              <GitBranch size={14} className="mr-1 inline" />
              当前请求链路
            </div>
            <div className="flex items-center gap-2">
              {requestId ? (
                <button className="btn-secondary text-xs" onClick={() => void loadTraceSnapshot()} disabled={traceLoading}>
                  {traceLoading ? <Loader2 size={12} className="animate-spin" /> : '刷新 Trace'}
                </button>
              ) : null}
              <span className="text-xs text-gray-400">{chainEvents.length} 节点事件</span>
            </div>
          </div>
          <div className="mt-3 space-y-2 text-xs text-gray-600">
            <div className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-gray-400">Request ID</div>
              {requestId ? (
                <div className="mt-1 space-y-2">
                  <a href={`#/trace/${requestId}`} className="inline-flex items-center gap-1 font-mono text-primary hover:underline">
                    <CheckCircle2 size={12} />
                    {requestId}
                  </a>
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={`#/trace/${requestId}`} className="btn-primary text-xs">打开 Trace 详情</a>
                    <a href="#/trace" className="btn-secondary text-xs">回到链路总览</a>
                  </div>
                </div>
              ) : (
                <div className="mt-1 text-gray-500">等待请求开始</div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg bg-gray-50 px-3 py-2">
                <div className="text-gray-400">Trace 状态</div>
                <div className="mt-1 font-medium text-gray-800">{traceSnapshot?.status || (requestId ? '拉取中' : '-')}</div>
              </div>
              <div className="rounded-lg bg-gray-50 px-3 py-2">
                <div className="text-gray-400">节点 / 事件</div>
                <div className="mt-1 font-medium text-gray-800">
                  {traceSnapshot?.node_count ?? chainEvents.length} / {sseEvents.length}
                </div>
              </div>
              <div className="rounded-lg bg-gray-50 px-3 py-2">
                <div className="text-gray-400">耗时</div>
                <div className="mt-1 font-medium text-gray-800">
                  {traceSnapshot?.total_latency_ms != null ? `${traceSnapshot.total_latency_ms}ms` : '-'}
                </div>
              </div>
              <div className="rounded-lg bg-gray-50 px-3 py-2">
                <div className="text-gray-400">待关注节点</div>
                <div className="mt-1 font-medium text-gray-800">{traceIssueCount}</div>
              </div>
            </div>

            <div className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-gray-400">当前节点</div>
              <div className="mt-1 font-medium text-gray-800">
                {traceLatestNode ? getTraceNodeDisplayName(traceLatestNode) : (latestEvent?.node_id || '等待链路推进')}
              </div>
              <div className="mt-1 text-gray-500">
                {traceLatestNode?.node_id || latestEvent?.phase || '暂无阶段信息'}
              </div>
            </div>

            <div className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-gray-400">输入摘要</div>
              <div className="mt-1 break-words text-sm text-gray-800">{traceInputPreview || '等待 Trace 摘要补齐'}</div>
            </div>

            <div className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-gray-400">输出摘要</div>
              <div className="mt-1 break-words text-sm text-gray-800">{traceOutputPreview || '等待回复内容或 Trace 输出'}</div>
            </div>

            <div className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="text-gray-400">最新事件</div>
              <div className="mt-1 font-medium text-gray-800">
                {latestEvent ? `${formatEventPhase(String(latestEvent.phase || latestEvent.event || '?').toUpperCase())}` : '等待事件'}
              </div>
              <div className="mt-1 text-gray-500">
                {latestEvent ? `${latestEvent.node_id || '无节点'} · ${new Date(latestEvent.ts_ms).toLocaleTimeString('zh-CN')}` : '发起请求后会自动更新'}
              </div>
            </div>

            {traceError ? <NoticeBanner variant="info" title="Trace 摘要">{traceError}</NoticeBanner> : null}
          </div>
        </div>

        <div className="card flex-1 overflow-y-auto p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-sm font-semibold text-gray-800">
              <Sparkles size={14} className="mr-1 inline" />
              实时事件流
            </div>
            <span className="text-xs text-gray-400">{sseEvents.length} 条</span>
          </div>
          {sseEvents.length > 0 ? (
            <div className="space-y-1.5">
              {sseEvents.map((event, index) => (
                <EventRow key={`${event.ts_ms || index}-${index}`} event={event} index={index} />
              ))}
              <div ref={eventsEndRef} />
            </div>
          ) : (
            <EmptyStateCard
              title="等待聊天请求"
              hint="发起请求后，这里会展示数字生命链路、决策与流式事件。"
              className="p-6"
            />
          )}
        </div>
      </div>
    </div>
  )
}
