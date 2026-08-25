export const DASH = '—'

import {
  formatDateTime as formatSharedDateTime,
  formatNumber as formatSharedNumber,
} from '../shared/formatters.js'

export const MODE_META = {
  normal: {
    emoji: '🟢',
    label: '正常',
    desc: '全功能在线',
    semantic: '数字生命体完整在线，允许主动对话、记忆更新与情绪表达。',
    scheduler: 'HIGH + MEDIUM + LOW',
    behaviorSummary: '全部子系统可按需参与。',
    highlights: ['主动对话正常触发', '记忆、情感、VTS 按需工作', '夜间与日记任务保持调度'],
  },
  focus: {
    emoji: '🎯',
    label: '专注',
    desc: '低打扰陪伴',
    semantic: '感知你正在专注，优先保持安静陪伴与低干扰响应。',
    scheduler: 'HIGH',
    behaviorSummary: '保留被动响应，压低主动打扰。',
    highlights: ['主动打扰显著减少', '保留必要响应与记忆', '低优先级调度暂缓'],
  },
  gaming: {
    emoji: '🎮',
    label: '游戏',
    desc: '陪玩陪伴',
    semantic: '围绕游戏场景提供陪伴、观察与适配式回应。',
    scheduler: 'HIGH',
    behaviorSummary: '允许陪玩识图与轻量交互，抑制无关主动触发。',
    highlights: ['允许游戏场景陪伴', '情绪和关系仍会积累', '非关键轮询进一步收缩'],
  },
  sleep: {
    emoji: '🌙',
    label: '睡眠',
    desc: '离线内在演化',
    semantic: '外显对话收缩，后台继续进行内在世界、梦境与情感漂移。',
    scheduler: 'HIGH + MEDIUM',
    behaviorSummary: '对外低频，对内持续演化。',
    highlights: ['梦境与离线状态继续积累', '晨间或消息可唤醒', '等待与思念等状态会自然漂移'],
  },
  maintenance: {
    emoji: '🛠️',
    label: '维护',
    desc: '系统维护窗口',
    semantic: '系统处于维护窗口，外部执行链路应尽量停止。',
    scheduler: '全部暂停',
    behaviorSummary: '仅保留必要的健康与维护逻辑。',
    highlights: ['外部链路暂停', '适合版本切换或排障', '避免产生混杂运行状态'],
  },
  locked: {
    emoji: '🔒',
    label: '锁定',
    desc: '安全锁定',
    semantic: '系统进入安全保护状态，拒绝常规执行。',
    scheduler: '全部暂停',
    behaviorSummary: '仅保留最小安全逻辑。',
    highlights: ['阻断常规响应', '等待人工处理', '优先保护数据与人格一致性'],
  },
}

export const STAGE_LABELS = {
  STRANGER: '初见义妹',
  ACQUAINTANCE: '相处义妹',
  FRIEND: '关系好的兄妹',
  CLOSE: '亲近',
  CLOSE_FRIEND: '亲近兄妹',
  COMPANION: '陪伴',
  CONFIDANT: '亲密恋人',
  INTIMATE: '亲密恋人',
  SOULMATE: '灵魂伴侣',
}

export const REPLY_POSTURE_LABELS = {
  balanced: '平衡',
  focused: '专注',
  warm_reconnect: '温柔重连',
  brief_guarded: '克制防守',
  withdrawn: '收敛',
  warm: '温柔',
  playful: '轻快',
  guarded: '谨慎',
  brief: '简短',
}

export const MOTIVE_LABELS = {
  reach_out: '主动靠近',
  care: '照顾安抚',
  withdraw: '抽离退避',
  continuity: '维持联系',
  self_protect: '自我保护',
  none: '暂无主导动机',
}

export const RISK_LEVEL_LABELS = {
  none: '无',
  low: '低',
  medium: '中',
  high: '高',
  critical: '严重',
}

export const ABSENCE_BAND_LABELS = {
  recent: '近期在线',
  '6h_plus': '离线 6 小时以上',
  '24h_plus': '离线 24 小时以上',
  '72h_plus': '离线 72 小时以上',
  '7d_plus': '离线 7 天以上',
}

export const RECONNECT_STYLE_LABELS = {
  soft_resume: '轻柔续接',
  light_reconnect: '轻量重连',
  reunion: '重逢',
  deep_reunion: '深度重连',
}

