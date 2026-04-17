import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { RefreshCw, ShieldCheck, AlertTriangle, Cpu, Bot, Database, Tv, FileWarning, Zap, GitBranch, Layers, ShieldAlert, TriangleAlert, Lock, BookLock, GitMerge, Minimize2, FileCode, Archive, ScrollText, Activity, Flame, ArrowLeft } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import InsightChartCard from '../components/insights/InsightChartCard.jsx'
import InsightSection from '../components/insights/InsightSection.jsx'
import MetricCard from '../components/MetricCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
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
  fetchCapabilityGapStats,
  fetchRecallPackStats,
  fetchUIAssertionStats,
  fetchFailureStats,
  fetchPermissionStats,
  fetchSchedulerHistory,
  fetchTribeEvents,
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
import { formatAlertTitle, formatEmotionTag, humanizeToken } from '../utils/digitalLife.js'

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
  { id: 'global', title: '全局与异常', icon: AlertTriangle, desc: '入口守护决策、全局异常追踪、热力图、请求链路统计、失败事件', color: 'text-red-600', bg: 'bg-gradient-to-br from-red-50 to-red-100 border border-red-100', iconBg: 'bg-red-100' },
  { id: 'memory', title: '器官与记忆', icon: Database, desc: '器官健康、记忆统计、情景写入、上下文压缩', color: 'text-teal-700', bg: 'bg-gradient-to-br from-teal-50 to-teal-100 border border-teal-100', iconBg: 'bg-teal-100' },
  { id: 'llm', title: '执行与推理', icon: Bot, desc: '执行循环、模型成本、路由决策、提示构建、召回包', color: 'text-indigo-700', bg: 'bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-100', iconBg: 'bg-indigo-100' },
  { id: 'safety', title: '安全与护栏', icon: ShieldCheck, desc: '质检违规、权限拒绝、规则校验、界面断言安全', color: 'text-emerald-700', bg: 'bg-gradient-to-br from-emerald-50 to-emerald-100 border border-emerald-100', iconBg: 'bg-emerald-100' },
  { id: 'state', title: '状态与规范', icon: GitMerge, desc: '关系查询统计、主设定提案', color: 'text-cyan-700', bg: 'bg-gradient-to-br from-cyan-50 to-cyan-100 border border-cyan-100', iconBg: 'bg-cyan-100' },
  { id: 'vts', title: '表情通道', icon: Tv, desc: '表情通道统计', color: 'text-pink-700', bg: 'bg-gradient-to-br from-pink-50 to-pink-100 border border-pink-100', iconBg: 'bg-pink-100' },
]

// ===== 子组件：统计卡片 =====
const StatCard = ({ label, value, sub, color = 'text-gray-800' }) => (
  <MetricCard label={label} value={value} hint={sub ?? ''} tone={color} variant="stacked" />
)

