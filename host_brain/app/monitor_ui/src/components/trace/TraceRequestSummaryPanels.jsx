import React from 'react'
import { TraceMiniStat as MiniStat } from './TraceSurfaces.jsx'
import { safeStringify, truncateText } from './TraceRequestDetailModel.jsx'

export function JsonBlock({ data, emptyLabel = '暂无数据' }) {
  if (data == null || data === '') return <div className="text-xs text-gray-400">{emptyLabel}</div>
  const text = typeof data === 'string' ? data : safeStringify(data)
  return <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-primary-100/70 bg-white/75 p-3 text-xs text-gray-700">{text}</pre>
}

export function TextPreviewCard({ label, text, emptyLabel, tone = 'default' }) {
  const toneClassName = tone === 'error'
    ? 'border-red-200 bg-red-50/65'
    : 'border-primary-100/70 bg-white/72'
  const labelToneClassName = tone === 'error' ? 'text-red-600' : 'text-gray-500'
  return (
    <div className={`rounded-xl border p-4 ${toneClassName}`}>
      <div className={`text-xs font-medium ${labelToneClassName}`}>{label}</div>
      <div className="mt-2 whitespace-pre-wrap break-all text-sm leading-6 text-gray-800">
        {text || <span className="text-gray-400">{emptyLabel}</span>}
      </div>
    </div>
  )
}

