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

// ===== Overview =====

/** 聚合双端所有服务健康状态 */
export function fetchServicesHealth() {
  return request('/obs/services/health')
}

// ===== Sessions =====

/**
 * 分页会话列表
 * @param {object} params - { page, page_size, status, keyword, time_range }
 */
export function fetchSessions(params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ''))
  ).toString()
  return request(`/obs/sessions${qs ? '?' + qs : ''}`)
}

// ===== Trace =====

/**
 * 获取指定 request_id 的结构化节点列表
 * @param {string} requestId
 */
export function fetchTraceNodes(requestId) {
  return request(`/obs/trace/${encodeURIComponent(requestId)}/nodes`)
}

// ===== Metrics =====

/**
 * 多维度时序数据
 * @param {object} params - { time_range: '1h'|'6h'|'24h'|'7d' }
 */
export function fetchMetricsTimeseries(params = {}) {
  const qs = new URLSearchParams(params).toString()
  return request(`/obs/metrics/timeseries${qs ? '?' + qs : ''}`)
}

// ===== 兼容旧接口（v2 panel.html 使用） =====

export function fetchMetrics() {
  return request('/metrics')
}

export function fetchRecentRequests(params = {}) {
  const qs = new URLSearchParams(params).toString()
  return request(`/ui/search-requests${qs ? '?' + qs : ''}`)
}
