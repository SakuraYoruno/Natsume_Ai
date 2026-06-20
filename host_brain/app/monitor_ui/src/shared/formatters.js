const DEFAULT_LOCALE = 'zh-CN'
const DEFAULT_TIME_OPTIONS = Object.freeze({
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const DEFAULT_DATE_TIME_OPTIONS = Object.freeze({
  hour12: false,
})

function toFiniteTimestamp(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : null
}

export function formatPercent(value, digits = 1, { placeholder = '—' } = {}) {
  const number = Number(value)
  if (!Number.isFinite(number)) return placeholder
  return `${number.toFixed(digits)}%`
}

export function formatTime(value, {
  placeholder = '—',
  locale = DEFAULT_LOCALE,
  options = DEFAULT_TIME_OPTIONS,
} = {}) {
  const timestamp = toFiniteTimestamp(value)
  if (timestamp == null) return placeholder
  try {
    return new Date(timestamp).toLocaleTimeString(locale, options)
  } catch {
    return placeholder
  }
}

export function formatDateTime(value, {
  placeholder = '—',
  locale = DEFAULT_LOCALE,
  options = DEFAULT_DATE_TIME_OPTIONS,
} = {}) {
  const timestamp = toFiniteTimestamp(value)
  if (timestamp == null) return placeholder
  try {
    return new Date(timestamp).toLocaleString(locale, options)
  } catch {
    return placeholder
  }
}

export function formatNumber(value, digits = 2, {
  placeholder = '—',
  locale = DEFAULT_LOCALE,
  minimumFractionDigits = 0,
} = {}) {
  const number = Number(value)
  if (!Number.isFinite(number)) return placeholder
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: digits,
    minimumFractionDigits,
  }).format(number)
}
