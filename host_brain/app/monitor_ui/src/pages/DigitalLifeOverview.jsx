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
  fetchMemoryStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
  fetchVtsStats,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import BackgroundRuntimeCard from '../components/BackgroundRuntimeCard.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { getGrowthSignals } from '../utils/growthSignals.js'
import {
  compactText,
  formatAbsenceBand,
  formatDominantMotive,
  formatEmotionTag,
  formatNumber,
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
  const label = status === 'wired' ? '已接入' : status === 'degraded' ? '降级' : '待补齐'

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100 text-gray-600">
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
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {compactText(text, 200)}
      </div>
    </div>
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
      const [vtsRes, memoryRes, servicesRes, subsystemsRes] = await Promise.all([
        fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null),
        fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
      ])

      setVtsData(vtsRes)
      setMemoryData(memoryRes)
      setServicesData(servicesRes)
      setSubsystemsData(subsystemsRes)
    } catch (loadError) {
      setError(loadError.message || '数字生命总览加载失败')
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

  const wiredDomains = useMemo(
    () => [
      {
        icon: Orbit,
        title: '存在状态轴',
        desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
        status: 'wired',
        actionTo: '/runtime-mode',
        actionLabel: '查看状态轴',
      },
      {
        icon: Heart,
        title: '关系状态',
        desc: bondState.current_stage
          ? `${currentBondStage} · 趋势 ${formatTrend(bondState.trend)}`
          : '关系系统已在线，但当前快照仍缺少阶段样本。',
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
          : '记忆页已接入，但首页摘要还缺少当前窗口样本。',
        status: avgMemorySuccessRate != null ? 'wired' : 'degraded',
        actionTo: '/memory',
        actionLabel: '查看记忆',
      },
      {
        icon: Sparkles,
        title: '回复策略',
        desc: silenceState.willingness_level
          ? `意愿 ${silenceState.willingness_level} · 姿态 ${replyPosture}`
          : '回复策略已接入，但当前窗口没有稳定的策略快照。',
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
          : '自我模型已在线，但本页还没有拿到足够的成长样本。',
        status: growthSignals.hasAnyGrowth || selfModelSnapshot.recent_mood_trend ? 'wired' : 'degraded',
        actionTo: '/self-model',
        actionLabel: '查看自我模型',
      },
      {
        icon: MoonStar,
        title: '内在世界',
        desc: offlineState.absence_band
          ? `${absenceBand} · 等待话题 ${(offlineState.anticipation_topics || []).length} 个`
          : '内在世界可访问，但当前快照未形成完整的离线状态。',
        status: offlineState.absence_band ? 'wired' : 'degraded',
        actionTo: '/inner-world',
        actionLabel: '查看内在世界',
      },
      {
        icon: Settings,
        title: '系统底座',
        desc: servicesData
          ? `降级 ${servicesData.degraded ?? 0} · 离线 ${servicesData.down ?? 0} · 未知 ${servicesData.unknown ?? 0} · 活跃子系统 ${activeSubsystemCount}`
          : '系统底座状态暂时不可见。',
        status: servicesData ? 'wired' : 'degraded',
        actionTo: '/system',
        actionLabel: '查看底座',
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
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命总览</h1>
          <p className="text-sm text-gray-500">围绕当前运行态、关系、情绪、记忆与策略的统一快照。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={refreshAll} disabled={loading || runtimeLoading}>
            <RefreshCw size={14} className={loading || runtimeLoading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命运行态"
        title={`${modeMeta.emoji} ${modeMeta.label}`}
        description={compactText(runtimeSnapshot?.runtime_summary?.headline || modeMeta.semantic, 160)}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={[
          `关系：${currentBondStage}`,
          `回复：${replyPosture}`,
          `主导动机：${dominantMotive}`,
          `风险：${riskLevel}`,
        ]}
      />

      {error ? <NoticeBanner variant="error" title="总览统计加载失败">{error}</NoticeBanner> : null}
      {runtimeError ? <NoticeBanner variant="warning" title="运行快照加载异常">{runtimeError}</NoticeBanner> : null}

      {runtimeSnapshot ? (
        <>
          <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} />
          <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
        </>
      ) : !runtimeLoading ? (
        <EmptyStateCard
          title="暂时没有数字生命快照"
          hint="请先产生一次完整对话或等待运行态采样完成。"
        />
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前情绪" value={currentEmotion} hint={`VTS 事件 ${vtsData?.total_events ?? 0}`} tone="text-pink-600" />
        <MetricCard label="关系阶段" value={currentBondStage} hint={`趋势 ${formatTrend(bondState.trend)}`} tone="text-rose-600" />
        <MetricCard label="主导动机" value={dominantMotive} hint={`回复姿态 ${replyPosture}`} tone="text-violet-600" />
        <MetricCard label="关键槽位" value={nonLiveSlots.length === 0 ? '全部在线' : `${nonLiveSlots.length} 个异常`} hint={`风险 ${riskLevel}`} tone="text-orange-500" />
      </div>

      {runtimeAlerts.length > 0 ? (
        <div className="space-y-2">
          {runtimeAlerts.slice(0, 3).map((item) => (
            <NoticeBanner key={item.reason_code} variant={item.severity === 'critical' ? 'error' : item.severity === 'warning' ? 'warning' : 'info'} title={item.title}>
              {item.detail}
            </NoticeBanner>
          ))}
        </div>
      ) : null}

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Sparkles size={16} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">非监督成长</h2>
          <Link to="/self-model" className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
            查看详情
          </Link>
        </div>
        {growthSignals.hasAnyGrowth ? (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <MetricCard label="成长周期" value={growthSignals.cycleCount || 0} hint={growthSignals.lastCycleLabel !== '—' ? `最近周期 ${growthSignals.lastCycleLabel}` : '尚未形成稳定周期'} tone="text-fuchsia-600" />
              <MetricCard label="活跃主题" value={growthSignals.activeThemes.length} hint={growthSignals.activeThemes.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 42) : '暂无主题聚合'} tone="text-indigo-600" />
              <MetricCard label="成长里程碑" value={growthSignals.milestones.length} hint={growthSignals.milestones.length > 0 ? compactText(growthSignals.milestones[0], 36) : '暂无里程碑'} tone="text-purple-600" />
              <MetricCard label="护栏风险" value={riskLevel} hint="成长展示不会绕过叙事护栏" tone="text-orange-500" />
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
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {wiredDomains.map((item) => (
          <DomainCard key={item.title} {...item} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ShieldAlert size={16} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">当前快照</h2>
          </div>
          <div className="space-y-3">
            <KeyValueRow label="当前模式" value={modeMeta.label} />
            <KeyValueRow label="离线分层" value={absenceBand} />
            <KeyValueRow label="回复上限" value={responsePolicy.max_chars != null ? `${responsePolicy.max_chars} 字` : '—'} />
            <KeyValueRow label="允许追问" value={responsePolicy.allow_followup == null ? '—' : responsePolicy.allow_followup ? '是' : '否'} />
            <KeyValueRow label="允许换题" value={responsePolicy.allow_topic_shift == null ? '—' : responsePolicy.allow_topic_shift ? '是' : '否'} />
            <KeyValueRow label="平均记忆成功率" value={avgMemorySuccessRate != null ? `${avgMemorySuccessRate}%` : '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Orbit size={16} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">非在线槽位</h2>
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
    </div>
  )
}
