import { deepRepairMojibake } from '../utils/text.js'

/**
 * API 请求层 — 封装所有 /obs/* 请求
 */

const BASE = ''
// The overview dashboard fans out many concurrent /obs requests on first paint.
// In local dogfooding, those bursts can exceed 15s and caused sections to
// silently fall back to null, which then rendered misleading zeros / empty states.
const REQUEST_TIMEOUT_MS = 35_000
const HOT_GET_CACHE_TTL_MS = 1_500
const HOT_GET_PREFIXES = [
  '/obs/runtime-snapshot',
  '/obs/prompt-builder/stats',
  '/obs/capability-gaps/stats',
  '/obs/insights/summary-snapshot',
  '/obs/digital-life/overview-snapshot',
  '/obs/codex-bio-ops',
  '/obs/life-body',
  '/obs/life-timeline',
  '/obs/pathologies',
  '/obs/trace-feed/snapshot',
]
const inflightHotGets = new Map()
const hotGetCache = new Map()

function shouldDedupHotGet(url, method) {
  return String(method || 'GET').toUpperCase() === 'GET'
    && HOT_GET_PREFIXES.some((prefix) => String(url || '').startsWith(prefix))
}

function cloneObsPayload(payload) {
  if (typeof globalThis.structuredClone === 'function') {
    return globalThis.structuredClone(payload)
  }
  return JSON.parse(JSON.stringify(payload))
}

function getHotGetCached(url) {
  const cached = hotGetCache.get(url)
  if (!cached) return null
  if (cached.expiresAtMs <= Date.now()) {
    hotGetCache.delete(url)
    return null
  }
  return cloneObsPayload(cached.data)
}

function setHotGetCached(url, data) {
  hotGetCache.set(url, {
    expiresAtMs: Date.now() + HOT_GET_CACHE_TTL_MS,
    data: cloneObsPayload(data),
  })
}

function invalidateHotGetPrefix(prefix) {
  for (const key of hotGetCache.keys()) {
    if (String(key).startsWith(prefix)) hotGetCache.delete(key)
  }
}

async function executeRequest(url, options = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...rest } = options
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), Math.max(1000, Number(timeoutMs) || REQUEST_TIMEOUT_MS))
  try {
    const res = await fetch(BASE + url, {
      headers: { 'Content-Type': 'application/json', ...rest.headers },
      signal: controller.signal,
      ...rest,
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`HTTP ${res.status}: ${text}`)
    }
    const data = await res.json()
    return deepRepairMojibake(data)
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error(`request timeout after ${Math.max(1000, Number(timeoutMs) || REQUEST_TIMEOUT_MS)}ms: ${url}`)
    }
    throw error
  } finally {
    globalThis.clearTimeout(timeout)
  }
}

async function request(url, options = {}) {
  const method = String(options?.method || 'GET').toUpperCase()
  if (!shouldDedupHotGet(url, method)) {
    return executeRequest(url, options)
  }

  const cached = getHotGetCached(url)
  if (cached != null) {
    return cached
  }

  const inflight = inflightHotGets.get(url)
  if (inflight) {
    return inflight.then((data) => cloneObsPayload(data))
  }

  const promise = executeRequest(url, options)
    .then((data) => {
      setHotGetCached(url, data)
      return data
    })
    .finally(() => {
      inflightHotGets.delete(url)
    })

  inflightHotGets.set(url, promise)
  return promise.then((data) => cloneObsPayload(data))
}

// ===== 工具函数 =====

/** 将 '1h'|'6h'|'24h'|'7d' 转换为分钟数 */
function timeRangeToMinutes(range) {
  const map = { '1h': 60, '6h': 360, '24h': 1440, '7d': 10080 }
  return map[range] ?? 60
}

// ===== Overview =====

/** 聚合双端所有器官健康状态 */
export function fetchServicesHealth(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/services/health${qs ? '?' + qs : ''}`)
}

/**
 * 观测聚合指标（含 M-MA4 多 agent 专项：agent_hop_count、agent_dispatch_count、switches 等）
 */
export function fetchObsMetrics() {
  return request('/obs/metrics')
}

// ===== Sessions =====

/**
 * 分页会话列表
 * @param {object} params - { page, page_size, status, keyword, time_range }
 */
export function fetchSessions(params = {}) {
  const { time_range, ...rest } = params
  const mapped = {
    ...rest,
    window_minutes: time_range ? timeRangeToMinutes(time_range) : undefined,
  }
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(mapped).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/sessions${qs ? '?' + qs : ''}`)
}

