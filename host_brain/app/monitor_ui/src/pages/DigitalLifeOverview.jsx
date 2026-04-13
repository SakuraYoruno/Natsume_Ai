import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Brain,
  Database,
  Heart,
  MoonStar,
  Orbit,
  RefreshCw,
  Settings,
  ShieldAlert,
  Sparkles,
  Waves,
} from 'lucide-react'
import {
  fetchCapabilityGapStats,
  fetchMemoryStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
  fetchVtsStats,
} from '../api/obs.js'
import BackgroundRuntimeCard from '../components/BackgroundRuntimeCard.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import VitalLoopSummaryPanel from '../components/VitalLoopSummaryPanel.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { getGrowthSignals } from '../utils/growthSignals.js'
import {
  compactText,
  formatAbsenceBand,
  formatDominantMotive,
  formatEmotionTag,
  formatReplyPosture,
  formatRiskLevel,
  formatSlotLabel,
  formatSlotStatus,
  formatStage,
  formatTrend,
  MODE_META,
} from '../utils/digitalLife.js'
import { buildDigitalLifeAlerts, getNonLiveSlots } from '../utils/runtimeHealth.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const DOMAIN_STYLES = {
  wired: 'border-green-200 bg-green-50 text-green-700',
  degraded: 'border-orange-200 bg-orange-50 text-orange-700',
  pending: 'border-gray-200 bg-gray-50 text-gray-500',
}

