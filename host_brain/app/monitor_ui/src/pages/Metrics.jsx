import React from 'react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import MetricsOverviewSurface from '../features/observability/components/metrics/MetricsOverviewSurface.jsx'
import { useMetricsOverviewController } from '../features/observability/metricsOverviewController.js'

export default function Metrics() {
  const metrics = useMetricsOverviewController()

  return <MetricsOverviewSurface {...metrics} DigitalLifeRuntimeCard={DigitalLifeRuntimeCard} />
}
