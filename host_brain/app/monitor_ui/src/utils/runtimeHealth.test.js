import assert from 'node:assert/strict'
import test from 'node:test'

import { countNotLiveRuntimeSlots } from '../features/operations/systemConfigModel.js'
import {
  buildDigitalLifeAlerts,
  getDigitalLifeRuntimeSummary,
  getNonLiveSlots,
  getRuntimeSlotCount,
} from './runtimeHealth.js'

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

test('digital life alerts expose every real degraded organ without neutralizing severity', () => {
  const snapshot = {
    timestamp: 1_700_000_000_000,
    sys_mode_status: { degraded: true, degraded_reason: 'scheduler stalled' },
    degraded_reasons: [{ reason_code: 'MEMORY_STALE', detail: 'memory stale' }],
    slot_status: { sys_mode: 'live', memory: 'degraded' },
    self_reflection_pulse: { narrative_guardrails: { risk_level: 'critical', risk_tags: ['dependency'] } },
    silence_state: { topic_resistance_active: true, resistant_topic: '加班' },
    render_policy: { should_be_withdrawn: true },
    runtime_summary: { reply_posture: { style: 'gentle' } },
    background_runtime: {
      scheduler_status: 'stopped',
      tick_health: 'stalled',
      is_advancing: false,
      seconds_since_inner_world_tick: 90,
      core_ticks_missing: ['inner_world', 'relationship'],
      persona_user_alignment_ok: false,
    },
    interoception_state: { interoceptive_index: 0.9, recovery_urgency: 0.86 },
    vestibular_state: { stability_state: 'critical', jitter_index: 0.9 },
    motor_cortex_state: { last_plan_id: 'plan-1', last_dispatch_ok: false, last_dispatch_reason: 'edge offline' },
    homeostasis_report: { status: 'critical', minute_window: { avg_cognitive_load_index: 0.8 }, hour_window: { avg_stress_index: 0.7 } },
  }
  const alerts = buildDigitalLifeAlerts(snapshot, {
    promptStats: { injection_detected_count: 2, high_risk_count: 1 },
    capabilityGapData: { forge_runtime_quality: { summary: { critical_proposals: 1, unhealthy_proposals: 2, tracked_proposals: 3, avg_health_score: 41.2 } } },
  })
  const reasons = new Set(alerts.map((item) => item.reason_code))

  for (const reason of [
    'RUNTIME_MODE_DEGRADED',
    'RUNTIME_SNAPSHOT_DEGRADED',
    'RUNTIME_SLOT_NOT_LIVE',
    'REFLECTION_GUARD_ACTIVE',
    'TOPIC_RESISTANCE_ACTIVE',
    'WITHDRAWN_REPLY_POLICY',
    'PROMPT_INJECTION_DETECTED',
    'PROMPT_HIGH_RISK_BUILD',
    'BACKGROUND_RUNTIME_NOT_ADVANCING',
    'BACKGROUND_CORE_TICK_MISSING',
    'BACKGROUND_PERSONA_MISMATCH',
    'INTEROCEPTION_OVERLOAD',
    'VESTIBULAR_INSTABILITY',
    'MOTOR_DISPATCH_DEGRADED',
    'HOMEOSTASIS_WARNING',
    'FORGE_RUNTIME_CRITICAL_PROPOSALS',
  ]) {
    assert.equal(reasons.has(reason), true, reason)
  }
  assert.equal(alerts[0].severity, 'critical')
  assert.equal(alerts.at(-1).severity, 'info')
})

test('forge unhealthy branch and digital-life headline preserve relational context', () => {
  const alerts = buildDigitalLifeAlerts({ timestamp: 1 }, {
    capabilityGapData: { forge_runtime_quality: { summary: { critical_proposals: 0, unhealthy_proposals: 2, tracked_proposals: 5, avg_health_score: 68 } } },
  })
  assert.equal(alerts[0].reason_code, 'FORGE_RUNTIME_UNHEALTHY_PROPOSALS')
  assert.equal(buildDigitalLifeAlerts(null).length, 0)

  const summary = getDigitalLifeRuntimeSummary({
    sys_mode: 'focus',
    bond_state: { current_stage: 'friend' },
    motive_state: { dominant_motive: 'connect' },
    offline_state: { absence_band: 'long' },
    self_reflection_pulse: { narrative_guardrails: { risk_level: 'medium' } },
    runtime_summary: {
      reply_posture: { style: 'gentle' },
      notable_signals: ['memory_recalled'],
    },
  })
  assert.match(summary.headline, /关系阶段/)
  assert.match(summary.headline, /主导动机/)
  assert.equal(summary.notableSignals.length, 1)
})
