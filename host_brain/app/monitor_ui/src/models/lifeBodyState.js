import {
  clampPercent,
  formatPercent,
  hasReadableValue,
} from './lifeBodyShared.js'

export function buildLifeState({ hasRuntimeSnapshot, modeMeta, runtimeAlerts, backgroundRuntime, humanoidLoopAlertsData }) {
  if (!hasRuntimeSnapshot) {
    return {
      label: '待连接',
      tone: 'pending',
      summary: '当前没有接到生命体运行快照，主体视图会以中性空态展示。',
    }
  }

  const criticalCount = runtimeAlerts.filter((item) => item.severity === 'critical').length
  const warningCount = runtimeAlerts.filter((item) => item.severity === 'warning').length
  const gatewaySeverity = String(humanoidLoopAlertsData?.severity || '').toLowerCase()
  const notAdvancing = backgroundRuntime && backgroundRuntime.is_advancing === false

  if (criticalCount > 0 || gatewaySeverity === 'critical') {
    return {
      label: '失衡',
      tone: 'critical',
      summary: '当前存在高风险病灶，已经开始影响生命体的回复、节律或执行闭环。',
    }
  }

  if (notAdvancing || warningCount > 0 || gatewaySeverity === 'warning') {
    return {
      label: '紧张',
      tone: 'warning',
      summary: '整体仍在线，但部分器官系统已经出现结构性波动，建议优先查看病灶面板。',
    }
  }

  if (String(modeMeta?.label || '') === '睡眠') {
    return {
      label: '离线演化',
      tone: 'stable',
      summary: '当前以离线推进为主，外显交互收缩，但内在生命流仍在继续演化。',
    }
  }

  return {
    label: '稳定',
    tone: 'stable',
    summary: '生命流当前可以稳定穿过关系、情绪、记忆与执行系统，主体状态清晰可读。',
  }
}

