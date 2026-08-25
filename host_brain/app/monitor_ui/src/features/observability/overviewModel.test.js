import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildOverviewEvidenceHandoff,
  buildOverviewHeroMetaItems,
  buildOverviewPulseNodes,
  buildOverviewQueryView,
  getActiveSubsystems,
  getOverviewServiceGroups,
  getSubsystemFootnote,
  getSubsystemHeadline,
} from './overviewModel.js'

test('远端外周服务未启动时单独展示未验收数量', () => {
  const data = {
    total: 17,
    up: 2,
    degraded: 0,
    down: 0,
    not_tested: 5,
    unknown: 10,
  }

  const pulse = buildOverviewPulseNodes({
    wsConnected: false,
    data,
    runningCount: 0,
  })
  const meta = buildOverviewHeroMetaItems({ data, activeSubsystemCount: 0 })

  assert.equal(pulse[1].detail, '5 项未验收')
  assert.ok(meta.includes('未验收：5'))
  assert.ok(meta.includes('未知：10'))
})

test('子系统摘要保留失败、降级、拦截并按状态排序', () => {
  assert.equal(getSubsystemHeadline({ total: 0 }), '当前窗口无调用')
  assert.equal(getSubsystemHeadline({ total: 2, detail: '上游超时' }), '上游超时')
  assert.equal(getSubsystemHeadline({ total: 2, success_rate: 50 }), '成功率 50%')
  assert.equal(getSubsystemFootnote({ total: 4, success: 1, degraded: 1, failed: 1, blocked: 1 }), '成功 1/4 · 降级 1 · 失败 1 · 拦截 1')

  const groups = getOverviewServiceGroups([
    { service_id: 'edge', service_type: 'edge' },
    { service_id: 'host', service_type: 'host' },
  ])
  assert.deepEqual(groups.edgeServices.map((item) => item.service_id), ['edge'])
  assert.deepEqual(groups.llmNodes.map((item) => item.service_id), ['host'])

  const active = getActiveSubsystems({ subsystems: [
    { subsystem: 'healthy', total: 2, success: 2, success_rate: 100, last_ts: 20 },
    { subsystem: 'broken', total: 1, success: 0, failed: 1, success_rate: 0, last_ts: 10 },
    { subsystem: 'idle', total: 0 },
  ] })
  assert.deepEqual(active.map((item) => item.subsystem), ['broken', 'healthy'])
  assert.equal(active[0].normalizedStatus, 'down')
})

test('总览优先把离线服务交接到 Alerts 并保留真实样本数', () => {
  const view = buildOverviewQueryView({
    wsConnected: true,
    liveHealthData: {
      total: 2,
      up: 1,
      degraded: 0,
      down: 1,
      not_tested: 0,
      unknown: 0,
      services: [
        { service_id: 'host', display_name: 'Host', service_type: 'host', status: 'UP' },
        { service_id: 'edge', display_name: 'Edge', service_type: 'edge', status: 'DOWN' },
      ],
    },
    healthData: { total: 99 },
    liveHealthUpdatedAt: 30,
    lastUpdatedAt: 20,
    subsysData: { subsystems: [{ subsystem: 'chat', display_name: '对话', total: 3, success: 2, failed: 1, last_ts: 40 }] },
    agentLoopData: { running_count: 1, failed: 0, total_loops: 3 },
    wsAlerts: [{ reason_code: 'EDGE_DOWN' }],
    windowMinutes: 60,
  })

  assert.equal(view.data.total, 2)
  assert.equal(view.overviewUpdatedAt, 30)
  assert.equal(view.edgeServices.length, 1)
  assert.equal(view.llmNodes.length, 1)
  assert.equal(view.sampleMeta.sampleSize, 3)
  assert.equal(view.sampleMeta.source, 'health websocket + overview queries')
  assert.equal(view.evidenceHandoff.primaryKind, 'service')
  assert.equal(view.evidenceHandoff.actionTo, '/observability/alerts')
  assert.match(view.evidenceHandoff.title, /Edge/)
  assert.ok(view.evidenceHandoff.routeCards.some((item) => item.key === 'alerts' && item.state === 'primary'))
})

test('总览在服务正常时依次交接护栏、活跃子系统和样本', () => {
  const base = {
    data: { total: 1, up: 1, degraded: 0, down: 0, services: [{ service_id: 'host', status: 'UP' }] },
    sampleMeta: { sampleSize: 2, source: 'queries' },
    wsAlerts: [],
  }
  const guard = buildOverviewEvidenceHandoff({ ...base, agentLoopData: { failed: 1 } })
  assert.equal(guard.primaryKind, 'guard')
  assert.equal(guard.actionTo, '/observability/metrics')

  const subsystem = buildOverviewEvidenceHandoff({
    ...base,
    activeSubsystems: [{ subsystem: 'memory', display_name: '记忆', total: 4, success: 4, normalizedStatus: 'up' }],
    agentLoopData: { failed: 0 },
  })
  assert.equal(subsystem.primaryKind, 'subsystem')
  assert.equal(subsystem.actionTo, '/observability/trace')

  const samples = buildOverviewEvidenceHandoff({ ...base, agentLoopData: { failed: 0 } })
  assert.equal(samples.primaryKind, 'samples')
  assert.equal(samples.actionTo, '/observability/sessions')
})
