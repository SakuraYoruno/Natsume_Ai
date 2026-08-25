import { formatAlertTitle, humanizeToken } from '../../../../../utils/digitalLife.js'
import { formatTime } from '../insightsGroupShared.jsx'

export default function RecentErrors({ items = [] }) {
  if (!items.length) return null

  return (
    <div className="card mt-4 overflow-hidden">
      <div className="border-b border-gray-200 bg-gray-50 px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-gray-500">
        最近错误
      </div>
      <div className="divide-y divide-gray-100 sm:hidden">
        {items.map((item, index) => (
          <div key={index} className="space-y-2 px-4 py-3 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="whitespace-nowrap text-gray-500">{formatTime(item.ts_ms)}</span>
              <span className="rounded border border-red-100 bg-red-50 px-2 py-0.5 font-medium text-red-600">
                {item.reason_code ? formatAlertTitle(item.reason_code) : '—'}
              </span>
            </div>
            <div className="font-medium text-orange-600">{item.stage ? humanizeToken(item.stage) : '—'}</div>
            <div className="break-words text-gray-500">{item.detail || '—'}</div>
          </div>
        ))}
      </div>
      <table className="hidden w-full table-fixed text-sm sm:table">
        <thead className="border-b border-gray-200 bg-gray-50">
          <tr>
            {['时间', '环节', '原因码', '详情'].map((header) => (
              <th key={header} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{header}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item, index) => (
            <tr key={index} className="hover:bg-gray-50">
              <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{formatTime(item.ts_ms)}</td>
              <td className="px-4 py-2 text-xs text-orange-600">{item.stage ? humanizeToken(item.stage) : '—'}</td>
              <td className="px-4 py-2 text-xs text-red-600">{item.reason_code ? formatAlertTitle(item.reason_code) : '—'}</td>
              <td className="truncate px-4 py-2 text-xs text-gray-500" title={item.detail}>{item.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
