import { lazy } from 'react'

export const PAGE_LOADERS = Object.freeze({
  '/cockpit': () => import('../pages/DigitalLifeOverview.jsx'),
  '/life/timeline': () => import('../pages/LifeTimelinePage.jsx'),
  '/life/pathologies': () => import('../pages/PathologyCenterPage.jsx'),
  '/life/pathologies/:pathologyId': () => import('../pages/PathologyDetailPage.jsx'),
  '/life/emotion': () => import('../pages/EmotionDashboard.jsx'),
  '/life/relationship': () => import('../pages/Bond.jsx'),
  '/life/memory': () => import('../pages/MemoryBrowser.jsx'),
  '/life/runtime': () => import('../pages/LifeRuntime.jsx'),
  '/life/runtime-mode': () => import('../pages/RuntimeMode.jsx'),
  '/life/inner-world': () => import('../pages/InnerWorldDashboard.jsx'),
  '/life/response-policy': () => import('../pages/RenderPolicyDashboard.jsx'),
  '/life/self-model': () => import('../pages/SelfModelDashboard.jsx'),
  '/life/proactive': () => import('../pages/ProactiveAutonomyDashboard.jsx'),
  '/operations/prompt': () => import('../pages/PromptConfig.jsx'),
  '/operations/persona': () => import('../pages/PersonaPromptOps.jsx'),
  '/operations/system': () => import('../pages/SystemConfig.jsx'),
  '/operations/canon': () => import('../pages/Canon.jsx'),
  '/observability/overview': () => import('../pages/Overview.jsx'),
  '/observability/sessions': () => import('../pages/Sessions.jsx'),
  '/observability/trace': () => import('../pages/Trace.jsx'),
  '/observability/bio-ops': () => import('../pages/CodexBioOpsObservability.jsx'),
  '/observability/metrics': () => import('../pages/Metrics.jsx'),
  '/observability/insights': () => import('../pages/Insights.jsx'),
  '/observability/alerts': () => import('../pages/Alerts.jsx'),
  '/observability/logs': () => import('../pages/LogStream.jsx'),
  '/execution/gateway': () => import('../pages/Gateway.jsx'),
  '/execution/skills': () => import('../pages/SkillCatalog.jsx'),
  '/execution/tools': () => import('../pages/Tools.jsx'),
  '/execution/automation': () => import('../pages/Automation.jsx'),
  '/execution/task-runtime': () => import('../pages/TaskRuntime.jsx'),
  '/execution/tribe': () => import('../pages/TribeConsole.jsx'),
  '/execution/edge-command': () => import('../pages/EdgeCommand.jsx'),
  '/security/immune': () => import('../pages/SecurityImmune.jsx'),
  '/integrations/body': () => import('../pages/BodyIntegration.jsx'),
})

function resolveLoader(path) {
  const normalizedPath = String(path || '').trim()
  if (PAGE_LOADERS[normalizedPath]) return PAGE_LOADERS[normalizedPath]
  if (normalizedPath.startsWith('/observability/trace')) return PAGE_LOADERS['/observability/trace']
  if (normalizedPath.startsWith('/observability/insights')) return PAGE_LOADERS['/observability/insights']
  if (normalizedPath.startsWith('/life/pathologies/')) return PAGE_LOADERS['/life/pathologies/:pathologyId']
  return null
}

function lazyPage(path) {
  const loader = PAGE_LOADERS[path]
  return lazy(loader)
}

export const DigitalLifeOverview = lazyPage('/cockpit')
export const CodexBioOpsObservability = lazyPage('/observability/bio-ops')
export const LifeTimelinePage = lazyPage('/life/timeline')
export const PathologyCenterPage = lazyPage('/life/pathologies')
export const PathologyDetailPage = lazyPage('/life/pathologies/:pathologyId')
export const Overview = lazyPage('/observability/overview')
export const Sessions = lazyPage('/observability/sessions')
export const Trace = lazyPage('/observability/trace')
export const Metrics = lazyPage('/observability/metrics')
export const Insights = lazyPage('/observability/insights')
export const Alerts = lazyPage('/observability/alerts')
export const LogStream = lazyPage('/observability/logs')
export const EmotionDashboard = lazyPage('/life/emotion')
export const MemoryBrowser = lazyPage('/life/memory')
export const SystemConfig = lazyPage('/operations/system')
export const PromptConfig = lazyPage('/operations/prompt')
export const PersonaPromptOps = lazyPage('/operations/persona')
export const RuntimeMode = lazyPage('/life/runtime-mode')
export const InnerWorldDashboard = lazyPage('/life/inner-world')
export const RenderPolicyDashboard = lazyPage('/life/response-policy')
export const SelfModelDashboard = lazyPage('/life/self-model')
export const ProactiveAutonomyDashboard = lazyPage('/life/proactive')
export const Canon = lazyPage('/operations/canon')
export const Bond = lazyPage('/life/relationship')
export const Tools = lazyPage('/execution/tools')
export const Automation = lazyPage('/execution/automation')
export const TaskRuntime = lazyPage('/execution/task-runtime')
export const Gateway = lazyPage('/execution/gateway')
export const SkillCatalog = lazyPage('/execution/skills')
export const LifeRuntime = lazyPage('/life/runtime')
export const TribeConsole = lazyPage('/execution/tribe')
export const EdgeCommand = lazyPage('/execution/edge-command')
export const SecurityImmune = lazyPage('/security/immune')
export const BodyIntegration = lazyPage('/integrations/body')

export function preloadRoute(path) {
  const loader = resolveLoader(path)
  if (!loader) {
    return Promise.resolve(null)
  }
  return loader().catch(() => null)
}
