import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildMotorOrganSystemModel } from '../../models/organSystemModel.js'

export const GATEWAY_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export function getGatewayPlatforms(data) {
  return Array.isArray(data?.platforms) ? data.platforms : []
}

export function isActiveGatewayPlatform(platform) {
  return platform?.status === 'UP' || (platform?.inbound_count ?? 0) > 0 || (platform?.outbound_count ?? 0) > 0
}

export function getActiveGatewayPlatforms(platforms) {
  const rows = Array.isArray(platforms) ? platforms : []
  return rows.filter(isActiveGatewayPlatform)
}

export function getGatewayEventCount(data) {
  return Number(data?.total_inbound ?? 0) + Number(data?.total_outbound ?? 0) + Number(data?.total_errors ?? 0)
}

export function isGatewayInitialState(data) {
  return Boolean(data)
    && (data.total_inbound ?? 0) === 0
    && (data.total_outbound ?? 0) === 0
    && (data.total_errors ?? 0) === 0
}

export function countGatewayPlatformsByStatus(platforms, status) {
  const rows = Array.isArray(platforms) ? platforms : []
  return rows.filter((item) => item?.status === status).length
}

export function countKnownGatewayPlatforms(platforms) {
  const rows = Array.isArray(platforms) ? platforms : []
  return rows.filter((item) => ['UP', 'DEGRADED', 'DOWN', 'DISABLED'].includes(item?.status)).length
}

export function getWeChatRateLimits(wechatDetail) {
  return Array.isArray(wechatDetail?.rate_limits) ? wechatDetail.rate_limits : []
}

export function getWeChatRecentEvents(wechatDetail) {
  return Array.isArray(wechatDetail?.recent_events) ? wechatDetail.recent_events : []
}

export function buildGatewayLoadState({ data, wechatDetail, loading = false, refreshing = false, error = null } = {}) {
  const gatewayEventCount = getGatewayEventCount(data)
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data || wechatDetail),
    hasSamples: !isGatewayInitialState(data) && gatewayEventCount > 0,
    available: Boolean(data || wechatDetail),
  })
}

export function buildGatewayHeroDescription({ data, isInitialState, knownPlatformCount = 0 } = {}) {
  if (isInitialState && knownPlatformCount === 0) {
    return '当前窗口暂无流量样本；Gateway 服务在线，但分平台健康明细未接入，不能据此判断通道健康。'
  }
  if (isInitialState) return '当前窗口暂无流量样本，平台卡显示的是适配器健康态。'
  return `近窗口入站 ${data?.total_inbound ?? 0}，出站 ${data?.total_outbound ?? 0}，错误 ${data?.total_errors ?? 0}。`
}

export function buildGatewayHeroMetaItems({ activePlatformCount = 0, data, windowMinutes } = {}) {
  return [
    `在线平台：${activePlatformCount}`,
    `拟人化：${data?.humanize_enabled ? '开启' : '关闭'}`,
    `窗口：${windowMinutes} 分钟`,
  ]
}

export function buildGatewayMetricCards(data) {
  return [
    {
      key: 'inbound',
      label: '总入站',
      value: data?.total_inbound ?? '—',
      tone: 'text-blue-600',
      hint: '',
    },
    {
      key: 'outbound',
      label: '总出站',
      value: data?.total_outbound ?? '—',
      tone: 'text-green-600',
      hint: '',
    },
    {
      key: 'errors',
      label: '总错误',
      value: data?.total_errors ?? '—',
      tone: (data?.total_errors ?? 0) > 0 ? 'text-red-500' : 'text-gray-400',
      hint: '',
    },
    {
      key: 'humanize',
      label: '拟人化',
      value: data?.humanize_enabled ? '开启' : '关闭',
      hint: data?.humanize_enabled ? `字速 ${data?.humanize_typing_speed_cps ?? '—'} 字/秒` : '已关闭',
      tone: data?.humanize_enabled ? 'text-green-600' : 'text-gray-400',
    },
  ]
}

