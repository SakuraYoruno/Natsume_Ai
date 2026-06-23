export const LOAD_STATES = Object.freeze({
  LOADING: 'loading',
  REFRESHING: 'refreshing',
  STALE: 'stale',
  NO_SAMPLE: 'no_sample',
  UNAVAILABLE: 'unavailable',
  ERROR: 'error',
  READY: 'ready',
})

const LOAD_STATE_META = {
  [LOAD_STATES.LOADING]: {
    label: '加载中',
    description: '首次加载中，暂未形成有效观测数据。',
    className: 'border-blue-200 bg-blue-50 text-blue-700',
  },
  [LOAD_STATES.REFRESHING]: {
    label: '刷新中',
    description: '已有旧数据，正在刷新最新快照。',
    className: 'border-sky-200 bg-sky-50 text-sky-700',
  },
  [LOAD_STATES.STALE]: {
    label: '已过期',
    description: '已有数据，但超过了建议新鲜度阈值。',
    className: 'border-amber-200 bg-amber-50 text-amber-700',
  },
  [LOAD_STATES.NO_SAMPLE]: {
    label: '无样本',
    description: '接口成功返回，但当前窗口没有样本。',
    className: 'border-gray-200 bg-gray-50 text-gray-600',
  },
  [LOAD_STATES.UNAVAILABLE]: {
    label: '暂不可用',
    description: '当前模块不可用、未启用或未产出可用快照。',
    className: 'border-slate-200 bg-slate-50 text-slate-600',
  },
  [LOAD_STATES.ERROR]: {
    label: '加载失败',
    description: '请求失败，请查看错误信息或重试。',
    className: 'border-red-200 bg-red-50 text-red-700',
  },
  [LOAD_STATES.READY]: {
    label: '已就绪',
    description: '数据已加载，可用于观测与排查。',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  },
}

export function getLoadStateMeta(state) {
  return LOAD_STATE_META[state] || LOAD_STATE_META[LOAD_STATES.UNAVAILABLE]
}

export function resolveLoadState({
  loading = false,
  error = null,
  hasData = false,
  hasSamples = false,
  available = true,
  stale = false,
} = {}) {
  if (loading && hasData) return LOAD_STATES.REFRESHING
  if (loading) return LOAD_STATES.LOADING
  if (error) return LOAD_STATES.ERROR
  if (!available) return LOAD_STATES.UNAVAILABLE
  if (stale) return LOAD_STATES.STALE
  if (hasData && !hasSamples) return LOAD_STATES.NO_SAMPLE
  if (hasData) return LOAD_STATES.READY
  return LOAD_STATES.UNAVAILABLE
}

export function formatWindowLabel(windowMinutes) {
  const value = Number(windowMinutes || 0)
  if (!value) return '窗口 —'
  if (value % 1440 === 0) {
    return `窗口 ${(value / 1440).toFixed(0)}d`
  }
  if (value % 60 === 0) {
    return `窗口 ${(value / 60).toFixed(0)}h`
  }
  return `窗口 ${value}m`
}

export function formatRelativeTime(timestamp) {
  const value = typeof timestamp === 'number' ? timestamp : new Date(timestamp || 0).getTime()
  if (!value) return '更新时间 —'
  const diffMs = Date.now() - value
  if (diffMs < 0) return '更新时间 刚刚'
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `更新时间 ${diffSec}s 前`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `更新时间 ${diffMin}m 前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `更新时间 ${diffHour}h 前`
  const diffDay = Math.floor(diffHour / 24)
  return `更新时间 ${diffDay}d 前`
}
