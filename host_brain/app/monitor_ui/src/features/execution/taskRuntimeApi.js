// @ts-check

import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'
import {
  buildReviewDecisionPayload,
  buildReviewResumePayload,
} from './taskRuntimeModel.js'

const { execution } = ENDPOINT_REGISTRY
const TASK_REVIEW_LOCATOR_ENDPOINT = Object.freeze({
  service: 'obs',
  path: '/obs/task-runtime/review-locator',
  risk: 'low',
})

export function fetchTaskRuntimeExecutions(params = {}) {
  return endpointGet(execution.taskRuntimeExecutions, params)
}

export function fetchTaskRuntimeExecution(graphId, params = {}) {
  return endpointGet(execution.taskRuntimeExecution, params, { graphId })
}

export function fetchTaskReviewLocator(reviewId) {
  const value = typeof reviewId === 'string' ? reviewId.trim() : ''
  if (!value) throw new Error('review_id is required')
  return endpointGet(TASK_REVIEW_LOCATOR_ENDPOINT, { review_id: value })
}

export function cancelTaskGraph(graphId, graphRevision, reasonCode) {
  const graph = typeof graphId === 'string' ? graphId.trim() : ''
  const reason = typeof reasonCode === 'string' ? reasonCode.trim().toUpperCase() : ''
  if (!graph) throw new Error('graph_id is required')
  if (!Number.isInteger(graphRevision) || graphRevision < 1) {
    throw new Error('graph_revision must be a positive integer')
  }
  if (!/^[A-Z][A-Z0-9_]{1,127}$/.test(reason)) {
    throw new Error('reason_code must be canonical uppercase')
  }
  return endpointPost(execution.taskGraphCancel, {
    graph_id: graph,
    graph_revision: graphRevision,
    reason_code: reason,
  })
}

export function approveTaskReview(review, actorId) {
  return endpointPost(
    execution.taskReviewApprove,
    buildReviewDecisionPayload(review, actorId),
    { reviewId: review.review_id },
  )
}

export function rejectTaskReview(review, actorId) {
  const payload = buildReviewDecisionPayload(review, actorId)
  return endpointPost(
    execution.taskReviewReject,
    { actor_id: payload.actor_id },
    { reviewId: review.review_id },
  )
}

export function resumeTaskReview(review, resumeTokenRef, resumeResultRef = '') {
  return endpointPost(
    execution.taskReviewResume,
    buildReviewResumePayload(review, resumeTokenRef, resumeResultRef),
    { reviewId: review.review_id },
  )
}
