/**
 * API 请求层 — 封装所有 /obs/* 请求
 */

const BASE = ''

async function request(url, options = {}) {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

// ===== 工具函数 =====

/** 将 '1h'|'6h'|'24h'|'7d' 转换为分钟数 */
function timeRangeToMinutes(range) {
  const map = { '1h': 60, '6h': 360, '24h': 1440, '7d': 10080 }
  return map[range] ?? 60
}

// ===== Overview =====

/** 聚合双端所有服务健康状态 */
export function fetchServicesHealth(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/services/health${qs ? '?' + qs : ''}`)
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
  return request(`/ui/search-requests${qs ? '?' + qs : ''}`)
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

// ===== TODO-23: Bond 管理 =====

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

// ===== TODO-24: Tools 管理 =====

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

// ===== TODO-26: Memory 子系统统计 =====

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

// ===== TODO-25: Scheduler 调度器管理 =====

/** 列出所有调度任务（含健康状态） */
export function fetchSchedulerTasks() {
  return request('/scheduler/tasks')
}

/** 调度器健康快照 */
export function fetchSchedulerHealth() {
  return request('/scheduler/health')
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
