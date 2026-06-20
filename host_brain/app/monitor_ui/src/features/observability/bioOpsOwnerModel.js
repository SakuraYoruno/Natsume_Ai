const asArray = (value) => (Array.isArray(value) ? value : [])

const asCounts = (value) => (value && typeof value === 'object' ? value : {})

const toCount = (value) => Number(value || 0)

export function getBioOpsOwnerRunParams(runId) {
  const id = String(runId || '').trim()
  return id ? { run_id: id } : {}
}

export function getBioOpsPendingAdoptionReview({ items, selectedId, detail } = {}) {
  const rows = asArray(items)
  const activeRow = rows.find((item) => item?.candidate_id === selectedId) || rows[0] || null
  const activeDetail = detail?.adoption?.candidate_id === activeRow?.candidate_id ? detail.adoption : null
  const active = activeDetail || activeRow
  return {
    rows,
    activeRow,
    activeDetail,
    active,
    activeArtifacts: asArray(active?.artifacts),
    activeDecision: active?.decision || null,
  }
}

export function buildBioOpsAdoptionDecisionBody(decision, adoption = {}) {
  return {
    owner_organ: adoption?.owner_organ,
    operator: 'monitor_ui',
    reason: `${decision} from Codex BioOps owner review panel`,
  }
}

export function buildBioOpsOwnerStateReviewBody(decision, ownerOrgan) {
  return {
    owner_organ: ownerOrgan,
    operator: 'monitor_ui',
    reason: decision === 'accept' ? 'owner accepted runtime hint' : 'owner rejected runtime hint',
  }
}

export function buildBioOpsOwnerInboxViewModel({
  data,
  imports,
  queues,
  ownerState,
  ownerRuntimeHints,
  lastPackets,
} = {}) {
  const rows = asArray(data?.items)
  const queueRows = asArray(queues?.items)
  const stateRows = asArray(ownerState?.items)
  const hintRows = asArray(ownerRuntimeHints?.items)
  const counts = asCounts(data?.status_counts)
  const importCounts = asCounts(imports?.status_counts)
  const queueCounts = asCounts(queues?.status_counts)
  const stateCounts = asCounts(ownerState?.status_counts)
  const reviewCounts = asCounts(ownerState?.review_status_counts)
  const importReadyCount = toCount(importCounts.READY_FOR_ORGAN_CONSUMPTION)
  const packetCount = toCount(lastPackets?.packet_count)

  return {
    rows,
    queueRows,
    stateRows,
    hintRows,
    counts,
    importCounts,
    queueCounts,
    stateCounts,
    reviewCounts,
    ownerStateCount: stateCounts.IMPORTED_FOR_OWNER_REVIEW || ownerState?.count || 0,
    runtimeHintCount: ownerRuntimeHints?.count || 0,
    canMaterializeReady: toCount(counts.READY_FOR_OWNER_IMPORT) > 0,
    canMaterializePackets: importReadyCount > 0,
    canConsumePackets: Boolean(packetCount || importReadyCount),
    canImportOwnerState: toCount(queueCounts.QUEUED_FOR_OWNER) > 0,
    inboxStatusTiles: ['READY_FOR_OWNER_IMPORT', 'PENDING_REVIEW', 'REJECTED_BY_OWNER', 'BLOCKED'].map((status) => ({
      status,
      value: counts[status] || 0,
    })),
    importSummaryTiles: [
      {
        key: 'import-ready',
        label: 'Import-ready',
        value: importReadyCount,
        containerClassName: 'border-indigo-100 bg-indigo-50',
        labelClassName: 'text-indigo-700',
      },
      {
        key: 'import-blocked',
        label: 'Import blocked',
        value: importCounts.BLOCKED || 0,
        containerClassName: 'border-red-100 bg-red-50',
        labelClassName: 'text-red-700',
      },
      {
        key: 'organ-packets',
        label: 'Organ packets',
        value: packetCount,
        containerClassName: 'border-gray-100 bg-gray-50',
        labelClassName: 'text-gray-700',
      },
      {
        key: 'owner-queues',
        label: 'Owner queues',
        value: queueCounts.QUEUED_FOR_OWNER || 0,
        containerClassName: 'border-purple-100 bg-purple-50',
        labelClassName: 'text-purple-700',
      },
      {
        key: 'owner-state',
        label: 'Owner state',
        value: stateCounts.IMPORTED_FOR_OWNER_REVIEW || ownerState?.count || 0,
        containerClassName: 'border-emerald-100 bg-emerald-50',
        labelClassName: 'text-emerald-700',
      },
      {
        key: 'runtime-hints',
        label: 'Runtime hints',
        value: ownerRuntimeHints?.count || 0,
        containerClassName: 'border-cyan-100 bg-cyan-50',
        labelClassName: 'text-cyan-700',
      },
    ],
    ownerReviewStatusTiles: ['PENDING_OWNER_STATE_REVIEW', 'OWNER_STATE_ACCEPTED', 'OWNER_STATE_REJECTED'].map((status) => ({
      status,
      value: reviewCounts[status] || 0,
    })),
  }
}
