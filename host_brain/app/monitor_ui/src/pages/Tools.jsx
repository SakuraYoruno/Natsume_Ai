import React from 'react'
import ToolsDashboardSurface from '../features/execution/components/ToolsDashboardSurface.jsx'
import { useToolsDashboardController } from '../features/execution/toolsDashboardController.js'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'

export default function Tools() {
  const tools = useToolsDashboardController()

  return (
    <ToolsDashboardSurface
      {...tools}
      DigitalLifeRuntimeCard={DigitalLifeRuntimeCard}
      OrganSystemOverview={OrganSystemOverview}
    />
  )
}