// ===== 主页面 =====
export default function Insights() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const navigate = useNavigate()
  const location = useLocation()
  const currentGroup = location.pathname.split('/').pop()
  const focusTarget = new URLSearchParams(location.search).get('focus')
  const isRoot = currentGroup === 'insights' || currentGroup === ''
  const currentGroupMeta = INSIGHT_GROUPS.find((group) => group.id === currentGroup) || null

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
  const { runtimeSnapshot, promptStats: promptBuilderData } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })
  const insightPulseNodes = [
    {
      label: '窗口',
      value: `${windowMinutes}m`,
      tone: 'primary',
      active: true,
    },
    {
      label: '全局异常',
      value: String(errData?.total_errors ?? 0),
      tone: 'rose',
      active: (errData?.total_errors ?? 0) > 0,
    },
    {
      label: '记忆器官',
      value: String(memoryData?.subsystems?.length ?? 0),
      tone: 'teal',
      active: Boolean(memoryData?.subsystems?.length),
    },
    {
      label: '执行循环',
      value: String(agentData?.total_loops ?? 0),
      tone: 'violet',
      active: (agentData?.total_loops ?? 0) > 0,
    },
    {
      label: '表情通道',
      value: String(vtsData?.total_events ?? 0),
      tone: 'pink',
      active: (vtsData?.total_events ?? 0) > 0,
    },
    {
      label: '模型呼吸',
      value: String(llmCallData?.total_calls ?? 0),
      tone: 'amber',
      active: (llmCallData?.total_calls ?? 0) > 0,
    },
  ]

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

  useEffect(() => {
    if (currentGroup !== 'global' || focusTarget !== 'forge-risk') {
      return undefined
    }
    const timer = setTimeout(() => {
      document.getElementById('forge-runtime-risk')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 120)
    return () => clearTimeout(timer)
  }, [currentGroup, focusTarget])

  return (
    <div className="space-y-8">
      <div className="card space-y-3 p-5 md:p-6">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">皮层洞察</h1>
            <p className="mt-0.5 text-sm text-gray-500">把神经链路、记忆链路、执行推理与生命体运行状态收敛到同一套洞察面。</p>
          </div>
          <div className="flex items-center gap-2">
            <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
            <button className="btn-secondary" onClick={load} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              {loading ? '刷新中' : '刷新'}
            </button>
          </div>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 md:grid-cols-4">
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">窗口：{windowMinutes}m</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">全局异常：{errData?.total_errors ?? 0}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">子系统：{subsysData?.subsystems?.length ?? 0}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">执行循环：{agentData?.total_loops ?? 0}</div>
        </div>
      </div>

      <PageHero
        icon={currentGroupMeta?.icon || Activity}
        eyebrow={isRoot ? '数字生命体综合洞察' : `${currentGroupMeta?.title || '器官分组'} 深度洞察`}
        title={isRoot ? '数字生命体器官级洞察面板' : currentGroupMeta?.title || '器官分组'}
        description={
          isRoot
            ? '从全局异常、记忆器官、安全守卫到 VTS 通道，先看总览，再按分组继续下钻。'
            : currentGroupMeta?.desc || '查看该分组下的详细指标、图表与最近事件。'
        }
        gradientClass="from-amber-50 via-white to-sky-50"
        metaItems={[
          `时间窗口：${windowMinutes} 分钟`,
          `全局错误：${errData?.total_errors ?? 0}`,
          `子系统：${subsysData?.subsystems?.length ?? 0}`,
          `Agent Loop：${agentData?.total_loops ?? 0}`,
          `VTS 事件：${vtsData?.total_events ?? 0}`,
        ]}
      />

      {isRoot ? (
        <LivingPulseStrip
          title="器官脉冲"
          subtitle="先看全局生命节律，再下钻到各个器官分区。"
          nodes={insightPulseNodes}
        />
      ) : null}

      {error && <NoticeBanner variant="error">加载失败：{error}</NoticeBanner>}
      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptBuilderData} compact={!isRoot} />
      {isRoot ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {INSIGHT_GROUPS.map(g => (
            <button
              type="button"
              key={g.id}
              className={`card-hover group w-full cursor-pointer p-0 text-left ${g.bg}`}
              onClick={() => navigate(`/insights/${g.id}`)}
            >
              <div className="space-y-4 p-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`rounded-lg p-2.5 shadow-sm ${g.iconBg} ${g.color}`}>
                      <g.icon size={24} strokeWidth={2} />
                    </div>
                    <h3 className="text-lg font-bold tracking-tight text-gray-800">{g.title}</h3>
                  </div>
                  <div className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${g.iconBg} ${g.color} bg-opacity-50`}>
                    <span>进入</span>
                    <ArrowLeft size={12} className="rotate-180 transition-transform group-hover:translate-x-0.5 group-hover:rotate-180" />
                  </div>
                </div>
                <p className="text-sm leading-relaxed text-gray-600">{g.desc}</p>
                <div className="lifeline-rail h-1.5">
                  <div className="lifeline-wave h-full w-full" />
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-8">
          <div className="card p-4">
            <button className="btn-secondary" onClick={() => navigate('/insights')}>
              <ArrowLeft size={16} /> 返回分组
            </button>
          </div>

          {currentGroup === 'global' && (
            <>
              {/* ===== P1: Governor 决策漏斗 ===== */}
      <InsightSection icon={ShieldCheck} title="入口守护决策漏斗" color="text-blue-600">
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
          <InsightChartCard
            title="拒绝原因分布"
            hasData={(govData?.reason_codes?.length ?? 0) > 0}
            emptyIcon={ShieldCheck}
            emptyText="暂无拒绝记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="拒绝原因分布"
              direction="horizontal"
              categories={(govData?.reason_codes ?? []).map((r) => formatAlertTitle(r.reason_code))}
              series={[{ name: '次数', data: (govData?.reason_codes ?? []).map((r) => r.count), color: '#F04438' }]}
            />
          </InsightChartCard>
        </div>
      </InsightSection>
              {/* ===== P1: 全局错误追踪 ===== */}
      <InsightSection icon={AlertTriangle} title="全局错误追踪" color="text-red-500">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="错误总数" value={errData?.total_errors} color={errData?.total_errors > 0 ? 'text-red-500' : 'text-green-600'} />
          {(errData?.stages ?? []).slice(0, 3).map((s) => (
            <StatCard key={s.stage} label={humanizeToken(s.stage)} value={s.count} sub={`${s.pct}%`} color="text-orange-500" />
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
          <InsightChartCard
            title="错误原因 Top10"
            hasData={(errData?.top_codes?.length ?? 0) > 0}
            emptyIcon={AlertTriangle}
            emptyText="暂无错误记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="错误原因 Top10"
              direction="horizontal"
              categories={(errData?.top_codes ?? []).map((c) => formatAlertTitle(c.reason_code))}
              series={[{ name: '次数', data: (errData?.top_codes ?? []).map((c) => c.count), color: '#F79009' }]}
            />
          </InsightChartCard>
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
                  {['时间', '环节', '原因码', '详情'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(errData?.recent ?? []).map((e, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(e.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs text-orange-600">{e.stage ? humanizeToken(e.stage) : '—'}</td>
                    <td className="px-4 py-2 text-xs text-red-600">{e.reason_code ? formatAlertTitle(e.reason_code) : '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs" title={e.detail}>{e.detail || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </InsightSection>
              <ErrorHeatmapSection windowMinutes={windowMinutes} />
              <CapabilityGapStatsSection windowMinutes={windowMinutes} highlightForgeRisk={focusTarget === 'forge-risk'} />
              <ChainStatsSection windowMinutes={windowMinutes} />
              <FailureStatsSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'memory' && (
            <>
              {/* ===== P2: 器官健康矩阵 ===== */}
      <InsightSection icon={Cpu} title="器官健康矩阵" color="text-purple-600">
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
            <EmptyStateCard title="暂无器官数据" hint="当前窗口未采集到器官健康样本。" className="col-span-full" />
          )}
        </div>
      </InsightSection>
              {/* ===== Ref #32: Memory 子系统统计 ===== */}
      <InsightSection icon={Database} title="记忆器官统计" color="text-teal-600">
        {/* 聚合指标卡片 */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard
            label="情景写入量"
            value={memoryData?.total_writes ?? '—'}
            color="text-teal-600"
          />
          <StatCard
            label="记忆守卫拒绝率"
            value={memoryData?.guard_reject_rate != null ? `${memoryData.guard_reject_rate}%` : '—'}
            color={memoryData?.guard_reject_rate > 30 ? 'text-red-500' : memoryData?.guard_reject_rate > 10 ? 'text-orange-500' : 'text-green-600'}
          />
          <StatCard
            label="上下文压缩率"
            value={memoryData?.compress_rate != null ? `${memoryData.compress_rate}%` : '—'}
            color="text-blue-600"
          />
          <StatCard
            label="长期蒸馏次数"
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
            <EmptyStateCard title="暂无记忆子系统数据" hint="等待记忆链路产出统计后展示。" icon={Database} className="col-span-full p-6" />
          )}
        </div>
      </InsightSection>
              <EpisodicStoreStatsSection windowMinutes={windowMinutes} />
              <ContextCompressorStatsSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'llm' && (
            <>
              {/* ===== P3: Agent Loop 统计 ===== */}
      <InsightSection icon={Bot} title="执行循环统计" color="text-indigo-600">
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
              最近自动化循环执行
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
          <EmptyStateCard title="暂无执行循环记录" hint="待有会话执行后展示统计明细。" icon={Bot} />
        )}
      </InsightSection>
              {/* ===== Ref #41: LLM 调用成本统计 ===== */}
      <InsightSection icon={Zap} title="推理成本统计" color="text-amber-600">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总调用次数" value={llmCallData?.total_calls ?? '—'} />
          <StatCard
            label="总词元消耗"
            value={llmCallData?.total_tokens != null ? llmCallData.total_tokens.toLocaleString() : '—'}
            color="text-amber-600"
          />
          <StatCard
            label="输入词元"
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
          <InsightChartCard
            title="各模型词元消耗"
            hasData={(llmCallData?.models?.length ?? 0) > 0}
            emptyIcon={Zap}
            emptyText="暂无模型调用数据"
          >
            <BarChart
              title="各模型词元消耗"
              direction="horizontal"
              categories={(llmCallData?.models ?? []).map((m) => m.model_id)}
              series={[
                { name: '输入', data: (llmCallData?.models ?? []).map((m) => m.total_prompt_tokens), color: '#2970FF' },
                { name: '输出', data: (llmCallData?.models ?? []).map((m) => m.total_completion_tokens), color: '#F79009' },
              ]}
            />
          </InsightChartCard>
          {/* 调用量时序趋势 */}
          <div className="card p-4">
            <LineChart
              title="模型调用量趋势"
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
                  {['模型 ID', '调用次数', '输入词元', '输出词元', '总词元', '平均延迟', 'P95 延迟'].map((h) => (
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
      </InsightSection>
              <RouteStatsSection windowMinutes={windowMinutes} />
              <PromptBuilderStatsSection windowMinutes={windowMinutes} />
              <RecallPackSection windowMinutes={windowMinutes} />
            </>
          )}

          {currentGroup === 'safety' && (
            <>
              {/* ===== Ref #40: QA 违规统计 ===== */}
      <InsightSection icon={FileWarning} title="QA 违规统计" color="text-red-600">
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
          <InsightChartCard
            title="违规码分布"
            hasData={(qaData?.violation_codes?.length ?? 0) > 0}
            emptyIcon={FileWarning}
            emptyText="暂无 QA 违规记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="违规码分布"
              direction="horizontal"
              categories={(qaData?.violation_codes ?? []).map((c) => c.violation_code)}
              series={[{ name: '次数', data: (qaData?.violation_codes ?? []).map((c) => c.count), color: '#F04438' }]}
            />
          </InsightChartCard>
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
      </InsightSection>
              <PermissionStatsSection windowMinutes={windowMinutes} />
              <SchedulerGateStatsSection windowMinutes={windowMinutes} />
              <MemoryGuardStatsSection windowMinutes={windowMinutes} />
              <UIAssertionSection windowMinutes={windowMinutes} />
              <TribeEventsSection windowMinutes={windowMinutes} />
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
      <InsightSection icon={Tv} title="表情通道统计" color="text-pink-600">
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
          <InsightChartCard
            title="Top 情感标签分布"
            hasData={(vtsData?.top_emotions?.length ?? 0) > 0}
            emptyIcon={Tv}
            emptyText="暂无表情事件数据"
          >
            <BarChart
              title="Top 情感标签分布"
              direction="horizontal"
              categories={(vtsData?.top_emotions ?? []).map((e) => formatEmotionTag(e.emotion_tag))}
              series={[{ name: '次数', data: (vtsData?.top_emotions ?? []).map((e) => e.count), color: '#EC4899' }]}
            />
          </InsightChartCard>
          {/* Talk Gate 统计 */}
          <div className="card p-4">
            <div className="text-xs font-medium text-gray-500 mb-3">对话门控统计</div>
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
              <NoticeBanner variant="info" className="mt-4">暂无对话门控数据</NoticeBanner>
            )}
          </div>
        </div>
      </InsightSection>
            </>
          )}
        </div>
      )}

    </div>
  )
}

// ===== Ref #47: 路由决策统计子组件 =====
function TribeEventsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  const [eventTypeFilter, setEventTypeFilter] = React.useState('')
  const [sourceFilter, setSourceFilter] = React.useState('')
  const [targetFilter, setTargetFilter] = React.useState('')
  const [loading, setLoading] = React.useState(false)

  const load = React.useCallback(() => {
    setLoading(true)
    return fetchTribeEvents({
      limit: 20,
      event_type: eventTypeFilter || undefined,
      source_agent: sourceFilter || undefined,
      target_agent: targetFilter || undefined,
    })
      .then(setData)
      .catch(() => setData({ total: 0, items: [] }))
      .finally(() => setLoading(false))
  }, [eventTypeFilter, sourceFilter, targetFilter])

  React.useEffect(() => {
    load()
  }, [windowMinutes, load])

  const items = data?.items ?? []
  const eventTypeCounter = new Map()
  for (const event of items) {
    const type = String(event?.event_type || 'unknown')
    eventTypeCounter.set(type, (eventTypeCounter.get(type) || 0) + 1)
  }
  const topEventTypes = Array.from(eventTypeCounter.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)

  return (
    <InsightSection icon={GitBranch} title="Tribe 事件总线观测" color="text-emerald-700">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="最近事件数" value={loading ? '...' : (data?.total ?? '—')} color="text-gray-700" />
        <StatCard label="窗口分钟" value={windowMinutes} color="text-gray-700" />
        <StatCard label="事件类型数" value={eventTypeCounter.size} color="text-blue-600" />
        <StatCard
          label="最近事件时间"
          value={items.length > 0 ? formatTime(items[0]?.ts_ms) : '—'}
          color="text-gray-600"
        />
      </div>

      <div className="card p-4 mb-4">
        <div className="text-xs font-semibold text-gray-600 mb-3">过滤条件</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="event_type"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
          />
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="source_agent"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
          />
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="target_agent"
            value={targetFilter}
            onChange={(e) => setTargetFilter(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={load} disabled={loading}>
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              className="btn-secondary text-xs"
              onClick={() => {
                setEventTypeFilter('')
                setSourceFilter('')
                setTargetFilter('')
              }}
              disabled={loading}
            >
              清空
            </button>
          </div>
        </div>
      </div>

      {topEventTypes.length > 0 && (
        <div className="card p-4 mb-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">事件类型 Top</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {topEventTypes.map(([eventType, count]) => (
              <div key={eventType} className="flex items-center justify-between text-xs border border-gray-200 rounded px-3 py-2">
                <span className="font-mono text-gray-700 truncate pr-2" title={eventType}>{eventType}</span>
                <span className="text-emerald-700 font-semibold">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {items.length > 0 ? (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
            最近 Tribe 事件
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['时间', '事件类型', 'source', 'target', 'payload'].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((event) => {
                const payload = event?.payload ?? {}
                const payloadPreview = JSON.stringify(payload)
                return (
                  <tr key={event.event_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">
                      {formatTime(event.ts_ms)}
                    </td>
                    <td className="px-4 py-2 text-xs font-mono text-emerald-700">
                      {event.event_type}
                    </td>
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">
                      {event.source_agent || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs font-mono text-purple-600">
                      {event.target_agent || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500 max-w-md truncate" title={payloadPreview}>
                      {payloadPreview}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyStateCard title="暂无 Tribe 事件数据" hint="当前窗口未采集到事件。" />
      )}
    </InsightSection>
  )
}

function RouteStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchLlmRouteStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={GitBranch} title="模型路由决策统计" color="text-indigo-600">
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
            <EmptyStateCard title="暂无路由数据" hint="窗口内无路由决策样本。" className="p-6" />
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
            <EmptyStateCard title="暂无任务类型数据" hint="窗口内尚无可统计的任务类型。" className="p-6" />
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
    </InsightSection>
  )
}

// ===== Ref #49: RecallPack budget stats =====
function RecallPackSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchRecallPackStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={Layers} title="召回包词元预算" color="text-cyan-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="召回包总数" value={data?.total_packs ?? '—'} color="text-cyan-600" />
        <StatCard label="平均词元数" value={data?.avg_total_tokens != null ? Math.round(data.avg_total_tokens) : '—'} color="text-blue-600" />
        <StatCard label="截断率" value={data?.truncation_rate != null ? `${data.truncation_rate}%` : '—'} color={data?.truncation_rate > 30 ? 'text-red-500' : 'text-green-600'} />
        <StatCard label="冲突率" value={data?.conflict_rate != null ? `${data.conflict_rate}%` : '—'} color={data?.conflict_rate > 10 ? 'text-orange-500' : 'text-green-600'} />
      </div>
      {(data?.slots ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">各槽位词元消耗</div>
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
    </InsightSection>
  )
}

// ===== Ref #53: FailureMatrix 失败事件统计子组件 =====
function FailureStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchFailureStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={TriangleAlert} title="失败事件统计" color="text-amber-600">
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
    </InsightSection>
  )
}

// ===== Ref #54: PermissionGuard 权限拒绝统计子组件 =====
function PermissionStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchPermissionStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={Lock} title="权限拒绝统计" color="text-violet-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="允许次数" value={data?.allowed_count ?? '—'} color="text-green-600" />
        <StatCard label="拒绝次数" value={data?.denied_count ?? '—'} color="text-red-600" />
        <StatCard label="节律拒绝" value={data?.scheduler_denied_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.top_denied_tools ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝工具 Top</div>
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
    </InsightSection>
  )
}

function SchedulerGateStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  const navigate = useNavigate()
  React.useEffect(() => {
    fetchSchedulerHistory({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  const goAutomation = React.useCallback(
    (params = {}) => {
      const search = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        const normalized = String(value || '').trim()
        if (normalized) search.set(key, normalized)
      })
      const query = search.toString()
      navigate(`/automation${query ? `?${query}` : ''}`)
    },
    [navigate],
  )

  return (
    <InsightSection icon={ShieldAlert} title="节律门控统计" color="text-amber-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="门控拦截" value={data?.gate_blocked_total ?? '—'} color="text-orange-500" />
        <StatCard
          label="门控拦截率"
          value={data?.gate_blocked_rate != null ? `${data.gate_blocked_rate}%` : '—'}
          color={(data?.gate_blocked_rate ?? 0) > 25 ? 'text-red-500' : 'text-green-600'}
        />
        <StatCard label="推进中任务" value={data?.running_count ?? '—'} color="text-blue-600" />
        <StatCard label="异常任务" value={data?.failed_count ?? '—'} color="text-red-600" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InsightChartCard
          title="门控原因 Top"
          hasData={(data?.blocked_reason_codes?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无门控拦截记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="门控原因 Top"
            direction="horizontal"
            categories={(data?.blocked_reason_codes ?? []).map((item) => item.reason_code)}
            series={[{ name: '次数', data: (data?.blocked_reason_codes ?? []).map((item) => item.count), color: '#F79009' }]}
          />
        </InsightChartCard>
        <InsightChartCard
          title="Gate 状态分布"
          hasData={(data?.gate_states?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无 Gate 状态数据"
          emptyIconClassName="text-gray-400"
        >
          <BarChart
            title="Gate 状态分布"
            direction="horizontal"
            categories={(data?.gate_states ?? []).map((item) => item.gate_state)}
            series={[{ name: '次数', data: (data?.gate_states ?? []).map((item) => item.count), color: '#2E90FA' }]}
          />
        </InsightChartCard>
      </div>
      <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50/60 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-amber-800">需要任务级阻断明细时，可直接跳转执行页继续下钻。</div>
          <button
            onClick={() => goAutomation()}
            className="rounded border border-amber-200 bg-white px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-100"
          >
            前往执行页
          </button>
        </div>
        {(data?.blocked_reason_codes?.length ?? 0) > 0 ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {(data?.blocked_reason_codes ?? []).slice(0, 3).map((item) => (
              <button
                key={item.reason_code}
                onClick={() => goAutomation({ reason_code: item.reason_code })}
                className="rounded bg-orange-100 px-2 py-1 text-[11px] font-mono text-orange-700 hover:bg-orange-200"
                title={`按原因筛选：${item.reason_code}`}
              >
                reason={item.reason_code}
              </button>
            ))}
          </div>
        ) : null}
        {(data?.gate_states?.length ?? 0) > 0 ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {(data?.gate_states ?? []).slice(0, 3).map((item) => (
              <button
                key={item.gate_state}
                onClick={() => goAutomation({ gate_state: item.gate_state })}
                className="rounded bg-blue-100 px-2 py-1 text-[11px] font-mono text-blue-700 hover:bg-blue-200"
                title={`按 Gate 筛选：${item.gate_state}`}
              >
                gate={item.gate_state}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </InsightSection>
  )
}

// ===== Ref #59: MemoryGuard 宪法校验统计子组件 =====
function MemoryGuardStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchMemoryGuardStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={BookLock} title="记忆守卫校验统计" color="text-emerald-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="通过次数" value={data?.passed_count ?? '—'} color="text-green-600" />
        <StatCard label="拦截次数" value={data?.blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="拦截率" value={data?.block_rate != null ? `${data.block_rate}%` : '—'} color={data?.block_rate > 20 ? 'text-red-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="仅叙事" value={data?.narrative_only_count ?? '—'} color="text-orange-500" />
        <StatCard label="梦境拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
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
    </InsightSection>
  )
}

// ===== Ref #60: StateArbiter Bond 查询统计子组件（Phase XIV v4: 仅 bond_graph）=====
function StateArbiterStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchStateArbiterStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={GitMerge} title="状态仲裁关系查询统计" color="text-cyan-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总查询次数" value={data?.total_resolutions ?? '—'} color="text-gray-700" />
        <StatCard label="冲突次数" value={data?.conflict_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color="text-orange-500" />
        <StatCard label="关系快照" value={data?.session_override_count ?? '—'} color="text-blue-600" />
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
    </InsightSection>
  )
}

// ===== Ref #61: ContextCompressor 压缩效率统计子组件 =====
function ContextCompressorStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchContextCompressorStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={Minimize2} title="上下文压缩效率统计" color="text-teal-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总压缩次数" value={data?.total_compressions ?? '—'} color="text-gray-700" />
        <StatCard label="活跃压缩" value={data?.active_count ?? '—'} color="text-green-600" />
        <StatCard label="平均压缩率" value={data?.avg_compression_ratio != null ? `${(data.avg_compression_ratio * 100).toFixed(1)}%` : '—'} color="text-teal-600" />
        <StatCard label="平均节省词元" value={data?.avg_token_saving != null ? data.avg_token_saving.toFixed(1) : '—'} color="text-blue-600" />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="总节省词元" value={data?.total_tokens_saved ?? '—'} color="text-teal-700" />
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
    </InsightSection>
  )
}

// ===== Ref #65: PromptBuilder 构建统计子组件 =====
function PromptBuilderStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchPromptBuilderStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={FileCode} title="提示构建统计" color="text-violet-600">
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
          <div className="text-xs font-semibold text-gray-600 mb-3">槽位填充分布</div>
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
    </InsightSection>
  )
}

// ===== Ref #66: EpisodicStore 写入统计子组件 =====
function EpisodicStoreStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchEpisodicStoreStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={Archive} title="情景记忆写入统计" color="text-amber-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总尝试次数" value={data?.total_attempts ?? '—'} color="text-gray-700" />
        <StatCard label="写入成功" value={data?.written_count ?? '—'} color="text-green-600" />
        <StatCard label="跳过次数" value={data?.skipped_count ?? '—'} color="text-gray-500" />
        <StatCard label="写入率" value={data?.write_rate != null ? `${data.write_rate}%` : '—'} color={data?.write_rate < 50 ? 'text-orange-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="记忆守卫拦截" value={data?.memory_guard_blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="梦境拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
        <StatCard label="仅叙事拦截" value={data?.narrative_only_blocked_count ?? '—'} color="text-orange-500" />
        <StatCard label="蒸馏资格" value={data?.distill_eligible_count ?? '—'} color="text-blue-600" />
      </div>
    </InsightSection>
  )
}

// ===== Ref #50: UIAssertion 统计子组件 =====
function UIAssertionSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchUIAssertionStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={ShieldAlert} title="UI 断言安全统计" color="text-red-600">
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
    </InsightSection>
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
    <InsightSection icon={Activity} title="请求链路统计" color="text-blue-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总请求数" value={data?.total_requests ?? '—'} color="text-gray-700" />
        <StatCard label="入口守护拦截率" value={data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—'} color={data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700'} />
        <StatCard label="记忆写入次数" value={data?.episodic_written_count ?? '—'} color="text-green-600" />
        <StatCard label="主设定提案次数" value={data?.canon_proposal_count ?? '—'} color="text-indigo-600" />
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
            <EmptyStateCard
              title="暂无节点调用数据"
              hint="窗口内暂无审计快照记录。"
              className="p-6"
            />
          )}
        </div>
      </div>
    </InsightSection>
  )
}

/*
function CapabilityGapStatsSection({ windowMinutes, highlightForgeRisk = false }) {
  const [data, setData] = React.useState(null)

  React.useEffect(() => {
    fetchCapabilityGapStats({
      limit_recent: 30,
      limit_hotspots: 8,
      limit_candidates: 8,
      min_candidate_detected: 3,
      min_candidate_sessions: 2,
      min_candidate_failure_rate: 0.35,
      min_candidate_failed: 2,
    }).then(setData).catch(() => {})
  }, [windowMinutes])

  const qualitySummary = data?.forge_runtime_quality?.summary || {}
  const qualityItems = data?.forge_runtime_quality?.items || []
  const worstItems = [...qualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 5)

  return (
    <InsightSection icon={GitBranch} title="能力缺口与锻造质量" color="text-indigo-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="缺口检测" value={data?.detected_total ?? '—'} color="text-gray-700" />
        <StatCard label="缺口失败" value={data?.failed_total ?? '—'} color="text-red-600" />
        <StatCard label="活跃临时能力" value={data?.active_runtime_capabilities ?? '—'} color="text-violet-600" />
        <StatCard label="Forge 候选" value={(data?.forge_candidates ?? []).length} color="text-blue-600" />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="跟踪提案" value={qualitySummary?.tracked_proposals ?? 0} color="text-gray-700" />
        <StatCard label="不健康提案" value={qualitySummary?.unhealthy_proposals ?? 0} color="text-orange-500" />
        <StatCard label="临界提案" value={qualitySummary?.critical_proposals ?? 0} color="text-red-600" />
        <StatCard
          label="平均健康分"
          value={qualitySummary?.avg_health_score != null ? `${Number(qualitySummary.avg_health_score).toFixed(1)}` : '—'}
          color={Number(qualitySummary?.avg_health_score ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">能力缺口热点</div>
          {(data?.hotspots ?? []).length > 0 ? (
            <div className="space-y-2">
              {(data?.hotspots ?? []).slice(0, 6).map((item) => (
                <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-orange-600">{item.reason_code}</span>
                    <span className="text-gray-500">失败率 {(Number(item.failure_rate || 0) * 100).toFixed(1)}%</span>
                  </div>
                  <div className="mt-1 text-gray-500">
                    family={item.suggested_family} 路 detected={item.detected_count} 路 failed={item.failed_count}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无能力缺口热点" hint="等待缺口事件累计后展示热点。" className="p-5" />
          )}
        </div>

        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">Forge 运行质量（低健康优先）</div>
          {worstItems.length > 0 ? (
            <div className="space-y-2">
              {worstItems.map((item) => {
                const health = Number(item.health_score || 0)
                const barClass = health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400'
                return (
                  <div key={item.proposal_id} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-indigo-600">{(item.proposal_id || '').slice(0, 18)}</span>
                      <span className="text-gray-500">{String(item.stage || '').toUpperCase()}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                        <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(3, Math.min(100, health))}%` }} />
                      </div>
                      <span className="text-gray-600">{health.toFixed(1)}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      fail={(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% 路 p95={item.window_p95_latency_ms ?? 0}ms 路 tune={item.last_tune_action || 'NONE'}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyStateCard title="暂无 Forge 运行质量样本" hint="尚未观测到提案运行结果。" className="p-5" />
          )}
        </div>
      </div>
    </InsightSection>
  )
}

*/

function CapabilityGapStatsSection({ windowMinutes, highlightForgeRisk = false }) {
  const [data, setData] = React.useState(null)

  React.useEffect(() => {
    fetchCapabilityGapStats({
      limit_recent: 30,
      limit_hotspots: 8,
      limit_candidates: 8,
      min_candidate_detected: 3,
      min_candidate_sessions: 2,
      min_candidate_failure_rate: 0.35,
      min_candidate_failed: 2,
    }).then(setData).catch(() => {})
  }, [windowMinutes])

  const qualitySummary = data?.forge_runtime_quality?.summary || {}
  const qualityItems = data?.forge_runtime_quality?.items || []
  const worstItems = [...qualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 5)

  return (
    <div id="forge-runtime-risk" className={highlightForgeRisk ? 'rounded-2xl ring-2 ring-red-200/80 ring-offset-2 ring-offset-white' : ''}>
      {highlightForgeRisk ? (
        <NoticeBanner variant="warning" title="Forge 风险焦点模式">
          当前视图由全局告警跳转而来，已定位到 Capability Gap / Forge Runtime 细分面板。
        </NoticeBanner>
      ) : null}
      <InsightSection icon={GitBranch} title="Capability Gap & Forge Quality" color="text-indigo-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="Gap Detected" value={data?.detected_total ?? '--'} color="text-gray-700" />
        <StatCard label="Gap Failed" value={data?.failed_total ?? '--'} color="text-red-600" />
        <StatCard label="Runtime Caps" value={data?.active_runtime_capabilities ?? '--'} color="text-violet-600" />
        <StatCard label="Forge Candidates" value={(data?.forge_candidates ?? []).length} color="text-blue-600" />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="Tracked Props" value={qualitySummary?.tracked_proposals ?? 0} color="text-gray-700" />
        <StatCard label="Unhealthy" value={qualitySummary?.unhealthy_proposals ?? 0} color="text-orange-500" />
        <StatCard label="Critical" value={qualitySummary?.critical_proposals ?? 0} color="text-red-600" />
        <StatCard
          label="Avg Health"
          value={qualitySummary?.avg_health_score != null ? `${Number(qualitySummary.avg_health_score).toFixed(1)}` : '--'}
          color={Number(qualitySummary?.avg_health_score ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">Capability Gap Hotspots</div>
          {(data?.hotspots ?? []).length > 0 ? (
            <div className="space-y-2">
              {(data?.hotspots ?? []).slice(0, 6).map((item) => (
                <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-orange-600">{item.reason_code}</span>
                    <span className="text-gray-500">fail {(Number(item.failure_rate || 0) * 100).toFixed(1)}%</span>
                  </div>
                  <div className="mt-1 text-gray-500">
                    family={item.suggested_family} | detected={item.detected_count} | failed={item.failed_count}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="No capability hotspots" hint="Waiting for gap events to accumulate." className="p-5" />
          )}
        </div>

        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">Forge Runtime Quality (Worst First)</div>
          {worstItems.length > 0 ? (
            <div className="space-y-2">
              {worstItems.map((item) => {
                const health = Number(item.health_score || 0)
                const barClass = health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400'
                return (
                  <div key={item.proposal_id} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-indigo-600">{(item.proposal_id || '').slice(0, 18)}</span>
                      <span className="text-gray-500">{String(item.stage || '').toUpperCase()}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                        <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(3, Math.min(100, health))}%` }} />
                      </div>
                      <span className="text-gray-600">{health.toFixed(1)}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      fail={(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95={item.window_p95_latency_ms ?? 0}ms | tune={item.last_tune_action || 'NONE'}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyStateCard title="No Forge quality samples" hint="No proposal runtime result observed yet." className="p-5" />
          )}
        </div>
      </div>
      </InsightSection>
    </div>
  )
}

function CanonProposalStatsSection({ windowMinutes }) {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchCanonProposalStats({ window_minutes: windowMinutes }).then(setData).catch(() => {})
  }, [windowMinutes])
  return (
    <InsightSection icon={ScrollText} title="主设定提案统计" color="text-indigo-600">
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
    </InsightSection>
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
    <InsightSection icon={Flame} title="错误热力图" color="text-red-600">
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
    </InsightSection>
  )
}
