const DEFAULT_REASON = 'OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE'

function parsePayload(raw) {
  const start = raw.indexOf('{')
  if (start < 0) return {}
  try {
    const parsed = JSON.parse(raw.slice(start))
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function describeForgeProjectionError(error) {
  const raw = String(error?.message || error || '').trim()
  const payload = parsePayload(raw)
  const detail = payload?.detail && typeof payload.detail === 'object' ? payload.detail : {}
  const reasonCode = String(
    payload.reason_code
      || detail.reason_code
      || (raw.match(/[A-Z][A-Z0-9_]{4,}/)?.[0])
      || DEFAULT_REASON
  )
  const upstreamReasonCode = String(
    payload.upstream_reason_code || detail.upstream_reason_code || ''
  )
  const degradedState = String(
    payload.degraded_state || detail.degraded_state || ''
  ).toUpperCase()
  const configurationFailure = [reasonCode, upstreamReasonCode]
    .some((value) => /(?:CONFIG|AUTH_NOT_CONFIGURED|READ_CONFIG)/.test(value))

  let message = '能力演化数据暂不可用，其他数字生命运行状态不受此提示影响。'
  if (configurationFailure) {
    message = 'Host 与 OBS 的能力演化读链尚未接通，请重启本机服务栈。'
  } else if (degradedState === 'STALE' || upstreamReasonCode.includes('STALE')) {
    message = '能力演化投影已过期，系统正在重新同步；当前不会展示旧结果。'
  } else if (payload.retryable === true) {
    message = '能力演化投影正在恢复，可稍后重新同步。'
  }

  return {
    title: '能力演化投影暂不可用',
    message,
    reasonCode,
  }
}

export { DEFAULT_REASON as DEFAULT_FORGE_PROJECTION_REASON }
