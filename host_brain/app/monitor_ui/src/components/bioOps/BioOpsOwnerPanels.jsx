import React, { useEffect, useState } from 'react'
import { CheckCircle2, Database, Eye, FileText, GitBranch, RefreshCw, ShieldCheck, XCircle } from 'lucide-react'
import { EvidenceChips, StatusPill, classNames } from './BioOpsSurfaces.jsx'
import {
  acceptCodexBioOpsAdoption,
  acceptCodexBioOpsOwnerState,
  consumeCodexBioOpsOwnerQueues,
  fetchCodexBioOpsAdoptionDetail,
  fetchCodexBioOpsOwnerInbox,
  fetchCodexBioOpsOwnerImportCandidates,
  fetchCodexBioOpsOwnerQueues,
  fetchCodexBioOpsOwnerRuntimeHints,
  fetchCodexBioOpsOwnerState,
  importCodexBioOpsOwnerState,
  materializeCodexBioOpsOwnerInbox,
  materializeCodexBioOpsOwnerIntakePackets,
  rejectCodexBioOpsAdoption,
  rejectCodexBioOpsOwnerState,
} from '../../api/obs.js'

export function PendingAdoptions({ items, runId, onChanged }) {
  const rows = Array.isArray(items) ? items : []
  const [selectedId, setSelectedId] = useState(rows[0]?.candidate_id || null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [decisionBusy, setDecisionBusy] = useState(null)
  const [error, setError] = useState(null)
  if (!rows.length) {
    return (
      <div className="rounded-lg border border-white/80 bg-white/82 p-4 text-sm text-gray-500 shadow-sm">
        当前没有待 owner 接管的候选；报告类能力保持只读输出。
      </div>
    )
  }
  const activeRow = rows.find((item) => item.candidate_id === selectedId) || rows[0]
  const activeDetail = detail?.adoption?.candidate_id === activeRow?.candidate_id ? detail.adoption : null
  const active = activeDetail || activeRow
  const runParams = runId ? { run_id: runId } : {}

  const loadDetail = async (candidateId) => {
    const id = String(candidateId || '').trim()
    if (!id) return
    setSelectedId(id)
    setError(null)
    setLoadingDetail(true)
    try {
      const payload = await fetchCodexBioOpsAdoptionDetail(id, runParams)
      setDetail(payload)
    } catch (err) {
      setError(err)
    } finally {
      setLoadingDetail(false)
    }
  }

  const recordDecision = async (decision) => {
    if (!active?.candidate_id || !active?.owner_organ) return
    setError(null)
    setDecisionBusy(decision)
    try {
      const body = {
        owner_organ: active.owner_organ,
        operator: 'monitor_ui',
        reason: `${decision} from Codex BioOps owner review panel`,
      }
      const payload = decision === 'accept'
        ? await acceptCodexBioOpsAdoption(active.candidate_id, body, runParams)
        : await rejectCodexBioOpsAdoption(active.candidate_id, body, runParams)
      setDetail({ adoption: payload.adoption })
      await onChanged?.()
    } catch (err) {
      setError(err)
    } finally {
      setDecisionBusy(null)
    }
  }

  const activeArtifacts = Array.isArray(active?.artifacts) ? active.artifacts : []
  const activeDecision = active?.decision || null

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[0.78fr_1.22fr]">
      <div className="space-y-3">
        {rows.slice(0, 8).map((item) => {
          const selected = item.candidate_id === active?.candidate_id
          return (
            <button
              type="button"
              key={item.candidate_id}
              className={classNames(
                'w-full rounded-lg border p-4 text-left shadow-sm transition-colors',
                selected ? 'border-amber-300 bg-amber-50' : 'border-white/80 bg-white/82 hover:border-amber-200 hover:bg-amber-50/60'
              )}
              onClick={() => loadDetail(item.candidate_id)}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-amber-900">{item.capability}</div>
                  <div className="mt-0.5 truncate text-xs text-amber-700">{item.owner_organ} · {item.output_kind}</div>
                </div>
                <StatusPill status={item.status || 'OWNER_GATED'} />
              </div>
              <p className="mt-2 line-clamp-3 text-sm leading-6 text-amber-800">{item.summary}</p>
              <EvidenceChips items={(item.evidence_refs || []).map((ref) => `evidence:${ref}`)} />
            </button>
          )
        })}
      </div>

      <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">Owner review</div>
            <div className="mt-0.5 break-all text-xs text-gray-500">{active?.candidate_id}</div>
          </div>
          <StatusPill status={activeDecision?.decision || active?.status || 'OWNER_GATED'} />
        </div>

        {loadingDetail ? (
          <div className="mt-3 h-24 animate-pulse rounded-lg border border-gray-100 bg-gray-50" />
        ) : (
          <>
            <p className="mt-3 text-sm leading-6 text-gray-700">{active?.summary}</p>
            <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-2">
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Owner <span className="ml-1 font-semibold">{active?.owner_organ || '—'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Output <span className="ml-1 font-semibold">{active?.output_kind || '—'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Write <span className="ml-1 font-semibold">{active?.write_policy || 'decision artifact'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Direct write <span className="ml-1 font-semibold">{String(Boolean(active?.direct_long_term_write))}</span>
              </div>
            </div>

            <EvidenceChips
              items={[
                ...(active?.risk_flags || []).map((flag) => `risk:${flag}`),
                ...(active?.evidence_refs || []).map((ref) => `evidence:${ref}`),
              ]}
            />

            {activeArtifacts.length ? (
              <div className="mt-3 space-y-2">
                {activeArtifacts.slice(0, 3).map((artifact, index) => (
                  <code key={artifact.path || index} className="block break-all rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5 text-[11px] text-gray-500">
                    {artifact.path || artifact.artifact_id || JSON.stringify(artifact)}
                  </code>
                ))}
              </div>
            ) : null}

            {activeDecision ? (
              <div className="mt-3 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-xs leading-5 text-green-700">
                已记录 {activeDecision.decision}；OBS 只写决策 artifact，不直接修改长期状态。
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 transition-colors hover:bg-green-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={Boolean(decisionBusy)}
                  onClick={() => recordDecision('accept')}
                >
                  <CheckCircle2 size={13} />
                  {decisionBusy === 'accept' ? '记录中' : 'Owner accept'}
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={Boolean(decisionBusy)}
                  onClick={() => recordDecision('reject')}
                >
                  <XCircle size={13} />
                  {decisionBusy === 'reject' ? '记录中' : 'Owner reject'}
                </button>
              </div>
            )}
          </>
        )}

        {error ? (
          <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
            {error.message}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export function OwnerInboxPanel({ runId }) {
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
  const runParams = runId ? { run_id: runId } : {}

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
      const body = {
        owner_organ: ownerOrgan,
        operator: 'monitor_ui',
        reason: decision === 'accept' ? 'owner accepted runtime hint' : 'owner rejected runtime hint',
      }
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

  const rows = Array.isArray(data?.items) ? data.items : []
  const queueRows = Array.isArray(queues?.items) ? queues.items : []
  const stateRows = Array.isArray(ownerState?.items) ? ownerState.items : []
  const hintRows = Array.isArray(ownerRuntimeHints?.items) ? ownerRuntimeHints.items : []
  const counts = data?.status_counts || {}
  const importCounts = imports?.status_counts || {}
  const queueCounts = queues?.status_counts || {}
  const stateCounts = ownerState?.status_counts || {}
  const reviewCounts = ownerState?.review_status_counts || {}

  return (
    <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900">Owner inbox</div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">
            accept/reject 之后再做 owner、证据、敏感目标校验；通过后只生成 import candidate artifact。
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={loading}
            onClick={loadInbox}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            扫描
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-semibold text-primary shadow-sm transition-colors hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={materializing || !Number(counts.READY_FOR_OWNER_IMPORT || 0)}
            onClick={materializeReady}
          >
            <FileText size={13} />
            {materializing ? '生成中' : '生成 inbox artifacts'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={packetizing || !Number(importCounts.READY_FOR_ORGAN_CONSUMPTION || 0)}
            onClick={materializePackets}
          >
            <GitBranch size={13} />
            {packetizing ? '生成中' : '生成 organ packets'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200 bg-purple-50 px-3 py-1.5 text-xs font-semibold text-purple-700 shadow-sm transition-colors hover:bg-purple-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={consuming || !(Number(lastPackets?.packet_count || 0) || Number(importCounts.READY_FOR_ORGAN_CONSUMPTION || 0))}
            onClick={consumePackets}
          >
            <Database size={13} />
            {consuming ? '消费中' : '排入 owner queues'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={importing || !Number(queueCounts.QUEUED_FOR_OWNER || 0)}
            onClick={importOwnerState}
          >
            <ShieldCheck size={13} />
            {importing ? '导入中' : '导入 owner state'}
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        {['READY_FOR_OWNER_IMPORT', 'PENDING_REVIEW', 'REJECTED_BY_OWNER', 'BLOCKED'].map((status) => (
          <div key={status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
            <StatusPill status={status} />
            <div className="mt-2 text-xl font-bold text-gray-900">{counts[status] || 0}</div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-2 py-2">
          <div className="font-semibold text-indigo-700">Import-ready</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{importCounts.READY_FOR_ORGAN_CONSUMPTION || 0}</div>
        </div>
        <div className="rounded-lg border border-red-100 bg-red-50 px-2 py-2">
          <div className="font-semibold text-red-700">Import blocked</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{importCounts.BLOCKED || 0}</div>
        </div>
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
          <div className="font-semibold text-gray-700">Organ packets</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{lastPackets?.packet_count || 0}</div>
        </div>
        <div className="rounded-lg border border-purple-100 bg-purple-50 px-2 py-2">
          <div className="font-semibold text-purple-700">Owner queues</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{queueCounts.QUEUED_FOR_OWNER || 0}</div>
        </div>
        <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-2 py-2">
          <div className="font-semibold text-emerald-700">Owner state</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{stateCounts.IMPORTED_FOR_OWNER_REVIEW || ownerState?.count || 0}</div>
        </div>
        <div className="rounded-lg border border-cyan-100 bg-cyan-50 px-2 py-2">
          <div className="font-semibold text-cyan-700">Runtime hints</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{ownerRuntimeHints?.count || 0}</div>
        </div>
      </div>

      {rows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-gray-100">
          {rows.slice(0, 6).map((item) => (
            <div key={item.candidate_id} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="text-gray-500">{item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="font-semibold text-gray-500">{item.import_allowed ? 'import-ready' : item.reason_code || 'waiting'}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-500">
          {loading ? '正在扫描 owner inbox...' : '暂无 owner inbox 项。'}
        </div>
      )}

      {lastMaterialized ? (
        <div className="mt-3 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-xs leading-5 text-green-700">
          已生成 {lastMaterialized.materialized_count} 个 owner import candidate artifact；长期写入仍由 owner organ 自己执行。
        </div>
      ) : null}

      {lastPackets ? (
        <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs leading-5 text-indigo-700">
          已生成 {lastPackets.packet_count} 个 owner intake packet；每个器官只消费自己的 packet。
        </div>
      ) : null}

      {lastConsumed ? (
        <div className="mt-3 rounded-lg border border-purple-100 bg-purple-50 px-3 py-2 text-xs leading-5 text-purple-700">
          已排入 {lastConsumed.queued_count} 个 owner queue item；这是候选队列 artifact，不代表长期状态已写入。
        </div>
      ) : null}

      {lastImported ? (
        <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-700">
          已导入 {lastImported.imported_count} 个 owner candidate state；它是 owner 的持久候选，不是已应用的长期记忆。
        </div>
      ) : null}

      {lastStateReview ? (
        <div className="mt-3 rounded-lg border border-cyan-100 bg-cyan-50 px-3 py-2 text-xs leading-5 text-cyan-700">
          Owner state review: {lastStateReview.status} · runtime hints {lastStateReview.runtime_hints?.count || 0} · 长期写入仍为 false。
        </div>
      ) : null}

      {queueRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-purple-100">
          {queueRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.channel_id}-${item.candidate_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="truncate text-gray-500">{item.channel_id || item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="font-semibold text-gray-500">{item.durable_write_performed ? 'wrote' : 'artifact-only'}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {stateRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-emerald-100">
          {stateRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.channel_id}-${item.candidate_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.72fr_0.78fr_0.95fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="truncate text-gray-500">{item.channel_id || item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.review_status || item.status} />
                <span className="font-semibold text-gray-500">{item.durable_memory_write_performed ? 'memory-write' : 'candidate-only'}</span>
              </div>
              <div className="flex flex-wrap justify-start gap-1.5 sm:justify-end">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 font-semibold text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={reviewingCandidate === `accept:${item.candidate_id}` || item.review_status === 'OWNER_STATE_ACCEPTED'}
                  onClick={() => reviewOwnerState(item.candidate_id, item.owner_organ, 'accept')}
                >
                  <CheckCircle2 size={12} />
                  accept
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-semibold text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={reviewingCandidate === `reject:${item.candidate_id}` || item.review_status === 'OWNER_STATE_REJECTED'}
                  onClick={() => reviewOwnerState(item.candidate_id, item.owner_organ, 'reject')}
                >
                  <XCircle size={12} />
                  reject
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {stateRows.length ? (
        <div className="mt-2 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-3">
          {['PENDING_OWNER_STATE_REVIEW', 'OWNER_STATE_ACCEPTED', 'OWNER_STATE_REJECTED'].map((status) => (
            <div key={status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
              <StatusPill status={status} />
              <div className="mt-2 text-lg font-bold text-gray-900">{reviewCounts[status] || 0}</div>
            </div>
          ))}
        </div>
      ) : null}

      {hintRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-cyan-100">
          {hintRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.hint_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.guidance_type || item.capability}</div>
                <div className="truncate text-gray-500">{item.guidance || item.summary}</div>
              </div>
              <div className="truncate text-gray-500">{item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="inline-flex items-center gap-1 font-semibold text-gray-500"><Eye size={12} /> trace-only</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {error ? (
        <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
          {error.message}
        </div>
      ) : null}
    </div>
  )
}
