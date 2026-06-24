import React from 'react'
import { RefreshCw } from 'lucide-react'
import TimeWindowSelect from '../../../shared/ui/TimeWindowSelect.jsx'
import { LIFE_COPY } from '../../../shared/content/terminology.js'

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
        <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{LIFE_COPY.cockpitEyebrow}</div>
        <h1 className="mt-1 text-xl font-semibold text-gray-900">{LIFE_COPY.cockpitTitle}</h1>
        <p className="text-sm text-gray-500">{LIFE_COPY.cockpitSubtitle}</p>
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
