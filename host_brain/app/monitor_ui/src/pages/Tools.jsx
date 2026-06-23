import React from 'react'
import ToolsDashboardSurface from '../features/execution/components/ToolsDashboardSurface.jsx'
import { useToolsDashboardController } from '../features/execution/toolsDashboardController.js'

export default function Tools() {
  const tools = useToolsDashboardController()

  return <ToolsDashboardSurface {...tools} />
}
