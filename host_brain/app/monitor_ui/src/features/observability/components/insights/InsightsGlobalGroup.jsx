import CapabilityGapStatsSection from './global/CapabilityGapStatsSection.jsx'
import ChainStatsSection from './global/ChainStatsSection.jsx'
import ErrorHeatmapSection from './global/ErrorHeatmapSection.jsx'
import FailureStatsSection from './global/FailureStatsSection.jsx'
import GlobalErrorSection from './global/GlobalErrorSection.jsx'
import GovernorDecisionSection from './global/GovernorDecisionSection.jsx'

export default function InsightsGlobalGroup({
  windowMinutes,
  govData,
  errData,
  focusTarget,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }

  return (
    <>
      <GovernorDecisionSection govData={govData} />
      <GlobalErrorSection errData={errData} />
      <ErrorHeatmapSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <CapabilityGapStatsSection windowMinutes={windowMinutes} highlightForgeRisk={focusTarget === 'forge-risk'} {...deepQueryProps} />
      <ChainStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <FailureStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
