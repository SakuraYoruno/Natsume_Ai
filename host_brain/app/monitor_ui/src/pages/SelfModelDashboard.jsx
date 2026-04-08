import React from 'react'
import { Brain, Heart, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { getGrowthSignals } from '../utils/growthSignals.js'
import {
  asArray,
  compactText,
  formatAllowedStyle,
  formatDigitalLifeTag,
  formatEmotionTag,
  formatNumber,
  formatRiskLevel,
  formatStage,
  formatTrend,
} from '../utils/digitalLife.js'

function ListBlock({ title, items, emptyText }) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
      <div className="mt-4 space-y-2">
        {items.length > 0 ? (
          items.map((item, index) => (
            <div
              key={`${title}-${index}`}
              className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
            >
              {compactText(item, 220)}
            </div>
          ))
        ) : (
          <EmptyStateCard title="暂无条目" hint={emptyText} className="p-4" />
        )}
      </div>
    </div>
  )
}

function SummaryBlock({ title, text }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {compactText(text, 240)}
      </div>
    </div>
  )
}

export default function SelfModelDashboard() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const { runtimeSnapshot, loading, error, reload } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const selfModel = runtimeSnapshot?.self_model_snapshot || {}
  const selfReflection = runtimeSnapshot?.self_reflection_pulse || {}
  const guardrails = selfReflection?.narrative_guardrails || {}
  const bondSummary = runtimeSnapshot?.bond_summary_human || {}
  const growthSignals = getGrowthSignals(runtimeSnapshot)

  const milestones = asArray(selfModel.growth_milestones)
  const personalityChanges = asArray(selfModel.personality_changes)
  const riskTags = asArray(guardrails.risk_tags)
  const promptDirectives = asArray(guardrails.prompt_directives)
  const riskLevelRaw = String(guardrails.risk_level || '').toLowerCase()
  const riskLevelLabel = formatRiskLevel(riskLevelRaw)
  const allowedStyleLabel = formatAllowedStyle(guardrails.allowed_style)
  const formattedRiskTags = riskTags.map(formatDigitalLifeTag).filter(Boolean)
  const recentMoodTrendLabel = selfModel.recent_mood_trend ? formatTrend(selfModel.recent_mood_trend) : '平稳'
  const dominantEmotionLabel = selfModel.dominant_recent_emotion
    ? formatEmotionTag(selfModel.dominant_recent_emotion)
    : '-'
  const triggerReasonLabel = selfReflection.trigger_reason ? formatDigitalLifeTag(selfReflection.trigger_reason) : '-'
  const bondTrendLabel = (bondSummary.trend || runtimeSnapshot?.bond_state?.trend)
    ? formatTrend(bondSummary.trend || runtimeSnapshot?.bond_state?.trend)
    : '-'
  const growthHeadline = growthSignals.primarySummary || selfReflection.growth_note || selfReflection.self_awareness || '当前暂无明显成长摘要。'

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">自我模型</h1>
          <p className="text-sm text-gray-500">自我模型、反思脉冲、成长里程碑与叙事护栏。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Brain}
        eyebrow="人格与成长专题"
        title={recentMoodTrendLabel}
        description={compactText(growthHeadline, 220)}
        gradientClass="from-primary-50 via-white to-violet-50"
        metaItems={[
          `阶段：${formatStage(runtimeSnapshot?.bond_state?.current_stage)}`,
          `共享记忆：${selfModel.shared_memory_count ?? 0}`,
          `成长周期：${growthSignals.cycleCount || 0}`,
          `风险：${riskLevelLabel}`,
        ]}
      />

      {error ? (
        <NoticeBanner variant="error" title="自我模型数据加载失败">
          {error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <MetricCard
          label="情绪稳定性"
          value={formatNumber(selfModel.emotional_stability, 2)}
          hint={recentMoodTrendLabel}
          tone="text-blue-600"
        />
        <MetricCard
          label="共享记忆"
          value={selfModel.shared_memory_count ?? 0}
          hint={
            selfModel.last_deep_talk_days_ago != null
              ? `最近深聊：${selfModel.last_deep_talk_days_ago} 天前`
              : '最近暂无深聊记录'
          }
          tone="text-pink-600"
        />
        <MetricCard label="成长里程碑" value={milestones.length} hint="已记录节点" tone="text-purple-600" />
        <MetricCard
          label="成长周期"
          value={growthSignals.cycleCount || 0}
          hint={growthSignals.lastCycleLabel !== '—' ? `最近周期 ${growthSignals.lastCycleLabel}` : '尚未形成稳定周期'}
          tone="text-fuchsia-600"
        />
        <MetricCard
          label="活跃主题"
          value={growthSignals.activeThemes.length}
          hint={growthSignals.activeThemes.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 36) : '暂无主题聚合'}
          tone="text-indigo-600"
        />
        <MetricCard
          label="护栏风险"
          value={riskLevelLabel}
          hint={`${riskTags.length} 个风险标签`}
          tone={riskLevelRaw === 'high' || riskLevelRaw === 'critical' ? 'text-red-500' : riskLevelRaw === 'medium' ? 'text-orange-500' : 'text-green-600'}
        />
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">非监督成长</h2>
        </div>
        {growthSignals.hasAnyGrowth ? (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="space-y-3">
                <KeyValueRow label="成长总览" value={growthSignals.primarySummary || '-'} maxValueWidthClassName="max-w-[72%]" />
                <KeyValueRow label="最近周期" value={growthSignals.lastCycleLabel} />
                <KeyValueRow label="成长周期数" value={growthSignals.cycleCount || 0} />
                <KeyValueRow label="活跃主题数" value={growthSignals.activeThemes.length} />
                {growthSignals.awarenessItems.map((item) => (
                  <KeyValueRow
                    key={item.key}
                    label={item.label}
                    value={item.value}
                    maxValueWidthClassName="max-w-[72%]"
                  />
                ))}
              </div>

              <div className="space-y-3">
                {growthSignals.insightSections.length > 0 ? (
                  growthSignals.insightSections.map((item) => (
                    <SummaryBlock key={item.key} title={item.label} text={item.value} />
                  ))
                ) : (
                  <EmptyStateCard title="暂无成长摘要" hint="当前快照还没有聚合出可展示的成长文本。" className="p-4" />
                )}
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium text-gray-500">活跃成长主题</div>
              <div className="flex flex-wrap gap-2">
                {growthSignals.activeThemes.length > 0 ? (
                  growthSignals.activeThemes.map((item) => (
                    <span
                      key={item}
                      className="rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2.5 py-1 text-xs font-medium text-fuchsia-700"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-gray-400">当前还没有稳定的主题聚合。</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyStateCard
            title="非监督成长尚在积累"
            hint="后端已经接入成长字段，但当前窗口还没有足够的成长周期、主题或感知文本。"
            className="mt-4 p-4"
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">自我模型</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="最近情绪趋势" value={recentMoodTrendLabel} />
            <KeyValueRow label="主导近期情绪" value={dominantEmotionLabel} />
            <KeyValueRow label="关系成长感知" value={selfModel.bond_growth_perception || '-'} />
            <KeyValueRow label="共享记忆数" value={selfModel.shared_memory_count ?? 0} />
            <KeyValueRow
              label="最近深聊"
              value={selfModel.last_deep_talk_days_ago != null ? `${selfModel.last_deep_talk_days_ago} 天前` : '-'}
            />
            <KeyValueRow label="人格变化数" value={personalityChanges.length} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">自我反思脉冲</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="自我感知" value={selfReflection.self_awareness || '-'} />
            <KeyValueRow label="情绪感知" value={selfReflection.mood_awareness || '-'} />
            <KeyValueRow label="成长笔记" value={selfReflection.growth_note || '-'} />
            <KeyValueRow label="触发原因" value={triggerReasonLabel} />
            <KeyValueRow label="允许风格" value={allowedStyleLabel} />
            <KeyValueRow label="可扩展自叙事" value={guardrails.allow_self_narrative_expansion ? '是' : '否'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">叙事护栏</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="风险等级" value={riskLevelLabel} />
            <KeyValueRow label="最大自叙事句数" value={guardrails.max_self_narrative_sentences ?? '-'} />
            <KeyValueRow label="护栏指令" value={guardrails.instruction || '-'} />
          </div>
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-gray-500">风险标签</div>
            <div className="flex flex-wrap gap-2">
              {formattedRiskTags.length > 0 ? (
                formattedRiskTags.map((item) => (
                  <span
                    key={item}
                    className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-xs font-medium text-orange-700"
                  >
                    {item}
                  </span>
                ))
              ) : (
                <span className="text-sm text-gray-400">暂无风险标签</span>
              )}
            </div>
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Heart size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">关系摘要</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="阶段" value={formatStage(bondSummary.stage || runtimeSnapshot?.bond_state?.current_stage)} />
            <KeyValueRow label="趋势" value={bondTrendLabel} />
            <KeyValueRow label="能力提示" value={bondSummary.capability_hint || '-'} />
            <KeyValueRow label="共享记忆" value={bondSummary.shared_memory_count ?? '—'} />
            <KeyValueRow label="里程碑提示" value={bondSummary.milestone_hint || '-'} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <ListBlock title="成长里程碑" items={milestones} emptyText="当前还没有明确的成长里程碑。" />
        <ListBlock title="活跃成长主题" items={growthSignals.activeThemes} emptyText="当前还没有形成稳定的成长主题。" />
        <ListBlock title="人格变化" items={personalityChanges} emptyText="目前没有检测到可记录的人格变化。" />
        <ListBlock title="提示指令" items={promptDirectives} emptyText="当前没有额外的提示指令。" />
      </div>
    </div>
  )
}
