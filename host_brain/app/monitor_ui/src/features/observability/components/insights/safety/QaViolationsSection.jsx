import { FileWarning } from 'lucide-react'
import LineChart from '../../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../../shared/ui/charts/BarChart.jsx'
import InsightChartCard from '../InsightChartCard.jsx'
import InsightSection from '../InsightSection.jsx'
import { buildQaViolationStatCards } from '../../../insightsSafetyModel.js'
import { formatTime, StatCard } from '../insightsGroupShared.jsx'

export default function QaViolationsSection({ qaData }) {
  const qaStatCards = buildQaViolationStatCards(qaData)

  return (
    <InsightSection icon={FileWarning} title="QA 违规统计" color="text-red-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {qaStatCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InsightChartCard
          title="违规码分布"
          hasData={(qaData?.violation_codes?.length ?? 0) > 0}
          emptyIcon={FileWarning}
          emptyText="暂无 QA 违规记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="违规码分布"
            direction="horizontal"
            categories={(qaData?.violation_codes ?? []).map((c) => c.violation_code)}
            series={[{ name: '次数', data: (qaData?.violation_codes ?? []).map((c) => c.count), color: '#F04438' }]}
          />
        </InsightChartCard>
        <div className="card p-4">
          <LineChart
            title="违规趋势"
            xData={qaData?.timeseries_labels ?? []}
            series={[{ name: '违规数', data: qaData?.timeseries_counts ?? [], color: '#F04438' }]}
          />
        </div>
      </div>
      {(qaData?.recent_violations?.length ?? 0) > 0 && (
        <div className="card overflow-hidden mt-4">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
            最近 QA 违规
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['时间', '节点', '违规码', '处置', '详情'].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(qaData?.recent_violations ?? []).map((v, index) => (
                <tr key={index} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(v.ts_ms)}</td>
                  <td className="px-4 py-2 text-xs font-mono text-blue-600">{v.node_id ?? '—'}</td>
                  <td className="px-4 py-2 text-xs font-mono text-red-600">{v.violation_code ?? '—'}</td>
                  <td className="px-4 py-2 text-xs font-mono text-orange-500">{v.action ?? '—'}</td>
                  <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs" title={v.detail}>{v.detail || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </InsightSection>
  )
}
