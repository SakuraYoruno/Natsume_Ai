// @ts-check

export const EXECUTION_INDEX_SCHEMA_VERSION = 'Natsume.operator.execution_index.v1'
export const EXECUTION_REVIEW_SCHEMA_VERSION = 'Natsume.operator.execution_review.v1'

const TERMINAL_GRAPH_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])

/** @param {unknown} value */
function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? /** @type {Record<string, any>} */ (value) : {}
}

/** @param {unknown} value */
function asArray(value) {
  return Array.isArray(value) ? value : []
}

/** @param {unknown} value */
function asText(value) {
  return String(value ?? '').trim()
}

/** @param {unknown} value */
function asNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

/** @param {unknown} value */
export function normalizeExecutionIndex(value) {
  const payload = asObject(value)
  const compatible = payload.schema_version === EXECUTION_INDEX_SCHEMA_VERSION
  const executions = compatible
    ? asArray(payload.executions)
      .map(asObject)
      .filter((item) => asText(item.graph_id))
      .map((item) => ({
        ...item,
        graph_id: asText(item.graph_id),
        revision: Math.max(1, asNumber(item.revision)),
        status: asText(item.status) || 'UNKNOWN',
        terminal: Boolean(item.terminal),
        node_count: Math.max(0, asNumber(item.node_count)),
        updated_ts_ms: Math.max(0, asNumber(item.updated_ts_ms)),
      }))
    : []
  return {
    schemaVersion: asText(payload.schema_version),
    compatible,
    error: compatible ? null : '执行索引 schema 不兼容，已停止消费。',
    readOnly: payload.read_only === true,
    executions,
  }
}

/** @param {Record<string, any>} review */
export function getReviewActionState(review) {
  const item = asObject(review)
  const actions = asObject(item.actions)
  const status = asText(item.status).toUpperCase()
  const expired = Boolean(item.expired) || status === 'EXPIRED'
  const tokenRequired = actions.resume_requires_token === true
  return {
    status,
    expired,
    canApprove: !expired && status === 'PENDING' && actions.approve === true,
    canReject: !expired && status === 'PENDING' && actions.reject === true,
    canResume: !expired && status === 'APPROVED' && actions.resume === true && tokenRequired,
    tokenRequired,
  }
}

/**
 * @param {Record<string, any>} review
 * @param {unknown} actorId
 */
export function buildReviewDecisionPayload(review, actorId) {
  const item = asObject(review)
  const actor = asText(actorId)
  if (!actor) throw new Error('actor_id is required')
  return {
    actor_id: actor,
    subject_ref: asText(item.subject_ref),
    subject_version: Math.max(0, asNumber(item.subject_version)),
    resume_phase: asText(item.resume_phase),
    checkpoint_id: asText(item.checkpoint_id),
  }
}

/**
 * Build the only resume payload accepted by the UI.  No boolean confirmation
 * or status flag can substitute for the bound token and checkpoint fields.
 * @param {Record<string, any>} review
 * @param {unknown} resumeTokenRef
 * @param {unknown} resumeResultRef
 */
export function buildReviewResumePayload(review, resumeTokenRef, resumeResultRef = '') {
  const item = asObject(review)
  const action = getReviewActionState(item)
  const token = asText(resumeTokenRef)
  if (!action.canResume) throw new Error('review is not resumable')
  if (!token) throw new Error('resume_token_ref is required')
  const payload = {
    resume_token_ref: token,
    subject_ref: asText(item.subject_ref),
    subject_version: Math.max(0, asNumber(item.subject_version)),
    resume_phase: asText(item.resume_phase),
    checkpoint_id: asText(item.checkpoint_id),
  }
  const resultRef = asText(resumeResultRef)
  return resultRef ? { ...payload, resume_result_ref: resultRef } : payload
}

/** @param {unknown} value */
export function buildTaskRuntimeViewModel(value) {
  const payload = asObject(value)
  const compatible = payload.schema_version === EXECUTION_REVIEW_SCHEMA_VERSION
  const readOnly = payload.read_only === true
  const mutationContract = asObject(payload.mutation_contract)
  const safeMutationContract = readOnly
    && mutationContract.direct_database_write === false
    && mutationContract.resume_requires_bound_token === true
    && mutationContract.automatic_resume === false
  if (!compatible || !readOnly || !safeMutationContract) {
    return {
      compatible: false,
      error: !compatible
        ? '执行 read model schema 不兼容，已停止消费。'
        : '执行 read model 未声明安全只读边界，已停止消费。',
      execution: null,
      nodes: [],
      edges: [],
      attempts: [],
      budgets: [],
      verification: [],
      reviews: [],
      timeline: [],
      cursorPage: { after: 0, next_cursor: 0, event_count: 0, has_more: false },
      metrics: { nodes: 0, attempts: 0, reviews: 0, events: 0 },
    }
  }

  const execution = asObject(payload.execution)
  const dag = asObject(payload.dag)
  const nodes = asArray(dag.nodes).map(asObject)
  const attempts = asArray(payload.attempts).map(asObject)
  const reviews = asArray(payload.reviews).map((item) => {
    const review = asObject(item)
    return { ...review, actionState: getReviewActionState(review) }
  })
  const seenDedupe = new Set()
  const timeline = asArray(payload.timeline)
    .map(asObject)
    .sort((left, right) => asNumber(left.cursor) - asNumber(right.cursor))
    .filter((event) => {
      const key = asText(event.dedupe_key) || `cursor:${asNumber(event.cursor)}`
      if (seenDedupe.has(key)) return false
      seenDedupe.add(key)
      return true
    })

  return {
    compatible: true,
    error: null,
    schemaVersion: asText(payload.schema_version),
    generatedAtMs: asNumber(payload.generated_at_ms),
    execution: {
      ...execution,
      graph_id: asText(execution.graph_id),
      revision: Math.max(1, asNumber(execution.revision)),
      status: asText(execution.status) || 'UNKNOWN',
      terminal: Boolean(execution.terminal) || TERMINAL_GRAPH_STATUSES.has(asText(execution.status)),
    },
    nodes,
    edges: asArray(dag.edges).map(asObject),
    attempts,
    budgets: asArray(payload.budgets).map(asObject),
    verification: asArray(payload.verification).map(asObject),
    reviews,
    timeline,
    cursorPage: {
      ...asObject(payload.cursor_page),
      after: Math.max(0, asNumber(asObject(payload.cursor_page).after)),
      next_cursor: Math.max(0, asNumber(asObject(payload.cursor_page).next_cursor)),
      event_count: timeline.length,
      has_more: Boolean(asObject(payload.cursor_page).has_more),
    },
    metrics: {
      nodes: nodes.length,
      attempts: attempts.length,
      reviews: reviews.length,
      events: timeline.length,
    },
  }
}
