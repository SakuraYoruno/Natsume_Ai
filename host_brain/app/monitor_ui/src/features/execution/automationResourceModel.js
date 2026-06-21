export const AUTOMATION_PRIORITY_STYLES = Object.freeze({
  HIGH: 'text-red-600 bg-red-50',
  MEDIUM: 'text-blue-600 bg-blue-50',
  LOW: 'text-gray-500 bg-gray-50',
})

export const AUTOMATION_RESOURCE_CLASS_META = Object.freeze({
  light_tick: Object.freeze({
    label: '轻量生命 tick',
    shortLabel: '轻 tick',
    description: '低成本内在脉冲，高压时仍优先保留。',
    chipClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    barClass: 'bg-emerald-500',
  }),
  heartbeat: Object.freeze({
    label: '心跳巡检',
    shortLabel: '心跳',
    description: '状态采样与基础保活，资源占用很低。',
    chipClass: 'border-teal-200 bg-teal-50 text-teal-700',
    barClass: 'bg-teal-500',
  }),
  heartbeat_llm: Object.freeze({
    label: 'LLM 心跳',
    shortLabel: 'LLM 心跳',
    description: '带模型探针的健康检查，压力升高时可延后。',
    chipClass: 'border-sky-200 bg-sky-50 text-sky-700',
    barClass: 'bg-sky-500',
  }),
  background_llm: Object.freeze({
    label: '后台 LLM',
    shortLabel: '后台 LLM',
    description: '异步模型任务，不阻塞主链，压力升高时会让路。',
    chipClass: 'border-blue-200 bg-blue-50 text-blue-700',
    barClass: 'bg-blue-500',
  }),
  memory_heavy: Object.freeze({
    label: '记忆重任务',
    shortLabel: '记忆重',
    description: '蒸馏、索引或长记忆处理，内存高时优先推迟。',
    chipClass: 'border-violet-200 bg-violet-50 text-violet-700',
    barClass: 'bg-violet-500',
  }),
  browser_heavy: Object.freeze({
    label: '浏览器重任务',
    shortLabel: '浏览器',
    description: '网页自动化和采集任务，内存紧张时延后重试。',
    chipClass: 'border-amber-200 bg-amber-50 text-amber-700',
    barClass: 'bg-amber-500',
  }),
  gpu_heavy: Object.freeze({
    label: 'GPU/视觉重任务',
    shortLabel: 'GPU',
    description: '游戏陪伴、视觉或显存相关任务，显存高时会暂停等待。',
    chipClass: 'border-rose-200 bg-rose-50 text-rose-700',
    barClass: 'bg-rose-500',
  }),
  maintenance: Object.freeze({
    label: '维护任务',
    shortLabel: '维护',
    description: '清理、审计和低频维护，通常可错峰运行。',
    chipClass: 'border-slate-200 bg-slate-50 text-slate-600',
    barClass: 'bg-slate-400',
  }),
  default: Object.freeze({
    label: '未声明画像',
    shortLabel: '未声明',
    description: '尚未显式标注资源画像，会按默认后台任务处理。',
    chipClass: 'border-gray-200 bg-gray-50 text-gray-600',
    barClass: 'bg-gray-400',
  }),
})

export const AUTOMATION_RESOURCE_PRESSURE_TONES = Object.freeze({
  HIGH: 'text-red-600',
  ELEVATED: 'text-amber-600',
  NORMAL: 'text-emerald-600',
  default: 'text-gray-600',
})

export const AUTOMATION_RESOURCE_GATE_DECISION_CLASSES = Object.freeze({
  allow: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  defer: 'border-amber-200 bg-amber-50 text-amber-700',
  default: 'border-gray-200 bg-gray-50 text-gray-600',
})

export const AUTOMATION_RESOURCE_MANAGER_RUNTIME_CLASSES = Object.freeze({
  running: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  snapshot: 'border-amber-200 bg-amber-50 text-amber-700',
})

export function getAutomationResourceMeta(resourceClass) {
  return AUTOMATION_RESOURCE_CLASS_META[String(resourceClass || '').trim()] || AUTOMATION_RESOURCE_CLASS_META.default
}

export function resolveResourcePressureTone(status) {
  return AUTOMATION_RESOURCE_PRESSURE_TONES[String(status || '').trim().toUpperCase()]
    || AUTOMATION_RESOURCE_PRESSURE_TONES.default
}

export function resolveResourceGateDecisionClass(decision) {
  return AUTOMATION_RESOURCE_GATE_DECISION_CLASSES[String(decision || '').trim().toLowerCase()]
    || AUTOMATION_RESOURCE_GATE_DECISION_CLASSES.default
}

