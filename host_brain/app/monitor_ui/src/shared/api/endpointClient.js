import { endpointPath } from './endpointRegistry.js'
import { httpRequest } from './httpClient.js'
import { obsRequest } from './obsClient.js'
import { submitOperatorCommand } from './operatorCommandGateway.js'

function assertEndpointEntry(entry) {
  if (
    !entry
    || typeof entry !== 'object'
    || (!entry.path && !entry.queryPath && !entry.commandId && entry.retired !== true)
  ) {
    throw new Error('Invalid endpoint registry entry')
  }
}

/**
 * @param {Record<string, any>} entry
 * @param {{method?: string, pathParams?: Record<string, any>, query?: Record<string, any>, body?: any, options?: Record<string, any>}} [config]
 */
export function endpointRequest(entry, {
  method = 'GET',
  pathParams = {},
  query = {},
  body,
  options = {},
} = {}) {
  assertEndpointEntry(entry)
  const normalizedMethod = String(method || 'GET').toUpperCase()
  if (normalizedMethod !== 'GET' && entry.retired === true) {
    throw Object.assign(new Error('OPERATOR_COMMAND_RETIRED'), {
      commandId: String(entry.retiredCommandId || ''),
      replacementAuthority: String(entry.replacementAuthority || ''),
      replacementWave: String(entry.replacementWave || ''),
    })
  }
  if (normalizedMethod !== 'GET' && entry.commandId) {
    return submitOperatorCommand(entry.commandId, {
      body: body || {},
      pathParams,
      query,
      options,
    })
  }
  if (normalizedMethod !== 'GET') {
    const safeQueryMethods = Array.isArray(entry.queryMethods)
      ? entry.queryMethods.map((value) => String(value).toUpperCase())
      : []
    if (entry.queryOnly !== true || !safeQueryMethods.includes(normalizedMethod)) {
      throw new Error('DIRECT_MUTATION_ENDPOINT_DENIED')
    }
  }
  const url = endpointPath(entry, pathParams, query)
  /** @type {Record<string, any>} */
  const requestOptions = {
    ...options,
    method: normalizedMethod,
  }
  if (body !== undefined) {
    requestOptions.body = body
  }

  if (entry.service === 'obs') {
    return obsRequest(url, requestOptions)
  }
  if (entry.service === 'host') {
    return httpRequest(url, requestOptions)
  }
  throw new Error(`Unsupported endpoint service: ${entry.service}`)
}

export function endpointGet(entry, query = {}, pathParams = {}, options = {}) {
  return endpointRequest(entry, {
    method: 'GET',
    pathParams,
    query,
    options,
  })
}

export function endpointPost(entry, body = {}, pathParams = {}, query = {}, options = {}) {
  return endpointRequest(entry, {
    method: 'POST',
    pathParams,
    query,
    body,
    options,
  })
}

export function endpointPut(entry, body = {}, pathParams = {}, query = {}, options = {}) {
  return endpointRequest(entry, {
    method: 'PUT',
    pathParams,
    query,
    body,
    options,
  })
}
