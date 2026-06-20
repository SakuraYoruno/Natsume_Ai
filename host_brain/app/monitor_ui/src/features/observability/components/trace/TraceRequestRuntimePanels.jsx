import React, { useMemo, useState } from 'react'
import { Background, Controls, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Activity, Clock3, GitBranch, Layers3, ShieldAlert } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import RenderValue from '../../../../shared/ui/RenderValue.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from './TraceSurfaces.jsx'
import { TagList, TextPreviewCard } from './TraceRequestSummaryPanels.jsx'
import { getTraceNodeDisplayName } from './traceNodeText.js'
import {
  buildDeltaEntries,
  buildSubgraphFlow,
  dedupeStreamEvents,
  mergeDeltaEntries,
} from './traceRequestRuntimePanelModel.js'
import { buildUnifiedTimelineView } from './traceUnifiedTimelineModel.js'
import {
  ISSUE_STATUSES,
  TRACE_NODE_TYPES,
  buildImmuneStageMeta,
  buildStageSnapshots,
  buildStageTimelineSections,
  buildUnifiedTimelineItems,
  formatDateTime,
  getEventPreview,
  getNodePreview,
  getTribePayloadPreview,
  normalizeStatus,
} from './TraceRequestDetailModel.jsx'

export { buildDeltaEntries, buildSubgraphFlow, dedupeStreamEvents, mergeDeltaEntries }

