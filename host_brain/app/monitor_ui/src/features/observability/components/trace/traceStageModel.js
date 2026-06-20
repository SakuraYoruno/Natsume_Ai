import {
  Activity,
  Bot,
  Database,
  Filter,
  Layers3,
  ShieldAlert,
  Sparkles,
  Wand2,
} from 'lucide-react'
import { normalizeTraceNodeKey } from './traceNodeText.js'

export const TRACE_STAGE_GROUPS = Object.freeze([
  Object.freeze({
    id: 'edge',
    label: '边缘输入',
    description: '网关接收、转发与完成回写等边缘事件。',
    icon: Activity,
    nodeIds: Object.freeze(['asr', 'gateway_webhook', 'input_reporter']),
  }),
  Object.freeze({
    id: 'entry',
    label: '入口守护',
    description: '输入进入 Host 后的净化与 Governor 判定。',
    icon: ShieldAlert,
    nodeIds: Object.freeze(['host_input', 'airlock_ingress', 'governor']),
  }),
  Object.freeze({
    id: 'immune',
    label: '免疫门禁',
    description: 'Immune Gate 风险扫描与拦截/报告决策。',
    icon: ShieldAlert,
    nodeIds: Object.freeze(['immune_gate']),
  }),
  Object.freeze({
    id: 'retrieval',
    label: '检索规划',
    description: 'Query Planner、Agentic RAG 与 Recall Fusion 的检索收口。',
    icon: Filter,
    nodeIds: Object.freeze(['query_planner', 'agentic_rag', 'recall_fusion', 'retrieval_embedding', 'retrieval_evidence_pack', 'retrieval_pipeline']),
  }),
  Object.freeze({
    id: 'persona',
    label: '人格上下文',
    description: '情感、人设常驻记忆与动机状态的注入结果。',
    icon: Sparkles,
    nodeIds: Object.freeze(['emotion_context', 'persona_resident_memory', 'motive_state']),
  }),
  Object.freeze({
    id: 'orchestration',
    label: '编排决策',
    description: '响应策略与 Cortex Orchestrator 的主链路判断、路由结果。',
    icon: Bot,
    nodeIds: Object.freeze(['render_policy', 'cortex_orchestrator']),
  }),
  Object.freeze({
    id: 'agent',
    label: '代理调度',
    description: 'Executive Router、各类 Sub Agent 与 Surface Reply 的实际执行。',
    icon: Layers3,
    nodeIds: Object.freeze(['executive_router', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'surface_reply']),
  }),
  Object.freeze({
    id: 'llm',
    label: '提示与模型',
    description: 'Prompt Builder 与 LLM Stream 的最终出文过程。',
    icon: Wand2,
    nodeIds: Object.freeze(['prompt_builder', 'llm_stream', 'llm_gateway', 'cortex_orchestrator_llm_stream']),
  }),
  Object.freeze({
    id: 'postprocess',
    label: '收口输出',
    description: '出口净化、记忆/Bond 写入与 TTS/VTS 收尾。',
    icon: Database,
    nodeIds: Object.freeze(['airlock_egress', 'pii_filter', 'episodic_store', 'bond_update', 'audit_snapshot', 'tts', 'vts_driver']),
  }),
])

export const TRACE_STAGE_OWNER_BY_NODE_ID = Object.freeze(Object.fromEntries(
  TRACE_STAGE_GROUPS.flatMap((group) => group.nodeIds.map((nodeId) => [nodeId, group.id]))
))

export const TRACE_STAGE_GROUP_META_BY_ID = Object.freeze(Object.fromEntries(
  TRACE_STAGE_GROUPS.map((group) => [group.id, group])
))

export const TRACE_TIMELINE_STAGE_ORDER = Object.freeze([...TRACE_STAGE_GROUPS.map((group) => group.id), 'other'])

export const TRACE_FALLBACK_STAGE_META = Object.freeze({
  label: '其他节点',
  description: '未归入主链阶段的节点。',
  icon: Layers3,
})

export const TRACE_STABLE_STAGE_LABELS = Object.freeze({
  edge: '边缘输入',
  entry: '入口守护',
  immune: '免疫门禁',
  retrieval: '检索规划',
  persona: '人格上下文',
  orchestration: '编排决策',
  agent: '代理调度',
  llm: '提示与模型',
  postprocess: '收口输出',
  other: '其他',
})

export function normalizeTraceStageNodeId(node) {
  if (!node) return ''
  const rawId = String(node?.node_id || node?.id || '').trim()
  const normalizedId = normalizeTraceNodeKey(rawId)
  if (normalizedId.startsWith('sub_agent:code')) return 'sub_agent_code'
  if (normalizedId.startsWith('sub_agent:translate')) return 'sub_agent_translate'
  if (normalizedId.startsWith('sub_agent:intent')) return 'sub_agent_intent'
  if (normalizedId.startsWith('sub_agent:vision')) return 'sub_agent_vision'
  if (normalizedId.startsWith('sub_agent:')) return 'sub_agent_intent'
  if (normalizedId.startsWith('bond_update')) return 'bond_update'
  if (normalizedId.startsWith('episodic_store')) return 'episodic_store'
  return normalizedId
}

export function resolveTraceStageGroupId(node) {
  const normalizedId = normalizeTraceStageNodeId(node)
  const explicitOwner = TRACE_STAGE_OWNER_BY_NODE_ID[normalizedId]
  if (explicitOwner) return explicitOwner
  const groupId = String(node?.group_id || '').trim()
  if (TRACE_STAGE_GROUP_META_BY_ID[groupId]) return groupId
  const side = String(node?.side || node?.layer || '').trim().toLowerCase()
  if (side === 'edge') return 'edge'
  return 'other'
}

export function getStableStageLabel(stageId) {
  return TRACE_STABLE_STAGE_LABELS[stageId] || stageId || TRACE_STABLE_STAGE_LABELS.other
}
