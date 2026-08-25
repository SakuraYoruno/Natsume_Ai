import {
  buildRetrievalRuntimeMeta,
  formatDateTime,
  getSummaryInputText,
  getSummaryOutputText,
  ISSUE_STATUSES,
  RUNNING_STATUSES,
} from './components/trace/TraceRequestDetailModel.jsx'
import { dedupeStreamEvents } from './components/trace/traceRequestRuntimePanelModel.js'

export function getSortedTraceNodes(traceData) {
  const items = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  items.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return items
}

function getNodeId(node) {
  return node?.node_id || node?.id || node?.name || ''
}

function getNodeLabel(node) {
  return node?.display_name || node?.label || node?.name || getNodeId(node) || 'unknown node'
}

function buildTraceRequestTriageRailModel({
  requestId,
  traceData,
  nodes,
  summary,
  issueNodeCount,
  runningNodeCount,
  streamEvents,
  subgraphs,
  tribeEvents,
  retrievalMeta,
  liveConnected,
  traceDegraded,
  degradedMessage,
  degradedReason,
}) {
  const nodeCount = traceData?.node_count ?? nodes.length
  const issueNodes = nodes.filter((node) => ISSUE_STATUSES.has(String(node.status || '').toUpperCase()))
  const firstIssueNode = issueNodes[0] ?? null
  const firstIssueLabel = firstIssueNode ? getNodeLabel(firstIssueNode) : ''
  const hasNodes = nodeCount > 0
  const hasTribeEvents = tribeEvents.length > 0
  const hasSubgraphs = subgraphs.length > 0
  const traceStatus = String(traceData?.status || '').trim() || '待加载'
  const evidenceCount = streamEvents.length + subgraphs.length + tribeEvents.length
  const lastEvidenceTs = streamEvents[0]?.ts_ms || tribeEvents[0]?.ts_ms || null
  const runtimeDrift = retrievalMeta?.alignment === '读写漂移'

  let status = 'healthy'
  let headline = '请求链路完整，可从阶段概览进入节点'
  let description = '这条请求已经具备基础链路数据。先按阶段扫覆盖，再进入节点详情核对输入、输出和 runtime 证据。'
  let primaryAction = {
    kind: 'section',
    targetId: 'trace-stage-overview',
    label: '查看阶段链路',
    detail: '按 edge、input、retrieval、agent、model、output 的顺序排查。',
  }

  if (!traceData) {
    status = 'blocked'
    headline = '链路详情还没有可用快照'
    description = '请求级 trace 尚未落盘或正在补拉。先刷新快照，必要时回到 Trace Feed 查实时流。'
    primaryAction = {
      kind: 'route',
      to: '/observability/trace',
      label: '返回 Trace Feed',
      detail: '从实时 feed 重新选择 request。',
    }
  } else if (issueNodeCount > 0) {
    status = 'attention'
    headline = `先处理 ${issueNodeCount} 个异常/降级节点`
    description = `${firstIssueLabel} 是当前最值得先看的节点。先打开节点详情确认输入输出，再对照 Immune replay、stream events 和 Tribe 事件。`
    primaryAction = {
      kind: 'node',
      node: firstIssueNode,
      targetId: 'trace-node-detail',
      label: '定位异常节点',
      detail: `${firstIssueLabel} · ${firstIssueNode?.status || 'unknown'}`,
    }
  } else if (traceDegraded) {
    status = 'degraded'
    headline = '主链路可读，但扩展证据正在降级'
    description = degradedMessage || degradedReason || '主链路快照已返回，部分 replay、子图或 runtime 证据还在补齐。先看护栏回放和检索运行时。'
    primaryAction = {
      kind: 'section',
      targetId: 'trace-immune-replay',
      label: '查看护栏回放',
      detail: '确认是否由 gate、scope、policy 或 replay 缺口造成降级。',
    }
  } else if (runningNodeCount > 0) {
    status = 'running'
    headline = `还有 ${runningNodeCount} 个节点在运行`
    description = '请求尚未完全收束。先看实时事件和 live entries，等状态稳定后再检查节点时间线。'
    primaryAction = {
      kind: 'section',
      targetId: 'trace-stream-events',
      label: '查看实时事件',
      detail: liveConnected ? 'SSE 已连接，优先看最新 delta。' : 'SSE 未连接，当前通过轮询补齐。',
    }
  } else if (!hasNodes) {
    status = 'empty'
    headline = '链路已返回，但缺少节点明细'
    description = '请求摘要可用，节点级证据还没有落盘。先看输入输出摘要和检索 runtime，避免误判为空白页。'
    primaryAction = {
      kind: 'section',
      targetId: 'trace-summary',
      label: '查看请求摘要',
      detail: '用 summary 和 retrieval runtime 先判断请求是否有效。',
    }
  }

  const secondaryAction = requestId
    ? {
        kind: 'route',
        to: `/execution/automation?immune_replay_request_id=${encodeURIComponent(requestId)}`,
        label: '打开 Immune Ops',
        detail: '带上 request_id 查看 replay 和 guard 决策。',
      }
    : {
        kind: 'route',
        to: '/observability/trace',
        label: '返回 Trace Feed',
        detail: '从实时列表重新选择 request。',
      }

  const handoffSteps = [
    {
      key: 'snapshot',
      label: 'Snapshot',
      value: traceStatus,
      detail: traceData ? `${nodeCount} 个节点已落盘。` : '等待 trace snapshot 返回。',
      state: traceData ? 'ready' : 'blocked',
      action: {
        kind: 'section',
        targetId: traceData ? 'trace-stage-overview' : 'trace-summary',
        label: traceData ? '查看阶段' : '查看摘要',
      },
    },
    {
      key: 'issue',
      label: 'Issue Node',
      value: firstIssueLabel || 'none',
      detail: issueNodeCount > 0 ? `${issueNodeCount} 个异常/降级节点需要复核。` : '当前没有异常节点。',
      state: issueNodeCount > 0 ? 'warning' : 'ready',
      action: issueNodeCount > 0
        ? {
            kind: 'node',
            node: firstIssueNode,
            targetId: 'trace-node-detail',
            label: '定位节点',
          }
        : {
            kind: 'section',
            targetId: 'trace-stage-overview',
            label: '查看阶段',
          },
    },
    {
      key: 'evidence',
      label: 'Evidence Feed',
      value: `${evidenceCount} refs`,
      detail: `stream ${streamEvents.length} · subgraph ${subgraphs.length} · Tribe ${tribeEvents.length}`,
      state: evidenceCount > 0 ? 'ready' : 'warning',
      action: {
        kind: 'section',
        targetId: 'trace-stream-events',
        label: '查看证据',
      },
    },
    {
      key: 'runtime',
      label: 'Runtime Context',
      value: retrievalMeta?.alignment || 'unknown',
      detail: retrievalMeta?.embeddingModel || '暂无 retrieval runtime 样本。',
      state: runtimeDrift ? 'warning' : 'ready',
      action: {
        kind: 'section',
        targetId: 'trace-summary',
        label: '查看运行时',
      },
    },
    {
      key: 'repair',
      label: 'Repair Handoff',
      value: requestId ? 'Immune Ops' : 'Trace Feed',
      detail: requestId ? '带 request_id 跳到自动化/免疫回放入口。' : '缺 request_id 时回到 Trace Feed。',
      state: requestId ? 'ready' : 'blocked',
      action: requestId
        ? {
            kind: 'route',
            to: `/execution/automation?immune_replay_request_id=${encodeURIComponent(requestId)}`,
            label: '打开 Immune Ops',
          }
        : {
            kind: 'route',
            to: '/observability/trace',
            label: '返回 Trace Feed',
          },
    },
  ]

  return {
    status,
    eyebrow: 'Trace Triage',
    title: '请求排障控制台',
    headline,
    description,
    primaryAction,
    secondaryAction,
    handoffSteps,
    cards: [
      {
        key: 'status',
        label: '请求状态',
        value: traceStatus,
        detail: summary?.decision ? `决策：${summary.decision}` : '来自 trace snapshot 的当前状态。',
        tone: issueNodeCount > 0 ? 'degraded' : 'ready',
      },
      {
        key: 'nodes',
        label: '节点覆盖',
        value: String(nodeCount),
        detail: hasNodes ? `运行中 ${runningNodeCount} · 异常 ${issueNodeCount}` : '等待节点级明细落盘。',
        tone: hasNodes ? 'ready' : 'blocked',
      },
      {
        key: 'evidence',
        label: '证据面',
        value: String(evidenceCount),
        detail: lastEvidenceTs ? `最近 ${formatDateTime(lastEvidenceTs)}` : '等待 stream、subgraph 或 Tribe 证据。',
        tone: evidenceCount > 0 ? 'ready' : 'degraded',
      },
      {
        key: 'runtime',
        label: '检索运行时',
        value: retrievalMeta?.alignment || '未知',
        detail: retrievalMeta?.embeddingModel || '暂无 embedding runtime 样本。',
        tone: retrievalMeta?.alignment === '读写漂移' ? 'degraded' : 'ready',
      },
    ],
    gates: [
      {
        label: '快照',
        state: traceData ? 'ready' : 'blocked',
        detail: traceData ? 'Trace snapshot 已可读。' : 'Snapshot 缺失，先回 feed 或刷新。',
      },
      {
        label: '节点',
        state: issueNodeCount > 0 ? 'warning' : (hasNodes ? 'ready' : 'blocked'),
        detail: issueNodeCount > 0 ? `${issueNodeCount} 个节点需要人工看详情。` : (hasNodes ? '节点链路可继续下钻。' : '没有节点明细。'),
      },
      {
        label: '实时',
        state: liveConnected ? 'ready' : 'warning',
        detail: liveConnected ? 'SSE live feed 已连接。' : 'SSE 未连上，使用轮询补齐。',
      },
      {
        label: '联动',
        state: hasSubgraphs || hasTribeEvents ? 'ready' : 'warning',
        detail: hasSubgraphs || hasTribeEvents ? `子图 ${subgraphs.length} · Tribe ${tribeEvents.length}` : '暂无子图或 Tribe 事件。',
      },
    ],
  }
}

