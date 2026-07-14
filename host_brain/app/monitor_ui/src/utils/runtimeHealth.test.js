import assert from 'node:assert/strict'
import test from 'node:test'

import { countNotLiveRuntimeSlots } from '../features/operations/systemConfigModel.js'
import { buildDigitalLifeAlerts, getNonLiveSlots, getRuntimeSlotCount } from './runtimeHealth.js'

const privacyEnvelope = {
  schema_version: 'natsume.operator-query.v1',
  redacted: true,
  withheld: [{
    field: 'bond_narrative',
    citation_ref: 'operator-private-bond-narrative://sha256/example',
  }],
}

test('operator privacy metadata is not treated as a degraded runtime slot', () => {
  const snapshot = {
    timestamp: 1_700_000_000_000,
    slot_status: {
      sys_mode: 'live',
      memory: 'degraded',
      _operator_privacy: privacyEnvelope,
    },
  }

  assert.deepEqual(getNonLiveSlots(snapshot), [['memory', 'degraded']])
  assert.equal(getRuntimeSlotCount(snapshot), 2)
  assert.equal(countNotLiveRuntimeSlots(snapshot), 1)

  const slotAlerts = buildDigitalLifeAlerts(snapshot).filter(
    (item) => item.reason_code === 'RUNTIME_SLOT_NOT_LIVE',
  )
  assert.equal(slotAlerts.length, 1)
  assert.match(slotAlerts[0].detail, /memory/i)
  assert.doesNotMatch(slotAlerts[0].detail, /operator_privacy|\[object Object\]/)
})

test('privacy-only slot metadata does not create a false degradation alert', () => {
  const snapshot = {
    timestamp: 1_700_000_000_000,
    slot_status: {
      sys_mode: 'live',
      _operator_privacy: privacyEnvelope,
    },
  }

  assert.deepEqual(getNonLiveSlots(snapshot), [])
  assert.equal(getRuntimeSlotCount(snapshot), 1)
  assert.equal(countNotLiveRuntimeSlots(snapshot), 0)
  assert.equal(
    buildDigitalLifeAlerts(snapshot).some(
      (item) => item.reason_code === 'RUNTIME_SLOT_NOT_LIVE',
    ),
    false,
  )
})
