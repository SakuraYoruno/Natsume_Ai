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
    humanoidReplyCommitRate,
    humanoidDeviationRecoveryRate,
    humanoidTotalRequests,
    humanoidFailedRequests,
    humanoidDeviationDetected,
    humanoidClosedLoopHint,
    humanoidReplyCommitHint,
    humanoidDeviationHint,
    humanoidFailureBannerVariant,
    humanoidCompletionRate,
  } = view

  return (
    <LifeSection
      title="Gateway 回复闭环健康"
      hint="展示请求从 Acceptance Reply Outbox 提交、终态收束到偏航修复的健康度。"
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
          definition="终态、Acceptance Reply Outbox 提交与偏航恢复都以当前窗口中的 gateway request 为统一口径；Outbox 提交不等于外部平台物理 ACK。"
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
          label="回复入 Outbox"
          value={humanoidReplyCommitRate}
          hint={humanoidReplyCommitHint}
          tone="text-blue-600"
        />
        <MetricCard
          label="终态率"
          value={humanoidCompletionRate}
          hint={`完成 ${humanoidLoopData?.completed_requests ?? 0}`}
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
            hint="等待 Gateway 请求进入后，这里会显示回复 Outbox、终态和偏航恢复指标。"
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