// ===== Trace =====

/**
 * 获取指定 request_id 的结构化节点列表
 * @param {string} requestId
 */
export function fetchTraceNodes(requestId) {
  return request(`/obs/trace/${encodeURIComponent(requestId)}`)
}

/**
 * 获取指定 request_id 的 Chat Stream SSE 事件流（PRESENCE/AGENT_SELECTED 等）
 * @param {string} requestId
 */
export function fetchTraceStreamEvents(requestId) {
  return request(`/obs/trace/${encodeURIComponent(requestId)}/stream_events`)
}

export function connectTraceLiveFeed(params = {}, handlers = {}) {
  if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
    throw new Error('EventSource not available')
  }

  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  const source = new EventSource(`/obs/trace-feed/live${qs ? '?' + qs : ''}`)

  const parseEvent = (event) => {
    try {
      return JSON.parse(event.data)
    } catch (error) {
      console.warn('[SSE] Failed to parse trace live feed payload:', error)
      return null
    }
  }

  source.onopen = () => {
    handlers.onOpen?.()
  }

  source.addEventListener('snapshot', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onSnapshot?.(data)
  })

  source.addEventListener('delta', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onDelta?.(data)
  })

  source.addEventListener('heartbeat', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onHeartbeat?.(data)
  })

  source.addEventListener('trace_error', (event) => {
    const data = parseEvent(event)
    handlers.onError?.(data ?? { type: 'error', detail: 'trace live feed payload parse failed' })
  })

  source.onerror = (error) => {
    handlers.onConnectionError?.(error)
  }

  return {
    close: () => source.close(),
    source,
  }
}

export function fetchTraceLiveSnapshot(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/trace-feed/snapshot${qs ? '?' + qs : ''}`)
}

export function fetchInsightsSummarySnapshot(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/insights/summary-snapshot${qs ? '?' + qs : ''}`)
}

export function fetchDigitalLifeOverviewSnapshot(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/digital-life/overview-snapshot${qs ? '?' + qs : ''}`)
}

export function fetchCodexBioOpsObservability(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/codex-bio-ops${qs ? '?' + qs : ''}`)
}

export function fetchCodexBioOpsAdoptions(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/codex-bio-ops/adoptions${qs ? '?' + qs : ''}`)
}

export function fetchCodexBioOpsAdoptionDetail(candidateId, params = {}) {
  const id = String(candidateId || '').trim()
  if (!id) {
    throw new Error('candidate_id is required')
  }
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/codex-bio-ops/adoptions/${encodeURIComponent(id)}${qs ? '?' + qs : ''}`)
}

export function acceptCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  const id = String(candidateId || '').trim()
  if (!id) {
    throw new Error('candidate_id is required')
  }
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/codex-bio-ops/adoptions/${encodeURIComponent(id)}/accept${qs ? '?' + qs : ''}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  }).then((data) => {
    invalidateHotGetPrefix('/obs/codex-bio-ops')
    return data
  })
}

export function rejectCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  const id = String(candidateId || '').trim()
  if (!id) {
    throw new Error('candidate_id is required')
  }
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/codex-bio-ops/adoptions/${encodeURIComponent(id)}/reject${qs ? '?' + qs : ''}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  }).then((data) => {
    invalidateHotGetPrefix('/obs/codex-bio-ops')
    return data
  })
}

export function fetchLifeBody(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/life-body${qs ? '?' + qs : ''}`)
}

export function fetchLifeTimeline(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/life-timeline${qs ? '?' + qs : ''}`)
}

