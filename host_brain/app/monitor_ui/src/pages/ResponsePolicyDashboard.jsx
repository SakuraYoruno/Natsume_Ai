import React from 'react'
import {
  Brain,
  HeartPulse,
  MessagesSquare,
  MoonStar,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import VitalLoopSummaryPanel from '../components/VitalLoopSummaryPanel.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import {
  asArray,
  classNames,
  compactText,
  formatBoolean,
  formatDigitalLifeTag,
  formatDominantMotive,
  formatDrowsinessLevel,
  formatFatigueLevel,
  formatNumber,
  formatReplyPosture,
  formatWillingnessLevel,
} from '../utils/digitalLife.js'

function TagList({ items, tone = 'gray', emptyTitle, emptyHint }) {
  if (!items.length) {
    return <EmptyStateCard title={emptyTitle} hint={emptyHint} className="p-5" />
  }

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className={classNames(
            'rounded-full border px-2.5 py-1 text-xs font-medium',
            tone === 'orange'
              ? 'border-orange-200 bg-orange-50 text-orange-700'
              : tone === 'purple'
                ? 'border-purple-200 bg-purple-50 text-purple-700'
                : 'border-gray-200 bg-gray-50 text-gray-700'
          )}
        >
          {item}
        </span>
      ))}
    </div>
  )
}

