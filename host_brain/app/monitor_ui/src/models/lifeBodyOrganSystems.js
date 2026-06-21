import { createRequestRefs } from './observabilityRefs.js'
import {
  formatPercent,
  hasReadableValue,
  ORGAN_META,
  pickStatus,
  STATUS_RANK,
} from './lifeBodyShared.js'

export function buildOrganSystems({
  runtimeUpdatedAt,
  overviewUpdatedAt,
  hasRuntimeSnapshot,
  modeMeta,
  runtimeSnapshot,
  servicesData,
  nonLiveSlots,
  currentEmotion,
  currentBondStage,
  bondTrend,
  replyPosture,
  hasMemoryData,
  hasMemorySamples,
  avgMemorySuccessRate,
  sampledMemorySubsystemCount,
  configuredMemorySubsystemCount,
  promptStats,
  riskLevel,
  growthSignals,
  capabilityGapData,
  humanoidLoopData,
  humanoidLoopAlertsData,
  humanoidLoopRemediationData,
  runtimeAlerts,
}) {
  const backgroundRuntime = runtimeSnapshot?.background_runtime || {}
  const nervousRefs = createRequestRefs(humanoidLoopRemediationData?.representative_requests || []).slice(0, 2)
  const injectedCount = Number(promptStats?.injection_detected_count || 0)
  const promptHighRiskCount = Number(promptStats?.high_risk_count || 0)
  const forgeSummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeTracked = Number(forgeSummary?.tracked_proposals || 0)
  const forgeCritical = Number(forgeSummary?.critical_proposals || 0)
  const forgeUnhealthy = Number(forgeSummary?.unhealthy_proposals || 0)
  const gatewaySeverity = String(humanoidLoopAlertsData?.severity || '').toLowerCase()
  const gatewayStatus = String(humanoidLoopAlertsData?.status || '').toLowerCase()
  const gatewaySamples = Number(humanoidLoopData?.total_gateway_requests || 0)
  const hasEmotionValue = hasReadableValue(currentEmotion)
  const hasBondStageValue = hasReadableValue(currentBondStage)
  const hasReplyPostureValue = hasReadableValue(replyPosture)

  return [
    {
      id: 'nervous',
      name: ORGAN_META.nervous.name,
      status: pickStatus({
        hasData: hasRuntimeSnapshot || Boolean(servicesData),
        critical: nonLiveSlots.length >= 4 || Number(servicesData?.down || 0) > 0,
        warning: nonLiveSlots.length > 0 || Number(servicesData?.degraded || 0) > 0,
      }),
      freshness: runtimeUpdatedAt || overviewUpdatedAt,
      trend: modeMeta?.label || '--',
      riskLevel: nonLiveSlots.length > 0 ? '局部离线' : '低',
      summary: hasRuntimeSnapshot
        ? `当前意识链处于${modeMeta?.label || '运行中'}，回复姿态 ${replyPosture || '--'}。`
        : '等待运行快照恢复后，再判断意识链连续性。',
      symptoms: [
        hasRuntimeSnapshot
          ? (nonLiveSlots.length > 0 ? `${nonLiveSlots.length} 个关键槽位未在线` : '关键槽位保持在线')
          : '等待运行快照恢复',
        servicesData ? `底座降级 ${servicesData.degraded ?? 0} · 离线 ${servicesData.down ?? 0}` : '神经底座状态待连通',
      ],
      downstreamImpacts: [
        nonLiveSlots.length > 0 ? '主链路可解释性下降，建议继续沿 Trace 回放。' : '当前主链路可以直接下钻到 Trace 与 Session。',
      ],
      representativeRefs: nervousRefs,
      drilldownPath: ORGAN_META.nervous.path,
      drilldownLabel: ORGAN_META.nervous.drilldownLabel,
    },
    {
      id: 'limbic',
      name: ORGAN_META.limbic.name,
      status: pickStatus({
        hasData: hasRuntimeSnapshot || hasEmotionValue || hasBondStageValue,
        critical: runtimeAlerts.some((item) => item.reason_code === 'TOPIC_RESISTANCE_ACTIVE'),
        warning: replyPosture === '收敛' || replyPosture === '谨慎' || !hasEmotionValue,
      }),
      freshness: runtimeUpdatedAt || overviewUpdatedAt,
      trend: bondTrend || '--',
      riskLevel: hasReplyPostureValue ? replyPosture : '--',
      summary: hasEmotionValue || hasBondStageValue
        ? `当前情绪 ${currentEmotion || '--'}，关系阶段 ${currentBondStage || '--'}。`
        : '等待情绪与关系样本聚合。',
      symptoms: [
        hasEmotionValue ? `情绪波形：${currentEmotion}` : '情绪波形待采样',
        hasBondStageValue ? `关系温度：${currentBondStage}${bondTrend && bondTrend !== '—' ? ` · ${bondTrend}` : ''}` : '关系温度待聚合',
      ],
      downstreamImpacts: [
        `当前回复姿态 ${hasReplyPostureValue ? replyPosture : '--'}，会直接影响陪伴感与亲密度表达。`,
      ],
      representativeRefs: [],
      drilldownPath: '/life/relationship',
      drilldownLabel: ORGAN_META.limbic.drilldownLabel,
    },
    {
      id: 'memory',
      name: ORGAN_META.memory.name,
      status: pickStatus({
        hasData: hasMemoryData,
        critical: hasMemorySamples && avgMemorySuccessRate < 50,
        warning: !hasMemorySamples || (hasMemorySamples && avgMemorySuccessRate < 75),
      }),
      freshness: overviewUpdatedAt,
      trend: hasMemorySamples ? '有样本' : '待采样',
      riskLevel: hasMemorySamples ? `${avgMemorySuccessRate}%` : '无样本',
      summary: !hasMemoryData
        ? '记忆统计暂不可用。'
        : hasMemorySamples
          ? `平均成功率 ${avgMemorySuccessRate}% ，已有 ${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统产生样本。`
          : '当前窗口还没有足够的记忆流量样本。',
      symptoms: [
        hasMemorySamples ? `${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统已采到样本` : '当前窗口没有样本',
        hasMemorySamples ? `平均成功率 ${avgMemorySuccessRate}%` : '召回、蒸馏与写入链路等待实际流量',
      ],
      downstreamImpacts: [
        hasMemorySamples ? '可继续下钻查看五路召回、子系统成功率与守卫统计。' : '当前更像“未观察到”，而不是“记忆失败”。',
      ],
      representativeRefs: [],
      drilldownPath: ORGAN_META.memory.path,
      drilldownLabel: ORGAN_META.memory.drilldownLabel,
    },
    {
      id: 'endocrine',
      name: ORGAN_META.endocrine.name,
      status: pickStatus({
        hasData: hasRuntimeSnapshot && Object.keys(backgroundRuntime || {}).length > 0,
        critical: backgroundRuntime?.persona_user_alignment_ok === false,
        warning: backgroundRuntime?.is_advancing === false || (backgroundRuntime?.core_ticks_missing || []).length > 0,
      }),
      freshness: runtimeUpdatedAt || overviewUpdatedAt,
      trend: String(backgroundRuntime?.tick_health || '--'),
      riskLevel: backgroundRuntime?.is_advancing === false ? '推进异常' : '低',
      summary: !hasRuntimeSnapshot
        ? '等待运行快照恢复后，再判断节律与内在推进。'
        : backgroundRuntime?.is_advancing
          ? '后台推进仍在继续，节律中枢当前可维持内在世界演化。'
          : '后台推进未达预期，节律中枢已经开始影响生命体连续性。',
      symptoms: [
        hasRuntimeSnapshot
          ? `scheduler ${String(backgroundRuntime?.scheduler_status || '--').toUpperCase()} · tick ${String(backgroundRuntime?.tick_health || '--')}`
          : '等待节律快照恢复',
        hasRuntimeSnapshot
          ? ((backgroundRuntime?.core_ticks_missing || []).length > 0
            ? `缺失 ${backgroundRuntime.core_ticks_missing.join('、')}`
            : '核心 Tick 已注册')
          : '核心 Tick 状态待判定',
      ],
      downstreamImpacts: [
        backgroundRuntime?.is_advancing === false
          ? '会拖慢内在世界、沉默机制与体感推进。'
          : '当前节律能为情绪漂移、恢复与内在演化提供底座。',
      ],
      representativeRefs: [],
      drilldownPath: ORGAN_META.endocrine.path,
      drilldownLabel: ORGAN_META.endocrine.drilldownLabel,
    },
    {
      id: 'immune',
      name: ORGAN_META.immune.name,
      status: pickStatus({
        hasData: hasRuntimeSnapshot || injectedCount > 0 || promptHighRiskCount > 0,
        critical: ['高', '严重'].includes(String(riskLevel)) || injectedCount > 0,
        warning: String(riskLevel) === '中' || promptHighRiskCount > 0,
      }),
      freshness: runtimeUpdatedAt || overviewUpdatedAt,
      trend: riskLevel || '--',
      riskLevel: riskLevel || '--',
      summary: ['高', '严重'].includes(String(riskLevel))
        ? '叙事护栏和风险收束已经进入高压态，建议优先检查免疫与门控。'
        : '当前免疫压力总体可控，但仍应留意提示构建与叙事护栏波动。',
      symptoms: [
        injectedCount > 0 ? `提示注入 ${injectedCount} 次` : hasRuntimeSnapshot ? '当前窗口未见提示注入' : '等待免疫样本',
        promptHighRiskCount > 0 ? `高风险 Prompt 构建 ${promptHighRiskCount} 次` : `叙事护栏风险 ${riskLevel || '--'}`,
      ],
      downstreamImpacts: [
        '会直接影响是否放行回复、是否收敛输出和是否触发保护姿态。',
      ],
      representativeRefs: [],
      drilldownPath: ORGAN_META.immune.path,
      drilldownLabel: ORGAN_META.immune.drilldownLabel,
    },
    {
      id: 'motor',
      name: ORGAN_META.motor.name,
      status: pickStatus({
        hasData: gatewaySamples > 0,
        critical: gatewaySeverity === 'critical',
        warning: gatewaySeverity === 'warning' || gatewayStatus === 'degraded' || (gatewaySamples > 0 && Number(humanoidLoopData?.closed_loop_rate || 0) < 70),
      }),
      freshness: overviewUpdatedAt,
      trend: gatewayStatus || '--',
      riskLevel: gatewaySamples > 0 ? formatPercent(humanoidLoopData?.closed_loop_rate) : '无样本',
      summary: gatewaySamples > 0
        ? `最近窗口共有 ${gatewaySamples} 条 Gateway 请求，闭环率 ${formatPercent(humanoidLoopData?.closed_loop_rate)}。`
        : '等待真实 Gateway 请求进入后，再判断运动系统执行质量。',
      symptoms: [
        gatewaySamples > 0
          ? `ACK ${formatPercent(humanoidLoopData?.ack_touch_rate)} · Progress ${formatPercent(humanoidLoopData?.progress_touch_rate)}`
          : '当前窗口尚未形成执行样本',
        gatewaySamples > 0
          ? `偏航恢复 ${formatPercent(humanoidLoopData?.deviation_recovery_rate)}`
          : '等待闭环链路采样',
      ],
      downstreamImpacts: [
        gatewaySamples > 0
          ? '直接影响用户是否感知到系统已经开始行动、持续推进并最终收束。'
          : '当前看不到运动系统真实负载，判断应保持中性。',
      ],
      representativeRefs: createRequestRefs(humanoidLoopRemediationData?.representative_requests || []).slice(0, 2),
      drilldownPath: ORGAN_META.motor.path,
      drilldownLabel: ORGAN_META.motor.drilldownLabel,
    },
    {
      id: 'self',
      name: ORGAN_META.self.name,
      status: pickStatus({
        hasData: Boolean(growthSignals?.hasAnyGrowth) || forgeTracked > 0 || hasRuntimeSnapshot,
        critical: forgeCritical > 0,
        warning: forgeUnhealthy > 0 || !growthSignals?.hasAnyGrowth,
      }),
      freshness: runtimeUpdatedAt || overviewUpdatedAt,
      trend: growthSignals?.lastCycleLabel || '--',
      riskLevel: forgeTracked > 0 ? `forge ${forgeCritical}/${forgeTracked}` : '低',
      summary: growthSignals?.hasAnyGrowth
        ? growthSignals.primarySummary || growthSignals.overviewSummary || '自我模型已有连续成长样本。'
        : '自我系统已在线，但当前还在等待更稳定的成长样本。',
      symptoms: [
        growthSignals?.hasAnyGrowth ? `成长周期 ${growthSignals.cycleCount || 0}` : '成长样本待形成',
        forgeTracked > 0 ? `forge critical ${forgeCritical} · unhealthy ${forgeUnhealthy}` : '当前窗口没有 Forge runtime 样本',
      ],
      downstreamImpacts: [
        growthSignals?.hasAnyGrowth
          ? '会影响长期身份叙事、风格稳定度和主设定演化。'
          : '目前可以下钻查看自我模型、人格文案与主设定是否一致。',
      ],
      representativeRefs: [],
      drilldownPath: ORGAN_META.self.path,
      drilldownLabel: ORGAN_META.self.drilldownLabel,
    },
  ].sort((left, right) => (STATUS_RANK[left.status] ?? 99) - (STATUS_RANK[right.status] ?? 99))
}
