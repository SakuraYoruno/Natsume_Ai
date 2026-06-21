import {
  LOAD_STATES,
  ORGAN_STATUS,
  clampPercent,
  createOrganSystemModel,
  metric,
  toFixed,
} from './organSystemCore.js'

export function buildLimbicOrganSystemModel({
  loadState,
  updatedAt,
  moodLabel,
  moodKey,
  emotionTagLabel,
  bondStageLabel,
  relationshipTemperature,
  bondEventCount,
  gateLabel,
  gateState,
  degradeRate,
  applyRate,
  talkGateAllowed,
  sampleCount,
  cortisolLevel,
  emotionalStability,
}) {
  const noData = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
  const noSample = loadState === LOAD_STATES.NO_SAMPLE
  const status = noData
    ? ORGAN_STATUS.PENDING
    : gateState === 'protective' || moodKey === 'critical'
      ? ORGAN_STATUS.CRITICAL
      : gateState === 'cautious' || Number(degradeRate || 0) > 15 || moodKey === 'tired'
        ? ORGAN_STATUS.WARNING
        : noSample
          ? ORGAN_STATUS.PENDING
          : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'limbic',
    name: '边缘系统',
    status,
    loadState,
    freshness: updatedAt,
    trend: moodLabel || '--',
    riskLevel: gateLabel || '--',
    summary: noData
      ? '等待情绪快照与表情聚合恢复后，再判断边缘系统是否稳定。'
      : `当前主情绪 ${emotionTagLabel || '--'}，关系阶段 ${bondStageLabel || '--'}，门控处于 ${gateLabel || '--'}。`,
    symptoms: [
      sampleCount > 0 ? `近窗口累计 ${sampleCount} 个 VTS 情绪样本` : '当前窗口暂无足够表情样本',
      `关系温度 ${(Math.max(0, Math.min(1, Number(relationshipTemperature || 0))) * 100).toFixed(0)}° · 事件 ${bondEventCount || 0} 次`,
      `表情应用率 ${clampPercent(applyRate)} · 降级率 ${clampPercent(degradeRate)}`,
    ],
    downstreamImpacts: [
      `对话放行 ${talkGateAllowed ?? 0} 次，边缘门控会直接改变回复姿态与亲密度表达。`,
      `cortisol ${toFixed(cortisolLevel)} · 情绪稳定度 ${emotionalStability == null ? '待判定' : clampPercent(emotionalStability)}`,
    ],
    metrics: [
      metric('当前情绪', emotionTagLabel || '待判定', moodLabel || '等待运行快照', 'text-pink-600'),
      metric('关系阶段', bondStageLabel || '待判定', `事件 ${bondEventCount || 0} 次`, 'text-purple-600'),
      metric('门控状态', gateLabel || '待判定', `降级率 ${clampPercent(degradeRate)}`, gateState === 'protective' ? 'text-red-600' : gateState === 'cautious' ? 'text-orange-500' : 'text-green-600'),
      metric('表情样本', String(sampleCount || 0), `放行 ${talkGateAllowed || 0}`, 'text-blue-600'),
    ],
    drilldownPath: '/life/relationship',
    drilldownLabel: '查看关系与回复',
  })
}

