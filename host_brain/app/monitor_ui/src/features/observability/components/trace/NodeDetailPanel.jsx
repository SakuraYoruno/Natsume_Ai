import { X } from 'lucide-react'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import JsonBlock from '../../../../shared/ui/JsonBlock.jsx'
import RenderValue from '../../../../shared/ui/RenderValue.jsx'
import { formatDateTime } from '../../../../shared/formatters.js'
import { normalizeBadgeStatus } from './traceFlowModel.js'
import { getTraceLayerLabel, getTraceNodeDisplayName, getTraceNodeTypeLabel } from './traceNodeText.js'

function InfoRow({ label, value }) {
  if (value == null || value === '') return null
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="w-24 shrink-0 text-right text-gray-500">{label}</span>
      <span className="break-all text-gray-800"><RenderValue value={value} /></span>
    </div>
  )
}

function toCompactText(value, maxLength = 96) {
  const text = String(value ?? '').trim().replace(/\s+/g, ' ')
  if (!text) return ''
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text
}

function pushSummaryMetric(metrics, label, value, hint = null) {
  if (value == null || value === '') return
  metrics.push({ label, value, hint })
}

function normalizeTagItems(items, limit = 8) {
  if (!Array.isArray(items)) return []
  return items
    .map((item) => toCompactText(item, 72))
    .filter(Boolean)
    .slice(0, limit)
}

function buildNodeSummaryMetrics(node) {
  const input = (node?.input_data && typeof node.input_data === 'object') ? node.input_data : {}
  const output = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
  const metrics = []

  pushSummaryMetric(metrics, '事件', input.event_type || output.phase)
  pushSummaryMetric(metrics, '意图', output.intent || output.query_plan?.intent)
  pushSummaryMetric(metrics, '候选', output.candidate_count, output.route_count != null ? `路由 ${output.route_count}` : null)
  pushSummaryMetric(metrics, 'TopK', output.topk_count)
  pushSummaryMetric(metrics, '证据', output.evidence_count ?? output.evidence_refs_count, output.pack_id || null)
  pushSummaryMetric(metrics, '情绪', output.emotion_tag, output.mood_state || null)
  pushSummaryMetric(metrics, '动机', output.dominant_motive)
  pushSummaryMetric(
    metrics,
    'Bond',
    output.bond?.stage || output.to_stage || output.from_stage,
    output.bond?.score != null ? `score ${output.bond.score}` : (output.total_score != null ? `score ${output.total_score}` : null),
  )
  pushSummaryMetric(metrics, '策略', output.path || output.route || output.mode || output.decision, output.reason || output.target || null)
  pushSummaryMetric(
    metrics,
    '约束',
    output.max_chars != null ? `${output.max_chars} chars` : null,
    output.max_sentences != null ? `${output.max_sentences} 句` : null,
  )
  pushSummaryMetric(metrics, '首字延迟', input.first_token_ms != null ? `${input.first_token_ms}ms` : null)
  pushSummaryMetric(
    metrics,
    '子代理',
    output.sub_agent_id || output.target,
    output.delegated_agent_count != null ? `delegated ${output.delegated_agent_count}` : null,
  )
  pushSummaryMetric(
    metrics,
    '网关',
    output.gateway_status,
    output.reply_text_len != null ? `reply ${output.reply_text_len} chars` : null,
  )

  return metrics.slice(0, 8)
}

function buildNodeSummaryTags(node) {
  const output = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
  const nestedImmune = (output?.immune_gate && typeof output.immune_gate === 'object') ? output.immune_gate : {}
  const immuneReasonCodes = [
    ...(Array.isArray(output.reason_codes) ? output.reason_codes : []),
    ...(Array.isArray(nestedImmune.reason_codes) ? nestedImmune.reason_codes : []),
    output.reason_code,
    nestedImmune.reason_code,
    node?.reason_code,
  ].filter(Boolean)
  return [
    { label: '目标存储', items: normalizeTagItems(output.target_stores || output.query_plan?.target_stores) },
    { label: '原因标签', items: normalizeTagItems(output.reason_tags || output.policy_reason_tags || output.motive_reason_tags) },
    { label: 'Immune 原因', items: normalizeTagItems(immuneReasonCodes) },
    { label: 'Immune 规则', items: normalizeTagItems(output.matched_rules || nestedImmune.matched_rules) },
    { label: '缺失维度', items: normalizeTagItems(output.final_evaluation?.missing_aspects) },
    { label: '证据引用', items: normalizeTagItems(output.selected_refs || output.evidence_refs) },
  ].filter((group) => group.items.length > 0)
}

