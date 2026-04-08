import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Workflow } from 'lucide-react'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  fetchSchedulerHealth,
  fetchSchedulerHistory,
  fetchSchedulerTasks,
  triggerSchedulerTask,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'

const STATUS_STYLES = {
  PENDING: { bg: 'bg-gray-100', text: 'text-gray-600', dot: 'bg-gray-400' },
  RUNNING: { bg: 'bg-blue-100', text: 'text-blue-700', dot: 'bg-blue-500 animate-pulse' },
  SUCCESS: { bg: 'bg-green-100', text: 'text-green-700', dot: 'bg-green-500' },
  FAILED: { bg: 'bg-red-100', text: 'text-red-700', dot: 'bg-red-500' },
  DISABLED: { bg: 'bg-gray-100', text: 'text-gray-400', dot: 'bg-gray-300' },
  CANCELLED: { bg: 'bg-yellow-100', text: 'text-yellow-700', dot: 'bg-yellow-400' },
}

const PRIORITY_STYLES = {
  HIGH: 'text-red-600 bg-red-50',
  MEDIUM: 'text-blue-600 bg-blue-50',
  LOW: 'text-gray-500 bg-gray-50',
}

function TaskCard({ item, onEnable, onDisable, onTrigger, actionLoading }) {
  const { task, health } = item
  const status = health?.status || 'PENDING'
  const style = STATUS_STYLES[status] || STATUS_STYLES.PENDING
  const isLoading = actionLoading === task.task_id

  return (
    <div className={`space-y-3 rounded-lg border bg-white p-4 ${task.enabled ? 'border-gray-200' : 'border-gray-100 opacity-75'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
            <span className="truncate text-sm font-medium text-gray-800">{task.task_id}</span>
          </div>
          {task.description ? <div className="ml-4 mt-0.5 truncate text-xs text-gray-500">{task.description}</div> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.MEDIUM}`}>
            {task.priority}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.run_count ?? 0}</div>
          <div className="text-gray-400">执行次数</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.error_count ?? 0}</div>
          <div className="text-gray-400">失败次数</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{task.trigger_mode || '—'}</div>
          <div className="text-gray-400">触发模式</div>
        </div>
      </div>

      {health?.last_error ? <NoticeBanner variant="error">✕ {health.last_error}</NoticeBanner> : null}

      <div className="flex gap-2">
        <button
          onClick={() => onTrigger(task.task_id)}
          disabled={isLoading || !task.enabled}
          className="flex-1 rounded border border-blue-300 px-2 py-1.5 text-xs text-blue-600 transition-colors hover:bg-blue-50 disabled:opacity-40"
        >
          {isLoading ? '处理中…' : '▶ 立即触发'}
        </button>
        {task.enabled ? (
          <button
            onClick={() => onDisable(task.task_id)}
            disabled={isLoading}
            className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-40"
          >
            暂停
          </button>
        ) : (
          <button
            onClick={() => onEnable(task.task_id)}
            disabled={isLoading}
            className="flex-1 rounded border border-green-300 px-2 py-1.5 text-xs text-green-600 transition-colors hover:bg-green-50 disabled:opacity-40"
          >
            启用
          </button>
        )}
      </div>
    </div>
  )
}

