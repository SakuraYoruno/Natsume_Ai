import { useEffect, useState } from 'react'

import {
  acceptCodexBioOpsOwnerState,
  consumeCodexBioOpsOwnerQueues,
  fetchCodexBioOpsOwnerInbox,
  fetchCodexBioOpsOwnerImportCandidates,
  fetchCodexBioOpsOwnerQueues,
  fetchCodexBioOpsOwnerRuntimeHints,
  fetchCodexBioOpsOwnerState,
  importCodexBioOpsOwnerState,
  materializeCodexBioOpsOwnerInbox,
  materializeCodexBioOpsOwnerIntakePackets,
  rejectCodexBioOpsOwnerState,
} from '../../../bioOpsApi.js'
import {
  buildBioOpsOwnerInboxViewModel,
  buildBioOpsOwnerStateReviewBody,
  getBioOpsOwnerRunParams,
} from '../../../bioOpsOwnerModel.js'

export function useOwnerInboxPanel({ runId }) {
  const [data, setData] = useState(null)
  const [imports, setImports] = useState(null)
  const [queues, setQueues] = useState(null)
  const [ownerState, setOwnerState] = useState(null)
  const [ownerRuntimeHints, setOwnerRuntimeHints] = useState(null)
  const [lastMaterialized, setLastMaterialized] = useState(null)
  const [lastPackets, setLastPackets] = useState(null)
  const [lastConsumed, setLastConsumed] = useState(null)
  const [lastImported, setLastImported] = useState(null)
  const [lastStateReview, setLastStateReview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [materializing, setMaterializing] = useState(false)
  const [packetizing, setPacketizing] = useState(false)
  const [consuming, setConsuming] = useState(false)
  const [importing, setImporting] = useState(false)
  const [reviewingCandidate, setReviewingCandidate] = useState('')
  const [error, setError] = useState(null)
  const runParams = getBioOpsOwnerRunParams(runId)
  const inboxView = buildBioOpsOwnerInboxViewModel({
    data,
    imports,
    queues,
    ownerState,
    ownerRuntimeHints,
    lastPackets,
  })
  const { rows, queueRows, stateRows, hintRows } = inboxView

  const loadInbox = async () => {
    setLoading(true)
    setError(null)
    try {
      const [inboxPayload, importPayload, queuePayload, statePayload, hintPayload] = await Promise.all([
        fetchCodexBioOpsOwnerInbox(runParams),
        fetchCodexBioOpsOwnerImportCandidates(runParams),
        fetchCodexBioOpsOwnerQueues(runParams),
        fetchCodexBioOpsOwnerState(runParams),
        fetchCodexBioOpsOwnerRuntimeHints(runParams),
      ])
      setData(inboxPayload)
      setImports(importPayload)
      setQueues(queuePayload)
      setOwnerState(statePayload)
      setOwnerRuntimeHints(hintPayload)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (runId) {
      loadInbox()
    }
  }, [runId])

  const materializeReady = async () => {
    setMaterializing(true)
    setError(null)
    try {
      const payload = await materializeCodexBioOpsOwnerInbox({}, runParams)
      setLastMaterialized(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setMaterializing(false)
    }
  }

  const materializePackets = async () => {
    setPacketizing(true)
    setError(null)
    try {
      const payload = await materializeCodexBioOpsOwnerIntakePackets({}, runParams)
      setLastPackets(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setPacketizing(false)
    }
  }

  const consumePackets = async () => {
    setConsuming(true)
    setError(null)
    try {
      const payload = await consumeCodexBioOpsOwnerQueues({}, runParams)
      setLastConsumed(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setConsuming(false)
    }
  }

  const importOwnerState = async () => {
    setImporting(true)
    setError(null)
    try {
      const payload = await importCodexBioOpsOwnerState({}, runParams)
      setLastImported(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setImporting(false)
    }
  }

  const reviewOwnerState = async (candidateId, ownerOrgan, decision) => {
    const id = String(candidateId || '').trim()
    if (!id) return
    setReviewingCandidate(`${decision}:${id}`)
    setError(null)
    try {
      const body = buildBioOpsOwnerStateReviewBody(decision, ownerOrgan)
      const payload = decision === 'accept'
        ? await acceptCodexBioOpsOwnerState(id, body, runParams)
        : await rejectCodexBioOpsOwnerState(id, body, runParams)
      setLastStateReview(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setReviewingCandidate('')
    }
  }

  return {
    consumePackets,
    consuming,
    error,
    hintRows,
    importOwnerState,
    importing,
    inboxView,
    lastConsumed,
    lastImported,
    lastMaterialized,
    lastPackets,
    lastStateReview,
    loadInbox,
    loading,
    materializePackets,
    materializeReady,
    materializing,
    packetizing,
    queueRows,
    reviewOwnerState,
    reviewingCandidate,
    rows,
    stateRows,
  }
}
