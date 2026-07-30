import { BOND_STAGE_COLORS } from '../../shared/statusMeta.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildBondOrganSystemModel } from '../../models/organSystemModel.js'
import { formatDateTime, formatStage, formatTrend } from '../../utils/digitalLife.js'

export function formatBondHealthStatus(status) {
  if (!status) return '—'
  if (status === 'OK') return '正常'
  if (status === 'DEGRADED') return '降级'
  if (status === 'DOWN') return '离线'
  return status
}

export function buildBondStageDistribution(items = []) {
  const safeItems = Array.isArray(items) ? items : []
  const total = safeItems.reduce((sum, item) => sum + Number(item?.count || 0), 0)
  return {
    total,
    items: safeItems.map((item) => {
      const count = Number(item?.count || 0)
      const percent = total > 0 ? (count / total) * 100 : 0
      return {
        ...item,
        count,
        label: formatStage(item?.stage),
        percent,
        percentLabel: `${percent.toFixed(0)}%`,
        width: `${percent}%`,
        color: BOND_STAGE_COLORS[item?.stage] || '#cbd5e1',
      }
    }),
  }
}

export function buildBondHeroMetaItems({
  health,
  users = [],
  stageSampleCount,
  selectedUser,
}) {
  return [
    `引擎状态：${formatBondHealthStatus(health?.status)}`,
    `用户数：${health?.user_count ?? users.length}`,
    `分布样本：${stageSampleCount}`,
    `已选用户：${selectedUser || '未选择'}`,
  ]
}

export function buildBondLoadState({
  loading = false,
  refreshing = false,
  overviewError = null,
  health = null,
  users = [],
  stageDist = [],
  stageSampleCount = 0,
  stale = false,
} = {}) {
  const hasData = Boolean(health || users.length > 0 || stageDist.length > 0)
  return resolveLoadState({
    loading: loading || refreshing,
    error: overviewError,
    hasData,
    hasSamples: users.length > 0 || stageSampleCount > 0 || Number(health?.user_count ?? 0) > 0,
    available: hasData,
    stale,
  })
}

export function getBondUpdatedAt(lastUpdatedAt, detailUpdatedAt) {
  return Math.max(lastUpdatedAt || 0, detailUpdatedAt || 0) || null
}

export function buildBondLabels({ health = null, userDetail = null } = {}) {
  return {
    healthStatus: formatBondHealthStatus(health?.status),
    currentStageLabel: formatStage(userDetail?.current_stage),
    currentTrendLabel: formatTrend(userDetail?.trend),
    lastEventLabel: formatDateTime(userDetail?.last_event_ts_ms),
  }
}

export function buildBondHeroView({ selectedUser, health, users = [], stageSampleCount } = {}) {
  return {
    title: selectedUser || formatBondHealthStatus(health?.status),
    description: selectedUser
      ? '当前正在查看迁移窗口内的历史 Bond 投影；它不会决定亲密权限或主动频率。'
      : '这里集中展示历史 Bond 投影。真实关系连续性由 Digital Life episode 与 aftereffect 生命周期维护。',
    metaItems: buildBondHeroMetaItems({ health, users, stageSampleCount, selectedUser }),
  }
}

export function buildBondSampleMeta({ bondLoadState, bondUpdatedAt, stageSampleCount } = {}) {
  return {
    loadState: bondLoadState,
    sampleSize: stageSampleCount,
    sampleLabel: '关系样本',
    updatedAt: bondUpdatedAt,
    source: 'bond / stage',
    definition: '本页只读聚合历史 Bond 投影、用户列表、阶段分布与当前用户详情；它不是关系写 authority。',
  }
}

export function buildBondNoticeView({ overviewError, loading = false, refreshing = false, bondLoadState } = {}) {
  const idle = !overviewError && !loading && !refreshing
  return {
    overviewError,
    noSample: idle && bondLoadState === LOAD_STATES.NO_SAMPLE,
    unavailable: idle && bondLoadState === LOAD_STATES.UNAVAILABLE,
  }
}

