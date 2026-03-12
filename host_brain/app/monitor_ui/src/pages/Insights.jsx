import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, ShieldCheck, AlertTriangle, Cpu, Bot, Database, Tv, FileWarning, Zap, GitBranch, Layers, ShieldAlert, TriangleAlert, Lock, BookLock, GitMerge, Minimize2, FileCode, Archive } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import {
  fetchGovernorStats,
  fetchErrors,
  fetchSubsystemsStats,
  fetchAgentLoopStats,
  fetchMemoryStats,
  fetchVtsStats,
  fetchQaStats,
  fetchLlmCallStats,
  fetchLlmRouteStats,
  fetchContextPackStats,
  fetchUIAssertionStats,
  fetchFailureStats,
  fetchPermissionStats,
  fetchMemoryGuardStats,
  fetchStateArbiterStats,
  fetchContextCompressorStats,
  fetchPromptBuilderStats,
  fetchEpisodicStoreStats,
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
  const [memoryData,  setMemoryData]  = useState(null)
  const [vtsData,     setVtsData]     = useState(null)
  const [qaData,      setQaData]      = useState(null)
  const [llmCallData, setLlmCallData] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const intervalMinutes = windowMinutes <= 60 ? 5 : windowMinutes <= 360 ? 30 : windowMinutes <= 1440 ? 60 : 360
      const [gov, err, subsys, agent, memory, vts, qa, llmCall] = await Promise.allSettled([
        fetchGovernorStats({ window_minutes: windowMinutes, interval_minutes: intervalMinutes }),
        fetchErrors({ window_minutes: windowMinutes, interval_minutes: intervalMinutes, limit: 15 }),
        fetchSubsystemsStats({ window_minutes: windowMinutes }),
        fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 8 }),
        fetchMemoryStats({ window_minutes: windowMinutes }),
        fetchVtsStats({ window_minutes: windowMinutes }),
        fetchQaStats({ window_minutes: windowMinutes, interval_minutes: intervalMinutes, limit: 15 }),
        fetchLlmCallStats({ window_minutes: windowMinutes, interval_minutes: intervalMinutes }),
      ])
      if (gov.status === 'fulfilled')     setGovData(gov.value)
      if (err.status === 'fulfilled')     setErrData(err.value)
      if (subsys.status === 'fulfilled')  setSubsysData(subsys.value)
      if (agent.status === 'fulfilled')   setAgentData(agent.value)
      if (memory.status === 'fulfilled')  setMemoryData(memory.value)
      if (vts.status === 'fulfilled')     setVtsData(vts.value)
      if (qa.status === 'fulfilled')      setQaData(qa.value)
      if (llmCall.status === 'fulfilled') setLlmCallData(llmCall.value)
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

      {/* ===== TODO-38: VTS 通道统计 ===== */}
      <section>
        <SectionTitle icon={Tv} title="VTS 通道统计" color="text-pink-600" />
        {/* 聚合指标卡片 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard
            label="总事件数"
            value={vtsData?.total_events ?? '—'}
          />
          <StatCard
            label="表情应用率"
            value={vtsData?.apply_rate != null ? `${vtsData.apply_rate}%` : '—'}
            color={vtsData?.apply_rate >= 80 ? 'text-green-600' : vtsData?.apply_rate >= 50 ? 'text-orange-500' : 'text-red-500'}
          />
          <StatCard
            label="降级率"
            value={vtsData?.degrade_rate != null ? `${vtsData.degrade_rate}%` : '—'}
            color={vtsData?.degrade_rate > 20 ? 'text-orange-500' : 'text-green-600'}
          />
          <StatCard
            label="拒绝率"
            value={vtsData?.reject_rate != null ? `${vtsData.reject_rate}%` : '—'}
            color={vtsData?.reject_rate > 10 ? 'text-red-500' : 'text-green-600'}
          />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Top 情感标签横向柱状图 */}
          <div className="card p-4">
            {(vtsData?.top_emotions?.length ?? 0) > 0 ? (
              <BarChart
                title="Top 情感标签分布"
                direction="horizontal"
                categories={(vtsData?.top_emotions ?? []).map((e) => e.emotion_tag)}
                series={[{ name: '次数', data: (vtsData?.top_emotions ?? []).map((e) => e.count), color: '#EC4899' }]}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
                <Tv size={24} className="mb-2 text-gray-300" />
                <span className="text-xs font-medium text-gray-500">Top 情感标签分布</span>
                <span className="mt-1">暂无 VTS 事件数据</span>
              </div>
            )}
          </div>
          {/* Talk Gate 统计 */}
          <div className="card p-4">
            <div className="text-xs font-medium text-gray-500 mb-3">Talk Gate 统计</div>
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center">
                <div className="text-2xl font-bold text-gray-700">{vtsData?.talk_gate_total ?? '—'}</div>
                <div className="text-xs text-gray-400 mt-1">总请求</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-green-600">{vtsData?.talk_gate_allowed ?? '—'}</div>
                <div className="text-xs text-gray-400 mt-1">允许</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-red-500">{vtsData?.talk_gate_blocked ?? '—'}</div>
                <div className="text-xs text-gray-400 mt-1">拒绝</div>
              </div>
            </div>
            {vtsData?.talk_gate_total > 0 && (
              <div className="mt-4">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>允许率</span>
                  <span>{Math.round((vtsData.talk_gate_allowed / vtsData.talk_gate_total) * 100)}%</span>
                </div>
                <div className="w-full bg-gray-100 rounded-full h-2">
                  <div
                    className="bg-green-500 h-2 rounded-full transition-all"
                    style={{ width: `${Math.round((vtsData.talk_gate_allowed / vtsData.talk_gate_total) * 100)}%` }}
                  />
                </div>
              </div>
            )}
            {(vtsData?.talk_gate_total ?? 0) === 0 && (
              <div className="flex flex-col items-center justify-center h-32 text-gray-400 text-sm">
                <span>暂无 Talk Gate 数据</span>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ===== TODO-40: QA 违规统计 ===== */}
      <section>
        <SectionTitle icon={FileWarning} title="QA 违规统计" color="text-red-600" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard
            label="违规总数"
            value={qaData?.total_violations ?? '—'}
            color={qaData?.total_violations > 0 ? 'text-red-500' : 'text-green-600'}
          />
          <StatCard
            label="降级次数"
            value={qaData?.degraded_count ?? '—'}
            color={qaData?.degraded_count > 0 ? 'text-orange-500' : 'text-green-600'}
          />
          <StatCard
            label="降级率"
            value={qaData?.degrade_rate != null ? `${qaData.degrade_rate}%` : '—'}
            color={qaData?.degrade_rate > 30 ? 'text-red-500' : qaData?.degrade_rate > 10 ? 'text-orange-500' : 'text-green-600'}
          />
          <StatCard
            label="重写成功率"
            value={qaData?.rewrite_success_rate != null ? `${qaData.rewrite_success_rate}%` : '—'}
            color={qaData?.rewrite_success_rate >= 80 ? 'text-green-600' : 'text-orange-500'}
          />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 违规码分布 */}
          <div className="card p-4">
            {(qaData?.violation_codes?.length ?? 0) > 0 ? (
              <BarChart
                title="违规码分布"
                direction="horizontal"
                categories={(qaData?.violation_codes ?? []).map((c) => c.violation_code)}
                series={[{ name: '次数', data: (qaData?.violation_codes ?? []).map((c) => c.count), color: '#F04438' }]}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
                <FileWarning size={24} className="mb-2 text-green-400" />
                <span className="text-xs font-medium text-gray-500">违规码分布</span>
                <span className="mt-1">暂无 QA 违规记录</span>
              </div>
            )}
          </div>
          {/* 违规时序趋势 */}
          <div className="card p-4">
            <LineChart
              title="违规趋势"
              xData={qaData?.timeseries_labels ?? []}
              series={[{ name: '违规数', data: qaData?.timeseries_counts ?? [], color: '#F04438' }]}
            />
          </div>
        </div>
        {/* 最近违规列表 */}
        {(qaData?.recent_violations?.length ?? 0) > 0 && (
          <div className="card overflow-hidden mt-4">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              最近 QA 违规
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', '节点', '违规码', '处置', '详情'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(qaData?.recent_violations ?? []).map((v, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(v.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">{v.node_id ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-red-600">{v.violation_code ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-orange-500">{v.action ?? '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs" title={v.detail}>{v.detail || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ===== TODO-41: LLM 调用成本统计 ===== */}
      <section>
        <SectionTitle icon={Zap} title="LLM 调用成本统计" color="text-amber-600" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总调用次数" value={llmCallData?.total_calls ?? '—'} />
          <StatCard
            label="总 Token 消耗"
            value={llmCallData?.total_tokens != null ? llmCallData.total_tokens.toLocaleString() : '—'}
            color="text-amber-600"
          />
          <StatCard
            label="Prompt Tokens"
            value={llmCallData?.total_prompt_tokens != null ? llmCallData.total_prompt_tokens.toLocaleString() : '—'}
            color="text-blue-600"
          />
          <StatCard
            label="平均延迟"
            value={llmCallData?.avg_latency_ms != null ? `${llmCallData.avg_latency_ms}ms` : '—'}
            color={llmCallData?.avg_latency_ms > 2000 ? 'text-red-500' : llmCallData?.avg_latency_ms > 1000 ? 'text-orange-500' : 'text-green-600'}
          />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 按 model_id 分组的 token 消耗 */}
          <div className="card p-4">
            {(llmCallData?.models?.length ?? 0) > 0 ? (
              <BarChart
                title="各模型 Token 消耗"
                direction="horizontal"
                categories={(llmCallData?.models ?? []).map((m) => m.model_id)}
                series={[
                  { name: 'Prompt', data: (llmCallData?.models ?? []).map((m) => m.total_prompt_tokens), color: '#2970FF' },
                  { name: 'Completion', data: (llmCallData?.models ?? []).map((m) => m.total_completion_tokens), color: '#F79009' },
                ]}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
                <Zap size={24} className="mb-2 text-gray-300" />
                <span className="text-xs font-medium text-gray-500">各模型 Token 消耗</span>
                <span className="mt-1">暂无 LLM 调用数据</span>
              </div>
            )}
          </div>
          {/* 调用量时序趋势 */}
          <div className="card p-4">
            <LineChart
              title="LLM 调用量趋势"
              xData={llmCallData?.timeseries_labels ?? []}
              series={[
                { name: '调用次数', data: llmCallData?.timeseries_calls ?? [], color: '#2970FF' },
              ]}
            />
          </div>
        </div>
        {/* 各模型详情表格 */}
        {(llmCallData?.models?.length ?? 0) > 0 && (
          <div className="card overflow-hidden mt-4">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              各模型调用详情
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['模型 ID', '调用次数', 'Prompt Tokens', 'Completion Tokens', '总 Tokens', '平均延迟', 'P95 延迟'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(llmCallData?.models ?? []).map((m, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">{m.model_id}</td>
                    <td className="px-4 py-2 text-xs text-gray-700 text-center">{m.call_count}</td>
                    <td className="px-4 py-2 text-xs text-blue-500 text-right">{m.total_prompt_tokens.toLocaleString()}</td>
                    <td className="px-4 py-2 text-xs text-amber-500 text-right">{m.total_completion_tokens.toLocaleString()}</td>
                    <td className="px-4 py-2 text-xs font-medium text-gray-700 text-right">{m.total_tokens.toLocaleString()}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 text-right">{m.avg_latency_ms != null ? `${m.avg_latency_ms}ms` : '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 text-right">{m.p95_latency_ms != null ? `${m.p95_latency_ms}ms` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ===== TODO-47: LLM 路由决策统计 ===== */}
      <RouteStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-49: ContextPack token 预算统计 ===== */}
      <ContextPackSection windowMinutes={windowMinutes} />

      {/* ===== TODO-50: UIAssertion 统计 ===== */}
      <UIAssertionSection windowMinutes={windowMinutes} />

      {/* ===== TODO-53: FailureMatrix 失败事件统计 ===== */}
      <FailureStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-54: PermissionGuard 权限拒绝统计 ===== */}
      <PermissionStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-59: MemoryGuard 宪法校验统计 ===== */}
      <MemoryGuardStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-60: StateArbiter 事实仲裁统计 ===== */}
      <StateArbiterStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-61: ContextCompressor 压缩效率统计 ===== */}
      <ContextCompressorStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-65: PromptBuilder 构建统计 ===== */}
      <PromptBuilderStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-66: EpisodicStore 写入统计 ===== */}
      <EpisodicStoreStatsSection windowMinutes={windowMinutes} />

      {/* ===== TODO-32: Memory 子系统统计 ===== */}
      <section>
        <SectionTitle icon={Database} title="Memory 子系统统计" color="text-teal-600" />
        {/* 聚合指标卡片 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard
            label="EpisodicStore 写入量"
            value={memoryData?.total_writes ?? '—'}
            color="text-teal-600"
          />
          <StatCard
            label="MemoryGuard 拒绝率"
            value={memoryData?.guard_reject_rate != null ? `${memoryData.guard_reject_rate}%` : '—'}
            color={memoryData?.guard_reject_rate > 30 ? 'text-red-500' : memoryData?.guard_reject_rate > 10 ? 'text-orange-500' : 'text-green-600'}
          />
          <StatCard
            label="ContextCompressor 压缩率"
            value={memoryData?.compress_rate != null ? `${memoryData.compress_rate}%` : '—'}
            color="text-blue-600"
          />
          <StatCard
            label="LongTermDistiller 蒸馏次数"
            value={memoryData?.distill_count ?? '—'}
            color="text-purple-600"
          />
        </div>
        {/* 子系统详情卡片 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {(memoryData?.subsystems ?? []).map((s) => (
            <div key={s.subsystem} className={`card p-3 border ${statusBg(s.success_rate)}`}>
              <div className="text-xs font-medium text-gray-700 mb-2 truncate" title={s.display_name}>
                {s.display_name}
              </div>
              <div className={`text-xl font-bold ${statusColor(s.success_rate)}`}>
                {s.success_rate != null ? `${s.success_rate}%` : '—'}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {s.total > 0 ? `${s.success}/${s.total} 成功` : '无数据'}
              </div>
              {/* 子系统专属指标 */}
              {s.extra?.reject_rate != null && (
                <div className="text-xs text-red-500 mt-0.5">拒绝率 {s.extra.reject_rate}%</div>
              )}
              {s.extra?.avg_compress_rate_pct != null && (
                <div className="text-xs text-blue-500 mt-0.5">压缩率 {s.extra.avg_compress_rate_pct}%</div>
              )}
              {s.extra?.distill_count != null && (
                <div className="text-xs text-purple-500 mt-0.5">蒸馏 {s.extra.distill_count} 次</div>
              )}
              {s.last_ts && (
                <div className="text-xs text-gray-300 mt-1 truncate" title={formatTime(s.last_ts)}>
                  {formatTime(s.last_ts)}
                </div>
              )}
            </div>
          ))}
          {(memoryData?.subsystems ?? []).length === 0 && (
            <div className="col-span-full text-center py-8 text-gray-400 text-sm">
              <Database size={24} className="mx-auto mb-2 text-gray-300" />
              暂无 Memory 子系统数据
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

// ===== TODO-47: 路由决策统计子组件 =====
function RouteStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchLlmRouteStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={GitBranch} title="LLM 路由决策统计" color="text-indigo-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总路由决策" value={data?.total_decisions ?? '—'} color="text-indigo-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color={data?.fallback_rate > 20 ? 'text-red-500' : 'text-orange-500'} />
        <StatCard label="降级率" value={data?.fallback_rate != null ? `${data.fallback_rate}%` : '—'} color={data?.fallback_rate > 20 ? 'text-red-500' : 'text-green-600'} />
        <StatCard label="模型种类" value={data?.models?.length ?? '—'} color="text-gray-700" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">模型选择分布</div>
          {(data?.models ?? []).length === 0 ? (
            <div className="text-center py-6 text-gray-400 text-xs">暂无路由数据</div>
          ) : (
            <div className="space-y-2">
              {(data?.models ?? []).map((m) => (
                <div key={m.model_target} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-24 truncate" title={m.model_target}>{m.model_target}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className="bg-indigo-400 h-2 rounded-full" style={{ width: `${data.total_decisions > 0 ? (m.select_count / data.total_decisions * 100).toFixed(1) : 0}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-10 text-right">{m.select_count}</div>
                  {m.fallback_count > 0 && <div className="text-xs text-orange-500">↓{m.fallback_count}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">任务类型分布</div>
          {(data?.task_types ?? []).length === 0 ? (
            <div className="text-center py-6 text-gray-400 text-xs">暂无数据</div>
          ) : (
            <div className="space-y-2">
              {(data?.task_types ?? []).map((t) => (
                <div key={t.task_type} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-20 truncate">{t.task_type}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className="bg-purple-400 h-2 rounded-full" style={{ width: `${t.pct}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-10 text-right">{t.pct}%</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

// ===== TODO-49: ContextPack 预算统计子组件 =====
function ContextPackSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchContextPackStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={Layers} title="ContextPack Token 预算统计" color="text-cyan-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总 Pack 数" value={data?.total_packs ?? '—'} color="text-cyan-600" />
        <StatCard label="平均 Token 数" value={data?.avg_total_tokens != null ? Math.round(data.avg_total_tokens) : '—'} color="text-blue-600" />
        <StatCard label="截断率" value={data?.truncation_rate != null ? `${data.truncation_rate}%` : '—'} color={data?.truncation_rate > 30 ? 'text-red-500' : 'text-green-600'} />
        <StatCard label="冲突率" value={data?.conflict_rate != null ? `${data.conflict_rate}%` : '—'} color={data?.conflict_rate > 10 ? 'text-orange-500' : 'text-green-600'} />
      </div>
      {(data?.slots ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">各槽位 Token 消耗</div>
          <div className="space-y-2">
            {(data?.slots ?? []).map((s) => (
              <div key={s.slot} className="flex items-center gap-2">
                <div className="text-xs text-gray-600 w-28 truncate font-mono" title={s.slot}>{s.slot}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-cyan-400 h-2 rounded-full" style={{ width: `${data.avg_total_tokens > 0 ? Math.min(100, s.avg_tokens / data.avg_total_tokens * 100).toFixed(1) : 0}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-12 text-right">{Math.round(s.avg_tokens)} tok</div>
                {s.truncation_rate > 0 && <div className="text-xs text-orange-400">截断{s.truncation_rate}%</div>}
                {s.conflict_suppressed_count > 0 && <div className="text-xs text-red-400">冲突{s.conflict_suppressed_count}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-53: FailureMatrix 失败事件统计子组件 =====
function FailureStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchFailureStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={TriangleAlert} title="FailureMatrix 失败事件统计" color="text-amber-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总失败事件" value={data?.total_failures ?? '—'} color="text-gray-700" />
        <StatCard label="中止次数" value={data?.abort_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
        <StatCard label="中止率" value={data?.abort_rate != null ? `${data.abort_rate}%` : '—'} color={data?.abort_rate > 30 ? 'text-red-500' : 'text-green-600'} />
      </div>
      {(data?.top_events ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">失败事件分布</div>
          <div className="space-y-2">
            {(data?.top_events ?? []).map((e) => (
              <div key={e.event} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-40 truncate">{e.event}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-amber-400 h-2 rounded-full" style={{ width: `${e.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{e.count}</div>
                {e.degraded_mode && <div className="text-xs text-blue-400 w-28 truncate">{e.degraded_mode}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-54: PermissionGuard 权限拒绝统计子组件 =====
function PermissionStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchPermissionStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={Lock} title="PermissionGuard 权限拒绝统计" color="text-violet-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="允许次数" value={data?.allowed_count ?? '—'} color="text-green-600" />
        <StatCard label="拒绝次数" value={data?.denied_count ?? '—'} color="text-red-600" />
        <StatCard label="SCHEDULER 拒绝" value={data?.scheduler_denied_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.top_denied_tools ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">Top 拒绝工具</div>
          <div className="space-y-2">
            {(data?.top_denied_tools ?? []).map((t) => (
              <div key={t.tool_name} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{t.tool_name}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: `${t.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{t.deny_count}</div>
                {t.proposer_class && <div className="text-xs text-gray-400 w-20 truncate">{t.proposer_class}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-59: MemoryGuard 宪法校验统计子组件 =====
function MemoryGuardStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchMemoryGuardStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={BookLock} title="MemoryGuard 宪法校验统计" color="text-emerald-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="通过次数" value={data?.passed_count ?? '—'} color="text-green-600" />
        <StatCard label="拦截次数" value={data?.blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="拦截率" value={data?.block_rate != null ? `${data.block_rate}%` : '—'} color={data?.block_rate > 20 ? 'text-red-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="NARRATIVE_ONLY" value={data?.narrative_only_count ?? '—'} color="text-orange-500" />
        <StatCard label="Dream 拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
        <StatCard label="蒸馏资格" value={data?.distill_eligible_count ?? '—'} color="text-blue-600" />
      </div>
      {(data?.top_reject_reasons ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝原因分布</div>
          <div className="space-y-2">
            {(data?.top_reject_reasons ?? []).map((r) => (
              <div key={r.reason} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-44 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-emerald-400 h-2 rounded-full" style={{ width: `${r.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{r.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-60: StateArbiter 事实仲裁统计子组件 =====
function StateArbiterStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchStateArbiterStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={GitMerge} title="StateArbiter 事实仲裁统计" color="text-cyan-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总仲裁次数" value={data?.total_resolutions ?? '—'} color="text-gray-700" />
        <StatCard label="冲突次数" value={data?.conflict_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color="text-orange-500" />
        <StatCard label="SESSION_OVERRIDE" value={data?.session_override_count ?? '—'} color="text-blue-600" />
      </div>
      {(data?.top_sources ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">状态源命中分布</div>
          <div className="space-y-2">
            {(data?.top_sources ?? []).map((s) => (
              <div key={s.source} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{s.source}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-cyan-400 h-2 rounded-full" style={{ width: `${s.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{s.hit_count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-61: ContextCompressor 压缩效率统计子组件 =====
function ContextCompressorStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchContextCompressorStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={Minimize2} title="ContextCompressor 压缩效率统计" color="text-teal-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总压缩次数" value={data?.total_compressions ?? '—'} color="text-gray-700" />
        <StatCard label="活跃压缩" value={data?.active_count ?? '—'} color="text-green-600" />
        <StatCard label="平均压缩率" value={data?.avg_compression_ratio != null ? `${(data.avg_compression_ratio * 100).toFixed(1)}%` : '—'} color="text-teal-600" />
        <StatCard label="平均节省轮次" value={data?.avg_token_saving != null ? data.avg_token_saving.toFixed(1) : '—'} color="text-blue-600" />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="总节省轮次" value={data?.total_tokens_saved ?? '—'} color="text-teal-700" />
        <StatCard label="摘要生成次数" value={data?.summary_generated_count ?? '—'} color="text-indigo-600" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.strategy_distribution ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">压缩策略分布</div>
          <div className="space-y-2">
            {(data?.strategy_distribution ?? []).map((s) => (
              <div key={s.strategy} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-24 truncate">{s.strategy}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-teal-400 h-2 rounded-full" style={{ width: `${s.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{s.pct}%</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-65: PromptBuilder 构建统计子组件 =====
function PromptBuilderStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchPromptBuilderStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={FileCode} title="PromptBuilder 构建统计" color="text-violet-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总构建次数" value={data?.total_builds ?? '—'} color="text-gray-700" />
        <StatCard label="注入检测" value={data?.injection_detected_count ?? '—'} color="text-red-600" />
        <StatCard label="注入拦截" value={data?.injection_blocked_count ?? '—'} color="text-orange-500" />
        <StatCard label="检测率" value={data?.injection_detection_rate != null ? `${data.injection_detection_rate}%` : '—'} color={data?.injection_detection_rate > 5 ? 'text-red-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="高风险消息" value={data?.high_risk_count ?? '—'} color="text-red-600" />
        <StatCard label="严重风险消息" value={data?.critical_risk_count ?? '—'} color="text-red-700" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.slot_fill_distribution ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">Slot 填充分布</div>
          <div className="space-y-2">
            {(data?.slot_fill_distribution ?? []).map((s) => (
              <div key={s.slot} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{s.slot}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: `${s.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{s.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ===== TODO-66: EpisodicStore 写入统计子组件 =====
function EpisodicStoreStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchEpisodicStoreStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={Archive} title="EpisodicStore 写入统计" color="text-amber-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总尝试次数" value={data?.total_attempts ?? '—'} color="text-gray-700" />
        <StatCard label="写入成功" value={data?.written_count ?? '—'} color="text-green-600" />
        <StatCard label="跳过次数" value={data?.skipped_count ?? '—'} color="text-gray-500" />
        <StatCard label="写入率" value={data?.write_rate != null ? `${data.write_rate}%` : '—'} color={data?.write_rate < 50 ? 'text-orange-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="MemoryGuard 拦截" value={data?.memory_guard_blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="Dream 拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
        <StatCard label="NARRATIVE_ONLY" value={data?.narrative_only_blocked_count ?? '—'} color="text-orange-500" />
        <StatCard label="蒸馏资格" value={data?.distill_eligible_count ?? '—'} color="text-blue-600" />
      </div>
    </section>
  )
}

// ===== TODO-50: UIAssertion 统计子组件 =====
function UIAssertionSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchUIAssertionStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={ShieldAlert} title="UI 断言安全统计" color="text-red-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总断言事件" value={data?.total_assertions ?? '—'} color="text-gray-700" />
        <StatCard label="敏感界面拦截" value={data?.sensitive_blocked ?? '—'} color="text-red-600" />
        <StatCard label="前置断言失败" value={data?.precondition_failed ?? '—'} color="text-orange-500" />
        <StatCard label="后置断言失败" value={data?.postcondition_failed ?? '—'} color="text-yellow-600" />
      </div>
      {(data?.top_failed_reasons ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">前置断言失败原因分布</div>
          <div className="space-y-1.5">
            {(data?.top_failed_reasons ?? []).map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                  <div className="bg-red-400 h-1.5 rounded-full" style={{ width: `${data.precondition_failed > 0 ? (r.count / data.precondition_failed * 100).toFixed(1) : 0}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{r.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
