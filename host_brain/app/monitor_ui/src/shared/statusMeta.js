export const CANON_MANIFEST_STATUS_LABELS = Object.freeze({
  ACTIVE: '生效中',
  active: '生效中',
  UNDONE: '已撤销',
  GC: '已回收',
})

export const CANON_MANIFEST_STATUS_CLASSES = Object.freeze({
  ACTIVE: 'border-green-200 bg-green-50 text-green-700',
  active: 'border-green-200 bg-green-50 text-green-700',
  UNDONE: 'border-orange-200 bg-orange-50 text-orange-700',
  GC: 'border-gray-200 bg-gray-100 text-gray-500',
})

export const CANON_PROPOSAL_STATUS_LABELS = Object.freeze({
  pending: '待审核',
  APPLIED: '已生效',
  rejected: '已拒绝',
  rolled_back: '已回滚',
})

export const CANON_PROPOSAL_STATUS_CLASSES = Object.freeze({
  pending: 'border-yellow-200 bg-yellow-100 text-yellow-700',
  APPLIED: 'border-green-200 bg-green-100 text-green-700',
  rejected: 'border-red-200 bg-red-100 text-red-700',
  rolled_back: 'border-gray-200 bg-gray-100 text-gray-600',
})

export const TOOL_PROVIDER_STATUS_LABELS = Object.freeze({
  ACTIVE: '已启用',
  STAGED: '待启用',
  ERROR: '异常',
})

export const TOOL_PROVIDER_STATUS_CLASSES = Object.freeze({
  ACTIVE: 'bg-green-100 text-green-700',
  STAGED: 'bg-yellow-100 text-yellow-700',
  ERROR: 'bg-red-100 text-red-700',
})

export const TOOL_SIDE_EFFECT_STYLES = Object.freeze({
  READ: Object.freeze({ bg: 'bg-green-100', text: 'text-green-700', label: '只读' }),
  WRITE: Object.freeze({ bg: 'bg-orange-100', text: 'text-orange-700', label: '写入' }),
})

export const TOOL_AUTH_LEVEL_STYLES = Object.freeze({
  NONE: Object.freeze({ bg: 'bg-gray-100', text: 'text-gray-600', label: '无需确认' }),
  APPROVAL: Object.freeze({ bg: 'bg-yellow-100', text: 'text-yellow-700', label: '审批确认' }),
  APPROVAL_REQUIRED: Object.freeze({ bg: 'bg-yellow-100', text: 'text-yellow-700', label: '审批确认' }),
  CONFIRM: Object.freeze({ bg: 'bg-red-100', text: 'text-red-700', label: '高风险确认' }),
  CONFIRM_REQUIRED: Object.freeze({ bg: 'bg-red-100', text: 'text-red-700', label: '高风险确认' }),
})

export const SKILL_CLEARANCE_STYLES = Object.freeze({
  LOW: Object.freeze({ bg: 'bg-green-100', text: 'text-green-700', label: 'LOW' }),
  MEDIUM: Object.freeze({ bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'MEDIUM' }),
  HIGH: Object.freeze({ bg: 'bg-red-100', text: 'text-red-700', label: 'HIGH' }),
})

export const SKILL_SOURCE_STYLES = Object.freeze({
  builtin: Object.freeze({ bg: 'bg-blue-100', text: 'text-blue-700', label: '内置' }),
  local: Object.freeze({ bg: 'bg-green-100', text: 'text-green-700', label: '本地' }),
  github: Object.freeze({ bg: 'bg-purple-100', text: 'text-purple-700', label: 'GitHub' }),
  external: Object.freeze({ bg: 'bg-orange-100', text: 'text-orange-700', label: '外部' }),
  hub: Object.freeze({ bg: 'bg-indigo-100', text: 'text-indigo-700', label: 'Hub' }),
})

