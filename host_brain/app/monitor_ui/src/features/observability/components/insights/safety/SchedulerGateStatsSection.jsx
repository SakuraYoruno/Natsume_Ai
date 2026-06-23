import React from 'react'
import { useNavigate } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import BarChart from '../../../../../shared/ui/charts/BarChart.jsx'
import InsightChartCard from '../InsightChartCard.jsx'
import InsightSection from '../InsightSection.jsx'
import { useSchedulerGateStatsQuery } from '../../../diagnosticQueries.js'
import {
  buildAutomationGatePath,
  buildSchedulerGateStatCards,
} from '../../../insightsSafetyModel.js'
import { SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function SchedulerGateStatsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useSchedulerGateStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const navigate = useNavigate()
  const statCards = buildSchedulerGateStatCards(data)
  const goAutomation = React.useCallback(
    (params = {}) => {
      navigate(buildAutomationGatePath(params))
    },
    [navigate],
  )

  return (
    <InsightSection icon={ShieldAlert} title="节律门控统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['gate_blocked_total', 'blocked_reason_codes', 'gate_states']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InsightChartCard
          title="门控原因 Top"
          hasData={(data?.blocked_reason_codes?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无门控拦截记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="门控原因 Top"
            direction="horizontal"
            categories={(data?.blocked_reason_codes ?? []).map((item) => item.reason_code)}
            series={[{ name: '次数', data: (data?.blocked_reason_codes ?? []).map((item) => item.count), color: '#F79009' }]}
          />
        </InsightChartCard>
        <InsightChartCard
          title="Gate 状态分布"
          hasData={(data?.gate_states?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无 Gate 状态数据"
          emptyIconClassName="text-gray-400"
        >
          <BarChart
            title="Gate 状态分布"
            direction="horizontal"
            categories={(data?.gate_states ?? []).map((item) => item.gate_state)}
            series={[{ name: '次数', data: (data?.gate_states ?? []).map((item) => item.count), color: '#2E90FA' }]}
          />
        </InsightChartCard>
      </div>
      <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50/60 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-amber-800">需要任务级阻断明细时，可直接跳转执行页继续下钻。</div>
          <button
            onClick={() => goAutomation()}
            className="rounded border border-amber-200 bg-white px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-100"
          >
            前往执行页
          </button>
        </div>
        {(data?.blocked_reason_codes?.length ?? 0) > 0 ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {(data?.blocked_reason_codes ?? []).slice(0, 3).map((item) => (
              <button
                key={item.reason_code}
                onClick={() => goAutomation({ reason_code: item.reason_code })}
                className="rounded bg-orange-100 px-2 py-1 text-[11px] font-mono text-orange-700 hover:bg-orange-200"
                title={`按原因筛选：${item.reason_code}`}
              >
                reason={item.reason_code}
              </button>
            ))}
          </div>
        ) : null}
        {(data?.gate_states?.length ?? 0) > 0 ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {(data?.gate_states ?? []).slice(0, 3).map((item) => (
              <button
                key={item.gate_state}
                onClick={() => goAutomation({ gate_state: item.gate_state })}
                className="rounded bg-blue-100 px-2 py-1 text-[11px] font-mono text-blue-700 hover:bg-blue-200"
                title={`按 Gate 筛选：${item.gate_state}`}
              >
                gate={item.gate_state}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </InsightSection>
  )
}