export const SLOT_LABELS = {
  persona_user_id: '用户身份',
  hard_state: '硬状态',
  sys_mode_status: '生命体存在态轴',
  activity_monitor: '活动感知',
  emotion_context: '情绪上下文',
  endocrine_state: '内分泌状态',
  immune_state: '免疫状态',
  bond_state: '关系状态',
  inner_world_state: '内在世界',
  offline_state: '离线状态',
  silence_state: '沉默机制',
  body_sense: '身体感',
  self_model_snapshot: '自我模型',
  self_reflection_pulse: '自我反思',
  bond_narrative: '关系叙事',
  motive_state: '动机系统',
  render_policy: '回复策略',
  somatic_state: '躯体状态',
  peripheral_state: '外周状态',
  circadian_state: '昼夜节律',
  proprioception_state: '本体感觉',
  initiative_decision: '主动门禁',
  default_mode_state: '默认模式',
  mirror_neuron_state: '镜像神经元',
  memory_layers: '记忆分层',
  language_dual_system: '语言双系统',
  homeostasis_report: '稳态报告',
  interoception_state: '内感受',
  motor_cortex_state: '运动皮层',
  cerebellum_state: '小脑校正',
  glymphatic_state: '恢复清除',
  vestibular_state: '前庭平衡',
}

export const SLOT_STATUS_LABELS = {
  live: '在线',
  degraded: '降级',
  unavailable: '不可用',
  loading: '同步中',
  error: '异常',
  unknown: '未知',
}

export const WILLINGNESS_LEVEL_LABELS = {
  MUTE: '静默',
  SILENT: '沉默',
  LOW: '低意愿',
  RELUCTANT: '勉强回应',
  NORMAL: '正常',
  HIGH: '高意愿',
}

export const FATIGUE_LEVEL_LABELS = {
  FRESH: '轻松',
  TIRED: '轻疲惫',
  WEARY: '疲惫',
  HIGH: '高疲惫',
  EXHAUSTED: '透支',
  UNKNOWN: '未知',
}

export const DROWSINESS_LEVEL_LABELS = {
  ALERT: '清醒',
  AWAKE: '清醒',
  MED: '微困',
  DROWSY: '困倦',
  HIGH: '明显困倦',
  HEAVY: '极困',
  UNKNOWN: '未知',
}

export const ALLOWED_STYLE_LABELS = {
  brief: '简短克制',
  guarded: '谨慎保守',
}

export const ALERT_TITLES = {
  RUNTIME_MODE_DEGRADED: '生命体存在态轴降级',
  RUNTIME_SNAPSHOT_DEGRADED: '运行快照存在缺口',
  RUNTIME_SLOT_NOT_LIVE: '关键槽位未在线',
  REFLECTION_GUARD_ACTIVE: '自我反思护栏生效',
  TOPIC_RESISTANCE_ACTIVE: '当前存在话题抗拒',
  WITHDRAWN_REPLY_POLICY: '回复策略偏收敛',
  PROMPT_INJECTION_DETECTED: '检测到提示注入',
  PROMPT_HIGH_RISK_BUILD: '检测到高风险 Prompt 构建',
  SERVICE_DEGRADED: '服务降级',
  SERVICE_DOWN: '服务离线',
  BACKGROUND_RUNTIME_NOT_ADVANCING: '后台推进异常',
  BACKGROUND_CORE_TICK_MISSING: '后台核心任务缺失',
  BACKGROUND_PERSONA_MISMATCH: '后台人格对齐异常',
  INTEROCEPTION_OVERLOAD: '内感受负载过高',
  VESTIBULAR_INSTABILITY: '前庭平衡不稳',
  MOTOR_DISPATCH_DEGRADED: '动作分发降级',
  HOMEOSTASIS_WARNING: '稳态风险上升',
}

export const EMOTION_TAG_LABELS = {
  NEUTRAL: '平静',
  HAPPY: '开心',
  JOY: '喜悦',
  EXCITED: '兴奋',
  SAD: '难过',
  SADNESS: '难过',
  ANGRY: '愤怒',
  ANGER: '愤怒',
  FEAR: '害怕',
  SURPRISE: '惊讶',
  SURPRISED: '惊讶',
  CONFUSED: '困惑',
  CALM: '平静',
  LOVE: '喜欢',
  CURIOUS: '好奇',
  SHY: '害羞',
  BORED: '无聊',
  DISGUST: '排斥',
}

export const TREND_LABELS = {
  RISING: '上升',
  IMPROVING: '改善',
  WARMING: '回暖',
  WARMING_UP: '回暖',
  STABLE: '稳定',
  STEADY: '稳定',
  MIXED: '波动',
  SWINGING: '波动',
  FLUCTUATING: '波动',
  DECLINING: '下降',
  FALLING: '下降',
  COOLING: '降温',
}

export const DECISION_LABELS = {
  ALLOW: '放行',
  PASS: '通过',
  REJECT: '拒绝',
  BLOCK: '拦截',
  COMMIT: '确认回复',
  CONTINUE: '继续',
}