export const AUTOMATION_TASK_STATUS_STYLES = Object.freeze({
  PENDING: Object.freeze({ bg: 'bg-gray-100', text: 'text-gray-600', dot: 'bg-gray-400' }),
  RUNNING: Object.freeze({ bg: 'bg-blue-100', text: 'text-blue-700', dot: 'bg-blue-500 animate-pulse' }),
  SUCCESS: Object.freeze({ bg: 'bg-green-100', text: 'text-green-700', dot: 'bg-green-500' }),
  FAILED: Object.freeze({ bg: 'bg-red-100', text: 'text-red-700', dot: 'bg-red-500' }),
  DISABLED: Object.freeze({ bg: 'bg-gray-100', text: 'text-gray-400', dot: 'bg-gray-300' }),
  CANCELLED: Object.freeze({ bg: 'bg-yellow-100', text: 'text-yellow-700', dot: 'bg-yellow-400' }),
})

export const AUTOMATION_HISTORY_STATUS_CLASSES = Object.freeze({
  SUCCESS: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  FAILED: 'border-rose-200 bg-rose-50/70 text-rose-700',
  RUNNING: 'border-sky-200 bg-sky-50/70 text-sky-700',
})

export const AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES = Object.freeze({
  live: 'border-green-200 bg-green-50 text-green-700',
  degraded: 'border-orange-200 bg-orange-50 text-orange-700',
})

export const PROMPT_SOURCE_BADGE_CLASSES = Object.freeze({
  override: 'bg-green-100 text-green-700 border-green-200',
  prompt_spec: 'bg-blue-100 text-blue-700 border-blue-200',
  default: 'bg-gray-100 text-gray-600 border-gray-200',
})

export const PROMPT_SOURCE_LABELS = Object.freeze({
  override: '已覆盖',
  prompt_spec: 'PromptSpec',
  default: '默认',
})

export const PROMPT_CONTENT_TYPE_BADGE_CLASSES = Object.freeze({
  json: 'bg-purple-100 text-purple-700 border-purple-200',
  text: 'bg-blue-100 text-blue-700 border-blue-200',
})

export const PROMPT_CONTENT_TYPE_LABELS = Object.freeze({
  json: '结构化',
  text: '文本',
})

export const BIO_OPS_STATUS_CLASSES = Object.freeze({
  EXECUTED: 'border-green-200 bg-green-50 text-green-700',
  PASS: 'border-green-200 bg-green-50 text-green-700',
  AUDITED: 'border-blue-200 bg-blue-50 text-blue-700',
  PROPOSED: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  CANDIDATE: 'border-amber-200 bg-amber-50 text-amber-700',
  OWNER_GATED: 'border-amber-200 bg-amber-50 text-amber-700',
  READY_FOR_OWNER_IMPORT: 'border-green-200 bg-green-50 text-green-700',
  READY_FOR_ORGAN_CONSUMPTION: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  QUEUED_FOR_OWNER: 'border-purple-200 bg-purple-50 text-purple-700',
  IMPORTED_FOR_OWNER_REVIEW: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  ALREADY_IMPORTED: 'border-slate-200 bg-slate-50 text-slate-700',
  PENDING_OWNER_STATE_REVIEW: 'border-amber-200 bg-amber-50 text-amber-700',
  OWNER_STATE_ACCEPTED: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  OWNER_STATE_REJECTED: 'border-slate-200 bg-slate-50 text-slate-700',
  ACTIVE: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  INACTIVE: 'border-slate-200 bg-slate-50 text-slate-700',
  PENDING_REVIEW: 'border-amber-200 bg-amber-50 text-amber-700',
  REJECTED_BY_OWNER: 'border-slate-200 bg-slate-50 text-slate-700',
  TRACED: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  REPORT_ONLY: 'border-slate-200 bg-slate-50 text-slate-700',
  BLOCKED: 'border-red-200 bg-red-50 text-red-700',
  FAILED: 'border-red-200 bg-red-50 text-red-700',
  DEGRADED: 'border-orange-200 bg-orange-50 text-orange-700',
  NEEDS_INPUT: 'border-orange-200 bg-orange-50 text-orange-700',
  CHECK: 'border-orange-200 bg-orange-50 text-orange-700',
  NOT_RUN: 'border-gray-200 bg-gray-50 text-gray-600',
  UNKNOWN: 'border-gray-200 bg-gray-50 text-gray-600',
})

