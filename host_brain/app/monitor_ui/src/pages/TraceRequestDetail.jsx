import React from 'react'
import TraceRequestDetailSurface from '../features/observability/components/trace/TraceRequestDetailSurface.jsx'
import { useTraceRequestDetailController } from '../features/observability/traceRequestDetailController.js'

export default function TraceRequestDetail() {
  const traceRequestDetail = useTraceRequestDetailController()

  return (
    <TraceRequestDetailSurface {...traceRequestDetail} />
  )
}
