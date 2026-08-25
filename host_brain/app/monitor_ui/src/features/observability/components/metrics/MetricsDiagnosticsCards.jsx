import { Activity, Archive, BookLock, FileCode, GitMerge, Layers, ScrollText, Search } from 'lucide-react'
import InlineQueryMeta from '../../../../shared/ui/observability/InlineQueryMeta.jsx'
import {
  buildCanonProposalPendingBadge,
  buildMetricDiagnosticRateBar,
  buildMetricDiagnosticStats,
  buildSemanticRecallBackendLine,
  buildSemanticRecallStatusBadge,
  getMetricDiagnosticSampleKeys,
  resolveMetricQueryLoadState,
  shouldShowSemanticRecallMockWarning,
} from '../../metricsDiagnosticsModel.js'
import {
  useCanonProposalStatsQuery,
  useChainStatsQuery,
  useDistillationStatsQuery,
  useEpisodicStoreStatsQuery,
  useMemoryGuardStatsQuery,
  usePromptBuilderStatsQuery,
  useSemanticRecallStatsQuery,
  useStateArbiterStatsQuery,
} from '../../diagnosticQueries.js'

function MiniCardMeta({ queryState, windowMinutes, metricKind }) {
  return (
    <InlineQueryMeta
      loadState={resolveMetricQueryLoadState(queryState, getMetricDiagnosticSampleKeys(metricKind))}
      loadStateDetail={queryState?.queryError}
      updatedAt={queryState?.lastUpdatedAt}
      windowMinutes={windowMinutes}
    />
  )
}

function MetricStatsGrid({ items }) {
  return (
    <div className="grid grid-cols-4 gap-3 text-center">
      {items.map(({ label, value, color }) => (
        <div key={label}>
          <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
          <div className="text-xs text-gray-400 mt-0.5">{label}</div>
        </div>
      ))}
    </div>
  )
}

function MetricRateBar({ bar }) {
  if (!bar) return null
  return (
    <div className="mt-3">
      <div className="flex justify-between text-xs text-gray-400 mb-1">
        <span>{bar.label}</span>
        <span className={bar.valueClassName}>{bar.valueLabel}</span>
      </div>
      <div className="bg-gray-100 rounded-full h-1.5">
        <div className={`h-1.5 rounded-full ${bar.barClassName}`} style={{ width: bar.width }} />
      </div>
    </div>
  )
}

function MemoryGuardMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useMemoryGuardStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('memoryGuard', data)
  const rateBar = buildMetricDiagnosticRateBar('memoryGuard', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <BookLock size={14} className="text-emerald-600" />
        <span className="text-sm font-semibold text-gray-700">记忆守卫规则校验（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} metricKind="memoryGuard" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

function StateArbiterMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useStateArbiterStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('stateArbiter', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <GitMerge size={14} className="text-cyan-600" />
        <span className="text-sm font-semibold text-gray-700">状态仲裁关系查询（近 1h）</span>
        <span className="text-xs text-gray-400">仅关系图快照</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} metricKind="stateArbiter" />
      <MetricStatsGrid items={stats} />
    </div>
  )
}

function PromptBuilderMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = usePromptBuilderStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('promptBuilder', data)
  const rateBar = buildMetricDiagnosticRateBar('promptBuilder', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <FileCode size={14} className="text-violet-600" />
        <span className="text-sm font-semibold text-gray-700">提示构建安全（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} metricKind="promptBuilder" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

function EpisodicStoreMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useEpisodicStoreStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('episodicStore', data)
  const rateBar = buildMetricDiagnosticRateBar('episodicStore', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Archive size={14} className="text-amber-600" />
        <span className="text-sm font-semibold text-gray-700">情景记忆写入（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} metricKind="episodicStore" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

function CanonProposalMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useCanonProposalStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('canonProposal', data)
  const rateBar = buildMetricDiagnosticRateBar('canonProposal', data)
  const pendingBadge = buildCanonProposalPendingBadge(data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <ScrollText size={14} className="text-indigo-600" />
        <span className="text-sm font-semibold text-gray-700">主设定提案（全量）</span>
        {pendingBadge ? (
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ml-auto ${pendingBadge.className}`}>
            {pendingBadge.label}
          </span>
        ) : null}
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} metricKind="canonProposal" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

function DistillationMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useDistillationStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('distillation', data)
  const rateBar = buildMetricDiagnosticRateBar('distillation', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Layers size={14} className="text-purple-600" />
        <span className="text-sm font-semibold text-gray-700">情节记忆蒸馏（近 24h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} metricKind="distillation" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

function SemanticRecallMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useSemanticRecallStatsQuery({ pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const stats = buildMetricDiagnosticStats('semanticRecall', data)
  const statusBadge = buildSemanticRecallStatusBadge(data)
  const backendLine = buildSemanticRecallBackendLine(data)
  const showMockWarning = shouldShowSemanticRecallMockWarning(data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Search size={14} className="text-sky-600" />
        <span className="text-sm font-semibold text-gray-700">语义召回</span>
        {statusBadge ? (
          <span className={`ml-auto text-xs px-1.5 py-0.5 rounded border font-medium ${statusBadge.className}`}>
            {statusBadge.label}
          </span>
        ) : null}
      </div>
      <MiniCardMeta queryState={queryState} metricKind="semanticRecall" />
      <MetricStatsGrid items={stats} />
      {backendLine ? (
        <div className="mt-2 text-xs text-gray-400">
          {backendLine}
        </div>
      ) : null}
      {showMockWarning ? (
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
  const stats = buildMetricDiagnosticStats('chain', data)
  const rateBar = buildMetricDiagnosticRateBar('chain', data)

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={14} className="text-blue-600" />
        <span className="text-sm font-semibold text-gray-700">请求链路统计（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} metricKind="chain" />
      <MetricStatsGrid items={stats} />
      <MetricRateBar bar={rateBar} />
    </div>
  )
}

export default function MetricsDiagnosticsCards({ pollingEnabled = false, pollingInterval = 30 }) {
  return (
    <>
      <MemoryGuardMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <StateArbiterMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <PromptBuilderMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <EpisodicStoreMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <CanonProposalMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <ChainMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <DistillationMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <SemanticRecallMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
    </>
  )
}