export function fetchPathologies(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/pathologies${qs ? '?' + qs : ''}`)
}

export function connectTraceRequestLive(requestId, handlers = {}, params = {}) {
  if (!requestId) {
    throw new Error('requestId is required')
  }
  return connectTraceLiveFeed(
    {
      keyword: requestId,
      limit: 1,
      window_minutes: 10080,
      interval_ms: 1000,
      ...params,
    },
    handlers
  )
}

/**
 * 获取当前 Host 图编排定义（节点、边），供展示图拓扑
 */
export function fetchGraphSchema() {
  return request('/obs/graph/schema')
}

// ===== Metrics =====

/**
 * 多维度时序数据
 * @param {object} params - { time_range: '1h'|'6h'|'24h'|'7d' }
 */
export function fetchMetricsTimeseries(params = {}) {
  const { time_range, ...rest } = params
  const rangeMinutes = time_range ? timeRangeToMinutes(time_range) : 60
  // 根据时间范围自动选择合适的聚合间隔
  const intervalMinutes = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : rangeMinutes <= 1440 ? 60 : 360
  const mapped = {
    ...rest,
    range_minutes: rangeMinutes,
    interval_minutes: intervalMinutes,
  }
  const qs = new URLSearchParams(mapped).toString()
  return request(`/obs/metrics/timeseries${qs ? '?' + qs : ''}`)
}

// ===== LLM 节点 =====

/** LLM 云节点独立健康状态 */
export function fetchLlmNodes(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/llm/nodes${qs ? '?' + qs : ''}`)
}

// ===== 兼容旧接口（v2 panel.html 使用） =====

export function fetchMetrics() {
  return request('/metrics')
}

export function fetchRecentRequests(params = {}) {
  const qs = new URLSearchParams(params).toString()
  return request(`/api/ui/search-requests${qs ? '?' + qs : ''}`)
}

// ===== P1: Governor 决策统计 =====

/**
 * Governor 决策漏斗 + reason_code 分布
 * @param {object} params - { window_minutes, interval_minutes }
 */
export function fetchGovernorStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/governor/stats${qs ? '?' + qs : ''}`)
}

// ===== P1: 全局错误日志 =====

/**
 * 全局错误日志统计
 * @param {object} params - { window_minutes, interval_minutes, limit }
 */
export function fetchErrors(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/errors${qs ? '?' + qs : ''}`)
}

// ===== P2/P3: 子系统健康统计 =====

/**
 * 子系统健康统计（Memory/Retrieval/Skill/Agent Loop/Airlock 等）
 * @param {object} params - { window_minutes }
 */
export function fetchSubsystemsStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/subsystems/stats${qs ? '?' + qs : ''}`)
}

// ===== P3: Agent Loop 详细统计 =====

/**
 * Agent Loop 详细统计
 * @param {object} params - { window_minutes, limit }
 */
export function fetchAgentLoopStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/agent_loop/stats${qs ? '?' + qs : ''}`)
}

// ===== Canon 管理 =====

/**
 * 列出 Canon 提案（分页，可按 status 过滤）
 * @param {object} params - { status, page, page_size }
 */
export function fetchCanonProposals(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/canon/proposals${qs ? '?' + qs : ''}`)
}

// ===== Prompt Config =====

export function fetchPromptTemplates(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/prompt-config/templates${qs ? '?' + qs : ''}`)
}

export function fetchPromptTemplate(templateId) {
  return request(`/prompt-config/templates/${encodeURIComponent(templateId)}`)
}

export function fetchPersonaFirstBatchCatalog() {
  return request('/prompt-config/catalog/persona-first-batch')
}

/**
 * 列出当前 World Canon 文档（SSOT 快照）
 */
export function fetchCanonDocuments() {
  return request('/canon/documents')
}

/**
 * 提交新 Canon 提案
 * @param {object} body - CanonProposalSubmitRequest
 */
