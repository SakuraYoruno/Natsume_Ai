// @ts-check

const DIGEST = /^[0-9a-f]{64}$/
const COMMIT = /^[0-9a-f]{40}$/
const REQUIRED_KINDS = new Set(['memory_write', 'proactive', 'research_planning'])
const FORBIDDEN_PUBLIC_KEYS = new Set([
  'dataset_id',
  'output_digest',
  'raw_output',
  'raw_thought',
  'rationale',
  'sandbox_attestation',
  'source_body',
])
const FORBIDDEN_BODY_KEYS = new Set(['records', 'results'])

/** @param {unknown} value */
function object(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? /** @type {Record<string, any>} */ (value)
    : {}
}

/** @param {unknown} value */
function array(value) {
  return Array.isArray(value) ? value : []
}

/** @param {unknown} value */
function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

/** @param {unknown} value */
function integer(value) {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** @param {unknown} value */
function digest(value) {
  const normalized = text(value).toLowerCase()
  return DIGEST.test(normalized) ? normalized : ''
}

/** @param {unknown} value */
function numericMap(value) {
  const source = object(value)
  /** @type {Record<string, number>} */
  const result = {}
  Object.entries(source).forEach(([key, candidate]) => {
    if (key && typeof candidate === 'number' && Number.isFinite(candidate)) {
      result[key] = candidate
    }
  })
  return result
}

/** @param {unknown} value */
function hasForbiddenPublicKey(value) {
  if (Array.isArray(value)) return value.some(hasForbiddenPublicKey)
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, child]) => (
    FORBIDDEN_PUBLIC_KEYS.has(String(key).toLowerCase())
    || (
      FORBIDDEN_BODY_KEYS.has(String(key).toLowerCase())
      && child !== null
      && typeof child === 'object'
    )
    || hasForbiddenPublicKey(child)
  ))
}

/** @param {unknown} reviewRef */
function reviewHref(reviewRef) {
  const ref = text(reviewRef)
  return ref ? `/execution/task-runtime?review_id=${encodeURIComponent(ref)}` : ''
}

/** @param {unknown} value */
function normalizeProposal(value) {
  const item = object(value)
  const proposalDigest = digest(item.proposal_digest)
  const reviewRef = text(item.review_ref)
  const safe = Boolean(
    text(item.proposal_id)
    && proposalDigest
    && text(item.target_kind)
    && text(item.target_version)
    && item.review_required === true
    && item.production_influence_enabled === false
  )
  return {
    safe,
    proposalId: text(item.proposal_id),
    proposalDigest,
    targetKind: text(item.target_kind),
    targetVersion: text(item.target_version),
    reviewRequired: item.review_required === true,
    reviewStatus: text(item.review_status) || 'AWAITING_REVIEW',
    reviewRef,
    reviewHref: reviewHref(reviewRef),
    productionInfluenceEnabled: item.production_influence_enabled === true,
    createdAtMs: integer(item.created_at_ms) ?? 0,
  }
}

/** @param {unknown} value */
function normalizeArm(value) {
  const item = object(value)
  const receiptDigest = digest(item.receipt_digest)
  const sandboxDigest = digest(item.sandbox_attestation_digest)
  const verificationDigest = digest(item.verification_receipt_digest)
  const evidenceDigest = digest(item.verification_evidence_digest)
  const isBaseline = item.is_baseline === true
  const safe = Boolean(
    text(item.run_id)
    && text(item.execution_id)
    && receiptDigest
    && text(item.variant_id)
    && text(item.policy_version)
    && text(item.code_version)
    && digest(item.config_digest)
    && ['PASS', 'FAIL', 'INCOMPLETE'].includes(text(item.status).toUpperCase())
    && sandboxDigest
    && text(item.container_id)
    && verificationDigest
    && evidenceDigest
    && item.production_side_effects === false
    && item.production_influence_enabled === false
    && item.benefit_eligible === true
  )
  return {
    safe,
    runId: text(item.run_id),
    executionId: text(item.execution_id),
    receiptDigest,
    variantId: text(item.variant_id),
    isBaseline,
    policyVersion: text(item.policy_version),
    codeVersion: text(item.code_version),
    configDigest: digest(item.config_digest),
    status: text(item.status).toUpperCase() || 'UNKNOWN',
    modelIdentity: text(item.model_identity),
    usage: numericMap(item.usage),
    cost: numericMap(item.cost),
    latencyMs: integer(item.latency_ms) ?? 0,
    sandboxDigest,
    containerId: text(item.container_id),
    verificationDigest,
    verificationStatus: text(item.verification_status).toUpperCase() || 'UNKNOWN',
    evidenceDigest,
    outcome: numericMap(item.outcome),
    risk: numericMap(item.risk),
    metrics: numericMap(item.metrics),
  }
}

