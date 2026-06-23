import React from 'react'
import { ShieldCheck } from 'lucide-react'
import LineChart from '../../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../../shared/ui/charts/BarChart.jsx'
import InsightChartCard from '../InsightChartCard.jsx'
import InsightSection from '../InsightSection.jsx'
import { formatAlertTitle } from '../../../../../utils/digitalLife.js'
import { buildGovernorDecisionStatCards } from '../../../insightsGlobalModel.js'
import { StatCard } from '../insightsGroupShared.jsx'

export default function GovernorDecisionSection({ govData }) {
  const governorStatCards = buildGovernorDecisionStatCards(govData)

  return (
    <InsightSection icon={ShieldCheck} title="入口守护决策漏斗" color="text-blue-600">
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {governorStatCards.map((card) => (
          <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <LineChart
            title="决策趋势"
            xData={govData?.timeseries_labels ?? []}
            series={[
              { name: '批准', data: govData?.timeseries_approved ?? [], color: '#12B76A' },
              { name: '拒绝', data: govData?.timeseries_rejected ?? [], color: '#F04438' },
            ]}
          />
        </div>
        <InsightChartCard
          title="拒绝原因分布"
          hasData={(govData?.reason_codes?.length ?? 0) > 0}
          emptyIcon={ShieldCheck}
          emptyText="暂无拒绝记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="拒绝原因分布"
            direction="horizontal"
            categories={(govData?.reason_codes ?? []).map((item) => formatAlertTitle(item.reason_code))}
            series={[{ name: '次数', data: (govData?.reason_codes ?? []).map((item) => item.count), color: '#F04438' }]}
          />
        </InsightChartCard>
      </div>
    </InsightSection>
  )
}
