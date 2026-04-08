import React, { Suspense, lazy } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import Layout from './components/Layout.jsx'

const DigitalLifeOverview = lazy(() => import('./pages/DigitalLifeOverview.jsx'))
const Overview = lazy(() => import('./pages/Overview.jsx'))
const Sessions = lazy(() => import('./pages/Sessions.jsx'))
const Trace = lazy(() => import('./pages/Trace.jsx'))
const Metrics = lazy(() => import('./pages/Metrics.jsx'))
const Insights = lazy(() => import('./pages/Insights.jsx'))
const Canon = lazy(() => import('./pages/Canon.jsx'))
const Bond = lazy(() => import('./pages/Bond.jsx'))
const Tools = lazy(() => import('./pages/Tools.jsx'))
const Automation = lazy(() => import('./pages/Automation.jsx'))
const AgentLoop = lazy(() => import('./pages/AgentLoop.jsx'))
const Gateway = lazy(() => import('./pages/Gateway.jsx'))
const SkillsHub = lazy(() => import('./pages/SkillsHub.jsx'))
const ChatDebug = lazy(() => import('./pages/ChatDebug.jsx'))
const Alerts = lazy(() => import('./pages/Alerts.jsx'))
const LogStream = lazy(() => import('./pages/LogStream.jsx'))
const EmotionDashboard = lazy(() => import('./pages/EmotionDashboard.jsx'))
const MemoryBrowser = lazy(() => import('./pages/MemoryBrowser.jsx'))
const SystemConfig = lazy(() => import('./pages/SystemConfig.jsx'))
const RuntimeMode = lazy(() => import('./pages/RuntimeMode.jsx'))
const PromptConfig = lazy(() => import('./pages/PromptConfig.jsx'))
const PersonaPromptOps = lazy(() => import('./pages/PersonaPromptOps.jsx'))
const InnerWorldDashboard = lazy(() => import('./pages/InnerWorldDashboard.jsx'))
const ResponsePolicyDashboard = lazy(() => import('./pages/ResponsePolicyDashboard.jsx'))
const SelfModelDashboard = lazy(() => import('./pages/SelfModelDashboard.jsx'))

function RouteLoading() {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 shadow-sm">
      正在加载页面…
    </div>
  )
}

function withSuspense(element) {
  return <Suspense fallback={<RouteLoading />}>{element}</Suspense>
}

export default function App() {
  return (
    <ErrorBoundary>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/digital-life" replace />} />
            <Route path="digital-life" element={withSuspense(<DigitalLifeOverview />)} />
            <Route path="overview" element={withSuspense(<Overview />)} />
            <Route path="sessions" element={withSuspense(<Sessions />)} />
            <Route path="trace/*" element={withSuspense(<Trace />)} />
            <Route path="metrics" element={withSuspense(<Metrics />)} />
            <Route path="insights/*" element={withSuspense(<Insights />)} />
            <Route path="chat-debug" element={withSuspense(<ChatDebug />)} />
            <Route path="alerts" element={withSuspense(<Alerts />)} />
            <Route path="logs" element={withSuspense(<LogStream />)} />
            <Route path="emotion" element={withSuspense(<EmotionDashboard />)} />
            <Route path="memory" element={withSuspense(<MemoryBrowser />)} />
            <Route path="system" element={withSuspense(<SystemConfig />)} />
            <Route path="prompt-config" element={withSuspense(<PromptConfig />)} />
            <Route path="persona-config" element={withSuspense(<PersonaPromptOps />)} />
            <Route path="runtime-mode" element={withSuspense(<RuntimeMode />)} />
            <Route path="inner-world" element={withSuspense(<InnerWorldDashboard />)} />
            <Route path="response-policy" element={withSuspense(<ResponsePolicyDashboard />)} />
            <Route path="self-model" element={withSuspense(<SelfModelDashboard />)} />
            <Route path="canon" element={withSuspense(<Canon />)} />
            <Route path="bond" element={withSuspense(<Bond />)} />
            <Route path="tools" element={withSuspense(<Tools />)} />
            <Route path="automation" element={withSuspense(<Automation />)} />
            <Route path="agent" element={withSuspense(<AgentLoop />)} />
            <Route path="gateway" element={withSuspense(<Gateway />)} />
            <Route path="skills" element={withSuspense(<SkillsHub />)} />
            <Route path="wechat" element={<Navigate to="/gateway" replace />} />
          </Route>
        </Routes>
        <Toaster position="top-right" toastOptions={{ duration: 4000, style: { fontSize: '13px' } }} />
      </HashRouter>
    </ErrorBoundary>
  )
}
