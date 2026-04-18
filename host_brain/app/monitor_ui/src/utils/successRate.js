export function summarizeSuccessRateSamples(items, options = {}) {
  const normalizedItems = Array.isArray(items) ? items : []
  const rateKey = options.rateKey || 'success_rate'
  const totalKey = options.totalKey || 'total'

  const sampledItems = normalizedItems.filter((item) => {
    const total = Number(item?.[totalKey] ?? 0)
    const rate = item?.[rateKey]
    return total > 0 && rate != null && !Number.isNaN(Number(rate))
  })

  if (sampledItems.length === 0) {
    return {
      averageRate: null,
      sampledCount: 0,
      configuredCount: normalizedItems.length,
    }
  }

  const totalRate = sampledItems.reduce((sum, item) => sum + Number(item?.[rateKey]), 0)

  return {
    averageRate: Math.round(totalRate / sampledItems.length),
    sampledCount: sampledItems.length,
    configuredCount: normalizedItems.length,
  }
}

export function getSuccessRateTone(rate) {
  if (rate == null || Number.isNaN(Number(rate))) {
    return 'text-gray-500'
  }
  if (Number(rate) >= 90) {
    return 'text-green-600'
  }
  if (Number(rate) >= 70) {
    return 'text-orange-500'
  }
  return 'text-red-500'
}
