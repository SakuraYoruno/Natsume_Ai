import { useRef } from 'react'
import {
  ArrowDownToLine,
  MessageSquare,
  RefreshCw,
  Send,
  XCircle,
  Zap,
} from 'lucide-react'
import MetricCard from '../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../../../shared/ui/TimeWindowSelect.jsx'
import DrilldownLink from '../../../shared/ui/observability/DrilldownLink.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import { GATEWAY_COPY } from '../../../shared/content/terminology.js'
import { GATEWAY_WINDOW_OPTIONS } from '../gatewayModel.js'
import GatewayCommandRail from './GatewayCommandRail.jsx'
import GatewayPlatformsSection from './gateway/GatewayPlatformsSection.jsx'
import GatewayWeChatDetailCard from './gateway/GatewayWeChatDetailCard.jsx'

const GATEWAY_METRIC_ICONS = {
  inbound: ArrowDownToLine,
  outbound: Send,
  errors: XCircle,
  humanize: Zap,
}

export default function GatewaySurface({
  windowMinutes,
  onWindowMinutesChange,
  onRefresh,
  loading,
  refreshing,
  error,
  lastUpdatedAt,
  platforms,
  gatewayEventCount,
  hasWeChatRateLimits,
  hasWeChatEvents,
  gatewayLoadState,
  motorOrganModel,
  heroTitle,
  heroDescription,
  heroMetaItems,
  metricCards,
  commandRailView,
  showUnavailableNotice,
  showNoSampleNotice,
  wechatDetail,
  OrganOverviewComponent,
}) {
  const platformSectionRef = useRef(null)
  const wechatSectionRef = useRef(null)
  const scrollToSection = (ref) => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{GATEWAY_COPY.heroTitle}</h1>
          <p className="mt-0.5 text-sm text-gray-500">{GATEWAY_COPY.heroSubtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect
            value={windowMinutes}
            onChange={onWindowMinutesChange}
            options={GATEWAY_WINDOW_OPTIONS}
            className="rounded border border-gray-300 px-2 py-1.5 text-xs"
          />
          <button className="btn-secondary" onClick={onRefresh} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={MessageSquare}
        eyebrow={GATEWAY_COPY.heroEyebrow}
        title={heroTitle}
        description={heroDescription}
        gradientClass="from-primary-50 via-white to-emerald-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={gatewayLoadState}
            windowMinutes={windowMinutes}
            sampleSize={gatewayEventCount}
            sampleLabel="事件计数"
            updatedAt={lastUpdatedAt}
            source="gateway + wechat detail"
            definition="Gateway 页同时展示平台健康态和窗口内消息事件；空流量与接口不可用会明确区分。"
          />
        )}
      />

      <GatewayCommandRail
        busy={loading || refreshing}
        view={commandRailView}
        onFocusPlatforms={() => scrollToSection(platformSectionRef)}
        onFocusWeChat={() => scrollToSection(wechatSectionRef)}
        onRefresh={onRefresh}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}
      {showUnavailableNotice ? (
        <NoticeBanner variant="warning">
          网关观测接口当前未返回可用数据，页面会保留中性空态，避免把不可用误判为空流量。
        </NoticeBanner>
      ) : null}

      {showNoSampleNotice ? (
        <div className="card p-4 text-sm text-gray-500">
          <div className="mb-1 text-xs text-gray-400">
            当前数据为空流量初始态，页面显示的是健康快照，不是接口故障。
          </div>
          如果你正在压测或联调，请先触发一条网关输入后再观察分平台统计。
        </div>
      ) : null}

      {OrganOverviewComponent ? <OrganOverviewComponent model={motorOrganModel} /> : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard
            key={card.key}
            label={card.label}
            value={card.value}
            tone={card.tone}
            icon={GATEWAY_METRIC_ICONS[card.key]}
            variant="stacked"
            hint={card.hint}
          />
        ))}
      </div>

      <DrilldownLink
        to="/observability/trace"
        label="进入 Trace"
        hint="如果平台健康但用户仍感知异常，优先从 request 级链路继续追 Gateway 前后的主流程。"
      />

      <div id="gateway-platform-health" ref={platformSectionRef} className="scroll-mt-24">
        <GatewayPlatformsSection platforms={platforms} />
      </div>

      <div id="gateway-wechat-detail" ref={wechatSectionRef} className="scroll-mt-24">
        <GatewayWeChatDetailCard
          wechatDetail={wechatDetail}
          hasWeChatRateLimits={hasWeChatRateLimits}
          hasWeChatEvents={hasWeChatEvents}
        />
      </div>
    </div>
  )
}
