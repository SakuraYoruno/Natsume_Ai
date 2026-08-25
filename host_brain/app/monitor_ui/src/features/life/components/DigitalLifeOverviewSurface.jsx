import { useMemo, useState } from 'react'
import DigitalLifeAvatarSection from './DigitalLifeAvatarSection.jsx'
import DigitalLifeCommandRail from './DigitalLifeCommandRail.jsx'
import DigitalLifeFocusLens from './DigitalLifeFocusLens.jsx'
import DigitalLifeHeroSection from './DigitalLifeHeroSection.jsx'
import DigitalLifeLazySections from './DigitalLifeLazySections.jsx'
import DigitalLifeNav from './DigitalLifeNav.jsx'
import DigitalLifeOverviewHeader from './DigitalLifeOverviewHeader.jsx'
import DigitalLifeOverviewNotices from './DigitalLifeOverviewNotices.jsx'
import DigitalLifeRuntimeDetailSection from './DigitalLifeRuntimeDetailSection.jsx'
import DigitalLifeWallClockSection from './DigitalLifeWallClockSection.jsx'
import MainChainReshapePanel from './MainChainReshapePanel.jsx'
import LifeBodyFocusPanel from './lifeBody/LifeBodyFocusPanel.jsx'
import LifeBodyPathologyPanel from './lifeBody/LifeBodyPathologyPanel.jsx'
import LifeBodyTimeline from './lifeBody/LifeBodyTimeline.jsx'
import LifeBodyTopology from './lifeBody/LifeBodyTopology.jsx'
import LifeBodyVitalsStrip from './lifeBody/LifeBodyVitalsStrip.jsx'
import LifeEvolutionSection from './LifeEvolutionSection.jsx'
import { buildDigitalLifeFocusLensModel } from '../digitalLifeOverviewModel.js'

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
  commandRailModel,
  mainChainReshapeModel,
  wallClockSoakQuery,
  lifeEvolutionUiEnabled,
  lifeEvolutionQuery,
  lifeEvolutionModel,
  lifePreviewBusy,
  lifePreviewError,
  onRunLifeActionPreview,
  avatarStateQuery,
  avatarCommandBusy,
  avatarCommandError,
  onApplyAvatarBehavior,
  capabilitySectionModel,
  gatewaySectionModel,
  secondarySectionsModel,
}) {
  const [focusLensMode, setFocusLensMode] = useState('priority')
  const [showSystemDetails, setShowSystemDetails] = useState(false)
  const dashboardBusy = Boolean(
    loading
      || refreshing
      || runtimeLoading
      || wallClockSoakQuery.loading
      || wallClockSoakQuery.refreshing
  )
  const refreshAnimating = Boolean(dashboardBusy || avatarStateQuery.refreshing)
  const focusLensModel = useMemo(
    () => buildDigitalLifeFocusLensModel(lifeBodyModel, focusLensMode),
    [focusLensMode, lifeBodyModel]
  )
  const visibleLifeBodyModel = focusLensModel.visibleModel || lifeBodyModel

  return (
    <div className="space-y-6">
      <DigitalLifeOverviewHeader
        windowMinutes={windowMinutes}
        onWindowMinutesChange={onWindowMinutesChange}
        onRefreshDashboard={onRefreshDashboard}
        dashboardBusy={dashboardBusy}
        refreshAnimating={refreshAnimating}
      />

      <DigitalLifeNav />

      <DigitalLifeHeroSection
        modeMeta={modeMeta}
        lifeBodyModel={lifeBodyModel}
        heroDescription={heroDescription}
        heroMetaItems={heroMetaItems}
        overviewLoadState={overviewLoadState}
        error={error}
        refreshWarning={refreshWarning}
        windowMinutes={windowMinutes}
        overviewTrackedSections={overviewTrackedSections}
        overviewUpdatedAt={overviewUpdatedAt}
      />

      <DigitalLifeCommandRail
        model={commandRailModel}
        dashboardBusy={dashboardBusy}
        onRefreshDashboard={onRefreshDashboard}
        refreshWarning={refreshWarning}
      />

      <DigitalLifeFocusLens
        model={focusLensModel}
        onModeChange={setFocusLensMode}
      />

      <MainChainReshapePanel model={mainChainReshapeModel} />
      <LifeBodyVitalsStrip vitals={visibleLifeBodyModel.vitalSigns} />
      <LifeBodyFocusPanel guidance={lifeBodyModel.diagnosticGuidance} />

      <DigitalLifeAvatarSection
        avatarStateQuery={avatarStateQuery}
        avatarCommandBusy={avatarCommandBusy}
        avatarCommandError={avatarCommandError}
        onApplyAvatarBehavior={onApplyAvatarBehavior}
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.8fr)_minmax(320px,0.95fr)]">
        <LifeBodyTopology
          lifeState={visibleLifeBodyModel.lifeState}
          organSystems={visibleLifeBodyModel.organSystems}
          organStatusCounts={visibleLifeBodyModel.organStatusCounts}
          primaryPathology={lifeBodyModel.primaryPathology}
          primaryOrganId={lifeBodyModel.primaryOrgan?.id}
        />
        <LifeBodyPathologyPanel pathologies={visibleLifeBodyModel.pathologies} />
      </div>

      <LifeBodyTimeline events={visibleLifeBodyModel.timelineEvents} />

      <DigitalLifeOverviewNotices
        error={error}
        refreshWarning={refreshWarning}
        runtimeError={runtimeError}
      />

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowSystemDetails(event.currentTarget.open)}
      >
        <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 text-sm font-semibold text-gray-800">
          <span>
            系统诊断与历史详情
            <span className="ml-2 text-xs font-normal text-gray-500">时钟、演化、运行快照、能力与 Gateway 深层视图</span>
          </span>
          <span className="shrink-0 rounded-full border border-primary-100 bg-primary-50 px-2.5 py-1 text-xs text-primary">
            {showSystemDetails ? '收起' : '展开'}
          </span>
        </summary>
        {showSystemDetails ? (
          <div className="space-y-6 border-t border-gray-100 p-4">
            <DigitalLifeWallClockSection wallClockSoakQuery={wallClockSoakQuery} />

            {lifeEvolutionUiEnabled ? (
              <LifeEvolutionSection
                query={lifeEvolutionQuery}
                model={lifeEvolutionModel}
                previewBusy={lifePreviewBusy}
                previewError={lifePreviewError}
                onPreview={onRunLifeActionPreview}
              />
            ) : null}

            <DigitalLifeRuntimeDetailSection
              runtimeSnapshot={runtimeSnapshot}
              runtimeLoading={runtimeLoading}
              promptStats={promptStats}
              capabilityGapData={capabilityGapData}
            />

            <DigitalLifeLazySections
              capabilitySectionModel={capabilitySectionModel}
              gatewaySectionModel={gatewaySectionModel}
              secondarySectionsModel={secondarySectionsModel}
            />
          </div>
        ) : null}
      </details>
    </div>
  )
}
