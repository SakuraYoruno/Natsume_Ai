import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildEmotionDashboardViewModel,
  buildEmotionGaugeView,
  buildEmotionHormoneBarView,
  getEmotionSampleState,
} from './emotionDashboardModel.js'
import { buildDigitalLifeWiredDomains } from './digitalLifeWiredDomainsModel.js'
import {
  buildEmotionMemorySubsystemRows,
  buildEmotionMetricCards,
  buildEmotionVtsObservation,
  buildEmotionVtsStatCards,
} from './emotionDashboardViews.js'
import { buildMemorySubsystemMetricCards } from './memoryBrowserModel.js'
import { buildVitalSigns } from '../../models/lifeBodyState.js'
import {
  OBS_RESOURCE_AVAILABILITY,
  buildObservabilityResourceState,
  formatObservableCount,
  formatObservablePercent,
  settleObservabilityResources,
} from './observabilityResourceState.js'

test('D6 keeps typed 503 unavailable and ordinary request errors distinct', () => {
  const unavailable = buildObservabilityResourceState({
    key: 'vtsData',
    source: 'avatar_action_log',
    result: {
      status: 'rejected',
      reason: {
        status: 503,
        message: 'Service Unavailable',
        payload: { detail: { reason_code: 'PROJECTION_SOURCE_UNAVAILABLE' } },
      },
    },
  })
  const failed = buildObservabilityResourceState({
    key: 'memoryData',
    source: 'memory_ops_log',
    result: {
      status: 'rejected',
      reason: { status: 500, message: 'broken projection' },
    },
  })

  assert.equal(unavailable.availability, OBS_RESOURCE_AVAILABILITY.UNAVAILABLE)
  assert.equal(unavailable.reasonCode, 'PROJECTION_SOURCE_UNAVAILABLE')
  assert.equal(unavailable.httpStatus, 503)
  assert.equal(failed.availability, OBS_RESOURCE_AVAILABILITY.ERROR)
  assert.equal(failed.message, 'broken projection')
})

test('D6 partial observability result preserves good payload and renderable issue', async () => {
  const settled = await settleObservabilityResources([
    { key: 'memoryData', source: 'memory_ops_log', request: async () => ({ total_writes: 2 }) },
    {
      key: 'vtsData',
      source: 'avatar_action_log',
      request: async () => {
        const error = new Error('PROJECTION_SOURCE_UNAVAILABLE')
        error.status = 503
        throw error
      },
    },
  ])

  assert.equal(settled.resources.memoryData.data.total_writes, 2)
  assert.equal(settled.resources.memoryData.available, true)
  assert.equal(settled.issues.length, 1)
  assert.equal(settled.issues[0].key, 'vtsData')
  assert.equal(settled.issues[0].availability, OBS_RESOURCE_AVAILABILITY.UNAVAILABLE)
})

test('D6 nullable measurements never render as zero percent or dash percent', () => {
  assert.equal(formatObservableCount(null), '不可测')
  assert.equal(formatObservablePercent(null), '不可测')

  const vtsCards = buildEmotionVtsStatCards({
    total_events: 0,
    apply_rate: null,
    degrade_rate: null,
    talk_gate_total: 0,
    talk_gate_allowed: 0,
    talk_gate_blocked: 0,
  })
  assert.equal(vtsCards[0].value, '无样本')
  assert.equal(vtsCards[1].value, '无样本')
  assert.ok(vtsCards.every((card) => card.value !== '0%' && card.value !== '—%'))

  const metricCards = buildEmotionMetricCards({ vtsData: null })
  assert.equal(metricCards.at(-1).value, '不可用')
  assert.equal(metricCards.at(-1).hint, 'VTS 数据源不可用')
  assert.equal(getEmotionSampleState({ vtsData: null }).sampleCount, null)
})

test('D6 empty runtime emotion snapshot stays nullable through every dashboard view', () => {
  const model = buildEmotionDashboardViewModel({
    runtimeSnapshot: null,
    vtsData: null,
    memoryData: null,
    resourceStates: {
      vtsData: {
        available: false,
        availability: OBS_RESOURCE_AVAILABILITY.UNAVAILABLE,
        source: 'avatar_action_log',
        reasonCode: 'PROJECTION_SOURCE_UNAVAILABLE',
      },
    },
    resourceIssues: [{
      key: 'vtsData',
      available: false,
      availability: OBS_RESOURCE_AVAILABILITY.UNAVAILABLE,
      message: 'PROJECTION_SOURCE_UNAVAILABLE',
    }],
  })

  assert.equal(model.runtimeEmotionAvailable, false)
  assert.deepEqual(model.emotionState, {
    valence: null,
    arousal: null,
    dominance: null,
    mood: null,
    emotionTag: null,
    bondStage: null,
  })
  assert.equal(model.emotionTagLabel, '待判定')
  assert.equal(model.bondStageLabel, '待判定')
  assert.equal(model.gateView.gateLabel, '待判定')
  assert.equal(model.bondMetrics.bondTemperatureLabel, '不可测')
  assert.ok(model.gauges.every((item) => item.value == null && item.valueLabel === '不可测' && item.available === false))
  assert.ok(model.hormoneBars.every((item) => item.value == null && item.valueLabel === '不可测' && item.available === false))
  assert.equal(model.metricCards[0].value, '待判定')
  assert.equal(model.metricCards.at(-1).value, '不可用')
  assert.equal(model.metricCards.at(-1).hint, 'VTS 数据源不可用')
  assert.ok(model.limbicOrganModel.symptoms.every((item) => !item.includes('100%') && !item.includes('0°')))

  assert.equal(buildEmotionGaugeView({ label: '效价', value: 0, color: 'text-green-600' }).valueLabel, '0.00')
  assert.equal(buildEmotionHormoneBarView({ label: 'Cortisol', value: 0, color: '#000' }).valueLabel, '0.00')
})