export const BIO_OPS_STATUS_LABELS = Object.freeze({
  EXECUTED: '已执行',
  PASS: '通过',
  AUDITED: '已审计',
  PROPOSED: '提案',
  CANDIDATE: '候选',
  OWNER_GATED: 'Owner 接管',
  READY_FOR_OWNER_IMPORT: '待 owner 导入',
  READY_FOR_ORGAN_CONSUMPTION: '待 organ 消费',
  QUEUED_FOR_OWNER: 'Owner 队列',
  IMPORTED_FOR_OWNER_REVIEW: 'Owner 状态候选',
  ALREADY_IMPORTED: '已导入',
  PENDING_OWNER_STATE_REVIEW: '待 owner state 审核',
  OWNER_STATE_ACCEPTED: 'State 已采纳',
  OWNER_STATE_REJECTED: 'State 已拒绝',
  ACTIVE: '活跃',
  INACTIVE: '停用',
  PENDING_REVIEW: '待审核',
  REJECTED_BY_OWNER: 'Owner 拒绝',
  TRACED: '已 trace',
  REPORT_ONLY: '报告模式',
  BLOCKED: '已阻断',
  FAILED: '失败',
  DEGRADED: '降级',
  NEEDS_INPUT: '需输入',
  CHECK: '需检查',
  NOT_RUN: '未运行',
  UNKNOWN: '未知',
})

export const BIO_OPS_STATUS_ORDER = Object.freeze(['PROPOSED', 'CANDIDATE', 'AUDITED', 'BLOCKED', 'DEGRADED', 'FAILED', 'NOT_RUN'])

export const DIGITAL_LIFE_ALERT_SEVERITY_CLASSES = Object.freeze({
  critical: 'border-red-200/80 bg-red-50/90 text-red-700',
  warning: 'border-orange-200/80 bg-orange-50/90 text-orange-700',
  info: 'border-blue-200/80 bg-blue-50/90 text-blue-700',
})

export const DIGITAL_LIFE_SOAK_VERDICT_LABELS = Object.freeze({
  RUNNING: '验证中',
  PASS: '通过',
  FAIL: '失败',
  MISSING: '未发现',
})

export const DIGITAL_LIFE_SOAK_ACCEPTANCE_VERDICT_LABELS = Object.freeze({
  PASS: '通过',
  RUNNING: '验证中',
  PARTIAL: '部分',
  UNKNOWN: '待证据',
  NOT_COVERED: '未覆盖',
  FAIL: '失败',
})

export const DIGITAL_LIFE_SOAK_LAYER_CLASSES = Object.freeze({
  healthy: 'border-emerald-200 bg-emerald-50/75',
  running: 'border-blue-200 bg-blue-50/75',
  degraded: 'border-orange-200 bg-orange-50/75',
  down: 'border-red-200 bg-red-50/75',
  unknown: 'border-gray-200 bg-gray-50/80',
})

export const LIFE_BODY_STATUS_LABELS = Object.freeze({
  critical: '重压',
  warning: '波动',
  stable: '稳定',
  pending: '待连通',
})

export const LIFE_BODY_STATUS_CLASSES = Object.freeze({
  critical: 'border-red-200 bg-red-50/90 text-red-700',
  warning: 'border-orange-200 bg-orange-50/90 text-orange-700',
  stable: 'border-emerald-200 bg-emerald-50/90 text-emerald-700',
  pending: 'border-gray-200 bg-gray-50/90 text-gray-600',
})

export const LIFE_BODY_TONE_LABELS = Object.freeze({
  critical: '重压',
  warning: '波动',
  stable: '稳定',
  pending: '待连通',
  info: '信号',
})