export function submitCanonProposal(body) {
  return request('/canon/proposals', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 审核通过 Canon 提案
 * @param {string} proposalId
 * @param {object} body - CanonProposalApproveRequest
 */
export function approveCanonProposal(proposalId, body) {
  return request(`/canon/proposals/${encodeURIComponent(proposalId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 拒绝 Canon 提案
 * @param {string} proposalId
 * @param {object} body - CanonProposalRejectRequest
 */
export function rejectCanonProposal(proposalId, body) {
  return request(`/canon/proposals/${encodeURIComponent(proposalId)}/reject`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * Ref #81: 回滚已 APPLIED 的 Canon 提案（恢复到 base_version）
 * @param {string} proposalId
 * @param {object} body - { rollback_reason, operator_node_id }
 */
export function rollbackCanonProposal(proposalId, body = {}) {
  return request(`/canon/proposals/${encodeURIComponent(proposalId)}/rollback`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * Ref #44: ArtifactVault Manifest 统计（ACTIVE/UNDONE/GC、可逆比例、最近 manifest 列表）
 * @param {object} params - { limit }
 */
export function fetchVaultStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/vault/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #82: 请求链路统计（Phase XIV v4）=====

/**
 * 请求链路统计（各阶段耗时分布/降级率/失败率）
 * @param {object} params - { window_minutes }
 */
export function fetchChainStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/chain/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #23: Bond 管理 =====

/** BondStageEngine 健康状态 */
export function fetchBondHealth() {
  return request('/bond/health')
}

/** 列出所有有 Bond 状态的用户 */
export function fetchBondUsers() {
  return request('/bond/users')
}

/**
 * 查询用户当前 Bond 阶段与积分状态
 * @param {string} userId
 */
export function fetchBondStage(userId) {
  return request(`/bond/stage/${encodeURIComponent(userId)}`)
}

/**
 * 获取 context-pack-safe Bond Summary
 * @param {string} userId
 */
export function fetchBondSummary(userId) {
  return request(`/bond/summary/${encodeURIComponent(userId)}`)
}

/**
 * 提交 Bond 事件
 * @param {object} body - BondEventRequest
 */
export function submitBondEvent(body) {
  return request('/bond/event', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ===== Ref #24: Tools 管理 =====

/** ToolBroker 健康状态 */
export function fetchToolHealth() {
  return request('/tools/health')
}

/**
 * 获取 Top-K 工具元数据包
 * @param {object} params - { query, k, include_write, tags }
 */
export function fetchToolMetaPack(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/tools/meta-pack${qs ? '?' + qs : ''}`)
}

/**
 * 查询单个工具元数据
 * @param {string} toolId
 */
export function fetchToolMeta(toolId) {
  return request(`/tools/${encodeURIComponent(toolId)}`)
}

/**
 * 执行工具
 * @param {object} body - ToolExecuteRequest
 */
export function executeTool(body) {
  return request('/tools/execute', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function fetchCustomSkillProviders() {
  return request('/tools/skills/custom-providers')
}

export function fetchCustomSkillProviderTemplate() {
  return request('/tools/skills/custom-providers/template')
}

export function installCustomSkillProvider(body) {
  return request('/tools/skills/custom-providers/install', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ===== Ref #26: Memory 子系统统计 =====

/**
 * Memory 子系统详细统计（EpisodicStore/MemoryGuard/ContextCompressor/LongTermDistiller）
 * @param {object} params - { window_minutes }
 */
export function fetchMemoryStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/memory/stats${qs ? '?' + qs : ''}`)
}

/**
 * 记忆常驻设定 / 人格风格注入快照。
 */
export function fetchResidentMemoryContext() {
  return request('/obs/memory/resident-context')
}

// ===== Ref #25: Scheduler 调度器管理 =====

/** 列出所有调度任务（含健康状态） */
export function fetchSchedulerTasks() {
  return request('/scheduler/tasks')
}

/** 调度器健康快照 */
export function fetchSchedulerHealth() {
  return request('/scheduler/health')
}

export function fetchImmuneGateStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/immune-gate/stats${qs ? '?' + qs : ''}`)
}

export function fetchImmuneGateReplay(requestId, params = {}) {
  const rid = String(requestId || '').trim()
  if (!rid) {
    throw new Error('request_id is required')
  }
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/immune-gate/replay/${encodeURIComponent(rid)}${qs ? '?' + qs : ''}`)
}

/**
 * 启用调度任务
 * @param {string} taskId
 */
export function enableSchedulerTask(taskId) {
  return request(`/scheduler/tasks/${encodeURIComponent(taskId)}/enable`, { method: 'POST' })
}

/**
 * 禁用调度任务
 * @param {string} taskId
 */
export function disableSchedulerTask(taskId) {
  return request(`/scheduler/tasks/${encodeURIComponent(taskId)}/disable`, { method: 'POST' })
}

/**
 * 立即触发调度任务（通过 PATCH 重置 trigger_config 中的 next_run_ts 为 0）
 * @param {string} taskId
 */
export function triggerSchedulerTask(taskId) {
  // scheduler_routes 没有专用 trigger 端点，通过 PATCH 重置触发时间来实现立即触发
  return request(`/scheduler/tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ trigger_config: { next_run_ts: 0 } }),
  })
}

// ===== Ref #30: VTS 通道统计 =====

/**
 * VTS 通道统计（表情应用率/降级率/拒绝率、Top 情感标签、Talk Gate 事件统计）
 * @param {object} params - { window_minutes }
 */
export function fetchVtsStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/vts/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #31: 蒸馏统计 =====

