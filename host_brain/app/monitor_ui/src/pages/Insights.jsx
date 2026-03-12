import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, ShieldCheck, AlertTriangle, Cpu, Bot } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import {
  fetchGovernorStats,
  fetchErrors,
  fetchSubsystemsStats,
  fetchAgentLoopStats,
} from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

// ===== 工具函数 =====

function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function statusColor(rate) {
  if (rate == null) return 'text-gray-400'
  if (rate >= 90) return 'text-green-600'
  if (rate >= 70) return 'text-orange-500'
  return 'text-red-500'
}

function statusBg(rate) {
  if (rate == null) return 'bg-gray-100'
  if (rate >= 90) return 'bg-green-50 border-green-200'
  if (rate >= 70) return 'bg-orange-50 border-orange-200'
  return 'bg-red-50 border-red-200'
}

const WINDOW_OPTIONS = [
  { label: '1h',  value: 60    },
  { label: '6h',  value: 360   },
  { label: '24h', value: 1440  },
  { label: '7d',  value: 10080 },
]

// ===== 子组件：统计卡片 =====
function StatCard({ label, value, sub, color = 'text-gray-800' }) {
  return (
    <div className="card p-4 text-center">
      <div className={`text-2xl font-bold ${color}`}>{value ?? '—'}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

// ===== 子组件：板块标题 =====
function SectionTitle({ icon: Icon, title, color = 'text-gray-700' }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon size={16} className={color} />
      <h2 className={`text-sm font-semibold ${color}`}>{title}</h2>
    </div>
  )
}

// ===== 主页面 =====
export default function Insights() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  const [govData,     setGovData]     = useState(null)
  const [errData,     setErrData]     = useState(null)
  const [subsysData,  setSubsysData]  = useState(null)
  const [agentData,   setAgentData]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const intervalMinutes = windowMinutes <= 60 ? 5 : windowMinutes <= 360 ? 30 : windowMinutes <= 1440 ? 60 : 360
      const [gov, err, subsys, agent] = await Promise.allSettled([
        fetchGovernorStats({ window_minutes: windowMinutes, interval_minutes: intervalMinutes }),
        fetchErrors({ window_minutes: windowMinutes, interval_minutes: intervalMinutes, limit: 15 }),
        fetchSubsystemsStats({ window_minutes: windowMinutes }),
        fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 8 }),
      ])
      if (gov.status === 'fulfilled')    setGovData(gov.value)
      if (err.status === 'fulfilled')    setErrData(err.value)
      if (subsys.status === 'fulfilled') setSubsysData(subsys.value)
      if (agent.status === 'fulfilled')  setAgentData(agent.value)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  return (
    <div className="space-y-8">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">监控洞察</h1>
          <p className="text-sm text-gray-500 mt-0.5">Governor 决策 · 错误追踪 · 子系统健康 · Agent Loop</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded border border-gray-300 overflow-hidden text-sm">
            {WINDOW_OPTIONS.map(({ label, value }) => (
              <button
                key={value}
                className={`px-2.5 py-1.5 transition-colors ${
                  windowMinutes === value
                    ? 'bg-primary text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => setWindowMinutes(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      {error && <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>}

      {/* ===== P1: Governor 决策漏斗 ===== */}
      <section>
        <SectionTitle icon={ShieldCheck} title="Governor 决策漏斗" color="text-blue-600" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总决策数"  value={govData?.total}     />
          <StatCard label="批准"      value={govData?.approved}  color="text-green-600" />
          <StatCard label="拒绝"      value={govData?.rejected}  color="text-red-500" />
          <StatCard
            label="拒绝率"
            value={govData?.rejection_rate != null ? `${govData.rejection_rate}%` : '—'}
            color={govData?.rejection_rate > 20 ? 'text-red-500' : govData?.rejection_rate > 10 ? 'text-orange-500' : 'text-green-600'}
          />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 时序趋势 */}
          <div className="card p-4">
            <LineChart
              title="决策趋势"
              xData={govData?.timeseries_labels ?? []}
              series={[
                { name: '批准', data: govData?.timeseries_approved ?? [], color: '#12B76A' },
                { name: '拒绝', data: govData?.timeseries_rejected ?? [], color: '#F04438' },
              ]}
            />
          </div>
          {/* reason_code 分布 */}
          <div className="card p-4">
            {(govData?.reason_codes?.length ?? 0) > 0 ? (
              <BarChart
                title="拒绝 reason_code 分布"
                direction="horizontal"
                categories={(govData?.reason_codes ?? []).map((r) => r.reason_code)}
                series={[{ name: '次数', data: (govData?.reason_codes ?? []).map((r) => r.count), color: '#F04438' }]}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
                <ShieldCheck size={24} className="mb-2 text-green-400" />
                <span className="text-xs font-medium text-gray-500">拒绝 reason_code 分布</span>
                <span className="mt-1">暂无拒绝记录</span>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ===== P1: 全局错误追踪 ===== */}
      <section>
        <SectionTitle icon={AlertTriangle} title="全局错误追踪" color="text-red-500" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="错误总数" value={errData?.total_errors} color={errData?.total_errors > 0 ? 'text-red-500' : 'text-green-600'} />
          {(errData?.stages ?? []).slice(0, 3).map((s) => (
            <StatCard key={s.stage} label={s.stage} value={s.count} sub={`${s.pct}%`} color="text-orange-500" />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 错误时序 */}
          <div className="card p-4">
            <LineChart
              title="错误趋势"
              xData={errData?.timeseries_labels ?? []}
              series={[{ name: '错误数', data: errData?.timeseries_counts ?? [], color: '#F04438' }]}
            />
          </div>
          {/* Top reason_code */}
          <div className="card p-4">
            {(errData?.top_codes?.length ?? 0) > 0 ? (
              <BarChart
                title="错误 reason_code Top10"
                direction="horizontal"
                categories={(errData?.top_codes ?? []).map((c) => c.reason_code)}
                series={[{ name: '次数', data: (errData?.top_codes ?? []).map((c) => c.count), color: '#F79009' }]}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
                <AlertTriangle size={24} className="mb-2 text-green-400" />
                <span className="text-xs font-medium text-gray-500">错误 reason_code Top10</span>
                <span className="mt-1">暂无错误记录</span>
              </div>
            )}
          </div>
        </div>
        {/* 最近错误列表 */}
        {(errData?.recent?.length ?? 0) > 0 && (
          <div className="card overflow-hidden mt-4">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              最近错误
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', 'Stage', 'Reason Code', '详情'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(errData?.recent ?? []).map((e, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(e.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs font-mono text-orange-600">{e.stage ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-red-600">{e.reason_code ?? '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs" title={e.detail}>{e.detail || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ===== P2: 子系统健康矩阵 ===== */}
      <section>
        <SectionTitle icon={Cpu} title="子系统健康矩阵" color="text-purple-600" />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {(subsysData?.subsystems ?? []).map((s) => (
            <div
              key={s.subsystem}
              className={`card p-3 border ${statusBg(s.success_rate)}`}
            >
              <div className="text-xs font-medium text-gray-700 mb-2 truncate" title={s.display_name}>
                {s.display_name}
              </div>
              <div className={`text-xl font-bold ${statusColor(s.success_rate)}`}>
                {s.success_rate != null ? `${s.success_rate}%` : '—'}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {s.total > 0 ? `${s.success}/${s.total} 成功` : '无数据'}
              </div>
              {s.p95_ms != null && (
                <div className="text-xs text-gray-400">P95 {s.p95_ms}ms</div>
              )}
              {s.last_ts && (
                <div className="text-xs text-gray-300 mt-1 truncate" title={formatTime(s.last_ts)}>
                  {formatTime(s.last_ts)}
                </div>
              )}
            </div>
          ))}
          {(subsysData?.subsystems ?? []).length === 0 && (
            <div className="col-span-full text-center py-10 text-gray-400 text-sm">暂无子系统数据</div>
          )}
        </div>
      </section>

      {/* ===== P3: Agent Loop 统计 ===== */}
      <section>
        <SectionTitle icon={Bot} title="Agent Loop 统计" color="text-indigo-600" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总执行次数"  value={agentData?.total_loops} />
          <StatCard label="目标达成"    value={agentData?.achieved}    color="text-green-600" />
          <StatCard label="未达成"      value={agentData?.failed}      color="text-red-500" />
          <StatCard
            label="达成率"
            value={agentData?.achievement_rate != null ? `${agentData.achievement_rate}%` : '—'}
            sub={agentData?.avg_rounds != null ? `平均 ${agentData.avg_rounds} 轮` : undefined}
            color={agentData?.achievement_rate >= 80 ? 'text-green-600' : agentData?.achievement_rate >= 50 ? 'text-orange-500' : 'text-red-500'}
          />
        </div>
        {(agentData?.recent_loops?.length ?? 0) > 0 && (
          <div className="card overflow-hidden">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              最近 Agent Loop 执行
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', '目标', '轮次', '指令', '成功/失败', '达成', '耗时'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(agentData?.recent_loops ?? []).map((l, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(l.ts_ms)}</td>
                    <td className="px-3 py-2 text-xs text-gray-700 max-w-xs truncate" title={l.goal}>{l.goal || '—'}</td>
                    <td className="px-3 py-2 text-xs text-center text-gray-600">{l.total_rounds}</td>
                    <td className="px-3 py-2 text-xs text-center text-gray-600">{l.total_actions}</td>
                    <td className="px-3 py-2 text-xs text-center">
                      <span className="text-green-600">{l.success_actions}</span>
                      <span className="text-gray-400">/</span>
                      <span className="text-red-500">{l.failed_actions}</span>
                    </td>
                    <td className="px-3 py-2 text-xs text-center">
                      {l.goal_achieved
                        ? <span className="text-green-600 font-medium">✓</span>
                        : <span className="text-red-500 font-medium">✗</span>
                      }
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500">
                      {l.total_latency_ms != null ? `${l.total_latency_ms}ms` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(agentData?.total_loops ?? 0) === 0 && (
          <div className="card p-8 text-center text-gray-400 text-sm">
            <Bot size={24} className="mx-auto mb-2 text-gray-300" />
            暂无 Agent Loop 执行记录
          </div>
        )}
      </section>
    </div>
  )
}
