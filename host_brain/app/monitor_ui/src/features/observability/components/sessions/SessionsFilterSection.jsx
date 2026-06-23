import { Search } from 'lucide-react'
import { getSessionsWindowLabel } from '../../sessionsModel.js'
import FlowSection from './FlowSection.jsx'

export default function SessionsFilterSection({
  keyword,
  status,
  windowMinutes,
  onKeywordChange,
  onStatusChange,
  onSearch,
}) {
  return (
    <FlowSection title="会话筛选与检索" hint="先在体内筛查，再下钻到单条神经链路。">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_180px_160px_auto]">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-xl border border-gray-200 py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
            placeholder="搜索 request_id、session_id 或输入摘要"
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onSearch()
            }}
          />
        </div>

        <select
          className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="rejected">拒绝</option>
          <option value="error">异常</option>
        </select>

        <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
          时间窗：{getSessionsWindowLabel(windowMinutes)}
        </div>

        <button className="btn-primary justify-center" onClick={onSearch}>
          搜索
        </button>
      </div>
    </FlowSection>
  )
}
