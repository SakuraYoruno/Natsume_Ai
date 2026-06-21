import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildRuntimeModeOrganSystemModel } from '../../models/organSystemModel.js'
import { DIGITAL_LIFE_MODE_BUTTON_CLASSES } from '../../shared/statusMeta.js'
import { MODE_META } from '../../utils/digitalLife.js'

export const RUNTIME_MODE_ORDER = ['normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked']

export const RUNTIME_MODE_AUTO_RULE_DEFS = Object.freeze({
  gaming_detect: {
    label: '检测游戏进程',
    desc: '识别到游戏场景后自动切换到游戏态。',
  },
  gaming_exit: {
    label: '游戏退出回收',
    desc: '游戏结束后回到正常态，恢复日常陪伴逻辑。',
  },
  idle_to_sleep: {
    label: '空闲转睡眠',
    desc: '长时间空闲并进入夜间窗口后自动转入睡眠态。',
  },
  wakeup_on_msg: {
    label: '消息唤醒',
    desc: '在睡眠态收到消息时自动唤醒。',
  },
  morning_wakeup: {
    label: '晨间唤醒',
    desc: '晨间首次交互时从睡眠态切回在线态。',
  },
})

export const RUNTIME_MODE_STATE_AXIS_ITEMS = [
  {
    title: '生命体存在态轴',
    desc: '决定当前处于什么硬状态，是数字生命体最外层的状态开关。',
  },
  {
    title: '离线时长',
    desc: 'absence_band 与 reconnect_style 影响重连氛围，不直接等于 sys_mode。',
  },
  {
    title: '回复姿态',
    desc: 'reply_posture 决定这轮会更温柔、克制、平衡还是收敛。',
  },
  {
    title: '内在驱动',
    desc: '动机、反思与风险护栏会继续塑造为什么这样回应。',
  },
]

export function formatRuntimeModeSourceText(source = '') {
  if (source === 'manual') return '手动'
  if (source === 'auto_gaming') return '游戏自动'
  if (source === 'auto_idle') return '睡眠自动'
  if (source === 'auto_wakeup') return '唤醒自动'
  if (source === 'skill') return '技能'
  if (source.startsWith('auto')) return '自动'
  return source || 'unknown'
}

export function getRuntimeModeMeta(mode) {
  return MODE_META[mode] || MODE_META.normal
}

export function getRuntimeModeSelection(status) {
  const currentMode = String(status?.sys_mode || 'normal').toLowerCase()
  return {
    currentMode,
    currentMeta: getRuntimeModeMeta(currentMode),
    validTransitions: Array.isArray(status?.valid_transitions) ? status.valid_transitions : [],
    autoRules: status?.auto_rules || {},
  }
}

export function getRuntimeModeEnabledRuleCount(autoRules = {}) {
  return Object.entries(RUNTIME_MODE_AUTO_RULE_DEFS).filter(([key]) => autoRules[key] !== false).length
}

export function buildRuntimeModeLoadState({ status, history = [], loading = false, refreshing = false, consoleError = null, stale = false } = {}) {
  const rows = Array.isArray(history) ? history : []
  return resolveLoadState({
    loading: loading || refreshing,
    error: consoleError && !status ? new Error(consoleError) : null,
    hasData: Boolean(status || rows.length > 0),
    hasSamples: Boolean(status || rows.length > 0),
    available: Boolean(status || rows.length > 0),
    stale,
  })
}

export function buildRuntimeModeHeroMetaItems({ status, validTransitions = [], enabledRuleCount = 0 } = {}) {
  const totalRuleCount = Object.keys(RUNTIME_MODE_AUTO_RULE_DEFS).length
  return [
    `空闲：${status?.idle_minutes ?? 0} 分钟`,
    `可切换：${validTransitions.length} 项`,
    `自动规则：${enabledRuleCount}/${totalRuleCount}`,
    status?.is_night_time ? '夜间窗口' : '日间窗口',
  ]
}