/** @param {unknown} value */
function normalizeComparison(value) {
  const item = object(value)
  const baselineRunId = text(item.baseline_run_id)
  const alternativeRunId = text(item.alternative_run_id)
  return {
    safe: Boolean(baselineRunId && alternativeRunId && baselineRunId !== alternativeRunId),
    baselineRunId,
    alternativeRunId,
    outcomeDelta: numericMap(item.outcome_delta),
    evidenceDelta: numericMap(item.evidence_delta),
    costDelta: numericMap(item.cost_delta),
    latencyDelta: numericMap(item.latency_delta),
    riskDelta: numericMap(item.risk_delta),
    verificationDelta: numericMap(item.verification_delta),
  }
}

/** @param {unknown} value */
function normalizeReport(value) {
  const item = object(value)
  const arms = array(item.arms).map(normalizeArm)
  const comparisons = array(item.comparisons).map(normalizeComparison)
  const proposals = array(item.proposals).map(normalizeProposal)
  const kind = text(item.experiment_kind).toLowerCase()
  const baselineCount = arms.filter((arm) => arm.isBaseline).length
  const safe = Boolean(
    text(item.report_id)
    && digest(item.report_digest)
    && REQUIRED_KINDS.has(kind)
    && text(item.workspace_id)
    && text(item.experiment_id)
    && digest(item.spec_digest)
    && digest(item.dataset_manifest_digest)
    && text(item.dataset_source_kind)
    && integer(item.dataset_record_count) !== null
    && integer(item.seed) !== null
    && digest(item.isolation_profile_digest)
    && arms.length >= 2
    && arms.every((arm) => arm.safe)
    && baselineCount === 1
    && comparisons.length >= 1
    && comparisons.every((comparison) => comparison.safe)
    && proposals.every((proposal) => proposal.safe)
    && item.promotion_evidence_eligible === true
    && item.review_required === true
    && item.production_influence_enabled === false
  )
  return {
    safe,
    reportId: text(item.report_id),
    reportDigest: digest(item.report_digest),
    kind,
    workspaceId: text(item.workspace_id),
    experimentId: text(item.experiment_id),
    specDigest: digest(item.spec_digest),
    datasetManifestDigest: digest(item.dataset_manifest_digest),
    datasetSourceKind: text(item.dataset_source_kind),
    datasetRecordCount: integer(item.dataset_record_count) ?? 0,
    seed: integer(item.seed) ?? 0,
    budget: numericMap(item.budget),
    verifier: {
      identity: text(object(item.verifier).identity),
      version: text(object(item.verifier).version),
      digest: digest(object(item.verifier).digest),
    },
    isolationProfileDigest: digest(item.isolation_profile_digest),
    arms,
    comparisons,
    proposals,
    reviewStatus: text(item.review_status) || (proposals.length ? 'AWAITING_REVIEW' : 'PROPOSAL_MISSING'),
    reviewRef: text(item.review_ref),
    reviewHref: reviewHref(item.review_ref),
    createdAtMs: integer(item.created_at_ms) ?? 0,
  }
}

