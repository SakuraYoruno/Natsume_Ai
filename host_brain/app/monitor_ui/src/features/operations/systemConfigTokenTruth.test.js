import assert from 'node:assert/strict'
import test from 'node:test'

import { buildSystemConfigCallRows } from './systemConfigModel.js'

test('SystemConfig call rows use unavailable and lower-bound labels without fabricating zero', () => {
  const rows = buildSystemConfigCallRows([
    {
      model_id: 'unmeasured',
      call_count: 2,
      total_tokens: null,
      total_tokens_measured_calls: 0,
      total_tokens_is_lower_bound: false,
    },
    {
      model_id: 'partial',
      call_count: 2,
      total_tokens: 25,
      total_tokens_measured_calls: 1,
      total_tokens_is_lower_bound: true,
    },
    {
      model_id: 'zero',
      call_count: 0,
      total_tokens: 0,
      total_tokens_measured_calls: 0,
      total_tokens_is_lower_bound: false,
    },
  ])

  assert.equal(rows[0].totalTokens, null)
  assert.equal(rows[0].totalTokensLabel, '不可测')
  assert.equal(rows[0].totalTokensAvailable, false)
  assert.equal(rows[1].totalTokensLabel, '≥ 25')
  assert.equal(rows[1].totalTokensCoverageLabel, '1/2 次调用可测')
  assert.equal(rows[2].totalTokens, 0)
  assert.equal(rows[2].totalTokensLabel, '0')
})