export function buildRuntimeModeMetricCards({ status, currentMeta, enabledRuleCount } = {}) {
  const totalRuleCount = Object.keys(RUNTIME_MODE_AUTO_RULE_DEFS).length
  return [
    {
      key: 'current_mode',
      label: '当前模式',
      value: currentMeta?.label,
      hint: currentMeta?.scheduler,
      tone: 'text-primary',
    },
    {
      key: 'idle_minutes',
      label: '空闲时间',
      value: `${status?.idle_minutes ?? 0} 分钟`,
      hint: '用于辅助自动切换',
      tone: 'text-blue-600',
    },
    {
      key: 'time_window',
      label: '时间窗口',
      value: status?.is_night_time ? '夜间' : '日间',
      hint: status?.is_night_time ? '适合睡眠态' : '适合在线态',
      tone: 'text-indigo-600',
    },
    {
      key: 'auto_rules',
      label: '自动规则',
      value: `${enabledRuleCount}/${totalRuleCount}`,
      hint: status?.degraded ? '当前生命体存在态轴降级' : '生命体存在态轴正常',
      tone: status?.degraded ? 'text-orange-500' : 'text-green-600',
    },
  ]
}

export function buildRuntimeModeButtonViews({ currentMode, validTransitions = [] } = {}) {
  return RUNTIME_MODE_ORDER.map((mode) => {
    const meta = getRuntimeModeMeta(mode)
    const isActive = mode === currentMode
    const canSwitch = validTransitions.includes(mode) && !isActive
    const isDisabled = !canSwitch && !isActive
    const colors = DIGITAL_LIFE_MODE_BUTTON_CLASSES[mode] || DIGITAL_LIFE_MODE_BUTTON_CLASSES.fallback
    return {
      mode,
      meta,
      isActive,
      canSwitch,
      isDisabled,
      colors,
      title: isActive ? `当前模式：${meta.label}` : canSwitch ? `切换到 ${meta.label}` : `当前无法直接切换到 ${meta.label}`,
    }
  })
}

export function buildRuntimeModeAutoRuleViews(autoRules = {}) {
  return Object.entries(RUNTIME_MODE_AUTO_RULE_DEFS).map(([key, rule]) => ({
    key,
    ...rule,
    enabled: autoRules[key] !== false,
  }))
}

export function buildRuntimeModeSwitchConfirmMessage(targetMode) {
  const meta = MODE_META[targetMode] || { label: targetMode, semantic: '' }
  return `确认切换到 ${meta.label}？\n\n${meta.semantic || meta.desc || ''}`
}

export function buildRuntimeModeSwitchPayload(targetMode) {
  return {
    target_mode: targetMode,
    reason: 'WebUI 手动切换',
    source: 'manual',
  }
}

export function buildRuntimeModeAutoRulePatch(ruleKey, currentValue) {
  return { rules: { [ruleKey]: !currentValue } }
}

export function buildRuntimeModeViewModel({
  status,
  history = [],
  loading = false,
  refreshing = false,
  consoleError = null,
  stale = false,
  lastUpdatedAt = null,
} = {}) {
  const {
    currentMode,
    currentMeta,
    validTransitions,
    autoRules,
  } = getRuntimeModeSelection(status)
  const enabledRuleCount = getRuntimeModeEnabledRuleCount(autoRules)
  const totalRuleCount = Object.keys(RUNTIME_MODE_AUTO_RULE_DEFS).length
  const runtimeModeLoadState = buildRuntimeModeLoadState({ status, history, loading, refreshing, consoleError, stale })
  const endocrineOrganModel = buildRuntimeModeOrganSystemModel({
    loadState: runtimeModeLoadState,
    updatedAt: lastUpdatedAt,
    currentModeLabel: currentMeta.label,
    currentModeKey: currentMode,
    schedulerLabel: currentMeta.scheduler,
    idleMinutes: status?.idle_minutes,
    validTransitionCount: validTransitions.length,
    enabledRuleCount,
    totalRuleCount,
    isNightTime: status?.is_night_time,
    degraded: status?.degraded,
  })

  return {
    currentMode,
    currentMeta,
    validTransitions,
    autoRules,
    enabledRuleCount,
    totalRuleCount,
    runtimeModeLoadState,
    endocrineOrganModel,
    modeButtons: buildRuntimeModeButtonViews({ currentMode, validTransitions }),
    autoRuleRows: buildRuntimeModeAutoRuleViews(autoRules),
    stateAxisItems: RUNTIME_MODE_STATE_AXIS_ITEMS,
    heroTitle: `${currentMeta.emoji} ${currentMeta.label}`,
    heroDescription: currentMeta.semantic,
    heroMetaItems: buildRuntimeModeHeroMetaItems({ status, validTransitions, enabledRuleCount }),
    metricCards: buildRuntimeModeMetricCards({ status, currentMeta, enabledRuleCount }),
    showUnavailableNotice: !consoleError && !loading && !refreshing && runtimeModeLoadState === LOAD_STATES.UNAVAILABLE,
  }
}
