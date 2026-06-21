import { endpointPath } from './endpointRegistry.js'
import { httpRequest } from './httpClient.js'
import { obsRequest } from './obsClient.js'

function assertEndpointEntry(entry) {
  if (!entry || typeof entry !== 'object' || !entry.path || !entry.service) {
    throw new Error('Invalid endpoint registry entry')
  }
}

export function endpointRequest(entry, {
  method = 'GET',
  pathParams = {},
  query = {},
  body,
  options = {},
} = {}) {
  assertEndpointEntry(entry)
  const url = endpointPath(entry, pathParams, query)
  const requestOptions = {
    ...options,
    method: String(method || 'GET').toUpperCase(),
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
