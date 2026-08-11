import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSecurityImmuneGateCards,
  buildSecurityImmuneMission,
  buildSecurityRuntimeBadge,
} from './securityImmuneModel.js'

function snapshot(available) {
  return {
    asset_count: 0,
    incident_count: 0,
    risk_counts: {},
    projection: {
      available,
      degraded_state: available ? 'HEALTHY' : 'SOURCE_UNAVAILABLE',
      reason_code: available ? null : 'OPERATOR_PROJECTION_SOURCE_UNAVAILABLE',
    },
  }
}

test('security immune never presents an unavailable durable projection as connected', () => {
  const unavailable = snapshot(false)

  assert.deepEqual(buildSecurityRuntimeBadge(unavailable), {
    status: 'degraded',
    label: 'degraded',
  })
  assert.equal(buildSecurityImmuneMission({
    runtime: unavailable,
    assets: unavailable,
    incidents: unavailable,
  }).title, 'Security Immune 降级运行')

  const cards = buildSecurityImmuneGateCards({
    runtime: unavailable,
    assets: unavailable,
    incidents: unavailable,
  })
  assert.equal(cards.find((card) => card.key === 'runtime').value, 'degraded')
  assert.equal(cards.find((card) => card.key === 'incidents').tone, 'orange')
})

test('security immune keeps healthy projections connected', () => {
  const available = snapshot(true)
  assert.deepEqual(buildSecurityRuntimeBadge(available), {
    status: 'healthy',
    label: 'connected',
  })
})
