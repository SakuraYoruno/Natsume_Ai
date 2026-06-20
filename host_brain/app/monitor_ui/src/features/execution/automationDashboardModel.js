import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildEndocrineOrganSystemModel } from '../../models/organSystemModel.js'
import { buildResourceSchedulingSummary } from './automationResourceModel.js'
import {
  buildAutomationHeroMetaItems,
  buildAutomationSchedulerPulseNodes,
  getAutomationSchedulerState,
  getAutomationTaskCounts,
} from './automationSchedulerModel.js'

export function buildAutomationLoadState({
  tasks = [],
  health = null,
  overviewError = null,
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  const taskItems = Array.isArray(tasks) ? tasks : []
  const hasData = Boolean(taskItems.length > 0 || health)
  return resolveLoadState({
    loading: loading || refreshing,
    error: overviewError && !taskItems.length && !health ? new Error(overviewError) : null,
    hasData,
    hasSamples: taskItems.length > 0,
    available: hasData,
    stale,
  })
}

export function buildAutomationSampleMeta({
  automationLoadState,
  tasks = [],
  lastUpdatedAt = null,
} = {}) {
  return {
    loadState: automationLoadState,
    sampleSize: Array.isArray(tasks) ? tasks.length : 0,
    sampleLabel: '节律任务',
    updatedAt: lastUpdatedAt,
    source: 'scheduler / automation',
    definition: 'Automation 页汇总节律任务总览、immune gate 运行态与 24h 历史脉络，页面主数据已经统一走 query 层。',
  }
}

export function buildAutomationNoticeView({
  overviewError = null,
  runtimeError = null,
  tasks = [],
  health = null,
  loading = false,
  refreshing = false,
  automationLoadState,
} = {}) {
  const hasOverviewData = Boolean((Array.isArray(tasks) && tasks.length > 0) || health)
  return {
    overviewError,
    overviewVariant: hasOverviewData ? 'warning' : 'error',
    runtimeErrorMessage: runtimeError?.message || (runtimeError ? String(runtimeError) : null),
    noSample: !overviewError && !loading && !refreshing && automationLoadState === LOAD_STATES.NO_SAMPLE,
  }
}

export function buildAutomationOrganModel({
  automationLoadState,
  lastUpdatedAt = null,
  schedulerState,
  taskCounts = {},
  hasAnyTaskFilter = false,
  runtimeSnapshot = null,
} = {}) {
  return buildEndocrineOrganSystemModel({
    loadState: automationLoadState,
    updatedAt: lastUpdatedAt,
    schedulerState,
    taskCount: taskCounts.totalCount,
    enabledCount: taskCounts.enabledCount,
    runningCount: taskCounts.runningCount,
    failedCount: taskCounts.failedCount,
    filteredCount: taskCounts.filteredCount,
    hasTaskFilters: hasAnyTaskFilter,
    backgroundRuntime: runtimeSnapshot?.background_runtime,
  })
}

export function buildAutomationDashboardViewModel({
  tasks = [],
  filteredTasks = [],
  health = null,
  resourceManager = null,
  overviewError = null,
  runtimeError = null,
  runtimeSnapshot = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  hasAnyTaskFilter = false,
} = {}) {
  const automationLoadState = buildAutomationLoadState({
    tasks,
    health,
    overviewError,
    loading,
    refreshing,
    stale,
  })
  const taskCounts = getAutomationTaskCounts(tasks, filteredTasks)
  const resourceSummary = buildResourceSchedulingSummary(tasks, resourceManager)
  const schedulerState = getAutomationSchedulerState(health)

  return {
    automationLoadState,
    taskCounts,
    resourceSummary,
    schedulerState,
    schedulerPulseNodes: buildAutomationSchedulerPulseNodes({
      schedulerState,
      taskCounts,
      resourceSummary,
      hasAnyTaskFilter,
    }),
    heroMetaItems: buildAutomationHeroMetaItems(taskCounts, resourceSummary),
    sampleMeta: buildAutomationSampleMeta({
      automationLoadState,
      tasks,
      lastUpdatedAt,
    }),
    noticeView: buildAutomationNoticeView({
      overviewError,
      runtimeError,
      tasks,
      health,
      loading,
      refreshing,
      automationLoadState,
    }),
    endocrineOrganModel: buildAutomationOrganModel({
      automationLoadState,
      lastUpdatedAt,
      schedulerState,
      taskCounts,
      hasAnyTaskFilter,
      runtimeSnapshot,
    }),
  }
}
