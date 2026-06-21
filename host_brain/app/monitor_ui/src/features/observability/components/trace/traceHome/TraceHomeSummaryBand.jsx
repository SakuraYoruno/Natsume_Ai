import { Activity, Bookmark, Clock3, Layers, Shield } from 'lucide-react'
import LivingPulseStrip from '../../../../../shared/ui/LivingPulseStrip.jsx'
import PageHero from '../../../../../shared/ui/PageHero.jsx'
import { HomeSummaryCard } from '../TraceSurfaces.jsx'

export default function TraceHomeSummaryBand({
  currentTraceHealthy,
  liveFeedConnected,
  liveModeCaption,
  liveModeLabel,
  liveTraceItems,
  pollingEnabled,
  selectedCurrentNodeLabel,
  selectedIssueCount,
  selectedLiveTraceTitle,
  selectedOutputPreview,
  selectedRequestId,
  selectedRunningCount,
  selectedTraceStageMeta,
  traceGroupCount,
  traceInfo,
  tracePulseNodes,
}) {
  return (
    <>
      <PageHero
        icon={Activity}
        eyebrow="神经传导 Trace"
        title="数字生命体神经链路可视化"
        description="从感官输入、检索规划、人格上下文到响应输出，统一查看当前请求的完整神经传导路径。"
        gradientClass="from-blue-50 via-white to-violet-50"
        metaItems={[
          `活跃请求：${liveTraceItems.length}`,
          `当前节点：${traceInfo?.nodeCount ?? 0}`,
          selectedRequestId ? '已选中请求' : '未选中请求',
          currentTraceHealthy ? '当前链路：健康' : '当前链路：待关注',
        ]}
      />

      <LivingPulseStrip
        title="链路脉冲"
        subtitle="先看这一条神经流现在怎么流动，再下钻到单条请求或节点细节。"
        nodes={tracePulseNodes}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <HomeSummaryCard
          icon={Bookmark}
          title="当前请求"
          value={selectedLiveTraceTitle || '等待选中请求'}
          caption={selectedRequestId || '从下方活跃请求流选择一条 request'}
        />
        <HomeSummaryCard
          icon={Layers}
          title="阶段覆盖"
          value={`${selectedTraceStageMeta.activeStageCount}/${traceGroupCount}`}
          caption={`当前阶段：${selectedTraceStageMeta.currentStageLabel}`}
        />
        <HomeSummaryCard
          icon={Shield}
          title="待关注节点"
          value={selectedIssueCount > 0 ? `${selectedIssueCount} 个异常/降级` : '无异常节点'}
          caption={selectedRunningCount > 0 ? `运行中 ${selectedRunningCount} 个节点` : (traceInfo?.latency != null ? `总耗时 ${traceInfo.latency}ms` : '等待节点继续推进')}
          toneClassName={selectedIssueCount > 0 ? 'text-orange-600' : 'text-emerald-600'}
        />
        <HomeSummaryCard
          icon={Activity}
          title="当前节点"
          value={selectedCurrentNodeLabel}
          caption={selectedOutputPreview || '暂无输出摘要，可能仍在执行或尚未落盘。'}
        />
        <HomeSummaryCard
          icon={Clock3}
          title="实时模式"
          value={liveModeLabel}
          caption={liveModeCaption}
          toneClassName={!pollingEnabled ? 'text-gray-700' : liveFeedConnected ? 'text-emerald-600' : 'text-amber-600'}
        />
      </div>
    </>
  )
}
