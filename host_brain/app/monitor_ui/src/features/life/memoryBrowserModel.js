import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildMemoryOrganSystemModel } from '../../models/organSystemModel.js'
import { getSuccessRateTone, summarizeSuccessRateSamples } from '../../utils/successRate.js'

export const MEMORY_BROWSER_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export function getMemorySubsystems(memoryData) {
  return Array.isArray(memoryData?.subsystems) ? memoryData.subsystems : []
}

export function buildMemoryDocumentView({ canonDocs, residentContext } = {}) {
  const docs = Array.isArray(canonDocs?.documents)
    ? canonDocs.documents
    : Array.isArray(canonDocs?.items)
      ? canonDocs.items
      : []
  const worldDocs = Array.isArray(residentContext?.world_canon_documents)
    ? residentContext.world_canon_documents
    : docs
  const personaDocs = Array.isArray(residentContext?.persona_resident_documents)
    ? residentContext.persona_resident_documents
    : []
  return {
    docs,
    worldDocs,
    personaDocs,
    residentDocCount: residentContext != null
      ? worldDocs.length + personaDocs.length
      : null,
  }
}

export function countMemorySubsystemOperations(subsystems) {
  const rows = Array.isArray(subsystems) ? subsystems : []
  return rows.reduce((sum, item) => sum + Number(
    item?.total
    ?? item?.extra?.observed_pre_save_hooks
    ?? item?.extra?.observed_terminal_events
    ?? 0,
  ), 0)
}

export function hasMemoryObservabilityData({
  memoryData,
  episodicData,
  distillData,
  canonDocs,
  compressData,
  residentContext,
} = {}) {
  return Boolean(memoryData || episodicData || distillData || canonDocs || compressData || residentContext)
}

export function hasMemoryTrafficSamples({
  sampledSubsystemCount = 0,
  worldDocs = [],
  personaDocs = [],
  episodicData,
  distillData,
  compressData,
} = {}) {
  return (
    sampledSubsystemCount > 0
    || worldDocs.length > 0
    || personaDocs.length > 0
    || (episodicData?.total_attempts ?? 0) > 0
    || (distillData?.observed_terminal_events ?? 0) > 0
    || (compressData?.observed_pre_save_hooks ?? 0) > 0
  )
}

export function buildMemoryHeroTitle(configuredSubsystemCount) {
  return configuredSubsystemCount > 0 ? `${configuredSubsystemCount} 个记忆子系统` : '记忆链路待采样'
}

export function buildMemoryHeroDescription({
  hasSubsystemSamples,
  avgSuccessRate,
  sampledSubsystemCount,
  configuredSubsystemCount,
  residentDocCount,
} = {}) {
  if (!hasSubsystemSamples) return '当前窗口尚无可计算成功率的写入 / Guard 样本；压缩钩子与蒸馏审计按独立口径展示。'
  return `近窗口平均成功率 ${avgSuccessRate}% · ${sampledSubsystemCount}/${configuredSubsystemCount} 个子系统有样本 · 常驻设定 ${residentDocCount == null ? '不可测' : `${residentDocCount} 份`}`
}

export function buildMemoryHeroMetaItems({ windowMinutes, residentDocCount, episodicWrites } = {}) {
  return [
    `窗口：${windowMinutes} 分钟`,
    `常驻设定：${residentDocCount == null ? '不可测' : `${residentDocCount} 份`}`,
    `情景写入：${episodicWrites == null ? '不可测' : episodicWrites}`,
  ]
}

export function buildMemorySubsystemMetricCards(subsystems) {
  const rows = Array.isArray(subsystems) ? subsystems : []
  return rows.map((subsystem) => {
    const source = subsystem?.extra?.source_table || '来源未标注'
    const observedHooks = subsystem?.extra?.observed_pre_save_hooks
    const observedTerminals = subsystem?.extra?.observed_terminal_events
    const observation = subsystem?.total != null
      ? `${subsystem.total} 次已观测事件`
      : observedHooks != null
        ? `${observedHooks} 次预存钩子（结果不可测）`
        : observedTerminals != null
          ? `${observedTerminals} 个终态审计事件（非周期数）`
          : '事件数不可测'
    return {
      key: subsystem?.subsystem,
      label: subsystem?.display_name,
      value: subsystem?.success_rate != null ? `${subsystem.success_rate}%` : '不可测',
      hint: `${observation} · ${source}`,
      tone: getSuccessRateTone(subsystem?.success_rate),
    }
  })
}

export function buildMemoryBrowserViewModel({
  memoryData,
  episodicData,
  distillData,
  canonDocs,
  compressData,
  residentContext,
  loading = false,
  refreshing = false,
  error = null,
  lastUpdatedAt = null,
  windowMinutes = 360,
  resourceIssues = [],
} = {}) {
  const subsystems = getMemorySubsystems(memoryData)
  const documentView = buildMemoryDocumentView({ canonDocs, residentContext })
  const {
    averageRate: avgSuccessRate,
    sampledCount: sampledSubsystemCount,
    configuredCount: configuredSubsystemCount,
  } = summarizeSuccessRateSamples(subsystems)
  const hasSubsystemSamples = avgSuccessRate != null
  const hasData = hasMemoryObservabilityData({
    memoryData,
    episodicData,
    distillData,
    canonDocs,
    compressData,
    residentContext,
  })
  const hasTraffic = hasMemoryTrafficSamples({
    sampledSubsystemCount,
    worldDocs: documentView.worldDocs,
    personaDocs: documentView.personaDocs,
    episodicData,
    distillData,
    compressData,
  })
  const resourceLoadError = !hasData
    ? resourceIssues.find((issue) => issue?.availability === 'error') || null
    : null
  const memoryLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: error || resourceLoadError,
    hasData,
    hasSamples: hasTraffic,
    available: hasData,
  })
  const memoryOrganModel = buildMemoryOrganSystemModel({
    loadState: memoryLoadState,
    updatedAt: lastUpdatedAt,
    hasData,
    hasSamples: hasTraffic,
    averageSuccessRate: avgSuccessRate,
    sampledSubsystemCount,
    configuredSubsystemCount,
    residentDocCount: documentView.residentDocCount,
    observedDistillTerminalEvents: distillData?.observed_terminal_events,
    observedCompressionHooks: compressData?.observed_pre_save_hooks,
    episodicWrites: episodicData?.written_count,
  })

  return {
    ...documentView,
    subsystems,
    subsystemOperationCount: countMemorySubsystemOperations(subsystems),
    avgSuccessRate,
    sampledSubsystemCount,
    configuredSubsystemCount,
    hasSubsystemSamples,
    hasMemoryObservabilityData: hasData,
    hasMemoryTraffic: hasTraffic,
    memoryLoadState,
    memoryOrganModel,
    subsystemMetricCards: buildMemorySubsystemMetricCards(subsystems),
    heroTitle: buildMemoryHeroTitle(configuredSubsystemCount),
    heroDescription: buildMemoryHeroDescription({
      hasSubsystemSamples,
      avgSuccessRate,
      sampledSubsystemCount,
      configuredSubsystemCount,
      residentDocCount: documentView.residentDocCount,
    }),
    heroMetaItems: buildMemoryHeroMetaItems({
      windowMinutes,
      residentDocCount: documentView.residentDocCount,
      episodicWrites: episodicData?.written_count,
    }),
    showUnavailableNotice: !error && !loading && !refreshing && memoryLoadState === LOAD_STATES.UNAVAILABLE,
    showNoSampleNotice: !error && !loading && !refreshing && memoryLoadState === LOAD_STATES.NO_SAMPLE,
  }
}
