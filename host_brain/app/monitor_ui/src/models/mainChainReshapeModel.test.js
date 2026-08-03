import assert from 'node:assert/strict'
import test from 'node:test'

import { buildMainChainReshapeModel } from './mainChainReshapeModel.js'

test('当前主链使用实时器官与后台 tick，不依赖历史 soak 文件', () => {
  const model = buildMainChainReshapeModel({
    runtimeSnapshot: {
      background_runtime: {
        scheduler_status: 'HEALTHY',
        scheduler_active: true,
        is_advancing: true,
        tick_health: 'fresh',
        seconds_since_inner_world_tick: 4,
      },
    },
    organTribeData: {
      total_agents: 23,
      active_agents: 7,
      projection: { degraded_state: 'HEALTHY' },
    },
    hasRuntimeSnapshot: true,
    hasMemoryData: true,
    hasMemorySamples: true,
    currentEmotion: '平静',
    humanoidLoopData: { total_gateway_requests: 0 },
  })

  assert.equal(model.stages.find((item) => item.id === 'organs')?.status, 'healthy')
  assert.equal(model.stages.find((item) => item.id === 'tick')?.status, 'healthy')
  assert.deepEqual(
    model.metrics.find((item) => item.label === '器官族'),
    { label: '器官族', value: '23/23', hint: '活跃 7/23' }
  )
  assert.deepEqual(
    model.metrics.find((item) => item.label === '后台 tick'),
    { label: '后台 tick', value: 'fresh', hint: 'HEALTHY · 距今 4s' }
  )
})

test('失源的实时器官投影不会伪装成健康', () => {
  const model = buildMainChainReshapeModel({
    organTribeData: {
      total_agents: 23,
      active_agents: 0,
      projection: { degraded_state: 'SOURCE_UNAVAILABLE' },
    },
  })

  assert.equal(model.stages.find((item) => item.id === 'organs')?.status, 'unknown')
})

test('Host 调度器投影可直接恢复后台 tick，不要求历史 soak 或聚合快照复制字段', () => {
  const now = Date.now()
  const model = buildMainChainReshapeModel({
    runtimeSnapshot: { background_runtime: {} },
    organTribeData: {
      total_agents: 23,
      active_agents: 0,
      projection: { degraded_state: 'HEALTHY' },
      host_scheduler: {
        clock_active: true,
        clock: {
          active: true,
          last_tick_ms: now - 4_000,
          next_retry_at_ms: now + 60_000,
        },
      },
    },
  })

  const tick = model.stages.find((item) => item.id === 'tick')
  const metric = model.metrics.find((item) => item.label === '后台 tick')
  assert.equal(tick?.status, 'healthy')
  assert.equal(metric?.value, 'fresh')
  assert.match(metric?.hint || '', /^HEALTHY · 距今 [0-9]+s$/)
})
