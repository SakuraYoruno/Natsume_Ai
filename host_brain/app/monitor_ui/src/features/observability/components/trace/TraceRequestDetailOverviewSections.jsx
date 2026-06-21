import { Database, MessageSquareText } from 'lucide-react'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import {
  TraceMiniStat as MiniStat,
  TraceSectionCard as SectionCard,
} from './TraceSurfaces.jsx'
import {
  AuditSummaryPanel,
  RawJsonDetails,
  RecallSummaryPanel,
  RetrievalRuntimeOverviewPanel,
  TextPreviewCard,
} from './TraceRequestSummaryPanels.jsx'
import {
  formatDateTime,
  formatIntent,
} from './TraceRequestDetailModel.jsx'

export function TraceRequestSummarySection({
  summary,
  inputPreview,
  outputPreview,
}) {
  return (
    <SectionCard
      icon={MessageSquareText}
      title="请求概览"
      description="先看本次输入、输出和决策摘要，确认这条链路在语义层面是否走对。"
      aside={summary.failure_event ? <StatusBadge status="error" label="含失败事件" /> : null}
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <TextPreviewCard label="输入文本" text={inputPreview} emptyLabel="暂无输入文本" />
        <TextPreviewCard
          label={summary.failure_event ? '输出 / 失败摘要' : '输出文本'}
          text={outputPreview}
          emptyLabel="暂无输出文本"
          tone={summary.failure_event ? 'error' : 'default'}
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MiniStat label="意图" value={formatIntent(summary.intent)} />
        <MiniStat label="路由选择" value={summary.routing_selected || '-'} hint={summary.reason_code || '无原因码'} />
        <MiniStat label="Bond 阶段" value={summary.bond_stage || '-'} hint={summary.bond_score != null ? `总分 ${summary.bond_score}` : '暂无分值'} />
        <MiniStat label="LLM 调用" value={summary.llm_calls ?? 0} hint={summary.governor_passed == null ? '未记录 governor 结果' : (summary.governor_passed ? 'Governor 通过' : 'Governor 拦截')} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="space-y-4">
          <div className="text-xs font-medium text-gray-500">召回摘要</div>
          <RecallSummaryPanel recall={summary.recall} />
          <RawJsonDetails title="原始召回摘要" data={summary.recall} emptyLabel="暂无召回摘要" />
        </div>
        <div className="space-y-4">
          <div className="text-xs font-medium text-gray-500">审计与摘要</div>
          <AuditSummaryPanel summary={summary} />
          <RawJsonDetails title="原始摘要详情" data={summary} emptyLabel="暂无摘要" />
        </div>
      </div>
    </SectionCard>
  )
}

export function TraceRetrievalRuntimeSection({
  retrievalMeta,
  retrievalRuntime,
}) {
  return (
    <SectionCard
      icon={Database}
      title="检索与运行时"
      description="这里聚合当前请求实际使用的 embedding 模型、fallback 情况和最近观测值。"
      aside={retrievalMeta.lastObservedAt ? <div className="text-xs text-gray-400">{formatDateTime(retrievalMeta.lastObservedAt)}</div> : null}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-2">
        <MiniStat label="模型套件" value={retrievalMeta.suiteProfile} hint={retrievalMeta.alignment} />
        <MiniStat label="候选数" value={retrievalMeta.candidateCount != null ? `${retrievalMeta.candidateCount}` : '-'} hint="当前请求召回候选量" />
        <MiniStat label="Embedding 模型" value={retrievalMeta.embeddingModel} hint={retrievalMeta.embeddingProvider} toneClassName={retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-600' : 'text-gray-900'} />
        <MiniStat label="Embedding fallback" value={retrievalMeta.embeddingFallbacks} hint={retrievalRuntime?.latest?.fallback_embedding_reason_code || '最近窗口统计'} toneClassName={retrievalMeta.embeddingFallbacks > 0 ? 'text-orange-600' : 'text-gray-900'} />
      </div>

      <div className="mt-4">
        <RetrievalRuntimeOverviewPanel retrievalMeta={retrievalMeta} retrievalRuntime={retrievalRuntime} />
      </div>
    </SectionCard>
  )
}
