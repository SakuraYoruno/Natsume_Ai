import {
  LOAD_STATES,
  ORGAN_STATUS,
  createOrganSystemModel,
  metric,
} from './organSystemCore.js'

export function buildEndocrineOrganSystemModel({
  loadState,
  updatedAt,
  schedulerState,
  taskCount,
  enabledCount,
  runningCount,
  failedCount,
  filteredCount,
  hasTaskFilters,
  backgroundRuntime,
}) {
  const missingTicks = Array.isArray(backgroundRuntime?.core_ticks_missing) ? backgroundRuntime.core_ticks_missing : []
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : failedCount > 0 || backgroundRuntime?.persona_user_alignment_ok === false
      ? ORGAN_STATUS.CRITICAL
      : !schedulerState?.running || backgroundRuntime?.is_advancing === false || missingTicks.length > 0
        ? ORGAN_STATUS.WARNING
        : loadState === LOAD_STATES.NO_SAMPLE
          ? ORGAN_STATUS.PENDING
          : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'endocrine',
    name: '内分泌系统 / 节律中枢',
    status,
    loadState,
    freshness: updatedAt,
    trend: schedulerState?.label || '--',
    riskLevel: failedCount > 0 ? `${failedCount} 个异常任务` : backgroundRuntime?.is_advancing === false ? '推进异常' : '低',
    summary: loadState === LOAD_STATES.UNAVAILABLE
      ? '当前还没有接到完整节律快照，器官执行台会保留中性观察位。'
      : `${schedulerState?.title || '节律状态待判定'}，当前共有 ${taskCount || 0} 个节律任务登记在案。`,
    symptoms: [
      `任务 ${taskCount || 0} · 启用 ${enabledCount || 0} · 推进中 ${runningCount || 0} · 异常 ${failedCount || 0}`,
      missingTicks.length > 0 ? `核心 Tick 缺失：${missingTicks.join('、')}` : '核心 Tick 已注册',
      hasTaskFilters ? `当前筛选命中 ${filteredCount || 0} 个任务` : '当前未应用任务筛选',
    ],
    downstreamImpacts: [
      backgroundRuntime?.is_advancing === false
        ? '后台推进异常会拖慢内在世界、沉默机制和体感推进。'
        : '节律中枢会持续为情绪漂移、恢复与离线演化提供底座。',
      failedCount > 0
        ? '异常任务会直接削弱后台推进连续性，建议沿历史脉络和免疫回放继续追。'
        : '当前任务健康度足以支撑生命体内部节律继续流动。',
    ],
    metrics: [
      metric('节律态', schedulerState?.label || '待判定', schedulerState?.running ? '调度在运行' : '调度未运行', schedulerState?.running ? 'text-emerald-600' : 'text-orange-500'),
      metric('任务总数', String(taskCount || 0), '当前登记任务', 'text-gray-800'),
      metric('推进中', String(runningCount || 0), `启用 ${enabledCount || 0}`, 'text-blue-600'),
      metric('异常任务', String(failedCount || 0), hasTaskFilters ? `筛中 ${filteredCount || 0}` : '无筛选', failedCount > 0 ? 'text-red-600' : 'text-gray-400'),
    ],
    drilldownPath: '/life/timeline',
    drilldownLabel: '沿生命时间轴继续追',
  })
}

