import React from 'react'
import { ArrowLeft } from 'lucide-react'

import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'

const InsightsGlobalGroup = React.lazy(() => import('./InsightsGlobalGroup.jsx'))
const InsightsMemoryGroup = React.lazy(() => import('./InsightsMemoryGroup.jsx'))
const InsightsLlmGroup = React.lazy(() => import('./InsightsLlmGroup.jsx'))
const InsightsSafetyGroup = React.lazy(() => import('./InsightsSafetyGroup.jsx'))
const InsightsStateGroup = React.lazy(() => import('./InsightsStateGroup.jsx'))
const InsightsVtsGroup = React.lazy(() => import('./InsightsVtsGroup.jsx'))

function InsightGroupSuspense({ label, children }) {
  return (
    <React.Suspense
      fallback={(
        <div className="card p-4">
          <LoadingNotice>{label}</LoadingNotice>
        </div>
      )}
    >
      {children}
    </React.Suspense>
  )
}

export default function InsightsDetailPane({
  agentData,
  currentGroup,
  deepQueryProps,
  errData,
  focusTarget,
  govData,
  llmCallData,
  memoryData,
  navigate,
  qaData,
  subsysData,
  vtsData,
  windowMinutes,
}) {
  const handleBackToGroups = () => {
    navigate('/observability/insights')
    if (window.location.hash !== '#/observability/insights') {
      window.location.hash = '#/observability/insights'
    }
  }

  return (
    <div className="space-y-8">
      <div className="card p-4">
        <button type="button" className="btn-secondary" onClick={handleBackToGroups}>
          <ArrowLeft size={16} /> 返回分组
        </button>
      </div>

      {currentGroup === 'global' && (
        <InsightGroupSuspense label="正在加载全局异常洞察…">
          <InsightsGlobalGroup
            windowMinutes={windowMinutes}
            govData={govData}
            errData={errData}
            focusTarget={focusTarget}
            {...deepQueryProps}
          />
        </InsightGroupSuspense>
      )}

      {currentGroup === 'memory' && (
        <InsightGroupSuspense label="正在加载记忆器官洞察…">
          <InsightsMemoryGroup
            windowMinutes={windowMinutes}
            subsysData={subsysData}
            memoryData={memoryData}
            {...deepQueryProps}
          />
        </InsightGroupSuspense>
      )}

      {currentGroup === 'llm' && (
        <InsightGroupSuspense label="正在加载执行与推理洞察…">
          <InsightsLlmGroup
            windowMinutes={windowMinutes}
            agentData={agentData}
            llmCallData={llmCallData}
            {...deepQueryProps}
          />
        </InsightGroupSuspense>
      )}

      {currentGroup === 'safety' && (
        <InsightGroupSuspense label="正在加载安全与护栏洞察…">
          <InsightsSafetyGroup
            windowMinutes={windowMinutes}
            qaData={qaData}
            {...deepQueryProps}
          />
        </InsightGroupSuspense>
      )}

      {currentGroup === 'state' && (
        <InsightGroupSuspense label="正在加载状态与规范洞察…">
          <InsightsStateGroup
            windowMinutes={windowMinutes}
            {...deepQueryProps}
          />
        </InsightGroupSuspense>
      )}

      {currentGroup === 'vts' && (
        <InsightGroupSuspense label="正在加载表情通道洞察…">
          <InsightsVtsGroup vtsData={vtsData} />
        </InsightGroupSuspense>
      )}
    </div>
  )
}