export const EVENT_PHASE_LABELS = {
  PRESENCE: '在场判定',
  COMMIT: '提交回复',
  REJECT: '拒绝',
  CHUNK: '流式片段',
  NODE: '链路节点',
}

export const SCENE_TYPE_LABELS = {
  private_chat: '私聊',
  direct_chat: '私聊',
  dm: '私聊',
  group_chat: '群聊',
  wx_private: '微信私聊',
  wx_group: '微信群聊',
  focus: '专注中',
  gaming: '游戏中',
  sleep: '睡眠态',
  idle: '空闲中',
}

export const SERVICE_STATUS_LABELS = {
  healthy: '健康',
  up: '健康',
  degraded: '降级',
  down: '离线',
  error: '故障',
  running: '运行中',
  not_tested: '未验收',
  unknown: '未知',
}

const TOKEN_LABELS = {
  llm: 'LLM',
  vts: 'VTS',
  tts: 'TTS',
  asr: 'ASR',
  rag: 'RAG',
  rpc: 'RPC',
  sse: 'SSE',
  ws: 'WebSocket',
  cortex_orchestrator: 'Cortex Orchestrator',
  executive_router: 'Executive Router',
  surface_reply: 'Surface Reply',
  sub_agent: 'Sub Agent',
  query_planner: 'Query Planner',
  agentic_rag: 'Agentic RAG',
  recall_fusion: 'Recall Fusion',
  emotion_context: 'Emotion Context',
  persona_resident_memory: 'Persona Resident Memory',
  motive_state: 'Motive State',
  render_policy: 'Render Policy',
  prompt_builder: 'Prompt Builder',
  airlock_egress: 'Airlock Egress',
}

function normalizeKey(value) {
  return normalizeText(value).replace(/\s+/g, '_')
}

function lookupLabel(map, value) {
  const key = normalizeKey(value)
  if (!key) return null
  return map[key] || map[key.toUpperCase()] || map[key.toLowerCase()] || null
}

export function normalizeText(value) {
  if (value == null) return ''
  return String(value).trim()
}

export function humanizeToken(value) {
  const raw = normalizeText(value)
  if (!raw) return DASH

  const mapped = TOKEN_LABELS[raw] || TOKEN_LABELS[raw.toLowerCase()]
  if (mapped) return mapped

  return raw
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .replace(/\bLlm\b/g, 'LLM')
    .replace(/\bVts\b/g, 'VTS')
    .replace(/\bAsr\b/g, 'ASR')
    .replace(/\bTts\b/g, 'TTS')
    .replace(/\bRag\b/g, 'RAG')
    .replace(/\bRpc\b/g, 'RPC')
}

export function classNames(...values) {
  return values.filter(Boolean).join(' ')
}

export function asArray(value) {
  if (Array.isArray(value)) return value
  if (value == null || value === '') return []
  return [value]
}

export function formatStage(stage) {
  return lookupLabel(STAGE_LABELS, stage) || humanizeToken(stage)
}

export function formatDateTime(tsMs) {
  if (tsMs == null || tsMs === '') return DASH
  if (typeof tsMs === 'string' && Number.isNaN(Number(tsMs))) {
    const parsedMs = Date.parse(tsMs)
    return Number.isNaN(parsedMs)
      ? DASH
      : formatSharedDateTime(parsedMs, { placeholder: DASH, options: { hour12: false } })
  }
  const numeric = Number(tsMs)
  if (!Number.isFinite(numeric)) return DASH
  const ms = numeric < 1e12 ? numeric * 1000 : numeric
  return formatSharedDateTime(ms, { placeholder: DASH, options: { hour12: false } })
}

export function formatNumber(value, digits = 2) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return DASH
  return formatSharedNumber(numeric, digits, {
    placeholder: DASH,
    minimumFractionDigits: digits > 0 && Math.abs(numeric) < 10 ? Math.min(digits, 2) : 0,
  })
}

export function formatBoolean(value, yes = '是', no = '否') {
  if (value == null) return DASH
  return value ? yes : no
}

export function compactText(value, maxLength = 120) {
  const text = normalizeText(value)
  if (!text) return DASH
  if (text.length <= maxLength) return text
  return `${text.slice(0, Math.max(0, maxLength - 1))}…`
}

export function formatMode(value) {
  const key = normalizeText(value).toLowerCase()
  return MODE_META[key]?.label || humanizeToken(value)
}

export function formatReplyPosture(value) {
  return lookupLabel(REPLY_POSTURE_LABELS, value) || humanizeToken(value)
}

export function formatDominantMotive(value) {
  return lookupLabel(MOTIVE_LABELS, value) || humanizeToken(value)
}

export function formatRiskLevel(value) {
  return lookupLabel(RISK_LEVEL_LABELS, value) || humanizeToken(value)
}