test('D6 life body handles absent background runtime and nullable VTS degrade rate', () => {
  const signs = buildVitalSigns({
    lifeState: { label: '稳定', tone: 'stable' },
    hasRuntimeSnapshot: true,
    currentEmotion: '待判定',
    currentBondStage: '待判定',
    bondTrend: '—',
    avgMemorySuccessRate: null,
    hasMemoryData: false,
    hasMemorySamples: false,
    sampledMemorySubsystemCount: 0,
    configuredMemorySubsystemCount: 0,
    riskLevel: '—',
    humanoidLoopData: null,
    backgroundRuntime: null,
    nonLiveSlots: [],
    vtsData: { degrade_rate: null },
  })
  const continuity = signs.find((item) => item.id === 'continuity')
  const emotion = signs.find((item) => item.id === 'emotion')

  assert.equal(continuity.value, '连续')
  assert.equal(continuity.tone, 'stable')
  assert.equal(emotion.value, '不可测')
  assert.equal(emotion.tone, 'pending')
})

test('D6 wired-domain summary does not turn nullable VTS rate into zero percent', () => {
  const domains = buildDigitalLifeWiredDomains({
    hasRuntimeSnapshot: true,
    runtimeSnapshot: { slot_status: { emotion_context: 'live' } },
    topEmotion: { emotion_tag: 'HAPPY' },
    currentEmotion: '高兴',
    vtsData: { degrade_rate: null },
  })
  const emotion = domains.find((item) => item.title === '情绪与表情')

  assert.match(emotion.desc, /VTS 降级率 不可测/)
  assert.doesNotMatch(emotion.desc, /VTS 降级率 0%/)
})

test('D6 memory and VTS views expose durable source and honest availability', () => {
  const subsystem = {
    subsystem: 'long_term_distiller',
    display_name: 'LongTermDistiller 蒸馏周期',
    total: null,
    success_rate: null,
    extra: { source_table: 'error_log', observed_terminal_events: 3 },
  }
  const memoryCards = buildMemorySubsystemMetricCards([subsystem])
  const emotionRows = buildEmotionMemorySubsystemRows({ subsystems: [subsystem] })
  const observation = buildEmotionVtsObservation(null, {
    available: false,
    availability: OBS_RESOURCE_AVAILABILITY.UNAVAILABLE,
    source: 'avatar_action_log',
    reasonCode: 'PROJECTION_SOURCE_UNAVAILABLE',
  })

  assert.equal(memoryCards[0].value, '不可测')
  assert.match(memoryCards[0].hint, /error_log/)
  assert.match(memoryCards[0].hint, /3 个终态审计事件/)
  assert.equal(emotionRows[0].successRate, '不可测')
  assert.equal(emotionRows[0].source, 'error_log')
  assert.equal(observation.source, 'avatar_action_log')
  assert.equal(observation.availability, '数据源暂不可用')
})

test('D6 memory surface consumes the new backend field names', async () => {
  const source = await readFile(new URL('./components/MemoryBrowserSurface.jsx', import.meta.url), 'utf8')
  for (const required of [
    'total_attempts',
    'written_count',
    'write_rate',
    'memory_guard_blocked_count',
    'total_proposals_submitted',
    'failure_measurement_available',
    'observed_terminal_events',
    'source_table',
  ]) {
    assert.match(source, new RegExp(required))
  }
  for (const retired of [
    'total_proposals ??',
    'guard_block_rate',
    'distill_eligible_rate',
    'dream_block_count',
  ]) {
    assert.doesNotMatch(source, new RegExp(retired.replace(/[?]/g, '\\?')))
  }
})

test('D6 Memory and VTS queries retain per-resource failures instead of swallowing them', async () => {
  const [memoryQueries, emotionQueries] = await Promise.all([
    readFile(new URL('./memoryQueries.js', import.meta.url), 'utf8'),
    readFile(new URL('./emotionQueries.js', import.meta.url), 'utf8'),
  ])
  for (const source of [memoryQueries, emotionQueries]) {
    assert.match(source, /settleObservabilityResources/)
    assert.match(source, /resourceStates/)
    assert.match(source, /resourceIssues/)
    assert.doesNotMatch(source, /\.catch\(\(\) => null\)/)
  }
})