export function buildVitalSigns({
  lifeState,
  hasRuntimeSnapshot,
  currentEmotion,
  currentBondStage,
  bondTrend,
  avgMemorySuccessRate,
  hasMemoryData,
  hasMemorySamples,
  sampledMemorySubsystemCount,
  configuredMemorySubsystemCount,
  riskLevel,
  humanoidLoopData,
  backgroundRuntime,
  nonLiveSlots,
  vtsData,
}) {
  const emotionalStability = vtsData ? clampPercent(100 - Number(vtsData?.degrade_rate || 0)) : null
  const closedLoopRate = clampPercent(humanoidLoopData?.closed_loop_rate)
  const recoveryRate = clampPercent(humanoidLoopData?.deviation_recovery_rate)
  const hasEmotionValue = hasReadableValue(currentEmotion)
  const hasBondStageValue = hasReadableValue(currentBondStage)
  const continuityValue = !hasRuntimeSnapshot
    ? '待判定'
    : !backgroundRuntime
      ? (nonLiveSlots.length === 0 ? '连续' : '波动')
      : backgroundRuntime.is_advancing
        ? (nonLiveSlots.length === 0 ? '连续' : '轻波动')
        : '断续'
  const continuityTone = !hasRuntimeSnapshot
    ? 'pending'
    : backgroundRuntime.is_advancing
      ? (continuityValue === '断续' ? 'critical' : continuityValue === '轻波动' || continuityValue === '波动' ? 'warning' : 'stable')
      : (continuityValue === '断续' ? 'critical' : continuityValue === '轻波动' || continuityValue === '波动' ? 'warning' : 'stable')
  const vitalityHint = !hasRuntimeSnapshot
    ? '等待运行快照恢复'
    : nonLiveSlots.length === 0
      ? '关键槽位在线'
      : `${nonLiveSlots.length} 个槽位异常`
  const emotionValue = !hasRuntimeSnapshot && emotionalStability == null
    ? '待判定'
    : emotionalStability == null
      ? (hasEmotionValue ? currentEmotion : '--')
      : `${Math.round(emotionalStability)}%`
  const bondValue = !hasRuntimeSnapshot && !hasBondStageValue
    ? '待判定'
    : hasBondStageValue
      ? currentBondStage
      : '--'
  const immunityValue = !hasRuntimeSnapshot && String(riskLevel || '') === '—' ? '待判定' : (riskLevel || '--')
  const immunityTone = !hasRuntimeSnapshot && immunityValue === '待判定'
    ? 'pending'
    : ['严重', '高'].includes(String(riskLevel))
      ? 'critical'
      : ['中'].includes(String(riskLevel))
        ? 'warning'
        : 'stable'
  const resilienceValue = recoveryRate == null
    ? hasRuntimeSnapshot
      ? (backgroundRuntime?.is_advancing ? '推进中' : '待恢复')
      : '待判定'
    : formatPercent(recoveryRate)
  const resilienceTone = recoveryRate != null && recoveryRate < 60
    ? 'warning'
    : !hasRuntimeSnapshot
      ? 'pending'
      : backgroundRuntime?.is_advancing === false
        ? 'warning'
        : 'stable'

  return [
    {
      id: 'vitality',
      label: '生命活性',
      organ: '神经 / 节律',
      value: lifeState.label,
      hint: vitalityHint,
      to: '/cockpit',
      tone: lifeState.tone,
    },
    {
      id: 'continuity',
      label: '意识连续性',
      organ: '神经系统',
      value: continuityValue,
      hint: hasRuntimeSnapshot
        ? (backgroundRuntime?.tick_health ? `tick ${backgroundRuntime.tick_health}` : '由运行快照与后台推进联合估计')
        : '等待运行快照恢复后判断',
      to: '/observability/trace',
      tone: continuityTone,
    },
    {
      id: 'emotion',
      label: '情绪稳定度',
      organ: '边缘系统',
      value: emotionValue,
      hint: hasEmotionValue ? `当前情绪 ${currentEmotion}` : '等待情绪样本',
      to: '/life/emotion',
      tone: !hasRuntimeSnapshot && emotionalStability == null ? 'pending' : emotionalStability != null && emotionalStability < 70 ? 'warning' : 'stable',
    },
    {
      id: 'bond',
      label: '关系温度',
      organ: '边缘系统',
      value: bondValue,
      hint: bondTrend && bondTrend !== '—' ? `趋势 ${bondTrend}` : '等待关系趋势',
      to: '/life/relationship',
      tone: !hasRuntimeSnapshot && !hasBondStageValue ? 'pending' : hasBondStageValue && currentBondStage !== '陌生' ? 'stable' : 'pending',
    },
    {
      id: 'memory',
      label: '记忆可用度',
      organ: '记忆系统',
      value: !hasMemoryData ? '暂不可用' : hasMemorySamples ? `${avgMemorySuccessRate}%` : '无样本',
      hint: !hasMemoryData ? '记忆统计暂不可用' : `${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统有样本`,
      to: '/life/memory',
      tone: !hasMemoryData ? 'pending' : hasMemorySamples && avgMemorySuccessRate < 70 ? 'warning' : hasMemorySamples ? 'stable' : 'pending',
    },
    {
      id: 'immune',
      label: '免疫压力',
      organ: '免疫系统',
      value: immunityValue,
      hint: immunityValue === '待判定' ? '等待风险样本' : '由叙事护栏、风险标签和提示构建风险共同反映',
      to: '/observability/alerts',
      tone: immunityTone,
    },
    {
      id: 'execution',
      label: '执行闭环率',
      organ: '运动系统',
      value: closedLoopRate == null ? '无样本' : formatPercent(closedLoopRate),
      hint: humanoidLoopData ? `ACK ${formatPercent(humanoidLoopData?.ack_touch_rate)} · Progress ${formatPercent(humanoidLoopData?.progress_touch_rate)}` : '等待 Gateway 请求样本',
      to: '/execution/gateway',
      tone: closedLoopRate != null && closedLoopRate < 70 ? 'warning' : closedLoopRate != null ? 'stable' : 'pending',
    },
    {
      id: 'resilience',
      label: '恢复力',
      organ: '节律 / 运动',
      value: resilienceValue,
      hint: recoveryRate == null
        ? hasRuntimeSnapshot
          ? '由偏航恢复率或后台推进节律近似表达'
          : '等待节律或执行样本'
        : `偏航恢复 ${formatPercent(recoveryRate)}`,
      to: '/execution/automation',
      tone: resilienceTone,
    },
  ]
}