export function formatAbsenceBand(value) {
  return lookupLabel(ABSENCE_BAND_LABELS, value) || humanizeToken(value)
}

export function formatReconnectStyle(value) {
  return lookupLabel(RECONNECT_STYLE_LABELS, value) || humanizeToken(value)
}

export function formatWillingnessLevel(value) {
  return lookupLabel(WILLINGNESS_LEVEL_LABELS, value) || humanizeToken(value)
}

export function formatFatigueLevel(value) {
  return lookupLabel(FATIGUE_LEVEL_LABELS, value) || humanizeToken(value)
}

export function formatDrowsinessLevel(value) {
  return lookupLabel(DROWSINESS_LEVEL_LABELS, value) || humanizeToken(value)
}

export function formatAllowedStyle(value) {
  return lookupLabel(ALLOWED_STYLE_LABELS, value) || humanizeToken(value)
}

export function formatSlotLabel(value) {
  return lookupLabel(SLOT_LABELS, value) || humanizeToken(value)
}

export function formatSlotStatus(value) {
  return lookupLabel(SLOT_STATUS_LABELS, value) || humanizeToken(value)
}

export function formatAlertTitle(reasonCode) {
  return lookupLabel(ALERT_TITLES, reasonCode) || humanizeToken(reasonCode)
}

export function formatEmotionTag(value) {
  return lookupLabel(EMOTION_TAG_LABELS, value) || humanizeToken(value)
}

export function formatTrend(value) {
  return lookupLabel(TREND_LABELS, value) || humanizeToken(value)
}

export function formatDecision(value) {
  return lookupLabel(DECISION_LABELS, value) || humanizeToken(value)
}

export function formatEventPhase(value) {
  return lookupLabel(EVENT_PHASE_LABELS, value) || humanizeToken(value)
}

export function formatSceneType(value) {
  return lookupLabel(SCENE_TYPE_LABELS, value) || humanizeToken(value)
}

export function formatServiceStatus(value) {
  return lookupLabel(SERVICE_STATUS_LABELS, value) || humanizeToken(value)
}

export function formatDigitalLifeTag(value) {
  const raw = normalizeText(value)
  if (!raw) return DASH

  const specialLabel =
    lookupLabel(MOTIVE_LABELS, raw)
    || lookupLabel(REPLY_POSTURE_LABELS, raw)
    || lookupLabel(RECONNECT_STYLE_LABELS, raw)
    || lookupLabel(ABSENCE_BAND_LABELS, raw)
    || lookupLabel(EMOTION_TAG_LABELS, raw)
    || lookupLabel(ALLOWED_STYLE_LABELS, raw)

  if (specialLabel) return specialLabel
  if (raw.startsWith('slot:')) return formatSlotLabel(raw.slice(5))
  if (raw.startsWith('alert:')) return formatAlertTitle(raw.slice(6))
  return humanizeToken(raw)
}

export function formatRuntimeSignal(signal) {
  if (signal == null) return ''
  if (typeof signal === 'string') return formatDigitalLifeTag(signal)
  if (typeof signal === 'object') {
    const label = normalizeText(signal.label || signal.title || signal.reason_code || signal.type)
    const detail = normalizeText(signal.detail || signal.value || signal.summary)
    if (label && detail) return `${formatDigitalLifeTag(label)}：${detail}`
    if (label) return formatDigitalLifeTag(label)
    if (detail) return detail
  }
  return humanizeToken(signal)
}

export function formatDegradedReason(reason) {
  if (reason == null) return ''
  if (typeof reason === 'string') return formatDigitalLifeTag(reason)
  if (typeof reason === 'object') {
    const slot = normalizeText(reason.slot || reason.slot_name)
    const title = normalizeText(reason.title || reason.reason_code || reason.code)
    const detail = normalizeText(reason.detail || reason.message || reason.status)
    const parts = []
    if (slot) parts.push(formatSlotLabel(slot))
    if (title) parts.push(formatAlertTitle(title))
    if (detail && detail !== title) parts.push(detail)
    return parts.filter(Boolean).join('：')
  }
  return humanizeToken(reason)
}

export function formatNonLiveSlotList(nonLiveSlots) {
  const items = asArray(nonLiveSlots)
    .map((item) => {
      if (Array.isArray(item)) {
        const [slot, status] = item
        return `${formatSlotLabel(slot)}（${formatSlotStatus(status)}）`
      }
      if (typeof item === 'object' && item) {
        return `${formatSlotLabel(item.slot || item.name)}（${formatSlotStatus(item.status)}）`
      }
      return formatSlotLabel(item)
    })
    .filter(Boolean)

  return items.length > 0 ? items.join('、') : DASH
}
