import assert from 'node:assert/strict'
import test from 'node:test'
import { buildEvolutionLabViewModel } from './evolutionLabModel.js'

const d = (value) => String(value).repeat(64).slice(0, 64)

function arm(id, baseline) {
  return {
    run_id: `run-${id}`,
    execution_id: `execution-${id}`,
    receipt_digest: d(id),
    variant_id: baseline ? 'baseline' : 'candidate',
    is_baseline: baseline,
    policy_version: baseline ? 'policy.v1' : 'policy.v2',
    code_version: baseline ? 'code.v1' : 'code.v2',
    config_digest: d(baseline ? 'a' : 'b'),
    status: 'PASS',
    model_identity: 'deterministic-policy://v1',
    usage: { records: 5 },
    cost: { microusd: 0 },
    latency_ms: 20,
    sandbox_attestation_digest: d(baseline ? 'c' : 'd'),
    container_id: d(baseline ? 'e' : 'f'),
    verification_receipt_digest: d(baseline ? '1' : '2'),
    verification_status: 'PASS',
    verification_evidence_digest: d(baseline ? '3' : '4'),
    outcome: { quality: baseline ? 0.6 : 1 },
    risk: { policy_errors: 0 },
    metrics: { pass_rate: baseline ? 0.6 : 1 },
    production_side_effects: false,
    benefit_eligible: true,
    production_influence_enabled: false,
    created_at_ms: 10,
  }
}

function report(kind, index) {
  const baseline = arm(`${index}a`, true)
  const candidate = arm(`${index}b`, false)
  return {
    report_id: `report-${kind}`,
    report_digest: d(String(index)),
    experiment_kind: kind,
    workspace_id: 'workspace-ne04',
    experiment_id: `experiment-${kind}`,
    spec_digest: d(String(index + 3)),
    dataset_manifest_digest: d(String(index + 6)),
    dataset_source_kind: 'synthetic',
    dataset_record_count: 5,
    seed: 73,
    budget: { max_runs: 2 },
    verifier: { identity: `verifier://${kind}`, version: '1.0.0', digest: d('9') },
    isolation_profile_digest: d('8'),
    arms: [baseline, candidate],
    comparisons: [{
      baseline_run_id: baseline.run_id,
      alternative_run_id: candidate.run_id,
      outcome_delta: { quality: 0.4 },
      evidence_delta: { trusted_receipts: 0 },
      cost_delta: { microusd: 0 },
      latency_delta: { milliseconds: 0 },
      risk_delta: { policy_errors: 0 },
      verification_delta: { pass_rate: 0.4 },
    }],
    promotion_evidence_eligible: true,
    review_required: true,
    review_status: 'AWAITING_REVIEW',
    review_ref: null,
    proposals: [{
      proposal_id: `proposal-${kind}`,
      proposal_digest: d('7'),
      target_kind: `${kind}-policy`,
      target_version: 'candidate.v2',
      review_required: true,
      review_status: 'AWAITING_REVIEW',
      review_ref: null,
      production_influence_enabled: false,
      created_at_ms: 10,
    }],
    production_influence_enabled: false,
    created_at_ms: 10,
  }
}

function page() {
  const reports = [
    report('memory_write', 1),
    report('proactive', 2),
    report('research_planning', 3),
  ]
  return {
    schema_version: 'Natsume.evolution_lab.operator_page.v1',
    available: true,
    read_only: true,
    proposal_only: true,
    production_influence_enabled: false,
    items: [{
      suite_id: 'suite-ne04',
      commit_sha: 'a'.repeat(40),
      migration_head: '0140#evolution_lab_redacted_dataset',
      receipt_digest: d('6'),
      status: 'PASS',
      engine_identity: 'docker://engine/29.2.1',
      image_reference: `python@sha256:${d('5')}`,
      workspace_ids: ['workspace-ne04'],
      report_count: 3,
      run_count: 6,
      reports,
      production_side_effects: false,
      production_influence_enabled: false,
      created_at_ms: 10,
    }],
    next_cursor: null,
    has_more: false,
    page_size: 1,
    source_truncated: false,
  }
}

test('trusted Evolution Lab model renders only proposal-only evidence', () => {
  const model = buildEvolutionLabViewModel(page())
  assert.equal(model.safe, true)
  assert.equal(model.metrics.suites, 1)
  assert.equal(model.metrics.reports, 3)
  assert.equal(model.metrics.runs, 6)
  assert.equal(model.metrics.proposals, 3)
  assert.equal(model.suites[0].reports[0].proposals[0].reviewHref, '')
})

test('trusted Evolution Lab model rejects mutation authority and private bodies', () => {
  const mutation = page()
  mutation.production_influence_enabled = true
  assert.equal(buildEvolutionLabViewModel(mutation).safe, false)

  const privateBody = page()
  privateBody.items[0].reports[0].raw_output = { results: ['private'] }
  const privateModel = buildEvolutionLabViewModel(privateBody)
  assert.equal(privateModel.safe, false)
  assert.match(privateModel.error, /原始输出/)
})

test('Review link is created only from a real review ref', () => {
  const payload = page()
  payload.items[0].reports[0].proposals[0].review_ref = 'review-ne04-1'
  const model = buildEvolutionLabViewModel(payload)
  assert.equal(model.safe, true)
  assert.equal(
    model.suites[0].reports[0].proposals[0].reviewHref,
    '/execution/task-runtime?review_id=review-ne04-1',
  )
})
