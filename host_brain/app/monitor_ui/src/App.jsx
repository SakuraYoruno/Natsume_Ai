import React from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Overview from './pages/Overview.jsx'
import Sessions from './pages/Sessions.jsx'
import Trace from './pages/Trace.jsx'
import Metrics from './pages/Metrics.jsx'
import Insights from './pages/Insights.jsx'

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="trace" element={<Trace />} />
          <Route path="trace/:requestId" element={<Trace />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="insights" element={<Insights />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}
