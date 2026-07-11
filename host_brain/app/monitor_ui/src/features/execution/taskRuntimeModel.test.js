import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EXECUTION_REVIEW_SCHEMA_VERSION,
  buildReviewResumePayload,
  buildTaskRuntimeViewModel,
  getReviewActionState,
} from './taskRuntimeModel.js'

function review(status, overrides = {}) {
  return {
    review_id: `review-${status.toLowerCase()}`,
    status,
    expired: status === 'EXPIRED',
    subject_ref: 'task-node://graph-1/node-1',
    subject_version: 7,
    resume_phase: 'PRE_EXECUTION',
    checkpoint_id: 'checkpoint://graph-1/node-1/7',
    actions: {
      approve: status === 'PENDING',
      reject: status === 'PENDING',
      resume: status === 'APPROVED',
      resume_requires_token: true,
    },
    ...overrides,
  }
}

function payload(overrides = {}) {
  return {
    schema_version: EXECUTION_REVIEW_SCHEMA_VERSION,
    generated_at_ms: 1_000,
    read_only: true,
    mutation_contract: {
      direct_database_write: false,
      resume_requires_bound_token: true,
      automatic_resume: false,
    },
    execution: { graph_id: 'graph-1', revision: 1, status: 'COMPLETED', terminal: true },
    dag: { nodes: [{ node_id: 'node-1', status: 'ACCEPTED' }], edges: [] },
    attempts: [],
    budgets: [],
    verification: [],
    reviews: [review('PENDING'), review('APPROVED'), review('EXPIRED')],
    timeline: [
      { cursor: 2, event_id: 'e2', dedupe_key: 'd2' },
      { cursor: 1, event_id: 'e1', dedupe_key: 'd1' },
      { cursor: 3, event_id: 'e3', dedupe_key: 'd2' },
    ],
    cursor_page: { after: 0, next_cursor: 3, has_more: false },
    ...overrides,
  }
}

test('read model fails closed on unknown schema or unsafe mutation boundary', () => {
  assert.equal(buildTaskRuntimeViewModel({ ...payload(), schema_version: 'unknown.v2' }).compatible, false)
  assert.equal(buildTaskRuntimeViewModel({ ...payload(), read_only: false }).compatible, false)
  assert.equal(buildTaskRuntimeViewModel({
    ...payload(),
    mutation_contract: { direct_database_write: false, automatic_resume: false },
  }).compatible, false)
})

test('review actions distinguish pending, approved, and expired', () => {
  assert.deepEqual(getReviewActionState(review('PENDING')), {
    status: 'PENDING',
    expired: false,
    canApprove: true,
    canReject: true,
    canResume: false,
    tokenRequired: true,
  })
  assert.equal(getReviewActionState(review('APPROVED')).canResume, true)
  assert.equal(getReviewActionState(review('EXPIRED')).canResume, false)
  assert.equal(getReviewActionState(review('APPROVED', {
    actions: { resume: true, resume_requires_token: false },
  })).canResume, false)
})

test('resume payload requires the bound token and contains no boolean bypass', () => {
  const approved = review('APPROVED')
  assert.throws(() => buildReviewResumePayload(approved, ''), /resume_token_ref is required/)
  const result = buildReviewResumePayload(approved, 'review-resume.v1.secret', 'review-resume://result/1')
  assert.deepEqual(result, {
    resume_token_ref: 'review-resume.v1.secret',
    subject_ref: 'task-node://graph-1/node-1',
    subject_version: 7,
    resume_phase: 'PRE_EXECUTION',
    checkpoint_id: 'checkpoint://graph-1/node-1/7',
    resume_result_ref: 'review-resume://result/1',
  })
  assert.equal('confirmed' in result, false)
})

test('view model orders cursor events and dedupes replay keys', () => {
  const model = buildTaskRuntimeViewModel(payload())
  assert.equal(model.compatible, true)
  assert.deepEqual(model.timeline.map((event) => event.cursor), [1, 2])
  assert.equal(model.metrics.events, 2)
  assert.equal(model.reviews.find((item) => item.status === 'EXPIRED').actionState.canApprove, false)
})
