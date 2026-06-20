import React from 'react'
import {
  CheckCircle2,
  History,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  XCircle,
} from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { formatTime } from '../../../shared/formatters.js'
import {
  getAgentLoopPhaseClass,
  isAgentLoopRunReady,
} from '../agentLoopModel.js'

function PhaseBadge({ phase }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${getAgentLoopPhaseClass(phase)}`}>
      {phase || 'UNKNOWN'}
    </span>
  )
}

export function AgentLoopRunControls({
  goal,
  setGoal,
  maxRounds,
  setMaxRounds,
  roundTimeout,
  setRoundTimeout,
  totalTimeout,
  setTotalTimeout,
  useMock,
  setUseMock,
  useEdgeRpc,
  setUseEdgeRpc,
  imageConsent,
  setImageConsent,
  running,
  onRun,
  onStop,
}) {
  return (
    <div className="card space-y-4 p-4">
      <div className="text-sm font-semibold text-gray-800">执行参数</div>

      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">执行目标</label>
        <textarea
          className="w-full resize-none rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-300"
          rows={4}
          placeholder="例如：打开设置页，切换到数字生命体总览，确认运行摘要卡可见。"
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          disabled={running}
        />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">最大轮次：{maxRounds}</label>
        <input type="range" min={1} max={20} step={1} value={maxRounds} onChange={(event) => setMaxRounds(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">单轮超时：{roundTimeout}s</label>
        <input type="range" min={5} max={120} step={5} value={roundTimeout} onChange={(event) => setRoundTimeout(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-gray-500">总超时：{totalTimeout}s</label>
        <input type="range" min={10} max={600} step={10} value={totalTimeout} onChange={(event) => setTotalTimeout(Number(event.target.value))} disabled={running} className="w-full accent-indigo-500" />
      </div>

      <div className="space-y-2">
        {[
          { label: '使用兜底模式', value: useMock, set: setUseMock },
          { label: '使用 edge RPC', value: useEdgeRpc, set: setUseEdgeRpc },
          { label: '允许上传截图', value: imageConsent, set: setImageConsent },
        ].map((item) => (
          <label key={item.label} className="flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={item.value}
              onChange={(event) => item.set(event.target.checked)}
              disabled={running}
              className="accent-indigo-500"
            />
            {item.label}
          </label>
        ))}
      </div>

      <div className="flex gap-2">
        <button
          className="flex flex-1 items-center justify-center gap-1.5 rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onRun}
          disabled={running || !isAgentLoopRunReady(goal)}
        >
          {running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? '执行中' : '启动'}
        </button>
        {running ? (
          <button className="rounded border border-red-300 px-3 py-2 text-red-600 hover:bg-red-50" onClick={onStop}>
            <Square size={14} />
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function AgentLoopRunSummaryCard({ runSummary }) {
  if (!runSummary) return null

  return (
    <div className={`card border p-4 ${runSummary.success ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}>
      <div className="mb-2 flex items-center gap-2">
        {runSummary.success ? <CheckCircle2 size={16} className="text-green-600" /> : <XCircle size={16} className="text-red-500" />}
        <span className={`text-sm font-semibold ${runSummary.success ? 'text-green-700' : 'text-red-600'}`}>
          {runSummary.success ? '目标已达成' : '目标未达成'}
        </span>
      </div>
      <div className="space-y-1 text-xs text-gray-600">
        <div>总轮次：{runSummary.rounds}</div>
        <div>总耗时：{runSummary.latency === '—' ? '—' : `${runSummary.latency}ms`}</div>
      </div>
    </div>
  )
}

export function AgentLoopEventsPanel({ events = [], eventsEndRef }) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-800">实时事件流</div>
        <span className="text-xs text-gray-400">{events.length} 条</span>
      </div>
      {events.length > 0 ? (
        <div className="max-h-[420px] space-y-2 overflow-y-auto">
          {events.map((event, index) => (
            <div key={`${event._ts || index}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3">
              <div className="flex items-center gap-2">
                <PhaseBadge phase={event.phase} />
                <span className="text-xs text-gray-400">{formatTime(event._ts)}</span>
                {event.round != null ? <span className="text-xs text-gray-500">第 {event.round} 轮</span> : null}
              </div>
              <div className="mt-2 text-sm text-gray-700">
                {event.summary || event.detail || event.error || event.raw || '无附加说明'}
              </div>
            </div>
          ))}
          <div ref={eventsEndRef} />
        </div>
      ) : (
        <EmptyStateCard
          title="等待执行"
          hint="启动 Agent Loop 后，这里会展示完整轮次事件。"
          className="p-8"
        />
      )}
    </div>
  )
}

export function AgentLoopHistoryPanel({ loopHistory = [] }) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
        <History size={14} />
        最近执行
      </div>
      {loopHistory.length > 0 ? (
        <div className="space-y-2">
          {loopHistory.map((item) => (
            <div key={`${item.request_id}-${item.ts_ms}`} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-medium text-gray-800">{item.goal || '未记录目标'}</div>
                {item.goal_achieved == null ? null : item.goal_achieved ? (
                  <CheckCircle2 size={14} className="text-green-600" />
                ) : (
                  <XCircle size={14} className="text-red-500" />
                )}
              </div>
              <div className="mt-1 space-y-1 text-xs text-gray-500">
                <div>状态：{item.final_status || '—'}</div>
                <div>轮次：{item.total_rounds ?? '—'} · 耗时：{item.total_latency_ms != null ? `${item.total_latency_ms}ms` : '—'}</div>
                <div>模式：{item.client_mode || '—'} · 时间：{formatTime(item.ts_ms)}</div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyStateCard title="暂无历史执行" hint="等执行完成后，这里会显示近 24 小时记录。" className="p-6" />
      )}
    </div>
  )
}

export function AgentLoopAssertionPanel({ uiAssertionData }) {
  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
        <ShieldAlert size={14} />
        断言守卫
      </div>
      {uiAssertionData ? (
        <div className="space-y-3 text-sm">
          <div className="rounded-lg bg-gray-50 px-3 py-3">
            <div className="text-xs text-gray-400">总断言</div>
            <div className="mt-1 text-lg font-semibold text-gray-900">{uiAssertionData.total_assertions ?? 0}</div>
          </div>
          <div className="rounded-lg bg-gray-50 px-3 py-3">
            <div className="text-xs text-gray-400">敏感拦截</div>
            <div className="mt-1 text-lg font-semibold text-orange-600">{uiAssertionData.sensitive_blocked ?? 0}</div>
          </div>
          <div className="rounded-lg bg-gray-50 px-3 py-3">
            <div className="text-xs text-gray-400">前置 / 后置失败</div>
            <div className="mt-1 text-lg font-semibold text-rose-600">
              {uiAssertionData.precondition_failed ?? 0} / {uiAssertionData.postcondition_failed ?? 0}
            </div>
          </div>
        </div>
      ) : (
        <EmptyStateCard title="暂无断言样本" hint="等待自动化执行后再查看守卫统计。" className="p-6" />
      )}
    </div>
  )
}
