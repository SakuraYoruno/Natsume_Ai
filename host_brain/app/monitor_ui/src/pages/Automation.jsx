import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, History, ListChecks, PauseCircle, Play, Power, RefreshCw, Search, Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  triggerSchedulerTask,
} from '../api/automation.js'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import ViewportLazySection from '../components/obs/ViewportLazySection.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { buildEndocrineOrganSystemModel } from '../models/organSystemModel.js'
import {
  useAutomationOverviewQuery,
  refreshSchedulerHistoryQuery,
} from '../queries/automationQueries.js'
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

const AutomationImmuneSectionContent = React.lazy(() => import('../components/automation/AutomationImmuneSectionContent.jsx'))
const AutomationHistorySectionContent = React.lazy(() => import('../components/automation/AutomationHistorySectionContent.jsx'))

function AutomationSection({ icon: Icon, title, description, actions = null, children }) {
  return (
    <section className="card space-y-4 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {Icon ? (
            <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2.5 text-primary">
              <Icon size={16} />
            </div>
          ) : null}
          <div>
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            {description ? <p className="mt-1 text-xs text-gray-500">{description}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
      {children}
    </section>
  )
}

function getTaskView(item) {
  const { task = {}, health = {} } = item || {}
  const status = health?.status || 'PENDING'
  const style = STATUS_STYLES[status] || STATUS_STYLES.PENDING
  const lastReasonCode = String(health?.last_reason_code || '').trim()
  const lastGateState = String(health?.last_gate_state || '').trim()
  const lastMoodState = String(health?.last_mood_state || '').trim()
  const lastImmuneDecision = String(health?.last_immune_decision || '').trim().toUpperCase()
  const lastImmuneRisk = String(health?.last_immune_risk_level || '').trim().toLowerCase()
  const lastImmuneReasonCodes = Array.isArray(health?.last_immune_reason_codes)
    ? health.last_immune_reason_codes.filter((code) => String(code || '').trim()).slice(0, 3)
    : []
  const lastCortisol = Number.isFinite(Number(health?.last_endocrine_cortisol))
    ? Number(health?.last_endocrine_cortisol).toFixed(2)
    : '—'
  const lastEnergy = Number.isFinite(Number(health?.last_endocrine_energy))
    ? Number(health?.last_endocrine_energy).toFixed(2)
    : '—'
  return {
    task,
    health,
    status,
    style,
    lastReasonCode,
    lastGateState,
    lastMoodState,
    lastImmuneDecision,
    lastImmuneRisk,
    lastImmuneReasonCodes,
    lastCortisol,
    lastEnergy,
  }
}

function TaskRow({ item, selected, onSelect }) {
  const {
    task,
    health,
    status,
    style,
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
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.MEDIUM}`}>
          {task.priority}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
      </div>
      <div className="min-w-0 text-xs text-gray-500">
        <div className="truncate">reason {lastReasonCode || '—'}</div>
        <div className="truncate">gate {lastGateState || '—'}</div>
      </div>
      <div className="min-w-0 text-xs text-gray-500 lg:text-right">
        <div className="truncate">{lastImmuneDecision ? `${lastImmuneDecision}${lastImmuneRisk ? `/${lastImmuneRisk}` : ''}` : 'immune —'}</div>
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
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.MEDIUM}`}>
            {task.priority}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
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
          <div className="font-bold text-gray-700">{task.trigger_mode || '—'}</div>
          <div className="text-gray-400">触发方式</div>
        </div>
      </div>

      {health?.last_error ? <NoticeBanner variant="error">✕ {health.last_error}</NoticeBanner> : null}
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

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          onClick={() => onTrigger(task.task_id)}
          disabled={isLoading || !task.enabled}
          className="btn-secondary justify-center border-blue-200 text-xs text-blue-700 disabled:opacity-40"
        >
          <Play size={14} />
          {isLoading ? '处理中…' : '立即触发'}
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
        trigger={task.trigger_mode || '—'} · mood={lastMoodState || '—'} · immune_reason={lastImmuneReasonCodes.join('|') || '—'}
      </div>
    </div>
  )
}

