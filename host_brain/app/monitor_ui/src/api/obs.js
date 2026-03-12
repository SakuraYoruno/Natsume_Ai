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

// ===== TODO-30: VTS 通道统计 =====

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

// ===== TODO-31: 蒸馏统计 =====

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

// ===== TODO-37: WeChat 通道管理 =====

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

// ===== TODO-34: AuditRetention 保留策略统计 =====

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

// ===== TODO-40: QA 违规统计 =====

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

// ===== TODO-41: LLM 调用成本统计 =====

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

// ===== TODO-42: Checkpoint 状态查询 =====

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

// ===== TODO-43: RateLimitStore 后端状态 =====

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

// ===== TODO-47: RoutingMatrix 路由决策统计 =====

/**
 * RoutingMatrix 路由决策统计（按 model_id 分组的选择次数/降级次数/降级率/task_type 分布）
 * @param {object} params - { window_minutes }
 */
export function fetchLlmRouteStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/llm/route-stats${qs ? '?' + qs : ''}`)
}

// ===== TODO-48: InputDispatch 生命周期统计 =====

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

// ===== TODO-49: ContextPack token 预算统计 =====

/**
 * ContextPack token 预算统计（各槽位平均 token 消耗/截断率/冲突抑制率）
 * @param {object} params - { window_minutes }
 */
export function fetchContextPackStats(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/context-pack/stats${qs ? '?' + qs : ''}`)
}

// ===== TODO-50: UIAssertionGuard 统计 =====

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

// ===== TODO-53: FailureMatrix 失败事件统计 =====

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

// ===== TODO-54: PermissionGuard 权限拒绝统计 =====

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

// ===== TODO-55: TaskScheduler 执行历史趋势 =====

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

// ===== TODO-56: ActionPlan 执行成功率/指令类型分布 =====

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



