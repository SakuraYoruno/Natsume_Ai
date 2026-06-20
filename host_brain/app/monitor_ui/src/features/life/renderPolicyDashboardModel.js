import {
  asArray,
  compactText,
  formatBoolean,
  formatDigitalLifeTag,
  formatDominantMotive,
  formatDrowsinessLevel,
  formatFatigueLevel,
  formatNumber,
  formatReplyPosture,
  formatWillingnessLevel,
} from '../../utils/digitalLife.js'

export const RESPONSE_POLICY_RUNTIME_WINDOW_MINUTES = 60

export function getRenderPolicyRuntimePieces(runtimeSnapshot) {
  return {
    silenceState: runtimeSnapshot?.silence_state || {},
    bodySense: runtimeSnapshot?.body_sense || {},
    motiveState: runtimeSnapshot?.motive_state || {},
    renderPolicy: runtimeSnapshot?.render_policy || {},
    runtimeSummary: runtimeSnapshot?.runtime_summary || {},
  }
}

export function buildRenderPolicyCollections({ runtimeSummary = {}, renderPolicy = {}, motiveState = {} } = {}) {
  return {
    whyReply: asArray(runtimeSummary.why_reply),
    decisionChain: asArray(runtimeSummary.decision_chain),
    policyReasonTags: asArray(renderPolicy.policy_reason_tags),
    motiveReasonTags: asArray(motiveState.motive_reason_tags),
  }
}

export function formatRenderPolicyTags(items = []) {
  return items.map(formatDigitalLifeTag).filter(Boolean)
}

export function buildRenderPolicyLabels({
  silenceState = {},
  bodySense = {},
  motiveState = {},
  runtimeSummary = {},
} = {}) {
  return {
    replyPostureLabel: formatReplyPosture(runtimeSummary.reply_posture?.style),
    willingnessLabel: formatWillingnessLevel(silenceState.willingness_level),
    dominantMotiveLabel: formatDominantMotive(motiveState.dominant_motive),
    fatigueLabel: formatFatigueLevel(bodySense.fatigue_level),
    drowsinessLabel: formatDrowsinessLevel(bodySense.drowsiness_level),
  }
}

export function getRenderPolicyActiveDrives(motiveState = {}) {
  return motiveState.active_drives || motiveState || {}
}

export function buildRenderPolicyHeroView({ runtimeSummary = {}, renderPolicy = {}, labels = {} } = {}) {
  const { replyPostureLabel = '—', willingnessLabel = '—', dominantMotiveLabel = '—' } = labels
  return {
    title: replyPostureLabel === '—' ? '未形成稳定姿态' : replyPostureLabel,
    description: compactText(runtimeSummary.headline || '当前还没有生成完整的回复决策摘要。', 200),
    metaItems: [
      `意愿：${willingnessLabel}`,
      `允许追问：${renderPolicy.allow_followup === false ? '否' : '是'}`,
      `主导动机：${dominantMotiveLabel}`,
    ],
  }
}

export function buildRenderPolicyMetricCards({
  silenceState = {},
  bodySense = {},
  renderPolicy = {},
  labels = {},
  motiveReasonTags = [],
} = {}) {
  return [
    {
      label: '回复意愿',
      value: `${formatNumber(silenceState.response_willingness, 2)} / ${labels.willingnessLabel}`,
      hint: '数值 + 档位',
      tone: 'text-blue-600',
    },
    {
      label: '回复姿态',
      value: labels.replyPostureLabel,
      hint: `字数上限 ${renderPolicy.max_chars ?? '—'}`,
      tone: 'text-purple-600',
    },
    {
      label: '主导动机',
      value: labels.dominantMotiveLabel,
      hint: `${motiveReasonTags.length} 个动机标签`,
      tone: 'text-pink-600',
    },
    {
      label: '身体负荷',
      value: `${labels.fatigueLabel} / ${labels.drowsinessLabel}`,
      hint: `活跃状态：${formatBoolean(bodySense.is_active)}`,
      tone: 'text-orange-500',
    },
  ]
}

export function formatSentenceBudget(renderPolicy = {}) {
  if (renderPolicy.min_sentences != null || renderPolicy.max_sentences != null) {
    return `${renderPolicy.min_sentences ?? '—'} ~ ${renderPolicy.max_sentences ?? '—'}`
  }
  return '—'
}

