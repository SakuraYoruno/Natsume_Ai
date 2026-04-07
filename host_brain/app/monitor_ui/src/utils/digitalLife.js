export const MODE_META = {
  normal: { emoji: '🟢', label: '正常', desc: '全功能开放' },
  focus: { emoji: '🎯', label: '专注', desc: '压低主动打扰' },
  gaming: { emoji: '🎮', label: '游戏', desc: '低延迟陪玩链路' },
  sleep: { emoji: '💤', label: '睡眠', desc: '睡眠 / 梦境态' },
  maintenance: { emoji: '🔧', label: '维护', desc: '维护保护' },
  locked: { emoji: '🔒', label: '锁定', desc: '锁定保护' },
}

export const STAGE_LABELS = {
  STRANGER: '陌生人',
  ACQUAINTANCE: '熟人',
  FRIEND: '朋友',
  CLOSE_FRIEND: '挚友',
  COMPANION: '伴侣',
}

export function classNames(...values) {
  return values.filter(Boolean).join(' ')
}

export function asArray(value) {
  return Array.isArray(value) ? value.filter((item) => item != null && item !== '') : []
}

export function formatStage(stage) {
  return stage ? STAGE_LABELS[stage] || stage : '—'
}

export function formatDateTime(tsMs) {
  if (!tsMs) {
    return '—'
  }
  try {
    return new Date(Number(tsMs)).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return '—'
  }
}

export function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return '—'
  }
  return numeric.toFixed(digits)
}

export function formatBoolean(value, yes = '是', no = '否') {
  return value ? yes : no
}

export function compactText(value, maxLength = 120) {
  const text = String(value || '').trim()
  if (!text) {
    return '—'
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}
