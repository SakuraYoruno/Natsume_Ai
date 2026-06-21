import { useState } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'
import { EvidenceChips, StatusPill, classNames } from '../BioOpsSurfaces.jsx'
import {
  acceptCodexBioOpsAdoption,
  fetchCodexBioOpsAdoptionDetail,
  rejectCodexBioOpsAdoption,
} from '../../../bioOpsApi.js'
import {
  buildBioOpsAdoptionDecisionBody,
  getBioOpsOwnerRunParams,
  getBioOpsPendingAdoptionReview,
} from '../../../bioOpsOwnerModel.js'

export default function PendingAdoptions({ items, runId, onChanged }) {
  const initialRows = getBioOpsPendingAdoptionReview({ items }).rows
  const [selectedId, setSelectedId] = useState(initialRows[0]?.candidate_id || null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [decisionBusy, setDecisionBusy] = useState(null)
  const [error, setError] = useState(null)
  const review = getBioOpsPendingAdoptionReview({ items, selectedId, detail })
  const { rows, active, activeArtifacts, activeDecision } = review
  if (!rows.length) {
    return (
      <div className="rounded-lg border border-white/80 bg-white/82 p-4 text-sm text-gray-500 shadow-sm">
        当前没有待 owner 接管的候选；报告类能力保持只读输出。
      </div>
    )
  }
  const runParams = getBioOpsOwnerRunParams(runId)

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
      const body = buildBioOpsAdoptionDecisionBody(decision, active)
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