function buildGatewayFocusAction({
  data,
  error,
  gatewayEventCount,
  hasData,
  isLoading,
  rateLimitCount,
  downPlatformCount,
  degradedPlatformCount,
  unknownPlatformCount,
}) {
  if (error) {
    return {
      icon: 'alert',
      title: '先恢复网关观测接口',
      detail: 'Gateway stats 请求失败，页面保持只读降级。优先查看日志或 Host/Obs 连接状态。',
      label: '查看日志',
      to: '/observability/logs',
      state: 'blocked',
    }
  }

  if (isLoading) {
    return {
      icon: 'radio',
      title: '正在同步网关快照',
      detail: '等待 Gateway 与 WeChat stats 返回后，再判断平台健康和路由风险。',
      label: '刷新状态',
      action: 'refresh',
      state: 'warning',
    }
  }

  if (unknownPlatformCount > 0) {
    return {
      icon: 'wifi',
      title: '先补齐分平台健康证据',
      detail: `当前仍有 ${unknownPlatformCount} 个平台状态未确认；只能确认 Gateway 进程与 durable transport 在线，不能把零流量解释为通道健康。`,
      label: '查看服务状态',
      to: '/observability/overview',
      state: 'warning',
    }
  }

  if (downPlatformCount > 0 || degradedPlatformCount > 0) {
    const parts = [
      downPlatformCount > 0 ? `${downPlatformCount} 个平台离线` : null,
      degradedPlatformCount > 0 ? `${degradedPlatformCount} 个平台降级` : null,
    ].filter(Boolean).join('，')
    return {
      icon: 'wifi',
      title: '先处理平台健康',
      detail: `${parts}。先定位具体平台，再决定是否进入日志或 Trace。`,
      label: '定位平台健康',
      action: 'platforms',
      state: downPlatformCount > 0 ? 'blocked' : 'warning',
    }
  }

  if (rateLimitCount > 0) {
    return {
      icon: 'message',
      title: '复核 WeChat 频率限制',
      detail: `${rateLimitCount} 条频率限制命中，先确认是否需要重置或降低发送速率。`,
      label: '定位微信通道',
      action: 'wechat',
      state: 'warning',
    }
  }

  if (gatewayEventCount > 0) {
    return {
      icon: 'trace',
      title: '沿 Trace 复核路由链路',
      detail: `窗口内已有 ${gatewayEventCount} 条入站、出站或错误事件，可继续追 request 级链路。`,
      label: '进入 Trace',
      to: '/observability/trace',
      state: 'ready',
    }
  }

  if (hasData || data) {
    return {
      icon: 'edge',
      title: '当前是空流量健康态',
      detail: '接口可读但没有流量样本。若在联调下行能力，下一步进入 Edge Command 触发可追踪命令。',
      label: '进入 Edge Command',
      to: '/execution/edge-command',
      state: 'warning',
    }
  }

  return {
    icon: 'radio',
    title: '等待网关数据源接入',
    detail: '当前没有 Gateway 或 WeChat 快照，先刷新或确认 Observability Backend 已启动。',
    label: '刷新状态',
    action: 'refresh',
    state: 'blocked',
  }
}

export function buildGatewayCommandRailView({
  data,
  wechatDetail,
  platforms,
  activePlatforms,
  gatewayEventCount,
  downPlatformCount,
  degradedPlatformCount,
  unknownPlatformCount,
  loading = false,
  refreshing = false,
  error = null,
} = {}) {
  const isLoading = loading || refreshing
  const hasData = Boolean(data || wechatDetail)
  const totalPlatformCount = platforms.length
  const activePlatformCount = activePlatforms.length
  const totalErrors = Number(data?.total_errors ?? 0)
  const rateLimitCount = getWeChatRateLimits(wechatDetail).length
  const wechatEventCount = getWeChatRecentEvents(wechatDetail).length
  const riskSignalCount = totalErrors + downPlatformCount + degradedPlatformCount + unknownPlatformCount + rateLimitCount
  const focusAction = buildGatewayFocusAction({
    data,
    error,
    gatewayEventCount,
    hasData,
    isLoading,
    rateLimitCount,
    downPlatformCount,
    degradedPlatformCount,
    unknownPlatformCount,
  })
  const telemetryState = error ? 'blocked' : (isLoading ? 'warning' : (hasData ? 'ready' : 'blocked'))
  const platformState = totalPlatformCount === 0 || unknownPlatformCount > 0
    ? 'warning'
    : downPlatformCount > 0
      ? 'blocked'
      : degradedPlatformCount > 0
        ? 'warning'
        : 'ready'
  const wechatState = !wechatDetail ? 'warning' : (rateLimitCount > 0 ? 'warning' : 'ready')
  const evidenceState = gatewayEventCount > 0 ? 'ready' : (hasData ? 'warning' : 'blocked')

  return {
    state: focusAction.state,
    statusLabel: error
      ? '网关观测降级'
      : downPlatformCount > 0
        ? `${downPlatformCount} 个平台离线`
        : degradedPlatformCount > 0
          ? `${degradedPlatformCount} 个平台降级`
          : rateLimitCount > 0
            ? `${rateLimitCount} 条 WeChat 限制`
            : unknownPlatformCount > 0
              ? `${unknownPlatformCount} 个平台状态未确认`
              : gatewayEventCount > 0
                ? '路由样本可追踪'
                : '空流量健康态',
    focusAction,
    gateCards: [
      {
        label: 'Telemetry',
        state: telemetryState,
        detail: error ? 'Gateway stats 请求失败。' : (isLoading ? '正在同步 Gateway / WeChat stats。' : (hasData ? '数据源已返回。' : '暂无可用快照。')),
      },
      {
        label: 'Platform Health',
        state: platformState,
        detail: totalPlatformCount
          ? `${activePlatformCount}/${totalPlatformCount} active，${downPlatformCount} down，${degradedPlatformCount} degraded，${unknownPlatformCount} unknown。`
          : '当前窗口没有平台快照。',
      },
      {
        label: 'WeChat Channel',
        state: wechatState,
        detail: wechatDetail ? `${rateLimitCount} 条限制，${wechatEventCount} 条最近事件。` : 'WeChat stats 未返回，按可降级通道处理。',
      },
      {
        label: 'Routing Evidence',
        state: evidenceState,
        detail: gatewayEventCount > 0 ? `${gatewayEventCount} 条路由事件可继续追 Trace。` : '当前窗口没有可追踪路由事件。',
      },
    ],
    handoffSteps: [
      {
        key: 'telemetry',
        label: 'Telemetry Source',
        value: isLoading ? 'syncing' : (hasData ? 'gateway + wechat' : 'missing'),
        detail: hasData ? 'Gateway stats 或 WeChat detail 至少一个数据源可读。' : '没有可读快照时只展示降级指引。',
        state: telemetryState,
      },
      {
        key: 'platforms',
        label: 'Platform Coverage',
        value: totalPlatformCount ? `${activePlatformCount}/${totalPlatformCount} active` : 'no platform',
        detail: totalPlatformCount ? `${downPlatformCount} down / ${degradedPlatformCount} degraded / ${unknownPlatformCount} unknown。` : '平台列表为空，无法判断分平台健康。',
        state: platformState,
      },
      {
        key: 'risk',
        label: 'Risk Hotspot',
        value: riskSignalCount ? `${riskSignalCount} signals` : 'clear',
        detail: `${totalErrors} errors，${downPlatformCount} down，${degradedPlatformCount} degraded，${unknownPlatformCount} unknown，${rateLimitCount} WeChat limits。`,
        state: downPlatformCount > 0 || totalErrors > 0 ? 'blocked' : (riskSignalCount > 0 ? 'warning' : 'ready'),
      },
      {
        key: 'wechat',
        label: 'WeChat Channel',
        value: wechatDetail ? `${rateLimitCount} limits / ${wechatEventCount} events` : 'not connected',
        detail: wechatDetail ? '微信通道详情可用于确认节流和最近消息事件。' : '微信详情不可用时不影响其他平台健康判断。',
        state: wechatState,
      },
      {
        key: 'handoff',
        label: 'Next Handoff',
        value: focusAction.to ? focusAction.label : focusAction.title,
        detail: focusAction.detail,
        state: focusAction.state,
      },
    ],
  }
}

