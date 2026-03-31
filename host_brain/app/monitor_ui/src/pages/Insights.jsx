import React, { useEffect, useState, useCallback } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { RefreshCw, ShieldCheck, AlertTriangle, Cpu, Bot, Database, Tv, FileWarning, Zap, GitBranch, Layers, ShieldAlert, TriangleAlert, Lock, BookLock, GitMerge, Minimize2, FileCode, Archive, ScrollText, Activity, Flame, ArrowLeft } from 'lucide-react'
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
  fetchRecallPackStats,
  fetchUIAssertionStats,
  fetchFailureStats,
  fetchPermissionStats,
  fetchMemoryGuardStats,
  fetchStateArbiterStats,
  fetchContextCompressorStats,
  fetchPromptBuilderStats,
  fetchEpisodicStoreStats,
  fetchCanonProposalStats,
  fetchChainStats,
  fetchErrorHeatmap,
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

function normalizeCardStatus(rate, status) {
  const raw = String(status || '').toUpperCase()
  if (raw === 'UP' || raw === 'OK' || raw === 'HEALTHY') return 'up'
  if (raw === 'DEGRADED' || raw === 'WARN') return 'degraded'
  if (raw === 'DOWN' || raw === 'FAIL' || raw === 'FAILED' || raw === 'ERROR') return 'down'
  if (rate == null) return 'unknown'
  if (rate >= 90) return 'up'
  if (rate >= 70) return 'degraded'
  return 'down'
}

function statusColor(rate, status) {
  const normalized = normalizeCardStatus(rate, status)
  if (normalized === 'up') return 'text-green-600'
  if (normalized === 'degraded') return 'text-orange-500'
  if (normalized === 'down') return 'text-red-500'
  return 'text-gray-400'
}

function statusBg(rate, status) {
  const normalized = normalizeCardStatus(rate, status)
  if (normalized === 'up') return 'bg-green-50 border-green-200'
  if (normalized === 'degraded') return 'bg-orange-50 border-orange-200'
  if (normalized === 'down') return 'bg-red-50 border-red-200'
  return 'bg-gray-100'
}

function subsystemSummary(item) {
  if ((item?.total ?? 0) <= 0) return '近窗口无调用'
  if (item?.detail) return item.detail
  const parts = [`${item?.success ?? 0}/${item?.total ?? 0} 正常`]
  if ((item?.degraded ?? 0) > 0) parts.push(`${item.degraded} 降级`)
  if ((item?.failed ?? 0) > 0) parts.push(`${item.failed} 故障`)
  if ((item?.blocked ?? 0) > 0) parts.push(`拦截 ${item.blocked}`)
  return parts.join(' · ')
}

const WINDOW_OPTIONS = [
  { label: '1h',  value: 60    },
  { label: '6h',  value: 360   },
  { label: '24h', value: 1440  },
  { label: '7d',  value: 10080 },
]