/** @param {unknown} value */
function normalizeSuite(value) {
  const item = object(value)
  const reports = array(item.reports).map(normalizeReport)
  const kinds = new Set(reports.map((report) => report.kind))
  const workspaceIds = array(item.workspace_ids).map(text).filter(Boolean)
  const safe = Boolean(
    text(item.suite_id)
    && COMMIT.test(text(item.commit_sha).toLowerCase())
    && text(item.migration_head)
    && digest(item.receipt_digest)
    && text(item.status).toUpperCase() === 'PASS'
    && text(item.engine_identity)
    && /^.+@sha256:[0-9a-f]{64}$/.test(text(item.image_reference).toLowerCase())
    && workspaceIds.length >= 1
    && reports.length === 3
    && kinds.size === 3
    && [...REQUIRED_KINDS].every((kind) => kinds.has(kind))
    && reports.every((report) => report.safe)
    && integer(item.report_count) === reports.length
    && integer(item.run_count) === reports.reduce((sum, report) => sum + report.arms.length, 0)
    && item.production_side_effects === false
    && item.production_influence_enabled === false
  )
  return {
    safe,
    suiteId: text(item.suite_id),
    commitSha: text(item.commit_sha).toLowerCase(),
    migrationHead: text(item.migration_head),
    receiptDigest: digest(item.receipt_digest),
    status: text(item.status).toUpperCase() || 'UNKNOWN',
    engineIdentity: text(item.engine_identity),
    imageReference: text(item.image_reference),
    workspaceIds,
    reports,
    createdAtMs: integer(item.created_at_ms) ?? 0,
  }
}

/** @param {unknown} value */
export function buildEvolutionLabViewModel(value) {
  const payload = object(value)
  if (!Object.keys(payload).length) {
    return {
      available: false,
      safe: true,
      error: null,
      readOnly: true,
      proposalOnly: true,
      suites: [],
      nextCursor: '',
      hasMore: false,
      metrics: { suites: 0, reports: 0, runs: 0, proposals: 0 },
    }
  }
  const authoritySafe = (
    payload.schema_version === 'Natsume.evolution_lab.operator_page.v1'
    && payload.read_only === true
    && payload.proposal_only === true
    && payload.production_influence_enabled === false
  )
  const privacySafe = !hasForbiddenPublicKey(payload)
  const suites = array(payload.items).map(normalizeSuite)
  const suitesSafe = suites.every((suite) => suite.safe)
  const safe = authoritySafe && privacySafe && suitesSafe
  const reports = suites.flatMap((suite) => suite.reports)
  const runs = reports.flatMap((report) => report.arms)
  const proposals = reports.flatMap((report) => report.proposals)
  return {
    available: payload.available === true,
    safe,
    error: !authoritySafe
      ? 'Operator Lab 投影意外暴露了写入或生产影响 authority，已停止渲染。'
      : !privacySafe
        ? 'Operator Lab 投影包含原始输出、数据集正文或其他禁用字段，已停止渲染。'
        : !suitesSafe
          ? 'Suite、Report、Run、Verifier 或 Proposal 证据绑定不完整，已按不可信处理。'
          : null,
    readOnly: payload.read_only === true,
    proposalOnly: payload.proposal_only === true,
    suites: safe ? suites : [],
    nextCursor: safe ? text(payload.next_cursor) : '',
    hasMore: safe && payload.has_more === true,
    sourceTruncated: payload.source_truncated === true,
    workspaceId: text(payload.workspace_id),
    metrics: {
      suites: safe ? suites.length : 0,
      reports: safe ? reports.length : 0,
      runs: safe ? runs.length : 0,
      proposals: safe ? proposals.length : 0,
    },
  }
}

export const EVOLUTION_LAB_KIND_LABELS = Object.freeze({
  research_planning: 'Research Planning',
  proactive: 'Proactive',
  memory_write: 'Memory Write',
})