/**
 * LongTermDistiller 蒸馏统计（循环次数、提案提交量、蒸馏成功率）
 * @param {object} params - { window_minutes, limit }
 */
export function fetchDistillationStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/distillation/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #37: WeChat 通道管理 =====

/**
 * WeChat 通道统计（通道健康状态、频率限制统计、最近事件）
 * @param {object} params - { window_minutes, limit }
 */
export function fetchWeChatStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/wechat/stats${qs ? '?' + qs : ''}`)
}

/**
 * 手动重置 WeChat 通道频率限制计数器
 * @param {object} params - { reason }
 */
export function resetWeChatChannel(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/wechat/reset${qs ? '?' + qs : ''}`, { method: 'POST' })
}

// ===== Ref #34: AuditRetention 保留策略统计 =====

/**
 * AuditRetention 保留策略执行统计
 * @param {object} params - { limit }
 */
export function fetchRetentionStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/retention/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #40: QA 违规统计 =====

/**
 * QA 违规统计（违规码分布、降级率、重写成功率、时序趋势、最近违规列表）
 * @param {object} params - { window_minutes, interval_minutes, limit }
 */
export function fetchQaStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/qa/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #41: LLM 调用成本统计 =====

/**
 * LLM 调用成本统计（按 model_id 分组的 token 消耗/平均延迟/调用次数）
 * @param {object} params - { window_minutes, interval_minutes }
 */
export function fetchLlmCallStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/llm/call-stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #42: Checkpoint 状态查询 =====

/**
 * 最近 checkpoint 列表
 * @param {object} params - { limit, include_expired }
 */
export function fetchCheckpoints(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/checkpoints${qs ? '?' + qs : ''}`)
}

/**
 * 单个 checkpoint 详情
 * @param {string} checkpointId
 */
export function fetchCheckpointDetail(checkpointId) {
  return request(`/obs/checkpoints/${encodeURIComponent(checkpointId)}`)
}

// ===== Ref #43: RateLimitStore 后端状态 =====

/**
 * RateLimitStore 后端状态（当前后端类型/是否降级/各 key 当前计数/TTL）
 * @param {object} params - { top_k }
 */
export function fetchRateLimitBackendStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/rate-limit/backend-stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #47: LLM 路由决策统计 =====

/**
 * LLM 路由决策统计（按 model_id 分组的选择次数/降级次数/降级率/task_type 分布）
 * @param {object} params - { window_minutes }
 */
export function fetchLlmRouteStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/llm/route-stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #48: InputDispatch 生命周期统计 =====

/**
 * InputDispatch 生命周期统计（按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量）
 * @param {object} params - { window_minutes, interval_minutes }
 */
export function fetchInputDispatchStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/dispatch-stats${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop stats (ACK/progress/deviation recovery/closed-loop rate).
 * @param {object} params - { window_minutes }
 */
export function fetchInputHumanoidLoopStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-stats${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop trend stats.
 * @param {object} params - { window_minutes, interval_minutes }
 */
export function fetchInputHumanoidLoopTrend(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-trend${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop alerts evaluated from trend buckets.
 * @param {object} params
 */
export function fetchInputHumanoidLoopAlerts(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-alerts${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop remediation playbook.
 * @param {object} params
 */
export function fetchInputHumanoidLoopRemediation(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-remediation${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop diagnostics and root-cause summary.
 * @param {object} params
 */
export function fetchInputHumanoidLoopDiagnostics(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-diagnostics${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop hotspot clustering.
 * @param {object} params
 */
export function fetchInputHumanoidLoopHotspots(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-hotspots${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop hotspot time trend.
 * @param {object} params
 */
export function fetchInputHumanoidLoopHotspotTrend(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-hotspot-trend${qs ? '?' + qs : ''}`)
}

/**
 * Gateway humanoid closed-loop source correlation.
 * @param {object} params
 */
