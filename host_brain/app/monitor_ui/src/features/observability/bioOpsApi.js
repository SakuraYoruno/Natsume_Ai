import { fetchCodexBioOpsObservability } from '../../shared/api/sharedObservability.js'
import {
  CODEX_BIO_OPS_CACHE_PREFIX,
  OBS_ENDPOINTS,
  observabilityGet,
  observabilityPost,
  requireCandidateId,
} from './observabilityApiClient.js'

export { fetchCodexBioOpsObservability }

export function fetchCodexBioOpsAdoptions(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsAdoptions, params)
}

export function runCodexBioOpsDailyCycle(body = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsDailyCycleRun, body, {}, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsAdoptionDetail(candidateId, params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsAdoption, params, {
    candidateId: requireCandidateId(candidateId),
  })
}

export function acceptCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsAdoptionAccept, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function rejectCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsAdoptionReject, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerInbox(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsOwnerInbox, params)
}

export function materializeCodexBioOpsOwnerInbox(body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerInboxMaterialize, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerImportCandidates(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsOwnerImportCandidates, params)
}

export function materializeCodexBioOpsOwnerIntakePackets(body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerIntakePackets, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerQueues(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsOwnerQueues, params)
}

export function consumeCodexBioOpsOwnerQueues(body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerQueuesConsume, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerState(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsOwnerState, params)
}

export function importCodexBioOpsOwnerState(body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerStateImport, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerRuntimeHints(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.codexBioOpsOwnerRuntimeHints, params)
}

export function acceptCodexBioOpsOwnerState(candidateId, body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerStateAccept, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function rejectCodexBioOpsOwnerState(candidateId, body = {}, params = {}) {
  return observabilityPost(OBS_ENDPOINTS.codexBioOpsOwnerStateReject, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}
