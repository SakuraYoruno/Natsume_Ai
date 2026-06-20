import React from 'react'
import {
  RefreshCw,
  Sparkles,
} from 'lucide-react'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../../../shared/ui/observability/ViewportLazySection.jsx'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../../../shared/ui/TimeWindowSelect.jsx'
import AvatarStateCard from './AvatarStateCard.jsx'
import BackgroundRuntimeCard from './BackgroundRuntimeCard.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'
import DigitalLifeRuntimeCard from './DigitalLifeRuntimeCard.jsx'
import DigitalLifeWallClockSoakCard from './DigitalLifeWallClockSoakCard.jsx'
import MainChainReshapePanel from './MainChainReshapePanel.jsx'
import VitalLoopSummaryPanel from './VitalLoopSummaryPanel.jsx'
import LifeBodyFocusPanel from './lifeBody/LifeBodyFocusPanel.jsx'
import LifeBodyPathologyPanel from './lifeBody/LifeBodyPathologyPanel.jsx'
import LifeBodyTimeline from './lifeBody/LifeBodyTimeline.jsx'
import LifeBodyTopology from './lifeBody/LifeBodyTopology.jsx'
import LifeBodyVitalsStrip from './lifeBody/LifeBodyVitalsStrip.jsx'

const DigitalLifeCapabilitySection = React.lazy(() => import('./DigitalLifeCapabilitySection.jsx'))
const DigitalLifeGatewaySection = React.lazy(() => import('./DigitalLifeGatewaySection.jsx'))
const DigitalLifeSecondarySections = React.lazy(() => import('./DigitalLifeSecondarySections.jsx'))

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

function CapabilitySectionFallback({ loading = false }) {
  return (
    <LifeSection
      title="Capability Gap & Forge Loop"
      hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
      actionTo="/observability/insights/global"
      actionLabel="Open Global Insights"
    >
      {loading ? (
        <LoadingNotice>
          正在载入 gap pressure 与 forge runtime 视图…
        </LoadingNotice>
      ) : (
        <LazyLoadingNotice>
          正在准备 gap pressure 与 forge runtime 视图…
        </LazyLoadingNotice>
      )}
    </LifeSection>
  )
}

function GatewaySectionFallback({ loading = false }) {
  return (
    <LifeSection
      title="人体闭环健康（Gateway）"
      hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
      actionTo="/execution/gateway"
      actionLabel="查看通道详情"
    >
      {loading ? (
        <LoadingNotice>
          正在载入闭环趋势、病灶热区和自愈建议视图…
        </LoadingNotice>
      ) : (
        <LazyLoadingNotice>
          正在准备闭环趋势、病灶热区和自愈建议视图…
        </LazyLoadingNotice>
      )}
    </LifeSection>
  )
}

function SecondarySectionsFallback({ loading = false }) {
  return (
    <LifeSection title="生命体次级分区" hint="体征、成长、器官联通态与体内快照会在进入视口后加载。">
      {loading ? (
        <LoadingNotice>
          正在载入次级生命体分区…
        </LoadingNotice>
      ) : (
        <LazyLoadingNotice>
          正在准备次级生命体分区…
        </LazyLoadingNotice>
      )}
    </LifeSection>
  )
}

function RuntimeErrorBanner({ runtimeError }) {
  if (!runtimeError) return null
  const runtimeErrorText = runtimeError instanceof Error ? runtimeError.message : String(runtimeError)
  const unavailable = /404|Not Found/i.test(runtimeErrorText)

  return (
    <NoticeBanner
      variant="info"
      title={unavailable ? '运行快照暂不可用' : '运行快照加载异常'}
    >
      {unavailable
        ? '当前环境没有接到运行快照，但页面其余部分仍可正常浏览。'
        : runtimeErrorText}
    </NoticeBanner>
  )
}

export default function DigitalLifeOverviewSurface({
  windowMinutes,
  onWindowMinutesChange,
  onRefreshDashboard,
  loading,
  refreshing,
  error,
  refreshWarning,
  runtimeLoading,
  runtimeError,
  runtimeSnapshot,
  promptStats,
  capabilityGapData,
  modeMeta,
  lifeBodyModel,
  hasRuntimeSnapshot,
  replyPosture,
  overviewLoadState,
  overviewTrackedSections,
  overviewUpdatedAt,
  heroDescription,
  heroMetaItems,
  mainChainReshapeModel,
  wallClockSoakQuery,
  avatarStateQuery,
  avatarCommandBusy,
  avatarCommandError,
  onApplyAvatarBehavior,
  capabilitySectionModel,
  gatewaySectionModel,
  secondarySectionsModel,
}) {
  const dashboardBusy = Boolean(
    loading
      || refreshing
      || runtimeLoading
      || wallClockSoakQuery.loading
      || wallClockSoakQuery.refreshing
  )
  const refreshAnimating = Boolean(dashboardBusy || avatarStateQuery.refreshing)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
          <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={WINDOW_OPTIONS} />
          <button
            className="btn-secondary"
            onClick={onRefreshDashboard}
            disabled={dashboardBusy}
          >
            <RefreshCw
              size={14}
              className={refreshAnimating ? 'animate-spin' : ''}
            />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命驾驶舱"
        title={`${modeMeta.emoji} ${lifeBodyModel.lifeState.label}`}
        description={heroDescription}
        gradientClass="from-primary-50 via-white to-pink-50"
        showPulseRail={false}
        metaItems={heroMetaItems}
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
          onBehavior={onApplyAvatarBehavior}
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
      <RuntimeErrorBanner runtimeError={runtimeError} />

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
        fallback={<CapabilitySectionFallback />}
      >
        <React.Suspense fallback={<CapabilitySectionFallback loading />}>
          <DigitalLifeCapabilitySection model={capabilitySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="240px 0px"
        eagerAfterMs={1200}
        fallback={<GatewaySectionFallback />}
      >
        <React.Suspense fallback={<GatewaySectionFallback loading />}>
          <DigitalLifeGatewaySection model={gatewaySectionModel} />
        </React.Suspense>
      </ViewportLazySection>

      <ViewportLazySection
        rootMargin="200px 0px"
        eagerAfterMs={1200}
        fallback={<SecondarySectionsFallback />}
      >
        <React.Suspense fallback={<SecondarySectionsFallback loading />}>
          <DigitalLifeSecondarySections model={secondarySectionsModel} />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
