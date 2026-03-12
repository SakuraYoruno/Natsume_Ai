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
