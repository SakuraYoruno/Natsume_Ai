export const STATUS_RANK = {
  critical: 0,
  warning: 1,
  stable: 2,
  pending: 3,
}

export const SEVERITY_RANK = {
  critical: 0,
  warning: 1,
  info: 2,
}

export const HOTSPOT_ORGAN_MAP = {
  prefrontal_governor: 'nervous',
  language_feedback: 'nervous',
  somatic_progress: 'motor',
  immune_realign: 'immune',
  motor_execution: 'motor',
  closed_loop_integration: 'motor',
}

export const ORGAN_META = {
  nervous: {
    name: '神经系统',
    path: '/observability/trace',
    drilldownLabel: '进入 Trace',
    fallbackImpact: '会影响主链路的连续性、可解释性和请求回放效率。',
  },
  limbic: {
    name: '边缘系统',
    path: '/life/emotion',
    drilldownLabel: '查看情绪与关系',
    fallbackImpact: '会直接改变回复姿态、关系温度与陪伴感。',
  },
  memory: {
    name: '记忆系统',
    path: '/life/memory',
    drilldownLabel: '查看记忆详情',
    fallbackImpact: '会影响回忆注入、设定延续和长期陪伴一致性。',
  },
  endocrine: {
    name: '内分泌系统',
    path: '/execution/automation',
    drilldownLabel: '查看节律详情',
    fallbackImpact: '会影响后台推进、疲劳恢复和内在世界演化。',
  },
  immune: {
    name: '免疫系统',
    path: '/observability/alerts',
    drilldownLabel: '查看免疫告警',
    fallbackImpact: '会影响门控放行、风险收束和叙事护栏。',
  },
  motor: {
    name: '运动系统',
    path: '/execution/gateway',
    drilldownLabel: '查看执行闭环',
    fallbackImpact: '会影响 ACK、进度触达和用户对行动结果的体感。',
  },
  self: {
    name: '自我系统',
    path: '/life/self-model',
    drilldownLabel: '查看自我模型',
    fallbackImpact: '会影响身份一致性、成长叙事和长期人格稳定度。',
  },
}

export function clampPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return null
  return Math.max(0, Math.min(100, Number(value)))
}

export function formatPercent(value, digits = 1) {
  const numeric = clampPercent(value)
  if (numeric == null) return '--'
  return `${numeric.toFixed(digits)}%`
}

export function formatWindowLabel(windowMinutes) {
  if (windowMinutes <= 60) return '1h'
  if (windowMinutes <= 360) return '6h'
  if (windowMinutes <= 1440) return '24h'
  if (windowMinutes <= 10080) return '7d'
  return `${windowMinutes}m`
}

export function hasReadableValue(value) {
  const normalized = String(value ?? '').trim()
  return Boolean(normalized) && normalized !== '—' && normalized !== '--' && normalized !== '待判定'
}

export function buildPathologyDetailPath(pathologyId) {
  return `/life/pathologies/${encodeURIComponent(String(pathologyId || ''))}`
}

export function getAlertOrganId(alert) {
  const code = String(alert?.reason_code || '').toUpperCase()
  if (code.startsWith('BACKGROUND_') || code === 'INTEROCEPTION_OVERLOAD' || code === 'VESTIBULAR_INSTABILITY' || code === 'HOMEOSTASIS_WARNING') {
    return 'endocrine'
  }
  if (code === 'TOPIC_RESISTANCE_ACTIVE' || code === 'WITHDRAWN_REPLY_POLICY') {
    return 'limbic'
  }
  if (code === 'PROMPT_INJECTION_DETECTED' || code === 'PROMPT_HIGH_RISK_BUILD') {
    return 'immune'
  }
  if (code === 'REFLECTION_GUARD_ACTIVE' || code.startsWith('FORGE_RUNTIME_')) {
    return 'self'
  }
  if (code === 'MOTOR_DISPATCH_DEGRADED') {
    return 'motor'
  }
  return 'nervous'
}

export function pickStatus({ hasData, critical, warning }) {
  if (!hasData) return 'pending'
  if (critical) return 'critical'
  if (warning) return 'warning'
  return 'stable'
}
