import React from 'react'
import { CheckCircle2, GitBranch, Loader2 } from 'lucide-react'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import InlineQueryMeta from '../../../../shared/ui/observability/InlineQueryMeta.jsx'
import { formatTime } from '../../../../shared/formatters.js'
import { formatEventPhase } from '../../../../utils/digitalLife.js'
import { getTraceNodeDisplayName } from '../trace/traceNodeText.js'
import ChatDebugEventStream from './ChatDebugEventStream.jsx'

export default function ChatTraceSidebar({
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
    <aside className="flex min-w-0 flex-col gap-3 xl:sticky xl:top-4 xl:max-h-[calc(100vh-8rem)] xl:overflow-y-auto xl:pr-1">
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

      <ChatDebugEventStream sseEvents={sseEvents} eventsEndRef={eventsEndRef} />
    </aside>
  )
}
