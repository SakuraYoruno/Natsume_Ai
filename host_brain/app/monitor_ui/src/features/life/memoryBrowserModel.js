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
    residentDocCount: worldDocs.length + personaDocs.length,
  }
}

export function countMemorySubsystemOperations(subsystems) {
  const rows = Array.isArray(subsystems) ? subsystems : []
  return rows.reduce((sum, item) => sum + Number(item?.total || 0), 0)
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
    || (episodicData?.total_writes ?? 0) > 0
    || (distillData?.total_cycles ?? 0) > 0
    || (compressData?.total_compressions ?? 0) > 0
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
  if (!hasSubsystemSamples) return '当前窗口还没有足够的记忆流量样本。'
  return `近窗口平均成功率 ${avgSuccessRate}% · ${sampledSubsystemCount}/${configuredSubsystemCount} 个子系统有样本 · 常驻设定 ${residentDocCount} 份`
}

export function buildMemoryHeroMetaItems({ windowMinutes, residentDocCount, episodicWrites } = {}) {
  return [
    `窗口：${windowMinutes} 分钟`,
    `常驻设定：${residentDocCount} 份`,
    `情景写入：${episodicWrites ?? 0}`,
  ]
}

export function buildMemorySubsystemMetricCards(subsystems) {
  const rows = Array.isArray(subsystems) ? subsystems : []
  return rows.map((subsystem) => ({
    key: subsystem?.subsystem,
    label: subsystem?.display_name,
    value: subsystem?.success_rate != null ? `${subsystem.success_rate}%` : '—',
    hint: `${subsystem?.total ?? 0} 次操作`,
    tone: getSuccessRateTone(subsystem?.success_rate),
  }))
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
  const memoryLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
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
    distillCycles: distillData?.total_cycles,
    episodicWrites: episodicData?.total_writes,
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
      episodicWrites: episodicData?.total_writes,
    }),
    showUnavailableNotice: !error && !loading && !refreshing && memoryLoadState === LOAD_STATES.UNAVAILABLE,
    showNoSampleNotice: !error && !loading && !refreshing && memoryLoadState === LOAD_STATES.NO_SAMPLE,
  }
}
