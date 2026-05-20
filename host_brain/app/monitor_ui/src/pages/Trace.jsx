import React, { useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import TraceHome from '../components/trace/TraceHome.jsx'
import { TracePageFallback } from '../components/trace/TraceSurfaces.jsx'

const TraceRequestDetail = React.lazy(() => import('./TraceRequestDetail.jsx'))
const TraceFullChainView = React.lazy(() => import('../components/trace/TraceAdvancedViews.jsx').then((module) => ({ default: module.FullChainView })))
const TraceGroupDagRoute = React.lazy(() => import('../components/trace/TraceAdvancedViews.jsx').then((module) => ({ default: module.GroupDagRoute })))
export default function Trace() {
  const [traceData, setTraceData] = useState(null)
  const [traceInfo, setTraceInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const sharedProps = { traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }

  return (
    <Routes>
      <Route index element={<TraceHome {...sharedProps} />} />
      <Route
        path="full"
        element={(
          <React.Suspense
            fallback={(
              <TracePageFallback>正在加载全链路拓扑图…</TracePageFallback>
            )}
          >
            <TraceFullChainView traceData={traceData} />
          </React.Suspense>
        )}
      />
      <Route
        path="group/:groupId"
        element={(
          <React.Suspense
            fallback={(
              <TracePageFallback>正在加载分组链路图…</TracePageFallback>
            )}
          >
            <TraceGroupDagRoute traceData={traceData} />
          </React.Suspense>
        )}
      />
      <Route
        path=":requestId"
        element={(
          <React.Suspense
            fallback={(
              <TracePageFallback>正在打开请求级 Trace 详情…</TracePageFallback>
            )}
          >
            <TraceRequestDetail />
          </React.Suspense>
        )}
      />
    </Routes>
  )
}
