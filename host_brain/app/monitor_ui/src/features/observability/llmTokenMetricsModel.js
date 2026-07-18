import {
  buildTokenMetricView,
  normalizeNullableTokenCount,
} from '../../shared/tokenMetrics.js'

function buildMetric(source, valueKey, measuredCallsKey, lowerBoundKey, totalCalls) {
  return buildTokenMetricView({
    value: source?.[valueKey],
    measuredCalls: source?.[measuredCallsKey],
    totalCalls,
    isLowerBound: source?.[lowerBoundKey],
  })
}

function buildModelTokenRows(models) {
  const rows = Array.isArray(models) ? models : []
  return rows.map((model, index) => {
    const callCount = normalizeNullableTokenCount(model?.call_count)
    return {
      key: model?.model_id || `llm-model-${index}`,
      modelId: model?.model_id || '未知模型',
      callCount,
      prompt: buildMetric(
        model,
        'total_prompt_tokens',
        'prompt_tokens_measured_calls',
        'total_prompt_tokens_is_lower_bound',
        callCount,
      ),
      completion: buildMetric(
        model,
        'total_completion_tokens',
        'completion_tokens_measured_calls',
        'total_completion_tokens_is_lower_bound',
        callCount,
      ),
      total: buildMetric(
        model,
        'total_tokens',
        'total_tokens_measured_calls',
        'total_tokens_is_lower_bound',
        callCount,
      ),
      avgLatencyMs: model?.avg_latency_ms,
      p95LatencyMs: model?.p95_latency_ms,
    }
  })
}

function buildTokenTimeseries(callData) {
  const labels = Array.isArray(callData?.timeseries_labels) ? callData.timeseries_labels : []
  const tokenValues = Array.isArray(callData?.timeseries_tokens) ? callData.timeseries_tokens : []
  const measuredCalls = Array.isArray(callData?.timeseries_token_measured_calls)
    ? callData.timeseries_token_measured_calls
    : []
  const totalCalls = Array.isArray(callData?.timeseries_calls) ? callData.timeseries_calls : []
  const lowerBoundFlags = Array.isArray(callData?.timeseries_token_lower_bound_flags)
    ? callData.timeseries_token_lower_bound_flags
    : []

  const points = labels.map((label, index) => ({
    label,
    ...buildTokenMetricView({
      value: tokenValues[index],
      measuredCalls: measuredCalls[index],
      totalCalls: totalCalls[index],
      isLowerBound: lowerBoundFlags[index],
    }),
  }))

  return {
    labels,
    points,
    values: points.map((point) => point.value),
    lowerBoundFlags: points.map((point) => point.isLowerBound),
    coverageLabels: points.map((point) => point.coverageLabel),
    hasData: points.some((point) => point.available),
    hasLowerBounds: points.some((point) => point.isLowerBound),
  }
}

function buildTokenCoverageNotice(totalCalls, totalMetric) {
  if (!(totalCalls > 0)) return null
  if (!totalMetric.available) {
    return totalMetric.coverageLabel
      ? `窗口内有 ${totalCalls} 次模型调用，但总词元仅 ${totalMetric.coverageLabel}，未测部分不补零。`
      : `窗口内有 ${totalCalls} 次模型调用，但总词元当前不可测，未测部分不补零。`
  }
  if (totalMetric.isLowerBound) {
    return totalMetric.coverageLabel
      ? `总词元只有 ${totalMetric.coverageLabel}，当前数值为已测调用的下界。`
      : '总词元当前仅能作为已测值下界，不代表完整消耗。'
  }
  return null
}

export function buildLlmTokenMetricsView(callData) {
  const totalCalls = normalizeNullableTokenCount(callData?.total_calls)
  const total = buildMetric(
    callData,
    'total_tokens',
    'total_tokens_measured_calls',
    'total_tokens_is_lower_bound',
    totalCalls,
  )
  const prompt = buildMetric(
    callData,
    'total_prompt_tokens',
    'prompt_tokens_measured_calls',
    'total_prompt_tokens_is_lower_bound',
    totalCalls,
  )
  const completion = buildMetric(
    callData,
    'total_completion_tokens',
    'completion_tokens_measured_calls',
    'total_completion_tokens_is_lower_bound',
    totalCalls,
  )
  const models = buildModelTokenRows(callData?.models)
  const timeseries = buildTokenTimeseries(callData)

  return {
    totalCalls,
    total,
    prompt,
    completion,
    models,
    modelCategories: models.map((model) => model.modelId),
    modelPromptValues: models.map((model) => model.prompt.value),
    modelPromptLowerBoundFlags: models.map((model) => model.prompt.isLowerBound),
    modelPromptCoverageLabels: models.map((model) => model.prompt.coverageLabel),
    modelCompletionValues: models.map((model) => model.completion.value),
    modelCompletionLowerBoundFlags: models.map((model) => model.completion.isLowerBound),
    modelCompletionCoverageLabels: models.map((model) => model.completion.coverageLabel),
    hasModelTokenData: models.some((model) => model.prompt.available || model.completion.available),
    hasModelLowerBounds: models.some((model) => model.prompt.isLowerBound || model.completion.isLowerBound),
    timeseries,
    coverageNotice: buildTokenCoverageNotice(totalCalls, total),
  }
}