function DomainCard({ icon: Icon, title, desc, status, actionTo, actionLabel }) {
  const label = status === 'wired' ? '已接入' : status === 'degraded' ? '降级' : '待接入'

  return (
    <div className="card card-hover p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-white/80 bg-white/80 text-primary shadow-sm">
            <Icon size={18} />
          </span>
          <div>
            <div className="text-sm font-semibold text-gray-800">{title}</div>
            <div className="mt-1 text-xs text-gray-500">{desc}</div>
          </div>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${DOMAIN_STYLES[status] || DOMAIN_STYLES.pending}`}>
          {label}
        </span>
      </div>
      {actionTo ? (
        <Link to={actionTo} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-700">
          {actionLabel || '查看'}
        </Link>
      ) : null}
    </div>
  )
}

function GrowthTextCard({ title, text }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-sm backdrop-blur-sm">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {compactText(text, 200)}
      </div>
    </div>
  )
}

function LifeSection({ title, hint, actionTo, actionLabel, children }) {
  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="mb-3 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {actionTo ? (
            <Link to={actionTo} className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
              {actionLabel || '查看'}
            </Link>
          ) : null}
        </div>

        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}

        <div className="lifeline-rail mb-4 h-5 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-70" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        {children}
      </div>
    </section>
  )
}

export default function DigitalLifeOverview() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [vtsData, setVtsData] = useState(null)
  const [memoryData, setMemoryData] = useState(null)
  const [servicesData, setServicesData] = useState(null)
  const [subsystemsData, setSubsystemsData] = useState(null)
  const [capabilityGapData, setCapabilityGapData] = useState(null)

  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    error: runtimeError,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [vtsRes, memoryRes, servicesRes, subsystemsRes, capabilityGapRes] = await Promise.all([
        fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null),
        fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchCapabilityGapStats({
          limit_recent: 30,
          limit_hotspots: 8,
          limit_candidates: 8,
          min_candidate_detected: 3,
          min_candidate_sessions: 2,
          min_candidate_failure_rate: 0.35,
          min_candidate_failed: 2,
        }).catch(() => null),
      ])

      setVtsData(vtsRes)
      setMemoryData(memoryRes)
      setServicesData(servicesRes)
      setSubsystemsData(subsystemsRes)
      setCapabilityGapData(capabilityGapRes)
    } catch (loadError) {
      setError(loadError.message || '数字生命体总览加载失败')
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return undefined
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([load(), reloadRuntime()])
  }, [load, reloadRuntime])

  const modeKey = String(runtimeSnapshot?.sys_mode || 'normal').toLowerCase()
  const modeMeta = MODE_META[modeKey] || MODE_META.normal
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats })
  const nonLiveSlots = getNonLiveSlots(runtimeSnapshot)
  const bondState = runtimeSnapshot?.bond_state || {}
  const emotionContext = runtimeSnapshot?.emotion_context || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const responsePolicy = runtimeSnapshot?.response_policy || {}
  const silenceState = runtimeSnapshot?.silence_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const selfModelSnapshot = runtimeSnapshot?.self_model_snapshot || {}
  const topEmotion = emotionContext?.emotion_tag || vtsData?.top_emotions?.[0]?.emotion_tag
  const memorySubsystems = memoryData?.subsystems || []

  const avgMemorySuccessRate = memorySubsystems.length > 0
    ? Math.round(memorySubsystems.reduce((sum, item) => sum + Number(item?.success_rate || 0), 0) / memorySubsystems.length)
    : null

  const activeSubsystemCount = (subsystemsData?.subsystems || []).filter((item) => Number(item?.total || 0) > 0).length
  const currentBondStage = formatStage(bondState.current_stage)
  const currentEmotion = formatEmotionTag(topEmotion)
  const dominantMotive = formatDominantMotive(motiveState.dominant_motive)
  const replyPosture = formatReplyPosture(runtimeSnapshot?.runtime_summary?.reply_posture?.style)
  const riskLevel = formatRiskLevel(runtimeSnapshot?.self_reflection_pulse?.narrative_guardrails?.risk_level)
  const absenceBand = formatAbsenceBand(offlineState.absence_band)
  const growthSignals = getGrowthSignals(runtimeSnapshot)
  const forgeQualitySummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeQualityItems = capabilityGapData?.forge_runtime_quality?.items || []
  const forgeRiskItems = [...forgeQualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 3)
  const avgForgeHealth = Number(forgeQualitySummary?.avg_health_score ?? 0)
  const forgeCriticalCount = Number(forgeQualitySummary?.critical_proposals ?? 0)
  const forgeUnhealthyCount = Number(forgeQualitySummary?.unhealthy_proposals ?? 0)

  const pulseNodes = [
    { label: '存在态', value: modeMeta.label, tone: 'primary', active: true },
    { label: '关系', value: currentBondStage, tone: 'rose', active: Boolean(bondState.current_stage) },
    { label: '情绪', value: currentEmotion, tone: 'pink', active: Boolean(topEmotion) },
    { label: '回复', value: replyPosture, tone: 'violet', active: Boolean(replyPosture) },
    {
      label: '记忆',
      value: avgMemorySuccessRate != null ? `${avgMemorySuccessRate}% · ${memorySubsystems.length} 个子系统` : '--',
      tone: 'teal',
      active: avgMemorySuccessRate != null,
    },
    {
      label: '内在世界',
      value: offlineState.absence_band ? absenceBand : '在线',
      tone: 'amber',
      active: Boolean(offlineState.absence_band),
    },
    {
      label: '自我模型',
      value: growthSignals.hasAnyGrowth ? '已形成样本' : '待成形',
      tone: 'gray',
      active: growthSignals.hasAnyGrowth,
    },
  ]

  const pulseNodesView = [
    { label: 'Mode', value: modeMeta.label, tone: 'primary', active: true },
    { label: 'Bond', value: currentBondStage, tone: 'rose', active: Boolean(bondState.current_stage) },
    { label: 'Emotion', value: currentEmotion, tone: 'pink', active: Boolean(topEmotion) },
    { label: 'Reply', value: replyPosture, tone: 'violet', active: Boolean(replyPosture) },
    {
      label: 'Memory',
      value: avgMemorySuccessRate != null ? `${avgMemorySuccessRate}% / ${memorySubsystems.length} subsystems` : '--',
      tone: 'teal',
      active: avgMemorySuccessRate != null,
    },
    {
      label: 'Forge',
      value: (forgeQualitySummary?.tracked_proposals ?? 0) > 0
        ? `${avgForgeHealth.toFixed(1)} / critical ${forgeCriticalCount}`
        : 'waiting',
      tone: 'indigo',
      active: (forgeQualitySummary?.tracked_proposals ?? 0) > 0,
    },
    {
      label: 'Inner',
      value: offlineState.absence_band ? absenceBand : 'online',
      tone: 'amber',
      active: Boolean(offlineState.absence_band),
    },
    {
      label: 'Self',
      value: growthSignals.hasAnyGrowth ? 'available' : 'warming',
      tone: 'gray',
      active: growthSignals.hasAnyGrowth,
    },
  ]

  const wiredDomains = useMemo(
    () => [
      {
        icon: Orbit,
        title: '存在态轴',
        desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
        status: 'wired',
        actionTo: '/runtime-mode',
        actionLabel: '查看存在态轴',
      },
      {
        icon: Heart,
        title: '关系状态',
        desc: bondState.current_stage
          ? `${currentBondStage} · 趋势 ${formatTrend(bondState.trend)}`
          : '关系系统已在线，但当前窗口仍缺少阶段样本。',
        status: bondState.current_stage ? 'wired' : 'degraded',
        actionTo: '/bond',
        actionLabel: '查看关系',
      },
      {
        icon: Waves,
        title: '情绪与表情',
        desc: topEmotion
          ? `当前情绪 ${currentEmotion} · VTS 降级率 ${vtsData?.degrade_rate ?? 0}%`
          : '情绪上下文已接入，但当前窗口还没有可用的表情样本。',
        status: topEmotion || runtimeSnapshot?.slot_status?.emotion_context === 'live' ? 'wired' : 'degraded',
        actionTo: '/emotion',
        actionLabel: '查看情绪',
      },
      {
        icon: Database,
        title: '记忆系统',
        desc: avgMemorySuccessRate != null
          ? `平均成功率 ${avgMemorySuccessRate}% · ${memorySubsystems.length} 个子系统`
          : '记忆页已接入，但首页摘要暂未形成样本。',
        status: avgMemorySuccessRate != null ? 'wired' : 'degraded',
        actionTo: '/memory',
        actionLabel: '查看记忆',
      },
      {
        icon: Sparkles,
        title: '回复策略',
        desc: silenceState.willingness_level
          ? `意愿 ${silenceState.willingness_level} · 姿态 ${replyPosture}`
          : '策略系统已接入，但当前窗口缺少稳定快照。',
        status: silenceState.willingness_level ? 'wired' : 'degraded',
        actionTo: '/response-policy',
        actionLabel: '查看策略',
      },
      {
        icon: Brain,
        title: '自我模型',
        desc: growthSignals.hasAnyGrowth
          ? growthSignals.overviewSummary
          : selfModelSnapshot.recent_mood_trend
            ? `近期情绪趋势 ${formatTrend(selfModelSnapshot.recent_mood_trend)}`
            : '自我模型在线，等待更多成长样本聚合。',
        status: growthSignals.hasAnyGrowth || selfModelSnapshot.recent_mood_trend ? 'wired' : 'degraded',
        actionTo: '/self-model',
        actionLabel: '查看自我模型',
      },
      {
        icon: MoonStar,
        title: '内在世界',
        desc: offlineState.absence_band
          ? `${absenceBand} · 等待话题 ${(offlineState.anticipation_topics || []).length} 个`
          : '内在世界可访问，当前尚未形成完整离线态快照。',
        status: offlineState.absence_band ? 'wired' : 'degraded',
        actionTo: '/inner-world',
        actionLabel: '查看内在世界',
      },
      {
        icon: Settings,
        title: '神经底座',
        desc: servicesData
          ? `降级 ${servicesData.degraded ?? 0} · 离线 ${servicesData.down ?? 0} · 未知 ${servicesData.unknown ?? 0} · 活跃子系统 ${activeSubsystemCount}`
          : '神经底座状态暂时不可见。',
        status: servicesData ? 'wired' : 'degraded',
        actionTo: '/system',
        actionLabel: '查看神经底座',
      },
    ],
    [
      activeSubsystemCount,
      absenceBand,
      avgMemorySuccessRate,
      bondState.current_stage,
      bondState.trend,
      currentBondStage,
      currentEmotion,
      memorySubsystems.length,
      modeMeta,
      offlineState.absence_band,
      offlineState.anticipation_topics,
      replyPosture,
      growthSignals.hasAnyGrowth,
      growthSignals.overviewSummary,
      selfModelSnapshot.recent_mood_trend,
      servicesData,
      silenceState.willingness_level,
      topEmotion,
      vtsData?.degrade_rate,
      runtimeSnapshot?.slot_status?.emotion_context,
    ]
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
          <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={refreshAll} disabled={loading || runtimeLoading}>
            <RefreshCw size={14} className={loading || runtimeLoading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命体运行态"
        title={`${modeMeta.emoji} ${modeMeta.label}`}
        description={compactText(runtimeSnapshot?.runtime_summary?.headline || `${modeMeta.semantic}生命流正在穿过关系、情绪、记忆与回复层。`, 160)}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={[
          `关系：${currentBondStage}`,
          `回复：${replyPosture}`,
          `主导动机：${dominantMotive}`,
          `风险：${riskLevel}`,
        ]}
      />

      <LivingPulseStrip
        title="生命流"
        subtitle="把当前瞬时状态串成一条可读脉冲流，先看流动，再看器官细节。"
        nodes={pulseNodesView}
      />

      {error ? <NoticeBanner variant="error" title="总览统计加载失败">{error}</NoticeBanner> : null}
      {runtimeError ? (
        <NoticeBanner
          variant="info"
          title={/404|Not Found/i.test(runtimeError) ? '运行快照暂不可用' : '运行快照加载异常'}
        >
          {/404|Not Found/i.test(runtimeError)
            ? '当前环境没有接到运行快照，但页面其余部分仍可正常浏览。'
            : runtimeError}
        </NoticeBanner>
      ) : null}

      {runtimeSnapshot ? (
        <>
          <DigitalLifeRuntimeCard
            runtimeSnapshot={runtimeSnapshot}
            promptStats={promptStats}
            capabilityGapData={capabilityGapData}
          />
          <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} showDetailLink />
          <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
        </>
      ) : !runtimeLoading ? (
        <EmptyStateCard
          title="暂时没有数字生命体快照"
          hint="请先产生一次完整对话或等待采样完成。"
        />
      ) : null}

      <LifeSection
        title="Capability Gap & Forge Loop"
        hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
        actionTo="/insights/global"
        actionLabel="Open Global Insights"
      >
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="Gap Detected" value={capabilityGapData?.detected_total ?? '--'} hint={`failed ${capabilityGapData?.failed_total ?? 0}`} tone="text-indigo-600" />
          <MetricCard label="Forge Candidates" value={(capabilityGapData?.forge_candidates ?? []).length} hint={`runtime caps ${capabilityGapData?.active_runtime_capabilities ?? 0}`} tone="text-blue-600" />
          <MetricCard label="Avg Health" value={(forgeQualitySummary?.tracked_proposals ?? 0) > 0 ? avgForgeHealth.toFixed(1) : '--'} hint={`tracked ${forgeQualitySummary?.tracked_proposals ?? 0}`} tone={avgForgeHealth >= 70 ? 'text-green-600' : 'text-orange-500'} />
          <MetricCard label="Risk Proposals" value={forgeCriticalCount} hint={`unhealthy ${forgeUnhealthyCount}`} tone={forgeCriticalCount > 0 ? 'text-red-600' : forgeUnhealthyCount > 0 ? 'text-orange-500' : 'text-green-600'} />
        </div>

        {forgeCriticalCount > 0 ? (
          <div className="mt-4">
            <NoticeBanner variant="error" title="Forge has critical proposals">
              {`critical=${forgeCriticalCount}, unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
            </NoticeBanner>
          </div>
        ) : forgeUnhealthyCount > 0 ? (
          <div className="mt-4">
            <NoticeBanner variant="warning" title="Forge has unhealthy proposals">
              {`unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
            </NoticeBanner>
          </div>
        ) : null}

        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">Gap Hotspots</div>
            {(capabilityGapData?.hotspots ?? []).length > 0 ? (
              <div className="space-y-2">
                {(capabilityGapData?.hotspots ?? []).slice(0, 5).map((item) => (
                  <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-orange-600">{item.reason_code}</span>
                      <span className="text-gray-500">{`fail ${(Number(item.failure_rate || 0) * 100).toFixed(1)}%`}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {`family=${item.suggested_family} | detected=${item.detected_count} | failed=${item.failed_count}`}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyStateCard title="No gap hotspots" hint="Hotspots will appear after gap events are collected." className="p-4" />
            )}
          </div>

          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">Forge Risk Queue</div>
            {forgeRiskItems.length > 0 ? (
              <div className="space-y-2">
                {forgeRiskItems.map((item) => {
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
                        {`fail ${(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95 ${item.window_p95_latency_ms ?? 0}ms | tune ${item.last_tune_action || 'NONE'}`}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyStateCard title="No forge runtime samples" hint="Runtime quality will appear after proposal executions." className="p-4" />
            )}
          </div>
        </div>
      </LifeSection>

      <LifeSection
        title="体征核心指标"
        hint="把关键体征压成一屏可读指标，避免拆散到多个监控页。"
      >
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="当前情绪" value={currentEmotion} hint={`VTS 事件 ${vtsData?.total_events ?? 0}`} tone="text-pink-600" />
          <MetricCard label="关系阶段" value={currentBondStage} hint={`趋势 ${formatTrend(bondState.trend)}`} tone="text-rose-600" />
          <MetricCard label="主导动机" value={dominantMotive} hint={`回复姿态 ${replyPosture}`} tone="text-violet-600" />
          <MetricCard label="关键槽位" value={nonLiveSlots.length === 0 ? '全部在线' : `${nonLiveSlots.length} 个异常`} hint={`风险 ${riskLevel}`} tone="text-orange-500" />
        </div>

        {runtimeAlerts.length > 0 ? (
          <div className="mt-4 space-y-2">
            {runtimeAlerts.slice(0, 3).map((item) => (
              <NoticeBanner
                key={item.reason_code}
                variant={item.severity === 'critical' ? 'error' : item.severity === 'warning' ? 'warning' : 'info'}
                title={item.title}
              >
                {item.detail}
              </NoticeBanner>
            ))}
          </div>
        ) : null}
      </LifeSection>

      <LifeSection
        title="非监督成长"
        hint="成长面板不再是独立后台模块，而是生命体连续性的一部分。"
        actionTo="/self-model"
        actionLabel="查看详情"
      >
        {growthSignals.hasAnyGrowth ? (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <MetricCard
                label="成长周期"
                value={growthSignals.cycleCount || 0}
                hint={growthSignals.lastCycleLabel !== '--' ? `最近周期 ${growthSignals.lastCycleLabel}` : '尚未形成稳定周期'}
                tone="text-fuchsia-600"
              />
              <MetricCard
                label="活跃主题"
                value={growthSignals.activeThemes.length}
                hint={growthSignals.activeThemes.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 42) : '暂无主题聚合'}
                tone="text-indigo-600"
              />
              <MetricCard
                label="成长里程碑"
                value={growthSignals.milestones.length}
                hint={growthSignals.milestones.length > 0 ? compactText(growthSignals.milestones[0], 36) : '暂无里程碑'}
                tone="text-purple-600"
              />
              <MetricCard
                label="护栏风险"
                value={riskLevel}
                hint="成长展示不会绕过叙事护栏"
                tone="text-orange-500"
              />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="space-y-3">
                <KeyValueRow label="成长总览" value={growthSignals.primarySummary || '-'} maxValueWidthClassName="max-w-[72%]" />
                <KeyValueRow label="最近周期" value={growthSignals.lastCycleLabel} />
                {growthSignals.awarenessItems.map((item) => (
                  <KeyValueRow
                    key={item.key}
                    label={item.label}
                    value={item.value}
                    maxValueWidthClassName="max-w-[72%]"
                  />
                ))}
              </div>

              <div className="space-y-3">
                {growthSignals.insightSections.slice(0, 3).map((item) => (
                  <GrowthTextCard key={item.key} title={item.label} text={item.value} />
                ))}
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium text-gray-500">活跃成长主题</div>
              <div className="flex flex-wrap gap-2">
                {growthSignals.activeThemes.length > 0 ? (
                  growthSignals.activeThemes.map((item) => (
                    <span
                      key={item}
                      className="rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2.5 py-1 text-xs font-medium text-fuchsia-700"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-gray-400">当前还没有稳定的成长主题。</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyStateCard
            title="非监督成长尚未形成可观测快照"
            hint="等成长周期、主题或感知文本累积后，这里会出现摘要并直连自我模型详情。"
            className="p-4"
          />
        )}
      </LifeSection>

      <LifeSection
        title="器官系统联通态"
        hint="将已实现的类人器官系统聚合为统一联通视图。"
      >
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {wiredDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </LifeSection>

      <LifeSection
        title="体内快照与异常槽位"
        hint="左侧看当前体内快照，右侧暴露离线/降级槽位。"
      >
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldAlert size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">当前快照</h3>
            </div>
            <div className="space-y-3">
              <KeyValueRow label="当前模式" value={modeMeta.label} />
              <KeyValueRow label="离线分层" value={absenceBand} />
              <KeyValueRow label="回复上限" value={responsePolicy.max_chars != null ? `${responsePolicy.max_chars} 字` : '--'} />
              <KeyValueRow label="允许追问" value={responsePolicy.allow_followup == null ? '--' : responsePolicy.allow_followup ? '是' : '否'} />
              <KeyValueRow label="允许换题" value={responsePolicy.allow_topic_shift == null ? '--' : responsePolicy.allow_topic_shift ? '是' : '否'} />
              <KeyValueRow label="平均记忆成功率" value={avgMemorySuccessRate != null ? `${avgMemorySuccessRate}%` : '--'} />
            </div>
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Orbit size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">非在线槽位</h3>
            </div>
            {nonLiveSlots.length > 0 ? (
              <div className="space-y-3">
                {nonLiveSlots.map(([slot, status]) => (
                  <KeyValueRow
                    key={slot}
                    label={formatSlotLabel(slot)}
                    value={formatSlotStatus(status)}
                    valueClassName={status === 'degraded' ? 'text-orange-600' : 'text-red-600'}
                  />
                ))}
              </div>
            ) : (
              <EmptyStateCard
                title="全部关键槽位在线"
                hint="当前快照中没有检测到缺席或降级的核心槽位。"
                className="p-5"
              />
            )}
          </div>
        </div>
      </LifeSection>
    </div>
  )
}
