import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { httpGet } from '../../shared/api/httpClient.js'
import { obsGet, obsPost } from '../../shared/api/obsClient.js'

const { operations } = ENDPOINT_REGISTRY

export { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'

export function fetchCodexBioOpsObservability(params = {}) {
  return getObs(operations.codexBioOps, params)
}

export function fetchLlmCallStats(params = {}) {
  return getObs(operations.llmCallStats, params)
}

export function fetchLlmRouteStats(params = {}) {
  return getObs(operations.llmRouteStats, params)
}

export function fetchRateLimitBackendStats(params = {}) {
  return getObs(operations.rateLimitBackendStats, params)
}

export function fetchRetrievalRuntime(params = {}) {
  return getObs(operations.retrievalRuntime, params)
}

export function fetchServicesHealth(params = {}) {
  return getObs(operations.servicesHealth, params)
}

export function fetchPromptTemplates(params = {}) {
  return httpGet(endpointPath(operations.promptTemplates, {}, params))
}

export function fetchPromptTemplate(templateId) {
  return httpGet(endpointPath(operations.promptTemplate, { templateId }))
}

export function fetchPersonaFirstBatchCatalog() {
  return httpGet(operations.personaFirstBatchCatalog.path)
}

export function fetchCanonProposals(params = {}) {
  return getObs(operations.canonProposals, params)
}

export function fetchCanonDocuments() {
  return getObs(operations.canonDocuments)
}

export function submitCanonProposal(body) {
  return postObs(operations.canonProposals, body)
}

export function approveCanonProposal(proposalId, body) {
  return postObs(operations.canonProposalApprove, body, { proposalId })
}

export function rejectCanonProposal(proposalId, body) {
  return postObs(operations.canonProposalReject, body, { proposalId })
}

export function rollbackCanonProposal(proposalId, body = {}) {
  return postObs(operations.canonProposalRollback, body, { proposalId })
}

export function fetchVaultStats(params = {}) {
  return getObs(operations.canonVaultStats, params)
}

export function fetchCapabilityForgeProposals(params = {}) {
  return getObs(operations.forgeProposals, params)
}

export function fetchCapabilityForgeRuntimeQuality(params = {}) {
  return getObs(operations.forgeRuntimeQuality, params)
}

export function fetchCapabilityForgeProposal(proposalId) {
  return getObs(operations.forgeProposal, {}, { proposalId })
}

export function approveCapabilityForgeProposal(proposalId, body) {
  return postObs(operations.forgeProposalApprove, body, { proposalId })
}

export function rollbackCapabilityForgeProposal(proposalId, body) {
  return postObs(operations.forgeProposalRollback, body, { proposalId })
}

function getObs(entry, params = {}, pathParams = {}) {
  return obsGet(endpointPath(entry, pathParams, params))
}

function postObs(entry, body = {}, pathParams = {}, params = {}) {
  return obsPost(endpointPath(entry, pathParams, params), body)
}
