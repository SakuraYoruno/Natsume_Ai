import { resolveLoadState } from '../../models/loadState.js'

export const METRIC_DIAGNOSTIC_SAMPLE_KEYS = {
  memoryGuard: ['total_checks', 'blocked_count', 'top_reject_reasons'],
  stateArbiter: ['total_resolutions', 'top_sources'],
  promptBuilder: ['total_builds', 'degraded_count', 'high_risk_count'],
  episodicStore: ['total_attempts', 'written_count', 'memory_guard_blocked_count'],
  canonProposal: ['total', 'pending', 'recent_proposals'],
  distillation: ['total_candidates', 'distilled_count', 'failed_count'],
  semanticRecall: ['status', 'top_k'],
  chain: ['total_requests', 'episodic_written_count', 'qa_rewrite_count'],
}

export function getMetricDiagnosticSampleKeys(kind) {
  return METRIC_DIAGNOSTIC_SAMPLE_KEYS[kind] || []
}

export function hasMetricSamples(data, sampleKeys = []) {
  if (!data) return false
  if (sampleKeys.length === 0) return true

  return sampleKeys.some((key) => {
    const value = data?.[key]
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'number') return value > 0
    return value != null
  })
}

export function resolveMetricQueryLoadState(queryState, sampleKeys = []) {
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

function clampPercent(value) {
  return `${Math.min(Number(value || 0), 100)}%`
}

export function buildMetricDiagnosticStats(kind, data = {}) {
  switch (kind) {
    case 'memoryGuard':
      return [
        { label: '总校验', value: data?.total_checks, color: 'text-gray-700' },
        { label: '通过', value: data?.passed_count, color: 'text-green-600' },
        { label: '拦截', value: data?.blocked_count, color: 'text-red-600' },
        { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
      ]
    case 'stateArbiter':
      return [
        { label: '总查询', value: data?.total_resolutions, color: 'text-gray-700' },
        { label: '冲突', value: data?.conflict_count, color: 'text-red-600' },
        { label: '降级', value: data?.fallback_count, color: 'text-orange-500' },
        { label: '关系快照', value: data?.session_override_count, color: 'text-blue-600' },
      ]
    case 'promptBuilder':
      return [
        { label: '总构建', value: data?.total_builds, color: 'text-gray-700' },
        {
          label: '注入检测',
          value: data?.injection_detected_count,
          color: data?.injection_detected_count > 0 ? 'text-red-600' : 'text-green-600',
        },
        {
          label: '高风险',
          value: data?.high_risk_count,
          color: data?.high_risk_count > 0 ? 'text-orange-500' : 'text-gray-400',
        },
        { label: '降级', value: data?.degraded_count, color: 'text-orange-500' },
      ]
    case 'episodicStore':
      return [
        { label: '总尝试', value: data?.total_attempts, color: 'text-gray-700' },
        { label: '写入成功', value: data?.written_count, color: 'text-green-600' },
        {
          label: '记忆守卫拦截',
          value: data?.memory_guard_blocked_count,
          color: data?.memory_guard_blocked_count > 0 ? 'text-red-600' : 'text-gray-400',
        },
        { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
      ]
    case 'canonProposal':
      return [
        { label: '总提案', value: data?.total, color: 'text-gray-700' },
        { label: '待审核', value: data?.pending, color: data?.pending > 0 ? 'text-orange-500' : 'text-gray-400' },
        { label: '已通过', value: data?.applied, color: 'text-green-600' },
        { label: '已拒绝', value: data?.rejected, color: 'text-red-600' },
      ]
    case 'distillation':
      return [
        { label: '总候选', value: data?.total_candidates, color: 'text-gray-700' },
        { label: '蒸馏成功', value: data?.distilled_count, color: 'text-green-600' },
        { label: '跳过', value: data?.skipped_count, color: 'text-gray-400' },
        { label: '失败', value: data?.failed_count, color: data?.failed_count > 0 ? 'text-red-600' : 'text-gray-400' },
      ]
    case 'semanticRecall':
      return [
        { label: '召回上限', value: data?.top_k, color: 'text-gray-700' },
        {
          label: '相似度阈值',
          value: data?.similarity_threshold != null ? data.similarity_threshold.toFixed(2) : '—',
          color: 'text-sky-600',
        },
        { label: '兜底文档', value: data?.mock_doc_count, color: data?.use_mock ? 'text-orange-500' : 'text-gray-400' },
        { label: '模式', value: data?.use_mock ? '兜底' : 'Chroma', color: data?.use_mock ? 'text-orange-500' : 'text-green-600' },
      ]
    case 'chain':
      return [
        { label: '总请求', value: data?.total_requests, color: 'text-gray-700' },
        {
          label: '入口守护拦截率',
          value: data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—',
          color: data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700',
        },
        { label: '记忆写入', value: data?.episodic_written_count, color: 'text-green-600' },
        { label: 'QA 重写', value: data?.qa_rewrite_count, color: 'text-orange-500' },
      ]
    default:
      return []
  }
}

export function buildMetricDiagnosticRateBar(kind, data = {}) {
  switch (kind) {
    case 'memoryGuard':
      if (data?.block_rate == null) return null
      return {
        label: '拦截率',
        valueLabel: `${data.block_rate}%`,
        valueClassName: data.block_rate > 20 ? 'text-red-500 font-medium' : 'text-green-600',
        barClassName: 'bg-emerald-400',
        width: clampPercent(data.block_rate),
      }
    case 'promptBuilder':
      if (data?.injection_detection_rate == null) return null
      return {
        label: '注入检测率',
        valueLabel: `${data.injection_detection_rate}%`,
        valueClassName: data.injection_detection_rate > 5 ? 'text-red-500 font-medium' : 'text-green-600 font-medium',
        barClassName: data.injection_detection_rate > 5 ? 'bg-red-400' : 'bg-violet-400',
        width: clampPercent(data.injection_detection_rate),
      }
    case 'episodicStore':
      if (data?.write_rate == null) return null
      return {
        label: '写入率',
        valueLabel: `${data.write_rate}%`,
        valueClassName: data.write_rate < 50 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium',
        barClassName: 'bg-amber-400',
        width: clampPercent(data.write_rate),
      }
    case 'canonProposal':
      if (data?.approval_rate == null) return null
      return {
        label: '通过率',
        valueLabel: `${data.approval_rate}%`,
        valueClassName: data.approval_rate >= 70 ? 'text-green-600 font-medium' : 'text-orange-500 font-medium',
        barClassName: 'bg-indigo-400',
        width: clampPercent(data.approval_rate),
      }
    case 'distillation':
      if (data?.success_rate == null) return null
      return {
        label: '蒸馏成功率',
        valueLabel: `${data.success_rate}%`,
        valueClassName: data.success_rate < 70 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium',
        barClassName: 'bg-purple-400',
        width: clampPercent(data.success_rate),
      }
    case 'chain':
      if (data?.governor_block_rate == null) return null
      return {
        label: '入口守护拦截率',
        valueLabel: `${data.governor_block_rate}%`,
        valueClassName: data.governor_block_rate > 10 ? 'text-red-500 font-medium' : 'text-green-600 font-medium',
        barClassName: data.governor_block_rate > 10 ? 'bg-red-400' : 'bg-blue-400',
        width: clampPercent(data.governor_block_rate),
      }
    default:
      return null
  }
}

export function buildSemanticRecallStatusBadge(data) {
  if (!data) return null
  const isOk = data.status === 'OK'
  return {
    label: isOk ? '正常' : '降级',
    className: isOk
      ? 'bg-green-50 text-green-700 border-green-200'
      : 'bg-orange-50 text-orange-700 border-orange-200',
  }
}

export function buildCanonProposalPendingBadge(data) {
  if ((data?.pending ?? 0) <= 0) return null
  return {
    label: `${data.pending} 待审核`,
    className: 'bg-orange-100 text-orange-600',
  }
}

export function buildSemanticRecallBackendLine(data) {
  if (!data || data.use_mock) return ''
  return `ChromaDB: ${data.chroma_host}:${data.chroma_port} · 集合: ${data.collection_name}`
}

export function shouldShowSemanticRecallMockWarning(data) {
  return Boolean(data?.use_mock)
}
