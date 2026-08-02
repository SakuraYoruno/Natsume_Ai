import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildOverviewHeroMetaItems,
  buildOverviewPulseNodes,
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
    uiAssertionData: null,
  })
  const meta = buildOverviewHeroMetaItems({ data, activeSubsystemCount: 0 })

  assert.equal(pulse[1].detail, '5 项未验收')
  assert.ok(meta.includes('未验收：5'))
  assert.ok(meta.includes('未知：10'))
})