export function buildMotorOrganSystemModel({
  loadState,
  updatedAt,
  platformCount,
  activePlatformCount,
  downPlatformCount,
  degradedPlatformCount,
  totalInbound,
  totalOutbound,
  totalErrors,
  humanizeEnabled,
  humanizeTypingSpeed,
  rateLimitCount,
  recentEventCount,
}) {
  const hasTraffic = Number(totalInbound || 0) + Number(totalOutbound || 0) + Number(totalErrors || 0) > 0
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : downPlatformCount > 0
      ? ORGAN_STATUS.CRITICAL
      : degradedPlatformCount > 0 || Number(totalErrors || 0) > 0
        ? ORGAN_STATUS.WARNING
        : !hasTraffic && loadState === LOAD_STATES.NO_SAMPLE
          ? ORGAN_STATUS.PENDING
          : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'motor',
    name: '运动系统',
    status,
    loadState,
    freshness: updatedAt,
    trend: humanizeEnabled ? '拟人化开启' : '拟人化关闭',
    riskLevel: downPlatformCount > 0 ? `${downPlatformCount} 平台离线` : Number(totalErrors || 0) > 0 ? `错误 ${totalErrors}` : '低',
    summary: !hasTraffic
      ? `当前窗口暂无流量样本，${platformCount || 0} 个平台显示的是适配器健康态。`
      : `近窗口入站 ${totalInbound || 0}，出站 ${totalOutbound || 0}，错误 ${totalErrors || 0}。`,
    symptoms: [
      `在线平台 ${activePlatformCount || 0}/${platformCount || 0} · 降级 ${degradedPlatformCount || 0} · 离线 ${downPlatformCount || 0}`,
      humanizeEnabled ? `拟人化已开启 · 字速 ${humanizeTypingSpeed ?? '--'} 字/秒` : '拟人化当前关闭',
      `微信频率限制 ${rateLimitCount || 0} 条 · 最近事件 ${recentEventCount || 0} 条`,
    ],
    downstreamImpacts: [
      '运动系统会直接影响消息触达、ACK 反馈和用户对执行闭环的体感。',
      downPlatformCount > 0 || Number(totalErrors || 0) > 0
        ? '平台离线或发送错误会让用户感知到“答应了但没做到”。'
        : '当前外周执行链路总体稳定，可继续沿 Trace 追请求级闭环。',
    ],
    metrics: [
      metric('在线平台', String(activePlatformCount || 0), `总计 ${platformCount || 0}`, 'text-emerald-600'),
      metric('总入站', String(totalInbound || 0), '窗口统计', 'text-blue-600'),
      metric('总出站', String(totalOutbound || 0), humanizeEnabled ? '拟人化发送中' : '标准发送', 'text-green-600'),
      metric('总错误', String(totalErrors || 0), `频限 ${rateLimitCount || 0}`, Number(totalErrors || 0) > 0 ? 'text-red-600' : 'text-gray-400'),
    ],
    drilldownPath: '/observability/trace',
    drilldownLabel: '进入 Trace 继续追',
  })
}

export function buildToolsOrganSystemModel({
  loadState,
  updatedAt,
  healthStatus,
  catalogSize,
  readCount,
  writeCount,
  filter,
  query,
  runtimeSummary,
}) {
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : healthStatus && healthStatus !== 'OK'
      ? ORGAN_STATUS.WARNING
      : loadState === LOAD_STATES.NO_SAMPLE
        ? ORGAN_STATUS.PENDING
        : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'motor',
    name: '运动系统 / 工具执行域',
    status,
    loadState,
    freshness: updatedAt,
    trend: filter || 'ALL',
    riskLevel: healthStatus || '--',
    summary: healthStatus === 'OK'
      ? '工具引擎当前健康，适合继续检查权限边界、执行计划和 provider 状态。'
      : '工具引擎当前需要关注，建议优先检查目录健康、权限拦截和 provider 状态。',
    symptoms: [
      `工具总数 ${catalogSize || 0} · 只读 ${readCount || 0} · 写入 ${writeCount || 0}`,
      query ? `当前搜索：${query}` : '当前未使用搜索过滤',
      runtimeSummary || '运行态摘要待连接',
    ],
    downstreamImpacts: [
      '工具执行域会直接影响技能调用、外部动作落地和需要审批的写入操作。',
      writeCount > 0
        ? '写入工具越多，越需要清晰的审批边界和执行计划诊断。'
        : '当前目录以只读能力为主，风险相对可控。',
    ],
    metrics: [
      metric('引擎状态', healthStatus || '待判定', `目录 ${catalogSize || 0}`, healthStatus === 'OK' ? 'text-emerald-600' : 'text-orange-500'),
      metric('只读工具', String(readCount || 0), '低副作用', 'text-green-600'),
      metric('写入工具', String(writeCount || 0), '需关注审批边界', 'text-orange-600'),
      metric('当前筛选', filter || 'ALL', query ? '已带搜索词' : '无搜索', 'text-blue-600'),
    ],
    drilldownPath: '/execution/gateway',
    drilldownLabel: '查看外部执行闭环',
  })
}