export function getTaskResourceProfile(item) {
  const task = item?.task || {}
  const profile = task.resource_profile && typeof task.resource_profile === 'object' ? task.resource_profile : {}
  const resourceClass = String(profile.resource_class || profile.task_type || task.resource_class || '').trim() || 'default'
  const meta = getAutomationResourceMeta(resourceClass)
  return {
    ...profile,
    resource_class: resourceClass,
    uses_llm: Boolean(profile.uses_llm),
    uses_gpu: Boolean(profile.uses_gpu),
    meta,
  }
}

export function buildResourceSchedulingSummary(tasks, resourceManager) {
  const byClass = new Map()
  for (const item of tasks) {
    const profile = getTaskResourceProfile(item)
    const key = profile.resource_class || 'default'
    const current = byClass.get(key) || {
      key,
      label: profile.meta.label,
      shortLabel: profile.meta.shortLabel,
      description: profile.meta.description,
      chipClass: profile.meta.chipClass,
      barClass: profile.meta.barClass,
      count: 0,
      enabled: 0,
      llm: 0,
      gpu: 0,
    }
    current.count += 1
    if (item?.task?.enabled) current.enabled += 1
    if (profile.uses_llm) current.llm += 1
    if (profile.uses_gpu) current.gpu += 1
    byClass.set(key, current)
  }

  const recentGateDecisions = Array.isArray(resourceManager?.last_gate_decisions)
    ? resourceManager.last_gate_decisions.slice(0, 8)
    : []
  const pressureFromSnapshot = resourceManager?.last_snapshot?.pressure && typeof resourceManager.last_snapshot.pressure === 'object'
    ? resourceManager.last_snapshot.pressure
    : null
  const pressureFromGate = recentGateDecisions.find((row) => row?.pressure)?.pressure
  const pressure = pressureFromSnapshot || pressureFromGate || {}
  const allowed = recentGateDecisions.filter((row) => String(row?.decision || '').toLowerCase() === 'allow').length
  const deferred = recentGateDecisions.filter((row) => String(row?.decision || '').toLowerCase() === 'defer').length
  const degraded = recentGateDecisions.filter((row) => !['allow', 'defer'].includes(String(row?.decision || '').toLowerCase())).length

  return {
    groups: Array.from(byClass.values()).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'zh-CN')),
    recentGateDecisions,
    pressure,
    allowed,
    deferred,
    degraded,
    hasManager: Boolean(resourceManager) && String(resourceManager?.status || '').toUpperCase() !== 'UNAVAILABLE',
    status: String(resourceManager?.status || 'UNKNOWN').toUpperCase(),
    source: String(resourceManager?.source || 'unknown'),
    running: Boolean(resourceManager?.running),
  }
}

export function getResourceGroupBarWidth(group, groups = []) {
  const maxCount = Math.max(1, Array.isArray(groups) ? groups[0]?.count || 1 : 1)
  return `${Math.max(8, Math.min(100, (Number(group?.count || 0) / maxCount) * 100))}%`
}

export function buildResourceManagerDecisionView(resourceManager) {
  const decision = resourceManager?.last_decision && typeof resourceManager.last_decision === 'object'
    ? resourceManager.last_decision
    : {}

  return {
    decision,
    cannotRelease: Array.isArray(decision.cannot_release) ? decision.cannot_release.slice(0, 4) : [],
    canRelease: Array.isArray(decision.can_release) ? decision.can_release.slice(0, 4) : [],
    needsConfirm: Array.isArray(decision.needs_user_confirm) ? decision.needs_user_confirm.slice(0, 4) : [],
  }
}

export function buildResourceGateDecisionRowView(row) {
  const decisionText = String(row?.decision || 'unknown').toLowerCase()
  return {
    row,
    meta: getAutomationResourceMeta(row?.resource_class || row?.task_type),
    decisionText,
    decisionClass: resolveResourceGateDecisionClass(decisionText),
    pressureText: row?.pressure_status || row?.pressure?.status || '—',
  }
}

export function buildResourceSchedulingView(summary, resourceManager) {
  const pressureStatus = String(summary?.pressure?.status || resourceManager?.last_decision?.pressure_level || 'UNKNOWN').toUpperCase()
  return {
    pressureStatus,
    pressureTone: resolveResourcePressureTone(pressureStatus),
    gateMetricClass: summary?.deferred > 0 ? 'text-amber-600' : 'text-emerald-600',
    managerRuntime: {
      label: summary?.running ? '运行中' : '状态文件',
      className: summary?.running
        ? AUTOMATION_RESOURCE_MANAGER_RUNTIME_CLASSES.running
        : AUTOMATION_RESOURCE_MANAGER_RUNTIME_CLASSES.snapshot,
    },
    managerDecision: buildResourceManagerDecisionView(resourceManager),
  }
}
