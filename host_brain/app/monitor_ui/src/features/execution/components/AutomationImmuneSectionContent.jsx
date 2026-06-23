import React from 'react'
import ImmuneGateOpsPanel from './automationImmune/ImmuneGateOpsPanel.jsx'

export default function AutomationImmuneSectionContent({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  initialReplayRequestId = '',
  runtimeCard = null,
}) {
  return (
    <>
      {runtimeCard}
      <ImmuneGateOpsPanel
        pollingEnabled={pollingEnabled}
        pollingInterval={pollingInterval}
        pollingTick={pollingTick}
        initialReplayRequestId={initialReplayRequestId}
      />
    </>
  )
}
