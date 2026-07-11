// @ts-check

const REVIEW_ROUTE = '/execution/task-runtime'

/** @param {unknown} value */
function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? /** @type {Record<string, any>} */ (value)
    : {}
}

/** @param {unknown} value */
function asArray(value) {
  return Array.isArray(value) ? value : []
}

/** @param {unknown} value */
function asText(value) {
  return typeof value === 'string' ? value.trim() : ''
}

/** @param {unknown} value */
function asStringList(value) {
  return asArray(value).map(asText).filter(Boolean)
}

/** @param {unknown} value */
function asLevel(value) {
  if (Number.isInteger(value) && value >= 0 && value <= 5) {
    return { valid: true, value, code: `L${value}` }
  }
  const text = asText(value).toUpperCase()
  if (/^L[0-5]$/.test(text)) {
    return { valid: true, value: Number(text.slice(1)), code: text }
  }
  return { valid: false, value: null, code: 'UNKNOWN' }
}

/** @param {unknown} value */
function asRisk(value) {
  return Number.isInteger(value) && value >= 0 && value <= 5 ? value : null
}

/** @param {unknown} reviewRef */
function reviewHref(reviewRef) {
  const value = asText(reviewRef)
  return value ? `${REVIEW_ROUTE}?review_ref=${encodeURIComponent(value)}` : ''
}

/** @param {unknown} value */
function normalizeCapabilities(value, { hideUpgradeCandidates = false } = {}) {
  const source = asObject(value)
  return asArray(source.items).map((candidate) => {
    const item = asObject(candidate)
    const upgrade = asObject(item.upgrade_candidate)
    const maturity = asLevel(item.maturity_level)
    const ceiling = asLevel(item.static_maturity_ceiling)
    const canonicalRisk = asRisk(item.canonical_risk)
    const valid = maturity.valid && ceiling.valid && canonicalRisk !== null
    const reviewRef = valid && !hideUpgradeCandidates ? asText(upgrade.review_ref) : ''
    return {
      valid,
      capabilityId: asText(item.capability_id) || 'unknown-capability',
      sourceKind: asText(item.source_kind) || 'UNKNOWN',
      maturityLevel: maturity.value,
      maturityCode: maturity.code,
      maturityLabel: asText(item.maturity_label) || maturity.code,
      canonicalRisk,
      staticMaturityCeiling: ceiling.value,
      staticMaturityCeilingCode: ceiling.code,
      evidenceRefs: asStringList(item.evidence_refs),
      upgradeCandidate: {
        reviewRef,
        status: reviewRef ? (asText(upgrade.status) || 'PENDING') : 'HIDDEN',
        href: reviewHref(reviewRef),
      },
    }
  })
}

/** @param {unknown} value */
function normalizeDegradation(value) {
  const item = asObject(value)
  const recovery = asObject(item.recovery)
  return {
    status: asText(item.status) || 'UNKNOWN',
    reasonCodes: asStringList(item.reason_codes),
    restrictions: asStringList(item.restrictions),
    affectedCapabilities: asStringList(item.affected_capabilities),
    evidenceRefs: asStringList(item.evidence_refs),
    highRiskLatched: item.high_risk_latched === true,
    recovery: {
      status: asText(recovery.status) || 'NOT_AVAILABLE',
      eligible: recovery.eligible === true,
      evidenceRefs: asStringList(recovery.evidence_refs),
      reasonCodes: asStringList(recovery.reason_codes),
      reviewRef: asText(recovery.review_ref),
      href: reviewHref(recovery.review_ref),
    },
  }
}

/** @param {unknown} value */
function normalizeIdentity(value) {
  const item = asObject(value)
  const diff = asObject(item.diff)
  const review = asObject(item.review)
  const privateBodyIncluded = item.private_body_included !== false
  const diffItems = privateBodyIncluded
    ? []
    : asArray(diff.items || diff.differences).map((candidate) => {
      const row = asObject(candidate)
      return {
        section: asText(row.section) || 'identity',
        field: asText(row.field) || 'unknown',
        severity: asText(row.severity) || asText(diff.severity) || 'UNKNOWN',
        reasonCode: asText(row.reason_code) || 'UNSPECIFIED',
      }
    })
  return {
    safe: !privateBodyIncluded,
    status: asText(item.status) || 'UNKNOWN',
    currentSnapshotRef: asText(item.current_snapshot_ref),
    releaseRef: asText(item.release_ref),
    privateBodyIncluded,
    diff: {
      severity: asText(diff.severity) || 'NONE',
      items: diffItems,
    },
    review: {
      required: review.required === true,
      reviewRef: asText(review.review_ref),
      status: asText(review.status) || 'NOT_REQUIRED',
      href: reviewHref(review.review_ref),
    },
  }
}

/** @param {unknown} value */
export function buildGovernanceViewModel(value) {
  const payload = asObject(value)
  const available = Object.keys(payload).length > 0
  if (!available) {
    return {
      available: false,
      safe: true,
      error: null,
      automaticUpgradeAvailable: false,
      capabilities: [],
      degradation: null,
      identity: null,
    }
  }

  const automaticUpgradeAvailable = payload.automatic_upgrade_available === true
    || asObject(payload.capabilities).automatic_upgrade_available === true
  const identity = normalizeIdentity(payload.identity)
  const capabilities = normalizeCapabilities(payload.capabilities, {
    hideUpgradeCandidates: automaticUpgradeAvailable,
  })
  const capabilitySafe = capabilities.every((item) => item.valid)
  const safe = !automaticUpgradeAvailable && identity.safe && capabilitySafe
  return {
    available: true,
    safe,
    error: automaticUpgradeAvailable
      ? '治理快照意外声明可自动升级，已隐藏升级候选。'
      : (!identity.safe
          ? '身份快照包含私密正文标记，已停止渲染 identity diff。'
          : (!capabilitySafe ? '能力成熟度或静态风险字段非法，已按 UNKNOWN fail-closed。' : null)),
    automaticUpgradeAvailable,
    capabilities,
    degradation: normalizeDegradation(payload.degradation),
    identity: identity.safe ? identity : null,
  }
}
