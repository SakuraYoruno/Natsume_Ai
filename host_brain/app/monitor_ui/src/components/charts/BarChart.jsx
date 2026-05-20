import React, { Suspense, lazy, useMemo } from 'react'
import echarts from './echartsBarCore.js'

const ReactECharts = lazy(() =>
  import('echarts-for-react/lib/core').then((mod) => ({
    default: mod?.default?.default ?? mod?.default ?? mod,
  }))
)

function ChartFallback({ title, height }) {
  return (
    <div style={{ height }} className="flex items-center justify-center rounded-lg bg-gray-50 text-gray-400 text-sm">
      <div className="text-center">
        <div className="text-2xl mb-1">📊</div>
        <div>{title}</div>
        <div className="text-xs text-gray-300 mt-1">图表组件同步中…</div>
      </div>
    </div>
  )
}

export default function BarChart({
  title,
  series = [],
  categories = [],
  direction = 'vertical',
  yUnit = '',
  height = 240,
}) {
  const isHorizontal = direction === 'horizontal'

  const option = useMemo(() => {
    const categoryAxis = {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: '#EAECF0' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#667085',
        fontSize: 11,
        ...(isHorizontal ? {} : { rotate: categories.length > 6 ? 30 : 0 }),
      },
    }
    const valueAxis = {
      type: 'value',
      splitLine: { lineStyle: { color: '#F2F4F7', type: 'dashed' } },
      axisLabel: { color: '#98A2B3', fontSize: 11, formatter: (value) => `${value}${yUnit}` },
    }

    return {
      title: {
        text: title,
        textStyle: { fontSize: 13, fontWeight: 600, color: '#344054' },
        top: 0,
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const lines = params.map(
            (item) => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${item.color};margin-right:4px"></span>${item.seriesName}: <b>${item.value ?? '—'}${yUnit}</b>`
          )
          return `<div style="font-size:12px">${params[0]?.axisValue}<br/>${lines.join('<br/>')}</div>`
        },
      },
      legend: series.length > 1
        ? { right: 0, top: 0, textStyle: { fontSize: 11, color: '#667085' }, itemWidth: 12, itemHeight: 8 }
        : { show: false },
      grid: {
        left: isHorizontal ? 100 : 44,
        right: 16,
        top: series.length > 1 ? 44 : 36,
        bottom: isHorizontal ? 20 : 32,
      },
      xAxis: isHorizontal ? valueAxis : categoryAxis,
      yAxis: isHorizontal ? categoryAxis : valueAxis,
      series: series.map((item) => ({
        name: item.name,
        type: 'bar',
        data: item.data ?? [],
        itemStyle: {
          color: item.color ?? '#2970FF',
          borderRadius: isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        },
        barMaxWidth: 40,
        barGap: '20%',
      })),
    }
  }, [title, series, categories, direction, yUnit, isHorizontal])

  if (categories.length === 0) {
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
