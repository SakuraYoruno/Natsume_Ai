import { useMemo, useState } from 'react'
import {
  AdaptiveRepairPanel,
  AffectLearningPanel,
  DriveTrustPanel,
  GrowthClosurePanel,
  InnerLifePanel,
  InnerMechanismPanel,
  MemoryNarrativePanel,
  NaturalInteractionPanel,
  PresenceChannelPanel,
  RelationshipLayerPanel,
  SelfGovernancePanel,
  TrustFoundationPanel,
} from './components/bioOps/BioOpsCapabilityPanels.jsx'
import useAppStore from '../../store/useAppStore.js'
import { runCodexBioOpsDailyCycle } from './bioOpsApi.js'
import {
  buildBioOpsCapabilitySections,
  buildBioOpsMetricTiles,
  getBioOpsObservabilityViewData,
  isBioOpsKeyBenefitObservable,
} from './bioOpsModel.js'
import { useCodexBioOpsObservabilityQuery } from './codexBioOpsQueries.js'

const BIO_OPS_CAPABILITY_PANELS = {
  inner_mechanisms: InnerMechanismPanel,
  trust_foundation: TrustFoundationPanel,
  natural_interaction: NaturalInteractionPanel,
  relationship_layer: RelationshipLayerPanel,
  inner_life: InnerLifePanel,
  self_governance: SelfGovernancePanel,
  growth_closure: GrowthClosurePanel,
  memory_narrative: MemoryNarrativePanel,
  affect_learning: AffectLearningPanel,
  presence_channel: PresenceChannelPanel,
  drive_trust: DriveTrustPanel,
  adaptive_repair: AdaptiveRepairPanel,
}

export function useCodexBioOpsController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const [dailyRunning, setDailyRunning] = useState(false)
  const [dailyRunResult, setDailyRunResult] = useState(null)
  const [dailyRunError, setDailyRunError] = useState(null)
  const [showOwnerDeepDive, setShowOwnerDeepDive] = useState(false)

  const query = useCodexBioOpsObservabilityQuery({
    pollingEnabled: pollingEnabled && pageVisible,
    pollingInterval,
    pollingTick,
  })
  const data = query.data || null
  const bioOpsView = useMemo(() => getBioOpsObservabilityViewData(data), [data])
  const {
    codex,
    bioOps,
    inspect,
    blockedWrite,
    pendingAdoptions,
    statusCounts,
  } = bioOpsView
  const capabilitySections = useMemo(
    () => buildBioOpsCapabilitySections(bioOps, BIO_OPS_CAPABILITY_PANELS),
    [bioOps]
  )
  const metricTiles = useMemo(() => buildBioOpsMetricTiles(bioOpsView), [bioOpsView])
  const keyBenefitObservable = isBioOpsKeyBenefitObservable({ data, codex, inspect, blockedWrite })

  const runDailyCycle = async () => {
    setDailyRunning(true)
    setDailyRunError(null)
    try {
      const payload = await runCodexBioOpsDailyCycle({
        inputs: {
          evidence_refs: ['monitor_ui:manual_daily_cycle'],
          message: 'monitor ui daily cycle',
          planned_path: ['bio_ops_daily_cycle'],
        },
      })
      setDailyRunResult(payload)
      await query.refetch?.()
    } catch (err) {
      setDailyRunError(err)
    } finally {
      setDailyRunning(false)
    }
  }

  return {
    blockedWrite,
    bioOps,
    capabilitySections,
    codex,
    dailyRunning,
    dailyRunError,
    dailyRunResult,
    data,
    inspect,
    keyBenefitObservable,
    loading: query.loading,
    metricTiles,
    pendingAdoptions,
    refreshing: query.refreshing,
    error: query.error,
    lastUpdatedAt: query.lastUpdatedAt,
    showOwnerDeepDive,
    statusCounts,
    onOwnerDeepDiveToggle: setShowOwnerDeepDive,
    onRefresh: query.refetch,
    onRunDailyCycle: runDailyCycle,
  }
}
