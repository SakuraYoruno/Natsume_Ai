import React from 'react'
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
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import InlineQueryMeta from '../../../../shared/ui/observability/InlineQueryMeta.jsx'
import { formatTime } from '../../../../shared/formatters.js'
import {
  formatAlertTitle,
  formatDecision,
  formatEventPhase,
  formatSceneType,
  humanizeToken,
} from '../../../../utils/digitalLife.js'
import { getTraceNodeDisplayName } from '../trace/traceNodeText.js'

function ChatDebugPhaseBadge({ phase }) {
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

function ChatDebugEventRow({ event, index }) {
  const ts = formatTime(event.ts_ms, { placeholder: `#${index + 1}` })
  const phase = String(event.phase || event.event || '?').toUpperCase()

  return (
    <div className={`rounded-lg px-2 py-2 text-xs ${index % 2 === 0 ? 'bg-gray-50' : 'bg-white'}`}>
      <div className="flex items-center gap-2">
        <span className="w-20 shrink-0 text-gray-400">{ts}</span>
        <ChatDebugPhaseBadge phase={phase} />
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

function ChatDebugHeader({ stats }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">神经反射调试</h1>
        <p className="text-xs text-gray-500">直接发消息，实时看反射事件流、请求 ID 和神经链路入口。</p>
      </div>
      {stats ? (
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span><Clock size={11} className="mr-1 inline" />{stats.elapsed}ms</span>
          <span><Zap size={11} className="mr-1 inline" />{stats.eventCount} 事件</span>
        </div>
      ) : null}
    </div>
  )
}

function ChatTranscriptPanel({ chatHistory, currentResponse, chatEndRef }) {
  return (
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
  )
}

function ChatInputBar({
  message,
  setMessage,
  streaming,
  onSend,
  onStop,
}) {
  return (
    <div className="mt-3 flex gap-2">
      <input
        className="flex-1 rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
        placeholder="输入消息，按 Enter 发送"
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            onSend()
          }
        }}
        disabled={streaming}
      />
      {streaming ? (
        <button className="btn-secondary px-4" onClick={onStop}>
          <AlertTriangle size={14} />
          停止
        </button>
      ) : (
        <button className="btn-primary px-4" onClick={onSend} disabled={!message.trim()}>
          <Send size={14} />
          发送
        </button>
      )}
    </div>
  )
}

export function ChatDebugMainColumn({
  stats,
  streaming,
  heroMetaItems,
  runtimeCard,
  error,
  chatHistory,
  currentResponse,
  chatEndRef,
  message,
  setMessage,
  onSend,
  onStop,
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <ChatDebugHeader stats={stats} />

      <div className="mt-3">
        <PageHero
          icon={Sparkles}
          eyebrow="神经反射实时调试"
          title={streaming ? '当前正在流式反射调试' : '手动发起一次神经反射调试'}
          description="适合联调 SSE 事件流、回复生成、神经节点推进以及请求到 Trace 的跳转联动。"
          gradientClass="from-pink-50 via-white to-blue-50"
          metaItems={heroMetaItems}
        />
      </div>

      {runtimeCard}

      {error ? (
        <div className="mt-3">
          <NoticeBanner variant="error" title="请求失败">{error}</NoticeBanner>
        </div>
      ) : null}

      <ChatTranscriptPanel
        chatHistory={chatHistory}
        currentResponse={currentResponse}
        chatEndRef={chatEndRef}
      />

      <ChatInputBar
        message={message}
        setMessage={setMessage}
        streaming={streaming}
        onSend={onSend}
        onStop={onStop}
      />
    </div>
  )
}

export function ChatTraceSidebar({
  requestId,
  traceBusy,
  traceLoadState,
  traceError,
  traceUpdatedAt,
  chainEvents,
  traceSnapshot,
  traceNodeCount,
  sseEvents,
  traceIssueCount,
  traceLatestNode,
  latestEvent,
  traceInputPreview,
  traceOutputPreview,
  eventsEndRef,
  onRefreshTrace,
}) {
  return (
    <div className="flex w-96 shrink-0 flex-col gap-3">
      <div className="card p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold text-gray-800">
            <GitBranch size={14} className="mr-1 inline" />
            当前请求链路
          </div>
          <div className="flex items-center gap-2">
            {requestId ? (
              <button className="btn-secondary text-xs" onClick={onRefreshTrace} disabled={traceBusy}>
                {traceBusy ? <Loader2 size={12} className="animate-spin" /> : '刷新 Trace'}
              </button>
            ) : null}
            <span className="text-xs text-gray-400">{chainEvents.length} 节点事件</span>
          </div>
        </div>
        <InlineQueryMeta
          loadState={traceLoadState}
          loadStateDetail={traceError}
          updatedAt={traceUpdatedAt}
          staleAfterMs={15_000}
          windowMinutes={60}
          source="trace"
        />
        <div className="mt-3 space-y-2 text-xs text-gray-600">
          <div className="rounded-lg bg-gray-50 px-3 py-2">
            <div className="text-gray-400">Request ID</div>
            {requestId ? (
              <div className="mt-1 space-y-2">
                <a href={`#/observability/trace/${requestId}`} className="inline-flex items-center gap-1 font-mono text-primary hover:underline">
                  <CheckCircle2 size={12} />
                  {requestId}
                </a>
                <div className="flex flex-wrap items-center gap-2">
                  <a href={`#/observability/trace/${requestId}`} className="btn-primary text-xs">打开 Trace 详情</a>
                  <a href="#/observability/trace" className="btn-secondary text-xs">回到链路总览</a>
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
                {traceNodeCount} / {sseEvents.length}
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
              {latestEvent ? `${latestEvent.node_id || '无节点'} · ${formatTime(latestEvent.ts_ms, { placeholder: '-' })}` : '发起请求后会自动更新'}
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
              <ChatDebugEventRow key={`${event.ts_ms || index}-${index}`} event={event} index={index} />
            ))}
            <div ref={eventsEndRef} />
          </div>
        ) : (
          <EmptyStateCard
            title="等待聊天请求"
            hint="发起请求后，这里会展示数字生命体神经链路、决策与流式事件。"
            className="p-6"
          />
        )}
      </div>
    </div>
  )
}
