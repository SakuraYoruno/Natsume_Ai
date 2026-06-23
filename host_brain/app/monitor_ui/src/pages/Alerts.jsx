import React from 'react'
import AlertsCenterSurface from '../features/observability/components/alerts/AlertsCenterSurface.jsx'
import { useAlertsCenterController } from '../features/observability/alertsController.js'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'

export default function Alerts() {
  const alerts = useAlertsCenterController()

  return (
    <AlertsCenterSurface
      {...alerts}
      RuntimeCardComponent={DigitalLifeRuntimeCard}
      OrganOverviewComponent={OrganSystemOverview}
    />
  )
}