export function fetchInputHumanoidLoopSourceCorrelation(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/input/humanoid-loop-source-correlation${qs ? '?' + qs : ''}`)
}

// ===== Ref #50: UIAssertionGuard 统计 =====

/**
 * UIAssertionGuard 统计（SENSITIVE_UI_BLOCKED/UI_PRECONDITION_FAILED/UI_POSTCONDITION_FAILED 分布）
 * @param {object} params - { window_minutes }
 */
export function fetchUIAssertionStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/ui-assertion/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #53: FailureMatrix 失败事件统计 =====

/**
 * FailureMatrix 失败事件统计（7 种失败事件分布、中止率、降级模式分布）
 * @param {object} params - { window_minutes }
 */
export function fetchFailureStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/failure/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #54: PermissionGuard 权限拒绝统计 =====

/**
 * PermissionGuard 权限拒绝统计（工具调用允许/拒绝分布、SCHEDULER 拒绝次数）
 * @param {object} params - { window_minutes }
 */
export function fetchPermissionStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/permission/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #55: TaskScheduler 执行历史趋势 =====

/**
 * TaskScheduler 执行历史趋势（任务状态分布、run_count/error_count 趋势）
 * @param {object} params - { window_minutes }
 */
export function fetchSchedulerHistory(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/scheduler/history${qs ? '?' + qs : ''}`)
}

// ===== Ref #DL-TRIBE-OBS: Tribe 事件观测 =====

/**
 * Tribe 事件观测（最近事件窗口）
 * @param {object} params - { limit, event_type, source_agent, target_agent, request_id, session_id, conversation_id }
 */
export function fetchTribeEvents(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/tribe/events${qs ? '?' + qs : ''}`)
}

// ===== Ref #56: ActionPlan 执行成功率/指令类型分布 =====

/**
 * ActionPlan 执行成功率/指令类型分布（成功率、平均置信度、指令类型分布、降级原因）
 * @param {object} params - { window_minutes }
 */
export function fetchActionPlanStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/action-plan/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #59: MemoryGuard 宪法校验统计 =====

/**
 * MemoryGuard 宪法校验统计（过滤率/拒绝原因分布/NARRATIVE_ONLY 比例/蒸馏资格）
 * @param {object} params - { window_minutes }
 */
export function fetchMemoryGuardStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/memory-guard/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #60: StateArbiter Bond 查询统计（Phase XIV v4: 仅 bond_graph）=====

/**
 * StateArbiter Bond 查询统计（仅 bond_graph 快照查询）
 * @param {object} params - { window_minutes }
 */
export function fetchStateArbiterStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/state-arbiter/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #61: ContextCompressor 压缩效率统计 =====

/**
 * ContextCompressor 压缩效率统计（压缩率/token 节省/策略分布/摘要生成次数）
 * @param {object} params - { window_minutes }
 */
export function fetchContextCompressorStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/context-compressor/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #65: PromptBuilder 构建统计 =====

/**
 * PromptBuilder 构建统计（注入检测率/slot 填充分布/高风险消息比例）
 * @param {object} params - { window_minutes }
 */
export function fetchPromptBuilderStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/prompt-builder/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #66: EpisodicStore 写入统计 =====

/**
 * EpisodicStore 写入统计（写入率/MemoryGuard 拦截率/蒸馏资格率/dream 拦截次数）
 * @param {object} params - { window_minutes }
 */
export function fetchEpisodicStoreStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/episodic-store/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #75: 会话列表 CSV 导出 =====

/**
 * 触发会话列表 CSV 导出（直接下载文件）
 * @param {object} params - { window_minutes, status, keyword, max_rows }
 */
export async function exportSessionsCsv(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  const res = await fetch(`/obs/sessions/export${qs ? '?' + qs : ''}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `sessions_${Date.now()}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ===== Ref #76: Canon 提案统计 =====

/**
 * Canon 提案统计（pending/applied/rejected 分布 + 最近提案列表）
 * @param {object} params - { window_minutes }
 */
export function fetchCanonProposalStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/canon/proposals/stats${qs ? '?' + qs : ''}`)
}

// ===== Ref #86: Agent Loop 历史执行记录 =====

/**
 * Agent Loop 历史执行记录（最近 N 次执行结果）
 * @param {object} params - { window_minutes, limit }
 */
export function fetchAgentLoopHistory(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/agent-loop/history${qs ? '?' + qs : ''}`)
}

// ===== Ref #88: 错误热力图 =====

/**
 * 错误热力图（按小时 × 节点维度统计错误分布）
 * @param {object} params - { window_hours }
 */
export function fetchErrorHeatmap(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/error-heatmap${qs ? '?' + qs : ''}`)
}

// ===== Skill-First: Capability Gap / Forge Runtime =====

/**
 * Capability Gap 聚合统计（含 forge candidates 与 forge runtime quality）
 * @param {object} params
 */
