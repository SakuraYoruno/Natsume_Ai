const DASH = '--'

const STATE_LABELS = Object.freeze({
  CURRENT: '当前有效',
  EXPIRED: '已过期',
  CONFLICT: '来源冲突',
  DEGRADED: '降级信息',
})

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function refs(value) {
  return asArray(value)
    .filter((item) => typeof item === 'string' && item.trim())
    .map((item) => item.trim().slice(0, 512))
}

function text(value, fallback = DASH) {
  const resolved = String(value ?? '').trim()
  return resolved || fallback
}

function compact(value, limit = 160) {
  const resolved = typeof value === 'string' ? value : JSON.stringify(value ?? '')
  return resolved.length > limit ? `${resolved.slice(0, limit - 1)}…` : resolved || DASH
}

export function formatLifeConfidence(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return DASH
  return `${Math.round(Math.max(0, Math.min(1, numeric)) * 100)}%`
}

export function formatLifeTtl(item = {}) {
  if (item.expired) return '已过期'
  if (item.ttl_remaining_ms == null) return '未声明到期时间'
  const seconds = Math.max(0, Math.floor(Number(item.ttl_remaining_ms || 0) / 1000))
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${seconds}s`
}

function buildWorldFact(item = {}) {
  const factClass = String(item.fact_class || 'INFERENCE').toUpperCase()
  const displayState = String(item.display_state || 'CURRENT').toUpperCase()
  return {
    key: text(item.key),
    value: compact(item.value, 220),
    factClass,
    factClassLabel: factClass === 'FACT' ? '事实' : '推断',
    factClassTone: factClass === 'FACT' ? 'fact' : 'inference',
    epistemicType: text(item.epistemic_type),
    source: text(item.source, 'unknown'),
    sourceRef: text(item.source_ref),
    confidence: formatLifeConfidence(item.confidence),
    ttl: formatLifeTtl(item),
    displayState,
    displayStateLabel: STATE_LABELS[displayState] || displayState,
    expired: Boolean(item.expired),
    conflicted: Boolean(item.conflicted),
    conflictState: text(item.conflict_state, 'NONE'),
    conflictDetail: text(item.conflict_detail, ''),
    reasonCodes: refs(item.reason_codes),
  }
}

function buildAppraisal(item = {}) {
  const summary = item.summary || {}
  const bondSignal = item.bond_signal || {}
  return {
    id: text(item.appraisal_id),
    sourceKind: text(item.source_event_kind),
    sourceRef: text(item.source_event_ref),
    sourceDigest: text(item.source_event_digest),
    timestamp: Number(item.ts_ms || 0),
    kind: text(summary.kind),
    outcome: text(summary.outcome),
    epistemicType: text(summary.epistemic_type, 'inferred'),
    confidence: formatLifeConfidence(summary.confidence),
    valence: Number(summary.valence || 0).toFixed(2),
    arousal: Number(summary.arousal || 0).toFixed(2),
    bondDirection: text(bondSignal.direction, 'none'),
    bondStrength: formatLifeConfidence(bondSignal.strength),
    permissionEffect: text(bondSignal.permission_effect, 'NONE'),
    evidenceRefs: refs(item.evidence_refs),
    reasonTags: refs(item.reason_tags),
  }
}

function buildPreview(value = {}) {
  const raw = value || {}
  const decision = raw.arbiter_decision || raw
  const reasonCodes = refs(decision.reason_codes || raw.reason_codes)
  const restrictions = refs(decision.restrictions || raw.restrictions)
  const whyNow = refs(raw.why_now)
  const evidenceRefs = refs(raw.evidence_refs)
  const authorityBindingRef = text(decision.authority_binding_ref)
  const sceneDecisionRef = text(decision.scene_decision_ref)
  return {
    status: text(raw.status, 'NOT_AVAILABLE'),
    decision: text(decision.decision, 'BLOCK'),
    executionState: text(decision.execution_state, 'BLOCKED'),
    wouldExecute: Boolean(raw.would_execute),
    arbiterEligible: Boolean(raw.arbiter_eligible),
    executionPerformed: Boolean(raw.execution_performed),
    finality: text(raw.finality, raw.arbiter_decision ? 'GOVERNED_DRY_RUN' : 'AUTHORITY_CONTEXT_REQUIRED'),
    authorityContextStatus: text(raw.authority_context_status, 'UNAVAILABLE'),
    authoritySourceRef: text(raw.authority_source_ref),
    authorityValidationReason: text(raw.authority_validation_reason),
    executionPrecheckStatus: text(raw.execution_precheck_status, 'NOT_RUN'),
    authorityBindingRef,
    sceneDecisionRef,
    evidenceRefs: [authorityBindingRef, sceneDecisionRef, ...evidenceRefs].filter((item) => item !== DASH),
    governorDecision: text(decision.governor_decision),
    userGoalPreserved: Boolean(decision.user_goal_preserved),
    reasonCodes,
    restrictions,
    whyNow: whyNow.length ? whyNow : [...reasonCodes, ...restrictions],
  }
}

function buildArbiter(value = {}) {
  const raw = value || {}
  return {
    status: text(raw.status, 'UNAVAILABLE'),
    id: text(raw.decision_id),
    decision: text(raw.decision, 'BLOCK'),
    executionState: text(raw.execution_state, 'NOT_OBSERVED'),
    governorDecision: text(raw.governor_decision),
    authorityBindingValid: Boolean(raw.authority_binding_valid),
    userGoalPreserved: Boolean(raw.user_goal_preserved),
    finality: text(raw.finality, 'NO_COMMITTED_DECISION'),
    observedFromActualProducer: Boolean(raw.observed_from_actual_producer),
    reasonCodes: refs(raw.reason_codes),
    restrictions: refs(raw.restrictions),
    evidenceRefs: refs([
      raw.observation_source_ref,
      raw.authority_binding_ref,
      raw.scene_decision_ref,
    ]),
  }
}

export function buildLifeEvolutionModel(payload, previewOverride = null) {
  const data = payload && typeof payload === 'object' ? payload : {}
  const world = data.world || {}
  const bond = data.bond || {}
  const compatibility = bond.compatibility || {}
  const relationship = bond.relationship || {}
  const scene = data.scene || {}
  const facts = asArray(world.facts).map(buildWorldFact)

  return {
    available: Boolean(data.schema_version),
    schemaVersion: text(data.schema_version),
    viewId: text(data.view_id),
    generatedAt: Number(data.generated_at_ms || 0),
    readOnly: Boolean(data.read_only) && data.write_api_available === false,
    world: {
      status: text(world.status, 'UNAVAILABLE'),
      facts,
      conflictCount: facts.filter((item) => item.conflicted).length,
      expiredCount: facts.filter((item) => item.expired).length,
      factCount: facts.filter((item) => item.factClass === 'FACT').length,
      inferenceCount: facts.filter((item) => item.factClass !== 'FACT').length,
    },
    appraisals: asArray(data.appraisals?.records).map(buildAppraisal),
    appraisalPrivateBodyIncluded: Boolean(data.appraisals?.private_body_included),
    bond: {
      status: text(bond.status, 'UNAVAILABLE'),
      authorityLabel: text(bond.authority_label, 'PROJECTION_ONLY'),
      authoritative: Boolean(bond.authoritative),
      permissionEffect: text(bond.permission_effect, 'NONE'),
      stage: text(compatibility.current_stage),
      score: compatibility.total_score ?? DASH,
      trend: text(compatibility.trend),
      epistemicType: text(compatibility.epistemic_type, 'compatibility_projection'),
      vector: compact(relationship.vector, 180),
      rift: compact(relationship.rift, 180),
      repair: compact(relationship.repair, 180),
      boundary: compact(relationship.boundary, 180),
      evidenceRefs: refs(bond.evidence_refs),
    },
    scene: {
      status: text(scene.status, 'UNAVAILABLE'),
      id: text(scene.decision_id),
      type: text(scene.scene_type, 'general'),
      confidence: formatLifeConfidence(scene.confidence),
      ttl: formatLifeTtl(scene),
      expired: Boolean(scene.expired),
      conflicted: Boolean(scene.conflicted),
      conflictState: text(scene.conflict_state, 'NONE'),
      epistemicType: text(scene.epistemic_type, 'derived'),
      sourceRefs: refs(scene.source_refs),
      observationSourceRef: text(scene.observation_source_ref),
      observedFromActualProducer: Boolean(scene.observed_from_actual_producer),
      recordedPolicy: text(scene.recorded_proactive_policy, scene.proactive_policy || 'ALLOW'),
      effectivePolicy: text(scene.proactive_policy, 'ALLOW'),
      expressionMode: text(scene.expression_mode, 'natural'),
      permissionEffect: text(scene.permission_effect, 'RESTRICT_ONLY'),
      reasonCodes: refs(scene.reason_codes),
    },
    arbiter: buildArbiter(data.action_arbiter),
    preview: buildPreview(previewOverride || data.action_preview || {}),
  }
}
