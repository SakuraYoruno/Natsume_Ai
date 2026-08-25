import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import InlineQueryMeta from '../../../../shared/ui/observability/InlineQueryMeta.jsx'
import { resolveLoadState } from '../../../../models/loadState.js'
import { formatDateTime } from '../../../../shared/formatters.js'

const INSIGHT_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export function formatTime(tsMs) {
  return formatDateTime(tsMs, {
    options: INSIGHT_TIME_OPTIONS,
  })
}

export const StatCard = ({ label, value, sub, color = 'text-gray-800' }) => (
  <MetricCard label={label} value={value} hint={sub ?? ''} tone={color} variant="stacked" />
)

export function hasInsightSamples(data, sampleKeys = []) {
  if (!data) return false
  if (sampleKeys.length === 0) return true

  return sampleKeys.some((key) => {
    const value = data?.[key]
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'number') return value > 0
    return value != null
  })
}

export function resolveInsightSectionLoadState(queryState, sampleKeys = []) {
  const data = queryState?.statsData ?? null
  return resolveLoadState({
    loading: queryState?.loading || queryState?.refreshing,
    error: queryState?.queryError,
    hasData: Boolean(data),
    hasSamples: hasInsightSamples(data, sampleKeys),
    available: true,
    stale: queryState?.stale,
  })
}

export function SectionQueryMeta({ queryState, windowMinutes, sampleKeys = [] }) {
  return (
    <InlineQueryMeta
      loadState={resolveInsightSectionLoadState(queryState, sampleKeys)}
      loadStateDetail={queryState?.queryError}
      updatedAt={queryState?.lastUpdatedAt}
      windowMinutes={windowMinutes}
    />
  )
}
