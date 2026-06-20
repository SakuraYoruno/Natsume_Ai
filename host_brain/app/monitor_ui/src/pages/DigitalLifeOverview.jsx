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
import BackgroundRuntimeCard from '../features/life/components/BackgroundRuntimeCard.jsx'
import AvatarStateCard from '../features/life/components/AvatarStateCard.jsx'
import { LifeSection } from '../features/life/components/digitalLifeSectionShared.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../shared/ui/observability/ViewportLazySection.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import DigitalLifeWallClockSoakCard from '../features/life/components/DigitalLifeWallClockSoakCard.jsx'
import MainChainReshapePanel from '../features/life/components/MainChainReshapePanel.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import LifeBodyPathologyPanel from '../features/life/components/lifeBody/LifeBodyPathologyPanel.jsx'
import LifeBodyFocusPanel from '../features/life/components/lifeBody/LifeBodyFocusPanel.jsx'
import LifeBodyTimeline from '../features/life/components/lifeBody/LifeBodyTimeline.jsx'
import LifeBodyTopology from '../features/life/components/lifeBody/LifeBodyTopology.jsx'
import LifeBodyVitalsStrip from '../features/life/components/lifeBody/LifeBodyVitalsStrip.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import VitalLoopSummaryPanel from '../features/life/components/VitalLoopSummaryPanel.jsx'
import useLifeBodyPageData from '../hooks/useLifeBodyPageData.js'
import { buildMainChainReshapeModel } from '../models/mainChainReshapeModel.js'
import {
  useAvatarStateQuery,
  useDigitalLifeWallClockSoakQuery,
} from '../features/life/digitalLifeQueries.js'
import { postAvatarBehavior } from '../features/life/api.js'
import {
  buildDigitalLifeCapabilitySectionModel,
  buildDigitalLifeGatewaySectionModel,
  buildDigitalLifeHeroDescription,
  buildDigitalLifeHeroMetaItems,
  buildDigitalLifeSecondarySectionsModel,
  buildDigitalLifeWiredDomains,
  getDigitalLifePollingInterval,
} from '../features/life/digitalLifeOverviewModel.js'

