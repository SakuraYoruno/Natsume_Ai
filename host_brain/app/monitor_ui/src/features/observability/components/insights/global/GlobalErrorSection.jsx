import React from 'react'
import { AlertTriangle } from 'lucide-react'
import LineChart from '../../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../../shared/ui/charts/BarChart.jsx'
import InsightChartCard from '../InsightChartCard.jsx'
import InsightSection from '../InsightSection.jsx'
import { formatAlertTitle, humanizeToken } from '../../../../../utils/digitalLife.js'
import { buildGlobalErrorStatCards } from '../../../insightsGlobalModel.js'
import { StatCard } from '../insightsGroupShared.jsx'
import RecentErrors from './RecentErrors.jsx'

export default function GlobalErrorSection({ errData }) {
  const globalErrorStatCards = buildGlobalErrorStatCards(errData, humanizeToken)

  return (
    <InsightSection icon={AlertTriangle} title="全局错误追踪" color="text-red-500">
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {globalErrorStatCards.map((card) => (
          <StatCard key={card.key || card.label} label={card.label} value={card.value} sub={card.sub} color={card.color} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <LineChart
            title="错误趋势"
            xData={errData?.timeseries_labels ?? []}
            series={[{ name: '错误数', data: errData?.timeseries_counts ?? [], color: '#F04438' }]}
          />
        </div>
        <InsightChartCard
          title="错误原因 Top10"
          hasData={(errData?.top_codes?.length ?? 0) > 0}
          emptyIcon={AlertTriangle}
          emptyText="暂无错误记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="错误原因 Top10"
            direction="horizontal"
            categories={(errData?.top_codes ?? []).map((item) => formatAlertTitle(item.reason_code))}
            series={[{ name: '次数', data: (errData?.top_codes ?? []).map((item) => item.count), color: '#F79009' }]}
          />
        </InsightChartCard>
      </div>
      <RecentErrors items={errData?.recent ?? []} />
    </InsightSection>
  )
}