export function fetchCapabilityGapStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/capability-gaps/stats${qs ? '?' + qs : ''}`)
}

/**
 * Forge Runtime 质量快照（health/failure/latency）
 * @param {object} params - { limit }
 */
export function fetchCapabilityForgeRuntimeQuality(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/capability-gaps/forge-runtime/quality${qs ? '?' + qs : ''}`)
}

/**
 * Forge proposal 列表
 * @param {object} params - { status, limit }
 */
export function fetchCapabilityForgeProposals(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/capability-gaps/forge-proposals${qs ? '?' + qs : ''}`)
}

/**
 * Forge proposal 详情
 * @param {string} proposalId
 */
export function fetchCapabilityForgeProposal(proposalId) {
  return request(`/obs/capability-gaps/forge-proposals/${encodeURIComponent(proposalId)}`)
}

/**
 * 审批并推进 Forge proposal
 * @param {string} proposalId
 * @param {object} body - { approval_token_ref, approver, target_stage }
 */
export function approveCapabilityForgeProposal(proposalId, body) {
  return request(`/obs/capability-gaps/forge-proposals/${encodeURIComponent(proposalId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 回滚 Forge proposal
 * @param {string} proposalId
 * @param {object} body - { operator, reason }
 */
export function rollbackCapabilityForgeProposal(proposalId, body) {
  return request(`/obs/capability-gaps/forge-proposals/${encodeURIComponent(proposalId)}/rollback`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ===== Ref #105: 语义召回器统计 =====

/**
 * SemanticRecaller 配置和状态统计（use_mock/chroma/top_k/threshold/mock_doc_count）
 */
export function fetchSemanticRecallStats() {
  return request('/obs/semantic-recall/stats')
}

// ===== Gateway 六平台消息统计 =====

/**
 * Gateway 六平台消息统计
 * @param {object} params - { window_minutes }
 */
export function fetchGatewayStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/gateway/stats${qs ? '?' + qs : ''}`)
}

// ===== Skills Hub 统计 =====

/**
 * Skills Hub 技能列表与统计
 */
export function fetchSkillsHubStats() {
  return request('/obs/skills-hub/stats')
}

// ===== 存在状态 / sys_mode 管理 =====

/** 获取当前生命体存在态轴状态 */
export function fetchSysModeStatus() {
  return request('/obs/sys-mode')
}

/** 获取统一数字生命体 runtime snapshot */
export function fetchDigitalLifeRuntimeSnapshot(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/runtime-snapshot${qs ? '?' + qs : ''}`)
}

/** 切换当前生命体存在态轴 */
export function switchSysMode(body) {
  return request('/obs/sys-mode', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** 获取存在态切换历史 */
export function fetchSysModeHistory(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/sys-mode/history${qs ? '?' + qs : ''}`)
}

/** 更新自动切换规则 */
export function updateAutoRules(body) {
  return request('/obs/sys-mode/auto-rules', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

/** 发送存在态切换事件 */
export function postSysModeEvent(eventType) {
  return request(`/obs/sys-mode/event?event_type=${encodeURIComponent(eventType)}`, {
    method: 'POST',
  })
}

// ===== WebSocket 实时健康推送 =====

/**
 * 创建 WebSocket 连接到 /obs/ws/health
 * @param {function} onMessage - 收到消息的回调 (data: object)
 * @param {function} onError - 错误回调 (error: Event)
 * @param {function} onClose - 关闭回调
 * @returns {{ close: function }} - 返回关闭句柄
 */
export function connectHealthWebSocket(onMessage, onError, onClose) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const url = `${protocol}//${host}/obs/ws/health`

  const ws = new WebSocket(url)

  ws.onopen = () => {
    console.log('[WS] Health WebSocket connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage?.(data)
    } catch (e) {
      console.warn('[WS] Failed to parse message:', e)
    }
  }

  ws.onerror = (error) => {
    console.warn('[WS] Health WebSocket error:', error)
    onError?.(error)
  }

  ws.onclose = () => {
    console.log('[WS] Health WebSocket closed')
    onClose?.()
  }

  return {
    close: () => ws.close(),
    ws,
  }
}


// ===== Retrieval Runtime =====

/**
 * 当前 Retrieval embedding / reranker 实际运行模型与 fallback 统计。
 * @param {object} params - { request_id, limit, include_legacy }
 */
export function fetchRetrievalRuntime(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/retrieval/runtime${qs ? '?' + qs : ''}`)
}
