import React from 'react'
import { ListChecks, PauseCircle, Play, Power, Search } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { AUTOMATION_PRIORITY_STYLES } from '../automationResourceModel.js'
import { getAutomationActionLabel } from '../automationSchedulerModel.js'
import { getTaskView } from '../automationTaskModel.js'
import AutomationSection from './AutomationSection.jsx'

function TaskRow({ item, selected, onSelect }) {
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

function TaskDetailPanel({ item, onEnable, onDisable, onTrigger, actionLoading }) {
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

function LoadingTasksNotice() {
  return (
    <LoadingNotice>正在同步节律任务脉络...</LoadingNotice>
  )
}

export default function AutomationTaskExecutionSection({
  actionLoading,
  actionResult,
  enabledCount,
  failedCount,
  filteredCount,
  filteredTasks,
  gateFilter,
  hasAnyTaskFilter,
  loading,
  onClearFilters,
  onDisableTask,
  onEnableTask,
  onSelectTask,
  onTaskSearchChange,
  onTriggerTask,
  reasonFilter,
  runningCount,
  schedulerState,
  selectedTask,
  taskSearch,
  tasks,
}) {
  return (
    <AutomationSection
      icon={ListChecks}
      title="节律任务执行域"
      description="按器官节律对任务进行观测、筛选与即时干预。"
      actions={hasAnyTaskFilter ? (
        <button
          onClick={onClearFilters}
          className="btn-secondary text-xs"
        >
          清除筛选
        </button>
      ) : null}
    >
      {hasAnyTaskFilter ? (
        <NoticeBanner variant="info" title="已应用节律筛选">
          <span className="font-mono">
            {reasonFilter ? `reason=${reasonFilter}` : ''}
            {reasonFilter && gateFilter ? ' · ' : ''}
            {gateFilter ? `gate=${gateFilter}` : ''}
            {taskSearch.trim() ? `${reasonFilter || gateFilter ? ' · ' : ''}keyword=${taskSearch.trim()}` : ''}
          </span>
          <span className="mx-1">，当前命中 {filteredCount} 个任务。</span>
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          {
            label: '节律状态',
            value: schedulerState.label,
            color: schedulerState.running ? 'text-green-600' : schedulerState.label === '未知' ? 'text-gray-500' : 'text-red-500',
          },
          { label: '任务总数', value: tasks.length, color: 'text-gray-800' },
          { label: '当前展示', value: filteredCount, color: hasAnyTaskFilter ? 'text-violet-600' : 'text-gray-500' },
          { label: '已启用', value: enabledCount, color: 'text-blue-600' },
          {
            label: '推进中任务',
            value: runningCount,
            color: runningCount > 0 ? 'text-blue-600' : 'text-gray-400',
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl border border-primary-100/70 bg-white/70 p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="mt-0.5 text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>

      {actionResult ? (
        actionResult.ok ? (
          <NoticeBanner variant="success">
            节律 <code className="font-mono">{actionResult.taskId}</code> 已执行「{getAutomationActionLabel(actionResult.action)}」。
          </NoticeBanner>
        ) : (
          <NoticeBanner variant="error">
            节律 <code className="font-mono">{actionResult.taskId}</code> 操作失败：{actionResult.error}
          </NoticeBanner>
        )
      ) : null}

      {failedCount > 0 ? (
        <NoticeBanner variant="warning" title="检测到异常节律">
          当前有 {failedCount} 个任务处于失败状态，请检查对应日志与依赖状态。
        </NoticeBanner>
      ) : null}

      <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
            placeholder="搜索任务、画像、LLM/GPU、原因、gate 或 immune reason..."
            value={taskSearch}
            onChange={(event) => onTaskSearchChange(event.target.value)}
          />
        </div>
      </div>

      {loading && tasks.length === 0 ? (
        <LoadingTasksNotice />
      ) : tasks.length === 0 ? (
        <EmptyStateCard title="暂无登记任务" hint="请确认节律任务已注册。" />
      ) : filteredTasks.length === 0 ? (
        <EmptyStateCard title="筛选条件无匹配任务" hint="可清除关键词、reason 或 gate 筛选后重试。" />
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
          <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
            <div className="grid grid-cols-1 gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500 lg:grid-cols-[minmax(0,1fr)_8rem_8.5rem_8rem]">
              <span>任务</span>
              <span>状态 / 画像</span>
              <span>门控</span>
              <span className="lg:text-right">免疫 / 内分泌</span>
            </div>
            <div className="max-h-[560px] divide-y divide-gray-100 overflow-y-auto">
              {filteredTasks.map((item, index) => (
                <TaskRow
                  key={item.task?.task_id || `task-${index}`}
                  item={item}
                  selected={selectedTask?.task?.task_id === item.task?.task_id}
                  onSelect={onSelectTask}
                />
              ))}
            </div>
          </div>
          <div className="xl:sticky xl:top-24 xl:self-start">
            <TaskDetailPanel
              item={selectedTask}
              onEnable={onEnableTask}
              onDisable={onDisableTask}
              onTrigger={onTriggerTask}
              actionLoading={actionLoading}
            />
          </div>
        </div>
      )}
    </AutomationSection>
  )
}
