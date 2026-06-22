import { PauseCircle, Play, Power } from 'lucide-react'

import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { AUTOMATION_PRIORITY_STYLES } from '../automationResourceModel.js'
import { getTaskView } from '../automationTaskModel.js'

export default function AutomationTaskDetailPanel({ item, onEnable, onDisable, onTrigger, actionLoading }) {
  if (!item) {
    return <EmptyStateCard title="请选择一个节律任务" hint="左侧选择任务后，可查看健康、门控和操作入口。" />
  }
  const {
    task,
    health,
    status,
    style,
    resourceProfile,
    lastReasonCode,
    lastGateState,
    lastMoodState,
    lastImmuneDecision,
    lastImmuneRisk,
    lastImmuneReasonCodes,
    lastCortisol,
    lastEnergy,
  } = getTaskView(item)
  const isLoading = actionLoading === task.task_id

  return (
    <div className={`space-y-4 rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm ${task.enabled ? '' : 'opacity-80 saturate-75'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
            <h3 className="truncate font-mono text-base font-semibold text-gray-900">{task.task_id}</h3>
          </div>
          {task.description ? <p className="mt-1 text-sm leading-6 text-gray-600">{task.description}</p> : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${AUTOMATION_PRIORITY_STYLES[task.priority] || AUTOMATION_PRIORITY_STYLES.MEDIUM}`}>
            {task.priority}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
          <span className={`rounded border px-1.5 py-0.5 text-xs ${resourceProfile.meta.chipClass}`}>{resourceProfile.meta.shortLabel}</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.run_count ?? 0}</div>
          <div className="text-gray-400">节律次数</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.error_count ?? 0}</div>
          <div className="text-gray-400">失衡次数</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{task.trigger_mode || '-'}</div>
          <div className="text-gray-400">触发方式</div>
        </div>
      </div>

      {health?.last_error ? <NoticeBanner variant="error">x {health.last_error}</NoticeBanner> : null}
      {lastReasonCode ? (
        <NoticeBanner variant={lastReasonCode.includes('BLOCKED') ? 'warning' : 'info'}>
          reason={lastReasonCode}
          {lastGateState ? ` · gate=${lastGateState}` : ''}
          {lastMoodState ? ` · mood=${lastMoodState}` : ''}
          {lastImmuneDecision ? ` · immune=${lastImmuneDecision}${lastImmuneRisk ? `/${lastImmuneRisk}` : ''}` : ''}
          {lastImmuneReasonCodes.length > 0 ? ` · immune_reason=${lastImmuneReasonCodes.join('|')}` : ''}
          {` · cortisol=${lastCortisol} · energy=${lastEnergy}`}
        </NoticeBanner>
      ) : null}

      <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2 text-xs leading-6 text-gray-600">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded border px-2 py-0.5 font-medium ${resourceProfile.meta.chipClass}`}>{resourceProfile.meta.label}</span>
          {resourceProfile.uses_llm ? <span className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-blue-700">LLM</span> : null}
          {resourceProfile.uses_gpu ? <span className="rounded border border-rose-200 bg-rose-50 px-2 py-0.5 text-rose-700">GPU</span> : null}
        </div>
        <div className="mt-1 text-gray-500">{resourceProfile.meta.description}</div>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          onClick={() => onTrigger(task.task_id)}
          disabled={isLoading || !task.enabled}
          className="btn-secondary justify-center border-blue-200 text-xs text-blue-700 disabled:opacity-40"
        >
          <Play size={14} />
          {isLoading ? '处理中...' : '立即触发'}
        </button>
        {task.enabled ? (
          <button
            onClick={() => onDisable(task.task_id)}
            disabled={isLoading}
            className="btn-secondary justify-center text-xs disabled:opacity-40"
          >
            <PauseCircle size={14} />
            暂停
          </button>
        ) : (
          <button
            onClick={() => onEnable(task.task_id)}
            disabled={isLoading}
            className="btn-secondary justify-center border-green-200 text-xs text-green-700 disabled:opacity-40"
          >
            <Power size={14} />
            启用
          </button>
        )}
      </div>
      <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs leading-6 text-gray-500">
        trigger={task.trigger_mode || '-'} · mood={lastMoodState || '-'} · immune_reason={lastImmuneReasonCodes.join('|') || '-'}
      </div>
    </div>
  )
}
