import assert from 'node:assert/strict'
import test from 'node:test'

import {
  TOKEN_UNMEASURED_LABEL,
  buildTokenMetricView,
  hasMeasuredTokenValues,
  normalizeNullableTokenCount,
  normalizeNullableTokenSeries,
} from './tokenMetrics.js'

test('token normalization rejects missing, blank, negative and non-finite values', () => {
  for (const value of [null, undefined, '', ' ', -1, Number.NaN, Number.POSITIVE_INFINITY, {}, []]) {
    assert.equal(normalizeNullableTokenCount(value), null)
  }
  assert.equal(normalizeNullableTokenCount('12'), 12)
  assert.equal(normalizeNullableTokenCount(0), 0)
  assert.deepEqual(normalizeNullableTokenSeries([1, null, '2']), [1, null, 2])
  assert.deepEqual(normalizeNullableTokenSeries('not-an-array'), [])
  assert.equal(hasMeasuredTokenValues([null, '', 0]), true)
  assert.equal(hasMeasuredTokenValues([null, '']), false)
})

test('token metric distinguishes unmeasured, partial lower bound and full coverage', () => {
  const missing = buildTokenMetricView({ measuredCalls: 0, totalCalls: 3 })
  assert.equal(missing.label, TOKEN_UNMEASURED_LABEL)
  assert.equal(missing.available, false)
  assert.match(missing.detailLabel, /词元不可测/)

  const partial = buildTokenMetricView({ value: 1200, measuredCalls: 2, totalCalls: 4 })
  assert.equal(partial.isLowerBound, true)
  assert.match(partial.label, /^≥/)
  assert.match(partial.coverageLabel, /2\/4/)
  assert.match(partial.detailLabel, /已测值下界/)

  const full = buildTokenMetricView({ value: 1200, measuredCalls: 4, totalCalls: 4 })
  assert.equal(full.isLowerBound, false)
  assert.equal(full.hasPartialCoverage, false)
  assert.equal(full.detailLabel, full.coverageLabel)

  const explicit = buildTokenMetricView({ value: 1, isLowerBound: true })
  assert.equal(explicit.detailLabel, '已测值下界')
})
