import React from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import Layout from './components/Layout.jsx'
import Overview from './pages/Overview.jsx'
import Sessions from './pages/Sessions.jsx'
import Trace from './pages/Trace.jsx'
import Metrics from './pages/Metrics.jsx'
import Insights from './pages/Insights.jsx'
import Canon from './pages/Canon.jsx'
import Bond from './pages/Bond.jsx'
import Tools from './pages/Tools.jsx'
import Automation from './pages/Automation.jsx'
import AgentLoop from './pages/AgentLoop.jsx'
import Gateway from './pages/Gateway.jsx'
import SkillsHub from './pages/SkillsHub.jsx'
import ChatDebug from './pages/ChatDebug.jsx'
import Alerts from './pages/Alerts.jsx'
import LogStream from './pages/LogStream.jsx'
import EmotionDashboard from './pages/EmotionDashboard.jsx'
import MemoryBrowser from './pages/MemoryBrowser.jsx'
import SystemConfig from './pages/SystemConfig.jsx'
import RuntimeMode from './pages/RuntimeMode.jsx'

export default function App() {
  return (
    <ErrorBoundary>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="trace/*" element={<Trace />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="insights/*" element={<Insights />} />
          <Route path="chat-debug" element={<ChatDebug />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="logs" element={<LogStream />} />
          <Route path="emotion" element={<EmotionDashboard />} />
          <Route path="memory" element={<MemoryBrowser />} />
          <Route path="system" element={<SystemConfig />} />
          <Route path="runtime-mode" element={<RuntimeMode />} />
          <Route path="canon" element={<Canon />} />
          <Route path="bond" element={<Bond />} />
          <Route path="tools" element={<Tools />} />
          <Route path="automation" element={<Automation />} />
          <Route path="agent" element={<AgentLoop />} />
          <Route path="gateway" element={<Gateway />} />
          <Route path="skills" element={<SkillsHub />} />
          {/* 向后兼容 */}
          <Route path="wechat" element={<Navigate to="/gateway" replace />} />
        </Route>
      </Routes>
      <Toaster position="top-right" toastOptions={{ duration: 4000, style: { fontSize: '13px' } }} />
    </HashRouter>
    </ErrorBoundary>
  )
}