export default function ResponsePolicyDashboard() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, loading, error, reload } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const silenceState = runtimeSnapshot?.silence_state || {}
  const bodySense = runtimeSnapshot?.body_sense || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const responsePolicy = runtimeSnapshot?.response_policy || {}
  const runtimeSummary = runtimeSnapshot?.runtime_summary || {}

  const whyReply = asArray(runtimeSummary.why_reply)
  const decisionChain = asArray(runtimeSummary.decision_chain)
  const policyReasonTags = asArray(responsePolicy.policy_reason_tags)
  const motiveReasonTags = asArray(motiveState.motive_reason_tags)
  const activeDrives = motiveState.active_drives || motiveState || {}
  const replyPostureLabel = formatReplyPosture(runtimeSummary.reply_posture?.style)
  const willingnessLabel = formatWillingnessLevel(silenceState.willingness_level)
  const dominantMotiveLabel = formatDominantMotive(motiveState.dominant_motive)
  const fatigueLabel = formatFatigueLevel(bodySense.fatigue_level)
  const drowsinessLabel = formatDrowsinessLevel(bodySense.drowsiness_level)
  const formattedWhyReply = whyReply.map(formatDigitalLifeTag).filter(Boolean)
  const formattedDecisionChain = decisionChain.map(formatDigitalLifeTag).filter(Boolean)
  const formattedPolicyReasonTags = policyReasonTags.map(formatDigitalLifeTag).filter(Boolean)
  const formattedMotiveReasonTags = motiveReasonTags.map(formatDigitalLifeTag).filter(Boolean)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">回复策略</h1>
          <p className="text-sm text-gray-500">查看沉默机制、身体感、动机驱动与最终回复预算。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="实时回复决策"
        title={replyPostureLabel === '—' ? '未形成稳定姿态' : replyPostureLabel}
        description={compactText(runtimeSummary.headline || '当前还没有生成完整的回复决策摘要。', 200)}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={[
          `意愿：${willingnessLabel}`,
          `允许追问：${responsePolicy.allow_followup === false ? '否' : '是'}`,
          `主导动机：${dominantMotiveLabel}`,
        ]}
      />

      {error ? <NoticeBanner variant="error" title="回复策略加载失败">{error}</NoticeBanner> : null}

      {runtimeSnapshot ? <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} compact showDetailLink={false} /> : null}

      {!runtimeSnapshot && !loading && !error ? (
        <EmptyStateCard
          title="暂无回复策略快照"
          hint="等待一轮完整对话采样后，这里会自动显示。"
          className="p-6"
        />
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          label="回复意愿"
          value={`${formatNumber(silenceState.response_willingness, 2)} / ${willingnessLabel}`}
          hint="数值 + 档位"
          tone="text-blue-600"
        />
        <MetricCard
          label="回复姿态"
          value={replyPostureLabel}
          hint={`字数上限 ${responsePolicy.max_chars ?? '—'}`}
          tone="text-purple-600"
        />
        <MetricCard
          label="主导动机"
          value={dominantMotiveLabel}
          hint={`${motiveReasonTags.length} 个动机标签`}
          tone="text-pink-600"
        />
        <MetricCard
          label="身体负荷"
          value={`${fatigueLabel} / ${drowsinessLabel}`}
          hint={`活跃状态：${formatBoolean(bodySense.is_active)}`}
          tone="text-orange-500"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MoonStar size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">沉默机制</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="回复意愿" value={formatNumber(silenceState.response_willingness, 3)} />
            <KeyValueRow label="意愿档位" value={willingnessLabel} />
            <KeyValueRow
              label="话题抗拒"
              value={silenceState.topic_resistance_active ? `是 · ${silenceState.resistant_topic || '未记录话题'}` : '否'}
            />
            <KeyValueRow label="沉默提示" value={silenceState.silence_hint || '—'} />
            <KeyValueRow label="触发原因" value={silenceState.trigger_reason || '—'} />
            <KeyValueRow label="抗拒提示" value={silenceState.topic_resistance_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <HeartPulse size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">身体感</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="疲惫" value={`${formatNumber(bodySense.fatigue, 2)} / ${fatigueLabel}`} />
            <KeyValueRow label="困倦" value={`${formatNumber(bodySense.drowsiness, 2)} / ${drowsinessLabel}`} />
            <KeyValueRow label="孤独感" value={`${formatNumber(bodySense.loneliness, 2)} / ${bodySense.loneliness_level || '—'}`} />
            <KeyValueRow label="意愿惩罚" value={formatNumber(bodySense.willingness_penalty, 2)} />
            <KeyValueRow label="是否活跃" value={formatBoolean(bodySense.is_active)} />
            <KeyValueRow label="身体提示" value={bodySense.body_sense_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MessagesSquare size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">回复预算</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="字数预算" value={responsePolicy.max_chars != null ? `${responsePolicy.max_chars} 字` : '—'} />
            <KeyValueRow
              label="句数预算"
              value={
                responsePolicy.min_sentences != null || responsePolicy.max_sentences != null
                  ? `${responsePolicy.min_sentences ?? '—'} ~ ${responsePolicy.max_sentences ?? '—'}`
                  : '—'
              }
            />
            <KeyValueRow label="允许追问" value={formatBoolean(responsePolicy.allow_followup)} />
            <KeyValueRow label="允许换题" value={formatBoolean(responsePolicy.allow_topic_shift)} />
            <KeyValueRow label="允许答后主动" value={formatBoolean(responsePolicy.allow_proactive_after_reply)} />
            <KeyValueRow label="是否收敛" value={formatBoolean(responsePolicy.should_be_withdrawn)} />
            <KeyValueRow label="温度下限" value={formatNumber(responsePolicy.warmth_floor, 2)} />
            <KeyValueRow label="亲密上限" value={formatNumber(responsePolicy.intimacy_ceiling, 2)} />
            <KeyValueRow label="幽默上限" value={formatNumber(responsePolicy.humor_ceiling, 2)} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">动机驱动</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="主导动机" value={dominantMotiveLabel} />
            <KeyValueRow label="主动靠近" value={formatNumber(activeDrives.reach_out_drive, 2)} />
            <KeyValueRow label="维持联系" value={formatNumber(activeDrives.continuity_drive, 2)} />
            <KeyValueRow label="抽离退避" value={formatNumber(activeDrives.withdraw_drive, 2)} />
            <KeyValueRow label="自我保护" value={formatNumber(activeDrives.self_protect_drive, 2)} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">策略标签</h2>
          </div>
          <TagList
            items={formattedPolicyReasonTags}
            tone="orange"
            emptyTitle="暂无策略标签"
            emptyHint="等待策略引擎写入标签后再显示。"
          />
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">动机标签</h2>
          </div>
          <TagList
            items={formattedMotiveReasonTags}
            tone="purple"
            emptyTitle="暂无动机标签"
            emptyHint="等待动机引擎补齐标签。"
          />
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">回复理由</h2>
          </div>
          <TagList
            items={formattedWhyReply}
            emptyTitle="暂无回复理由"
            emptyHint="当前快照还没有写入明确的回复理由。"
          />
        </div>
      </div>

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">决策链</h2>
        </div>
        <div className="space-y-2">
          {formattedDecisionChain.length > 0 ? (
            formattedDecisionChain.map((item, index) => (
              <div
                key={`${item}-${index}`}
                className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
              >
                {item}
              </div>
            ))
          ) : (
            <EmptyStateCard
              title="暂无决策链信息"
              hint="当前快照尚未写入完整的决策链。"
              className="p-5"
            />
          )}
        </div>
      </div>
    </div>
  )
}
