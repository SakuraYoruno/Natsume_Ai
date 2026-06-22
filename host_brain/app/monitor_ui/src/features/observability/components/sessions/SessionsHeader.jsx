import { Download, RefreshCw } from 'lucide-react'
import TimeWindowSelect from '../../../../shared/ui/TimeWindowSelect.jsx'
import { SESSIONS_WINDOW_OPTIONS } from '../../sessionsModel.js'

export default function SessionsHeader({
  windowMinutes,
  exporting,
  loading,
  refreshing,
  onWindowMinutesChange,
  onExport,
  onRefresh,
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">神经会话沉积</h1>
        <p className="text-sm text-gray-500">把最近请求看成生命回声流，先看节律，再进入单条链路。</p>
      </div>
      <div className="flex items-center gap-2">
        <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={SESSIONS_WINDOW_OPTIONS} />
        <button className="btn-secondary" onClick={onExport} disabled={exporting}>
          <Download size={14} className={exporting ? 'animate-pulse' : ''} />
          {exporting ? '导出中' : '导出 CSV'}
        </button>
        <button className="btn-secondary" onClick={onRefresh} disabled={loading || refreshing}>
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
          {loading || refreshing ? '刷新中' : '刷新'}
        </button>
      </div>
    </div>
  )
}
