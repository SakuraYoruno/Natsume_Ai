export const TRACE_NODE_TYPE_META = Object.freeze({
  intent: Object.freeze({ color: '#2970FF', bg: '#EEF4FF', label: '意图' }),
  governor: Object.freeze({ color: '#7C3AED', bg: '#F5F3FF', label: '守护' }),
  immune: Object.freeze({ color: '#EA580C', bg: '#FFF7ED', label: '免疫' }),
  skill: Object.freeze({ color: '#0891B2', bg: '#ECFEFF', label: '技能' }),
  asr: Object.freeze({ color: '#059669', bg: '#ECFDF5', label: '语音' }),
  tts: Object.freeze({ color: '#D97706', bg: '#FFFBEB', label: '合成' }),
  vts: Object.freeze({ color: '#DB2777', bg: '#FDF2F8', label: '表情' }),
  llm: Object.freeze({ color: '#2970FF', bg: '#EEF4FF', label: '模型' }),
  memory: Object.freeze({ color: '#7C3AED', bg: '#F5F3FF', label: '记忆' }),
  bond: Object.freeze({ color: '#D97706', bg: '#FFFBEB', label: '关系' }),
  system: Object.freeze({ color: '#475467', bg: '#F9FAFB', label: '系统' }),
  branch: Object.freeze({ color: '#0891B2', bg: '#ECFEFF', label: '分流' }),
  recall: Object.freeze({ color: '#0891B2', bg: '#ECFEFF', label: '召回' }),
  retrieval: Object.freeze({ color: '#0891B2', bg: '#ECFEFF', label: '检索' }),
  agent: Object.freeze({ color: '#7C3AED', bg: '#F5F3FF', label: '代理' }),
  router: Object.freeze({ color: '#475467', bg: '#F9FAFB', label: '路由' }),
  judge: Object.freeze({ color: '#059669', bg: '#ECFDF5', label: '判定' }),
  edge: Object.freeze({ color: '#667085', bg: '#F9FAFB', label: '边缘' }),
  default: Object.freeze({ color: '#98A2B3', bg: '#F9FAFB', label: '节点' }),
})

export const TRACE_PROVIDER_BADGE_META = Object.freeze({
  openai: Object.freeze({ bg: '#ECFDF5', text: '#059669', label: 'OpenAI' }),
  gemini: Object.freeze({ bg: '#EEF4FF', text: '#2970FF', label: 'Gemini' }),
  deepseek: Object.freeze({ bg: '#FDF2F8', text: '#DB2777', label: 'DeepSeek' }),
})

export const TRACE_STATUS_BORDER_COLORS = Object.freeze({
  OK: '#12B76A',
  UP: '#12B76A',
  success: '#12B76A',
  healthy: '#12B76A',
  DEGRADED: '#F79009',
  degraded: '#F79009',
  FAIL: '#F04438',
  DOWN: '#F04438',
  error: '#F04438',
  RUNNING: '#2970FF',
  running: '#2970FF',
})

export const TRACE_LAYER_BADGE_META = Object.freeze({
  edge: Object.freeze({ bg: '#ECFDF5', text: '#059669', label: '边缘' }),
  host: Object.freeze({ bg: '#EEF4FF', text: '#2970FF', label: '主端' }),
  both: Object.freeze({ bg: '#F5F3FF', text: '#7C3AED', label: '双端' }),
})

export function resolveTraceNodeTypeColor(nodeType, fallback = TRACE_NODE_TYPE_META.default.color) {
  return TRACE_NODE_TYPE_META[nodeType]?.color ?? fallback
}

export function resolveTraceStatusBorder(status, fallback = '#D0D5DD') {
  return TRACE_STATUS_BORDER_COLORS[status] ?? fallback
}
