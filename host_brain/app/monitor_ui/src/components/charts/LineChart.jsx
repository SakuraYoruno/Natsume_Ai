import React, { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'

/**
 * 通用折线图封装组件
 * @param {string} title - 图表标题
 * @param {Array<{name, data, color}>} series - 折线系列
 * @param {string[]} xData - X 轴数据
 * @param {string} [yUnit] - Y 轴单位（显示在 tooltip）
 * @param {number} [height=240] - 图表高度
 */
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
        const lines = params.map(
          (p) => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color};margin-right:4px"></span>${p.seriesName}: <b>${p.value ?? '—'}${yUnit}</b>`
        )
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
      axisLabel: { color: '#98A2B3', fontSize: 11, formatter: (v) => `${v}${yUnit}` },
    },
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      smooth: true,
      data: s.data ?? [],
      itemStyle: { color: s.color ?? '#2970FF' },
      lineStyle: { color: s.color ?? '#2970FF', width: 2 },
      areaStyle: { color: s.color ?? '#2970FF', opacity: 0.06 },
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

  return <ReactECharts option={option} style={{ height }} notMerge />
}
