import React, { useState, useEffect, useCallback } from 'react'
import {
  fetchSchedulerTasks,
  fetchSchedulerHealth,
  enableSchedulerTask,
  disableSchedulerTask,
  triggerSchedulerTask,
} from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

// ── 状态样式 ──────────────────────────────────────────────────────────────
const STATUS_STYLES = {
  PENDING:   { bg: 'bg-gray-100',   text: 'text-gray-600',   dot: 'bg-gray-400'   },
  RUNNING:   { bg: 'bg-blue-100',   text: 'text-blue-700',   dot: 'bg-blue-500 animate-pulse' },
  SUCCESS:   { bg: 'bg-green-100',  text: 'text-green-700',  dot: 'bg-green-500'  },
  FAILED:    { bg: 'bg-red-100',    text: 'text-red-700',    dot: 'bg-red-500'    },
  DISABLED:  { bg: 'bg-gray-100',   text: 'text-gray-400',   dot: 'bg-gray-300'   },
  CANCELLED: { bg: 'bg-yellow-100', text: 'text-yellow-700', dot: 'bg-yellow-400' },
}

const PRIORITY_STYLES = {
  HIGH:   'text-red-600 bg-red-50',
  MEDIUM: 'text-blue-600 bg-blue-50',
  LOW:    'text-gray-500 bg-gray-50',
}

// ── 任务卡片 ──────────────────────────────────────────────────────────────
function TaskCard({ item, onEnable, onDisable, onTrigger, actionLoading }) {
  const { task, health } = item
  const status = health?.status || 'PENDING'
  const style = STATUS_STYLES[status] || STATUS_STYLES.PENDING
  const isLoading = actionLoading === task.task_id

  return (
    <div className={`bg-white rounded-lg border p-4 space-y-3 ${task.enabled ? 'border-gray-200' : 'border-gray-100 opacity-70'}`}>
      {/* 头部 */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
            <span className="font-medium text-sm text-gray-800 truncate">{task.task_id}</span>
          </div>
          {task.description && (
            <div className="text-xs text-gray-500 mt-0.5 ml-4 truncate">{task.description}</div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.MEDIUM}`}>
            {task.priority}
          </span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${style.bg} ${style.text}`}>
            {status}
          </span>
        </div>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="bg-gray-50 rounded p-2 text-center">
          <div className="font-bold text-gray-700">{health?.run_count ?? 0}</div>
          <div className="text-gray-400">执行次数</div>
        </div>
        <div className="bg-gray-50 rounded p-2 text-center">
          <div className="font-bold text-gray-700">{health?.error_count ?? 0}</div>
          <div className="text-gray-400">失败次数</div>
        </div>
        <div className="bg-gray-50 rounded p-2 text-center">
          <div className="font-bold text-gray-700">{task.trigger_mode}</div>
          <div className="text-gray-400">触发模式</div>
        </div>
      </div>

      {/* 最近错误 */}
      {health?.last_error && (
        <div className="text-xs text-red-600 bg-red-50 rounded p-2 truncate">
          ✗ {health.last_error}
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex gap-2">
        <button
          onClick={() => onTrigger(task.task_id)}
          disabled={isLoading || !task.enabled}
          className="flex-1 text-xs border border-blue-300 text-blue-600 rounded px-2 py-1.5 hover:bg-blue-50 disabled:opacity-40 transition-colors"
        >
          {isLoading ? '...' : '▶ 立即触发'}
        </button>
        {task.enabled ? (
          <button
            onClick={() => onDisable(task.task_id)}
            disabled={isLoading}
            className="flex-1 text-xs border border-gray-300 text-gray-600 rounded px-2 py-1.5 hover:bg-gray-50 disabled:opacity-40 transition-colors"
          >
            暂停
          </button>
        ) : (
          <button
            onClick={() => onEnable(task.task_id)}
            disabled={isLoading}
            className="flex-1 text-xs border border-green-300 text-green-600 rounded px-2 py-1.5 hover:bg-green-50 disabled:opacity-40 transition-colors"
          >
            启用
          </button>
        )}
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────
export default function Automation() {
  const pollingEnabled  = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)

  const [tasks, setTasks]           = useState([])
  const [health, setHealth]         = useState(null)
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState(null)
  const [actionLoading, setActionLoading] = useState(null)
  const [actionResult, setActionResult]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [t, h] = await Promise.all([
        fetchSchedulerTasks(),
        fetchSchedulerHealth(),
      ])
      setTasks(Array.isArray(t) ? t : [])
      setHealth(h)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!pollingEnabled) return
    const id = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(id)
  }, [pollingEnabled, pollingInterval, load])

  const handleAction = async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable')  await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await load()
    } catch (e) {
      setActionResult({ ok: false, taskId, action, error: e.message })
    } finally {
      setActionLoading(null)
    }
  }

  const enabledCount  = tasks.filter(t => t.task?.enabled).length
  const runningCount  = tasks.filter(t => t.health?.status === 'RUNNING').length
  const failedCount   = tasks.filter(t => t.health?.status === 'FAILED').length

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">自动化调度</h1>
          <p className="text-sm text-gray-500 mt-0.5">HostTaskScheduler 常驻任务管理</p>
        </div>
        <button onClick={load} disabled={loading}
          className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50">
          {loading ? '刷新中...' : '刷新'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>
      )}

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: '调度器状态', value: health?.running ? '运行中' : '已停止', color: health?.running ? 'text-green-600' : 'text-red-500' },
          { label: '任务总数', value: tasks.length, color: 'text-gray-800' },
          { label: '已启用', value: enabledCount, color: 'text-blue-600' },
          { label: '执行中', value: runningCount, color: runningCount > 0 ? 'text-blue-600' : 'text-gray-400' },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-white rounded-lg border border-gray-200 p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-0.5">{label}</div>
          </div>
        ))}
      </div>

      {/* 操作结果提示 */}
      {actionResult && (
        <div className={`p-3 rounded text-sm ${actionResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {actionResult.ok
            ? `✓ 任务 ${actionResult.taskId} 操作成功（${actionResult.action}）`
            : `✗ 任务 ${actionResult.taskId} 操作失败：${actionResult.error}`
          }
        </div>
      )}

      {/* 失败任务警告 */}
      {failedCount > 0 && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
          ⚠ 有 {failedCount} 个任务处于失败状态，请检查任务日志
        </div>
      )}

      {/* 任务列表 */}
      {tasks.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-400 text-sm">
          {loading ? '加载中...' : '暂无注册任务'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {tasks.map(item => (
            <TaskCard
              key={item.task?.task_id}
              item={item}
              onEnable={id => handleAction(id, 'enable')}
              onDisable={id => handleAction(id, 'disable')}
              onTrigger={id => handleAction(id, 'trigger')}
              actionLoading={actionLoading}
            />
          ))}
        </div>
      )}
    </div>
  )
}
