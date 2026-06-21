import React from 'react'
import { LazyLoadingNotice, LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import ViewportLazySection from '../../../shared/ui/observability/ViewportLazySection.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'

const DigitalLifeCapabilitySection = React.lazy(() => import('./DigitalLifeCapabilitySection.jsx'))
const DigitalLifeGatewaySection = React.lazy(() => import('./DigitalLifeGatewaySection.jsx'))
const DigitalLifeSecondarySections = React.lazy(() => import('./DigitalLifeSecondarySections.jsx'))

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

export default function DigitalLifeLazySections({
  capabilitySectionModel,
  gatewaySectionModel,
  secondarySectionsModel,
}) {
  return (
    <>
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
    </>
  )
}
