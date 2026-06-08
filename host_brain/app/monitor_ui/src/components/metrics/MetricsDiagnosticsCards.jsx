import React from 'react'
import { Activity, Archive, BookLock, FileCode, GitMerge, Layers, Minimize2, ScrollText, Search } from 'lucide-react'
import InlineQueryMeta from '../obs/InlineQueryMeta.jsx'
import { resolveLoadState } from '../../models/loadState.js'
import {
  useCanonProposalStatsQuery,
  useChainStatsQuery,
  useContextCompressorStatsQuery,
  useDistillationStatsQuery,
  useEpisodicStoreStatsQuery,
  useMemoryGuardStatsQuery,
  usePromptBuilderStatsQuery,
  useSemanticRecallStatsQuery,
  useStateArbiterStatsQuery,
} from '../../queries/diagnosticQueries.js'

function hasMetricSamples(data, sampleKeys = []) {
  if (!data) return false
  if (sampleKeys.length === 0) return true

  return sampleKeys.some((key) => {
    const value = data?.[key]
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'number') return value > 0
    return value != null
  })
}

function resolveMetricQueryLoadState(queryState, sampleKeys = []) {
  const data = queryState?.statsData ?? null
  return resolveLoadState({
    loading: queryState?.loading || queryState?.refreshing,
    error: queryState?.queryError,
    hasData: Boolean(data),
    hasSamples: hasMetricSamples(data, sampleKeys),
    available: true,
    stale: queryState?.stale,
  })
}

function MiniCardMeta({ queryState, windowMinutes, sampleKeys = [] }) {
  return (
    <InlineQueryMeta
      loadState={resolveMetricQueryLoadState(queryState, sampleKeys)}
      loadStateDetail={queryState?.queryError}
      updatedAt={queryState?.lastUpdatedAt}
      windowMinutes={windowMinutes}
    />
  )
}

function MemoryGuardMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useMemoryGuardStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <BookLock size={14} className="text-emerald-600" />
        <span className="text-sm font-semibold text-gray-700">记忆守卫规则校验（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_checks', 'blocked_count', 'top_reject_reasons']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总校验', value: data?.total_checks, color: 'text-gray-700' },
          { label: '通过', value: data?.passed_count, color: 'text-green-600' },
          { label: '拦截', value: data?.blocked_count, color: 'text-red-600' },
          { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.block_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>拦截率</span>
            <span className={data.block_rate > 20 ? 'text-red-500 font-medium' : 'text-green-600'}>{data.block_rate}%</span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-emerald-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.block_rate, 100)}%` }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function StateArbiterMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useStateArbiterStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <GitMerge size={14} className="text-cyan-600" />
        <span className="text-sm font-semibold text-gray-700">状态仲裁关系查询（近 1h）</span>
        <span className="text-xs text-gray-400">仅关系图快照</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_resolutions', 'top_sources']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总查询', value: data?.total_resolutions, color: 'text-gray-700' },
          { label: '冲突', value: data?.conflict_count, color: 'text-red-600' },
          { label: '降级', value: data?.fallback_count, color: 'text-orange-500' },
          { label: '关系快照', value: data?.session_override_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ContextCompressorMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useContextCompressorStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Minimize2 size={14} className="text-teal-600" />
        <span className="text-sm font-semibold text-gray-700">上下文压缩效率（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_compressions', 'summary_generated_count', 'total_tokens_saved']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总压缩', value: data?.total_compressions, color: 'text-gray-700' },
          { label: '活跃', value: data?.active_count, color: 'text-green-600' },
          { label: '节省词元', value: data?.total_tokens_saved, color: 'text-teal-600' },
          { label: '摘要生成', value: data?.summary_generated_count, color: 'text-indigo-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.avg_compression_ratio != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>平均压缩率</span>
            <span className="text-teal-600 font-medium">{(data.avg_compression_ratio * 100).toFixed(1)}%</span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-teal-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.avg_compression_ratio * 100, 100)}%` }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function PromptBuilderMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = usePromptBuilderStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <FileCode size={14} className="text-violet-600" />
        <span className="text-sm font-semibold text-gray-700">提示构建安全（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_builds', 'degraded_count', 'high_risk_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总构建', value: data?.total_builds, color: 'text-gray-700' },
          { label: '注入检测', value: data?.injection_detected_count, color: data?.injection_detected_count > 0 ? 'text-red-600' : 'text-green-600' },
          { label: '高风险', value: data?.high_risk_count, color: data?.high_risk_count > 0 ? 'text-orange-500' : 'text-gray-400' },
          { label: '降级', value: data?.degraded_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.injection_detection_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>注入检测率</span>
            <span className={data.injection_detection_rate > 5 ? 'text-red-500 font-medium' : 'text-green-600 font-medium'}>
              {data.injection_detection_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full ${data.injection_detection_rate > 5 ? 'bg-red-400' : 'bg-violet-400'}`}
              style={{ width: `${Math.min(data.injection_detection_rate, 100)}%` }}
            />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function EpisodicStoreMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useEpisodicStoreStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Archive size={14} className="text-amber-600" />
        <span className="text-sm font-semibold text-gray-700">情景记忆写入（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_attempts', 'written_count', 'memory_guard_blocked_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总尝试', value: data?.total_attempts, color: 'text-gray-700' },
          { label: '写入成功', value: data?.written_count, color: 'text-green-600' },
          { label: '记忆守卫拦截', value: data?.memory_guard_blocked_count, color: data?.memory_guard_blocked_count > 0 ? 'text-red-600' : 'text-gray-400' },
          { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.write_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>写入率</span>
            <span className={data.write_rate < 50 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium'}>
              {data.write_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-amber-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.write_rate, 100)}%` }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function CanonProposalMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useCanonProposalStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <ScrollText size={14} className="text-indigo-600" />
        <span className="text-sm font-semibold text-gray-700">主设定提案（全量）</span>
        {(data?.pending ?? 0) > 0 ? (
          <span className="text-xs bg-orange-100 text-orange-600 px-1.5 py-0.5 rounded font-medium ml-auto">
            {data.pending} 待审核
          </span>
        ) : null}
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} sampleKeys={['total', 'pending', 'recent_proposals']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总提案', value: data?.total, color: 'text-gray-700' },
          { label: '待审核', value: data?.pending, color: data?.pending > 0 ? 'text-orange-500' : 'text-gray-400' },
          { label: '已通过', value: data?.applied, color: 'text-green-600' },
          { label: '已拒绝', value: data?.rejected, color: 'text-red-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.approval_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>通过率</span>
            <span className={data.approval_rate >= 70 ? 'text-green-600 font-medium' : 'text-orange-500 font-medium'}>
              {data.approval_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-indigo-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.approval_rate, 100)}%` }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function DistillationMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useDistillationStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Layers size={14} className="text-purple-600" />
        <span className="text-sm font-semibold text-gray-700">情节记忆蒸馏（近 24h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} sampleKeys={['total_candidates', 'distilled_count', 'failed_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总候选', value: data?.total_candidates, color: 'text-gray-700' },
          { label: '蒸馏成功', value: data?.distilled_count, color: 'text-green-600' },
          { label: '跳过', value: data?.skipped_count, color: 'text-gray-400' },
          { label: '失败', value: data?.failed_count, color: data?.failed_count > 0 ? 'text-red-600' : 'text-gray-400' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.success_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>蒸馏成功率</span>
            <span className={data.success_rate < 70 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium'}>
              {data.success_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-purple-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.success_rate, 100)}%` }} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function SemanticRecallMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useSemanticRecallStatsQuery({ pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const isOk = data?.status === 'OK'

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Search size={14} className="text-sky-600" />
        <span className="text-sm font-semibold text-gray-700">语义召回</span>
        {data ? (
          <span className={`ml-auto text-xs px-1.5 py-0.5 rounded border font-medium ${
            isOk
              ? 'bg-green-50 text-green-700 border-green-200'
              : 'bg-orange-50 text-orange-700 border-orange-200'
          }`}>
            {isOk ? '正常' : '降级'}
          </span>
        ) : null}
      </div>
      <MiniCardMeta queryState={queryState} sampleKeys={['status', 'top_k']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '召回上限', value: data?.top_k, color: 'text-gray-700' },
          { label: '相似度阈值', value: data?.similarity_threshold != null ? data.similarity_threshold.toFixed(2) : '—', color: 'text-sky-600' },
          { label: '兜底文档', value: data?.mock_doc_count, color: data?.use_mock ? 'text-orange-500' : 'text-gray-400' },
          { label: '模式', value: data?.use_mock ? '兜底' : 'Chroma', color: data?.use_mock ? 'text-orange-500' : 'text-green-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data && !data.use_mock ? (
        <div className="mt-2 text-xs text-gray-400">
          ChromaDB: {data.chroma_host}:{data.chroma_port} · 集合: {data.collection_name}
        </div>
      ) : null}
      {data?.use_mock ? (
        <div className="mt-2 text-xs text-orange-400">
          当前使用兜底后端，生产环境请设置 <span className="font-mono">SEMANTIC_RECALL_USE_MOCK=false</span>
        </div>
      ) : null}
    </div>
  )
}

function ChainMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useChainStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={14} className="text-blue-600" />
        <span className="text-sm font-semibold text-gray-700">请求链路统计（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_requests', 'episodic_written_count', 'qa_rewrite_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总请求', value: data?.total_requests, color: 'text-gray-700' },
          { label: '入口守护拦截率', value: data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—', color: data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700' },
          { label: '记忆写入', value: data?.episodic_written_count, color: 'text-green-600' },
          { label: 'QA 重写', value: data?.qa_rewrite_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.governor_block_rate != null ? (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>入口守护拦截率</span>
            <span className={data.governor_block_rate > 10 ? 'text-red-500 font-medium' : 'text-green-600 font-medium'}>
              {data.governor_block_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full ${data.governor_block_rate > 10 ? 'bg-red-400' : 'bg-blue-400'}`}
              style={{ width: `${Math.min(data.governor_block_rate, 100)}%` }}
            />
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function MetricsDiagnosticsCards({ pollingEnabled = false, pollingInterval = 30 }) {
  return (
    <>
      <MemoryGuardMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <StateArbiterMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <ContextCompressorMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <PromptBuilderMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <EpisodicStoreMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <CanonProposalMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <ChainMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <DistillationMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <SemanticRecallMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
    </>
  )
}
