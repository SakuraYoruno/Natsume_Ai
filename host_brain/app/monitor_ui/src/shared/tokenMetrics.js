import { formatNumber } from './formatters.js'

export const TOKEN_UNMEASURED_LABEL = '不可测'

export function normalizeNullableTokenCount(value) {
  if (value == null) return null
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && value.trim() === '') return null
  const normalized = Number(value)
  return Number.isFinite(normalized) && normalized >= 0 ? normalized : null
}

export function normalizeNullableTokenSeries(values) {
  const rows = Array.isArray(values) ? values : []
  return rows.map((value) => normalizeNullableTokenCount(value))
}

export function hasMeasuredTokenValues(values) {
  return normalizeNullableTokenSeries(values).some((value) => value != null)
}

export function buildTokenMetricView({
  value,
  measuredCalls,
  totalCalls,
  isLowerBound = false,
} = {}) {
  const normalizedValue = normalizeNullableTokenCount(value)
  const normalizedMeasuredCalls = normalizeNullableTokenCount(measuredCalls)
  const normalizedTotalCalls = normalizeNullableTokenCount(totalCalls)
  const hasKnownCoverage = normalizedMeasuredCalls != null && normalizedTotalCalls != null
  const hasPartialCoverage = hasKnownCoverage
    && normalizedTotalCalls > 0
    && normalizedMeasuredCalls < normalizedTotalCalls
  const effectiveLowerBound = normalizedValue != null && (Boolean(isLowerBound) || hasPartialCoverage)
  const coverageLabel = hasKnownCoverage && normalizedTotalCalls > 0
    ? `${formatNumber(normalizedMeasuredCalls, 0)}/${formatNumber(normalizedTotalCalls, 0)} 次调用可测`
    : null

  let detailLabel = coverageLabel
  if (normalizedValue == null) {
    detailLabel = coverageLabel ? `${coverageLabel} · 词元不可测` : '词元不可测'
  } else if (effectiveLowerBound) {
    detailLabel = coverageLabel ? `${coverageLabel} · 已测值下界` : '已测值下界'
  }

  return {
    value: normalizedValue,
    label: normalizedValue == null
      ? TOKEN_UNMEASURED_LABEL
      : `${effectiveLowerBound ? '≥ ' : ''}${formatNumber(normalizedValue, 0)}`,
    available: normalizedValue != null,
    measuredCalls: normalizedMeasuredCalls,
    totalCalls: normalizedTotalCalls,
    hasKnownCoverage,
    hasPartialCoverage,
    isLowerBound: effectiveLowerBound,
    coverageLabel,
    detailLabel,
  }
}
