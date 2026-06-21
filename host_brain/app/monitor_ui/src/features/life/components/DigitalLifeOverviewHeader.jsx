import React from 'react'
import { RefreshCw } from 'lucide-react'
import TimeWindowSelect from '../../../shared/ui/TimeWindowSelect.jsx'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function DigitalLifeOverviewHeader({
  windowMinutes,
  onWindowMinutesChange,
  onRefreshDashboard,
  dashboardBusy,
  refreshAnimating,
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
        <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
      </div>
      <div className="flex items-center gap-2">
        <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={WINDOW_OPTIONS} />
        <button
          className="btn-secondary"
          onClick={onRefreshDashboard}
          disabled={dashboardBusy}
        >
          <RefreshCw
            size={14}
            className={refreshAnimating ? 'animate-spin' : ''}
          />
          刷新
        </button>
      </div>
    </div>
  )
}
