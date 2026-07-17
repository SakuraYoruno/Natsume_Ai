import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTokenMetricView,
  normalizeNullableTokenCount,
  normalizeNullableTokenSeries,
} from '../../shared/tokenMetrics.js'
import { buildLlmTokenMetricsView } from './llmTokenMetricsModel.js'

test('token formatter keeps missing measurements unavailable instead of coercing them to zero', () => {
  assert.equal(normalizeNullableTokenCount(null), null)
  assert.equal(normalizeNullableTokenCount(undefined), null)
  assert.equal(normalizeNullableTokenCount(''), null)
  assert.equal(normalizeNullableTokenCount('   '), null)
  assert.equal(normalizeNullableTokenCount(false), null)
  assert.equal(normalizeNullableTokenCount('not-a-number'), null)
  assert.equal(normalizeNullableTokenCount(0), 0)
  assert.deepEqual(normalizeNullableTokenSeries([12, null, 0, undefined]), [12, null, 0, null])

  const unavailable = buildTokenMetricView({ value: null, measuredCalls: 0, totalCalls: 3 })
  assert.equal(unavailable.label, '不可测')
  assert.equal(unavailable.available, false)
  assert.equal(unavailable.value, null)
  assert.equal(unavailable.coverageLabel, '0/3 次调用可测')
})

test('partial token coverage is rendered as a lower bound even when an older backend omits the flag', () => {
  const partial = buildTokenMetricView({ value: 1234, measuredCalls: 2, totalCalls: 3 })
  assert.equal(partial.label, '≥ 1,234')
  assert.equal(partial.isLowerBound, true)
  assert.equal(partial.detailLabel, '2/3 次调用可测 · 已测值下界')

  const emptyWindow = buildTokenMetricView({ value: 0, measuredCalls: 0, totalCalls: 0 })
  assert.equal(emptyWindow.label, '0')
  assert.equal(emptyWindow.isLowerBound, false)
  assert.equal(emptyWindow.coverageLabel, null)
})

test('LLM insights view preserves nullable model and timeseries token points with coverage metadata', () => {
  const view = buildLlmTokenMetricsView({
    total_calls: 3,
    total_prompt_tokens: null,
    prompt_tokens_measured_calls: 0,
    total_prompt_tokens_is_lower_bound: false,
    total_completion_tokens: 40,
    completion_tokens_measured_calls: 2,
    total_completion_tokens_is_lower_bound: true,
    total_tokens: 100,
    total_tokens_measured_calls: 2,
    total_tokens_is_lower_bound: true,
    models: [
      {
        model_id: 'model-a',
        call_count: 3,
        total_prompt_tokens: null,
        prompt_tokens_measured_calls: 0,
        total_completion_tokens: 40,
        completion_tokens_measured_calls: 2,
        total_completion_tokens_is_lower_bound: true,
        total_tokens: 100,
        total_tokens_measured_calls: 2,
        total_tokens_is_lower_bound: true,
      },
    ],
    timeseries_labels: ['10:00', '10:05', '10:10'],
    timeseries_calls: [2, 1, 0],
    timeseries_tokens: [10, null, 0],
    timeseries_token_measured_calls: [1, 0, 0],
    timeseries_token_lower_bound_flags: [true, false, false],
  })

  assert.equal(view.total.label, '≥ 100')
  assert.equal(view.prompt.label, '不可测')
  assert.match(view.coverageNotice, /下界/)
  assert.deepEqual(view.modelPromptValues, [null])
  assert.deepEqual(view.modelCompletionValues, [40])
  assert.deepEqual(view.timeseries.values, [10, null, 0])
  assert.deepEqual(view.timeseries.lowerBoundFlags, [true, false, false])
  assert.deepEqual(view.timeseries.coverageLabels, ['1/2 次调用可测', '0/1 次调用可测', null])
  assert.equal(view.timeseries.hasData, true)
})

test('legacy explicit token values remain exact when coverage fields are absent', () => {
  const view = buildLlmTokenMetricsView({
    total_calls: 1,
    total_prompt_tokens: 7,
    total_completion_tokens: 3,
    total_tokens: 10,
    models: [],
    timeseries_labels: ['10:00'],
    timeseries_calls: [1],
    timeseries_tokens: [10],
  })

  assert.equal(view.total.label, '10')
  assert.equal(view.total.isLowerBound, false)
  assert.equal(view.coverageNotice, null)
  assert.deepEqual(view.timeseries.values, [10])
})
