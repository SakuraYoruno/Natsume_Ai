import { resolveLoadState } from '../../models/loadState.js'

const TRACE_ISSUE_STATUSES = new Set(['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'])

export function truncateDebugText(value, maxLength = 160) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

export function getChatDebugChainEvents(events = []) {
  const items = Array.isArray(events) ? events : []
  return items.filter((event) => event.node_id || String(event.phase || '').toUpperCase() === 'NODE')
}

export function getLatestTraceNode(traceSnapshot) {
  const nodes = Array.isArray(traceSnapshot?.nodes) ? [...traceSnapshot.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return nodes[nodes.length - 1]
}

export function getTraceIssueCount(traceSnapshot) {
  return Array.isArray(traceSnapshot?.nodes)
    ? traceSnapshot.nodes.filter((node) => TRACE_ISSUE_STATUSES.has(String(node?.status || '').toUpperCase())).length
    : 0
}

export function getLatestChatContent(chatHistory, role) {
  return (Array.isArray(chatHistory) ? chatHistory : [])
    .filter((item) => item.role === role)
    .slice(-1)[0]?.content
}

export function buildChatDebugTracePreviews({
  traceSummary,
  chatHistory,
  currentResponse,
}) {
  return {
    traceInputPreview: truncateDebugText(
      traceSummary?.input_text || getLatestChatContent(chatHistory, 'user'),
      180
    ),
    traceOutputPreview: truncateDebugText(
      traceSummary?.output_text
      || traceSummary?.failure_event
      || currentResponse
      || getLatestChatContent(chatHistory, 'assistant'),
      180
    ),
  }
}

export function buildChatDebugHeroMetaItems({
  chatHistory,
  sseEvents,
  traceSnapshot,
  chainEventCount,
  requestId,
}) {
  return [
    `历史消息：${Array.isArray(chatHistory) ? chatHistory.length : 0}`,
    `SSE 事件：${Array.isArray(sseEvents) ? sseEvents.length : 0}`,
    traceSnapshot?.status ? `Trace：${traceSnapshot.status}` : `链路节点：${chainEventCount}`,
    requestId ? '已有 Request ID' : '等待新请求',
  ]
}

export function buildChatDebugCommandRailModel({
  message = '',
  streaming = false,
  error = null,
  stats = null,
  requestId = null,
  sseEvents = [],
  chainEvents = [],
  traceSnapshot = null,
  traceBusy = false,
  traceIssueCount = 0,
  traceLatestNode = null,
  latestEvent = null,
} = {}) {
  const hasMessage = Boolean(String(message || '').trim())
  const hasRequest = Boolean(requestId)
  const eventCount = Array.isArray(sseEvents) ? sseEvents.length : 0
  const chainCount = Array.isArray(chainEvents) ? chainEvents.length : 0
  const nodeCount = traceSnapshot?.node_count ?? traceSnapshot?.nodes?.length ?? chainCount
  const traceStatus = traceSnapshot?.status || (hasRequest ? (traceBusy ? '拉取中' : '待补齐') : '等待请求')
  const latestStage = traceLatestNode?.node_id || latestEvent?.node_id || latestEvent?.phase || '暂无事件'
  const traceReady = Boolean(traceSnapshot)
  const handoffSteps = [
    {
      key: 'prompt',
      label: 'Prompt',
      value: hasMessage ? 'ready' : (hasRequest ? 'sent' : 'empty'),
      detail: hasMessage
        ? truncateDebugText(message, 84)
        : (hasRequest ? '输入已提交到调试链路。' : '先填入样例或手动输入。'),
      state: hasMessage || hasRequest || streaming ? 'ready' : 'warning',
    },
    {
      key: 'stream',
      label: 'SSE Stream',
      value: streaming ? 'receiving' : `${eventCount} events`,
      detail: eventCount > 0 ? `${chainCount} 个节点事件已记录。` : '等待后端事件流。',
      state: streaming || eventCount > 0 ? 'ready' : 'warning',
    },
    {
      key: 'request',
      label: 'Request ID',
      value: requestId || 'pending',
      detail: hasRequest ? '已写入全局 selectedRequestId，可下钻 Trace。' : '等待 SSE 返回 request_id。',
      state: hasRequest ? 'ready' : 'blocked',
    },
    {
      key: 'trace',
      label: 'Trace Snapshot',
      value: traceReady ? (traceSnapshot.status || 'ready') : (traceBusy ? 'loading' : 'pending'),
      detail: traceReady
        ? `${nodeCount} 个节点 · ${traceIssueCount} 个待关注。`
        : (hasRequest ? 'request_id 已有，等待 Trace 快照补齐。' : '没有 request_id 时不会拉取。'),
      state: traceIssueCount > 0 ? 'warning' : (traceReady ? 'ready' : (hasRequest ? 'warning' : 'blocked')),
    },
    {
      key: 'detail',
      label: 'Detail Handoff',
      value: hasRequest ? 'openable' : 'locked',
      detail: hasRequest ? 'Trace 详情入口已可用。' : '拿到 request_id 后解锁详情页。',
      state: hasRequest ? 'ready' : 'blocked',
      requestId,
    },
  ]

  let status = 'idle'
  let headline = '先输入一条消息，启动一次可追踪调试'
  let description = '发送后左侧看回复，右侧看 SSE 事件、request_id 和 Trace 入口。建议先用一条短消息确认链路是否可追踪。'
  let primaryAction = hasMessage
    ? { kind: 'send', label: '发送调试消息', detail: '发送后自动清空输入并监听 SSE 事件。' }
    : { kind: 'sample', label: '填入调试样例', detail: '填入一条短消息，便于快速验证链路。' }
  let secondaryAction = hasRequest
    ? { kind: 'openTrace', requestId, label: '打开 Trace 详情', detail: '查看完整请求链路和节点证据。' }
    : { kind: 'noop', label: '等待 Request ID', detail: '请求开始后会自动生成 request_id。' }

  if (streaming) {
    status = 'streaming'
    headline = '正在流式调试，先观察事件推进'
    description = '当前请求正在生成回复。优先看右侧事件流是否持续推进，必要时停止流式请求。'
    primaryAction = { kind: 'stop', label: '停止流式请求', detail: '中断当前请求并保留已收到事件。' }
    secondaryAction = hasRequest
      ? { kind: 'openTrace', requestId, label: '打开 Trace 详情', detail: 'Trace 可能仍在补齐。' }
      : { kind: 'noop', label: '等待 Request ID', detail: 'SSE 事件出现后会自动更新。' }
  } else if (error) {
    status = 'blocked'
    headline = '请求失败，先恢复发送链路'
    description = String(error || '请求失败')
    primaryAction = hasMessage
      ? { kind: 'send', label: '重试发送', detail: '保留当前输入并重新发起请求。' }
      : { kind: 'sample', label: '填入调试样例', detail: '先用样例排除空输入问题。' }
    secondaryAction = { kind: 'sample', label: '换用样例', detail: '用稳定短消息重测。' }
  } else if (traceIssueCount > 0) {
    status = 'attention'
    headline = `Trace 已返回，${traceIssueCount} 个节点需要关注`
    description = '调试回复已经落到 Trace。先打开详情页看异常节点、输入输出和护栏回放。'
    primaryAction = { kind: 'openTrace', requestId, label: '打开 Trace 详情', detail: '打开详情页核对异常节点和输入输出证据。' }
    secondaryAction = { kind: 'refreshTrace', label: '刷新 Trace', detail: '重新拉取当前 request 的链路快照。' }
  } else if (hasRequest) {
    status = 'ready'
    headline = '已拿到 Request ID，可进入 Trace 复核'
    description = '本次聊天已经具备可下钻 request。可打开 Trace 详情核对阶段覆盖、流式事件和回复输出。'
    primaryAction = { kind: 'openTrace', requestId, label: '打开 Trace 详情', detail: '打开详情页核对阶段覆盖和回复输出。' }
    secondaryAction = { kind: 'refreshTrace', label: '刷新 Trace', detail: traceBusy ? 'Trace 正在刷新。' : '拉取最新链路快照。' }
  } else if (hasMessage) {
    status = 'ready'
    headline = '消息已就绪，可以发起调试'
    description = '发送后会实时收集 SSE 事件并自动捕获 request_id。'
  }

  return {
    status,
    eyebrow: 'Chat Debug Command',
    title: '聊天调试控制台',
    headline,
    description,
    primaryAction,
    secondaryAction,
    cards: [
      {
        key: 'input',
        label: '输入状态',
        value: hasMessage ? '已就绪' : (hasRequest ? '已发送' : '待输入'),
        detail: hasMessage
          ? truncateDebugText(message, 72)
          : (hasRequest ? '本次请求已提交，可继续输入下一条。' : '可以填入样例或手动输入。'),
        tone: hasMessage || hasRequest ? 'ready' : 'degraded',
      },
      {
        key: 'events',
        label: 'SSE 事件',
        value: String(eventCount),
        detail: `链路节点事件 ${chainCount}`,
        tone: eventCount > 0 ? 'ready' : 'degraded',
      },
      {
        key: 'trace',
        label: 'Trace 状态',
        value: traceStatus,
        detail: hasRequest ? `节点 ${nodeCount} · 待关注 ${traceIssueCount}` : '等待 request_id。',
        tone: traceIssueCount > 0 ? 'degraded' : (hasRequest ? 'ready' : 'blocked'),
      },
      {
        key: 'latest',
        label: '最新阶段',
        value: truncateDebugText(latestStage, 32) || '暂无',
        detail: stats ? `${stats.elapsed}ms · ${stats.eventCount} 事件` : '发送后显示耗时和事件数。',
        tone: latestEvent || traceLatestNode ? 'ready' : 'degraded',
      },
    ],
    handoffSteps,
    gates: [
      {
        label: '输入',
        state: hasMessage || streaming || hasRequest ? 'ready' : 'warning',
        detail: hasMessage ? '消息可发送。' : (streaming || hasRequest ? '请求已发出。' : '尚未输入消息。'),
      },
      {
        label: '流式',
        state: streaming ? 'warning' : (eventCount > 0 ? 'ready' : 'warning'),
        detail: streaming ? '正在接收 SSE。' : (eventCount > 0 ? '事件流已记录。' : '等待请求开始。'),
      },
      {
        label: 'Request',
        state: hasRequest ? 'ready' : 'blocked',
        detail: hasRequest ? requestId : '还没有 request_id。',
      },
      {
        label: 'Trace',
        state: traceIssueCount > 0 ? 'warning' : (traceSnapshot ? 'ready' : (hasRequest ? 'warning' : 'blocked')),
        detail: traceIssueCount > 0 ? `${traceIssueCount} 个节点需关注。` : (traceSnapshot ? 'Trace 快照已可读。' : '等待 Trace 快照。'),
      },
    ],
  }
}

export function buildChatDebugTraceLoadState({
  traceSnapshot = null,
  traceError = null,
  traceLoading = false,
  traceRefreshing = false,
  traceStale = false,
  requestId = null,
} = {}) {
  return resolveLoadState({
    loading: traceLoading || traceRefreshing,
    error: traceError,
    hasData: Boolean(traceSnapshot),
    hasSamples: (traceSnapshot?.node_count ?? traceSnapshot?.nodes?.length ?? 0) > 0,
    available: Boolean(requestId),
    stale: traceStale,
  })
}

export function buildChatDebugTraceView({
  sseEvents = [],
  chatHistory = [],
  currentResponse = '',
  traceSnapshot = null,
  traceError = null,
  traceLoading = false,
  traceRefreshing = false,
  traceStale = false,
  requestId = null,
} = {}) {
  const chainEvents = getChatDebugChainEvents(sseEvents)
  const latestEvent = Array.isArray(sseEvents) ? (sseEvents[sseEvents.length - 1] ?? null) : null
  const traceSummary = traceSnapshot?.summary ?? {}
  const traceLatestNode = getLatestTraceNode(traceSnapshot)
  const traceIssueCount = getTraceIssueCount(traceSnapshot)
  const traceNodeCount = traceSnapshot?.node_count ?? chainEvents.length
  const traceBusy = Boolean(traceLoading || traceRefreshing)
  const tracePreviews = buildChatDebugTracePreviews({
    traceSummary,
    chatHistory,
    currentResponse,
  })
  const traceLoadState = buildChatDebugTraceLoadState({
    traceSnapshot,
    traceError,
    traceLoading,
    traceRefreshing,
    traceStale,
    requestId,
  })

  return {
    chainEvents,
    latestEvent,
    traceSummary,
    traceLatestNode,
    traceIssueCount,
    traceNodeCount,
    traceBusy,
    traceInputPreview: tracePreviews.traceInputPreview,
    traceOutputPreview: tracePreviews.traceOutputPreview,
    traceLoadState,
    heroMetaItems: buildChatDebugHeroMetaItems({
      chatHistory,
      sseEvents,
      traceSnapshot,
      chainEventCount: chainEvents.length,
      requestId,
    }),
  }
}
