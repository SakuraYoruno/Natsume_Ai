import React from 'react'
import GatewaySurface from '../features/execution/components/GatewaySurface.jsx'
import { useGatewayController } from '../features/execution/gatewayController.js'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'

export default function Gateway() {
  const gateway = useGatewayController()

  return <GatewaySurface {...gateway} OrganOverviewComponent={OrganSystemOverview} />
}
