import {
  buildPathologyDetailPath,
  formatPercent,
  ORGAN_META,
} from './lifeBodyShared.js'

export function buildTimelineEvents({
  overviewUpdatedAt,
  runtimeSnapshot,
  runtimeUpdatedAt,
  currentEmotion,
  currentBondStage,
  bondTrend,
  hasMemoryData,
  hasMemorySamples,
  avgMemorySuccessRate,
  sampledMemorySubsystemCount,
  configuredMemorySubsystemCount,
  backgroundRuntime,
  humanoidLoopData,
  humanoidLoopHotspotsData,
  pathologies,
  growthSignals,
}) {
  const events = []
  const hasRuntimeSnapshot = Boolean(runtimeSnapshot)
  const runtimeTs = Number(runtimeSnapshot?.timestamp || runtimeUpdatedAt || 0)
  const gatewayTs = Number(humanoidLoopHotspotsData?.timestamp || overviewUpdatedAt || runtimeTs || 0)
  const memoryTs = Number(overviewUpdatedAt || runtimeTs || 0)

  if (hasRuntimeSnapshot && runtimeTs > 0) {
    events.push({
      id: 'runtime-refresh',
      type: 'snapshot',
      typeLabel: '快照',
      organId: 'nervous',
      organLabel: ORGAN_META.nervous.name,
      severity: 'info',
      timestamp: runtimeTs,
      title: '生命体快照刷新',
      detail: runtimeSnapshot?.runtime_summary?.headline || '运行快照已更新，可继续沿器官系统与病灶排查。',
      to: '/cockpit',
      linkLabel: '查看总览',
    })
  }

  if (hasRuntimeSnapshot && currentEmotion && currentEmotion !== '—') {
    events.push({
      id: 'emotion-state',
      type: 'emotion',
      typeLabel: '情绪',
      organId: 'limbic',
      organLabel: ORGAN_META.limbic.name,
      severity: 'info',
      timestamp: runtimeTs,
      title: '情绪波形采样',
      detail: `当前情绪 ${currentEmotion}，已回流到驾驶舱的生命体征判断。`,
      to: '/life/emotion',
      linkLabel: '查看情绪',
    })
  }

  if (hasRuntimeSnapshot && currentBondStage && currentBondStage !== '—') {
    events.push({
      id: 'bond-state',
      type: 'bond',
      typeLabel: '关系',
      organId: 'limbic',
      organLabel: ORGAN_META.limbic.name,
      severity: 'info',
      timestamp: runtimeTs,
      title: '关系温度更新',
      detail: bondTrend ? `${currentBondStage} · 趋势 ${bondTrend}` : `${currentBondStage} · 等待趋势聚合`,
      to: '/life/relationship',
      linkLabel: '查看关系',
    })
  }

  if (hasMemoryData) {
    events.push({
      id: 'memory-window',
      type: 'memory',
      typeLabel: '记忆',
      organId: 'memory',
      organLabel: ORGAN_META.memory.name,
      severity: hasMemorySamples ? 'info' : 'warning',
      timestamp: memoryTs,
      title: hasMemorySamples ? '记忆窗口已采样' : '记忆系统待采样',
      detail: hasMemorySamples
        ? `平均成功率 ${avgMemorySuccessRate}% · ${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统有样本`
        : '当前窗口暂无足够样本，记忆系统仍需更多实际流量。',
      to: '/life/memory',
      linkLabel: '查看记忆',
    })
  }

  if (backgroundRuntime && Object.keys(backgroundRuntime).length > 0) {
    events.push({
      id: 'rhythm',
      type: 'tick',
      typeLabel: '节律',
      organId: 'endocrine',
      organLabel: ORGAN_META.endocrine.name,
      severity: backgroundRuntime.is_advancing ? 'info' : 'warning',
      timestamp: runtimeTs,
      title: backgroundRuntime.is_advancing ? '后台推进继续前进' : '后台推进暂停',
      detail: `scheduler ${String(backgroundRuntime.scheduler_status || '--').toUpperCase()} · tick ${String(backgroundRuntime.tick_health || '--')}`,
      to: '/execution/automation',
      linkLabel: '查看节律',
    })
  }

  if (Number(humanoidLoopData?.total_gateway_requests || 0) > 0) {
    events.push({
      id: 'gateway-loop',
      type: 'execution',
      typeLabel: '执行',
      organId: 'motor',
      organLabel: ORGAN_META.motor.name,
      severity: Number(humanoidLoopData?.closed_loop_rate || 0) < 70 ? 'warning' : 'info',
      timestamp: gatewayTs,
      title: '执行闭环更新',
      detail: `闭环 ${formatPercent(humanoidLoopData?.closed_loop_rate)} · ACK ${formatPercent(humanoidLoopData?.ack_touch_rate)} · 偏航恢复 ${formatPercent(humanoidLoopData?.deviation_recovery_rate)}`,
      to: '/execution/gateway',
      linkLabel: '查看闭环',
    })
  }

  if (pathologies[0]) {
    events.push({
      id: 'top-pathology',
      type: 'pathology',
      typeLabel: '病灶',
      organId: pathologies[0].organId,
      organLabel: pathologies[0].organLabel,
      severity: pathologies[0].severity,
      pathologyId: pathologies[0].id,
      representativeRefs: pathologies[0].representativeRefs?.slice(0, 2) || [],
      timestamp: gatewayTs || runtimeTs,
      title: '主病灶升温',
      detail: `${pathologies[0].organLabel} · ${pathologies[0].title}`,
      to: pathologies[0].detailPath || buildPathologyDetailPath(pathologies[0].id),
      linkLabel: '查看病灶详情',
      secondaryTo: pathologies[0].recommendedPath,
      secondaryLinkLabel: pathologies[0].recommendedLabel,
    })
  }

  if (growthSignals?.hasAnyGrowth) {
    events.push({
      id: 'self-growth',
      type: 'self',
      typeLabel: '自我',
      organId: 'self',
      organLabel: ORGAN_META.self.name,
      severity: 'info',
      timestamp: runtimeTs || memoryTs,
      title: '自我系统继续成形',
      detail: growthSignals.primarySummary || growthSignals.overviewSummary || '已形成新的成长样本。',
      to: '/life/self-model',
      linkLabel: '查看自我模型',
    })
  }

  return events
    .filter((item) => item.timestamp > 0)
    .sort((left, right) => right.timestamp - left.timestamp)
    .slice(0, 6)
}