export const LIFE_BODY_TONE_CLASSES = Object.freeze({
  critical: 'border-red-200 bg-red-50/90 text-red-700',
  warning: 'border-orange-200 bg-orange-50/90 text-orange-700',
  stable: 'border-emerald-200 bg-emerald-50/90 text-emerald-700',
  pending: 'border-gray-200 bg-gray-50/90 text-gray-600',
  info: 'border-blue-200 bg-blue-50/90 text-blue-700',
})

export const LIFE_BODY_SEVERITY_CLASSES = Object.freeze({
  critical: 'border-red-200 bg-red-50 text-red-700',
  warning: 'border-orange-200 bg-orange-50 text-orange-700',
  info: 'border-blue-200 bg-blue-50 text-blue-700',
})

export const LIFE_BODY_PATHOLOGY_SEVERITY_LABELS = Object.freeze({
  critical: '高风险',
  warning: '波动',
  info: '提示',
})

export const LIFE_BODY_PATHOLOGY_SEVERITY_TONES = Object.freeze({
  critical: 'text-red-600',
  warning: 'text-orange-500',
  info: 'text-blue-600',
})

export const LIFE_BODY_TIMELINE_SEVERITY_LABELS = Object.freeze({
  critical: '高风险',
  warning: '波动',
  info: '信号',
})

export const SESSION_STATUS_TONES = Object.freeze({
  healthy: Object.freeze({
    ring: 'bg-emerald-400',
    border: 'border-emerald-200',
    bg: 'bg-emerald-50/80',
    badge: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  }),
  degraded: Object.freeze({
    ring: 'bg-amber-400',
    border: 'border-amber-200',
    bg: 'bg-amber-50/80',
    badge: 'bg-amber-100 text-amber-700 border-amber-200',
  }),
  error: Object.freeze({
    ring: 'bg-rose-400',
    border: 'border-rose-200',
    bg: 'bg-rose-50/80',
    badge: 'bg-rose-100 text-rose-700 border-rose-200',
  }),
  unknown: Object.freeze({
    ring: 'bg-slate-400',
    border: 'border-slate-200',
    bg: 'bg-white',
    badge: 'bg-slate-100 text-slate-700 border-slate-200',
  }),
})

export const SUBSYSTEM_STATUS_ORDER = Object.freeze({
  down: 0,
  degraded: 1,
  up: 2,
  unknown: 3,
})

export const ALERT_SEVERITY_RANK = Object.freeze({
  critical: 0,
  warning: 1,
  info: 2,
  resolved: 3,
})

export const ALERT_SEVERITY_LABELS = Object.freeze({
  critical: '严重',
  warning: '警告',
  info: '提示',
  resolved: '已恢复',
})

export const ALERT_SEVERITY_BADGE_CLASSES = Object.freeze({
  critical: 'text-red-700 bg-red-50 border-red-200',
  warning: 'text-orange-700 bg-orange-50 border-orange-200',
  info: 'text-blue-700 bg-blue-50 border-blue-200',
  resolved: 'text-green-700 bg-green-50 border-green-200',
})

export const ALERT_SEVERITY_CARD_CLASSES = Object.freeze({
  critical: 'border-red-200 bg-red-50/70',
  warning: 'border-orange-200 bg-orange-50/70',
  info: 'border-blue-200 bg-blue-50/70',
  resolved: 'border-green-200 bg-green-50/70',
})

export const ALERT_SEVERITY_ACCENT_CLASSES = Object.freeze({
  critical: 'bg-red-500',
  warning: 'bg-orange-400',
  info: 'bg-blue-400',
  resolved: 'bg-green-500',
})

export const GATEWAY_STATUS_LABELS = Object.freeze({
  UP: '在线',
  DEGRADED: '降级',
  DOWN: '离线',
  UNKNOWN: '未知',
})

export const GATEWAY_STATUS_TEXT_CLASSES = Object.freeze({
  UP: 'text-green-600',
  DEGRADED: 'text-orange-500',
  DOWN: 'text-red-500',
  UNKNOWN: 'text-gray-400',
})

