import { buildLifeTimelinePath } from './lifeBodyRoutes.js'
import { buildPathologyDetailPath } from './lifeBodyShared.js'

export function buildDiagnosticGuidance({
  lifeState,
  primaryOrgan,
  primaryPathology,
  timelineEvents,
}) {
  const actions = []

  if (primaryPathology) {
    actions.push({
      id: 'pathology',
      tone: primaryPathology.severity || 'warning',
      eyebrow: '主病灶',
      title: `${primaryPathology.organLabel} · ${primaryPathology.title}`,
      detail: primaryPathology.symptom,
      impact: primaryPathology.impactLabel,
      to: primaryPathology.detailPath || buildPathologyDetailPath(primaryPathology.id),
      label: '进入病灶详情',
      secondaryTo: buildLifeTimelinePath({ organId: primaryPathology.organId, type: 'pathology' }),
      secondaryLabel: '查看病灶事件链',
    })
  }

  if (primaryOrgan) {
    actions.push({
      id: 'organ',
      tone: primaryOrgan.status || 'pending',
      eyebrow: '首要器官',
      title: primaryOrgan.name,
      detail: primaryOrgan.summary,
      impact: (primaryOrgan.downstreamImpacts || [])[0] || '继续沿对应器官页判断当前行为影响。',
      to: primaryOrgan.drilldownPath,
      label: primaryOrgan.drilldownLabel || '查看器官详情',
      secondaryTo: buildLifeTimelinePath({ organId: primaryOrgan.id }),
      secondaryLabel: '查看相关生命事件',
    })
  }

  const leadEvent = (timelineEvents || [])[0] || null
  if (leadEvent) {
    actions.push({
      id: 'timeline',
      tone: leadEvent.severity || 'info',
      eyebrow: '最近事件',
      title: leadEvent.title,
      detail: leadEvent.detail,
      impact: `所属器官 ${leadEvent.organLabel || '未分类'}，适合沿统一事件链回看状态是如何升温的。`,
      to: leadEvent.to || buildLifeTimelinePath({ organId: leadEvent.organId, type: leadEvent.type }),
      label: leadEvent.linkLabel || '查看事件详情',
      secondaryTo: buildLifeTimelinePath({ organId: leadEvent.organId, type: leadEvent.type }),
      secondaryLabel: '进入生命时间轴',
    })
  }

  if (!actions.length) {
    actions.push({
      id: 'recovery',
      tone: lifeState?.tone === 'critical' ? 'critical' : 'pending',
      eyebrow: '观测恢复',
      title: '先恢复生命体快照',
      detail: '当前还没有足够的运行快照、病灶或事件样本，因此驾驶舱无法给出可靠的首要器官与主病灶判断。',
      impact: '先恢复 runtime / memory / gateway 观测后，首页的器官排序、病灶优先级和事件链建议才会重新变得可信。',
      to: '/life/timeline',
      label: '查看生命时间轴',
      secondaryTo: '/life/runtime-mode',
      secondaryLabel: '查看存在态',
    })
  }

  const topActions = actions.slice(0, 3)
  const summary = primaryPathology
    ? `当前最该先处理的是 ${primaryPathology.organLabel} 的病灶，然后回到 ${primaryOrgan?.name || '对应器官'} 和生命事件链确认影响范围。`
    : primaryOrgan
      ? `当前最值得先看的器官是 ${primaryOrgan.name}，再结合最近事件链判断它是否已经开始影响整体主体状态。`
      : lifeState?.tone === 'stable'
        ? '当前生命体没有形成稳定病灶，建议先沿时间轴观察最近事件是否开始聚成异常模式。'
        : '当前观测样本仍不足，建议先恢复运行快照，再回到时间轴与病灶中心继续诊断。'

  return {
    summary,
    actions: topActions,
  }
}
