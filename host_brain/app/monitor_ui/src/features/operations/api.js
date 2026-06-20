import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'

const { operations } = ENDPOINT_REGISTRY

export { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'
export {
  fetchCodexBioOpsObservability,
  fetchLlmCallStats,
  fetchLlmRouteStats,
  fetchRateLimitBackendStats,
  fetchRetrievalRuntime,
  fetchServicesHealth,
} from '../../shared/api/sharedObservability.js'

export function fetchPromptTemplates(params = {}) {
  return endpointGet(operations.promptTemplates, params)
}

export function fetchPromptTemplate(templateId) {
  return endpointGet(operations.promptTemplate, {}, { templateId })
}

export function fetchPersonaFirstBatchCatalog() {
  return endpointGet(operations.personaFirstBatchCatalog)
}

export function fetchCanonProposals(params = {}) {
  return endpointGet(operations.canonProposals, params)
}

export { fetchCanonDocuments } from '../../shared/api/sharedObservability.js'

export function submitCanonProposal(body) {
  return endpointPost(operations.canonProposals, body)
}

export function approveCanonProposal(proposalId, body) {
  return endpointPost(operations.canonProposalApprove, body, { proposalId })
}

export function rejectCanonProposal(proposalId, body) {
  return endpointPost(operations.canonProposalReject, body, { proposalId })
}

export function rollbackCanonProposal(proposalId, body = {}) {
  return endpointPost(operations.canonProposalRollback, body, { proposalId })
}

export function fetchVaultStats(params = {}) {
  return endpointGet(operations.canonVaultStats, params)
}

export function fetchCapabilityForgeProposals(params = {}) {
  return endpointGet(operations.forgeProposals, params)
}

export function fetchCapabilityForgeRuntimeQuality(params = {}) {
  return endpointGet(operations.forgeRuntimeQuality, params)
}

export function fetchCapabilityForgeProposal(proposalId) {
  return endpointGet(operations.forgeProposal, {}, { proposalId })
}

export function approveCapabilityForgeProposal(proposalId, body) {
  return endpointPost(operations.forgeProposalApprove, body, { proposalId })
}

export function rollbackCapabilityForgeProposal(proposalId, body) {
  return endpointPost(operations.forgeProposalRollback, body, { proposalId })
}
