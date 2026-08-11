import assert from 'node:assert/strict'
import test from 'node:test'
import { buildLifeEvolutionModel, formatLifeConfidence, formatLifeTtl } from './lifeEvolutionModel.js'

const payload = {
  schema_version: 'Natsume.life_evolution.view.v1',
  view_id: 'view-1',
  generated_at_ms: 1_800_200_000_000,
  read_only: true,
  write_api_available: false,
  world: {
    status: 'DEGRADED',
    facts: [
      {
        key: 'observed.fact',
        value: 'direct',
        fact_class: 'FACT',
        source: 'sensor',
        source_ref: 'sensor://1',
        confidence: 0.97,
        ttl_remaining_ms: 90_000,
        display_state: 'CURRENT',
      },
      {
        key: 'inferred.fact',
        value: 'derived',
        fact_class: 'INFERENCE',
        source: 'arbiter',
        source_ref: 'arbiter://1',
        confidence: 0.42,
        ttl_remaining_ms: 0,
        expired: true,
        conflicted: true,
        display_state: 'EXPIRED',
        conflict_state: 'CONFLICT',
      },
    ],
  },
  appraisals: {
    records: [{
      appraisal_id: 'appraisal-1',
      source_event_ref: 'episode://1',
      summary: { kind: 'feedback', confidence: 0.75, epistemic_type: 'inferred' },
      bond_signal: { direction: 'respect_boundary', permission_effect: 'NONE' },
      evidence_refs: ['turn://1'],
    }],
    private_body_included: false,
  },
  bond: {
    status: 'OK',
    authority_label: 'PROJECTION_ONLY',
    authoritative: false,
    permission_effect: 'NONE',
    compatibility: { current_stage: 'FRIEND', total_score: 10, trend: 'stable' },
    relationship: {},
  },
  scene: {
    decision_id: 'scene-1',
    source_refs: ['environment://1'],
    confidence: 0.7,
    ttl_remaining_ms: 20_000,
    recorded_proactive_policy: 'BLOCK',
    proactive_policy: 'ALLOW',
    permission_effect: 'RESTRICT_ONLY',
  },
  action_preview: {
    status: 'DRY_RUN',
    decision: 'BLOCK',
    execution_state: 'BLOCKED',
    would_execute: false,
    execution_performed: false,
    why_now: ['ARBITER_GOVERNOR_BLOCKED'],
    finality: 'GOVERNED_DRY_RUN',
  },
}

test('T-NLE-22 keeps fact and inference labels, provenance, confidence, TTL, expiry and conflict', () => {
  const model = buildLifeEvolutionModel(payload)
  assert.equal(model.available, true)
  assert.equal(model.readOnly, true)
  assert.equal(model.world.facts[0].factClassLabel, '事实')
  assert.equal(model.world.facts[0].sourceRef, 'sensor://1')
  assert.equal(model.world.facts[0].confidence, '97%')
  assert.equal(model.world.facts[0].ttl, '1m 30s')
  assert.equal(model.world.facts[1].factClassLabel, '推断')
  assert.equal(model.world.facts[1].displayStateLabel, '已过期')
  assert.equal(model.world.facts[1].conflicted, true)
  assert.equal(model.world.conflictCount, 1)
  assert.equal(model.world.expiredCount, 1)
})

test('T-NLE-22 presents Appraisal evidence and Bond as permission-neutral projection', () => {
  const model = buildLifeEvolutionModel(payload)
  assert.deepEqual(model.appraisals[0].evidenceRefs, ['turn://1'])
  assert.equal(model.appraisals[0].epistemicType, 'inferred')
  assert.equal(model.appraisalPrivateBodyIncluded, false)
  assert.equal(model.bond.authorityLabel, 'PROJECTION_ONLY')
  assert.equal(model.bond.authoritative, false)
  assert.equal(model.bond.permissionEffect, 'NONE')
})

test('T-NLE-22 shows recorded versus effective Scene policy and final dry-run reasoning', () => {
  const model = buildLifeEvolutionModel(payload)
  assert.equal(model.scene.recordedPolicy, 'BLOCK')
  assert.equal(model.scene.effectivePolicy, 'ALLOW')
  assert.equal(model.scene.permissionEffect, 'RESTRICT_ONLY')
  assert.equal(model.preview.decision, 'BLOCK')
  assert.equal(model.preview.executionPerformed, false)
  assert.deepEqual(model.preview.whyNow, ['ARBITER_GOVERNOR_BLOCKED'])
})

test('formatters are bounded and honest about missing expiry', () => {
  assert.equal(formatLifeConfidence(2), '100%')
  assert.equal(formatLifeConfidence('bad'), '--')
  assert.equal(formatLifeTtl({ ttl_remaining_ms: null }), '未声明到期时间')
  assert.equal(formatLifeTtl({ expired: true, ttl_remaining_ms: 0 }), '已过期')
})
