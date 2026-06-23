import React from 'react'

import AutomationDashboardSurface from '../features/execution/components/AutomationDashboardSurface.jsx'
import { useAutomationDashboardController } from '../features/execution/automationDashboardController.js'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'

export default function Automation() {
  const automationDashboard = useAutomationDashboardController()
  return (
    <AutomationDashboardSurface
      {...automationDashboard}
      renderOrganOverview={(model) => <OrganSystemOverview model={model} />}
      renderRuntimeCard={({ runtimeSnapshot, promptStats }) => (
        <DigitalLifeRuntimeCard
          runtimeSnapshot={runtimeSnapshot}
          promptStats={promptStats}
          compact
        />
      )}
    />
  )
}
