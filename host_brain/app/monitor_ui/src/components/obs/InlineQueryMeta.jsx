import React from 'react'
import { formatWindowLabel } from '../../models/loadState.js'
import FreshnessPill from './FreshnessPill.jsx'
import LoadStateBadge from './LoadStateBadge.jsx'

export default function InlineQueryMeta({
  loadState,
  loadStateDetail,
  updatedAt,
  staleAfterMs = 120_000,
  windowMinutes,
  source,
  className = '',
}) {
  return (
    <div className={`mb-3 flex flex-wrap items-center gap-2 text-xs ${className}`.trim()}>
      {loadState ? <LoadStateBadge state={loadState} detail={loadStateDetail} /> : null}
      {windowMinutes ? (
        <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-gray-500">
          {formatWindowLabel(windowMinutes)}
        </span>
      ) : null}
      {source ? (
        <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-gray-500">
          {`来源 ${source}`}
        </span>
      ) : null}
      {updatedAt ? <FreshnessPill timestamp={updatedAt} staleAfterMs={staleAfterMs} /> : null}
    </div>
  )
}
