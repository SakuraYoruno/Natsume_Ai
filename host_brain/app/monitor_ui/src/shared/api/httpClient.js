export class HttpError extends Error {
  constructor(message, { status = 0, payload = null, url = '' } = {}) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.payload = payload
    this.url = url
  }
}

function normalizeHeaders(headers, body) {
  const next = new Headers(headers || {})
  if (body !== undefined && body !== null && typeof body !== 'string' && !(body instanceof FormData)) {
    next.set('Content-Type', 'application/json')
  }
  next.set('Accept', next.get('Accept') || 'application/json, text/plain;q=0.9')
  return next
}

function normalizeBody(body) {
  if (body === undefined || body === null) return undefined
  if (typeof body === 'string' || body instanceof FormData) return body
  return JSON.stringify(body)
}

async function parseResponse(response) {
  if (response.status === 204) return null
  const contentType = response.headers.get('Content-Type') || ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  return response.text()
}

function formatErrorDetail(detail, fallback) {
  if (detail === null || detail === undefined || detail === '') return fallback
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail)
  } catch {
    return String(detail)
  }
}

export async function httpRequest(path, options = {}) {
  const { body, headers, timeoutMs = 0, signal, ...rest } = options
  const controller = timeoutMs > 0 ? new AbortController() : null
  let timeoutId = null
  if (controller && signal) {
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  if (controller) {
    timeoutId = setTimeout(() => controller.abort(new Error(`timeout:${timeoutMs}ms`)), timeoutMs)
  }
  try {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...rest,
      headers: normalizeHeaders(headers, body),
      body: normalizeBody(body),
      signal: controller?.signal || signal,
    })
    const payload = await parseResponse(response)
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? payload.detail || payload.error : payload
      throw new HttpError(formatErrorDetail(detail, `HTTP ${response.status}`), {
        status: response.status,
        payload,
        url: path,
      })
    }
    return payload
  } catch (error) {
    if (error?.name === 'AbortError' || String(error?.message || '').includes('timeout:')) {
      throw new HttpError(`请求超时：${path}`, { status: 0, payload: null, url: path })
    }
    throw error
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }
}

export function httpGet(path, options = {}) {
  return httpRequest(path, { ...options, method: 'GET' })
}

export function httpPost(path, body, options = {}) {
  return httpRequest(path, { ...options, method: 'POST', body })
}

export async function httpStreamRequest(path, options = {}) {
  const { body, headers, timeoutMs = 0, signal, ...rest } = options
  const controller = timeoutMs > 0 ? new AbortController() : null
  let timeoutId = null
  let timedOut = false
  if (controller && signal) {
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  if (controller) {
    timeoutId = setTimeout(() => {
      timedOut = true
      controller.abort(new Error(`timeout:${timeoutMs}ms`))
    }, timeoutMs)
  }
  try {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...rest,
      headers: normalizeHeaders(headers, body),
      body: normalizeBody(body),
      signal: controller?.signal || signal,
    })
    if (!response.ok) {
      const text = await response.text()
      throw new HttpError(text || `HTTP ${response.status}`, {
        status: response.status,
        payload: text,
        url: path,
      })
    }
    return response
  } catch (error) {
    if (timedOut || String(error?.message || '').includes('timeout:')) {
      throw new HttpError(`请求超时：${path}`, { status: 0, payload: null, url: path })
    }
    throw error
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }
}

export function httpStreamPost(path, body, options = {}) {
  return httpStreamRequest(path, { ...options, method: 'POST', body })
}

export async function httpText(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: normalizeHeaders({ Accept: 'text/plain, text/markdown;q=0.9' }, undefined),
    method: options.method || 'GET',
  })
  const text = await response.text()
  if (!response.ok) {
    throw new HttpError(text || `HTTP ${response.status}`, {
      status: response.status,
      payload: text,
      url: path,
    })
  }
  return text
}

export async function httpBlob(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: normalizeHeaders(options.headers || { Accept: '*/*' }, undefined),
    method: options.method || 'GET',
  })
  const blob = await response.blob()
  if (!response.ok) {
    throw new HttpError(`HTTP ${response.status}`, {
      status: response.status,
      payload: blob,
      url: path,
    })
  }
  return blob
}
