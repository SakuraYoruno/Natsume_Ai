import {
  OperatorCommandContractError,
  isOperatorCommandId,
  validateOperatorCommandPayload,
  validateOperatorCommandReceipt,
} from './generated/operatorCommandClient.ts'

const DEFAULT_TIMEOUT_MS = 35_000
const TOKEN_BYTES = 18

let runtimeOverride = null

export function configureOperatorCommandGateway(runtime = null) {
  runtimeOverride = runtime
}

export function resetOperatorCommandGateway() {
  runtimeOverride = null
}

/**
 * @param {string} commandId
 * @param {{body?: Record<string, unknown>, pathParams?: Record<string, unknown>, query?: Record<string, unknown>, options?: Record<string, any>}} [config]
 */
export async function submitOperatorCommand(commandId, {
  body = {},
  pathParams = {},
  query = {},
  options = {},
} = {}) {
  const normalizedId = String(commandId || '').trim()
  if (!isOperatorCommandId(normalizedId)) {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_NOT_IN_GENERATED_CONTRACT')
  }
  const payload = mergeCommandPayload(body, pathParams, query)
  validateOperatorCommandPayload(normalizedId, payload)
  const runtime = resolveRuntimeOverride()
  const timeoutMs = Number(options.timeoutMs || DEFAULT_TIMEOUT_MS)
  const controller = timeoutMs > 0 ? new AbortController() : null
  const externalSignal = options.signal
  let timeoutId = null
  if (controller && externalSignal) {
    externalSignal.addEventListener(
      'abort',
      () => controller.abort(externalSignal.reason),
      { once: true },
    )
  }
  if (controller) {
    timeoutId = setTimeout(
      () => controller.abort(new Error(`timeout:${timeoutMs}ms`)),
      timeoutMs,
    )
  }
  try {
    const transportOptions = {
      correlationId: String(options.correlationId || newOpaqueToken('corr')),
      idempotencyKey: String(options.idempotencyKey || newOpaqueToken('idem')),
      nonce: String(options.nonce || newOpaqueToken('nonce')),
      signal: controller?.signal || externalSignal,
    }
    return await executeOperatorCommandViaBff(
      normalizedId,
      payload,
      transportOptions,
      runtime?.fetchImpl,
    )
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }
}

/** @param {string} commandId @param {Record<string, unknown>} [payload] @param {Record<string, any>} [options] */
export async function submitOperatorCommandAsSse(commandId, payload = {}, options = {}) {
  const receipt = await submitOperatorCommand(commandId, { body: payload, options })
  const event = JSON.stringify({
    phase: 'OPERATOR_COMMAND_RECEIPT',
    receipt,
    result: receipt,
  })
  return new Response(`data: ${event}\n\n`, {
    headers: { 'Content-Type': 'text/event-stream' },
    status: 200,
  })
}

/**
 * @param {Record<string, unknown>} [body]
 * @param {Record<string, unknown>} [pathParams]
 * @param {Record<string, unknown>} [query]
 * @returns {Record<string, unknown>}
 */
export function mergeCommandPayload(body = {}, pathParams = {}, query = {}) {
  /** @type {Record<string, unknown>} */
  const payload = {}
  for (const source of [body, pathParams, query]) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      throw new OperatorCommandContractError('OPERATOR_COMMAND_PAYLOAD_INVALID')
    }
    for (const [rawKey, value] of Object.entries(source)) {
      if (value === undefined || value === null || value === '') continue
      const key = toSnakeCase(rawKey)
      if (Object.prototype.hasOwnProperty.call(payload, key) && payload[key] !== value) {
        throw new OperatorCommandContractError('OPERATOR_COMMAND_PAYLOAD_CONFLICT')
      }
      payload[key] = value
    }
  }
  return payload
}

function resolveRuntimeOverride() {
  const runtime = runtimeOverride
  if (!runtime) return null
  const fetchImpl = typeof runtime === 'function' ? runtime : runtime?.fetchImpl
  if (typeof fetchImpl !== 'function') {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_TRANSPORT_UNAVAILABLE')
  }
  return {
    fetchImpl,
  }
}

async function executeOperatorCommandViaBff(commandId, payload, options, fetchImpl) {
  const request = fetchImpl || globalThis.fetch
  if (typeof request !== 'function') {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_TRANSPORT_UNAVAILABLE')
  }
  const response = await request(
    `/v1/host/operator-command-bff/${encodeURIComponent(commandId)}`,
    {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Natsume-Correlation-Id': options.correlationId,
        'X-Natsume-Idempotency-Key': options.idempotencyKey,
        'X-Natsume-Nonce': options.nonce,
      },
      body: JSON.stringify({ payload }),
      signal: options.signal,
    },
  )
  let receipt
  try {
    receipt = await response.json()
  } catch {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_RECEIPT_INVALID')
  }
  validateOperatorCommandReceipt(receipt, commandId)
  if (!response.ok) {
    throw Object.assign(
      new OperatorCommandContractError(receipt.reason_code || 'OPERATOR_COMMAND_REJECTED'),
      { receipt, status: response.status },
    )
  }
  return receipt
}

function newOpaqueToken(prefix) {
  const cryptoApi = globalThis.crypto
  if (!cryptoApi || typeof cryptoApi.getRandomValues !== 'function') {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_RANDOM_SOURCE_UNAVAILABLE')
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(TOKEN_BYTES))
  const token = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${prefix}.${token}`
}

function toSnakeCase(value) {
  const normalized = String(value || '')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
    .toLowerCase()
  if (!normalized) {
    throw new OperatorCommandContractError('OPERATOR_COMMAND_PAYLOAD_FIELD_INVALID')
  }
  return normalized
}
