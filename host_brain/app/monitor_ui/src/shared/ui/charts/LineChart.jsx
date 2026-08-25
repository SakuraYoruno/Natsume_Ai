import { Suspense, lazy, useMemo } from 'react'
import echarts from './echartsLineCore.js'

const ReactECharts = lazy(() =>
  import('echarts-for-react/lib/core').then((mod) => ({
    default: mod?.default?.default ?? mod?.default ?? mod,
  }))
)

function ChartFallback({ title, height }) {
  return (
    <div style={{ height }} className="flex items-center justify-center rounded-lg bg-gray-50 text-gray-400 text-sm">
      <div className="text-center">
        <div className="text-2xl mb-1">📈</div>
        <div>{title}</div>
        <div className="text-xs text-gray-300 mt-1">图表组件同步中…</div>
      </div>
    </div>
  )
}

export default function LineChart({ title, series = [], xData = [], yUnit = '', height = 240 }) {
  const option = useMemo(() => ({
    title: {
      text: title,
      textStyle: { fontSize: 13, fontWeight: 600, color: '#344054' },
      top: 0,
    },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const lines = params.map((item) => {
          const sourceSeries = series[item.seriesIndex] ?? {}
          const lowerBound = Boolean(sourceSeries.lowerBoundFlags?.[item.dataIndex])
          const coverageLabel = sourceSeries.coverageLabels?.[item.dataIndex]
          const unavailableLabel = sourceSeries.unavailableLabel ?? '—'
          const valueLabel = item.value == null
            ? unavailableLabel
            : `${lowerBound ? '≥ ' : ''}${item.value}${yUnit}`
          const coverage = coverageLabel
            ? ` <span style="color:#98A2B3">(${coverageLabel})</span>`
            : ''
          return `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${item.color};margin-right:4px"></span>${item.seriesName}: <b>${valueLabel}</b>${coverage}`
        })
        return `<div style="font-size:12px">${params[0]?.axisValue}<br/>${lines.join('<br/>')}</div>`
      },
    },
    legend: {
      right: 0,
      top: 0,
      textStyle: { fontSize: 11, color: '#667085' },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: 44, right: 16, top: 44, bottom: 28 },
    xAxis: {
      type: 'category',
      data: xData,
      axisLine: { lineStyle: { color: '#EAECF0' } },
      axisTick: { show: false },
      axisLabel: { color: '#98A2B3', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#F2F4F7', type: 'dashed' } },
      axisLabel: { color: '#98A2B3', fontSize: 11, formatter: (value) => `${value}${yUnit}` },
    },
    series: series.map((item) => ({
      name: item.name,
      type: 'line',
      smooth: true,
      data: item.data ?? [],
      itemStyle: { color: item.color ?? '#2970FF' },
      lineStyle: { color: item.color ?? '#2970FF', width: 2 },
      areaStyle: { color: item.color ?? '#2970FF', opacity: 0.06 },
      symbol: 'circle',
      symbolSize: 4,
      showSymbol: xData.length <= 20,
    })),
  }), [title, series, xData, yUnit])

  if (xData.length === 0) {
    return (
      <div style={{ height }} className="flex items-center justify-center text-gray-400 text-sm">
        <div className="text-center">
          <div className="text-2xl mb-1">📊</div>
          <div>{title}</div>
          <div className="text-xs text-gray-300 mt-1">数据采集中…</div>
        </div>
      </div>
    )
  }

  return (
    <Suspense fallback={<ChartFallback title={title} height={height} />}>
      <ReactECharts echarts={echarts} option={option} style={{ height }} notMerge />
    </Suspense>
  )
}