function buildNodeSummaryPreview(node) {
  const input = (node?.input_data && typeof node.input_data === 'object') ? node.input_data : {}
  const output = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
  const candidates = [
    output.detail,
    output.final_response,
    output.output_text,
    output.reply_preview,
    output.message,
    output.content,
    output.reason,
    output.error,
    output.final_evaluation?.suggestion,
    input.input_preview,
    input.raw_text,
  ]
  for (const candidate of candidates) {
    const text = toCompactText(candidate, 180)
    if (text) return text
  }
  return ''
}

function SummaryMetricCard({ label, value, hint = null }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="text-[11px] font-medium text-gray-500">{label}</div>
      <div className="mt-1 break-all text-sm font-semibold text-gray-900"><RenderValue value={value} empty="-" /></div>
      {hint ? <div className="mt-1 text-[11px] text-gray-400"><RenderValue value={hint} /></div> : null}
    </div>
  )
}

export default function NodeDetailPanel({ node, onClose }) {
  if (!node) return null

  const duration = node.latency_ms != null
    ? `${node.latency_ms}ms`
    : (node.duration_ms != null ? `${node.duration_ms}ms` : null)

  const timestamp = node.ts_ms ? formatDateTime(node.ts_ms) : null
  const summaryMetrics = buildNodeSummaryMetrics(node)
  const summaryTags = buildNodeSummaryTags(node)
  const summaryPreview = buildNodeSummaryPreview(node)

  return (
    <div className="card flex h-full w-80 shrink-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-gray-800">
            {getTraceNodeDisplayName(node)}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <StatusBadge status={normalizeBadgeStatus(node.status)} />
            <span className="text-xs text-gray-400">{node.node_id}</span>
          </div>
        </div>
        <button className="btn-ghost shrink-0 p-1" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <div className="space-y-1.5">
          <InfoRow label="节点类型" value={getTraceNodeTypeLabel(node.node_type)} />
          <InfoRow label="所属端" value={getTraceLayerLabel(node.layer || node.side)} />
          <InfoRow label="分组" value={node.group_id} />
          <InfoRow label="所属节点" value={node.owner_node_id} />
          <InfoRow label="子图 ID" value={node.subgraph_id} />
          <InfoRow label="父节点" value={node.parent_node_id} />
          <InfoRow label="耗时" value={duration} />
          <InfoRow label="提供方" value={node.provider} />
          <InfoRow label="模型" value={node.model_id} />
          <InfoRow label="原因码" value={node.reason_code} />
          <InfoRow label="时间戳" value={timestamp} />
        </div>

        {node.error ? (
          <div>
            <div className="mb-1 text-xs font-medium text-red-500">错误</div>
            <div className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-600">
              <RenderValue value={node.error} />
            </div>
          </div>
        ) : null}

        {summaryMetrics.length > 0 ? (
          <div>
            <div className="mb-2 text-xs font-medium text-gray-500">摘要</div>
            <div className="grid grid-cols-2 gap-2">
              {summaryMetrics.map((item) => (
                <SummaryMetricCard
                  key={`${item.label}:${item.value}`}
                  label={item.label}
                  value={item.value}
                  hint={item.hint}
                />
              ))}
            </div>
          </div>
        ) : null}

        {summaryPreview ? (
          <div>
            <div className="mb-1 text-xs font-medium text-gray-500">摘要文本</div>
            <div className="rounded border border-gray-200 bg-gray-50 p-2 text-xs leading-6 text-gray-700">
              {summaryPreview}
            </div>
          </div>
        ) : null}

        {summaryTags.length > 0 ? (
          <div className="space-y-3">
            {summaryTags.map((group) => (
              <div key={group.label}>
                <div className="mb-1 text-xs font-medium text-gray-500">{group.label}</div>
                <div className="flex flex-wrap gap-1.5">
                  {group.items.map((item) => (
                    <span key={`${group.label}:${item}`} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-1 text-[11px] text-gray-700">
                      {item}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {(node.input_data != null || node.input != null) ? (
          <div>
            <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-gray-500">
              <span className="inline-block h-2 w-2 rounded-full bg-blue-400" />
              输入
            </div>
            <JsonBlock data={node.input_data ?? node.input} className="max-h-72 rounded border-gray-200" />
          </div>
        ) : null}

        {(node.output_data != null || node.output != null) ? (
          <div>
            <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-gray-500">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
              输出
            </div>
            <JsonBlock data={node.output_data ?? node.output} className="max-h-72 rounded border-gray-200" />
          </div>
        ) : null}
      </div>
    </div>
  )
}