export const GATEWAY_STATUS_BADGE_CLASSES = Object.freeze({
  UP: 'bg-green-50 border-green-200',
  DEGRADED: 'bg-orange-50 border-orange-200',
  DOWN: 'bg-red-50 border-red-200',
  UNKNOWN: 'bg-gray-50 border-gray-200',
})

export const GATEWAY_PLATFORM_META = Object.freeze({
  wechat: Object.freeze({ label: '微信', color: '#07C160' }),
  qq: Object.freeze({ label: 'QQ', color: '#12B7F5' }),
  telegram: Object.freeze({ label: 'Telegram', color: '#0088CC' }),
  feishu: Object.freeze({ label: '飞书', color: '#3370FF' }),
  wecom: Object.freeze({ label: '企业微信', color: '#2B7CE9' }),
  line: Object.freeze({ label: 'LINE', color: '#00B900' }),
})

export const LOG_LEVEL_STYLES = Object.freeze({
  ERROR: Object.freeze({ bg: 'bg-red-50', text: 'text-red-700', badge: 'bg-red-100 text-red-700' }),
  WARNING: Object.freeze({ bg: 'bg-orange-50', text: 'text-orange-600', badge: 'bg-orange-100 text-orange-700' }),
  INFO: Object.freeze({ bg: '', text: 'text-gray-700', badge: 'bg-blue-100 text-blue-700' }),
  DEBUG: Object.freeze({ bg: '', text: 'text-gray-500', badge: 'bg-gray-100 text-gray-500' }),
})

export const DIGITAL_LIFE_MODE_BUTTON_CLASSES = Object.freeze({
  normal: Object.freeze({
    active: 'ring-2 ring-green-500 bg-green-50 border-green-300',
    hover: 'hover:bg-green-50 hover:border-green-300',
  }),
  focus: Object.freeze({
    active: 'ring-2 ring-blue-500 bg-blue-50 border-blue-300',
    hover: 'hover:bg-blue-50 hover:border-blue-300',
  }),
  gaming: Object.freeze({
    active: 'ring-2 ring-purple-500 bg-purple-50 border-purple-300',
    hover: 'hover:bg-purple-50 hover:border-purple-300',
  }),
  sleep: Object.freeze({
    active: 'ring-2 ring-indigo-500 bg-indigo-50 border-indigo-300',
    hover: 'hover:bg-indigo-50 hover:border-indigo-300',
  }),
  maintenance: Object.freeze({
    active: 'ring-2 ring-yellow-500 bg-yellow-50 border-yellow-300',
    hover: 'hover:bg-yellow-50 hover:border-yellow-300',
  }),
  locked: Object.freeze({
    active: 'ring-2 ring-red-500 bg-red-50 border-red-300',
    hover: 'hover:bg-red-50 hover:border-red-300',
  }),
  fallback: Object.freeze({
    active: 'ring-2 ring-gray-500 bg-gray-50 border-gray-300',
    hover: '',
  }),
})

export const DIGITAL_LIFE_DOMAIN_STATUS_META = Object.freeze({
  wired: Object.freeze({ label: '已接入', className: 'border-green-200 bg-green-50 text-green-700' }),
  degraded: Object.freeze({ label: '降级', className: 'border-orange-200 bg-orange-50 text-orange-700' }),
  pending: Object.freeze({ label: '待连通', className: 'border-gray-200 bg-gray-50 text-gray-500' }),
})

export const DIGITAL_LIFE_MAIN_CHAIN_STAGE_CLASSES = Object.freeze({
  healthy: 'border-emerald-200 bg-emerald-50/70',
  running: 'border-blue-200 bg-blue-50/70',
  degraded: 'border-orange-200 bg-orange-50/70',
  down: 'border-red-200 bg-red-50/75',
  unknown: 'border-gray-200 bg-gray-50/75',
})

