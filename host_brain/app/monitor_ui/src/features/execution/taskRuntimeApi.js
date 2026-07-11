// @ts-check

import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'
import {
  buildReviewDecisionPayload,
  buildReviewResumePayload,
} from './taskRuntimeModel.js'

const { execution } = ENDPOINT_REGISTRY

export function fetchTaskRuntimeExecutions(params = {}) {
  return endpointGet(execution.taskRuntimeExecutions, params)
}

export function fetchTaskRuntimeExecution(graphId, params = {}) {
  return endpointGet(execution.taskRuntimeExecution, params, { graphId })
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