function LoadingTasksNotice() {
  return (
    <NoticeBanner variant="info" title="加载中">
      正在同步节律任务脉络…
    </NoticeBanner>
  )
}

export default function Automation() {
  const [searchParams, setSearchParams] = useSearchParams()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    error: runtimeError,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })
  const [actionLoading, setActionLoading] = useState('')
  const [actionResult, setActionResult] = useState(null)
  const [taskSearch, setTaskSearch] = useState('')
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [showOrganContext, setShowOrganContext] = useState(false)
  const [showImmuneOps, setShowImmuneOps] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const {
    tasks,
    health,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useAutomationOverviewQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const reasonFilter = String(searchParams.get('reason_code') || '').trim().toUpperCase()
  const gateFilter = String(searchParams.get('gate_state') || '').trim().toLowerCase()
  const immuneReplayRequestId = String(searchParams.get('immune_replay_request_id') || '').trim()
  const hasTaskFilters = Boolean(reasonFilter || gateFilter)
  const automationLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: overviewError && !tasks.length && !health ? new Error(overviewError) : null,
    hasData: Boolean(tasks.length > 0 || health),
    hasSamples: tasks.length > 0,
    available: Boolean(tasks.length > 0 || health),
    stale,
  })

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      refetch(),
      refreshSchedulerHistoryQuery({ windowMinutes: 1440 }),
      reloadRuntime(),
    ])
  }, [refetch, reloadRuntime])

  const handleAction = async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable') await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await Promise.allSettled([
        refetch(),
        refreshSchedulerHistoryQuery({ windowMinutes: 1440 }),
      ])
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

  const clearTaskFilters = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('reason_code')
    next.delete('gate_state')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const filteredTasks = useMemo(() => {
    const keyword = taskSearch.trim().toLowerCase()
    return tasks.filter((item) => {
      const taskReason = String(item?.health?.last_reason_code || '').trim().toUpperCase()
      const taskGate = String(item?.health?.last_gate_state || '').trim().toLowerCase()
      if (reasonFilter && taskReason !== reasonFilter) return false
      if (gateFilter && taskGate !== gateFilter) return false
      if (keyword) {
        const task = item?.task || {}
        const health = item?.health || {}
        const haystack = [
          task.task_id,
          task.description,
          task.priority,
          task.trigger_mode,
          health.status,
          health.last_reason_code,
          health.last_gate_state,
          health.last_immune_decision,
          ...(Array.isArray(health.last_immune_reason_codes) ? health.last_immune_reason_codes : []),
        ].map((value) => String(value || '').toLowerCase())
        if (!haystack.some((value) => value.includes(keyword))) return false
      }
      return true
    })
  }, [tasks, reasonFilter, gateFilter, taskSearch])

  const enabledCount = useMemo(() => tasks.filter((item) => item.task?.enabled).length, [tasks])
  const runningCount = useMemo(() => tasks.filter((item) => item.health?.status === 'RUNNING').length, [tasks])
  const failedCount = useMemo(() => tasks.filter((item) => item.health?.status === 'FAILED').length, [tasks])
  const filteredCount = filteredTasks.length
  const selectedTask = useMemo(
    () => filteredTasks.find((item) => item.task?.task_id === selectedTaskId) || filteredTasks[0] || null,
    [filteredTasks, selectedTaskId]
  )
  const hasAnyTaskFilter = hasTaskFilters || Boolean(taskSearch.trim())

  useEffect(() => {
    if (!filteredTasks.length) {
      if (selectedTaskId) setSelectedTaskId('')
      return
    }
    if (!filteredTasks.some((item) => item.task?.task_id === selectedTaskId)) {
      setSelectedTaskId(filteredTasks[0].task?.task_id || '')
    }
  }, [filteredTasks, selectedTaskId])

  const schedulerState = useMemo(() => {
    const rawStatus = String(health?.scheduler_status || '').toUpperCase()
    if (typeof health?.running === 'boolean') {
      if (!health.running) {
        return { running: false, label: '已停止', title: '节律当前未运行' }
      }
      if (rawStatus === 'DEGRADED') {
        return { running: true, label: '降级运行', title: '节律降级运行中' }
      }
      return { running: true, label: '运行中', title: '节律正在运行' }
    }
    if (rawStatus === 'DEGRADED') {
      return { running: true, label: '降级运行', title: '节律降级运行中' }
    }
    if (['HEALTHY', 'RUNNING', 'UP', 'OK'].includes(rawStatus)) {
      return { running: true, label: '运行中', title: '节律正在运行' }
    }
    if (['STOPPED', 'DOWN', 'DISABLED', 'OFFLINE'].includes(rawStatus)) {
      return { running: false, label: '已停止', title: '节律当前未运行' }
    }
    return { running: false, label: '未知', title: '节律状态未知' }
  }, [health])

  const actionNameMap = {
    enable: '启用',
    disable: '暂停',
    trigger: '立即触发',
  }
  const schedulerPulseNodes = useMemo(() => ([
    {
      label: '节律态',
      value: schedulerState.label,
      tone: schedulerState.running ? 'primary' : 'gray',
      active: schedulerState.running,
    },
    {
      label: '任务',
      value: String(tasks.length),
      tone: 'teal',
      active: tasks.length > 0,
    },
    {
      label: '启用',
      value: String(enabledCount),
      tone: 'violet',
      active: enabledCount > 0,
    },
    {
      label: '推进中',
      value: String(runningCount),
      tone: 'amber',
      active: runningCount > 0,
    },
    {
      label: '异常',
      value: String(failedCount),
      tone: 'rose',
      active: failedCount > 0,
    },
    {
      label: '已筛选',
      value: String(filteredCount),
      tone: 'gray',
      active: hasAnyTaskFilter,
    },
  ]), [enabledCount, failedCount, filteredCount, hasAnyTaskFilter, runningCount, schedulerState.label, schedulerState.running, tasks.length])
  const endocrineOrganModel = useMemo(() => buildEndocrineOrganSystemModel({
    loadState: automationLoadState,
    updatedAt: lastUpdatedAt,
    schedulerState,
    taskCount: tasks.length,
    enabledCount,
    runningCount,
    failedCount,
    filteredCount,
    hasTaskFilters: hasAnyTaskFilter,
    backgroundRuntime: runtimeSnapshot?.background_runtime,
  }), [
    automationLoadState,
    enabledCount,
    failedCount,
    filteredCount,
    hasAnyTaskFilter,
    lastUpdatedAt,
    runningCount,
    runtimeSnapshot?.background_runtime,
    schedulerState,
    tasks.length,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-800">器官执行台</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看常驻节律任务、免疫回放和历史脉络。</p>
        </div>
        <button
          onClick={refreshAll}
          disabled={loading || refreshing || runtimeLoading}
          className="btn-secondary text-xs disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading || refreshing || runtimeLoading ? 'animate-spin' : ''} />
          {loading || refreshing || runtimeLoading ? '刷新中' : '刷新'}
        </button>
      </div>

      <PageHero
        icon={Workflow}
        eyebrow="节律执行与回放"
        title={schedulerState.title}
        description="这里用于收口数字生命体的节律执行与免疫回放，把任务、原因和历史趋势放进同一段生命流里。"
        gradientClass="from-violet-50 via-white to-sky-50"
        metaItems={[
          `节律任务总数：${tasks.length}`,
          `已启用：${enabledCount}`,
          `推进中：${runningCount}`,
          `异常任务：${failedCount}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={automationLoadState}
            sampleSize={tasks.length}
            sampleLabel="节律任务"
            updatedAt={lastUpdatedAt}
            source="scheduler / automation"
            definition="Automation 页汇总节律任务总览、immune gate 运行态与 24h 历史脉络，页面主数据已经统一走 query 层。"
          />
        )}
      />

      {overviewError ? <NoticeBanner variant={tasks.length > 0 || health ? 'warning' : 'error'}>{overviewError}</NoticeBanner> : null}
      {runtimeError ? <NoticeBanner variant="warning">{runtimeError.message || String(runtimeError)}</NoticeBanner> : null}

      {!overviewError && !loading && !refreshing && automationLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前还没有已登记的节律任务样本。</NoticeBanner>
      ) : null}

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowOrganContext(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <Activity size={14} className="text-gray-400" />
            器官上下文与节律脉冲
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showOrganContext ? '已展开' : '展开'}
          </span>
        </summary>
        {showOrganContext ? (
          <div className="space-y-4 border-t border-gray-100 p-4">
            <OrganSystemOverview model={endocrineOrganModel} />
            <LivingPulseStrip
              title="节律脉冲"
              subtitle="这组节点用于快速判断节律中枢是否稳定。"
              nodes={schedulerPulseNodes}
            />
          </div>
        ) : null}
      </details>

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowImmuneOps(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <Activity size={14} className="text-gray-400" />
            生命体运行与免疫护栏
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showImmuneOps ? '已展开' : '展开'}
          </span>
        </summary>
        {showImmuneOps ? (
          <div className="border-t border-gray-100 p-4">
            <ViewportLazySection
              rootMargin="240px 0px"
              placeholder={(
                <NoticeBanner variant="info" title="按需加载中">
                  免疫护栏与回放分区将在滚动进入视口后展开。
                </NoticeBanner>
              )}
            >
              <React.Suspense
                fallback={(
                  <NoticeBanner variant="info" title="按需加载中">
                    正在装配生命体运行与免疫护栏分区…
                  </NoticeBanner>
                )}
              >
                <AutomationImmuneSectionContent
                  pollingEnabled={pollingEnabled}
                  pollingInterval={pollingInterval}
                  pollingTick={pollingTick}
                  initialReplayRequestId={immuneReplayRequestId}
                  runtimeSnapshot={runtimeSnapshot}
                  promptStats={promptStats}
                />
              </React.Suspense>
            </ViewportLazySection>
          </div>
        ) : null}
      </details>

      <AutomationSection
        icon={ListChecks}
        title="节律任务执行域"
        description="按器官节律对任务进行观测、筛选与即时干预。"
        actions={hasAnyTaskFilter ? (
          <button
            onClick={() => {
              clearTaskFilters()
              setTaskSearch('')
            }}
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
              节律 <code className="font-mono">{actionResult.taskId}</code> 已执行「{actionNameMap[actionResult.action] || actionResult.action}」。
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
              placeholder="搜索任务、原因、gate 或 immune reason..."
              value={taskSearch}
              onChange={(event) => setTaskSearch(event.target.value)}
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
                <span>状态</span>
                <span>门控</span>
                <span className="lg:text-right">免疫 / 内分泌</span>
              </div>
              <div className="max-h-[560px] divide-y divide-gray-100 overflow-y-auto">
                {filteredTasks.map((item) => (
                  <TaskRow
                    key={item.task?.task_id}
                    item={item}
                    selected={selectedTask?.task?.task_id === item.task?.task_id}
                    onSelect={setSelectedTaskId}
                  />
                ))}
              </div>
            </div>
            <div className="xl:sticky xl:top-24 xl:self-start">
              <TaskDetailPanel
                item={selectedTask}
                onEnable={(taskId) => handleAction(taskId, 'enable')}
                onDisable={(taskId) => handleAction(taskId, 'disable')}
                onTrigger={(taskId) => handleAction(taskId, 'trigger')}
                actionLoading={actionLoading}
              />
            </div>
          </div>
        )}
      </AutomationSection>

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowHistory(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <History size={14} className="text-gray-400" />
            节律历史脉络
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showHistory ? '已展开' : '展开'}
          </span>
        </summary>
        {showHistory ? (
          <div className="border-t border-gray-100 p-4">
            <ViewportLazySection
              rootMargin="240px 0px"
              placeholder={(
                <NoticeBanner variant="info" title="按需加载中">
                  节律历史脉络会在滚动接近后再展开，避免首屏提前拉取大块历史数据。
                </NoticeBanner>
              )}
            >
              <React.Suspense
                fallback={(
                  <NoticeBanner variant="info" title="按需加载中">
                    正在装配节律历史脉络分区…
                  </NoticeBanner>
                )}
              >
                <AutomationHistorySectionContent
                  pollingEnabled={pollingEnabled}
                  pollingInterval={pollingInterval}
                  pollingTick={pollingTick}
                />
              </React.Suspense>
            </ViewportLazySection>
          </div>
        ) : null}
      </details>
    </div>
  )
}