export const DIGITAL_LIFE_MAIN_CHAIN_DOT_CLASSES = Object.freeze({
  healthy: 'bg-emerald-500',
  running: 'bg-blue-500',
  degraded: 'bg-orange-500',
  down: 'bg-red-500',
  unknown: 'bg-gray-400',
})

export const EMOTION_MOOD_STYLES = Object.freeze({
  calm: Object.freeze({ color: 'text-blue-500', bg: 'bg-blue-50', label: '平静' }),
  steady: Object.freeze({ color: 'text-gray-500', bg: 'bg-gray-50', label: '稳定' }),
  engaged: Object.freeze({ color: 'text-green-600', bg: 'bg-green-50', label: '活跃' }),
  tired: Object.freeze({ color: 'text-orange-500', bg: 'bg-orange-50', label: '疲惫' }),
  critical: Object.freeze({ color: 'text-red-600', bg: 'bg-red-50', label: '临界' }),
})

export const EMOTION_TAG_COLORS = Object.freeze({
  HAPPY: '#22c55e',
  EXCITED: '#eab308',
  SAD: '#3b82f6',
  SADNESS: '#3b82f6',
  ANGRY: '#ef4444',
  ANGER: '#ef4444',
  NEUTRAL: '#9ca3af',
  SURPRISED: '#a855f7',
  SURPRISE: '#a855f7',
  CONFUSED: '#f97316',
  CALM: '#06b6d4',
  LOVE: '#ec4899',
  CURIOUS: '#8b5cf6',
  SHY: '#f472b6',
  BORED: '#6b7280',
})

export const ENDOCRINE_GATE_LABELS = Object.freeze({
  open: '开放',
  cautious: '谨慎',
  protective: '保护',
})

export const BOND_STAGE_COLORS = Object.freeze({
  STRANGER: '#94a3b8',
  ACQUAINTANCE: '#60a5fa',
  FRIEND: '#34d399',
  CLOSE: '#22c55e',
  CLOSE_FRIEND: '#a78bfa',
  COMPANION: '#f472b6',
  CONFIDANT: '#ec4899',
  INTIMATE: '#ef4444',
  SOULMATE: '#f59e0b',
})

export function resolveStatusLabel(labels, status, fallback = '—') {
  const value = String(status || '').trim()
  if (!value) return fallback
  return labels[value] || labels[value.toUpperCase()] || labels[value.toLowerCase()] || value
}

export function resolveStatusClass(classes, status, fallback) {
  const value = String(status || '').trim()
  if (!value) return fallback
  return classes[value] || classes[value.toUpperCase()] || classes[value.toLowerCase()] || fallback
}

export function classifyLogLevel(entry) {
  const reasonCode = String(entry?.reason_code || '').toUpperCase()
  if (reasonCode.includes('BLOCK') || reasonCode.includes('FAIL')) return 'ERROR'
  if (reasonCode.includes('DEGRADE') || reasonCode.includes('TIMEOUT')) return 'WARNING'
  return 'INFO'
}

export function normalizeSessionStatus(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'ok' || value === 'success') return 'healthy'
  if (value === 'rejected') return 'degraded'
  if (value === 'error' || value === 'fail') return 'error'
  return 'unknown'
}

export function resolveSessionStatusTone(status) {
  const normalized = normalizeSessionStatus(status)
  return SESSION_STATUS_TONES[normalized] || SESSION_STATUS_TONES.unknown
}

export function normalizeSubsystemStatus(subsystem) {
  const raw = String(subsystem?.status || '').toUpperCase()
  if (raw === 'UP' || raw === 'OK' || raw === 'HEALTHY') return 'up'
  if (raw === 'DEGRADED' || raw === 'WARN') return 'degraded'
  if (raw === 'DOWN' || raw === 'FAIL' || raw === 'FAILED' || raw === 'ERROR') return 'down'

  const rate = subsystem?.success_rate
  if (rate == null) return 'unknown'
  if (rate >= 80) return 'up'
  if (rate >= 50) return 'degraded'
  return 'down'
}
