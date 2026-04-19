import React, { useMemo, useState } from 'react'
import {
  Brain,
  Database,
  Heart,
  MoonStar,
  Orbit,
  RefreshCw,
  Settings,
  Sparkles,
  Waves,
} from 'lucide-react'
import BackgroundRuntimeCard from '../components/BackgroundRuntimeCard.jsx'
import { LifeSection } from '../components/digitalLife/digitalLifeSectionShared.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import ViewportLazySection from '../components/obs/ViewportLazySection.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LifeBodyPathologyPanel from '../components/lifeBody/LifeBodyPathologyPanel.jsx'
import LifeBodyFocusPanel from '../components/lifeBody/LifeBodyFocusPanel.jsx'
import LifeBodyTimeline from '../components/lifeBody/LifeBodyTimeline.jsx'
import LifeBodyTopology from '../components/lifeBody/LifeBodyTopology.jsx'
import LifeBodyVitalsStrip from '../components/lifeBody/LifeBodyVitalsStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import VitalLoopSummaryPanel from '../components/VitalLoopSummaryPanel.jsx'
import useLifeBodyPageData from '../hooks/useLifeBodyPageData.js'
import {
  compactText,
  formatTrend,
} from '../utils/digitalLife.js'

const DigitalLifeCapabilitySection = React.lazy(() => import('../components/digitalLife/DigitalLifeCapabilitySection.jsx'))
const DigitalLifeGatewaySection = React.lazy(() => import('../components/digitalLife/DigitalLifeGatewaySection.jsx'))
const DigitalLifeSecondarySections = React.lazy(() => import('../components/digitalLife/DigitalLifeSecondarySections.jsx'))

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function DigitalLifeOverview() {
  const [windowMinutes, setWindowMinutes] = useState(360)
  const {
    vtsData,
    memoryData,
    servicesData,
    subsystemsData,
    capabilityGapData,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
    loading,
    refreshing,
    error,
    refreshWarning,
    runtimeLoading,
    runtimeError,
    runtimeSnapshot,
    promptStats,
    modeMeta,
    hasRuntimeSnapshot,
    hasMemoryData,
    hasMemorySamples,
    avgMemorySuccessRate,
    sampledMemorySubsystemCount,
    configuredMemorySubsystemCount,
    activeSubsystemCount,
    currentBondStage,
    currentEmotion,
    dominantMotive,
    replyPosture,
    riskLevel,
    absenceBand,
    runtimeAlerts,
    nonLiveSlots,
    growthSignals,
    bondState,
    emotionContext,
    motiveState,
    responsePolicy,
    silenceState,
    offlineState,
    selfModelSnapshot,
    refreshAll,
    overviewTrackedSections,
    overviewUpdatedAt,
    runtimeUpdatedAt,
    lastOverviewUpdatedAt,
    overviewLoadState,
    runtimeVitalsLoadState,
    gatewayLoopLoadState,
    memoryOverviewLoadState,
    lifeBodyModel,
  } = useLifeBodyPageData({ windowMinutes })
  const forgeQualitySummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeQualityItems = capabilityGapData?.forge_runtime_quality?.items || []
  const forgeRiskItems = [...forgeQualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 3)
  const avgForgeHealth = Number(forgeQualitySummary?.avg_health_score ?? 0)
  const forgeCriticalCount = Number(forgeQualitySummary?.critical_proposals ?? 0)
  const forgeUnhealthyCount = Number(forgeQualitySummary?.unhealthy_proposals ?? 0)
  const hasHumanoidLoopData = Boolean(humanoidLoopData)
  const wiredDomains = useMemo(
    () => [
      {
        icon: Orbit,
        title: '存在态轴',
        desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
        status: hasRuntimeSnapshot ? 'wired' : 'pending',
        actionTo: '/runtime-mode',
        actionLabel: '查看存在态轴',
      },
      {
        icon: Heart,
        title: '关系状态',
        desc: !hasRuntimeSnapshot
          ? '等待运行快照恢复后补齐关系阶段与趋势摘要。'
          : bondState.current_stage
          ? `${currentBondStage} · 趋势 ${formatTrend(bondState.trend)}`
          : '关系系统已在线，但当前窗口仍缺少阶段样本。',
        status: !hasRuntimeSnapshot ? 'pending' : bondState.current_stage ? 'wired' : 'degraded',
        actionTo: '/bond',
        actionLabel: '查看关系',
      },
      {
        icon: Waves,
        title: '情绪与表情',
        desc: !hasRuntimeSnapshot && !topEmotion
          ? '等待运行快照恢复后补齐情绪与表情摘要。'
          : topEmotion
          ? `当前情绪 ${currentEmotion} · VTS 降级率 ${vtsData?.degrade_rate ?? 0}%`
          : '情绪上下文已接入，但当前窗口还没有可用的表情样本。',
        status: !hasRuntimeSnapshot && !topEmotion
          ? 'pending'
          : topEmotion || runtimeSnapshot?.slot_status?.emotion_context === 'live'
            ? 'wired'
            : 'degraded',
        actionTo: '/emotion',
        actionLabel: '查看情绪',
      },
      {
        icon: Database,
        title: '记忆系统',
        desc: !hasMemoryData
          ? '记忆统计暂时不可用，等待接口恢复后再刷新摘要。'
          : hasMemorySamples
          ? `平均成功率 ${avgMemorySuccessRate}% · ${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统有样本`
          : '记忆页已接入，但当前窗口还没有记忆流量样本。',
        status: hasMemoryData && hasMemorySamples ? 'wired' : 'pending',
        actionTo: '/memory',
        actionLabel: '查看记忆',
      },
      {
        icon: Sparkles,
        title: '回复策略',
        desc: !hasRuntimeSnapshot
          ? '等待运行快照恢复后补齐意愿与姿态摘要。'
          : silenceState.willingness_level
          ? `意愿 ${silenceState.willingness_level} · 姿态 ${replyPosture}`
          : '策略系统已接入，但当前窗口缺少稳定快照。',
        status: !hasRuntimeSnapshot ? 'pending' : silenceState.willingness_level ? 'wired' : 'degraded',
        actionTo: '/response-policy',
        actionLabel: '查看策略',
      },
      {
        icon: Brain,
        title: '自我模型',
        desc: !hasRuntimeSnapshot
          ? '等待运行快照恢复后补齐成长与自我观察摘要。'
          : growthSignals.hasAnyGrowth
          ? growthSignals.overviewSummary
          : selfModelSnapshot.recent_mood_trend
            ? `近期情绪趋势 ${formatTrend(selfModelSnapshot.recent_mood_trend)}`
            : '自我模型在线，等待更多成长样本聚合。',
        status: !hasRuntimeSnapshot
          ? 'pending'
          : growthSignals.hasAnyGrowth || selfModelSnapshot.recent_mood_trend
            ? 'wired'
            : 'degraded',
        actionTo: '/self-model',
        actionLabel: '查看自我模型',
      },
      {
        icon: MoonStar,
        title: '内在世界',
        desc: !hasRuntimeSnapshot
          ? '等待运行快照恢复后补齐离线态与期待话题摘要。'
          : offlineState.absence_band
          ? `${absenceBand} · 等待话题 ${(offlineState.anticipation_topics || []).length} 个`
          : '内在世界可访问，当前尚未形成完整离线态快照。',
        status: !hasRuntimeSnapshot ? 'pending' : offlineState.absence_band ? 'wired' : 'degraded',
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
      configuredMemorySubsystemCount,
      currentBondStage,
      currentEmotion,
      hasRuntimeSnapshot,
      modeMeta,
      offlineState.absence_band,
      offlineState.anticipation_topics,
      replyPosture,
      sampledMemorySubsystemCount,
      hasMemorySamples,
      hasMemoryData,
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
  const secondarySectionsModel = {
    windowMinutes,
    vtsData,
    bondState,
    currentEmotion,
    currentBondStage,
    dominantMotive,
    replyPosture,
    nonLiveSlots,
    riskLevel,
    runtimeAlerts,
    runtimeVitalsLoadState,
    runtimeSnapshot,
    runtimeUpdatedAt,
    growthSignals,
    compactText,
    wiredDomains,
    modeMeta,
    absenceBand,
    responsePolicy,
    hasMemoryData,
    hasMemorySamples,
    avgMemorySuccessRate,
    memoryOverviewLoadState,
    sampledMemorySubsystemCount,
    lastOverviewUpdatedAt,
    hasRuntimeSnapshot,
  }
  const capabilitySectionModel = {
    capabilityGapData,
    forgeQualitySummary,
    avgForgeHealth,
    forgeCriticalCount,
    forgeUnhealthyCount,
    forgeRiskItems,
  }
  const gatewaySectionModel = {
    windowMinutes,
    updatedAt: lastOverviewUpdatedAt,
    loadState: gatewayLoopLoadState,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
          <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={refreshAll} disabled={loading || refreshing || runtimeLoading}>
            <RefreshCw size={14} className={loading || refreshing || runtimeLoading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命驾驶舱"
        title={`${modeMeta.emoji} ${lifeBodyModel.lifeState.label}`}
        description={compactText(
          runtimeSnapshot?.runtime_summary?.headline
            || `${lifeBodyModel.lifeState.summary}${hasRuntimeSnapshot ? ' 生命流正在穿过关系、记忆、节律与执行层。' : ''}`,
          160
        )}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={[
          `存在态：${modeMeta.label}`,
          `最紧张器官：${lifeBodyModel.primaryOrgan?.name || '待判定'}`,
          `主病灶：${lifeBodyModel.primaryPathology?.title || '当前未形成'}`,
          `回复：${replyPosture}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={overviewLoadState}
            loadStateDetail={error || refreshWarning}
            windowMinutes={windowMinutes}
            sampleSize={overviewTrackedSections}
            sampleLabel="关键分区"
            updatedAt={overviewUpdatedAt}
            source="/obs/life-body aggregate"
            definition="驾驶舱现在优先走 obs sidecar 的生命体聚合接口，再在前端补主体化视图层；关键分区数量表示当前窗口中已成功返回数据的核心观测分区数。"
          />
        )}
      />

      <LifeBodyVitalsStrip vitals={lifeBodyModel.vitalSigns} />
      <LifeBodyFocusPanel guidance={lifeBodyModel.diagnosticGuidance} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.8fr)_minmax(320px,0.95fr)]">
        <LifeBodyTopology
          lifeState={lifeBodyModel.lifeState}
          organSystems={lifeBodyModel.organSystems}
          organStatusCounts={lifeBodyModel.organStatusCounts}
          primaryPathology={lifeBodyModel.primaryPathology}
          primaryOrganId={lifeBodyModel.primaryOrgan?.id}
        />
        <LifeBodyPathologyPanel pathologies={lifeBodyModel.pathologies} />
      </div>

      <LifeBodyTimeline events={lifeBodyModel.timelineEvents} />

      {error ? <NoticeBanner variant="error" title="总览统计加载失败">{error.message}</NoticeBanner> : null}
      {refreshWarning ? (
        <NoticeBanner variant="warning" title="总览刷新失败，当前展示旧快照">
          {refreshWarning.message}
        </NoticeBanner>
      ) : null}
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
        <LifeSection
          title="生命体细部解剖"
          hint="上面先看主体、器官和病灶；下面继续看运行细部、稳态与节律剖面。"
        >
          <div className="space-y-4">
          <DigitalLifeRuntimeCard
            runtimeSnapshot={runtimeSnapshot}
            promptStats={promptStats}
            capabilityGapData={capabilityGapData}
          />
          <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} showDetailLink />
          <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
          </div>
        </LifeSection>
      ) : !runtimeLoading ? (
        <EmptyStateCard
          title="暂时没有数字生命体快照"
          hint="请先产生一次完整对话或等待采样完成。"
        />
      ) : null}

      <ViewportLazySection
        rootMargin="180px 0px"
        placeholder={(
          <LifeSection
            title="Capability Gap & Forge Loop"
            hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
            actionTo="/insights/global"
            actionLabel="Open Global Insights"
          >
            <NoticeBanner variant="info" title="按需加载中">
              正在准备 gap pressure 与 forge runtime 视图…
            </NoticeBanner>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection
              title="Capability Gap & Forge Loop"
              hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
              actionTo="/insights/global"
              actionLabel="Open Global Insights"
            >
              <NoticeBanner variant="info" title="加载中">
                正在载入 gap pressure 与 forge runtime 视图…
              </NoticeBanner>
            </LifeSection>
          )}
        >
          <DigitalLifeCapabilitySection model={capabilitySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="240px 0px"
        placeholder={(
          <LifeSection
            title="人体闭环健康（Gateway）"
            hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
            actionTo="/gateway"
            actionLabel="查看通道详情"
          >
            <NoticeBanner variant="info" title="按需加载中">
              正在准备闭环趋势、病灶热区和自愈建议视图…
            </NoticeBanner>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection
              title="人体闭环健康（Gateway）"
              hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
              actionTo="/gateway"
              actionLabel="查看通道详情"
            >
              <NoticeBanner variant="info" title="加载中">
                正在载入闭环趋势、病灶热区和自愈建议视图…
              </NoticeBanner>
            </LifeSection>
          )}
        >
          <DigitalLifeGatewaySection model={gatewaySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="200px 0px"
        placeholder={(
          <LifeSection title="生命体次级分区" hint="体征、成长、器官联通态与体内快照会在进入视口后加载。">
            <NoticeBanner variant="info" title="按需加载中">
              正在准备次级生命体分区…
            </NoticeBanner>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection title="生命体次级分区" hint="体征、成长、器官联通态与体内快照会在进入视口后加载。">
              <NoticeBanner variant="info" title="加载中">
                正在载入次级生命体分区…
              </NoticeBanner>
            </LifeSection>
          )}
        >
          <DigitalLifeSecondarySections model={secondarySectionsModel} />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
