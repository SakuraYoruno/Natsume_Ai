import React from 'react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'
import {
  buildDigitalLifeGatewayView,
} from '../digitalLifeGatewayModel.js'
import GatewayLoopStatsPanel from './digitalLifeGateway/GatewayLoopStatsPanel.jsx'
import GatewayRemediationPanel from './digitalLifeGateway/GatewayRemediationPanel.jsx'
import GatewayTrendPanel from './digitalLifeGateway/GatewayTrendPanel.jsx'

export default function DigitalLifeGatewaySection({ model }) {
  const {
    windowMinutes,
    updatedAt,
    loadState,
    humanoidLoopData,
  } = model

  const view = buildDigitalLifeGatewayView(model)
  const {
    humanoidClosedLoopRate,
    humanoidAckTouchRate,
    humanoidProgressTouchRate,
    humanoidDeviationRecoveryRate,
    humanoidTotalRequests,
    humanoidFailedRequests,
    humanoidDeviationDetected,
    humanoidClosedLoopHint,
    humanoidAckHint,
    humanoidProgressHint,
    humanoidDeviationHint,
    humanoidFailureBannerVariant,
    humanoidCompletionRate,
  } = view

  return (
    <LifeSection
      title="人体闭环健康（Gateway）"
      hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
      actionTo="/execution/gateway"
      actionLabel="查看通道详情"
    >
      <div className="mb-4">
        <SampleMeta
          loadState={loadState}
          windowMinutes={windowMinutes}
          sampleSize={humanoidTotalRequests}
          sampleLabel="gateway request"
          updatedAt={updatedAt}
          source="/obs/input/humanoid-loop-stats"
          definition="终态收束、ACK 触达、进度触达与偏航恢复都以当前窗口中的 gateway request 为统一统计口径。"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          label="闭环率"
          value={humanoidClosedLoopRate}
          hint={humanoidClosedLoopHint}
          tone="text-emerald-600"
        />
        <MetricCard
          label="ACK 触达率"
          value={humanoidAckTouchRate}
          hint={humanoidAckHint}
          tone="text-blue-600"
        />
        <MetricCard
          label="进度触达率"
          value={humanoidProgressTouchRate}
          hint={humanoidProgressHint}
          tone="text-indigo-600"
        />
        <MetricCard
          label="偏航恢复率"
          value={humanoidDeviationRecoveryRate}
          hint={humanoidDeviationHint}
          tone={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-600'}
        />
      </div>

      <GatewayTrendPanel view={view} hasAlertsData={Boolean(model.humanoidLoopAlertsData)} />

      {!humanoidLoopData ? (
        <div className="mt-4">
          <EmptyStateCard
            title="暂无人体闭环采样"
            hint="等待 Gateway 请求进入后，这里会显示 ACK、进度触达和偏航恢复指标。"
            className="p-4"
          />
        </div>
      ) : null}

      {humanoidLoopData && humanoidFailedRequests > 0 ? (
        <div className="mt-4">
          <NoticeBanner
            variant={humanoidFailureBannerVariant}
            title="闭环存在失败请求"
          >
            {`失败 ${humanoidFailedRequests} · 已进入终态 ${humanoidLoopData?.completed_requests ?? 0} · 终态率 ${humanoidCompletionRate}。该口径按是否出现终态事件统计，可能与失败计数重叠。`}
          </NoticeBanner>
        </div>
      ) : null}

      <GatewayLoopStatsPanel
        humanoidLoopData={humanoidLoopData}
        humanoidTotalRequests={humanoidTotalRequests}
        humanoidFailedRequests={humanoidFailedRequests}
        humanoidDeviationDetected={humanoidDeviationDetected}
      />

      <GatewayRemediationPanel model={model} view={view} />
    </LifeSection>
  )
}
