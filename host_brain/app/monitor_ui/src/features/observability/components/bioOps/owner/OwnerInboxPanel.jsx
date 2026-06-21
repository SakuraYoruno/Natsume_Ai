import { useEffect, useState } from 'react'
import { CheckCircle2, Database, Eye, FileText, GitBranch, RefreshCw, ShieldCheck, XCircle } from 'lucide-react'
import { StatusPill, classNames } from '../BioOpsSurfaces.jsx'
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

export default function OwnerInboxPanel({ runId }) {
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
            disabled={materializing || !inboxView.canMaterializeReady}
            onClick={materializeReady}
          >
            <FileText size={13} />
            {materializing ? '生成中' : '生成 inbox artifacts'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={packetizing || !inboxView.canMaterializePackets}
            onClick={materializePackets}
          >
            <GitBranch size={13} />
            {packetizing ? '生成中' : '生成 organ packets'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200 bg-purple-50 px-3 py-1.5 text-xs font-semibold text-purple-700 shadow-sm transition-colors hover:bg-purple-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={consuming || !inboxView.canConsumePackets}
            onClick={consumePackets}
          >
            <Database size={13} />
            {consuming ? '消费中' : '排入 owner queues'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={importing || !inboxView.canImportOwnerState}
            onClick={importOwnerState}
          >
            <ShieldCheck size={13} />
            {importing ? '导入中' : '导入 owner state'}
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        {inboxView.inboxStatusTiles.map((tile) => (
          <div key={tile.status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
            <StatusPill status={tile.status} />
            <div className="mt-2 text-xl font-bold text-gray-900">{tile.value}</div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        {inboxView.importSummaryTiles.map((tile) => (
          <div key={tile.key} className={classNames('rounded-lg border px-2 py-2', tile.containerClassName)}>
            <div className={classNames('font-semibold', tile.labelClassName)}>{tile.label}</div>
            <div className="mt-1 text-xl font-bold text-gray-900">{tile.value}</div>
          </div>
        ))}
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
          {inboxView.ownerReviewStatusTiles.map((tile) => (
            <div key={tile.status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
              <StatusPill status={tile.status} />
              <div className="mt-2 text-lg font-bold text-gray-900">{tile.value}</div>
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
