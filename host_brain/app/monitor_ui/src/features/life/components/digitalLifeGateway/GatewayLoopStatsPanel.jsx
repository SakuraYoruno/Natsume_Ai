import React from 'react'
import KeyValueRow from '../../../../shared/ui/KeyValueRow.jsx'
import { formatGatewayPercent } from '../../digitalLifeGatewayModel.js'

export default function GatewayLoopStatsPanel({
  humanoidLoopData,
  humanoidTotalRequests,
  humanoidFailedRequests,
  humanoidDeviationDetected,
}) {
  if (!humanoidLoopData || (humanoidLoopData?.stage_counts || []).length === 0) return null

  return (
    <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="card p-4">
        <div className="mb-2 text-xs font-semibold text-gray-600">闭环阶段分布</div>
        <div className="space-y-2">
          {(humanoidLoopData?.stage_counts || []).slice(0, 6).map((item) => (
            <div key={item.stage} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-slate-700">{item.stage}</span>
                <span className="text-gray-500">{item.count}</span>
              </div>
              <div className="mt-1 text-gray-500">{`rate ${formatGatewayPercent(item.rate)}`}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card p-4">
        <div className="mb-2 text-xs font-semibold text-gray-600">闭环核心计数</div>
        <div className="space-y-3">
          <KeyValueRow label="总 Gateway 请求" value={humanoidTotalRequests} />
          <KeyValueRow label="已进入终态" value={humanoidLoopData?.completed_requests ?? 0} />
          <KeyValueRow label="失败请求" value={humanoidFailedRequests} valueClassName={humanoidFailedRequests > 0 ? 'text-red-600' : 'text-gray-700'} />
          <KeyValueRow label="回复 Outbox 已提交" value={humanoidLoopData?.reply_committed_requests ?? 0} />
          <KeyValueRow label="兼容 ACK 已确认（旧路径）" value={humanoidLoopData?.ack_confirmed_requests ?? 0} />
          <KeyValueRow
            label="偏航恢复"
            value={`${humanoidLoopData?.deviation_recovered_requests ?? 0}/${humanoidDeviationDetected}`}
            valueClassName={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-700'}
          />
        </div>
      </div>
    </div>
  )
}
