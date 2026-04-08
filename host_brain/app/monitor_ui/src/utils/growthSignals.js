import { asArray, compactText, formatDateTime } from './digitalLife.js'

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function asText(value) {
  return String(value ?? '').trim()
}

function asInt(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : null
}

export function formatGrowthMoment(value) {
  if (value == null || value === '') return '—'
  if (typeof value === 'number') return formatDateTime(value)

  const text = asText(value)
  if (!text) return '—'
  if (/^\d{10,13}$/.test(text)) return formatDateTime(Number(text))
  return text
}

export function getGrowthSignals(runtimeSnapshot) {
  const snapshot = asObject(runtimeSnapshot)
  const selfModel = asObject(snapshot.self_model_snapshot)
  const selfReflection = asObject(snapshot.self_reflection_pulse)
  const growthAwareness = asObject(selfReflection.growth_awareness)
  const growthSummary = asObject(selfReflection.growth_summary)

  const emergentGrowthNote = asText(selfReflection.emergent_growth_note || snapshot.emergent_growth_note)
  const growthNote = asText(selfReflection.growth_note)
  const selfAwareness = asText(selfReflection.self_awareness)
  const weeklySummary = asText(growthSummary.weekly_summary)
  const dailySummary = asText(growthSummary.daily_summary)
  const latestGrowthNote = asText(growthSummary.latest_growth_note)
  const lastPatternSummary = asText(growthSummary.last_pattern_summary)

  const awarenessItems = [
    { key: 'recent_change', label: '最近变化', value: asText(growthAwareness.recent_change) },
    { key: 'trait_felt', label: '性格体感', value: asText(growthAwareness.trait_felt) },
    { key: 'milestone_felt', label: '里程碑体感', value: asText(growthAwareness.milestone_felt) },
    { key: 'desire', label: '内在愿望', value: asText(growthAwareness.desire) },
  ].filter((item) => item.value)

  const activeThemes = asArray(growthSummary.active_themes)
    .map(asText)
    .filter(Boolean)
  const milestones = asArray(selfModel.growth_milestones)
    .map(asText)
    .filter(Boolean)

  const cycleCount = asInt(growthSummary.recent_cycle_count)
    ?? asInt(growthSummary.total_growth_cycles)
    ?? 0
  const lastCycleLabel = formatGrowthMoment(growthSummary.last_cycle_date || growthSummary.last_growth_ts)

  const primarySummary = emergentGrowthNote
    || weeklySummary
    || dailySummary
    || latestGrowthNote
    || growthNote
    || selfAwareness

  const insightSections = [
    { key: 'emergent', label: '涌现成长感知', value: emergentGrowthNote },
    { key: 'weekly', label: '周成长摘要', value: weeklySummary },
    { key: 'daily', label: '日成长摘要', value: dailySummary },
    { key: 'pattern', label: '最近模式总结', value: lastPatternSummary },
  ].filter((item) => item.value)

  const hasAnyGrowth = Boolean(
    primarySummary
    || awarenessItems.length > 0
    || activeThemes.length > 0
    || milestones.length > 0
    || cycleCount > 0
    || lastPatternSummary
  )

  return {
    selfModel,
    selfReflection,
    growthAwareness,
    growthSummary,
    awarenessItems,
    activeThemes,
    milestones,
    cycleCount,
    lastCycleLabel,
    emergentGrowthNote,
    latestGrowthNote,
    lastPatternSummary,
    primarySummary,
    insightSections,
    hasAnyGrowth,
    overviewSummary: compactText(
      primarySummary || lastPatternSummary || milestones[0] || '',
      96
    ),
  }
}
