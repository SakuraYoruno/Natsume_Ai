import React from 'react'
import MemoryGuardStatsSection from './safety/MemoryGuardStatsSection.jsx'
import PermissionStatsSection from './safety/PermissionStatsSection.jsx'
import QaViolationsSection from './safety/QaViolationsSection.jsx'
import SchedulerGateStatsSection from './safety/SchedulerGateStatsSection.jsx'
import TribeEventsSection from './safety/TribeEventsSection.jsx'
import UiAssertionSection from './safety/UiAssertionSection.jsx'

export default function InsightsSafetyGroup({
  windowMinutes,
  qaData,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }

  return (
    <>
      <QaViolationsSection qaData={qaData} />
      <PermissionStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <SchedulerGateStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <MemoryGuardStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <UiAssertionSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <TribeEventsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
