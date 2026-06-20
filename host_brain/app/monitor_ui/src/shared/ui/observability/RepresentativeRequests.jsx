import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../EmptyStateCard.jsx'

export default function RepresentativeRequests({
  title = '代表性请求',
  hint = '',
  items = [],
  emptyTitle = '暂无代表性请求',
  emptyHint = '当前窗口还没有足够异常样本形成稳定的 request 入口。',
  className = '',
}) {
  return (
    <div className={`rounded-2xl border border-white/80 bg-white/85 p-4 shadow-sm backdrop-blur-sm ${className}`.trim()}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">{title}</div>
          {hint ? <div className="mt-1 text-sm text-gray-600">{hint}</div> : null}
        </div>
        {items.length > 0 ? (
          <span className="rounded-full border border-primary-100 bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700">
            {`${items.length} requests`}
          </span>
        ) : null}
      </div>

      {items.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {items.map((item) => (
            <Link
              key={`${item.type}-${item.id}-${item.label}`}
              to={item.to}
              className="rounded-2xl border border-primary-200 bg-white px-3 py-2 text-left text-xs text-primary-700 shadow-sm hover:border-primary-300 hover:bg-primary-50"
            >
              <div className="font-semibold">{item.label}</div>
              <div className="mt-1 font-mono text-[11px] text-primary-600">{item.subtitle}</div>
              {item.tags.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1">
                  {item.tags.slice(0, 2).map((tag) => (
                    <span
                      key={`${item.id}-${tag}`}
                      className="rounded-full border border-primary-100 bg-primary-50 px-1.5 py-0.5 text-[10px] font-medium text-primary-700"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
            </Link>
          ))}
        </div>
      ) : (
        <div className="mt-4">
          <EmptyStateCard title={emptyTitle} hint={emptyHint} className="p-4" />
        </div>
      )}
    </div>
  )
}
