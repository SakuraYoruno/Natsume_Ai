import React, { useCallback, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveCapabilityForgeProposal,
  rollbackCapabilityForgeProposal,
} from '../api.js'
import { useCapabilityForgeProposalsQuery } from '../forgeQueries.js'

const APPROVER_ID = 'ui.forge_risk_bar'
const OPERATOR_ID = 'ui.forge_risk_bar'

function parseApiError(error) {
  const raw = String(error?.message || '').trim()
  const marker = raw.indexOf(': ')
  if (marker > -1) {
    const maybeJson = raw.slice(marker + 2).trim()
    if (maybeJson.startsWith('{') && maybeJson.endsWith('}')) {
      try {
        const parsed = JSON.parse(maybeJson)
        return String(parsed?.reason_code || parsed?.detail || raw)
      } catch {
        return raw
      }
    }
  }
  return raw || 'unknown_error'
}

export default function ForgeRiskActionBar({
  capabilityGapData = null,
  capabilityGapError = null,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const [busyAction, setBusyAction] = useState('')

  const qualitySummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const qualityItems = capabilityGapData?.forge_runtime_quality?.items || []
  const critical = Number(qualitySummary.critical_proposals || 0)
  const unhealthy = Number(qualitySummary.unhealthy_proposals || 0)
  const tracked = Number(qualitySummary.tracked_proposals || 0)
  const avgHealth = Number(qualitySummary.avg_health_score || 0)
  const hasRisk = critical > 0 || unhealthy > 0
  const shouldShow = hasRisk || tracked > 0
  const {
    proposals: proposalItems,
    loading,
    refreshing,
    refetch: refetchProposals,
  } = useCapabilityForgeProposalsQuery({
    limit: 20,
    enabled: shouldShow,
    pollingEnabled: pollingEnabled && shouldShow,
    pollingTick,
    pollingInterval,
  })

  const selectedProposal = useMemo(() => {
    if (!shouldShow) return null
    const proposalMap = new Map(
      proposalItems
        .filter((item) => item && typeof item === 'object')
        .map((item) => [String(item.proposal_id || ''), item])
    )
    const rankedQuality = [...qualityItems]
      .filter((item) => item && typeof item === 'object')
      .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))

    for (const quality of rankedQuality) {
      const proposal = proposalMap.get(String(quality.proposal_id || ''))
      if (proposal) {
        return proposal
      }
    }
    return (
      proposalItems.find((item) => String(item?.status || '').toUpperCase() === 'PENDING_APPROVAL')
      || proposalItems[0]
      || null
    )
  }, [proposalItems, qualityItems, shouldShow])

  const selectedQuality = useMemo(() => {
    if (!selectedProposal) return null
    const proposalId = String(selectedProposal.proposal_id || '')
    return qualityItems.find((item) => String(item?.proposal_id || '') === proposalId) || null
  }, [qualityItems, selectedProposal])

  const handleApproveCanary = useCallback(async () => {
    if (!selectedProposal) return
    const proposalId = String(selectedProposal.proposal_id || '')
    const approvalToken = String(selectedProposal?.governor_gate?.approval_token_ref || '').trim()
    if (!proposalId || !approvalToken) {
      toast.error('Approval token not available for this proposal')
      return
    }
    setBusyAction('approve')
    try {
      await approveCapabilityForgeProposal(proposalId, {
        approval_token_ref: approvalToken,
        approver: APPROVER_ID,
        target_stage: 'canary',
      })
      toast.success(`Forge proposal approved to canary: ${proposalId.slice(0, 16)}`)
      await refetchProposals()
    } catch (error) {
      toast.error(`Approve failed: ${parseApiError(error)}`)
    } finally {
      setBusyAction('')
    }
  }, [refetchProposals, selectedProposal])

  const handleRollback = useCallback(async () => {
    if (!selectedProposal) return
    const proposalId = String(selectedProposal.proposal_id || '')
    if (!proposalId) return
    setBusyAction('rollback')
    try {
      await rollbackCapabilityForgeProposal(proposalId, {
        operator: OPERATOR_ID,
        reason: 'manual rollback from forge risk action bar',
      })
      toast.success(`Forge proposal rolled back: ${proposalId.slice(0, 16)}`)
      await refetchProposals()
    } catch (error) {
      toast.error(`Rollback failed: ${parseApiError(error)}`)
    } finally {
      setBusyAction('')
    }
  }, [refetchProposals, selectedProposal])

  if (capabilityGapError) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-orange-200 bg-orange-50/90 px-3 py-2 text-xs text-orange-700 shadow-sm">
        <span className="font-semibold">Capability Forge unavailable</span>
        <span className="font-mono text-[11px]">
          {String(capabilityGapError || 'OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE')}
        </span>
      </div>
    )
  }

  if (!shouldShow) {
    return null
  }

  const selectedStatus = String(selectedProposal?.status || '').toUpperCase()
  const selectedStage = String(selectedProposal?.promotion?.stage || '').toUpperCase()
  const selectedHealth = Number(selectedQuality?.health_score ?? qualitySummary?.avg_health_score ?? 0)
  const badgeClass = critical > 0
    ? 'border-red-200 bg-red-50/90 text-red-700'
    : hasRisk
      ? 'border-orange-200 bg-orange-50/90 text-orange-700'
      : 'border-emerald-200 bg-emerald-50/90 text-emerald-700'

  const approveDisabled = (
    !selectedProposal
    || selectedStatus !== 'PENDING_APPROVAL'
    || Boolean(selectedProposal?.governor_gate?.token_consumed)
    || busyAction === 'rollback'
  )
  const rollbackDisabled = !selectedProposal || selectedStatus === 'ROLLED_BACK' || busyAction === 'approve'

  return (
    <div className={`flex flex-wrap items-center gap-2 rounded-2xl border px-3 py-2 text-xs shadow-sm ${badgeClass}`}>
      <span className="font-semibold">Forge Risk</span>
      <span>{`critical ${critical}`}</span>
      <span>{`unhealthy ${unhealthy}`}</span>
      <span>{`avg ${avgHealth.toFixed(1)}`}</span>
      <span>{`tracked ${tracked}`}</span>

      {selectedProposal ? (
        <span className="rounded-full border border-white/70 bg-white/80 px-2 py-0.5 text-[11px] text-gray-700">
          {`proposal ${(String(selectedProposal.proposal_id || '').slice(0, 14) || '--')} | ${selectedStatus || '--'} | ${selectedStage || '--'} | h ${selectedHealth.toFixed(1)}`}
        </span>
      ) : (
        <span className="text-[11px] text-gray-600">{loading || refreshing ? 'loading proposals...' : 'no proposal loaded'}</span>
      )}

      <button
        type="button"
        className="rounded-lg border border-white/75 bg-white/80 px-2 py-1 font-medium text-gray-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45"
        onClick={handleApproveCanary}
        disabled={approveDisabled}
        title="Approve selected proposal to canary"
      >
        {busyAction === 'approve' ? 'Approving...' : 'Approve→Canary'}
      </button>

      <button
        type="button"
        className="rounded-lg border border-white/75 bg-white/80 px-2 py-1 font-medium text-gray-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45"
        onClick={handleRollback}
        disabled={rollbackDisabled}
        title="Rollback selected proposal"
      >
        {busyAction === 'rollback' ? 'Rolling back...' : 'Rollback'}
      </button>

      <a className="font-medium hover:underline" href="#/observability/alerts?reason_code=FORGE_RUNTIME_CRITICAL_PROPOSALS">
        Alert Drill-down
      </a>
      <a className="font-medium hover:underline" href="#/observability/insights/global?focus=forge-risk">
        Forge Insights
      </a>
    </div>
  )
}