export function buildBondOrganViewModel({
  bondLoadState,
  bondUpdatedAt,
  health,
  users = [],
  stageSampleCount = 0,
  selectedUser,
  userDetail,
  labels = {},
} = {}) {
  return buildBondOrganSystemModel({
    loadState: bondLoadState,
    updatedAt: bondUpdatedAt,
    healthStatus: labels.healthStatus,
    selectedUser,
    userCount: health?.user_count ?? users.length,
    stageSampleCount,
    currentStageLabel: labels.currentStageLabel,
    trendLabel: labels.currentTrendLabel,
    totalScore: userDetail?.total_score,
    eventCount: userDetail?.event_count,
    capabilityHint: userDetail?.capability_hint,
    lastEventLabel: labels.lastEventLabel,
  })
}

export function buildBondMetricCards({ health, users = [], stageSampleCount = 0, userDetail = null, labels = {} } = {}) {
  return [
    {
      label: '引擎状态',
      value: labels.healthStatus,
      hint: `用户 ${health?.user_count ?? users.length}`,
      tone: 'text-green-600',
    },
    {
      label: '当前阶段',
      value: labels.currentStageLabel,
      hint: `趋势 ${labels.currentTrendLabel}`,
      tone: 'text-pink-600',
    },
    {
      label: '历史积分',
      value: userDetail?.total_score ?? '—',
      hint: `事件 ${userDetail?.event_count ?? 0} 次`,
      tone: 'text-purple-600',
    },
    {
      label: '最近事件',
      value: labels.lastEventLabel,
      hint: userDetail?.capability_hint || `阶段样本 ${stageSampleCount}`,
      tone: 'text-blue-600',
    },
  ]
}

export function buildBondDetailRows(userDetail) {
  if (!userDetail) return []
  return [
    { label: '用户 ID', value: userDetail.user_id },
    { label: '当前阶段', value: formatStage(userDetail.current_stage) },
    { label: '阶段序号', value: userDetail.stage_index },
    { label: '累计积分', value: userDetail.total_score },
    { label: '事件次数', value: userDetail.event_count },
    { label: '趋势', value: formatTrend(userDetail.trend) },
    { label: '能力提示', value: userDetail.capability_hint },
    { label: '最近事件', value: formatDateTime(userDetail.last_event_ts_ms) },
  ]
}

export function buildBondDetailView({ detailError, detailUpdatedAt, userDetail } = {}) {
  return {
    errorMessage: detailError
      ? `${detailError}${detailUpdatedAt ? ` · 最近更新时间 ${formatDateTime(detailUpdatedAt)}` : ''}`
      : null,
    rows: buildBondDetailRows(userDetail),
  }
}

export function buildBondDashboardViewModel({
  health,
  users = [],
  stageDist = [],
  stageSampleCount = 0,
  overviewError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  selectedUser = null,
  userDetail = null,
  detailError = null,
  detailUpdatedAt = null,
} = {}) {
  const bondLoadState = buildBondLoadState({
    loading,
    refreshing,
    overviewError,
    health,
    users,
    stageDist,
    stageSampleCount,
    stale,
  })
  const bondUpdatedAt = getBondUpdatedAt(lastUpdatedAt, detailUpdatedAt)
  const labels = buildBondLabels({ health, userDetail })

  return {
    bondLoadState,
    bondUpdatedAt,
    labels,
    heroView: buildBondHeroView({ selectedUser, health, users, stageSampleCount }),
    sampleMeta: buildBondSampleMeta({ bondLoadState, bondUpdatedAt, stageSampleCount }),
    noticeView: buildBondNoticeView({ overviewError, loading, refreshing, bondLoadState }),
    organModel: buildBondOrganViewModel({
      bondLoadState,
      bondUpdatedAt,
      health,
      users,
      stageSampleCount,
      selectedUser,
      userDetail,
      labels,
    }),
    metricCards: buildBondMetricCards({ health, users, stageSampleCount, userDetail, labels }),
    detailView: buildBondDetailView({ detailError, detailUpdatedAt, userDetail }),
  }
}
