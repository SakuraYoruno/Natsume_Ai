import assert from 'node:assert/strict'
import test from 'node:test'

import { describeForgeProjectionError } from './forgeProjectionError.js'

test('能力演化错误会转成可读提示而不是原样展示 JSON', () => {
  const error = 'Capability Forge unavailable: {"reason_code":"OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE","degraded_state":"STALE","upstream_reason_code":"OPERATOR_PROJECTION_STALE","retryable":true}'
  const result = describeForgeProjectionError(error)

  assert.equal(result.title, '能力演化投影暂不可用')
  assert.match(result.message, /重新同步/)
  assert.equal(result.reasonCode, 'OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE')
  assert.doesNotMatch(result.message, /\{.*\}/)
})

test('内部读凭据缺失会给出明确恢复入口', () => {
  const result = describeForgeProjectionError(
    'Capability Forge unavailable: {"reason_code":"OBS_HOST_READ_CONFIG"}'
  )

  assert.match(result.message, /Host 与 OBS/)
})
