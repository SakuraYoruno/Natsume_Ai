const EXPECTED_ORGAN_COUNT = 23

function numeric(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function percent(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return null
  return Math.max(0, Math.min(100, parsed))
}

function formatPercent(value) {
  const parsed = percent(value)
  if (parsed == null) return '--'
  return `${parsed.toFixed(1).replace(/\.0$/, '')}%`
}

function hasValue(value) {
  const normalized = String(value ?? '').trim()
  return Boolean(normalized) && !['--', '—', '待判定', '暂不可用'].includes(normalized)
}

function statusRank(status) {
  return {
    down: 0,
    degraded: 1,
    unknown: 2,
    running: 3,
    healthy: 4,
  }[status] ?? 2
}

function pickWorstStatus(items) {
  return items.reduce((worst, item) => (
    statusRank(item.status) < statusRank(worst) ? item.status : worst
  ), 'healthy')
}

function stage(id, title, status, detail, evidence = [], to = '') {
  return {
    id,
    title,
    status,
    detail,
    evidence: evidence.filter((item) => hasValue(item)),
    to,
  }
}

function buildOrganCoverage(soakSummary = {}, soakMeta = {}) {
  const expected = numeric(soakMeta.expectedOrganCount, EXPECTED_ORGAN_COUNT) || EXPECTED_ORGAN_COUNT
  const registered = numeric(soakSummary.registered_organ_count, 0)
  const mapped = numeric(soakSummary.last_mapped_organ_count, 0)
  const available = Boolean(soakSummary.run_id || soakSummary.status || soakSummary.verdict)
  const complete = registered >= expected && mapped >= expected

  return {
    expected,
    registered,
    mapped,
    status: !available ? 'unknown' : complete ? 'healthy' : registered > 0 || mapped > 0 ? 'degraded' : 'down',
    label: available ? `${registered}/${expected}` : '--',
    mappedLabel: available ? `${mapped}/${expected}` : '--',
  }
}

function buildTickStatus(soakSummary = {}, runtimeSnapshot = {}) {
  const background = runtimeSnapshot?.background_runtime || {}
  const schedulerStatus = String(soakSummary.scheduler_status || background.scheduler_status || '').toUpperCase()
  const tickHealth = String(soakSummary.tick_health || background.tick_health || '').toLowerCase()
  const alive = soakSummary.alive ?? background.alive
  const advancing = soakSummary.is_advancing ?? background.is_advancing
  const hasTick = Boolean(schedulerStatus || tickHealth || soakSummary.idle_tick_count != null || Object.keys(background).length)
  const healthy = alive !== false && advancing !== false && schedulerStatus === 'HEALTHY' && ['fresh', 'ok', 'healthy'].includes(tickHealth)

  return {
    status: !hasTick ? 'unknown' : healthy ? 'healthy' : alive === false || advancing === false ? 'down' : 'degraded',
    schedulerStatus: schedulerStatus || '--',
    tickHealth: tickHealth || '--',
    idleTickCount: soakSummary.idle_tick_count ?? background.tick_count ?? '--',
  }
}

function buildOutputLoopStatus(humanoidLoopData = null) {
  const total = numeric(humanoidLoopData?.total_gateway_requests, 0)
  const closedLoopRate = percent(humanoidLoopData?.closed_loop_rate)
  const ackRate = percent(humanoidLoopData?.ack_touch_rate)
  const progressRate = percent(humanoidLoopData?.progress_touch_rate)

  if (!humanoidLoopData) {
    return {
      status: 'unknown',
      detail: '等待 Gateway 闭环统计接入。',
      evidence: ['请求样本 --'],
    }
  }

  if (total <= 0) {
    return {
      status: 'running',
      detail: 'Gateway 闭环接口已接入，当前窗口暂无请求样本。',
      evidence: ['请求样本 0'],
    }
  }

  return {
    status: closedLoopRate != null && closedLoopRate < 70 ? 'degraded' : 'healthy',
    detail: `闭环 ${formatPercent(closedLoopRate)}，ACK ${formatPercent(ackRate)}，Progress ${formatPercent(progressRate)}。`,
    evidence: [`请求 ${total}`, `闭环 ${formatPercent(closedLoopRate)}`],
  }
}

export function buildMainChainReshapeModel({
  runtimeSnapshot = null,
  lifeBodyModel = {},
  lifeBodyDerived = {},
  wallClockSoakData = null,
  hasRuntimeSnapshot = false,
  hasMemoryData = false,
  hasMemorySamples = false,
  growthSignals = {},
  humanoidLoopData = null,
  runtimeAlerts = [],
  nonLiveSlots = [],
  currentEmotion = '',
  currentBondStage = '',
  dominantMotive = '',
  replyPosture = '',
  riskLevel = '',
} = {}) {
  const soakSummary = wallClockSoakData?.summary || {}
  const soakMeta = wallClockSoakData?.meta || {}
  const organCoverage = buildOrganCoverage(soakSummary, soakMeta)
  const tickStatus = buildTickStatus(soakSummary, runtimeSnapshot)
  const outputLoop = buildOutputLoopStatus(humanoidLoopData)
  const criticalAlerts = runtimeAlerts.filter((item) => item?.severity === 'critical').length
  const warningAlerts = runtimeAlerts.filter((item) => item?.severity === 'warning').length
  const organSystems = Array.isArray(lifeBodyModel.organSystems) ? lifeBodyModel.organSystems : []
  const unstableOrgans = organSystems.filter((item) => ['critical', 'warning'].includes(String(item?.status || '')))
  const hasGrowth = Boolean(growthSignals?.hasAnyGrowth || runtimeSnapshot?.self_model_snapshot?.recent_mood_trend)
  const hasPersonaContext = hasRuntimeSnapshot && [
    currentEmotion,
    currentBondStage,
    dominantMotive,
    replyPosture,
  ].some(hasValue)
  const hasTraceOrLineage = numeric(lifeBodyDerived.activeTraceFeedCount, 0) > 0
    || numeric(lifeBodyDerived.recentSessionCount, 0) > 0
    || numeric(lifeBodyDerived.memoryLineageCount, 0) > 0
    || numeric(lifeBodyDerived.tickLineageCount, 0) > 0

  const stages = [
    stage(
      'entry',
      '输入与神经入口',
      hasRuntimeSnapshot || hasTraceOrLineage ? 'healthy' : 'running',
      hasTraceOrLineage ? 'Trace / session lineage 已经回流到观测面。' : '当前以运行快照维持入口态势，等待更多请求样本。',
      [
        `Trace ${lifeBodyDerived.activeTraceFeedCount ?? 0}`,
        `Session ${lifeBodyDerived.recentSessionCount ?? 0}`,
      ],
      '/observability/trace'
    ),
    stage(
      'guard',
      '前额叶门控与免疫',
      criticalAlerts > 0 ? 'down' : warningAlerts > 0 || nonLiveSlots.length > 0 ? 'degraded' : hasRuntimeSnapshot ? 'healthy' : 'unknown',
      criticalAlerts > 0
        ? '免疫或叙事护栏出现关键告警，主链需要先收束风险。'
        : warningAlerts > 0 || nonLiveSlots.length > 0
          ? '门控仍可工作，但存在告警或非 live 槽位。'
          : '门控、风险与非 live 槽位处于可读状态。',
      [
        `风险 ${riskLevel || '--'}`,
        `告警 ${criticalAlerts}/${warningAlerts}`,
        `非 live ${nonLiveSlots.length}`,
      ],
      '/observability/alerts'
    ),
    stage(
      'context',
      '人格上下文装配',
      hasPersonaContext ? 'healthy' : hasRuntimeSnapshot ? 'degraded' : 'unknown',
      hasPersonaContext
        ? '情绪、关系、动机与回复姿态已经能合成主链人格上下文。'
        : '运行快照存在，但人格上下文缺少可显示字段。',
      [
        `情绪 ${currentEmotion || '--'}`,
        `关系 ${currentBondStage || '--'}`,
        `动机 ${dominantMotive || '--'}`,
        `回复 ${replyPosture || '--'}`,
      ],
      '/life/self-model'
    ),
    stage(
      'memory',
      '记忆与设定续写',
      !hasMemoryData ? 'unknown' : hasMemorySamples ? 'healthy' : 'running',
      !hasMemoryData
        ? '记忆统计接口暂不可用。'
        : hasMemorySamples
          ? '记忆统计有样本，长期上下文可参与主链判断。'
          : '记忆系统已接入，当前窗口等待样本。',
      [
        `记忆 lineage ${lifeBodyDerived.memoryLineageCount ?? 0}`,
        `设定/记忆样本 ${hasMemorySamples ? '有' : '待样本'}`,
      ],
      '/life/memory'
    ),
    stage(
      'organs',
      '23 个器官族接入',
      organCoverage.status,
      organCoverage.status === 'healthy'
        ? '23 个器官族已经注册并完成主链映射。'
        : organCoverage.status === 'unknown'
          ? '等待真实墙钟 soak 暴露器官族映射。'
          : '器官族注册或映射尚未满额。',
      [
        `注册 ${organCoverage.label}`,
        `映射 ${organCoverage.mappedLabel}`,
      ],
      '/cockpit'
    ),
    stage(
      'growth',
      '七情六欲与成长回路',
      hasGrowth ? 'healthy' : hasRuntimeSnapshot ? 'running' : 'unknown',
      hasGrowth
        ? '自我模型或成长信号已进入主链表达。'
        : '成长回路在线，等待更多自我模型样本沉积。',
      [
        `成长信号 ${hasGrowth ? '有' : '待样本'}`,
        `主病灶 ${lifeBodyModel.primaryPathology?.title || '未形成'}`,
      ],
      '/life/self-model'
    ),
    stage(
      'output',
      '输出、执行与回执闭环',
      outputLoop.status,
      outputLoop.detail,
      outputLoop.evidence,
      '/execution/gateway'
    ),
    stage(
      'tick',
      '后台 tick 与持续活性',
      tickStatus.status,
      tickStatus.status === 'healthy'
        ? '后台节律正在推进，生命体不是一次性快照。'
        : tickStatus.status === 'unknown'
          ? '等待 tick 或 soak 快照。'
          : '后台节律需要复查。',
      [
        `scheduler ${tickStatus.schedulerStatus}`,
        `tick ${tickStatus.tickHealth}`,
        `idle ${tickStatus.idleTickCount}`,
      ],
      '/execution/automation'
    ),
  ]

  const overallStatus = pickWorstStatus(stages)
  const hardBreaks = stages.filter((item) => item.status === 'down')
  const softGaps = stages.filter((item) => ['degraded', 'unknown'].includes(item.status))
  const runningGaps = stages.filter((item) => item.status === 'running')
  const completionCount = stages.filter((item) => ['healthy', 'running'].includes(item.status)).length
  const completionRatio = Math.round((completionCount / stages.length) * 100)
  const unstableOrganCount = unstableOrgans.length

  return {
    status: overallStatus === 'unknown' && runningGaps.length > 0 ? 'running' : overallStatus,
    title: hardBreaks.length
      ? '主链存在断点'
      : softGaps.length
        ? '主链重塑已接上，仍有观测缺口'
        : runningGaps.length
          ? '主链重塑已接上，等待样本沉积'
          : '重塑主链闭环在线',
    summary: hardBreaks.length
      ? `当前 ${hardBreaks.length} 个主链阶段需要处理。`
      : softGaps.length
        ? `核心链路可运行，${softGaps.length} 个阶段仍需更多样本或降级复查。`
        : runningGaps.length
          ? `核心链路已经接上，${runningGaps.length} 个阶段在线等待更多真实请求或记忆样本。`
          : '输入、门控、人格上下文、记忆、器官族、输出和后台 tick 已形成可观测闭环。',
    completionRatio,
    stages,
    gaps: [...hardBreaks, ...softGaps].slice(0, 4),
    metrics: [
      { label: '主链阶段', value: `${completionCount}/${stages.length}`, hint: `${completionRatio}% 可观测` },
      { label: '器官族', value: organCoverage.label, hint: `映射 ${organCoverage.mappedLabel}` },
      { label: 'idle tick', value: tickStatus.idleTickCount, hint: `${tickStatus.schedulerStatus} / ${tickStatus.tickHealth}` },
      { label: '器官病灶', value: unstableOrganCount, hint: unstableOrganCount ? '需看病灶中心' : '无明显紧张器官' },
    ],
  }
}