const DigitalLifeCapabilitySection = React.lazy(() => import('../features/life/components/DigitalLifeCapabilitySection.jsx'))
const DigitalLifeGatewaySection = React.lazy(() => import('../features/life/components/DigitalLifeGatewaySection.jsx'))
const DigitalLifeSecondarySections = React.lazy(() => import('../features/life/components/DigitalLifeSecondarySections.jsx'))

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function DigitalLifeOverview() {
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [avatarCommandBusy, setAvatarCommandBusy] = useState(false)
  const [avatarCommandError, setAvatarCommandError] = useState(null)
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
    topEmotion,
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
    renderPolicy,
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
    lifeBodyDerived,
    lifeBodyModel,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  } = useLifeBodyPageData({ windowMinutes })
  const wallClockSoakQuery = useDigitalLifeWallClockSoakQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval: getDigitalLifePollingInterval(pollingInterval, 60),
  })
  const avatarStateQuery = useAvatarStateQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval: getDigitalLifePollingInterval(pollingInterval, 15),
  })
  const mainChainReshapeModel = useMemo(
    () => buildMainChainReshapeModel({
      runtimeSnapshot,
      lifeBodyModel,
      lifeBodyDerived,
      wallClockSoakData: wallClockSoakQuery.data,
      hasRuntimeSnapshot,
      hasMemoryData,
      hasMemorySamples,
      growthSignals,
      humanoidLoopData,
      runtimeAlerts,
      nonLiveSlots,
      currentEmotion,
      currentBondStage,
      dominantMotive,
      replyPosture,
      riskLevel,
    }),
    [
      currentBondStage,
      currentEmotion,
      dominantMotive,
      growthSignals,
      hasMemoryData,
      hasMemorySamples,
      hasRuntimeSnapshot,
      humanoidLoopData,
      lifeBodyDerived,
      lifeBodyModel,
      nonLiveSlots,
      replyPosture,
      riskLevel,
      runtimeAlerts,
      runtimeSnapshot,
      wallClockSoakQuery.data,
    ]
  )
  const refreshDashboard = () => {
    void refreshAll()
    void wallClockSoakQuery.refetch()
    void avatarStateQuery.refetch()
  }
  const applyAvatarBehavior = async (behaviorId) => {
    setAvatarCommandBusy(true)
    setAvatarCommandError(null)
    try {
      await postAvatarBehavior({ behavior_id: behaviorId, source: 'monitor_digital_life' })
      await avatarStateQuery.refetch()
    } catch (err) {
      setAvatarCommandError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setAvatarCommandBusy(false)
    }
  }
  const wiredDomains = useMemo(
    () => buildDigitalLifeWiredDomains({
      icons: { Brain, Database, Heart, MoonStar, Orbit, Settings, Sparkles, Waves },
      activeSubsystemCount,
      absenceBand,
      avgMemorySuccessRate,
      bondState,
      configuredMemorySubsystemCount,
      currentBondStage,
      currentEmotion,
      growthSignals,
      hasMemoryData,
      hasMemorySamples,
      hasRuntimeSnapshot,
      modeMeta,
      offlineState,
      replyPosture,
      runtimeSnapshot,
      sampledMemorySubsystemCount,
      selfModelSnapshot,
      servicesData,
      silenceState,
      topEmotion,
      vtsData,
    }),
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
  const secondarySectionsModel = buildDigitalLifeSecondarySectionsModel({
    absenceBand,
    avgMemorySuccessRate,
    bondState,
    currentBondStage,
    currentEmotion,
    dominantMotive,
    growthSignals,
    hasMemoryData,
    hasMemorySamples,
    hasRuntimeSnapshot,
    lastOverviewUpdatedAt,
    memoryOverviewLoadState,
    modeMeta,
    nonLiveSlots,
    renderPolicy,
    replyPosture,
    riskLevel,
    runtimeAlerts,
    runtimeSnapshot,
    runtimeUpdatedAt,
    runtimeVitalsLoadState,
    sampledMemorySubsystemCount,
    vtsData,
    windowMinutes,
    wiredDomains,
  })
  const capabilitySectionModel = buildDigitalLifeCapabilitySectionModel(capabilityGapData)
  const gatewaySectionModel = buildDigitalLifeGatewaySectionModel({
    gatewayLoopLoadState,
    humanoidLoopAlertsData,
    humanoidLoopData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopRemediationData,
    humanoidLoopSourceCorrelationData,
    humanoidLoopTrendData,
    lastOverviewUpdatedAt,
    windowMinutes,
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
          <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button
            className="btn-secondary"
            onClick={refreshDashboard}
            disabled={loading || refreshing || runtimeLoading || wallClockSoakQuery.loading || wallClockSoakQuery.refreshing}
          >
            <RefreshCw
              size={14}
              className={
                loading || refreshing || runtimeLoading || wallClockSoakQuery.loading || wallClockSoakQuery.refreshing || avatarStateQuery.refreshing
                  ? 'animate-spin'
                  : ''
              }
            />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命驾驶舱"
        title={`${modeMeta.emoji} ${lifeBodyModel.lifeState.label}`}
        description={buildDigitalLifeHeroDescription({ runtimeSnapshot, lifeBodyModel, hasRuntimeSnapshot })}
        gradientClass="from-primary-50 via-white to-pink-50"
        showPulseRail={false}
        metaItems={buildDigitalLifeHeroMetaItems({ modeMeta, lifeBodyModel, replyPosture })}
        aside={(
          <SampleMeta
            className="max-w-md"
            compact
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

      <MainChainReshapePanel model={mainChainReshapeModel} />
      <LifeBodyVitalsStrip vitals={lifeBodyModel.vitalSigns} />
      <LifeBodyFocusPanel guidance={lifeBodyModel.diagnosticGuidance} />

      <LifeSection
        title="桌宠表现链路"
        hint="把 Host 对话、Edge 行为状态、MateEngine/VRM 驱动和危险热键拦截放在一张只读卡片里。"
      >
        <AvatarStateCard
          data={avatarStateQuery.avatarState}
          loading={avatarStateQuery.loading}
          refreshing={avatarStateQuery.refreshing}
          error={avatarStateQuery.error}
          commandError={avatarCommandError}
          behaviorBusy={avatarCommandBusy}
          updatedAt={avatarStateQuery.lastUpdatedAt}
          onRefresh={() => avatarStateQuery.refetch()}
          onBehavior={applyAvatarBehavior}
        />
      </LifeSection>

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

      <LifeSection
        title="真实墙钟闭环验证"
        hint="只读读取当前 24h wall-clock soak 记录，用来确认主链、器官映射与后台 tick 是否持续推进。"
      >
        <DigitalLifeWallClockSoakCard
          data={wallClockSoakQuery.data}
          loading={wallClockSoakQuery.loading}
          refreshing={wallClockSoakQuery.refreshing}
          error={wallClockSoakQuery.error}
          onRefresh={wallClockSoakQuery.refetch}
        />
      </LifeSection>

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
        eagerAfterMs={1200}
        fallback={(
          <LifeSection
            title="Capability Gap & Forge Loop"
            hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
            actionTo="/observability/insights/global"
            actionLabel="Open Global Insights"
          >
            <LazyLoadingNotice>
              正在准备 gap pressure 与 forge runtime 视图…
            </LazyLoadingNotice>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection
              title="Capability Gap & Forge Loop"
              hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
              actionTo="/observability/insights/global"
              actionLabel="Open Global Insights"
          >
              <LoadingNotice>
                正在载入 gap pressure 与 forge runtime 视图…
              </LoadingNotice>
            </LifeSection>
          )}
        >
          <DigitalLifeCapabilitySection model={capabilitySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="240px 0px"
        eagerAfterMs={1200}
        fallback={(
          <LifeSection
            title="人体闭环健康（Gateway）"
            hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
            actionTo="/execution/gateway"
            actionLabel="查看通道详情"
          >
            <LazyLoadingNotice>
              正在准备闭环趋势、病灶热区和自愈建议视图…
            </LazyLoadingNotice>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection
              title="人体闭环健康（Gateway）"
              hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
              actionTo="/execution/gateway"
              actionLabel="查看通道详情"
          >
              <LoadingNotice>
                正在载入闭环趋势、病灶热区和自愈建议视图…
              </LoadingNotice>
            </LifeSection>
          )}
        >
          <DigitalLifeGatewaySection model={gatewaySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="200px 0px"
        eagerAfterMs={1200}
        fallback={(
          <LifeSection title="生命体次级分区" hint="体征、成长、器官联通态与体内快照会在进入视口后加载。">
            <LazyLoadingNotice>
              正在准备次级生命体分区…
            </LazyLoadingNotice>
          </LifeSection>
        )}
      >
        <React.Suspense
          fallback={(
            <LifeSection title="生命体次级分区" hint="体征、成长、器官联通态与体内快照会在进入视口后加载。">
              <LoadingNotice>
                正在载入次级生命体分区…
              </LoadingNotice>
            </LifeSection>
          )}
        >
          <DigitalLifeSecondarySections model={secondarySectionsModel} />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