export function buildGatewayViewModel({
  data,
  wechatDetail,
  loading = false,
  refreshing = false,
  error = null,
  lastUpdatedAt = null,
  windowMinutes = 60,
} = {}) {
  const platforms = getGatewayPlatforms(data)
  const activePlatforms = getActiveGatewayPlatforms(platforms)
  const gatewayEventCount = getGatewayEventCount(data)
  const initialState = isGatewayInitialState(data)
  const downPlatformCount = countGatewayPlatformsByStatus(platforms, 'DOWN')
  const degradedPlatformCount = countGatewayPlatformsByStatus(platforms, 'DEGRADED')
  const knownPlatformCount = countKnownGatewayPlatforms(platforms)
  const unknownPlatformCount = Math.max(0, platforms.length - knownPlatformCount)
  const gatewayLoadState = buildGatewayLoadState({ data, wechatDetail, loading, refreshing, error })
  const hasWeChatRateLimits = getWeChatRateLimits(wechatDetail).length > 0
  const hasWeChatEvents = getWeChatRecentEvents(wechatDetail).length > 0
  const motorOrganModel = buildMotorOrganSystemModel({
    loadState: gatewayLoadState,
    updatedAt: lastUpdatedAt,
    platformCount: platforms.length,
    activePlatformCount: activePlatforms.length,
    downPlatformCount,
    degradedPlatformCount,
    unknownPlatformCount,
    totalInbound: data?.total_inbound,
    totalOutbound: data?.total_outbound,
    totalErrors: data?.total_errors,
    humanizeEnabled: data?.humanize_enabled,
    humanizeTypingSpeed: data?.humanize_typing_speed_cps,
    rateLimitCount: wechatDetail?.rate_limits?.length,
    recentEventCount: wechatDetail?.recent_events?.length,
  })

  return {
    platforms,
    activePlatforms,
    activePlatformCount: activePlatforms.length,
    gatewayEventCount,
    isInitialState: initialState,
    hasWeChatRateLimits,
    hasWeChatEvents,
    downPlatformCount,
    degradedPlatformCount,
    knownPlatformCount,
    unknownPlatformCount,
    gatewayLoadState,
    motorOrganModel,
    heroTitle: `${platforms.length} 平台`,
    heroDescription: buildGatewayHeroDescription({
      data,
      isInitialState: initialState,
      knownPlatformCount,
    }),
    heroMetaItems: buildGatewayHeroMetaItems({
      activePlatformCount: activePlatforms.length,
      data,
      windowMinutes,
    }),
    metricCards: buildGatewayMetricCards(data),
    commandRailView: buildGatewayCommandRailView({
      data,
      wechatDetail,
      platforms,
      activePlatforms,
      gatewayEventCount,
      downPlatformCount,
      degradedPlatformCount,
      unknownPlatformCount,
      loading,
      refreshing,
      error,
    }),
    showUnavailableNotice: !error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.UNAVAILABLE,
    showNoSampleNotice: !error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.NO_SAMPLE,
  }
}
