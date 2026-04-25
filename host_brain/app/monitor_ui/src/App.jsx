import React, { Suspense } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import Layout from './components/Layout.jsx'
import {
  AgentLoop,
  Alerts,
  Automation,
  Bond,
  Canon,
  ChatDebug,
  CodexBioOpsObservability,
  DigitalLifeOverview,
  EmotionDashboard,
  Gateway,
  InnerWorldDashboard,
  Insights,
  LifeTimelinePage,
  LogStream,
  MemoryBrowser,
  Metrics,
  Overview,
  PathologyCenterPage,
  PathologyDetailPage,
  PersonaPromptOps,
  PromptConfig,
  RenderPolicyDashboard,
  RuntimeMode,
  SelfModelDashboard,
  Sessions,
  SkillsHub,
  SystemConfig,
  Tools,
  Trace,
} from './routes/routeLoaders.js'


function RouteLoading() {
  return (
    <div className="card relative overflow-hidden p-6">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,_rgba(41,112,255,0.08),_transparent_40%),radial-gradient(circle_at_bottom_left,_rgba(16,185,129,0.07),_transparent_38%)]" />
      <div className="relative space-y-4">
        <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-semibold text-primary shadow-sm">
          <span className="lifeline-node" />
          数字生命体唤醒中
        </div>

        <div className="lifeline-rail h-8 px-3">
          <div className="lifeline-wave absolute inset-0 opacity-70" />
          <div className="relative flex h-full items-center justify-between">
            {[0, 1, 2, 3, 4].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.28}s` }} />
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <div key={index} className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-sm backdrop-blur-sm">
              <div className="h-2.5 w-24 rounded-full bg-primary-100/80" />
              <div className="mt-3 h-5 w-2/3 rounded-full bg-gray-200/80" />
              <div className="mt-2 h-2.5 w-full rounded-full bg-gray-100" />
              <div className="mt-1.5 h-2.5 w-4/5 rounded-full bg-gray-100" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function withSuspense(element) {
  return <Suspense fallback={<RouteLoading />}>{element}</Suspense>
}

export default function App() {
  return (
    <ErrorBoundary>
      <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/digital-life" replace />} />
            <Route path="digital-life" element={withSuspense(<DigitalLifeOverview />)} />
            <Route path="codex-bio-ops" element={withSuspense(<CodexBioOpsObservability />)} />
            <Route path="life-timeline" element={withSuspense(<LifeTimelinePage />)} />
            <Route path="pathologies" element={withSuspense(<PathologyCenterPage />)} />
            <Route path="pathologies/:pathologyId" element={withSuspense(<PathologyDetailPage />)} />
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
            <Route path="response-policy" element={withSuspense(<RenderPolicyDashboard />)} />
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
