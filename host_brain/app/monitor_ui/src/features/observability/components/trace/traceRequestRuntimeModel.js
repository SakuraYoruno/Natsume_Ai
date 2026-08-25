import { getTraceNodeDisplayName } from './traceNodeText.js'
import { getNodePreview } from './tracePreviewModel.js'
import {
  normalizeTraceStageNodeId,
  resolveTraceStageGroupId,
  TRACE_FALLBACK_STAGE_META,
  TRACE_STAGE_GROUP_META_BY_ID,
  TRACE_STAGE_GROUPS,
  TRACE_STAGE_OWNER_BY_NODE_ID,
  TRACE_TIMELINE_STAGE_ORDER,
} from './traceStageModel.js'

export { buildImmuneStageMeta } from './traceImmuneModel.js'

export const ISSUE_STATUSES = new Set(['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'])
export const RUNNING_STATUSES = new Set(['RUNNING', 'STARTED'])

export function normalizeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS', 'APPROVE', 'SKIPPED'].includes(value)) return 'healthy'
  if (RUNNING_STATUSES.has(value)) return 'running'
  if (value === 'DEGRADED') return 'degraded'
  if (ISSUE_STATUSES.has(value)) return 'error'
  return 'unknown'
}

function getStatusSeverity(status) {
  const normalized = normalizeStatus(status)
  return {
    error: 4,
    degraded: 3,
    running: 2,
    healthy: 1,
    unknown: 0,
  }[normalized] ?? 0
}

function getStageStatus(nodes) {
  if (!Array.isArray(nodes) || nodes.length === 0) return 'unknown'
  return nodes.reduce((selected, current) => (
    getStatusSeverity(current?.status) > getStatusSeverity(selected?.status) ? current : selected
  )).status
}

function getStagePreview(nodes) {
  const ordered = [...(Array.isArray(nodes) ? nodes : [])].sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const preview = getNodePreview(ordered[index])
    if (preview) return preview
  }
  return ''
}

export function buildStageSnapshots(nodes) {
  const nodeList = Array.isArray(nodes) ? nodes : []
  return TRACE_STAGE_GROUPS.map((group) => {
    const observedNodes = nodeList
      .filter((node) => {
        const groupId = String(node?.group_id || '').trim()
        const normalizedId = normalizeTraceStageNodeId(node)
        const explicitOwner = TRACE_STAGE_OWNER_BY_NODE_ID[normalizedId]
        if (explicitOwner) return explicitOwner === group.id
        return groupId === group.id
      })
      .sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))

    const expectedEntries = group.nodeIds.map((nodeId) => {
      const matchedNodes = observedNodes.filter((node) => normalizeTraceStageNodeId(node) === nodeId)
      const latestNode = matchedNodes[matchedNodes.length - 1] ?? null
      return {
        nodeId,
        label: getTraceNodeDisplayName(nodeId),
        node: latestNode,
        hit: matchedNodes.length > 0,
        status: latestNode?.status ?? 'UNKNOWN',
      }
    })

    const dynamicNodes = observedNodes.filter((node) => !group.nodeIds.includes(normalizeTraceStageNodeId(node)))
    const latestNode = observedNodes[observedNodes.length - 1] ?? null
    return {
      ...group,
      observedNodes,
      expectedEntries,
      dynamicNodes,
      latestNode,
      preview: getStagePreview(observedNodes),
      status: getStageStatus(observedNodes),
    }
  })
}

export function buildStageTimelineSections(nodes) {
  const sectionMap = Object.fromEntries(
    TRACE_TIMELINE_STAGE_ORDER.map((stageId) => [stageId, []])
  )

  ;(Array.isArray(nodes) ? nodes : []).forEach((node) => {
    const stageId = resolveTraceStageGroupId(node)
    if (!sectionMap[stageId]) sectionMap[stageId] = []
    sectionMap[stageId].push(node)
  })

  return TRACE_TIMELINE_STAGE_ORDER
    .map((stageId) => {
      const items = (sectionMap[stageId] || []).sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
      if (items.length === 0) return null
      const stageMeta = TRACE_STAGE_GROUP_META_BY_ID[stageId] || TRACE_FALLBACK_STAGE_META
      const latestNode = items[items.length - 1] ?? null
      const issueCount = items.filter((node) => ISSUE_STATUSES.has(String(node?.status || '').toUpperCase())).length
      return {
        stageId,
        label: stageMeta.label,
        description: stageMeta.description,
        icon: stageMeta.icon,
        nodes: items,
        latestNode,
        issueCount,
      }
    })
    .filter(Boolean)
}

export function buildRetrievalRuntimeMeta(retrievalRuntime) {
  const latest = retrievalRuntime?.latest ?? null
  return {
    embeddingModel: latest?.embedding_model_id || '未观测到',
    embeddingProvider:
      latest?.effective_embedding_provider
      || latest?.embedding_provider
      || '-',
    candidateCount: latest?.candidate_count ?? null,
    embeddingFallbacks: retrievalRuntime?.recent_embedding_fallbacks ?? 0,
    lastObservedAt: latest?.ts_ms ?? retrievalRuntime?.timestamp ?? null,
    latest,
  }
}
