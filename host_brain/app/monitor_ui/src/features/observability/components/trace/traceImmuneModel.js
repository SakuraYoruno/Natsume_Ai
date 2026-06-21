export function buildImmuneStageMeta(nodes = []) {
  const orderedNodes = [...(Array.isArray(nodes) ? nodes : [])]
    .sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))

  let decision = ''
  let riskLevel = ''
  const reasonTags = []
  const seenReasonTags = new Set()

  const pushReasonTag = (value) => {
    const normalized = String(value || '').trim()
    if (!normalized || seenReasonTags.has(normalized)) return
    seenReasonTags.add(normalized)
    reasonTags.push(normalized)
  }

  orderedNodes.forEach((node) => {
    const payload = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
    const nestedImmune = (payload?.immune_gate && typeof payload.immune_gate === 'object') ? payload.immune_gate : {}
    const mergedPayload = { ...nestedImmune, ...payload }

    const payloadDecision = String(mergedPayload.decision || '').trim().toUpperCase()
    const payloadRisk = String(mergedPayload.risk_level || '').trim().toLowerCase()
    if (payloadDecision) decision = payloadDecision
    if (payloadRisk) riskLevel = payloadRisk

    const reasonCode = String(node?.reason_code || mergedPayload.reason_code || '').trim()
    if (reasonCode) pushReasonTag(reasonCode)

    const reasonCodes = Array.isArray(mergedPayload.reason_codes) ? mergedPayload.reason_codes : []
    reasonCodes.forEach((code) => pushReasonTag(code))
  })

  return {
    decision: decision || null,
    riskLevel: riskLevel || null,
    reasonTags: reasonTags.slice(0, 3),
  }
}
