import {
  LOAD_STATES,
  ORGAN_STATUS,
  createOrganSystemModel,
  metric,
} from './organSystemCore.js'

export function buildImmuneOrganSystemModel({
  loadState,
  updatedAt,
  totalAlerts,
  criticalCount,
  warningCount,
  infoCount,
  runtimeAlertCount,
  serviceAlertCount,
  errorEventCount,
  topReasonLabel,
  filterCode,
  representativeRefs = [],
}) {
  const status = loadState === LOAD_STATES.UNAVAILABLE || loadState === LOAD_STATES.LOADING
    ? ORGAN_STATUS.PENDING
    : criticalCount > 0
      ? ORGAN_STATUS.CRITICAL
      : warningCount > 0
        ? ORGAN_STATUS.WARNING
        : loadState === LOAD_STATES.NO_SAMPLE
          ? ORGAN_STATUS.PENDING
          : ORGAN_STATUS.STABLE

  return createOrganSystemModel({
    id: 'immune',
    name: '免疫系统',
    status,
    loadState,
    freshness: updatedAt,
    trend: filterCode ? '按病灶过滤' : '全量观察',
    riskLevel: criticalCount > 0 ? `${criticalCount} 条严重` : warningCount > 0 ? `${warningCount} 条警告` : '低',
    summary: totalAlerts > 0
      ? `当前窗口共汇总 ${totalAlerts} 条告警，优先级最高的是 ${topReasonLabel || '未分类告警'}。`
      : '当前窗口没有形成稳定的免疫告警。若页面为空，说明守卫与器官健康暂未触发异常。',
    symptoms: [
      `运行时告警 ${runtimeAlertCount || 0} · 器官异常 ${serviceAlertCount || 0} · 错误事件 ${errorEventCount || 0}`,
      `严重 ${criticalCount || 0} · 警告 ${warningCount || 0} · 提示 ${infoCount || 0}`,
      topReasonLabel ? `当前最热 reason：${topReasonLabel}` : '当前没有形成主导告警类型',
    ],
    downstreamImpacts: [
      '免疫系统会直接影响是否放行回复、是否触发保护姿态，以及是否打断外部执行链路。',
      filterCode
        ? `当前正在聚焦 ${filterCode}，适合沿病灶中心或 Trace 继续追。`
        : '建议先处理严重告警，再回看病灶中心与代表性 request。',
    ],
    metrics: [
      metric('总告警', String(totalAlerts || 0), '当前过滤结果', 'text-gray-900'),
      metric('严重', String(criticalCount || 0), '优先处理', 'text-red-600'),
      metric('警告', String(warningCount || 0), '建议跟进', 'text-orange-500'),
      metric('器官异常', String(serviceAlertCount || 0), 'health probe', 'text-purple-600'),
    ],
    representativeRefs,
    drilldownPath: '/life/pathologies',
    drilldownLabel: '进入病灶中心',
  })
}
