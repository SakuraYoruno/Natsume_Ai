const TRACE_NODE_NAME_BY_ID = {
  host_input: '输入解析',
  asr: '语音识别',
  gateway_webhook: '网关回调',
  input_reporter: '输入上报',
  airlock_ingress: '入口净化',
  governor: '入口守护',
  immune_gate: 'Immune Gate',
  query_planner: '查询规划器',
  agentic_rag: 'Agentic RAG',
  recall_fusion: '召回融合',
  retrieval_embedding: '召回嵌入',
  retrieval_evidence_pack: '证据包',
  retrieval_pipeline: '检索流水线',
  emotion_context: '情感上下文',
  persona_resident_memory: '人设常驻记忆',
  motive_state: '动机状态',
  render_policy: '响应策略',
  cortex_orchestrator: 'Cortex Orchestrator',
  executive_router: 'Executive Router',
  surface_reply: 'Surface Reply',
  prompt_builder: '提示构建器',
  llm_stream: '模型流式生成',
  llm_gateway: 'LLM Gateway',
  airlock_egress: '出口净化',
  pii_filter: '隐私过滤',
  episodic_store: '情节记忆写入',
  bond_update: '关系更新',
  audit_snapshot: '审计快照',
  tts: '语音合成',
  vts_driver: '表情驱动',
  sub_agent_code: '子代理 · 代码',
  sub_agent_translate: '子代理 · 翻译',
  sub_agent_intent: '子代理 · 意图',
  sub_agent_vision: '子代理 · 视觉',
}

const TRACE_DISPLAY_NAME_ALIASES = {
  ASR: '语音识别',
  'ASR 语音识别': '语音识别',
  'Airlock Ingress': '入口净化',
  Governor: '入口守护',
  'Immune Gate': 'Immune Gate',
  'Query Planner': '查询规划器',
  'Agentic RAG': 'Agentic RAG',
  'Recall Fusion': '召回融合',
  'Recall Embedding': '召回嵌入',
  'Evidence Pack': '证据包',
  'Retrieval Pipeline': '检索流水线',
  'Emotion Context': '情感上下文',
  'Persona Resident Memory': '人设常驻记忆',
  'Motive State': '动机状态',
  'Render Policy': '响应策略',
  'Cortex Orchestrator': 'Cortex Orchestrator',
  'Executive Router': 'Executive Router',
  'Surface Reply': 'Surface Reply',
  'Prompt Builder': '提示构建器',
  'LLM Stream': '模型流式生成',
  'LLM Gateway': 'LLM Gateway',
  'Host Input': '输入解析',
  'Airlock Egress': '出口净化',
  'PII Filter': '隐私过滤',
  'Bond Update': '关系更新',
  'Audit Snapshot': '审计快照',
  'TTS': '语音合成',
  'VTS Driver': '表情驱动',
}

const TRACE_NODE_TYPE_LABELS = {
  intent: '意图',
  governor: '守护',
  immune: '免疫',
  skill: '技能',
  asr: '语音',
  tts: '合成',
  vts: '表情',
  llm: '模型',
  memory: '记忆',
  bond: '关系',
  system: '系统',
  branch: '分流',
  recall: '召回',
  retrieval: '检索',
  agent: '代理',
  router: '路由',
  judge: '判定',
  default: '节点',
}

const TRACE_LAYER_LABELS = {
  edge: '边缘端',
  host: '主端',
  both: '双端',
}

export function normalizeTraceNodeKey(nodeId = '') {
  const raw = String(nodeId || '').trim()
  if (!raw) return ''
  if (raw.startsWith('voice_tts_')) return 'tts'
  if (raw.startsWith('voice_vts_')) return 'vts_driver'
  if (raw.startsWith('voice_asr_')) return 'asr'
  if (raw.startsWith('voice_immune_')) return 'immune_gate'
  if (raw.startsWith('voice_edge_')) return 'input_reporter'
  if (raw.startsWith('voice_')) return 'input_reporter'
  if (raw === 'immune_gate' || raw === 'immune') return 'immune_gate'
  if (raw.startsWith('episodic_store')) return 'episodic_store'
  if (raw.startsWith('bond_update')) return 'bond_update'
  if (raw.startsWith('sub_agent:code') || raw === 'sub_agent_code') return 'sub_agent_code'
  if (raw.startsWith('sub_agent:translate') || raw === 'sub_agent_translate') return 'sub_agent_translate'
  if (raw.startsWith('sub_agent:intent') || raw === 'sub_agent_intent') return 'sub_agent_intent'
  if (raw.startsWith('sub_agent:vision') || raw === 'sub_agent_vision') return 'sub_agent_vision'
  if (raw.startsWith('airlock')) return raw.includes('egress') ? 'airlock_egress' : 'airlock_ingress'
  if (raw === 'host_input') return 'airlock_ingress'
  if (raw === 'llm_gateway' || raw === 'cortex_orchestrator_llm_stream') return 'llm_stream'
  if (raw === 'recall_fusion') return 'recall_fusion'
  if (raw.startsWith('retrieval_query_planner')) return 'query_planner'
  if (raw.startsWith('retrieval_agentic_rag')) return 'agentic_rag'
  if (raw.startsWith('retrieval_embedding')) return 'retrieval_embedding'
  if (raw.startsWith('retrieval_evidence_pack')) return 'retrieval_evidence_pack'
  if (raw.startsWith('retrieval_pipeline')) return 'retrieval_pipeline'
  if (raw.startsWith('retrieval_')) return raw
  return raw
}

function getDynamicNodeLabel(rawId = '') {
  if (rawId.startsWith('sub_agent:')) {
    return `子代理 · ${rawId.slice('sub_agent:'.length) || 'unknown'}`
  }
  if (rawId.startsWith('skill:')) {
    return `技能 · ${rawId.slice('skill:'.length) || 'unknown'}`
  }
  return ''
}

export function getTraceNodeDisplayName(nodeOrId, fallback = '') {
  if (nodeOrId && typeof nodeOrId === 'object') {
    const rawId = String(nodeOrId.node_id || nodeOrId.id || '').trim()
    const normalizedId = normalizeTraceNodeKey(rawId)
    if (TRACE_NODE_NAME_BY_ID[normalizedId]) return TRACE_NODE_NAME_BY_ID[normalizedId]

    const rawName = String(nodeOrId.display_name || nodeOrId.label || '').trim()
    if (TRACE_DISPLAY_NAME_ALIASES[rawName]) return TRACE_DISPLAY_NAME_ALIASES[rawName]
    if (rawName) return rawName

    const dynamicLabel = getDynamicNodeLabel(rawId)
    if (dynamicLabel) return dynamicLabel
    return fallback || rawId || TRACE_NODE_TYPE_LABELS.default
  }

  const rawId = String(nodeOrId || '').trim()
  const normalizedId = normalizeTraceNodeKey(rawId)
  return (
    TRACE_NODE_NAME_BY_ID[normalizedId]
    || TRACE_DISPLAY_NAME_ALIASES[rawId]
    || getDynamicNodeLabel(rawId)
    || fallback
    || rawId
    || TRACE_NODE_TYPE_LABELS.default
  )
}

export function getTraceNodeTypeLabel(nodeType = '') {
  const raw = String(nodeType || '').trim().toLowerCase()
  return TRACE_NODE_TYPE_LABELS[raw] || nodeType || TRACE_NODE_TYPE_LABELS.default
}

export function getTraceLayerLabel(layer = '') {
  const raw = String(layer || '').trim().toLowerCase()
  return TRACE_LAYER_LABELS[raw] || layer || ''
}
