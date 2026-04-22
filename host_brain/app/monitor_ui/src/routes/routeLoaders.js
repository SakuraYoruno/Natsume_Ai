import { lazy } from 'react'

export const PAGE_LOADERS = Object.freeze({
  '/digital-life': () => import('../pages/DigitalLifeOverview.jsx'),
  '/life-timeline': () => import('../pages/LifeTimelinePage.jsx'),
  '/pathologies': () => import('../pages/PathologyCenterPage.jsx'),
  '/pathology-detail': () => import('../pages/PathologyDetailPage.jsx'),
  '/overview': () => import('../pages/Overview.jsx'),
  '/sessions': () => import('../pages/Sessions.jsx'),
  '/trace': () => import('../pages/Trace.jsx'),
  '/metrics': () => import('../pages/Metrics.jsx'),
  '/insights': () => import('../pages/Insights.jsx'),
  '/chat-debug': () => import('../pages/ChatDebug.jsx'),
  '/alerts': () => import('../pages/Alerts.jsx'),
  '/logs': () => import('../pages/LogStream.jsx'),
  '/emotion': () => import('../pages/EmotionDashboard.jsx'),
  '/memory': () => import('../pages/MemoryBrowser.jsx'),
  '/system': () => import('../pages/SystemConfig.jsx'),
  '/prompt-config': () => import('../pages/PromptConfig.jsx'),
  '/persona-config': () => import('../pages/PersonaPromptOps.jsx'),
  '/runtime-mode': () => import('../pages/RuntimeMode.jsx'),
  '/inner-world': () => import('../pages/InnerWorldDashboard.jsx'),
  '/render-policy': () => import('../pages/RenderPolicyDashboard.jsx'),
  '/self-model': () => import('../pages/SelfModelDashboard.jsx'),
  '/canon': () => import('../pages/Canon.jsx'),
  '/bond': () => import('../pages/Bond.jsx'),
  '/tools': () => import('../pages/Tools.jsx'),
  '/automation': () => import('../pages/Automation.jsx'),
  '/agent': () => import('../pages/AgentLoop.jsx'),
  '/gateway': () => import('../pages/Gateway.jsx'),
  '/skills': () => import('../pages/SkillsHub.jsx'),
})

function lazyPage(path) {
  return lazy(PAGE_LOADERS[path])
}

export const DigitalLifeOverview = lazyPage('/digital-life')
export const LifeTimelinePage = lazyPage('/life-timeline')
export const PathologyCenterPage = lazyPage('/pathologies')
export const PathologyDetailPage = lazyPage('/pathology-detail')
export const Overview = lazyPage('/overview')
export const Sessions = lazyPage('/sessions')
export const Trace = lazyPage('/trace')
export const Metrics = lazyPage('/metrics')
export const Insights = lazyPage('/insights')
export const ChatDebug = lazyPage('/chat-debug')
export const Alerts = lazyPage('/alerts')
export const LogStream = lazyPage('/logs')
export const EmotionDashboard = lazyPage('/emotion')
export const MemoryBrowser = lazyPage('/memory')
export const SystemConfig = lazyPage('/system')
export const PromptConfig = lazyPage('/prompt-config')
export const PersonaPromptOps = lazyPage('/persona-config')
export const RuntimeMode = lazyPage('/runtime-mode')
export const InnerWorldDashboard = lazyPage('/inner-world')
export const RenderPolicyDashboard = lazyPage('/render-policy')
export const SelfModelDashboard = lazyPage('/self-model')
export const Canon = lazyPage('/canon')
export const Bond = lazyPage('/bond')
export const Tools = lazyPage('/tools')
export const Automation = lazyPage('/automation')
export const AgentLoop = lazyPage('/agent')
export const Gateway = lazyPage('/gateway')
export const SkillsHub = lazyPage('/skills')

export function preloadRoute(path) {
  const normalizedPath = String(path || '').trim()
  const loader = PAGE_LOADERS[normalizedPath]
  if (!loader) {
    return Promise.resolve(null)
  }
  return loader().catch(() => null)
}

