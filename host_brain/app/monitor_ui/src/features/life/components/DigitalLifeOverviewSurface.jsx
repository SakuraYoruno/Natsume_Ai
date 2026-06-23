import React from 'react'
import DigitalLifeAvatarSection from './DigitalLifeAvatarSection.jsx'
import DigitalLifeCommandRail from './DigitalLifeCommandRail.jsx'
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

      <MainChainReshapePanel model={mainChainReshapeModel} />
      <LifeBodyVitalsStrip vitals={lifeBodyModel.vitalSigns} />
      <LifeBodyFocusPanel guidance={lifeBodyModel.diagnosticGuidance} />

      <DigitalLifeAvatarSection
        avatarStateQuery={avatarStateQuery}
        avatarCommandBusy={avatarCommandBusy}
        avatarCommandError={avatarCommandError}
        onApplyAvatarBehavior={onApplyAvatarBehavior}
      />

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

      <DigitalLifeOverviewNotices
        error={error}
        refreshWarning={refreshWarning}
        runtimeError={runtimeError}
      />

      <DigitalLifeWallClockSection wallClockSoakQuery={wallClockSoakQuery} />

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
  )
}