export function buildRenderPolicyInfoRows({
  silenceState = {},
  bodySense = {},
  renderPolicy = {},
  labels = {},
  activeDrives = {},
} = {}) {
  return {
    silenceRows: [
      { label: '回复意愿', value: formatNumber(silenceState.response_willingness, 3) },
      { label: '意愿档位', value: labels.willingnessLabel },
      {
        label: '话题抗拒',
        value: silenceState.topic_resistance_active
          ? `是 · ${silenceState.resistant_topic || '未记录话题'}`
          : '否',
      },
      { label: '沉默提示', value: silenceState.silence_hint || '—' },
      { label: '触发原因', value: silenceState.trigger_reason || '—' },
      { label: '抗拒提示', value: silenceState.topic_resistance_hint || '—' },
    ],
    bodySenseRows: [
      { label: '疲惫', value: `${formatNumber(bodySense.fatigue, 2)} / ${labels.fatigueLabel}` },
      { label: '困倦', value: `${formatNumber(bodySense.drowsiness, 2)} / ${labels.drowsinessLabel}` },
      {
        label: '孤独感',
        value: `${formatNumber(bodySense.loneliness, 2)} / ${bodySense.loneliness_level || '—'}`,
      },
      { label: '意愿惩罚', value: formatNumber(bodySense.willingness_penalty, 2) },
      { label: '是否活跃', value: formatBoolean(bodySense.is_active) },
      { label: '身体提示', value: bodySense.body_sense_hint || '—' },
    ],
    renderPolicyRows: [
      { label: '字数预算', value: renderPolicy.max_chars != null ? `${renderPolicy.max_chars} 字` : '—' },
      { label: '句数预算', value: formatSentenceBudget(renderPolicy) },
      { label: '允许追问', value: formatBoolean(renderPolicy.allow_followup) },
      { label: '允许换题', value: formatBoolean(renderPolicy.allow_topic_shift) },
      { label: '允许答后主动', value: formatBoolean(renderPolicy.allow_proactive_after_reply) },
      { label: '是否收敛', value: formatBoolean(renderPolicy.should_be_withdrawn) },
      { label: '温度下限', value: formatNumber(renderPolicy.warmth_floor, 2) },
      { label: '亲密上限', value: formatNumber(renderPolicy.intimacy_ceiling, 2) },
      { label: '幽默上限', value: formatNumber(renderPolicy.humor_ceiling, 2) },
    ],
    motiveRows: [
      { label: '主导动机', value: labels.dominantMotiveLabel },
      { label: '主动靠近', value: formatNumber(activeDrives.reach_out_drive, 2) },
      { label: '维持联系', value: formatNumber(activeDrives.continuity_drive, 2) },
      { label: '抽离退避', value: formatNumber(activeDrives.withdraw_drive, 2) },
      { label: '自我保护', value: formatNumber(activeDrives.self_protect_drive, 2) },
    ],
  }
}

export function buildRenderPolicyTagBlocks({ formattedPolicyReasonTags = [], formattedMotiveReasonTags = [], formattedWhyReply = [] } = {}) {
  return [
    {
      title: '策略标签',
      icon: 'shield',
      tone: 'orange',
      items: formattedPolicyReasonTags,
      emptyTitle: '暂无策略标签',
      emptyHint: '等待策略引擎写入标签后再显示。',
    },
    {
      title: '动机标签',
      icon: 'sparkles',
      tone: 'purple',
      items: formattedMotiveReasonTags,
      emptyTitle: '暂无动机标签',
      emptyHint: '等待动机引擎补齐标签。',
    },
    {
      title: '回复理由',
      icon: 'sparkles',
      items: formattedWhyReply,
      emptyTitle: '暂无回复理由',
      emptyHint: '当前快照还没有写入明确的回复理由。',
    },
  ]
}

export function buildRenderPolicyDashboardViewModel({ runtimeSnapshot } = {}) {
  const pieces = getRenderPolicyRuntimePieces(runtimeSnapshot)
  const collections = buildRenderPolicyCollections(pieces)
  const labels = buildRenderPolicyLabels(pieces)
  const activeDrives = getRenderPolicyActiveDrives(pieces.motiveState)
  const formattedWhyReply = formatRenderPolicyTags(collections.whyReply)
  const formattedDecisionChain = formatRenderPolicyTags(collections.decisionChain)
  const formattedPolicyReasonTags = formatRenderPolicyTags(collections.policyReasonTags)
  const formattedMotiveReasonTags = formatRenderPolicyTags(collections.motiveReasonTags)

  return {
    ...pieces,
    ...collections,
    labels,
    activeDrives,
    formattedWhyReply,
    formattedDecisionChain,
    formattedPolicyReasonTags,
    formattedMotiveReasonTags,
    heroView: buildRenderPolicyHeroView({
      runtimeSummary: pieces.runtimeSummary,
      renderPolicy: pieces.renderPolicy,
      labels,
    }),
    metricCards: buildRenderPolicyMetricCards({
      silenceState: pieces.silenceState,
      bodySense: pieces.bodySense,
      renderPolicy: pieces.renderPolicy,
      labels,
      motiveReasonTags: collections.motiveReasonTags,
    }),
    infoRows: buildRenderPolicyInfoRows({
      silenceState: pieces.silenceState,
      bodySense: pieces.bodySense,
      renderPolicy: pieces.renderPolicy,
      labels,
      activeDrives,
    }),
    tagBlocks: buildRenderPolicyTagBlocks({
      formattedPolicyReasonTags,
      formattedMotiveReasonTags,
      formattedWhyReply,
    }),
  }
}