export function StageOverviewPanel({ nodes, selectedNode, onSelectNode }) {
  const stageSnapshots = useMemo(() => buildStageSnapshots(nodes), [nodes])

  return (
    <SectionCard
      icon={Layers3}
      title="阶段链路总览"
      description="把当前 request 拆成边缘 → 入口 → 检索 → 人格 → 编排 → 代理 → 模型 → 输出八层，直接看每层有没有跑到、跑得是否合理。"
      aside={<div className="text-xs text-gray-400">{stageSnapshots.filter((item) => item.observedNodes.length > 0).length}/{stageSnapshots.length} 层已命中</div>}
    >
      <div className="space-y-3">
        {stageSnapshots.map((stage) => {
          const Icon = stage.icon
          const activeNodeId = selectedNode?.node_id
          const isSelected = stage.observedNodes.some((node) => node.node_id === activeNodeId)
          const latestNode = stage.latestNode
          const immuneMeta = stage.id === 'immune' ? buildImmuneStageMeta(stage.observedNodes) : null
          return (
            <div
              key={stage.id}
              className={`rounded-2xl border p-4 transition-colors ${isSelected ? 'border-blue-300 bg-blue-50/55 shadow-[0_10px_24px_rgba(59,130,246,0.12)]' : 'border-primary-100/80 bg-white/82 hover:border-primary-200/80'}`}
            >
              <div className="flex items-start gap-3">
                <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${stage.observedNodes.length > 0 ? 'border border-primary-100/70 bg-white/95 text-blue-600 shadow-sm' : 'border border-primary-100/70 bg-primary-50/70 text-primary-300'}`}>
                  <Icon size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-sm font-semibold text-gray-900">{stage.label}</div>
                    <StatusBadge status={normalizeStatus(stage.status)} label={stage.observedNodes.length > 0 ? stage.status : '未命中'} />
                    <span className="text-xs text-gray-400">{stage.observedNodes.length} 个节点</span>
                    {latestNode ? <span className="text-xs text-gray-400">最近 {formatDateTime(latestNode.ts_ms)}</span> : null}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-gray-500">{stage.description}</div>
                  {stage.preview ? <div className="mt-2 break-all text-sm leading-6 text-gray-700">{stage.preview}</div> : null}
                  {immuneMeta && (immuneMeta.decision || immuneMeta.riskLevel || immuneMeta.reasonTags.length > 0) ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {(immuneMeta.decision || immuneMeta.riskLevel) ? (
                        <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700">
                          {(immuneMeta.decision || 'IMMUNE').toUpperCase()}
                          {immuneMeta.riskLevel ? `/${String(immuneMeta.riskLevel).toLowerCase()}` : ''}
                        </span>
                      ) : null}
                      {immuneMeta.reasonTags.slice(0, 2).map((reasonCode) => (
                        <span key={`${stage.id}-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                          {reasonCode}
                        </span>
                      ))}
                    </div>
                  ) : null}

                  <div className="mt-3 flex flex-wrap gap-2">
                    {stage.expectedEntries.map((entry) => (
                      <button
                        key={entry.nodeId}
                        className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors ${entry.hit ? 'border-blue-200 bg-white text-gray-700 hover:border-blue-300' : 'border-dashed border-primary-200/70 bg-primary-50/45 text-primary-400'}`}
                        onClick={() => entry.node && onSelectNode?.(entry.node)}
                        disabled={!entry.node}
                      >
                        <span>{entry.label}</span>
                        <StatusBadge status={normalizeStatus(entry.status)} />
                      </button>
                    ))}
                    {stage.dynamicNodes.map((node) => (
                      <button
                        key={node.node_id}
                        className="inline-flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs text-violet-700 transition-colors hover:border-violet-300"
                        onClick={() => onSelectNode?.(node)}
                      >
                        <span>{getTraceNodeDisplayName(node)}</span>
                        <StatusBadge status={normalizeStatus(node.status)} />
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}

export function StreamEventsPanel({ events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={Activity}
      title="流式事件"
      description="保留 request 级别的实时事件明细，便于核对输出流、阶段切换和节点补报。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="事件总数" value={events.length} />
        <MiniStat label="最新阶段" value={latestEvent?.phase || '-'} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat label="最新节点" value={latestEvent?.node_id || '-'} hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'} />
      </div>

      {loading ? <LoadingNotice>正在拉取流式事件…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="流式事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? <EmptyStateCard title="暂无流式事件" hint="该请求还没有可展示的事件记录。" className="p-4" /> : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event, index) => (
            <div key={`${event.event_type}-${event.ts_ms}-${index}`} className="rounded-xl border border-primary-100/70 bg-white/78 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-sky-700"><RenderValue value={event.phase || '-'} /></span>
                <span><RenderValue value={event.event_type || '-'} /></span>
                <span className="font-mono"><RenderValue value={event.node_id || '-'} /></span>
                <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
              </div>
              {getEventPreview(event) ? <div className="mt-2 break-all text-sm text-gray-700">{getEventPreview(event)}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </SectionCard>
  )
}

export function ImmuneReplayPanel({ replay, loading, error, requestId, onReload }) {
  const summary = replay?.summary || {}
  const decisions = Array.isArray(replay?.decisions) ? replay.decisions : []
  const reasonCodes = Array.isArray(summary?.reason_codes) ? summary.reason_codes : []
  const matchedRules = Array.isArray(summary?.matched_rules) ? summary.matched_rules : []

  return (
    <SectionCard
      icon={ShieldAlert}
      title="Immune 回放"
      description="按当前 request 回放免疫决策链，快速定位误拦截与规则命中。"
      aside={(
        <button className="btn-secondary text-xs" onClick={onReload} disabled={loading}>
          {loading ? '同步中...' : '刷新回放'}
        </button>
      )}
    >
      {loading ? <LoadingNotice>正在拉取 immune replay…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="回放拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && !replay ? (
        <EmptyStateCard title="当前请求暂无 immune 回放" hint="若未命中免疫流程，这里会保持为空。" className="p-4" />
      ) : null}
      {replay ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MiniStat label="request_id" value={requestId || '-'} />
            <MiniStat label="会话" value={replay?.session_id || '-'} />
            <MiniStat label="首/末决策" value={`${summary?.first_decision || '-'} → ${summary?.last_decision || '-'}`} />
            <MiniStat label="事件数 / max_score" value={`${summary?.event_count ?? 0} / ${summary?.max_score ?? '-'}`} />
          </div>

          {replay?.input_preview ? (
            <TextPreviewCard
              label="输入预览"
              text={String(replay.input_preview || '')}
              emptyLabel="暂无输入预览"
            />
          ) : null}

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <TagList label="Reason Codes" items={reasonCodes} emptyLabel="暂无 reason code" tone="warn" />
            <TagList label="Matched Rules" items={matchedRules} emptyLabel="暂无规则命中" />
          </div>

          <div className="rounded-xl border border-primary-100/70 bg-white/74 p-4">
            <div className="mb-2 text-xs font-medium text-gray-500">决策时间线</div>
            {decisions.length === 0 ? (
              <div className="text-sm text-gray-400">暂无决策事件</div>
            ) : (
              <div className="space-y-2">
                {decisions.map((item, index) => (
                  <div key={`${item.ts_ms}-${item.event_type}-${index}`} className="rounded-lg border border-primary-100/70 bg-white/85 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                        <RenderValue value={item.event_type || '-'} />
                      </span>
                      <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs text-orange-700">
                        <RenderValue value={item.decision || '-'} />{item.risk_level ? <RenderValue value={`/${String(item.risk_level)}`} /> : ''}
                      </span>
                      <span className="text-xs text-gray-500">score={item.score ?? '-'}</span>
                      <span className="ml-auto text-xs text-gray-400">{formatDateTime(item.ts_ms)}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      <RenderValue value={item.stage || '-'} />{item.phase ? <RenderValue value={` · ${String(item.phase)}`} /> : ''}{item.direction ? <RenderValue value={` · ${String(item.direction)}`} /> : ''}
                    </div>
                    {Array.isArray(item.reason_codes) && item.reason_codes.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.reason_codes.slice(0, 4).map((code, reasonIndex) => (
                          <span key={`${item.ts_ms}-reason-${reasonIndex}`} className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                            <RenderValue value={code} />
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {Array.isArray(item.matched_rules) && item.matched_rules.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.matched_rules.slice(0, 3).map((rule, ruleIndex) => (
                          <span key={`${item.ts_ms}-rule-${ruleIndex}`} className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700">
                            <RenderValue value={rule} />
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </SectionCard>
  )
}

export function TribeEventsPanel({ requestId, events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={GitBranch}
      title="Tribe 事件联动"
      description="展示当前 request 关联的跨智能体事件，帮助对齐 Edge/Host 之间的状态衔接。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="关联 request_id" value={requestId || '-'} />
        <MiniStat label="事件数" value={events.length} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat
          label="最新 source -> target"
          value={latestEvent ? `${latestEvent.source_agent || '-'} -> ${latestEvent.target_agent || '-'}` : '-'}
          hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'}
        />
      </div>

      {loading ? <LoadingNotice>正在拉取 Tribe 事件…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="Tribe 事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? (
        <EmptyStateCard title="暂无 Tribe 事件" hint="当前请求暂未观测到跨智能体事件。" className="p-4" />
      ) : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event) => {
            const payloadPreview = getTribePayloadPreview(event?.payload)
            return (
              <div key={event.event_id} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700">
                    <RenderValue value={event.event_type || '-'} />
                  </span>
                  <span className="font-mono"><RenderValue value={event.source_agent || '-'} /></span>
                  <span>{'->'}</span>
                  <span className="font-mono"><RenderValue value={event.target_agent || '-'} /></span>
                  <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
                </div>
                {payloadPreview ? <div className="mt-2 break-all text-sm text-gray-700">{payloadPreview}</div> : null}
              </div>
            )
          })}
        </div>
      ) : null}
    </SectionCard>
  )
}

export function SubgraphMiniFlow({ subgraph, selectedNode, onSelectNode }) {
  const flow = useMemo(
    () => buildSubgraphFlow(subgraph, onSelectNode, selectedNode),
    [subgraph, onSelectNode, selectedNode]
  )

  if (!flow.nodes.length) {
    return <EmptyStateCard title="子图无节点" hint="该子图没有可渲染的内部节点。" className="p-4" />
  }

  return (
    <div className="h-64 overflow-hidden rounded-xl border border-primary-100/70 bg-white/86">
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={TRACE_NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnDrag
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: 'smoothstep' }}
      >
        <Background color="#F1F5F9" gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

export function SubgraphsPanel({ subgraphs, selectedNode, onSelectNode }) {
  return (
    <SectionCard
      icon={Layers3}
      title="子代理内部子图"
      description="展示 sub agent 内部图的提交结果，可直接点选内部节点联动右侧详情。"
      aside={<div className="text-xs text-gray-400">共 {subgraphs.length} 张</div>}
    >
      {subgraphs.length === 0 ? <EmptyStateCard title="暂无子图" hint="当前请求尚未写入可展示的子代理内部图。" className="p-4" /> : (
        <div className="space-y-4">
          {subgraphs.map((subgraph) => {
            const subgraphNodes = Array.isArray(subgraph.nodes) ? subgraph.nodes : []
            const ownerLabel = subgraph.owner_node_id ? getTraceNodeDisplayName(subgraph.owner_node_id) : null
            return (
              <div key={subgraph.subgraph_id} className="rounded-xl border border-primary-100/70 bg-white/76 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-sm font-semibold text-gray-900">{subgraph.subgraph_id}</div>
                  {subgraph.owner_node_id ? (
                    <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs text-violet-700">
                      {ownerLabel}{ownerLabel !== subgraph.owner_node_id ? ` · ${subgraph.owner_node_id}` : ''}
                    </span>
                  ) : null}
                  <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-xs text-primary-700">{subgraph.node_count ?? subgraphNodes.length} 节点</span>
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">成功 {subgraph.success_count ?? 0}</span>
                  <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-600">失败 {subgraph.fail_count ?? 0}</span>
                  <span className="ml-auto text-xs text-gray-400">{formatDateTime(subgraph.committed_at_ms)}</span>
                </div>
                <div className="mt-3">
                  <SubgraphMiniFlow subgraph={subgraph} selectedNode={selectedNode} onSelectNode={onSelectNode} />
                </div>
                <div className="mt-3 space-y-2">
                  {subgraphNodes.map((node, index) => (
                    <button
                      key={`${subgraph.subgraph_id}:${node.node_id}:${index}`}
                      className="w-full rounded-lg border border-primary-100/70 bg-white/86 px-3 py-2 text-left transition-colors hover:bg-blue-50/35"
                      onClick={() => onSelectNode?.(node)}
                    >
                      <div className="flex items-center gap-2">
                        <div className="text-sm font-medium text-gray-800">{getTraceNodeDisplayName(node)}</div>
                        <StatusBadge status={normalizeStatus(node.status)} />
                        <div className="ml-auto text-xs text-gray-400">{formatDateTime(node.ts_ms)}</div>
                      </div>
                      <div className="mt-1 break-all text-xs text-gray-500">{node.node_id}</div>
                      {getNodePreview(node) ? <div className="mt-1 break-all text-xs text-gray-600">{getNodePreview(node)}</div> : null}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}

export function LiveEntriesPanel({ liveEntries, liveConnected, liveLastTs }) {
  return (
    <SectionCard
      icon={Activity}
      title="实时节点变化"
      description="优先展示 SSE 增量补报，帮助你快速确认当前 request 正在推进到哪一层。"
      aside={(
        <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${liveConnected ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
          {liveConnected ? 'SSE 已连接' : '重连中 / 轮询补齐'}
        </span>
      )}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="增量条目" value={liveEntries.length} />
        <MiniStat label="连接状态" value={liveConnected ? '实时流在线' : '实时流重连中'} hint={liveLastTs ? formatDateTime(liveLastTs) : '等待心跳'} />
        <MiniStat label="最新变更" value={liveEntries[0]?.displayName || '-'} hint={liveEntries[0]?.label || '暂无变化'} />
      </div>

      {liveEntries.length > 0 ? (
        <div className="space-y-2">
          {liveEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{entry.displayName}</div>
                <StatusBadge status={normalizeStatus(entry.status)} />
                <span className="text-xs text-gray-400">{entry.label}</span>
                {entry.previousStatus ? <span className="text-xs text-gray-400">{entry.previousStatus} → {entry.status}</span> : null}
                <div className="ml-auto text-xs text-gray-400">{formatDateTime(entry.tsMs)}</div>
              </div>
              <div className="mt-1 break-all font-mono text-xs text-gray-500">{entry.nodeId}</div>
              {entry.preview ? <div className="mt-2 break-all text-sm text-gray-700">{entry.preview}</div> : null}
            </div>
          ))}
        </div>
      ) : <EmptyStateCard title="等待节点变化" hint="实时链路就绪后会自动追加节点事件。" className="p-4" />}
    </SectionCard>
  )
}

export function UnifiedTimelinePanel({ nodes, tribeEvents, selectedNode, onSelectNode }) {
  const [typeFilter, setTypeFilter] = useState('all')
  const [stageFilter, setStageFilter] = useState('all')
  const [keyword, setKeyword] = useState('')

  const items = useMemo(
    () => buildUnifiedTimelineItems(nodes, tribeEvents),
    [nodes, tribeEvents]
  )
  const { stageOptions, filteredItems } = useMemo(
    () => buildUnifiedTimelineView({ items, typeFilter, stageFilter, keyword }),
    [items, keyword, stageFilter, typeFilter]
  )

  return (
    <SectionCard
      icon={Clock3}
      title="同轴时间线（节点 + Tribe）"
      description="把节点执行与 Tribe 事件放到同一时间轴，快速定位跨层状态衔接是否顺滑。"
      aside={<div className="text-xs text-gray-400">{filteredItems.length} / {items.length} 条</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="节点条目" value={nodes.length} />
        <MiniStat label="Tribe 条目" value={tribeEvents.length} />
        <MiniStat label="最新时间" value={items[0]?.ts_ms ? formatDateTime(items[0].ts_ms) : '-'} />
      </div>

      <div className="mb-4 rounded-xl border border-primary-100/70 bg-white/74 p-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[8rem_12rem_minmax(0,1fr)_auto]">
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            <option value="node">仅节点</option>
            <option value="tribe">仅Tribe</option>
          </select>
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={stageFilter}
            onChange={(event) => setStageFilter(event.target.value)}
          >
            <option value="all">全部阶段</option>
            {stageOptions.map((option) => (
              <option key={option.stageId} value={option.stageId}>{option.label}</option>
            ))}
          </select>
          <input
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            placeholder="搜索标题、节点ID、payload 关键词"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <button
            className="btn-secondary text-xs"
            onClick={() => {
              setTypeFilter('all')
              setStageFilter('all')
              setKeyword('')
            }}
          >
            重置
          </button>
        </div>
      </div>

      {filteredItems.length === 0 ? (
        <EmptyStateCard title="暂无可对齐数据" hint="等待节点执行或 Tribe 事件上报后会自动出现。" className="p-4" />
      ) : (
        <div className="space-y-2">
          {filteredItems.map((item) => {
            const stageLabel = item.stageLabel
            const isNode = item.type === 'node'
            const isSelectedNode = isNode && selectedNode?.node_id === item?.node?.node_id
            const containerClassName = isNode
              ? (isSelectedNode ? 'border-blue-200 bg-blue-50' : 'border-primary-100/70 bg-white/88 hover:bg-blue-50/30')
              : 'border-emerald-200 bg-emerald-50/60'
            return (
              <button
                key={item.key}
                className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${containerClassName}`}
                onClick={() => { if (isNode) onSelectNode?.(item.node) }}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className={`rounded-full border px-2 py-0.5 ${isNode ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
                    {isNode ? 'NODE' : 'TRIBE'}
                  </span>
                  <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-primary-700">{stageLabel}</span>
                  <StatusBadge status={normalizeStatus(item.status)} />
                  <span className="ml-auto">{formatDateTime(item.ts_ms)}</span>
                </div>
                <div className="mt-2 text-sm font-semibold text-gray-900 break-all">{item.title}</div>
                <div className="mt-1 text-xs font-mono text-gray-500 break-all">{item.subtitle}</div>
                {item.preview ? <div className="mt-2 text-sm text-gray-700 break-all">{item.preview}</div> : null}
              </button>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}

export function NodeTimelinePanel({ nodes, selectedNode, onSelectNode }) {
  const sections = useMemo(() => buildStageTimelineSections(nodes), [nodes])

  return (
    <SectionCard
      icon={Clock3}
      title="节点时间线"
      description="按阶段分组后再按时间顺序展开，先看当前 request 跑到哪一层，再钻到单个节点。"
      aside={<div className="text-xs text-gray-400">{nodes.length} 个节点</div>}
    >
      {nodes.length === 0 ? (
        <EmptyStateCard title="暂无节点时间线" hint="请求节点上报后会在这里展示。" className="p-4" />
      ) : (
        <div className="space-y-4">
          {sections.map((section) => {
            const Icon = section.icon
            return (
              <div key={section.stageId} className="overflow-hidden rounded-xl border border-primary-100/70 bg-white/72">
                <div className="flex flex-wrap items-start gap-3 border-b border-primary-100/70 bg-white/88 px-4 py-3">
                  <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white text-blue-600">
                    <Icon size={18} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-semibold text-gray-900">{section.label}</div>
                      <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-xs text-primary-700">{section.nodes.length} 个节点</span>
                      {section.issueCount > 0 ? (
                        <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-600">
                          异常/降级 {section.issueCount}
                        </span>
                      ) : null}
                      {section.latestNode?.ts_ms ? <span className="text-xs text-gray-400">最近 {formatDateTime(section.latestNode.ts_ms)}</span> : null}
                    </div>
                    <div className="mt-1 text-xs leading-5 text-gray-500">{section.description}</div>
                  </div>
                </div>

                <div className="divide-y divide-primary-100/70">
                  {section.nodes.map((node, index) => {
                    const isSelected = selectedNode?.node_id === node.node_id
                    const isIssue = ISSUE_STATUSES.has(String(node.status || '').toUpperCase())
                    const preview = getNodePreview(node)
                    return (
                      <button
                        key={`${section.stageId}:${node.node_id}:${index}`}
                        className={`w-full px-4 py-4 text-left transition-colors ${isSelected ? 'bg-blue-50/85' : isIssue ? 'bg-red-50/60 hover:bg-red-50' : 'bg-white/92 hover:bg-primary-50/45'}`}
                        onClick={() => onSelectNode(node)}
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-gray-400">{String(index + 1).padStart(2, '0')}</span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold text-gray-900">{getTraceNodeDisplayName(node)}</div>
                            <div className="text-xs text-gray-500">{node.node_id}{node.group_id ? ` / ${node.group_id}` : ''}</div>
                          </div>
                          <div className="flex items-center gap-2">
                            {node.latency_ms != null ? <span className="text-xs text-gray-500">{node.latency_ms}ms</span> : null}
                            <span className="text-xs text-gray-400">{formatDateTime(node.ts_ms)}</span>
                            <StatusBadge status={normalizeStatus(node.status)} />
                          </div>
                        </div>
                        {preview ? <div className="mt-2 break-all pl-6 text-sm leading-6 text-gray-600">{preview}</div> : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}
