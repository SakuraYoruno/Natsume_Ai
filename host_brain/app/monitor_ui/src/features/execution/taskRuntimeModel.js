// @ts-check

export const EXECUTION_INDEX_SCHEMA_VERSION = 'Natsume.operator.execution_index.v1'
export const EXECUTION_REVIEW_SCHEMA_VERSION = 'Natsume.operator.execution_review.v1'
export const REVIEW_LOCATOR_SCHEMA_VERSION = 'Natsume.operator.review_locator.v1'

const TERMINAL_GRAPH_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])
const REVIEW_ID_MAX_LENGTH = 512

/** @param {string} value */
function hasControlCharacter(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code <= 31 || code === 127) return true
  }
  return false
}

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
function strictText(value) {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * @param {unknown} value
 * @returns {number | null}
 */
function strictInteger(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null
}

/** @param {unknown} value */
function strictTextList(value) {
  if (!Array.isArray(value)) return []
  return value
    .map(strictText)
    .filter(Boolean)
}

/** @param {unknown} value */
function validReviewId(value) {
  const reviewId = strictText(value)
  return reviewId
    && reviewId.length <= REVIEW_ID_MAX_LENGTH
    && !hasControlCharacter(reviewId)
    ? reviewId
    : ''
}

/**
 * Parse the governance Review deep-link without accepting duplicate, blank, or
 * control-character-bearing identifiers.
 * @param {unknown} value
 */
export function parseTaskReviewDeepLink(value) {
  let params
  try {
    params = new URLSearchParams(typeof value === 'string' ? value : '')
  } catch {
    return { active: true, reviewId: '', error: 'Review 深链参数无法解析，命令入口保持关闭。' }
  }
  if (!params.has('review_id')) {
    return { active: false, reviewId: '', error: null }
  }
  const candidates = params.getAll('review_id')
  const rawReviewId = candidates.length === 1 ? candidates[0] : ''
  const reviewId = validReviewId(rawReviewId)
  if (candidates.length !== 1 || !reviewId || reviewId !== rawReviewId) {
    return { active: true, reviewId: '', error: 'Review 深链 ID 无效或重复，未定位任何 Review。' }
  }
  return { active: true, reviewId, error: null }
}

/** @param {unknown} value */
function normalizeLocatorReview(value) {
  const item = asObject(value)
  const reviewId = validReviewId(item.review_id)
  const subjectVersion = strictInteger(item.subject_version)
  const status = strictText(item.status).toUpperCase()
  const actions = asObject(item.actions)
  const graphValue = item.task_graph_id
  const validGraphValue = graphValue == null || typeof graphValue === 'string'
  const taskGraphId = validGraphValue ? strictText(graphValue) : ''
  const review = {
    review_id: reviewId,
    review_type: strictText(item.review_type),
    subject_ref: strictText(item.subject_ref),
    subject_version: subjectVersion == null ? -1 : subjectVersion,
    requester: strictText(item.requester),
    reason: strictText(item.reason),
    risk_level: strictInteger(item.risk_level) ?? 1,
    status,
    persisted_status: strictText(item.persisted_status).toUpperCase() || status,
    status_version: Math.max(0, strictInteger(item.status_version) ?? 0),
    expires_at: Math.max(0, strictInteger(item.expires_at) ?? 0),
    expired: item.expired === true || status === 'EXPIRED',
    resume_phase: strictText(item.resume_phase),
    checkpoint_id: strictText(item.checkpoint_id),
    diff_ref: strictText(item.diff_ref) || null,
    decision_actor: strictText(item.decision_actor) || null,
    token_ref_public: strictText(item.token_ref_public) || null,
    resume_count: Math.max(0, strictInteger(item.resume_count) ?? 0),
    resume_result_ref: strictText(item.resume_result_ref) || null,
    work_order_id: strictText(item.work_order_id) || null,
    task_graph_id: taskGraphId || null,
    task_node_id: strictText(item.task_node_id) || null,
    side_effects: strictTextList(item.side_effects),
    evidence_refs: strictTextList(item.evidence_refs),
    created_ts_ms: Math.max(0, strictInteger(item.created_ts_ms) ?? 0),
    updated_ts_ms: Math.max(0, strictInteger(item.updated_ts_ms) ?? 0),
    actions: {
      approve: actions.approve === true,
      reject: actions.reject === true,
      resume: actions.resume === true,
      resume_requires_token: actions.resume_requires_token === true,
      resume_binding_fields: strictTextList(actions.resume_binding_fields),
    },
  }
  const valid = Boolean(
    review.review_id
    && review.review_type
    && review.subject_ref
    && review.subject_version >= 0
    && review.status
    && review.resume_phase
    && review.checkpoint_id
    && validGraphValue
    && review.actions.resume_requires_token,
  )
  return valid ? { ...review, actionState: getReviewActionState(review) } : null
}

/**
 * Consume only the versioned, read-only single Review locator. The located
 * public Review is allowlisted rather than spread so private ReviewStore fields
 * cannot cross into the browser model.
 * @param {unknown} value
 * @param {unknown} requestedReviewId
 */
export function normalizeReviewLocator(value, requestedReviewId) {
  const payload = asObject(value)
  const requested = validReviewId(requestedReviewId)
  const base = {
    schemaVersion: strictText(payload.schema_version),
    generatedAtMs: Math.max(0, strictInteger(payload.generated_at_ms) ?? 0),
    compatible: false,
    readOnly: payload.read_only === true,
    found: false,
    reviewId: requested,
    taskGraphId: null,
    review: null,
    error: null,
  }
  if (!requested) {
    return { ...base, error: 'Review 深链 ID 无效，命令入口保持关闭。' }
  }
  if (payload.schema_version !== REVIEW_LOCATOR_SCHEMA_VERSION) {
    return { ...base, error: 'Review locator schema 不兼容，命令入口保持关闭。' }
  }
  if (payload.read_only !== true) {
    return { ...base, error: 'Review locator 未声明只读边界，命令入口保持关闭。' }
  }
  if (validReviewId(payload.review_id) !== requested) {
    return { ...base, error: 'Review locator 返回了不匹配的 Review ID，命令入口保持关闭。' }
  }
  if (payload.found === false) {
    return {
      ...base,
      compatible: true,
      error: `未找到 Review ${requested}，未选择或开放任何 Review 命令。`,
    }
  }
  if (payload.found !== true) {
    return { ...base, error: 'Review locator 缺少明确 found 状态，命令入口保持关闭。' }
  }
  const review = normalizeLocatorReview(payload.review)
  if (!review || review.review_id !== requested) {
    return { ...base, error: 'Review locator 的公开 Review 载荷无效或不匹配，命令入口保持关闭。' }
  }
  const graphValue = payload.task_graph_id
  if (graphValue !== null && typeof graphValue !== 'string') {
    return { ...base, error: 'Review locator 的 TaskGraph 绑定无效，命令入口保持关闭。' }
  }
  const taskGraphId = strictText(graphValue) || null
  if (review.task_graph_id && taskGraphId !== review.task_graph_id) {
    return { ...base, error: 'Review locator 的 TaskGraph 绑定不一致，命令入口保持关闭。' }
  }
  return {
    ...base,
    compatible: true,
    found: true,
    taskGraphId,
    review,
  }
}

/**
 * Select the highest canonical revision for a located TaskGraph.
 * @param {unknown} executions
 * @param {unknown} graphId
 */
export function selectLatestExecutionForGraph(executions, graphId) {
  const target = strictText(graphId)
  if (!target) return null
  return asArray(executions)
    .map(asObject)
    .filter((item) => strictText(item.graph_id) === target && asNumber(item.revision) >= 1)
    .sort((left, right) => (
      asNumber(right.revision) - asNumber(left.revision)
      || asNumber(right.updated_ts_ms) - asNumber(left.updated_ts_ms)
    ))[0] || null
}

/**
 * Require exactly one real execution Review before focusing it. Duplicate IDs
 * are treated as ambiguous and fail closed.
 * @param {unknown} reviews
 * @param {unknown} reviewId
 */
export function focusExecutionReview(reviews, reviewId) {
  const target = validReviewId(reviewId)
  const items = asArray(reviews).map(asObject)
  const matches = target
    ? items.filter((item) => validReviewId(item.review_id) === target)
    : []
  if (matches.length !== 1) {
    return {
      found: false,
      review: null,
      reviews: [],
      error: matches.length > 1
        ? `Review ${target} 在执行快照中重复，命令入口保持关闭。`
        : `Review ${target || 'unknown'} 不在目标执行快照中，命令入口保持关闭。`,
    }
  }
  const review = matches[0]
  return {
    found: true,
    review,
    reviews: [review, ...items.filter((item) => item !== review)],
    error: null,
  }
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
