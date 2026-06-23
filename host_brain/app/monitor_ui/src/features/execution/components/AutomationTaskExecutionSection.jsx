import { ListChecks, Search } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { getAutomationActionLabel } from '../automationSchedulerModel.js'
import AutomationSection from './AutomationSection.jsx'
import TaskDetailPanel from './AutomationTaskDetailPanel.jsx'
import TaskRow from './AutomationTaskRow.jsx'

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
