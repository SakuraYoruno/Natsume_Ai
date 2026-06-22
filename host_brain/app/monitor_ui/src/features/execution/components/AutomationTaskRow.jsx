import { AUTOMATION_PRIORITY_STYLES } from '../automationResourceModel.js'
import { getTaskView } from '../automationTaskModel.js'

export default function AutomationTaskRow({ item, selected, onSelect }) {
  const {
    task,
    status,
    style,
    resourceProfile,
    lastReasonCode,
    lastGateState,
    lastImmuneDecision,
    lastImmuneRisk,
    lastCortisol,
    lastEnergy,
  } = getTaskView(item)
  return (
    <button
      type="button"
      className={`grid w-full grid-cols-1 gap-2 px-3 py-3 text-left transition-colors lg:grid-cols-[minmax(0,1fr)_8rem_8.5rem_8rem] lg:items-center ${
        selected ? 'bg-violet-50' : 'bg-white hover:bg-gray-50'
      } ${task.enabled ? '' : 'opacity-70 saturate-75'}`}
      onClick={() => onSelect(task.task_id)}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
          <span className="truncate font-mono text-sm font-semibold text-gray-800">{task.task_id}</span>
        </div>
        <div className="mt-0.5 truncate text-xs text-gray-500">{task.description || '暂无描述'}</div>
      </div>
      <div className="flex flex-wrap gap-1">
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${AUTOMATION_PRIORITY_STYLES[task.priority] || AUTOMATION_PRIORITY_STYLES.MEDIUM}`}>
          {task.priority}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
        <span className={`rounded border px-1.5 py-0.5 text-xs ${resourceProfile.meta.chipClass}`}>{resourceProfile.meta.shortLabel}</span>
      </div>
      <div className="min-w-0 text-xs text-gray-500">
        <div className="truncate">reason {lastReasonCode || '-'}</div>
        <div className="truncate">gate {lastGateState || '-'}</div>
      </div>
      <div className="min-w-0 text-xs text-gray-500 lg:text-right">
        <div className="truncate">{lastImmuneDecision ? `${lastImmuneDecision}${lastImmuneRisk ? `/${lastImmuneRisk}` : ''}` : 'immune -'}</div>
        <div className="truncate">cortisol {lastCortisol} · energy {lastEnergy}</div>
      </div>
    </button>
  )
}
