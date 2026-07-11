import assert from 'node:assert/strict'
import test from 'node:test'
import { buildGovernanceViewModel } from './governanceModel.js'

function snapshot(overrides = {}) {
  return {
    schema_version: 'Natsume.governance.snapshot.v1',
    automatic_upgrade_available: false,
    capabilities: {
      items: [{
        capability_id: 'skill://search',
        source_kind: 'SKILL',
        maturity_level: 'L2',
        maturity_label: '验证中',
        canonical_risk: 3,
        static_maturity_ceiling: 4,
        evidence_refs: ['evidence://capability/search'],
        upgrade_candidate: {
          review_ref: 'review://upgrade/search',
          status: 'PENDING',
        },
      }],
    },
    degradation: {
      status: 'DEGRADED',
      reason_codes: ['MODEL_UNAVAILABLE'],
      restrictions: ['HIGH_RISK_DISABLED'],
      affected_capabilities: ['skill://search'],
      evidence_refs: ['event://degradation/1'],
      high_risk_latched: true,
      recovery: {
        status: 'REVIEW_REQUIRED',
        eligible: true,
        evidence_refs: ['probe://model/recovered'],
        review_ref: 'review://recovery/1',
      },
    },
    identity: {
      status: 'REVIEW_REQUIRED',
      current_snapshot_ref: 'identity://snapshot/current',
      release_ref: 'release://natsume/v1',
      private_body_included: false,
      diff: {
        severity: 'MAJOR',
        items: [{
          section: 'persona',
          field: 'boundary_digest',
          severity: 'MAJOR',
          reason_code: 'IDENTITY_BOUNDARY_CHANGED',
          previous_value: 'private-before',
          current_value: 'private-after',
        }],
      },
      review: {
        required: true,
        review_ref: 'review://identity/1',
        status: 'PENDING',
      },
      private_body: 'must-not-be-copied',
    },
    ...overrides,
  }
}

test('normalizes maturity, static risk, degradation evidence and review links', () => {
  const model = buildGovernanceViewModel(snapshot())
  assert.equal(model.available, true)
  assert.equal(model.safe, true)
  assert.deepEqual(model.capabilities[0], {
    valid: true,
    capabilityId: 'skill://search',
    sourceKind: 'SKILL',
    maturityLevel: 2,
    maturityCode: 'L2',
    maturityLabel: '验证中',
    canonicalRisk: 3,
    staticMaturityCeiling: 4,
    staticMaturityCeilingCode: 'L4',
    evidenceRefs: ['evidence://capability/search'],
    upgradeCandidate: {
      reviewRef: 'review://upgrade/search',
      status: 'PENDING',
      href: '/execution/task-runtime?review_id=review%3A%2F%2Fupgrade%2Fsearch',
    },
  })
  assert.equal(model.degradation.highRiskLatched, true)
  assert.deepEqual(model.degradation.recovery.evidenceRefs, ['probe://model/recovered'])
})

test('identity view is an allowlist and never carries private before/after values', () => {
  const model = buildGovernanceViewModel(snapshot())
  assert.equal(model.identity.privateBodyIncluded, false)
  assert.equal(model.identity.review.required, true)
  assert.match(model.identity.review.href, /review_id=/)
  assert.deepEqual(model.identity.diff.items[0], {
    section: 'persona',
    field: 'boundary_digest',
    severity: 'MAJOR',
    reasonCode: 'IDENTITY_BOUNDARY_CHANGED',
  })
  assert.equal(JSON.stringify(model).includes('private-before'), false)
  assert.equal(JSON.stringify(model).includes('must-not-be-copied'), false)
})

test('fails closed when automatic upgrade or private identity body is advertised', () => {
  const upgrade = buildGovernanceViewModel(snapshot({
    capabilities: { ...snapshot().capabilities, automatic_upgrade_available: true },
  }))
  assert.equal(upgrade.safe, false)
  assert.equal(upgrade.capabilities[0].upgradeCandidate.status, 'HIDDEN')
  assert.equal(upgrade.capabilities[0].upgradeCandidate.href, '')
  assert.match(upgrade.error, /自动升级/)

  const privateIdentity = buildGovernanceViewModel(snapshot({
    identity: { ...snapshot().identity, private_body_included: true },
  }))
  assert.equal(privateIdentity.safe, false)
  assert.equal(privateIdentity.identity, null)
  assert.match(privateIdentity.error, /私密正文/)
})

test('fails closed when identity is unavailable or a major diff lacks durable Review', () => {
  const unavailable = buildGovernanceViewModel(snapshot({
    identity: {
      status: 'UNAVAILABLE',
      private_body_included: false,
      diff: { severity: 'UNAVAILABLE', items: [] },
      review: { required: false, review_ref: null, status: 'UNAVAILABLE' },
    },
  }))
  assert.equal(unavailable.safe, false)
  assert.equal(unavailable.identity.available, false)
  assert.equal(unavailable.identity.review.status, 'UNAVAILABLE')
  assert.match(unavailable.error, /无法判定/)

  const missingReview = snapshot()
  missingReview.identity.review = { required: true, review_ref: '', status: 'PENDING' }
  const major = buildGovernanceViewModel(missingReview)
  assert.equal(major.safe, false)
  assert.equal(major.identity.review.bound, false)
  assert.equal(major.identity.review.href, '')
  assert.match(major.error, /未绑定 durable Review/)
})

test('rejects object refs and invalid maturity without coercion or clamping', () => {
  const bad = snapshot()
  bad.capabilities.items[0] = {
    ...bad.capabilities.items[0],
    capability_id: { private: 'id' },
    maturity_level: 99,
    static_maturity_ceiling: '4',
    canonical_risk: '3',
    evidence_refs: ['evidence://ok', { private: 'ref' }],
  }
  const model = buildGovernanceViewModel(bad)
  const item = model.capabilities[0]
  assert.equal(model.safe, false)
  assert.equal(item.capabilityId, 'unknown-capability')
  assert.equal(item.maturityLevel, null)
  assert.equal(item.maturityCode, 'UNKNOWN')
  assert.equal(item.staticMaturityCeilingCode, 'UNKNOWN')
  assert.equal(item.canonicalRisk, null)
  assert.deepEqual(item.evidenceRefs, ['evidence://ok'])
  assert.equal(JSON.stringify(model).includes('[object Object]'), false)
  assert.equal(item.upgradeCandidate.status, 'HIDDEN')
})

test('missing and 404-normalized snapshots hide both sections', () => {
  assert.deepEqual(buildGovernanceViewModel(null), {
    available: false,
    safe: true,
    error: null,
    automaticUpgradeAvailable: false,
    capabilities: [],
    degradation: null,
    identity: null,
  })
})
