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

export function buildGatewayHeroDescription({ data, isInitialState } = {}) {
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
  const gatewayLoadState = buildGatewayLoadState({ data, wechatDetail, loading, refreshing, error })
  const motorOrganModel = buildMotorOrganSystemModel({
    loadState: gatewayLoadState,
    updatedAt: lastUpdatedAt,
    platformCount: platforms.length,
    activePlatformCount: activePlatforms.length,
    downPlatformCount,
    degradedPlatformCount,
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
    hasWeChatRateLimits: (wechatDetail?.rate_limits ?? []).length > 0,
    hasWeChatEvents: (wechatDetail?.recent_events ?? []).length > 0,
    downPlatformCount,
    degradedPlatformCount,
    gatewayLoadState,
    motorOrganModel,
    heroTitle: `${platforms.length} 平台`,
    heroDescription: buildGatewayHeroDescription({ data, isInitialState: initialState }),
    heroMetaItems: buildGatewayHeroMetaItems({
      activePlatformCount: activePlatforms.length,
      data,
      windowMinutes,
    }),
    metricCards: buildGatewayMetricCards(data),
    showUnavailableNotice: !error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.UNAVAILABLE,
    showNoSampleNotice: !error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.NO_SAMPLE,
  }
}