const INSIGHT_GROUPS = [
  { id: 'global', title: '全局与错误', icon: AlertTriangle, desc: 'Governor决策、全局错误追踪、热力图、请求链路统计、失败事件', color: 'text-red-600', bg: 'bg-gradient-to-br from-red-50 to-red-100 border border-red-100', iconBg: 'bg-red-100' },
  { id: 'memory', title: '子系统与记忆', icon: Database, desc: '子系统健康、Memory统计、EpisodicStore、ContextCompressor', color: 'text-teal-700', bg: 'bg-gradient-to-br from-teal-50 to-teal-100 border border-teal-100', iconBg: 'bg-teal-100' },
  { id: 'llm', title: 'Agent与LLM', icon: Bot, desc: 'Agent Loop、LLM成本、路由决策、Prompt构建、RecallPack', color: 'text-indigo-700', bg: 'bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-100', iconBg: 'bg-indigo-100' },
  { id: 'safety', title: '安全与护栏', icon: ShieldCheck, desc: 'QA违规、权限拒绝、宪法校验、UI断言安全', color: 'text-emerald-700', bg: 'bg-gradient-to-br from-emerald-50 to-emerald-100 border border-emerald-100', iconBg: 'bg-emerald-100' },
  { id: 'state', title: '状态与规范', icon: GitMerge, desc: 'Bond 查询统计、Canon提案', color: 'text-cyan-700', bg: 'bg-gradient-to-br from-cyan-50 to-cyan-100 border border-cyan-100', iconBg: 'bg-cyan-100' },
  { id: 'vts', title: 'VTS通道', icon: Tv, desc: 'VTS通道统计', color: 'text-pink-700', bg: 'bg-gradient-to-br from-pink-50 to-pink-100 border border-pink-100', iconBg: 'bg-pink-100' },
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
  const navigate = useNavigate()
  const location = useLocation()
  const currentGroup = location.pathname.split('/').pop()
  const isRoot = currentGroup === 'insights' || currentGroup === ''

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
      {isRoot ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mt-6">
          {INSIGHT_GROUPS.map(g => (
            <div
              key={g.id}
              className={`rounded-xl p-6 cursor-pointer transition-all duration-300 hover:-translate-y-1 hover:shadow-lg flex flex-col gap-4 ${g.bg}`}
              onClick={() => navigate(`/insights/${g.id}`)}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`p-2.5 rounded-lg ${g.iconBg} ${g.color} shadow-sm`}>
                    <g.icon size={24} strokeWidth={2} />
                  </div>
                  <h3 className="text-lg font-bold text-gray-800 tracking-tight">{g.title}</h3>
                </div>
                <div className={`text-xs font-medium px-2 py-1 rounded-full ${g.iconBg} ${g.color} bg-opacity-50 flex items-center gap-1`}>
                  <span>进入</span>
                  <ArrowLeft size={12} className="rotate-180" />
                </div>
              </div>
              <p className="text-sm text-gray-600 leading-relaxed">{g.desc}</p>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-8 mt-6">
          <button className="btn-secondary" onClick={() => navigate('/insights')}>
            <ArrowLeft size={16} /> 返回分组
          </button>

          {currentGroup === 'global' && (
            <>
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
              <ErrorHeatmapSection windowMinutes={windowMinutes} />
              <ChainStatsSection windowMinutes={windowMinutes} />
              <FailureStatsSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'memory' && (
            <>
              {/* ===== P2: 子系统健康矩阵 ===== */}
      <section>
        <SectionTitle icon={Cpu} title="子系统健康矩阵" color="text-purple-600" />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {(subsysData?.subsystems ?? []).map((s) => (
            <div
              key={s.subsystem}
              className={`card p-3 border ${statusBg(s.success_rate, s.status)}`}
            >
              <div className="text-xs font-medium text-gray-700 mb-2 truncate" title={s.display_name}>
                {s.display_name}
              </div>
              <div className={`text-xl font-bold ${statusColor(s.success_rate, s.status)}`}>
                {s.success_rate != null ? `${s.success_rate}%` : '—'}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {subsystemSummary(s)}
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
              {/* ===== Ref #32: Memory 子系统统计 ===== */}
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
              <EpisodicStoreStatsSection windowMinutes={windowMinutes} />
              <ContextCompressorStatsSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'llm' && (
            <>
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
              {/* ===== Ref #41: LLM 调用成本统计 ===== */}
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
              <RouteStatsSection windowMinutes={windowMinutes} />
              <PromptBuilderStatsSection windowMinutes={windowMinutes} />
              <RecallPackSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'safety' && (
            <>
              {/* ===== Ref #40: QA 违规统计 ===== */}
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
              <PermissionStatsSection windowMinutes={windowMinutes} />
              <MemoryGuardStatsSection windowMinutes={windowMinutes} />
              <UIAssertionSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'state' && (
            <>
              <StateArbiterStatsSection windowMinutes={windowMinutes} />
              <CanonProposalStatsSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'vts' && (
            <>
              {/* ===== Ref #38: VTS 通道统计 ===== */}
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
            </>
          )}
        </div>
      )}

    </div>
  )
}

// ===== Ref #47: 路由决策统计子组件 =====
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

// ===== Ref #49: RecallPack budget stats =====
function RecallPackSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchRecallPackStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={Layers} title="RecallPack Token Budget" color="text-cyan-600" />
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

// ===== Ref #53: FailureMatrix 失败事件统计子组件 =====
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

// ===== Ref #54: PermissionGuard 权限拒绝统计子组件 =====
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

// ===== Ref #59: MemoryGuard 宪法校验统计子组件 =====
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

// ===== Ref #60: StateArbiter Bond 查询统计子组件（Phase XIV v4: 仅 bond_graph）=====
function StateArbiterStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchStateArbiterStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={GitMerge} title="StateArbiter Bond 查询统计" color="text-cyan-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总查询次数" value={data?.total_resolutions ?? '—'} color="text-gray-700" />
        <StatCard label="冲突次数" value={data?.conflict_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color="text-orange-500" />
        <StatCard label="Bond 快照" value={data?.session_override_count ?? '—'} color="text-blue-600" />
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

// ===== Ref #61: ContextCompressor 压缩效率统计子组件 =====
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

// ===== Ref #65: PromptBuilder 构建统计子组件 =====
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

// ===== Ref #66: EpisodicStore 写入统计子组件 =====
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

// ===== Ref #50: UIAssertion 统计子组件 =====
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

// ===== Ref #84: 请求链路统计子组件（Phase XIV v4）=====
function ChainStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchChainStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])

  const nodes = data?.node_stats ?? []
  const maxAvg = Math.max(...nodes.map(n => n.avg_latency_ms ?? 0), 1)

  const NODE_COLOR = {
    airlock_ingress: '#667085', airlock_egress: '#667085', airlock: '#667085',
    governor: '#7A5AF8', recall_fusion: '#0BA5EC',
    prompt_builder: '#475467', llm_stream: '#F79009', llm_gateway: '#F79009',
    episodic_store: '#EE46BC', bond_update: '#D97706', pii_filter: '#667085',
    sub_agent: '#7C3AED', audit_snapshot: '#667085',
    // 保留旧名称兼容（后端仍可能返回旧 chain/stats 数据）
    airlock_quarantine: '#667085', airlock_sanitizer: '#667085',
    graph_executor: '#2970FF', qa_contract: '#12B76A',
    canon_proposal: '#6366F1', audit: '#667085',
  }

  return (
    <section>
      <SectionTitle icon={Activity} title="请求链路统计" color="text-blue-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总请求数" value={data?.total_requests ?? '—'} color="text-gray-700" />
        <StatCard label="Governor 拦截率" value={data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—'} color={data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700'} />
        <StatCard label="记忆写入次数" value={data?.episodic_written_count ?? '—'} color="text-green-600" />
        <StatCard label="Canon 提案次数" value={data?.canon_proposal_count ?? '—'} color="text-indigo-600" />
      </div>
      {/* 节点耗时分布直方图 */}
      <div className="card p-4">
        <div className="text-xs font-semibold text-gray-600 mb-3">各节点平均耗时分布（近 {windowMinutes} 分钟）</div>
        <div className="space-y-2">
          {nodes.filter(n => n.total_calls > 0).map((n) => {
            const avgPct = maxAvg > 0 ? Math.max((n.avg_latency_ms ?? 0) / maxAvg * 100, 1) : 0
            const color = NODE_COLOR[n.node_name] ?? '#667085'
            return (
              <div key={n.node_name} className="flex items-center gap-2">
                <div className="w-36 text-xs text-gray-600 truncate shrink-0" title={n.node_name}>
                  {n.node_name}
                </div>
                <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
                  <div
                    className="h-2 rounded-full transition-all"
                    style={{ width: `${avgPct}%`, backgroundColor: color, opacity: 0.75 }}
                  />
                </div>
                <div className="w-24 text-xs text-right text-gray-500 shrink-0">
                  {n.avg_latency_ms != null ? `avg ${n.avg_latency_ms}ms` : '—'}
                  {n.p95_latency_ms != null && <span className="text-gray-400"> / p95 {n.p95_latency_ms}ms</span>}
                </div>
                {n.degraded_rate > 0 && (
                  <div className="text-xs text-orange-500 shrink-0 w-16 text-right">{n.degraded_rate}% 降级</div>
                )}
                {n.timeout_count > 0 && (
                  <div className="text-xs text-red-500 shrink-0 w-12 text-right">{n.timeout_count} 超时</div>
                )}
              </div>
            )
          })}
          {nodes.filter(n => n.total_calls > 0).length === 0 && (
            <div className="text-center py-4 text-xs text-gray-400">暂无节点调用数据（窗口内无 AUDIT_SNAPSHOT 记录）</div>
          )}
        </div>
      </div>
    </section>
  )
}

function CanonProposalStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchCanonProposalStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <section>
      <SectionTitle icon={ScrollText} title="Canon 提案统计" color="text-indigo-600" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总提案数" value={data?.total ?? '—'} color="text-gray-700" />
        <StatCard label="待审核" value={data?.pending ?? '—'} color={data?.pending > 0 ? 'text-orange-500' : 'text-gray-400'} />
        <StatCard label="已通过" value={data?.applied ?? '—'} color="text-green-600" />
        <StatCard label="已拒绝" value={data?.rejected ?? '—'} color="text-red-600" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        {/* 通过率进度条 */}
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">提案通过率</div>
          <div className="flex items-center gap-3">
            <div className="flex-1 bg-gray-100 rounded-full h-3">
              <div
                className="bg-indigo-400 h-3 rounded-full transition-all"
                style={{ width: `${Math.min(data?.approval_rate ?? 0, 100)}%` }}
              />
            </div>
            <span className={`text-sm font-bold ${(data?.approval_rate ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500'}`}>
              {data?.approval_rate != null ? `${data.approval_rate}%` : '—'}
            </span>
          </div>
          {data?.pending > 0 && (
            <div className="mt-2 text-xs text-orange-500 flex items-center gap-1">
              ⚠ 有 {data.pending} 条提案待审核
            </div>
          )}
        </div>
        {/* 状态分布 */}
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">状态分布</div>
          <div className="space-y-2">
            {(data?.status_distribution ?? []).map((s) => {
              const colorMap = { pending: 'bg-orange-400', applied: 'bg-green-400', rejected: 'bg-red-400' }
              return (
                <div key={s.status} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-16 capitalize">{s.status}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className={`${colorMap[s.status] ?? 'bg-gray-400'} h-2 rounded-full`} style={{ width: `${s.pct}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-12 text-right">{s.count} ({s.pct}%)</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {/* 最近提案列表 */}
      {(data?.recent_proposals ?? []).length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
            最近提案（近 {windowMinutes} 分钟）
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['提案 ID', '目标文档', '状态', '提案者', '时间'].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(data?.recent_proposals ?? []).map((p) => {
                const statusColor = { pending: 'text-orange-500', APPLIED: 'text-green-600', rejected: 'text-red-600' }
                return (
                  <tr key={p.proposal_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs font-mono text-indigo-600 truncate max-w-32" title={p.proposal_id}>
                      {p.proposal_id?.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-600 truncate max-w-28" title={p.target_doc_id}>
                      {p.target_doc_id}
                    </td>
                    <td className={`px-4 py-2 text-xs font-medium ${statusColor[p.status] ?? 'text-gray-500'}`}>
                      {p.status}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-28">
                      {p.proposer_node_id ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400 whitespace-nowrap">
                      {p.ts_ms ? new Date(p.ts_ms).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// ===== Ref #89: 错误热力图子组件 =====
function ErrorHeatmapSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    const windowHours = Math.max(1, Math.round(windowMinutes / 60))
    fetchErrorHeatmap({ window_hours: windowHours }).then(setData).catch(() => {})
  }, [windowMinutes])

  const cells = data?.cells ?? []
  const nodes = data?.nodes ?? []
  const hourlyTotals = data?.hourly_totals ?? []
  const maxCellCount = Math.max(...cells.map(c => c.error_count), 1)

  // 构建 hour → node → count 的查找表
  const cellMap = {}
  for (const c of cells) {
    if (!cellMap[c.hour]) cellMap[c.hour] = {}
    cellMap[c.hour][c.node_name] = c.error_count
  }

  const hours = Array.from({ length: 24 }, (_, i) => i)

  return (
    <section>
      <SectionTitle icon={Flame} title="错误热力图" color="text-red-600" />
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-4">
        <StatCard label="总错误数" value={data?.total_errors ?? '—'} color={data?.total_errors > 0 ? 'text-red-600' : 'text-green-600'} />
        <StatCard label="统计窗口" value={data?.window_hours != null ? `${data.window_hours}h` : '—'} color="text-gray-700" />
        <StatCard label="涉及节点数" value={nodes.length > 0 ? nodes.length : '—'} color="text-orange-500" />
      </div>
      {nodes.length === 0 ? (
        <div className="card p-6 text-center text-xs text-gray-400">
          ✓ 窗口内无错误记录
        </div>
      ) : (
        <div className="card p-4 overflow-x-auto">
          <div className="text-xs font-semibold text-gray-600 mb-3">按小时 × 节点错误分布（颜色深度 = 错误数量）</div>
          {/* 热力图表格 */}
          <table className="text-xs border-collapse">
            <thead>
              <tr>
                <th className="w-36 text-left text-gray-400 font-normal pb-1 pr-2">节点 \ 小时</th>
                {hours.map(h => (
                  <th key={h} className="w-6 text-center text-gray-400 font-normal pb-1" style={{ minWidth: '1.5rem' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {nodes.slice(0, 15).map(node => (
                <tr key={node}>
                  <td className="text-gray-600 pr-2 py-0.5 truncate max-w-36" title={node}>{node}</td>
                  {hours.map(h => {
                    const cnt = cellMap[h]?.[node] ?? 0
                    const intensity = cnt > 0 ? Math.max(cnt / maxCellCount, 0.15) : 0
                    return (
                      <td key={h} className="py-0.5 px-0.5" title={cnt > 0 ? `${node} @ ${h}:00 — ${cnt} 次错误` : ''}>
                        <div
                          className="w-5 h-5 rounded-sm"
                          style={{
                            backgroundColor: cnt > 0
                              ? `rgba(239, 68, 68, ${intensity})`
                              : 'rgba(243, 244, 246, 1)',
                          }}
                        />
                      </td>
                    )
                  })}
                </tr>
              ))}
              {/* 每小时总计行 */}
              <tr className="border-t border-gray-200">
                <td className="text-gray-400 pr-2 py-1 font-medium">每小时合计</td>
                {hours.map(h => {
                  const total = hourlyTotals.find(t => t.hour === h)?.total ?? 0
                  return (
                    <td key={h} className="py-1 px-0.5 text-center">
                      <span className={`text-xs font-mono ${total > 0 ? 'text-red-500' : 'text-gray-300'}`}>
                        {total > 0 ? total : '·'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            </tbody>
          </table>
          {/* 图例 */}
          <div className="flex items-center gap-3 mt-3 text-xs text-gray-400">
            <span>少</span>
            {[0.15, 0.35, 0.55, 0.75, 1.0].map(v => (
              <div key={v} className="w-4 h-4 rounded-sm" style={{ backgroundColor: `rgba(239, 68, 68, ${v})` }} />
            ))}
            <span>多</span>
          </div>
        </div>
      )}
    </section>
  )
}