export function buildMemoryOrganSystemModel({
  loadState,
  updatedAt,
  hasData,
  hasSamples,
  averageSuccessRate,
  sampledSubsystemCount,
  configuredSubsystemCount,
  residentDocCount,
  promptContextAvailable,
  distillCycles,
  episodicWrites,
}) {
  const status = !hasData || loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : hasSamples && Number(averageSuccessRate || 0) < 50
      ? ORGAN_STATUS.CRITICAL
      : !hasSamples || loadState === LOAD_STATES.NO_SAMPLE || Number(averageSuccessRate || 0) < 75
        ? ORGAN_STATUS.WARNING
        : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'memory',
    name: '记忆系统',
    status,
    loadState,
    freshness: updatedAt,
    trend: hasSamples ? '有样本' : '待采样',
    riskLevel: hasData ? (hasSamples ? `${averageSuccessRate}%` : '无样本') : '暂不可用',
    summary: !hasData
      ? '记忆链路当前没有返回可用统计，页面会保留中性空态。'
      : hasSamples
        ? `近窗口平均成功率 ${averageSuccessRate}% ，已有 ${sampledSubsystemCount}/${configuredSubsystemCount} 个子系统产出样本。`
        : '当前窗口还没有足够的记忆流量样本，记忆系统仍在待采样阶段。',
    symptoms: [
      hasSamples ? `${sampledSubsystemCount}/${configuredSubsystemCount} 个子系统已采样` : '当前窗口暂无子系统样本',
      `五路召回 · 常驻设定 ${residentDocCount || 0} 份`,
      `情景写入 ${episodicWrites || 0} · 蒸馏循环 ${distillCycles || 0}`,
    ],
    downstreamImpacts: [
      promptContextAvailable
        ? '最近一次对话样本已经进入 prompt context，可继续追人格与风格注入。'
        : '如果 prompt context 还未形成，回复会更依赖静态设定而不是动态记忆证据。',
      '记忆系统会直接影响召回注入、人格延续和长期陪伴一致性。',
    ],
    metrics: [
      metric('记忆成功率', hasSamples ? `${averageSuccessRate}%` : '无样本', `${sampledSubsystemCount}/${configuredSubsystemCount} 子系统`, hasSamples && Number(averageSuccessRate || 0) < 75 ? 'text-orange-500' : 'text-emerald-600'),
      metric('五路召回', '5', '固定检索骨架', 'text-blue-600'),
      metric('常驻设定', String(residentDocCount || 0), promptContextAvailable ? '已进入 prompt context' : '等待动态上下文', 'text-indigo-600'),
      metric('蒸馏/写入', `${distillCycles || 0}/${episodicWrites || 0}`, '蒸馏循环 / 情景写入', 'text-purple-600'),
    ],
    drilldownPath: '/operations/canon',
    drilldownLabel: '查看设定与常驻',
  })
}

export function buildBondOrganSystemModel({
  loadState,
  updatedAt,
  healthStatus,
  selectedUser,
  userCount,
  stageSampleCount,
  currentStageLabel,
  trendLabel,
  totalScore,
  eventCount,
  capabilityHint,
  lastEventLabel,
}) {
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : healthStatus === '离线'
      ? ORGAN_STATUS.CRITICAL
      : healthStatus === '降级' || trendLabel === '下降'
        ? ORGAN_STATUS.WARNING
        : loadState === LOAD_STATES.NO_SAMPLE
          ? ORGAN_STATUS.PENDING
          : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'limbic',
    name: '边缘系统 / 社交脑',
    status,
    loadState,
    freshness: updatedAt,
    trend: trendLabel || '--',
    riskLevel: healthStatus || '--',
    summary: selectedUser
      ? `当前正在观察 ${selectedUser} 的关系快照，阶段 ${currentStageLabel || '--'}。`
      : '当前还没有锁定具体用户，社交脑处于全局关系分布观察态。',
    symptoms: [
      `用户数 ${userCount || 0} · 阶段样本 ${stageSampleCount || 0}`,
      `当前阶段 ${currentStageLabel || '--'} · 趋势 ${trendLabel || '--'}`,
      `累计积分 ${totalScore ?? '—'} · 事件 ${eventCount || 0} 次`,
    ],
    downstreamImpacts: [
      capabilityHint ? `能力提示：${capabilityHint}` : '能力提示待用户详情返回后判定。',
      lastEventLabel && lastEventLabel !== '—'
        ? `最近关系事件发生在 ${lastEventLabel}，会直接影响陪伴感与互动亲密度。`
        : '关系事件越完整，边缘系统对回复姿态的判断会越稳定。',
    ],
    metrics: [
      metric('引擎状态', healthStatus || '待判定', `用户 ${userCount || 0}`, healthStatus === '正常' ? 'text-emerald-600' : 'text-orange-500'),
      metric('当前阶段', currentStageLabel || '待判定', `趋势 ${trendLabel || '--'}`, 'text-pink-600'),
      metric('累计积分', totalScore ?? '—', `事件 ${eventCount || 0} 次`, 'text-purple-600'),
      metric('关系样本', String(stageSampleCount || 0), selectedUser || '未锁定用户', 'text-blue-600'),
    ],
    drilldownPath: '/life/emotion',
    drilldownLabel: '返回边缘系统总览',
  })
}

