export const OBS_RESOURCE_AVAILABILITY = Object.freeze({
  AVAILABLE: 'available',
  UNAVAILABLE: 'unavailable',
  ERROR: 'error',
})

function asReasonCode(error) {
  const detail = error?.payload?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    return String(detail.reason_code || detail.code || detail.error || '').trim() || null
  }
  return String(error?.payload?.reason_code || error?.reason_code || '').trim() || null
}

function asErrorMessage(error, reasonCode) {
  if (reasonCode) return reasonCode
  return String(error?.message || error || 'OBSERVABILITY_RESOURCE_REQUEST_FAILED')
}

function isTypedUnavailable(error, reasonCode) {
  return Number(error?.status || 0) === 503 || String(reasonCode || '').toUpperCase().includes('UNAVAILABLE')
}

export function buildObservabilityResourceState({ key, source, result }) {
  if (result?.status === 'fulfilled' && result.value != null) {
    return {
      key,
      source,
      data: result.value,
      availability: OBS_RESOURCE_AVAILABILITY.AVAILABLE,
      available: true,
      httpStatus: 200,
      reasonCode: null,
      message: null,
    }
  }

  const error = result?.status === 'rejected' ? result.reason : null
  const reasonCode = error
    ? asReasonCode(error)
    : 'OBSERVABILITY_RESOURCE_EMPTY_RESPONSE'
  const unavailable = !error || isTypedUnavailable(error, reasonCode)

  return {
    key,
    source,
    data: null,
    availability: unavailable
      ? OBS_RESOURCE_AVAILABILITY.UNAVAILABLE
      : OBS_RESOURCE_AVAILABILITY.ERROR,
    available: false,
    httpStatus: Number(error?.status || 0) || null,
    reasonCode,
    message: error
      ? asErrorMessage(error, reasonCode)
      : '接口返回空响应，当前资源不可用。',
  }
}

export async function settleObservabilityResources(specs = []) {
  const normalizedSpecs = Array.isArray(specs) ? specs : []
  const results = await Promise.allSettled(
    normalizedSpecs.map((spec) => Promise.resolve().then(() => spec.request())),
  )
  const resources = Object.fromEntries(normalizedSpecs.map((spec, index) => {
    const state = buildObservabilityResourceState({
      key: spec.key,
      source: spec.source,
      result: results[index],
    })
    return [spec.key, state]
  }))

  return {
    resources,
    issues: Object.values(resources).filter((resource) => !resource.available),
  }
}

export function formatObservableCount(value) {
  return value == null || Number.isNaN(Number(value)) ? '不可测' : String(value)
}

export function formatObservablePercent(value) {
  return value == null || Number.isNaN(Number(value)) ? '不可测' : `${value}%`
}
