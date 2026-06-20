const AUTOMATION_SCHEDULER_RUNNING_STATUSES = new Set(['HEALTHY', 'RUNNING', 'UP', 'OK'])
const AUTOMATION_SCHEDULER_STOPPED_STATUSES = new Set(['STOPPED', 'DOWN', 'DISABLED', 'OFFLINE'])

export const AUTOMATION_ACTION_LABELS = Object.freeze({
  enable: '启用',
  disable: '暂停',
  trigger: '立即触发',
})

export function getAutomationActionLabel(action) {
  return AUTOMATION_ACTION_LABELS[action] || action
}

export function getAutomationSchedulerState(health) {
  const rawStatus = String(health?.scheduler_status || '').toUpperCase()
  if (typeof health?.running === 'boolean') {
    if (!health.running) {
      return { running: false, label: '已停止', title: '节律当前未运行' }
    }
    if (rawStatus === 'DEGRADED') {
      return { running: true, label: '降级运行', title: '节律降级运行中' }
    }
    return { running: true, label: '运行中', title: '节律正在运行' }
  }
  if (rawStatus === 'DEGRADED') {
    return { running: true, label: '降级运行', title: '节律降级运行中' }
  }
  if (AUTOMATION_SCHEDULER_RUNNING_STATUSES.has(rawStatus)) {
    return { running: true, label: '运行中', title: '节律正在运行' }
  }
  if (AUTOMATION_SCHEDULER_STOPPED_STATUSES.has(rawStatus)) {
    return { running: false, label: '已停止', title: '节律当前未运行' }
  }
  return { running: false, label: '未知', title: '节律状态未知' }
}

export function getAutomationTaskCounts(tasks, filteredTasks = []) {
  const taskItems = Array.isArray(tasks) ? tasks : []
  return {
    totalCount: taskItems.length,
    enabledCount: taskItems.filter((item) => item.task?.enabled).length,
    runningCount: taskItems.filter((item) => item.health?.status === 'RUNNING').length,
    failedCount: taskItems.filter((item) => item.health?.status === 'FAILED').length,
    filteredCount: Array.isArray(filteredTasks) ? filteredTasks.length : 0,
  }
}

export function buildAutomationSchedulerPulseNodes({
  schedulerState,
  taskCounts,
  resourceSummary,
  hasAnyTaskFilter,
}) {
  return [
    {
      label: '节律态',
      value: schedulerState?.label || '未知',
      tone: schedulerState?.running ? 'primary' : 'gray',
      active: Boolean(schedulerState?.running),
    },
    {
      label: '任务',
      value: String(taskCounts?.totalCount ?? 0),
      tone: 'teal',
      active: Number(taskCounts?.totalCount || 0) > 0,
    },
    {
      label: '启用',
      value: String(taskCounts?.enabledCount ?? 0),
      tone: 'violet',
      active: Number(taskCounts?.enabledCount || 0) > 0,
    },
    {
      label: '推进中',
      value: String(taskCounts?.runningCount ?? 0),
      tone: 'amber',
      active: Number(taskCounts?.runningCount || 0) > 0,
    },
    {
      label: '异常',
      value: String(taskCounts?.failedCount ?? 0),
      tone: 'rose',
      active: Number(taskCounts?.failedCount || 0) > 0,
    },
    {
      label: '资源压力',
      value: String(resourceSummary?.pressure?.status || resourceSummary?.status || '未知'),
      tone: resourceSummary?.deferred > 0 ? 'amber' : 'teal',
      active: Boolean(resourceSummary?.hasManager),
    },
    {
      label: '已筛选',
      value: String(taskCounts?.filteredCount ?? 0),
      tone: 'gray',
      active: Boolean(hasAnyTaskFilter),
    },
  ]
}

export function buildAutomationHeroMetaItems(taskCounts, resourceSummary) {
  return [
    `节律任务总数：${taskCounts?.totalCount ?? 0}`,
    `已启用：${taskCounts?.enabledCount ?? 0}`,
    `推进中：${taskCounts?.runningCount ?? 0}`,
    `异常任务：${taskCounts?.failedCount ?? 0}`,
    `资源压力：${resourceSummary?.pressure?.status || resourceSummary?.status || '未知'}`,
  ]
}