export function buildRuntimeModeOrganSystemModel({
  loadState,
  updatedAt,
  currentModeLabel,
  currentModeKey,
  schedulerLabel,
  idleMinutes,
  validTransitionCount,
  enabledRuleCount,
  totalRuleCount,
  isNightTime,
  degraded,
}) {
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : currentModeKey === 'locked'
      ? ORGAN_STATUS.CRITICAL
      : degraded || currentModeKey === 'maintenance'
        ? ORGAN_STATUS.WARNING
        : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'endocrine',
    name: '内分泌系统 / 存在态轴',
    status,
    loadState,
    freshness: updatedAt,
    trend: currentModeLabel || '--',
    riskLevel: degraded ? '降级' : currentModeKey === 'locked' ? '锁定' : '低',
    summary: `当前生命体存在态轴处于 ${currentModeLabel || '--'}，它负责最外层硬状态切换，不直接等于完整人格状态。`,
    symptoms: [
      `空闲 ${idleMinutes ?? 0} 分钟 · 可切换 ${validTransitionCount || 0} 项`,
      `自动规则 ${enabledRuleCount || 0}/${totalRuleCount || 0} · ${isNightTime ? '夜间窗口' : '日间窗口'}`,
      `调度优先级 ${schedulerLabel || '--'}${degraded ? ' · 当前降级' : ''}`,
    ],
    downstreamImpacts: [
      '存在态轴会直接改变主动打扰强度、允许的后台调度范围和睡眠/唤醒节奏。',
      currentModeKey === 'sleep'
        ? '当前更偏向离线内在演化。'
        : currentModeKey === 'focus'
          ? '当前会主动压低打扰并收缩非必要行为。'
          : '当前更偏向完整在线与常规陪伴。',
    ],
    metrics: [
      metric('当前模式', currentModeLabel || '待判定', schedulerLabel || '—', 'text-primary'),
      metric('空闲时间', `${idleMinutes ?? 0} 分钟`, isNightTime ? '夜间窗口' : '日间窗口', 'text-blue-600'),
      metric('可切换', String(validTransitionCount || 0), '状态机允许项', 'text-indigo-600'),
      metric('自动规则', `${enabledRuleCount || 0}/${totalRuleCount || 0}`, degraded ? '当前降级' : '当前正常', degraded ? 'text-orange-500' : 'text-green-600'),
    ],
    drilldownPath: '/execution/automation',
    drilldownLabel: '查看节律执行台',
  })
}

export function buildSelfOrganSystemModel({
  loadState,
  updatedAt,
  recentMoodTrendLabel,
  riskLevelLabel,
  riskLevelRaw,
  growthHeadline,
  sharedMemoryCount,
  milestoneCount,
  growthCycleCount,
  activeThemeCount,
  personalityChangeCount,
  promptDirectiveCount,
  bondStageLabel,
  allowedStyleLabel,
}) {
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : ['high', 'critical'].includes(String(riskLevelRaw || '').toLowerCase())
      ? ORGAN_STATUS.CRITICAL
      : String(riskLevelRaw || '').toLowerCase() === 'medium'
        ? ORGAN_STATUS.WARNING
        : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'self',
    name: '自我系统',
    status,
    loadState,
    freshness: updatedAt,
    trend: recentMoodTrendLabel || '--',
    riskLevel: riskLevelLabel || '--',
    summary: growthHeadline || '当前暂无稳定的自我成长摘要。',
    symptoms: [
      `共享记忆 ${sharedMemoryCount || 0} · 成长里程碑 ${milestoneCount || 0} · 成长周期 ${growthCycleCount || 0}`,
      `活跃主题 ${activeThemeCount || 0} · 人格变化 ${personalityChangeCount || 0}`,
      `关系阶段 ${bondStageLabel || '--'} · 允许风格 ${allowedStyleLabel || '--'}`,
    ],
    downstreamImpacts: [
      `当前护栏风险 ${riskLevelLabel || '--'}，会直接影响自叙事扩展与人格稳定度。`,
      promptDirectiveCount > 0
        ? `当前存在 ${promptDirectiveCount} 条提示指令，会继续塑造输出风格与边界。`
        : '当前没有额外提示指令，自我系统更多依赖记忆和成长脉冲继续成形。',
    ],
    metrics: [
      metric('情绪趋势', recentMoodTrendLabel || '待判定', `阶段 ${bondStageLabel || '--'}`, 'text-blue-600'),
      metric('共享记忆', String(sharedMemoryCount || 0), `里程碑 ${milestoneCount || 0}`, 'text-pink-600'),
      metric('成长周期', String(growthCycleCount || 0), `主题 ${activeThemeCount || 0}`, 'text-fuchsia-600'),
      metric('护栏风险', riskLevelLabel || '--', `指令 ${promptDirectiveCount || 0}`, ['high', 'critical'].includes(String(riskLevelRaw || '').toLowerCase()) ? 'text-red-600' : String(riskLevelRaw || '').toLowerCase() === 'medium' ? 'text-orange-500' : 'text-green-600'),
    ],
    drilldownPath: '/operations/canon',
    drilldownLabel: '查看主设定与自我叙事',
  })
}