export function TagList({ label, items, emptyLabel = '暂无', tone = 'default' }) {
  const values = Array.isArray(items)
    ? items.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  const toneClassName = tone === 'warn'
    ? 'border-amber-200 bg-amber-50 text-amber-700'
    : tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-primary-200 bg-primary-50/65 text-primary-700'
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/82 p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      {values.length === 0 ? (
        <div className="mt-2 text-sm text-gray-400">{emptyLabel}</div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {values.map((item) => (
            <span key={`${label}:${item}`} className={`rounded-full border px-2.5 py-1 text-xs ${toneClassName}`}>
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function RawJsonDetails({ title, data, emptyLabel = '暂无数据' }) {
  return (
    <details className="rounded-xl border border-primary-100/70 bg-white/82">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-gray-700">
        {title}
      </summary>
      <div className="border-t border-primary-100/70 p-4">
        <JsonBlock data={data} emptyLabel={emptyLabel} />
      </div>
    </details>
  )
}

export function extractRecallEvaluation(recall) {
  const stages = Array.isArray(recall?.stages) ? recall.stages : []
  for (const stage of stages) {
    const payload = stage?.payload
    const evaluation = payload?.agentic_meta?.final_evaluation || payload?.final_evaluation
    if (evaluation && typeof evaluation === 'object') return evaluation
  }
  return null
}

export function extractRecallTopItems(recall) {
  if (Array.isArray(recall?.evidence_topk) && recall.evidence_topk.length > 0) return recall.evidence_topk
  if (Array.isArray(recall?.topk) && recall.topk.length > 0) return recall.topk
  if (Array.isArray(recall?.items) && recall.items.length > 0) return recall.items
  return []
}

export function RecallSummaryPanel({ recall }) {
  const evaluation = extractRecallEvaluation(recall)
  const topItems = extractRecallTopItems(recall).slice(0, 4)
  const selectedRefs = Array.isArray(recall?.selected_refs) ? recall.selected_refs : []
  const evidenceRefs = Array.isArray(recall?.evidence_refs) ? recall.evidence_refs : []

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="候选数" value={recall?.candidate_count ?? '-'} hint={recall?.content_available ? '已持久化片段' : '仅阶段载荷'} />
        <MiniStat label="路由数" value={recall?.route_count ?? '-'} hint={recall?.route_error_count != null ? `错误 ${recall.route_error_count}` : '暂无路由错误'} />
        <MiniStat label="证据包" value={(recall?.evidence_refs_count ?? evidenceRefs.length) || '-'} hint={recall?.pack_id || '未生成 pack_id'} />
        <MiniStat label="Rerank TopK" value={recall?.topk_count ?? recall?.rerank_topk ?? '-'} hint={recall?.rerank_provider || '未记录 reranker'} />
        <MiniStat label="覆盖信心" value={evaluation?.confidence != null ? `${Math.round(Number(evaluation.confidence) * 100)}%` : '-'} hint={evaluation?.suggestion || '无建议'} toneClassName={evaluation?.confidence != null && Number(evaluation.confidence) < 0.6 ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="覆盖结论" value={evaluation?.sufficient == null ? '-' : (evaluation.sufficient ? '充分' : '不足')} hint={evaluation?.top_score != null ? `Top score ${evaluation.top_score}` : '未记录 top score'} toneClassName={evaluation?.sufficient === false ? 'text-amber-600' : 'text-gray-900'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="缺失维度" items={evaluation?.missing_aspects} emptyLabel="无缺失项" tone="warn" />
        <TagList label="Selected Refs" items={selectedRefs.length > 0 ? selectedRefs : evidenceRefs} emptyLabel="暂无证据引用" />
      </div>

      <div className="rounded-xl border border-primary-100/70 bg-white/78 p-4">
        <div className="text-xs font-medium text-gray-500">Top 召回片段</div>
        <div className="lifeline-rail mt-2 h-1">
          <div className="lifeline-wave h-full w-full" />
        </div>
        {topItems.length === 0 ? (
          <div className="mt-2 text-sm text-gray-400">暂无可展示片段</div>
        ) : (
          <div className="mt-3 space-y-3">
            {topItems.map((item, index) => (
              <div key={`recall-top-${index}`} className="rounded-lg border border-primary-100/70 bg-white/92 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-blue-700">#{index + 1}</span>
                  {item?.score != null ? <span>score {item.score}</span> : null}
                  {item?.confidence != null ? <span>confidence {item.confidence}</span> : null}
                  {item?.metadata?.store_id ? <span>{item.metadata.store_id}</span> : null}
                  {item?.doc_id ? <span className="font-mono">{item.doc_id}</span> : null}
                </div>
                <div className="mt-2 whitespace-pre-wrap break-all text-sm leading-6 text-gray-700">
                  {truncateText(item?.snippet || item?.text || item?.content || '', 280) || '暂无摘要文本'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function AuditSummaryPanel({ summary }) {
  const nodeTimingEntries = Object.entries(summary?.node_timings_ms || {})
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="任务类型" value={summary?.task_type || '-'} />
        <MiniStat label="决策原因码" value={summary?.reason_code || '-'} hint={summary?.decision || '未记录决策'} />
        <MiniStat label="Governor" value={summary?.governor_passed == null ? '-' : (summary.governor_passed ? '通过' : '拦截')} hint={summary?.airlock_blocked ? 'Airlock 拦截' : 'Airlock 未拦截'} toneClassName={summary?.failure_event ? 'text-red-600' : 'text-gray-900'} />
        <MiniStat label="失败事件" value={summary?.failure_event || '-'} hint={summary?.final_response_chars != null ? `输出 ${summary.final_response_chars} chars` : '未记录输出长度'} toneClassName={summary?.failure_event ? 'text-red-600' : 'text-gray-900'} />
        <MiniStat label="路由选择" value={summary?.routing_selected || '-'} />
        <MiniStat label="审计标签数" value={Array.isArray(summary?.audit_tags) ? summary.audit_tags.length : 0} hint={Array.isArray(summary?.metadata_keys) ? `${summary.metadata_keys.length} 个 metadata key` : '无 metadata key'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="Audit Tags" items={summary?.audit_tags} emptyLabel="暂无审计标签" />
        <TagList label="Emotion Tags" items={summary?.emotion_tags} emptyLabel="暂无情感标签" />
        <TagList label="Metadata Keys" items={summary?.metadata_keys} emptyLabel="暂无 metadata keys" />
        <TagList label="节点计时" items={nodeTimingEntries.map(([key, value]) => `${key}: ${value}ms`)} emptyLabel="暂无节点计时" />
      </div>
    </div>
  )
}

export function RetrievalRuntimeOverviewPanel({ retrievalMeta, retrievalRuntime }) {
  const latest = retrievalMeta?.latest || {}
  const selectedRefs = Array.isArray(latest?.selected_refs) ? latest.selected_refs : []
  const evidenceRefs = Array.isArray(latest?.evidence_refs) ? latest.evidence_refs : []
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="输出状态" value={latest?.output_status || latest?.status || '-'} hint={latest?.query_length != null ? `query length ${latest.query_length}` : '未记录 query_length'} />
        <MiniStat label="Recall 候选" value={latest?.recall_candidates ?? latest?.candidate_count ?? '-'} hint={latest?.recall_candidates_deduped != null ? `去重后 ${latest.recall_candidates_deduped}` : '未记录去重后候选'} />
        <MiniStat label="证据引用" value={latest?.evidence_refs ?? latest?.evidence_refs_count ?? '-'} hint={selectedRefs.length > 0 ? `selected ${selectedRefs.length}` : '未记录 selected refs'} />
        <MiniStat label="Embedding 提供方" value={latest?.effective_embedding_provider || latest?.embedding_provider || retrievalMeta?.embeddingProvider || '-'} hint={latest?.embedding_model_id || retrievalMeta?.embeddingModel || '未记录 embedding model'} toneClassName={latest?.fallback_embedding_used ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="Reranker 提供方" value={latest?.effective_rerank_provider || latest?.rerank_provider || retrievalMeta?.rerankerProvider || '-'} hint={latest?.rerank_model_id || retrievalMeta?.rerankerModel || '未记录 reranker model'} toneClassName={latest?.fallback_reranker_used ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="Fallback" value={(latest?.fallback_embedding_used || latest?.fallback_reranker_used) ? '已发生' : '未发生'} hint={latest?.fallback_embedding_reason_code || latest?.fallback_reranker_reason_code || '无 fallback reason'} toneClassName={(latest?.fallback_embedding_used || latest?.fallback_reranker_used) ? 'text-amber-600' : 'text-gray-900'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="Selected Refs" items={selectedRefs} emptyLabel="暂无 selected refs" />
        <TagList label="Evidence Refs" items={evidenceRefs} emptyLabel="暂无 evidence refs" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RawJsonDetails title="原始运行时快照" data={latest} emptyLabel="暂无本次运行时观测" />
        <RawJsonDetails title="原始运行时总览" data={retrievalRuntime} emptyLabel="暂无检索运行时数据" />
      </div>
    </div>
  )
}
