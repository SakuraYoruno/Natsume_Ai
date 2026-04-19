import React from 'react'
import { Orbit, ShieldAlert } from 'lucide-react'
import DrilldownLink from '../obs/DrilldownLink.jsx'
import SampleMeta from '../obs/SampleMeta.jsx'
import EmptyStateCard from '../EmptyStateCard.jsx'
import KeyValueRow from '../KeyValueRow.jsx'
import MetricCard from '../MetricCard.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import {
  DomainCard,
  GrowthTextCard,
  LifeSection,
} from './digitalLifeSectionShared.jsx'
import {
  formatSlotLabel,
  formatSlotStatus,
} from '../../utils/digitalLife.js'

export default function DigitalLifeSecondarySections({ model }) {
  const {
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
  } = model

  return (
    <>
      <LifeSection title="体征核心指标" hint="把关键体征压成一屏可读指标，避免拆散到多个监控页。">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="当前情绪" value={currentEmotion} hint={`VTS 事件 ${vtsData?.total_events ?? 0}`} tone="text-pink-600" />
          <MetricCard label="关系阶段" value={currentBondStage} hint={`趋势 ${bondState.trend ?? '--'}`} tone="text-rose-600" />
          <MetricCard label="主导动机" value={dominantMotive} hint={`回复姿态 ${replyPosture}`} tone="text-violet-600" />
          <MetricCard label="关键槽位" value={nonLiveSlots.length === 0 ? '全部在线' : `${nonLiveSlots.length} 个异常`} hint={`风险 ${riskLevel}`} tone="text-orange-500" />
        </div>

        {runtimeAlerts.length > 0 ? (
          <div className="mt-4 space-y-2">
            {runtimeAlerts.slice(0, 3).map((item) => (
              <NoticeBanner
                key={item.reason_code}
                variant={item.severity === 'critical' ? 'error' : item.severity === 'warning' ? 'warning' : 'info'}
                title={item.title}
              >
                {item.detail}
              </NoticeBanner>
            ))}
          </div>
        ) : null}

        <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
          <SampleMeta
            loadState={runtimeVitalsLoadState}
            windowMinutes={windowMinutes}
            sampleSize={Object.keys(runtimeSnapshot?.slot_status || {}).length || null}
            sampleLabel="关键槽位"
            updatedAt={runtimeUpdatedAt}
            source="runtime snapshot"
            definition="体征核心指标来自运行快照，用于快速判定是否继续向情绪、关系、策略或 Trace 深钻。"
          />
          <DrilldownLink
            to="/trace"
            label="进入 Trace"
            hint="如果体征异常，优先沿 request / session / tick 主链继续排查。"
          />
        </div>
      </LifeSection>

      <LifeSection
        title="非监督成长"
        hint="成长面板不再是独立后台模块，而是生命体连续性的一部分。"
        actionTo="/self-model"
        actionLabel="查看详情"
      >
        {growthSignals.hasAnyGrowth ? (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <MetricCard label="成长周期" value={growthSignals.cycleCount || 0} hint={growthSignals.lastCycleLabel !== '--' ? `最近周期 ${growthSignals.lastCycleLabel}` : '尚未形成稳定周期'} tone="text-fuchsia-600" />
              <MetricCard label="活跃主题" value={growthSignals.activeThemes.length} hint={growthSignals.activeThemes.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 42) : '暂无主题聚合'} tone="text-indigo-600" />
              <MetricCard label="成长里程碑" value={growthSignals.milestones.length} hint={growthSignals.milestones.length > 0 ? compactText(growthSignals.milestones[0], 36) : '暂无里程碑'} tone="text-purple-600" />
              <MetricCard label="护栏风险" value={riskLevel} hint="成长展示不会绕过叙事护栏" tone="text-orange-500" />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="space-y-3">
                <KeyValueRow label="成长总览" value={growthSignals.primarySummary || '-'} maxValueWidthClassName="max-w-[72%]" />
                <KeyValueRow label="最近周期" value={growthSignals.lastCycleLabel} />
                {growthSignals.awarenessItems.map((item) => (
                  <KeyValueRow key={item.key} label={item.label} value={item.value} maxValueWidthClassName="max-w-[72%]" />
                ))}
              </div>

              <div className="space-y-3">
                {growthSignals.insightSections.slice(0, 3).map((item) => (
                  <GrowthTextCard key={item.key} title={item.label} text={item.value} compactText={compactText} />
                ))}
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium text-gray-500">活跃成长主题</div>
              <div className="flex flex-wrap gap-2">
                {growthSignals.activeThemes.length > 0 ? (
                  growthSignals.activeThemes.map((item) => (
                    <span key={item} className="rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2.5 py-1 text-xs font-medium text-fuchsia-700">
                      {item}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-gray-400">当前还没有稳定的成长主题。</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyStateCard
            title="非监督成长尚未形成可观测快照"
            hint="等成长周期、主题或感知文本累积后，这里会出现摘要并直连自我模型详情。"
            className="p-4"
          />
        )}
      </LifeSection>

      <LifeSection title="器官系统联通态" hint="将已实现的类人器官系统聚合为统一联通视图。">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {wiredDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </LifeSection>

      <LifeSection title="体内快照与异常槽位" hint="左侧看当前体内快照，右侧暴露离线/降级槽位。">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldAlert size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">当前快照</h3>
            </div>
            <div className="space-y-3">
              <KeyValueRow label="当前模式" value={modeMeta.label} />
              <KeyValueRow label="离线分层" value={absenceBand} />
              <KeyValueRow label="回复上限" value={responsePolicy.max_chars != null ? `${responsePolicy.max_chars} 字` : '--'} />
              <KeyValueRow label="允许追问" value={responsePolicy.allow_followup == null ? '--' : responsePolicy.allow_followup ? '是' : '否'} />
              <KeyValueRow label="允许换题" value={responsePolicy.allow_topic_shift == null ? '--' : responsePolicy.allow_topic_shift ? '是' : '否'} />
              <KeyValueRow label="平均记忆成功率" value={!hasMemoryData ? '--' : hasMemorySamples ? `${avgMemorySuccessRate}%` : '无样本'} />
            </div>
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Orbit size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">非在线槽位</h3>
            </div>
            {!hasRuntimeSnapshot ? (
              <EmptyStateCard
                title="运行快照暂不可用"
                hint="等待 runtime snapshot 恢复后，这里会展示缺席或降级的槽位。"
                className="p-5"
              />
            ) : nonLiveSlots.length > 0 ? (
              <div className="space-y-3">
                {nonLiveSlots.map(([slot, status]) => (
                  <KeyValueRow
                    key={slot}
                    label={formatSlotLabel(slot)}
                    value={formatSlotStatus(status)}
                    valueClassName={status === 'degraded' ? 'text-orange-600' : 'text-red-600'}
                  />
                ))}
              </div>
            ) : (
              <EmptyStateCard title="全部关键槽位在线" hint="当前快照中没有检测到缺席或降级的核心槽位。" className="p-5" />
            )}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
          <SampleMeta
            loadState={memoryOverviewLoadState}
            windowMinutes={windowMinutes}
            sampleSize={sampledMemorySubsystemCount}
            sampleLabel="有样本子系统"
            updatedAt={lastOverviewUpdatedAt}
            source="/obs/memory/stats"
            definition="平均记忆成功率只统计当前窗口内有样本的子系统；无样本会保持中性展示，不再误报为 0%。"
          />
          <DrilldownLink
            to="/memory"
            label="查看记忆详情"
            hint="从这里继续下钻到子系统成功率、召回包预算、蒸馏与常驻设定。"
          />
        </div>
      </LifeSection>
    </>
  )
}
