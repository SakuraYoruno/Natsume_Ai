import { Flame } from 'lucide-react'
import InsightSection from '../InsightSection.jsx'
import { useErrorHeatmapQuery } from '../../../diagnosticQueries.js'
import { buildErrorHeatmapView } from '../../../insightsGlobalModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function ErrorHeatmapSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useErrorHeatmapQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const heatmap = buildErrorHeatmapView(data)

  return (
    <InsightSection icon={Flame} title="错误热力图" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_errors', 'cells', 'nodes']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="总错误数" value={data?.total_errors ?? '—'} color={data?.total_errors > 0 ? 'text-red-600' : 'text-green-600'} />
        <StatCard label="统计窗口" value={data?.window_hours != null ? `${data.window_hours}h` : '—'} color="text-gray-700" />
        <StatCard label="涉及节点数" value={heatmap.nodes.length > 0 ? heatmap.nodes.length : '—'} color="text-orange-500" />
      </div>
      {heatmap.nodes.length === 0 ? (
        <div className="card p-6 text-center text-xs text-gray-400">✓ 窗口内无错误记录</div>
      ) : (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">按小时 × 节点错误分布（颜色深度 = 错误数量）</div>
          <div className="space-y-3 sm:hidden">
            <div className="grid grid-cols-6 gap-1.5">
              {heatmap.mobileHourCells.map((cell) => (
                <div
                  key={cell.hour}
                  className="rounded border border-gray-100 px-1.5 py-1 text-center"
                  style={{ backgroundColor: cell.backgroundColor }}
                >
                  <div className="text-[10px] text-gray-400">{cell.hour}:00</div>
                  <div className={`text-xs font-semibold ${cell.valueClassName}`}>{cell.total || '·'}</div>
                </div>
              ))}
            </div>
            <div className="space-y-2">
              {heatmap.mobileNodeTotals.map((item) => (
                <div key={item.node} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="min-w-0 truncate text-gray-600" title={item.node}>{item.node}</span>
                    <span className="font-mono font-semibold text-red-600">{item.total}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white">
                    <div className="h-full rounded-full bg-red-400" style={{ width: item.barWidth }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="hidden overflow-x-auto sm:block">
            <table className="border-collapse text-xs">
              <thead>
                <tr>
                  <th className="w-36 pb-1 pr-2 text-left font-normal text-gray-400">节点 \ 小时</th>
                  {heatmap.hours.map((hour) => (
                    <th key={hour} className="w-6 pb-1 text-center font-normal text-gray-400" style={{ minWidth: '1.5rem' }}>
                      {hour}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmap.desktopRows.map((row) => (
                  <tr key={row.node}>
                    <td className="max-w-36 truncate py-0.5 pr-2 text-gray-600" title={row.node}>{row.node}</td>
                    {row.cells.map((cell) => (
                      <td key={cell.hour} className="px-0.5 py-0.5" title={cell.title}>
                        <div
                          className="h-5 w-5 rounded-sm"
                          style={{ backgroundColor: cell.backgroundColor }}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="border-t border-gray-200">
                  <td className="py-1 pr-2 font-medium text-gray-400">每小时合计</td>
                  {heatmap.hourlyTotalCells.map((cell) => (
                    <td key={cell.hour} className="px-0.5 py-1 text-center">
                      <span className={`font-mono text-xs ${cell.className}`}>
                        {cell.label}
                      </span>
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
            <div className="mt-3 flex items-center gap-3 text-xs text-gray-400">
              <span>少</span>
              {heatmap.legendCells.map((cell) => (
                <div key={cell.value} className="h-4 w-4 rounded-sm" style={{ backgroundColor: cell.backgroundColor }} />
              ))}
              <span>多</span>
            </div>
          </div>
        </div>
      )}
    </InsightSection>
  )
}