export function buildTraceRequestDetailViewModel({
  requestId,
  traceData,
  fallbackEvents = [],
  tribeEvents = [],
  retrievalRuntime = null,
  liveConnected = false,
} = {}) {
  const nodes = getSortedTraceNodes(traceData)
  const streamEvents = dedupeStreamEvents(traceData?.stream_events, fallbackEvents)
  const summary = traceData?.summary ?? {}
  const degradedReason = String(summary?.degraded_reason || '').trim()
  const degradedMessage = String(summary?.degraded_message || '').trim()
  const traceDegraded = Boolean(summary?.degraded)
  const subgraphs = Array.isArray(traceData?.subgraphs) ? traceData.subgraphs : []
  const inputPreview = getSummaryInputText(summary)
  const outputPreview = getSummaryOutputText(summary)
  const latestStreamEvent = streamEvents[0] ?? null
  const issueNodeCount = nodes.filter((node) => ISSUE_STATUSES.has(String(node.status || '').toUpperCase())).length
  const runningNodeCount = nodes.filter((node) => RUNNING_STATUSES.has(String(node.status || '').toUpperCase())).length
  const retrievalMeta = buildRetrievalRuntimeMeta(retrievalRuntime)
  const liveModeText = liveConnected ? 'SSE 实时流已连接' : '实时流重连中 / 轮询补齐'
  const triageRail = buildTraceRequestTriageRailModel({
    requestId,
    traceData,
    nodes,
    summary,
    issueNodeCount,
    runningNodeCount,
    streamEvents,
    subgraphs,
    tribeEvents,
    retrievalMeta,
    liveConnected,
    traceDegraded,
    degradedMessage,
    degradedReason,
  })
  const tracePulseNodes = [
    {
      label: '请求状态',
      value: traceData?.status || '待加载',
      tone: 'primary',
      active: Boolean(traceData?.status),
    },
    {
      label: '节点覆盖',
      value: `${traceData?.node_count ?? nodes.length}`,
      tone: 'teal',
      active: (traceData?.node_count ?? nodes.length) > 0,
    },
    {
      label: '异常节点',
      value: String(issueNodeCount),
      tone: 'rose',
      active: issueNodeCount > 0,
    },
    {
      label: '流式事件',
      value: String(streamEvents.length),
      tone: 'violet',
      active: streamEvents.length > 0,
    },
    {
      label: 'Tribe 联动',
      value: String(tribeEvents.length),
      tone: 'amber',
      active: tribeEvents.length > 0,
    },
    {
      label: '实时模式',
      value: liveModeText,
      tone: 'primary',
      active: liveConnected,
    },
  ]

  return {
    nodes,
    streamEvents,
    summary,
    inputPreview,
    outputPreview,
    latestStreamEvent,
    issueNodeCount,
    runningNodeCount,
    subgraphs,
    retrievalMeta,
    liveModeText,
    triageRail,
    tracePulseNodes,
    traceDegraded,
    degradedMessage,
    degradedReason,
  }
}
