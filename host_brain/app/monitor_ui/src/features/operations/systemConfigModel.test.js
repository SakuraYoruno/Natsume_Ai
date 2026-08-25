import assert from 'node:assert/strict'
import test from 'node:test'

import { LOAD_STATES } from '../../models/loadState.js'
import {
  SYSTEM_CONFIG_WINDOW_OPTIONS,
  buildSystemConfigLoadState,
  buildSystemConfigViewModel,
  getSystemConfigCollections,
} from './systemConfigModel.js'

test('系统配置读模型把调用、限流和运行槽位编译成同一视图', () => {
  const callData = { models: [{ model_id: 'deepseek-v4-flash', call_count: 4, total_tokens: 1200, total_tokens_measured_calls: 3, total_tokens_is_lower_bound: true, avg_latency_ms: 1234.6 }] }
  const rateData = { backend_type: 'redis', is_degraded: false, top_keys: [{ key: 'chat', current: 2, limit: 10, remaining: 8 }] }
  const healthData = { up: 5, total: 7, degraded: 1, down: 0, unknown: 1 }
  const retrievalRuntime = {
    latest: {
      embedding_model_id: 'embed-read',
      effective_embedding_provider: 'chroma',
      fallback_embedding_used: true,
    },
  }
  const view = buildSystemConfigViewModel({
    callData,
    rateData,
    healthData,
    retrievalRuntime,
    runtimeSnapshot: {
      sys_mode: 'focus',
      runtime_summary: { reply_posture: { style: 'gentle' } },
      slot_status: { sys_mode: 'live', memory: 'degraded', _operator_privacy: { redacted: true } },
    },
    lastUpdatedAt: 10,
    runtimeUpdatedAt: 20,
  })

  assert.equal(view.loadState, LOAD_STATES.READY)
  assert.equal(view.notLiveSlots, 1)
  assert.equal(view.updatedAt, 20)
  assert.equal(view.sampleSize, 8)
  assert.match(view.heroDescription, /未在线槽位数量为 1/)
  assert.equal(view.callRows[0].latencyLabel, '1235 ms')
  assert.equal(view.callRows[0].totalTokensIsLowerBound, true)
  assert.equal(view.rateKeyRows[0].exhausted, false)
  assert.equal(view.retrievalRuntimeView.readEmbeddingProvider, 'chroma')
  assert.equal(view.metricCards.some((item) => item.key === 'write_embedding' || item.key === 'alignment'), false)
  assert.equal(view.metricCards.find((item) => item.key === 'read_embedding').value, 'embed-read')
  assert.ok(view.heroMetaItems.includes('限流后端：redis'))
})

test('系统配置读模型对缺省、过期、刷新和错误状态保持真实', () => {
  assert.deepEqual(getSystemConfigCollections(), { callStats: [], rateKeys: [] })
  assert.equal(buildSystemConfigLoadState(), LOAD_STATES.UNAVAILABLE)
  assert.equal(buildSystemConfigLoadState({ callData: {}, loading: true }), LOAD_STATES.REFRESHING)
  assert.equal(buildSystemConfigLoadState({ loading: true }), LOAD_STATES.LOADING)
  assert.equal(buildSystemConfigLoadState({ error: new Error('boom') }), LOAD_STATES.ERROR)
  assert.equal(buildSystemConfigLoadState({ callData: {}, stale: true }), LOAD_STATES.STALE)
  assert.equal(buildSystemConfigLoadState({ callData: {} }), LOAD_STATES.NO_SAMPLE)

  const empty = buildSystemConfigViewModel({
    runtimeSnapshot: { slot_status: {} },
    rateData: { top_keys: [{ current: 1, limit: 1, remaining: 0 }] },
  })
  assert.equal(empty.rateKeyRows[0].exhausted, true)
  assert.equal(empty.callRows.length, 0)
  assert.equal(empty.retrievalRuntimeView, null)
  assert.equal(empty.metricCards.some((item) => item.key === 'write_embedding' || item.key === 'alignment'), false)
  assert.equal(SYSTEM_CONFIG_WINDOW_OPTIONS.length, 4)
})
