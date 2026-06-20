import React from 'react'
import { Tv } from 'lucide-react'
import BarChart from '../../../../shared/ui/charts/BarChart.jsx'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import { StatCard } from './insightsGroupShared.jsx'
import { formatEmotionTag } from '../../../../utils/digitalLife.js'

export default function InsightsVtsGroup({ vtsData }) {
  return (
    <InsightSection icon={Tv} title="表情通道统计" color="text-pink-600">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总事件数" value={vtsData?.total_events ?? '—'} />
        <StatCard
          label="表情应用率"
          value={vtsData?.apply_rate != null ? `${vtsData.apply_rate}%` : '—'}
          color={vtsData?.apply_rate >= 80 ? 'text-green-600' : vtsData?.apply_rate >= 50 ? 'text-orange-500' : 'text-red-500'}
        />
        <StatCard
          label="降级率"
          value={vtsData?.degrade_rate != null ? `${vtsData.degrade_rate}%` : '—'}
          color={vtsData?.degrade_rate > 20 ? 'text-orange-500' : 'text-green-600'}
        />
        <StatCard
          label="拒绝率"
          value={vtsData?.reject_rate != null ? `${vtsData.reject_rate}%` : '—'}
          color={vtsData?.reject_rate > 10 ? 'text-red-500' : 'text-green-600'}
        />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InsightChartCard
          title="Top 情感标签分布"
          hasData={(vtsData?.top_emotions?.length ?? 0) > 0}
          emptyIcon={Tv}
          emptyText="暂无表情事件数据"
        >
          <BarChart
            title="Top 情感标签分布"
            direction="horizontal"
            categories={(vtsData?.top_emotions ?? []).map((e) => formatEmotionTag(e.emotion_tag))}
            series={[{ name: '次数', data: (vtsData?.top_emotions ?? []).map((e) => e.count), color: '#EC4899' }]}
          />
        </InsightChartCard>
        <div className="card p-4">
          <div className="text-xs font-medium text-gray-500 mb-3">对话门控统计</div>
          <div className="grid grid-cols-3 gap-3">
            <div className="text-center">
              <div className="text-2xl font-bold text-gray-700">{vtsData?.talk_gate_total ?? '—'}</div>
              <div className="text-xs text-gray-400 mt-1">总请求</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">{vtsData?.talk_gate_allowed ?? '—'}</div>
              <div className="text-xs text-gray-400 mt-1">允许</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-red-500">{vtsData?.talk_gate_blocked ?? '—'}</div>
              <div className="text-xs text-gray-400 mt-1">拒绝</div>
            </div>
          </div>
          {vtsData?.talk_gate_total > 0 && (
            <div className="mt-4">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>允许率</span>
                <span>{Math.round((vtsData.talk_gate_allowed / vtsData.talk_gate_total) * 100)}%</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2">
                <div
                  className="bg-green-500 h-2 rounded-full transition-all"
                  style={{ width: `${Math.round((vtsData.talk_gate_allowed / vtsData.talk_gate_total) * 100)}%` }}
                />
              </div>
            </div>
          )}
          {(vtsData?.talk_gate_total ?? 0) === 0 && (
            <NoticeBanner variant="info" className="mt-4">暂无对话门控数据</NoticeBanner>
          )}
        </div>
      </div>
    </InsightSection>
  )
}