function SchedulerTimelineChart({ tasks }) {
  if (!tasks || tasks.length === 0) return null
  const maxRun = Math.max(...tasks.map((task) => task.run_count ?? 0), 1)

  return (
    <div className="mt-4">
      <div className="mb-3 text-xs font-semibold text-gray-600">各任务执行量对比（近 24h）</div>
      <div className="space-y-2">
        {tasks.map((task) => {
          const runPct = Math.max(((task.run_count ?? 0) / maxRun) * 100, 1)
          const errPct = task.run_count > 0 ? Math.min(((task.error_count ?? 0) / task.run_count) * 100, 100) : 0
          return (
            <div key={task.task_id} className="flex items-center gap-2">
              <div className="w-40 shrink-0 truncate text-xs text-gray-600" title={task.task_id}>
                {task.task_id}
              </div>
              <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-gray-100">
                <div className="absolute left-0 top-0 h-3 rounded-full bg-green-400 transition-all" style={{ width: `${runPct}%` }} />
                {errPct > 0 ? (
                  <div className="absolute left-0 top-0 h-3 rounded-full bg-red-400 transition-all" style={{ width: `${(runPct * errPct) / 100}%` }} />
                ) : null}
              </div>
              <div className="w-20 shrink-0 text-right text-xs text-gray-500">
                {task.run_count ?? 0} 次
                {task.error_count > 0 ? <span className="ml-1 text-red-500">/ {task.error_count} 失败</span> : null}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs text-gray-400">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-green-400" />
          执行次数
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-red-400" />
          失败次数（叠加）
        </span>
      </div>
    </div>
  )
}

function SchedulerHistorySection() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const history = await fetchSchedulerHistory({ window_minutes: 1440 })
      setData(history)
    } catch (fetchError) {
      setError(fetchError.message || '加载执行历史失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-700">执行历史趋势（近 24h）</div>
        <button
          onClick={loadHistory}
          disabled={loading}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      {loading && !data ? (
        <NoticeBanner variant="info" title="加载中">
          正在同步执行历史趋势…
        </NoticeBanner>
      ) : null}

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {[
          { label: '总任务', value: data?.total_tasks, color: 'text-gray-700' },
          { label: '运行中', value: data?.running_count, color: 'text-blue-600' },
          { label: '成功', value: data?.success_count, color: 'text-green-600' },
          { label: '失败', value: data?.failed_count, color: 'text-red-500' },
          { label: '已禁用', value: data?.disabled_count, color: 'text-gray-400' },
          {
            label: '整体错误率',
            value: data?.overall_error_rate != null ? `${data.overall_error_rate}%` : '—',
            color: data?.overall_error_rate > 20 ? 'text-red-500' : 'text-green-600',
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded bg-gray-50 p-2 text-center">
            <div className={`text-lg font-bold ${color}`}>{value ?? '—'}</div>
            <div className="mt-0.5 text-xs text-gray-400">{label}</div>
          </div>
        ))}
      </div>

      {!loading && (data?.tasks ?? []).length > 0 ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="px-3 py-2 text-left font-medium text-gray-500">任务 ID</th>
                  <th className="px-3 py-2 text-center font-medium text-gray-500">状态</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">执行次数</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">失败次数</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">错误率</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">最近错误</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {(data?.tasks ?? []).map((task) => (
                  <tr key={task.task_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-blue-600">{task.task_id}</td>
                    <td className="px-3 py-2 text-center">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          task.status === 'SUCCESS'
                            ? 'bg-green-100 text-green-700'
                            : task.status === 'FAILED'
                              ? 'bg-red-100 text-red-700'
                              : task.status === 'RUNNING'
                                ? 'bg-blue-100 text-blue-700'
                                : 'bg-gray-100 text-gray-500'
                        }`}
                      >
                        {task.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-700">{task.run_count}</td>
                    <td className="px-3 py-2 text-right text-red-500">{task.error_count}</td>
                    <td className="px-3 py-2 text-right">
                      <span className={task.error_rate > 20 ? 'text-red-500' : 'text-green-600'}>
                        {task.error_rate != null ? `${task.error_rate}%` : '—'}
                      </span>
                    </td>
                    <td className="max-w-xs truncate px-3 py-2 text-gray-400">{task.last_error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <SchedulerTimelineChart tasks={data.tasks} />
        </>
      ) : !loading ? (
        <EmptyStateCard title="暂无执行历史" hint="等待任务运行后会出现趋势数据。" className="p-6" />
      ) : null}
    </div>
  )
}

function LoadingTasksNotice() {
  return (
    <NoticeBanner variant="info" title="加载中">
      正在同步调度任务列表…
    </NoticeBanner>
  )
}

export default function Automation() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [tasks, setTasks] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState('')
  const [actionResult, setActionResult] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [taskList, schedulerHealth] = await Promise.all([fetchSchedulerTasks(), fetchSchedulerHealth()])
      setTasks(Array.isArray(taskList) ? taskList : [])
      setHealth(schedulerHealth)
    } catch (fetchError) {
      setError(fetchError.message || '加载调度数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, load])

  const handleAction = async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable') await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await load()
    } catch (actionError) {
      setActionResult({
        ok: false,
        taskId,
        action,
        error: actionError.message || '任务操作失败',
      })
    } finally {
      setActionLoading('')
    }
  }

  const enabledCount = useMemo(() => tasks.filter((item) => item.task?.enabled).length, [tasks])
  const runningCount = useMemo(() => tasks.filter((item) => item.health?.status === 'RUNNING').length, [tasks])
  const failedCount = useMemo(() => tasks.filter((item) => item.health?.status === 'FAILED').length, [tasks])
  const schedulerState = useMemo(() => {
    const rawStatus = String(health?.scheduler_status || '').toUpperCase()
    if (typeof health?.running === 'boolean') {
      if (!health.running) {
        return { running: false, label: '已停止', title: '调度器当前未运行' }
      }
      if (rawStatus === 'DEGRADED') {
        return { running: true, label: '降级运行', title: '调度器降级运行中' }
      }
      return { running: true, label: '运行中', title: '调度器正在运行' }
    }
    if (rawStatus === 'DEGRADED') {
      return { running: true, label: '降级运行', title: '调度器降级运行中' }
    }
    if (['HEALTHY', 'RUNNING', 'UP', 'OK'].includes(rawStatus)) {
      return { running: true, label: '运行中', title: '调度器正在运行' }
    }
    if (['STOPPED', 'DOWN', 'DISABLED', 'OFFLINE'].includes(rawStatus)) {
      return { running: false, label: '已停止', title: '调度器当前未运行' }
    }
    return { running: false, label: '未知', title: '调度器状态未知' }
  }, [health])

  const actionNameMap = {
    enable: '启用',
    disable: '暂停',
    trigger: '立即触发',
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">自动化调度</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看 HostTaskScheduler 常驻任务的状态、历史和手动触发入口。</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Workflow}
        eyebrow="自动化与调度"
        title={schedulerState.title}
        description="这里用于收口数字生命后台常驻任务，包括启停状态、执行次数、失败告警和近 24 小时历史趋势。"
        gradientClass="from-violet-50 via-white to-sky-50"
        metaItems={[
          `任务总数：${tasks.length}`,
          `已启用：${enabledCount}`,
          `运行中：${runningCount}`,
          `失败任务：${failedCount}`,
        ]}
      />

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          {
            label: '调度器状态',
            value: schedulerState.label,
            color: schedulerState.running ? 'text-green-600' : schedulerState.label === '未知' ? 'text-gray-500' : 'text-red-500',
          },
          { label: '任务总数', value: tasks.length, color: 'text-gray-800' },
          { label: '已启用', value: enabledCount, color: 'text-blue-600' },
          {
            label: '运行中任务',
            value: runningCount,
            color: runningCount > 0 ? 'text-blue-600' : 'text-gray-400',
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-lg border border-gray-200 bg-white p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="mt-0.5 text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>

      {actionResult ? (
        actionResult.ok ? (
          <NoticeBanner variant="success">
            任务 <code className="font-mono">{actionResult.taskId}</code> 已执行「{actionNameMap[actionResult.action] || actionResult.action}」。
          </NoticeBanner>
        ) : (
          <NoticeBanner variant="error">
            任务 <code className="font-mono">{actionResult.taskId}</code> 操作失败：{actionResult.error}
          </NoticeBanner>
        )
      ) : null}

      {failedCount > 0 ? (
        <NoticeBanner variant="warning" title="检测到失败任务">
          当前有 {failedCount} 个任务处于失败状态，请检查任务日志与依赖健康状态。
        </NoticeBanner>
      ) : null}

      {loading && tasks.length === 0 ? (
        <LoadingTasksNotice />
      ) : tasks.length === 0 ? (
        <EmptyStateCard title="暂无注册任务" hint="请确认调度任务已注册。" />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {tasks.map((item) => (
            <TaskCard
              key={item.task?.task_id}
              item={item}
              onEnable={(taskId) => handleAction(taskId, 'enable')}
              onDisable={(taskId) => handleAction(taskId, 'disable')}
              onTrigger={(taskId) => handleAction(taskId, 'trigger')}
              actionLoading={actionLoading}
            />
          ))}
        </div>
      )}

      <SchedulerHistorySection />
    </div>
  )
}
